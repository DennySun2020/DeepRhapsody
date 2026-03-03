/*
 * concurrent_pipeline.c
 * =====================
 * Multi-threaded task processing pipeline with HARD concurrency bugs.
 *
 * Architecture:
 *   [Producer] ---Queue A---> [4 Workers] ---Queue B---> [Consumer]
 *
 *   - Producer enqueues N tasks into Queue A (batch-mode, large capacity).
 *   - Workers dequeue from A, compute f(x) = x*x + 1, enqueue result to B.
 *   - Consumer dequeues from B and sums all results.
 *   - Main thread orchestrates startup, shutdown, and verification.
 *
 * Expected: sum of (i*i + 1) for i in [1..N] = N*(N+1)*(2N+1)/6 + N
 *
 * SYMPTOMS:
 *   - With N <= ~20:  program completes, but sum is WRONG.
 *   - With N > ~100:  program HANGS and hits the timeout.
 *     Tasks completed count is much less than N. Sum is also wrong.
 *
 * There are TWO independent bugs. Both are hard to find by reading alone.
 * You need to step through the execution with a debugger to observe how
 * the threads interleave during shutdown and result collection.
 *
 * Compile:
 *   cl /Zi /Od /W4 concurrent_pipeline.c   (MSVC)
 *   gcc -g -O0 -Wall -o concurrent_pipeline concurrent_pipeline.c -lpthread  (GCC/MinGW)
 *
 * Run:
 *   concurrent_pipeline.exe              (default: 5000 tasks, will HANG)
 *   concurrent_pipeline.exe 10           (small: will show wrong sum)
 *   concurrent_pipeline.exe 5000         (large: will HANG then timeout)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <pthread.h>
#include <unistd.h>
#include <errno.h>
#include <sys/time.h>
#endif

/* ========================================================================= */
/*  Configuration                                                            */
/* ========================================================================= */

#define NUM_WORKERS          4
#define QUEUE_B_CAPACITY     64       /* Bounded output queue (backpressure) */
#define TIMEOUT_MS           10000    /* Abort pipeline after 10 seconds */

/* ========================================================================= */
/*  Data types                                                               */
/* ========================================================================= */

typedef struct {
    int       id;        /* Task ID: 1..N */
    int       input;     /* Input value */
    long long output;    /* Computed result */
} Task;

typedef struct {
    Task*    items;
    int      capacity;
    int      head;
    int      tail;
    int      count;
    int      done;       /* 1 = no more items will be enqueued */
#ifdef _WIN32
    CRITICAL_SECTION     lock;
    CONDITION_VARIABLE   cv_not_empty;
    CONDITION_VARIABLE   cv_not_full;
#else
    pthread_mutex_t      lock;
    pthread_cond_t       cv_not_empty;
    pthread_cond_t       cv_not_full;
#endif
} TaskQueue;

/* ========================================================================= */
/*  Pipeline state                                                           */
/* ========================================================================= */

typedef struct {
    TaskQueue     queue_a;            /* Producer -> Workers */
    TaskQueue     queue_b;            /* Workers -> Consumer */
    int           num_tasks;
    volatile long tasks_completed;    /* Atomically incremented by workers */
    long          tasks_consumed;     /* Set by consumer */
    long long     consumer_sum;       /* Sum computed by consumer */
} Pipeline;

/* ========================================================================= */
/*  Thread-safe Queue Implementation                                         */
/* ========================================================================= */

static void queue_init(TaskQueue* q, int capacity) {
    memset(q, 0, sizeof(*q));
    q->capacity = capacity;
    q->items = (Task*)calloc((size_t)capacity, sizeof(Task));
    if (!q->items) {
        fprintf(stderr, "FATAL: failed to allocate queue (capacity=%d)\n", capacity);
        exit(1);
    }
#ifdef _WIN32
    InitializeCriticalSection(&q->lock);
    InitializeConditionVariable(&q->cv_not_empty);
    InitializeConditionVariable(&q->cv_not_full);
#else
    pthread_mutex_init(&q->lock, NULL);
    pthread_cond_init(&q->cv_not_empty, NULL);
    pthread_cond_init(&q->cv_not_full, NULL);
#endif
}

static void queue_destroy(TaskQueue* q) {
    free(q->items);
    q->items = NULL;
#ifdef _WIN32
    DeleteCriticalSection(&q->lock);
#else
    pthread_mutex_destroy(&q->lock);
    pthread_cond_destroy(&q->cv_not_empty);
    pthread_cond_destroy(&q->cv_not_full);
#endif
}

/*
 * Enqueue a task (blocks if queue is full).
 * Thread-safe: acquires lock, waits on cv_not_full if needed.
 */
static void queue_push(TaskQueue* q, const Task* task) {
#ifdef _WIN32
    EnterCriticalSection(&q->lock);
    while (q->count == q->capacity) {
        SleepConditionVariableCS(&q->cv_not_full, &q->lock, INFINITE);
    }
    q->items[q->tail] = *task;
    q->tail = (q->tail + 1) % q->capacity;
    q->count++;
    WakeConditionVariable(&q->cv_not_empty);
    LeaveCriticalSection(&q->lock);
#else
    pthread_mutex_lock(&q->lock);
    while (q->count == q->capacity) {
        pthread_cond_wait(&q->cv_not_full, &q->lock);
    }
    q->items[q->tail] = *task;
    q->tail = (q->tail + 1) % q->capacity;
    q->count++;
    pthread_cond_signal(&q->cv_not_empty);
    pthread_mutex_unlock(&q->lock);
#endif
}

/*
 * Dequeue a task (blocks until an item is available).
 * Returns 1 on success, 0 if the queue is permanently empty (done + count==0).
 */
static int queue_pop(TaskQueue* q, Task* out) {
#ifdef _WIN32
    EnterCriticalSection(&q->lock);
    while (q->count == 0) {
        if (q->done) {
            LeaveCriticalSection(&q->lock);
            return 0;
        }
        SleepConditionVariableCS(&q->cv_not_empty, &q->lock, INFINITE);
    }
    *out = q->items[q->head];
    q->head = (q->head + 1) % q->capacity;
    q->count--;
    WakeConditionVariable(&q->cv_not_full);
    LeaveCriticalSection(&q->lock);
    return 1;
#else
    pthread_mutex_lock(&q->lock);
    while (q->count == 0) {
        if (q->done) {
            pthread_mutex_unlock(&q->lock);
            return 0;
        }
        pthread_cond_wait(&q->cv_not_empty, &q->lock);
    }
    *out = q->items[q->head];
    q->head = (q->head + 1) % q->capacity;
    q->count--;
    pthread_cond_signal(&q->cv_not_full);
    pthread_mutex_unlock(&q->lock);
    return 1;
#endif
}

/*
 * Signal that no more items will be enqueued.
 * Wakes all threads waiting for items so they can check the done flag.
 */
static void queue_finish(TaskQueue* q) {
#ifdef _WIN32
    EnterCriticalSection(&q->lock);
    q->done = 1;
    WakeAllConditionVariable(&q->cv_not_empty);
    LeaveCriticalSection(&q->lock);
#else
    pthread_mutex_lock(&q->lock);
    q->done = 1;
    pthread_cond_broadcast(&q->cv_not_empty);
    pthread_mutex_unlock(&q->lock);
#endif
}

/* ========================================================================= */
/*  Processing function                                                      */
/* ========================================================================= */

/*
 * Simulates a moderately expensive computation (e.g., hashing, encoding).
 * Target function: f(x) = x*x + 1
 * The accumulation loop models realistic CPU-bound work per task.
 */
static long long compute(int input) {
    volatile long long accumulator = 0;
    for (int round = 0; round < 200; round++) {
        accumulator += (long long)input * (round + 1);
    }
    (void)accumulator;
    return (long long)input * input + 1;
}

/* ========================================================================= */
/*  Producer Thread                                                          */
/*  Generates tasks with input values 1..N and pushes them to Queue A.       */
/* ========================================================================= */

#ifdef _WIN32
static DWORD WINAPI producer_thread(LPVOID arg) {
#else
static void* producer_thread(void* arg) {
#endif
    Pipeline* p = (Pipeline*)arg;
    for (int i = 1; i <= p->num_tasks; i++) {
        Task t;
        t.id     = i;
        t.input  = i;
        t.output = 0;
        queue_push(&p->queue_a, &t);
    }
    queue_finish(&p->queue_a);
#ifdef _WIN32
    return 0;
#else
    return NULL;
#endif
}

/* ========================================================================= */
/*  Worker Thread                                                            */
/*  Dequeues from A, computes result, enqueues to B.                         */
/* ========================================================================= */

#ifdef _WIN32
static DWORD WINAPI worker_thread(LPVOID arg) {
#else
static void* worker_thread(void* arg) {
#endif
    Pipeline* p = (Pipeline*)arg;
    Task task;
    while (queue_pop(&p->queue_a, &task)) {
        task.output = compute(task.input);
        queue_push(&p->queue_b, &task);
#ifdef _WIN32
        InterlockedIncrement(&p->tasks_completed);
#else
        __sync_fetch_and_add(&p->tasks_completed, 1);
#endif
    }
#ifdef _WIN32
    return 0;
#else
    return NULL;
#endif
}

/* ========================================================================= */
/*  Consumer Thread                                                          */
/*  Dequeues results from B and accumulates the sum.                         */
/* ========================================================================= */

#ifdef _WIN32
static DWORD WINAPI consumer_thread(LPVOID arg) {
#else
static void* consumer_thread(void* arg) {
#endif
    Pipeline* p = (Pipeline*)arg;
    Task result;
    long count = 0;

    while (queue_pop(&p->queue_b, &result)) {
        p->consumer_sum += result.output;
        count++;
    }

    /*
     * Note: when queue_pop returns 0 the queue is done AND empty.
     * The 'result' variable still holds the last successfully
     * dequeued item — do NOT add it again.
     */

    p->tasks_consumed = count;

#ifdef _WIN32
    return 0;
#else
    return NULL;
#endif
}

/* ========================================================================= */
/*  Main — Pipeline Orchestration                                            */
/* ========================================================================= */

int main(int argc, char* argv[]) {
    int num_tasks = 5000;
    if (argc > 1) {
        num_tasks = atoi(argv[1]);
        if (num_tasks <= 0) num_tasks = 5000;
    }

    printf("==================================================\n");
    printf("  Concurrent Pipeline\n");
    printf("  Tasks: %d, Workers: %d, Timeout: %d ms\n",
           num_tasks, NUM_WORKERS, TIMEOUT_MS);
    printf("==================================================\n\n");

    /* ----- Initialize pipeline ----- */

    Pipeline pipeline;
    memset(&pipeline, 0, sizeof(pipeline));
    pipeline.num_tasks = num_tasks;

    /*
     * Queue A (input):  sized to hold all tasks for batch enqueue.
     *                   This lets the producer push everything upfront
     *                   without blocking on worker throughput.
     *
     * Queue B (output): bounded to QUEUE_B_CAPACITY. This applies
     *                   backpressure — if the consumer is slow, workers
     *                   block until space is available.
     */
    queue_init(&pipeline.queue_a, num_tasks);
    queue_init(&pipeline.queue_b, QUEUE_B_CAPACITY);

    /* ----- Spawn threads ----- */

    /* Start consumer first (it will block waiting for items in Queue B) */
#ifdef _WIN32
    HANDLE h_consumer = CreateThread(NULL, 0, consumer_thread, &pipeline, 0, NULL);
#else
    pthread_t h_consumer;
    pthread_create(&h_consumer, NULL, consumer_thread, &pipeline);
#endif

    /* Start worker pool */
#ifdef _WIN32
    HANDLE h_workers[NUM_WORKERS];
    for (int i = 0; i < NUM_WORKERS; i++) {
        h_workers[i] = CreateThread(NULL, 0, worker_thread, &pipeline, 0, NULL);
    }
#else
    pthread_t h_workers[NUM_WORKERS];
    for (int i = 0; i < NUM_WORKERS; i++) {
        pthread_create(&h_workers[i], NULL, worker_thread, &pipeline);
    }
#endif

    /* Start producer (enqueues all tasks to Queue A) */
#ifdef _WIN32
    HANDLE h_producer = CreateThread(NULL, 0, producer_thread, &pipeline, 0, NULL);
#else
    pthread_t h_producer;
    pthread_create(&h_producer, NULL, producer_thread, &pipeline);
#endif

    /*
     * ---- Shutdown Sequence ----
     *
     * Orderly pipeline teardown:
     *   1. Wait for the producer to finish enqueuing all tasks.
     *   2. Wait for all workers to finish processing.
     *   3. Signal Queue B (output) that no more results will arrive,
     *      so the consumer can exit cleanly once it drains the queue.
     *   4. Wait for the consumer to finish draining Queue B.
     */

#ifdef _WIN32
    /* Step 1: Wait for producer */
    WaitForSingleObject(h_producer, INFINITE);
    printf("[main] Producer finished enqueueing %d tasks.\n", num_tasks);

    /* Step 2: Wait for workers (with timeout to prevent infinite hang) */
    DWORD wait_result = WaitForMultipleObjects(
        NUM_WORKERS, h_workers, TRUE, TIMEOUT_MS);
    if (wait_result == WAIT_TIMEOUT) {
        printf("\n*** TIMEOUT: Workers did not complete within %d ms. ***\n",
               TIMEOUT_MS);
        printf("*** Pipeline is stuck. Forcefully terminating workers. ***\n\n");
        for (int i = 0; i < NUM_WORKERS; i++) {
            TerminateThread(h_workers[i], 1);
        }
    } else {
        printf("[main] All workers completed.\n");
    }

    /* Step 3: Notify consumer that no more results will arrive */
    queue_finish(&pipeline.queue_b);
    printf("[main] Queue B signaled done.\n");

    /* Step 4: Wait for consumer */
    WaitForSingleObject(h_consumer, 2000);
    printf("[main] Consumer finished.\n\n");

#else
    /* Step 1 */
    pthread_join(h_producer, NULL);
    printf("[main] Producer finished enqueueing %d tasks.\n", num_tasks);

    /* Step 2 */
    for (int i = 0; i < NUM_WORKERS; i++) {
        pthread_join(h_workers[i], NULL);
    }
    printf("[main] All workers completed.\n");

    /* Step 3 */
    queue_finish(&pipeline.queue_b);
    printf("[main] Queue B signaled done.\n");

    /* Step 4 */
    pthread_join(h_consumer, NULL);
    printf("[main] Consumer finished.\n\n");
#endif

    /* ===== Verification ===== */

    long long n            = (long long)num_tasks;
    long long expected_sum = n * (n + 1) * (2 * n + 1) / 6 + n;
    long long actual_sum   = pipeline.consumer_sum;
    long      completed    = pipeline.tasks_completed;
    long      consumed     = pipeline.tasks_consumed;

    printf("-------- Results --------\n");
    printf("Tasks produced:  %d\n",   num_tasks);
    printf("Tasks completed: %ld\n",  completed);
    printf("Tasks consumed:  %ld\n",  consumed);
    printf("Expected sum:    %lld\n", expected_sum);
    printf("Actual sum:      %lld\n", actual_sum);

    if (actual_sum == expected_sum && completed == num_tasks) {
        printf("\nRESULT: PASS\n");
    } else {
        long long diff = actual_sum - expected_sum;
        printf("\nRESULT: FAIL\n");
        if (completed < num_tasks) {
            printf("  -> %ld tasks were NOT processed (pipeline stalled?)\n",
                   (long)num_tasks - completed);
        }
        if (diff != 0) {
            printf("  -> Sum mismatch: actual - expected = %lld\n", diff);
        }
        if (consumed != completed) {
            printf("  -> Consumer received %ld items but workers completed %ld\n",
                   consumed, completed);
        }
    }

    /* Cleanup */
#ifdef _WIN32
    CloseHandle(h_producer);
    for (int i = 0; i < NUM_WORKERS; i++) CloseHandle(h_workers[i]);
    CloseHandle(h_consumer);
#endif
    queue_destroy(&pipeline.queue_a);
    queue_destroy(&pipeline.queue_b);

    return (actual_sum == expected_sum && completed == num_tasks) ? 0 : 1;
}
