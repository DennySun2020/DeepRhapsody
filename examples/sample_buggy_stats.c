/*
 * sample_buggy_stats.c
 * ====================
 * Sample C program with 3 intentional bugs for testing the NeuralDebug agent.
 *
 * This program reads an array of student scores and computes statistics
 * (mean, max, min, count of passing scores). It has 3 bugs:
 *
 *   Bug 1 (line ~40): Off-by-one in loop -- uses <= instead of <
 *          causing an out-of-bounds read.
 *
 *   Bug 2 (line ~55): Integer division truncation -- divides int/int
 *          instead of casting to double, producing wrong mean.
 *
 *   Bug 3 (line ~70): Wrong comparison -- uses > instead of >= for
 *          passing threshold, excluding students who score exactly 60.
 *
 * Expected output (correct):
 *   Count:   8
 *   Mean:    72.75
 *   Max:     98
 *   Min:     42
 *   Passing: 6
 *
 * Actual buggy output will differ.
 *
 * Compile: gcc -g -O0 -o sample_buggy_stats sample_buggy_stats.c
 *          cl /Zi /Od sample_buggy_stats.c
 */

#include <stdio.h>
#include <limits.h>

#define NUM_STUDENTS 8

/* Compute the sum of scores */
int compute_sum(int scores[], int count) {
    int total = 0;
    /* BUG 1: Off-by-one -- should be i < count, not i <= count.
       This reads one element past the end of the array. */
    for (int i = 0; i <= count; i++) {   /* <-- BUG: should be i < count */
        total += scores[i];
    }
    return total;
}

/* Compute the mean score */
double compute_mean(int scores[], int count) {
    int sum = compute_sum(scores, count);
    /* BUG 2: Integer division -- sum and count are both int,
       so this truncates the decimal part.
       Fix: cast to (double)sum / count */
    double mean = sum / count;   /* <-- BUG: integer division */
    return mean;
}

/* Count how many students passed (score >= 60) */
int count_passing(int scores[], int count) {
    int passing = 0;
    for (int i = 0; i < count; i++) {
        /* BUG 3: Uses > instead of >=.
           Students scoring exactly 60 are not counted as passing.
           Fix: change > to >= */
        if (scores[i] > 60) {   /* <-- BUG: should be >= 60 */
            passing++;
        }
    }
    return passing;
}

/* Find the maximum score */
int find_max(int scores[], int count) {
    int max = INT_MIN;
    for (int i = 0; i < count; i++) {
        if (scores[i] > max) {
            max = scores[i];
        }
    }
    return max;
}

/* Find the minimum score */
int find_min(int scores[], int count) {
    int min = INT_MAX;
    for (int i = 0; i < count; i++) {
        if (scores[i] < min) {
            min = scores[i];
        }
    }
    return min;
}

int main(void) {
    int scores[NUM_STUDENTS] = {92, 85, 60, 73, 98, 55, 67, 42};

    printf("========================================\n");
    printf("  Student Score Statistics\n");
    printf("========================================\n\n");

    printf("Scores: ");
    for (int i = 0; i < NUM_STUDENTS; i++) {
        printf("%d", scores[i]);
        if (i < NUM_STUDENTS - 1) printf(", ");
    }
    printf("\n\n");

    double mean = compute_mean(scores, NUM_STUDENTS);
    int max = find_max(scores, NUM_STUDENTS);
    int min = find_min(scores, NUM_STUDENTS);
    int passing = count_passing(scores, NUM_STUDENTS);

    printf("Count:   %d\n", NUM_STUDENTS);
    printf("Mean:    %.2f\n", mean);
    printf("Max:     %d\n", max);
    printf("Min:     %d\n", min);
    printf("Passing: %d (score >= 60)\n", passing);

    printf("\n");
    printf("Expected: mean=72.75, max=98, min=42, passing=6\n");
    printf("Got:      mean=%.2f, max=%d, min=%d, passing=%d\n",
           mean, max, min, passing);

    if (mean != 72.75 || max != 98 || min != 42 || passing != 6) {
        printf("\n*** RESULTS DO NOT MATCH EXPECTED VALUES ***\n");
    } else {
        printf("\nAll results correct!\n");
    }

    return 0;
}
