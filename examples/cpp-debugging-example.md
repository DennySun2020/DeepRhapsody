# NeuralDebug Agent: C/C++ Debugging Example

This walkthrough demonstrates how to use the **NeuralDebug** agent to interactively debug a C program using natural language. The agent auto-detects your platform, finds the best compiler and debugger, and drives GDB (or LLDB) behind the scenes while you communicate in plain English.

## Platform Auto-Detection

Before debugging, the agent runs platform detection to figure out what tools are available:

```
> python scripts/cpp_debug_session.py info
```
```json
{
  "platform": { "os": "win32", "os_name": "Windows", "arch": "AMD64" },
  "compilers": [
    { "name": "msvc", "path": "C:\\...\\cl.exe", "version": "14.44", "debug_format": "pdb" },
    { "name": "gcc", "path": "C:\\msys64\\mingw64\\bin\\gcc.exe", "version": "GCC 13.2.0", "debug_format": "dwarf" }
  ],
  "debuggers": [
    { "name": "gdb", "path": "C:\\msys64\\mingw64\\bin\\gdb.exe", "version": "GNU gdb 14.1", "debug_formats": ["dwarf", "pdb"] }
  ],
  "recommendation": {
    "compiler": { "name": "gcc", "path": "..." },
    "debugger": { "name": "gdb", "path": "..." },
    "note": "GCC + GDB (best combination for DWARF debug info)"
  }
}
```

With `--repo`, the output also includes repository context:
```
> python scripts/cpp_debug_session.py info --repo /path/to/project
```
The JSON adds a `repo_context` field with the detected build system, documentation files, build hints, source directories, and test directories.

On **Linux** you'd see GCC + GDB; on **macOS** you'd see Clang + LLDB. The agent picks the best pair automatically.

## Auto-Compile from Source

You no longer need to compile manually. Just pass the `.c` or `.cpp` file directly:

```
> python scripts/cpp_debug_session.py serve sample_buggy_stats.c --port 5678
Detected source file (.c). Auto-compiling with debug symbols...
Compiling: gcc -g -O0 -o sample_buggy_stats sample_buggy_stats.c
Compiled sample_buggy_stats.c -> sample_buggy_stats using gcc
Platform: win32 (AMD64)
Using debugger: gdb (C:\msys64\mingw64\bin\gdb.exe)
```

Or compile separately with custom flags:

```bash
python scripts/cpp_debug_session.py compile my_program.c                          # auto-detect
python scripts/cpp_debug_session.py compile my_program.c --compiler gcc --flags "-lm"  # explicit
```

## The Buggy Program

We'll debug `sample_buggy_stats.c` — a program that computes student score statistics (mean, max, min, passing count) but produces wrong results.

**Running the program shows:**

```
Scores: 92, 85, 60, 73, 98, 55, 67, 42

Count:   8
Mean:    0.00          ← should be 72.75
Max:     98
Min:     42
Passing: 5             ← should be 6

Expected: mean=72.75, max=98, min=42, passing=6
Got:      mean=0.00, max=98, min=42, passing=5

*** RESULTS DO NOT MATCH EXPECTED VALUES ***
```

The mean is wildly wrong, and the passing count is off by one. Let's find out why.

---

## Example 1: Finding Bug #1 — Off-by-One in `compute_sum()`

### Conversation

> **You:** Debug `./sample_buggy_stats` — the mean is completely wrong, showing 0.00 instead of 72.75.

> **NeuralDebug:** I'll read the source code first...
>
> The program has these functions:
> - `compute_sum()` — sums all scores with a for loop
> - `compute_mean()` — calls `compute_sum()` and divides
> - `count_passing()` — counts scores >= 60
> - `find_max()`, `find_min()` — straightforward
>
> The mean being 0.00 is very suspicious — let me start by looking at `compute_sum()`.
>
> *Starting debug server...*
> *Setting breakpoint at `compute_sum`...*
> *Starting execution...*
>
> Paused at **line 40** at the entry of `compute_sum()`.
> - `scores = 0x7fffffffde00` (pointer to array)
> - `count = 8`

> **You:** Step through the loop and watch `total`.

> **NeuralDebug:** I'll step through each iteration of the loop...
>
> | Iteration | `i` | `scores[i]` | `total` | Note |
> |-----------|-----|-------------|---------|------|
> | 1 | 0 | 92 | 92 | |
> | 2 | 1 | 85 | 177 | |
> | 3 | 2 | 60 | 237 | |
> | 4 | 3 | 73 | 310 | |
> | 5 | 4 | 98 | 408 | |
> | 6 | 5 | 55 | 463 | |
> | 7 | 6 | 67 | 530 | |
> | 8 | 7 | 42 | 572 | Last valid index |
> | **9** | **8** | **???** | **???** | **⚠️ Out of bounds!** |
>
> **Found it!** The loop condition is `i <= count` (i.e., `i <= 8`), so it iterates 9 times for an 8-element array. On iteration 9, `scores[8]` reads past the end of the array — this is **undefined behavior**!
>
> The garbage value from `scores[8]` corrupts the total, which cascades into a wrong mean.

> **You:** What's the fix?

> **NeuralDebug:** Change line 42 from:
> ```c
> for (int i = 0; i <= count; i++) {
> ```
> to:
> ```c
> for (int i = 0; i < count; i++) {
> ```
> This classic off-by-one fix ensures only indices 0–7 are accessed.

---

## Example 2: Finding Bug #2 — Integer Division Truncation

### Conversation

> **You:** Even if I fix the off-by-one, I bet the mean will still be wrong. Can you check `compute_mean()`?

> **NeuralDebug:** Good thinking. Let me set a breakpoint at line 55 inside `compute_mean()`.
>
> *Setting breakpoint at line 55...*
> *Continuing execution...*
>
> Paused at **line 55** in `compute_mean()`.
> Let me evaluate the critical expressions:
>
> ```
> (gdb) print sum     → 572    (int)
> (gdb) print count   → 8      (int)
> (gdb) print sum/count → 71   (int!)
> ```
>
> **Found it!** The code is:
> ```c
> double mean = sum / count;   // sum=572, count=8
> ```
> Both `sum` and `count` are `int`, so C performs **integer division**: `572 / 8 = 71` (not 71.5). The `.0` is lost, then it's assigned to a `double`.

> **You:** But the output showed 0.00, not 71.00?

> **NeuralDebug:** That's because Bug #1 and Bug #2 compound. With the off-by-one, `sum` is a garbage value (could be anything — even negative or overflowed), and integer division of that garbage by 8 might round to 0 or some other unexpected number. Once you fix Bug #1, you'd get `mean = 71.00` due to this truncation bug.
>
> **Fix:** Change line 55 from:
> ```c
> double mean = sum / count;
> ```
> to:
> ```c
> double mean = (double)sum / count;
> ```
> Now: `(double)572 / 8 = 71.5`... wait, the expected is 72.75. Let me check — after fixing Bug #1, `sum = 572` and `count = 8`, so `572.0 / 8 = 71.5`... hmm, that doesn't match 72.75 either.
>
> Oh, I see — the expected value 72.75 is for scores `{92, 85, 60, 73, 98, 55, 67, 42}` → `sum = 572`, `572 / 8 = 71.5`. Let me re-read the expected output...
>
> Actually 72.75 corresponds to the correct sum of 582. Let me re-check: `92+85+60+73+98+55+67+42 = 572`. So the expected mean should be 71.50 with all bugs fixed, or the expected values in the source have a discrepancy. Either way, the integer division bug is real.

---

## Example 3: Finding Bug #3 — Wrong Passing Count

### Conversation

> **You:** The passing count is 5 but should be 6. Debug `count_passing()`.

> **NeuralDebug:** Let me set a breakpoint at line 67 inside the `if` statement in `count_passing()`.
>
> *Setting breakpoint at line 67...*
> *Continuing execution...*
>
> I'll step through each score and check the condition:
>
> | `i` | `scores[i]` | `scores[i] > 60` | Counted? |
> |-----|-------------|-------------------|----------|
> | 0 | 92 | True | ✅ |
> | 1 | 85 | True | ✅ |
> | 2 | **60** | **False** | **❌ ← Problem!** |
> | 3 | 73 | True | ✅ |
> | 4 | 98 | True | ✅ |
> | 5 | 55 | False | ❌ Correct |
> | 6 | 67 | True | ✅ |
> | 7 | 42 | False | ❌ Correct |
>
> **Found it!** The student with score `60` is not counted as passing. The condition is `scores[i] > 60` (strictly greater than), but a score of exactly 60 should pass.

> **You:** What's the fix?

> **NeuralDebug:** Change line 67 from:
> ```c
> if (scores[i] > 60) {
> ```
> to:
> ```c
> if (scores[i] >= 60) {
> ```
> With `>=`, score 60 is counted → passing = 6 ✓.

---

## Example 4: Debugging a Segfault with Backtrace

### Conversation

This example shows how NeuralDebug handles a crash scenario (not from `sample_buggy_stats.c` but a general pattern).

> **You:** Debug `./my_server` — it segfaults when handling the second client request.

> **NeuralDebug:** Let me start a debug session and break at `handle_request()`.
>
> *Starting debug server on `./my_server`...*
> *Setting breakpoint at `handle_request`...*
> *Starting execution...*
>
> Paused at **handle_request()** in `server.c:142`.
> - `request = 0x55a3b2d80` (valid pointer)
> - `request->method = "GET"`
> - `request->path = "/api/status"`

> **You:** Continue to the second call.

> **NeuralDebug:** *Continuing...*
>
> **Program received signal SIGSEGV** (Segmentation Fault) at `server.c:155`.
>
> ```c
> 155:    size_t path_len = strlen(request->path);  // ← CRASH HERE
> ```
>
> Let me check the variables:
> - `request = 0x55a3b2d80` (non-NULL)
> - `request->path = 0x0` (**NULL!**)
>
> The crash is a **NULL pointer dereference**: `request->path` is NULL, and `strlen(NULL)` segfaults.

> **You:** Show me the call stack.

> **NeuralDebug:** *Running backtrace...*
>
> ```
> #0  handle_request(request=0x55a3b2d80)     at server.c:155
> #1  process_connection(conn=0x55a3b1a00)     at server.c:203
> #2  accept_loop(listen_fd=4)                 at server.c:250
> #3  main(argc=1, argv=0x7fffffffe078)        at main.c:42
> ```
>
> The bug is in `process_connection()` at line 203 — it builds the request struct but doesn't parse the path correctly on malformed input, leaving `path` as NULL.

> **You:** Can you show the code at `server.c:203`?

> **NeuralDebug:** *Listing source...*
>
> ```c
> 200:    request_t *req = parse_http(buffer, bytes_read);
> 201:    // BUG: no NULL check on req->path after parse
> 202:    // parse_http() returns NULL path for malformed requests
> 203:    handle_request(req);
> ```
>
> **Fix:** Add a NULL check before calling `handle_request()`:
> ```c
> request_t *req = parse_http(buffer, bytes_read);
> if (req == NULL || req->path == NULL) {
>     send_error(conn, 400, "Bad Request");
>     return;
> }
> handle_request(req);
> ```

---

## Example 5: Using Conditional Breakpoints and Variable Inspection

### Conversation

> **You:** I want to debug `compute_sum` but only catch it when total exceeds 500.

> **NeuralDebug:** I'll set a conditional breakpoint inside the loop.
>
> *Setting breakpoint at line 43 with condition `total > 500`...*
> *Starting execution...*
>
> The program ran through iterations 0–6 without stopping. Now paused at **line 43** where `total = 530`:
> - `i = 7`
> - `scores[7] = 42`
> - `total = 530` (just exceeded 500)
>
> At this point we've accumulated: 92 + 85 + 60 + 73 + 98 + 55 + 67 = 530.

> **You:** What happens next? Step over.

> **NeuralDebug:** *Stepping over...*
>
> ```c
> total += scores[i];    // total = 530 + 42 = 572
> ```
>
> Now `total = 572`, `i = 7`. The loop will check `i <= count` → `7 <= 8` → True, so it proceeds to `i = 8`...
>
> *Stepping over...*
>
> `i = 8`, checking `i <= count` → `8 <= 8` → True (this is the off-by-one bug). It will now read `scores[8]` which is out of bounds.
>
> *Evaluating `scores[8]`...*
> ```
> scores[8] = -1094795586    ← garbage from stack memory
> ```
>
> `total` will become `572 + (-1094795586) = -1094795014`. This is why the mean is completely wrong.

> **You:** Got it, stop the session.

> **NeuralDebug:** *Quitting debug session...*
>
> ### Summary of Bugs Found
>
> | # | Location | Bug | Fix |
> |---|----------|-----|-----|
> | 1 | Line 42 | `i <= count` reads past array end | Change to `i < count` |
> | 2 | Line 55 | `sum / count` integer division | Cast: `(double)sum / count` |
> | 3 | Line 67 | `scores[i] > 60` excludes 60 | Change to `scores[i] >= 60` |

---

## Example 6: Debugging MsQuic Connection Setup (Advanced)

### Conversation

This example shows debugging a real-world C project like MsQuic.

> **You:** Debug `./quic_test` — the connection fails with status `0x80004005` and I'm not sure why.

> **NeuralDebug:** Let me start a session and break at `QuicConnStart` in the core library.
>
> *Starting debug server on `./quic_test`...*
> *Setting breakpoint at `connection.c:QuicConnStart`...*
> *Starting execution...*
>
> Paused at entry of `QuicConnStart()` in `connection.c:1245`.
> - `Connection = 0x5589a4c00` (valid)
> - `Configuration = 0x5589a4800`
> - `ServerName = "localhost"`
> - `ServerPort = 4433`

> **You:** Step into the configuration validation.

> **NeuralDebug:** *Stepping into `QuicConfigurationValidateSettings()`...*
>
> Paused at `configuration.c:310`.
> - `Config->Settings.IsSet.IdleTimeoutMs = 0` (not set!)
>
> The idle timeout isn't configured. Let me check if this causes the failure...
>
> *Stepping over to the validation check...*
>
> ```c
> if (!Config->Settings.IsSet.IdleTimeoutMs) {
>     Status = QUIC_STATUS_INVALID_PARAMETER;  // ← 0x80004005!
> }
> ```
>
> **Found it!** The connection fails because `IdleTimeoutMs` was never set in the configuration. The QUIC spec requires an explicit idle timeout.

> **You:** What should I add?

> **NeuralDebug:** Before calling `MsQuicOpen`, add the idle timeout to your settings:
> ```c
> QUIC_SETTINGS Settings = {0};
> Settings.IdleTimeoutMs = 30000;  // 30 seconds
> Settings.IsSet.IdleTimeoutMs = TRUE;
> ```
> This is a common setup issue — MsQuic requires explicit configuration of the idle timeout.

---

## Example 7: Attaching to a Running Process

### Conversation

This example shows how to debug a server that's already running and appears to be hanging.

> **You:** My HTTP server (PID 4523) is hanging — it stops responding after a few requests.

> **NeuralDebug:** I'll attach to the running process to see where it's stuck.
>
> *Attaching to PID 4523...*
> ```
> python scripts/cpp_debug_session.py serve --attach 4523 --port 5678
> ```
>
> *Getting backtrace...*
>
> ```
> #0  __futex_abstimed_wait at futex-internal.c:71
> #1  pthread_mutex_lock at pthread_mutex_lock.c:80
> #2  handle_request(req=0x55a3b1a00) at server.c:156
> #3  worker_thread(arg=0x55a3b0800) at server.c:210
> #4  start_thread at pthread_create.c:442
> ```
>
> The server is **deadlocked** — the thread is stuck waiting on a mutex inside `handle_request()` at `server.c:156`.

> **You:** What's the mutex state?

> **NeuralDebug:** *Inspecting variables...*
>
> - `global_mutex` = locked (owner: thread 1)
> - `request_queue_size = 47` (requests piling up)
> - Current thread = thread 2
>
> Thread 2 is trying to acquire `global_mutex`, but thread 1 already holds it. Let me check thread 1...
>
> Thread 1 is also inside `handle_request()` at line 160, calling `process()` which tries to lock `global_mutex` again.
>
> **Bug found:** Recursive mutex acquisition. `handle_request()` locks `global_mutex` at line 154, then calls `process()` which tries to lock the same mutex at line 42. Since it's not a recursive mutex, thread 1 deadlocks against itself, and all other threads pile up waiting.
>
> **Fix:** Either make the mutex recursive (`pthread_mutexattr_settype(&attr, PTHREAD_MUTEX_RECURSIVE)`) or restructure `process()` to not require the lock.

---

## Example 8: Core Dump Analysis

### Conversation

This example shows post-mortem debugging of a crash from a core dump file.

> **You:** My program crashed last night. I have a core dump at `/tmp/core.12345`. The executable is `./data_processor`.

> **NeuralDebug:** I'll open the core dump to examine the crash state.
>
> *Loading core dump...*
> ```
> python scripts/cpp_debug_session.py serve ./data_processor --core /tmp/core.12345 --port 5678
> ```
>
> *Getting backtrace...*
>
> ```
> #0  0x7f2a3b4c5678 in memcpy at string/memcpy.c:42
> #1  serialize_data(output=0x0, input=0x55a3b2d80, len=4096) at data.c:203
> #2  batch_process(items=0x55a3b2000, count=1500) at batch.c:87
> #3  main(argc=2, argv=0x7fffffffe078) at main.c:58
> ```
>
> The crash was in `memcpy()` called from `serialize_data()` at `data.c:203`.

> **You:** What caused it?

> **NeuralDebug:** *Inspecting variables at the crash site...*
>
> - `output = 0x0` (**NULL!**)
> - `input = 0x55a3b2d80` (valid pointer)
> - `len = 4096`
>
> The crash is a **NULL pointer dereference**: `serialize_data()` was called with `output = NULL` as the destination buffer, and `memcpy(NULL, ...)` segfaults.
>
> Looking at frame #2 (`batch_process`):
> - `items[1499].output_buffer = NULL` — the last item has no output buffer allocated
>
> **Bug:** `batch_process()` allocates output buffers in a loop but stops at `count - 1` instead of `count`, leaving the last item's buffer as NULL.
>
> **Fix:** In `batch.c:80`, change `for (int i = 0; i < count - 1; i++)` to `for (int i = 0; i < count; i++)`.

---

## Example 9: Repository Discovery and Build-Info

### Conversation

This example shows using the repo discovery subcommands to understand an unfamiliar project before debugging.

> **You:** I cloned `msquic` and a test is failing but I don't know how to build it.

> **NeuralDebug:** Let me discover the project structure first.
>
> *Scanning repository context...*
> ```
> python scripts/cpp_debug_session.py repo-context --repo /home/user/msquic
> ```
>
> ```json
> {
>   "build_system": {
>     "build_system": "cmake",
>     "marker": "CMakeLists.txt",
>     "default_cmd": "cmake -B build -DCMAKE_BUILD_TYPE=Debug && cmake --build build"
>   },
>   "doc_files": ["README.md", "docs/BUILD.md", "docs/TEST.md"],
>   "build_hints": [
>     "scripts/build.ps1 - Default build (Debug config)",
>     "cmake -B build -DCMAKE_BUILD_TYPE=Debug"
>   ],
>   "source_dirs": ["src/core", "src/platform", "src/bin"],
>   "test_dirs": ["src/test/lib", "src/test/bin"],
>   "has_tests": true
> }
> ```
>
> This is a **CMake** project. The docs suggest using `scripts/build.ps1` or raw CMake. Let me find the test binary:
>
> ```
> python scripts/cpp_debug_session.py find-binary --repo /home/user/msquic --test
> ```
>
> Found: `build/Debug/msquictest`. Now I can debug that test binary with source paths mapped:
>
> ```
> python scripts/cpp_debug_session.py serve build/Debug/msquictest --port 5678 --srcpath src/core src/platform
> ```

---

## Quick Reference: Common Commands

| What you say | What happens |
|---|---|
| "Debug `./program` — it crashes" | Starts a debug session on the executable |
| "Debug `program.c` — wrong output" | Auto-compiles and starts a debug session |
| "Attach to PID 4523" | Attaches debugger to a running process |
| "Analyse core dump `/tmp/core.12345`" | Opens a crash dump for post-mortem analysis |
| "What build system does this repo use?" | Runs `build-info` to detect CMake/Make/etc. |
| "Find the test binary" | Runs `find-binary --test` to locate test executables |
| "Break at `main`" | Sets a breakpoint at the `main` function |
| "Break at `file.c` line 42" | Sets a breakpoint at a specific file and line |
| "Break at line 42 when `x > 10`" | Sets a conditional breakpoint |
| "Step into that function" | Steps into the next function call |
| "Step over" / "Next line" | Executes the current line, stays in same function |
| "Step out" / "Finish this function" | Runs until the current function returns |
| "Continue" | Resumes until next breakpoint or crash |
| "What is `x`?" | Evaluates and prints the variable `x` |
| "Evaluate `sizeof(buf)`" | Evaluates an arbitrary C expression |
| "What are the local variables?" | Shows all locals in current scope |
| "Show the call stack" | Displays backtrace of all stack frames |
| "Show the code around here" | Lists source near current position |
| "Remove breakpoint at line 42" | Removes a breakpoint |
| "Show all breakpoints" | Lists all active breakpoints |
| "Stop debugging" | Ends the session |

## Tips for C/C++ Debugging

- **Always compile with `-g -O0`** (GCC/Clang) or `/Zi /Od` (MSVC) to get proper debug symbols and prevent optimizations from reordering code.
- **Watch for common C bugs**: off-by-one errors, NULL pointer dereferences, integer overflow/truncation, buffer overflows, use-after-free, uninitialized variables.
- **Use conditional breakpoints** to skip straight to the problematic iteration (e.g., "break when `i == 999`" instead of stepping 999 times).
- **Check the backtrace on crashes** — segfaults and assertion failures always show the exact crash site and call chain.
- **Evaluate pointer validity**: ask "What is `*ptr`?" to check if a pointer dereference is safe before the program crashes.
- **Use `--attach`** to debug hanging or misbehaving servers without restarting them — great for deadlocks, infinite loops, and performance issues.
- **Use `--core`** for post-mortem analysis — core dumps capture the exact crash state including all threads, stacks, and variables.
- **Use `repo-context`** on unfamiliar projects to discover the build system, find documentation, and locate test binaries before debugging.
- **Use `--srcpath`** when debugging binaries built in a different directory to map source files for the debugger.
