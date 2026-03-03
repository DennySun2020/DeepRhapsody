# AI-Driven Debugging Sessions — How It Works

This document explains the architecture, workflow, and design principles behind the **NeuralDebug** agent — an AI-powered interactive debugger that lets developers find bugs using natural language instead of memorizing debugger commands.

---

## Overview

NeuralDebug bridges the gap between a developer describing a problem in plain English and the low-level mechanics of stepping through code with a real debugger. Rather than replacing the debugger, it acts as an intelligent translator and investigator:

```
Developer (natural language)
       │
       ▼
┌──────────────────────┐
│    NeuralDebug Agent  │  ← AI agent (Copilot)
│  Understands intent, │
│  forms hypotheses,   │
│  chooses debug actions │
└──────────┬───────────┘
           │  JSON commands over TCP
           ▼
┌──────────────────────┐     ┌────────────────┐
│    Debug Server       │────▶│  GDB / LLDB /  │
│  (Python TCP server)  │◀────│     CDB        │
│  Translates JSON to   │     │  (subprocess)  │
│  debugger commands     │     └────────────────┘
└──────────────────────┘              │
                                      ▼
                              ┌────────────────┐
                              │ Target Program  │
                              │ (being debugged)│
                              └────────────────┘
```

The key insight: the AI agent never touches the debugger directly. A persistent TCP server holds the debugger session alive between commands, while a stateless client sends one command at a time. This separation lets an AI agent drive a real debugging session across multiple conversational turns.

---

## Architecture

### Three-Layer Design

**Layer 1 — The AI Agent** (`NeuralDebug.agent.md`)

The Copilot agent that the developer talks to. It:
- Reads source code to understand the program structure
- Forms hypotheses about where bugs might be
- Translates natural language ("step into that function") into debug commands (`step_in`)
- Interprets JSON responses and explains them in plain language
- Suggests next steps based on observed values

**Layer 2 — The Debug Server** (`cpp_debug_session.py` / `python_debug_session.py`)

A Python TCP server that:
- Launches the debugger (GDB, LLDB, CDB, or Python bdb) as a subprocess
- Accepts JSON commands from the client: `{"action": "step_over", "args": ""}`
- Translates them to native debugger commands (e.g., GDB MI `-exec-next`, CDB `p`, LLDB `next`)
- Parses debugger output back into structured JSON with location, variables, call stack
- Stays alive between commands — the program remains paused, waiting for the next instruction

**Layer 3 — The Debugger** (GDB / LLDB / CDB / bdb)

The actual debugger process that controls the target:
- **GDB** — Machine Interface (MI) mode for structured input/output
- **LLDB** — Command-line text parsing
- **CDB** — Windows Console Debugger, reads PDB symbols natively
- **bdb** — Python's built-in debugger (stdlib, no external dependencies)

### Why TCP?

The debug server uses a TCP socket (default port 5678) rather than stdin/stdout because:

1. **Persistence** — The server process keeps the debugger and target alive between commands. The client connects, sends one command, gets a response, and disconnects. There's no need to maintain a continuous pipe.

2. **Statelessness** — Each client invocation is a fresh process. It connects, sends JSON, receives JSON, and exits. This makes it trivial for an AI agent to call via terminal commands.

3. **Decoupling** — The server can run as a background process in one terminal while the agent sends commands from another. Crashes in the client don't kill the debug session.

### Data Flow for a Single Command

```
Agent terminal                     Debug server (background)
─────────────                     ──────────────────────────

py cmd --port 5678 step_over
       │
       ├─── TCP connect ──────────▶ accept()
       ├─── send JSON ────────────▶ {"action":"step_over","args":""}
       │                             │
       │                             ├── [GDB] send: 5-exec-next
       │                             │   [CDB] send: p
       │                             │   [LLDB] send: next
       │                             │
       │                             ├── wait for stop event...
       │                             │   (debugger steps one line,
       │                             │    target pauses at next line)
       │                             │
       │                             ├── query location (-stack-info-frame)
       │                             ├── query call stack (-stack-list-frames)
       │                             ├── query locals (-stack-list-locals)
       │                             │
       │                             ├── build JSON response
       │  ◀── send JSON ────────────┤
       │                             │
       ▼                             ▼
print JSON response              wait for next connection...
```

---

## Platform & Toolchain Auto-Detection

Before any debugging can happen, the system needs to know what tools are available. The `info` subcommand discovers everything automatically:

```bash
python cpp_debug_session.py info
```

```json
{
  "platform": { "os": "win32", "os_name": "Windows", "arch": "AMD64" },
  "compilers": [
    { "name": "msvc", "path": "C:\\...\\cl.exe", "debug_format": "pdb" },
    { "name": "clang", "path": "C:\\...\\clang.exe", "debug_format": "dwarf" }
  ],
  "debuggers": [
    { "name": "cdb", "path": "C:\\...\\cdb.exe", "debug_formats": ["pdb"] }
  ],
  "recommendation": {
    "compiler": { "name": "msvc" },
    "debugger": { "name": "cdb" },
    "note": "MSVC + CDB (native Windows debugger, reads PDB)"
  }
}
```

With `--repo`, the output also includes repository context:
```bash
python cpp_debug_session.py info --repo /path/to/project
```
```json
{
  "platform": { "...": "..." },
  "compilers": ["..."],
  "debuggers": ["..."],
  "recommendation": { "...": "..." },
  "repo_context": {
    "build_system": { "build_system": "cmake", "marker": "CMakeLists.txt", "default_cmd": "cmake -B build -DCMAKE_BUILD_TYPE=Debug && cmake --build build" },
    "doc_files": ["README.md", "docs/BUILD.md"],
    "build_hints": ["cmake -B build -DCMAKE_BUILD_TYPE=Debug"],
    "source_dirs": ["src/core", "src/platform"],
    "test_dirs": ["src/test/lib", "src/test/bin"],
    "has_tests": true
  }
}
```

### What It Searches

| Tool Type | Search Locations |
|-----------|-----------------|
| **MSVC** (`cl.exe`) | PATH, then Visual Studio install via `vswhere.exe` (all MSVC version dirs, multiple host/target arch combos) |
| **GCC / Clang** | PATH, then VS LLVM directories for Clang |
| **GDB** | PATH |
| **LLDB** | PATH, then VS LLVM directories on Windows, `xcrun --find lldb` on macOS |
| **CDB** | PATH, then Windows SDK `Debuggers\{x64,x86,arm64}` directories, then WinDbg Preview (MSIX) install |

### Validation

Every tool binary is **validated** before being reported as available. The script runs `--version` (or `-version` for CDB) and checks that the process exits cleanly. This catches:
- Binaries with missing DLLs (common with VS-bundled LLDB on Windows)
- Corrupt installs
- Incompatible architectures

### Platform Preference Order

| Platform | Debugger Priority | Why |
|----------|------------------|-----|
| **Windows** | CDB → GDB → LLDB | CDB natively reads PDB from MSVC; same engine as WinDbg/Visual Studio |
| **Linux** | GDB → LLDB | GDB MI gives structured output; standard on most distros |
| **macOS** | LLDB → GDB | LLDB ships with Xcode; GDB requires code signing on macOS |

---

## The Three Debugger Backends

All three backends present the **same interface** to the debug server — the same set of `cmd_*` methods returning the same JSON schema. The server doesn't know or care which backend is running.

### GDB (Machine Interface)

```
Agent command       →  GDB MI command              →  GDB MI response
─────────────          ──────────────                  ───────────────
start               →  -exec-run                   →  *stopped,reason="breakpoint-hit",...
step_over           →  -exec-next                  →  *stopped,reason="end-stepping-range",...
set_breakpoint 42   →  -break-insert 42            →  ^done,bkpt={number="1",file="...",line="42"}
inspect             →  -stack-info-frame            →  ^done,frame={file="...",line="42",func="main"}
                       -stack-list-frames           →  ^done,stack=[frame={...},...]
                       -stack-list-locals 1         →  ^done,locals=[{name="x",value="42"},...]
evaluate x          →  -data-evaluate-expression x  →  ^done,value="42"
```

GDB MI is the most reliable backend — it produces structured, machine-parseable output with explicit result tokens.

### LLDB (Text Parsing)

```
Agent command       →  LLDB command        →  Parse text output
─────────────          ────────────            ──────────────────
start               →  run                 →  "Process launched... stopped at main.c:10"
step_over           →  next                →  "frame #0: main at main.c:11"
set_breakpoint 42   →  breakpoint set      →  "Breakpoint 1: where = ...file.c:42"
                       --line 42
inspect             →  frame variable      →  "(int) x = 42\n(char *) name = \"hello\""
evaluate x          →  expression -- x     →  "(int) $0 = 42"
backtrace           →  bt                  →  "frame #0: main...  frame #1: ..."
```

LLDB output is human-readable text, so the backend uses regex patterns to extract file, line, function, and variable information.

### CDB (Windows Console Debugger)

```
Agent command       →  CDB command         →  Parse text output
─────────────          ───────────            ──────────────────
start               →  g                   →  "Breakpoint 0 hit\nmodule!main [file.c @ 42]"
step_over           →  p                   →  "module!main+0x1a [file.c @ 43]"
step_into           →  t                   →  "module!helper+0x0 [util.c @ 10]"
step_out            →  gu                  →  "module!main+0x20 [file.c @ 44]"
set_breakpoint 42   →  bp `file.c:42`      →  "Breakpoint 0 set"
inspect             →  dv /t               →  "int x = 42\nchar * name = 0x... \"hello\""
evaluate x          →  ?? x                →  "int 42"
backtrace           →  kn                  →  "00 module!main [file.c @ 42]\n01 KERNEL32!..."
list source         →  lsp -a @$ip -l 10   →  source lines around instruction pointer
breakpoints         →  bl                  →  " 0 e ... module!main+0x1a"
remove breakpoint 0 →  bc 0               →  (breakpoint cleared)
quit                →  q                   →  process exits
```

CDB uses the same debugging engine as WinDbg and the Visual Studio debugger. It reads PDB symbols natively, making it the ideal choice for MSVC-compiled binaries on Windows. Its prompt is `0:000>` (thread:frame).

---

## Repository Discovery

For debugging binaries from larger projects, three subcommands help the agent understand the repo before launching a debug session:

### `build-info` — Detect Build System

```bash
python cpp_debug_session.py build-info --repo /path/to/repo
```

Detects the build tool by looking for marker files: `CMakeLists.txt` (CMake), `Makefile` (Make), `meson.build` (Meson), `*.sln`/`*.csproj` (MSBuild), `Cargo.toml` (Cargo), `BUILD`/`WORKSPACE` (Bazel), `configure`/`configure.ac` (autotools), `build.ps1`, or `build.ninja` (Ninja). Returns the suggested debug build command.

### `find-binary` — Locate Built Executables

```bash
python cpp_debug_session.py find-binary --repo /path/to/repo
python cpp_debug_session.py find-binary --repo /path/to/repo --hint my_program
python cpp_debug_session.py find-binary --repo /path/to/repo --test
```

Searches standard build output directories (`build/`, `out/`, `bin/`, `Debug/`, `Release/`, `target/debug/`, `artifacts/`, etc.) for executables. With `--hint`, results matching the name are sorted first. With `--test`, only test-related binaries are returned.

### `repo-context` — Full Repository Scan

```bash
python cpp_debug_session.py repo-context --repo /path/to/repo
```

Combines build detection with documentation discovery, returning:
- Build system and suggested build command
- Documentation files (README, BUILD, CONTRIBUTING, etc.)
- Build hints extracted from docs (lines mentioning "build", "compile", "debug")
- Source directories containing `.c`/`.h` files
- Test directories
- Whether the project has tests

---

## Attach Mode and Core Dump Analysis

Beyond debugging executables from startup, the system supports two additional modes:

### Attach to Running Process

```bash
python cpp_debug_session.py serve --attach <PID> --port 5678
python cpp_debug_session.py serve ./my_server --attach <PID> --port 5678  # with symbol file
```

The debugger attaches to an already-running process. This is useful for:
- **Deadlocks** — see which threads are stuck and what mutexes they hold
- **Hangs** — find infinite loops or blocking I/O calls
- **Memory issues** — inspect heap state of a live process

Behind the scenes:
- **GDB**: `gdb --interpreter=mi -p <PID>`
- **LLDB**: `lldb -p <PID>`
- **CDB**: `cdb -p <PID>`

After attaching, the process is paused and all normal debug commands work (inspect, backtrace, evaluate, etc.). The `start` command resumes execution.

### Core Dump / Crash Dump Analysis

```bash
python cpp_debug_session.py serve ./my_app --core /tmp/core.12345 --port 5678
```

Opens a core dump for post-mortem analysis. The crash state is pre-loaded — you can immediately inspect the call stack, local variables, and memory at the point of the crash.

Behind the scenes:
- **GDB**: `gdb --interpreter=mi ./my_app /tmp/core.12345`
- **LLDB**: `lldb -c /tmp/core.12345`
- **CDB**: `cdb -z crash.dmp`

Core dumps work with:
- **Linux**: ELF core dumps (generate with `ulimit -c unlimited` or `gcore`)
- **Windows**: `.dmp` files from Task Manager, WER, or `procdump`
- **macOS**: `/cores/core.*` files

---

## Source Path Mapping

When debugging binaries built in a different directory or from a CI system, source paths may not match. The server handles this automatically and manually:

### Automatic
The server auto-detects the repository root by walking up from the target to find a `.git` directory. This root is then added as a source directory for the debugger.

### Manual
Pass additional source directories with `--srcpath`:
```bash
python cpp_debug_session.py serve ./my_app --port 5678 --srcpath /repo/src /repo/lib
```

Behind the scenes:
- **GDB**: `-environment-directory /repo/src`
- **LLDB**: `settings append target.source-map . /repo/src`
- **CDB**: `.srcpath+ /repo/src`

---

## How the AI Agent Drives a Session

### Step-by-Step Workflow

```
┌─ Agent reads source code ──────────────────────────────────┐
│  Understands program structure, identifies suspicious areas │
└────────────────────────────┬───────────────────────────────┘
                             ▼
┌─ Agent detects toolchain ──────────────────────────────────┐
│  Runs `info` to discover OS, compilers, debuggers          │
│  Decides: compile with MSVC? Use CDB or GDB?               │
└────────────────────────────┬───────────────────────────────┘
                             ▼
┌─ Agent discovers repo context (optional) ──────────────────┐
│  Runs `repo-context` to detect build system, doc files,    │
│  source dirs, test dirs. Uses `build-info` & `find-binary` │
│  to locate the target executable in build output dirs.     │
└────────────────────────────┬───────────────────────────────┘
                             ▼
┌─ Agent launches debug server ──────────────────────────────┐
│  `serve my_program.c --port 5678`                          │
│  Or: `serve --attach PID` (running process)                │
│  Or: `serve ./prog --core dump.dmp` (crash dump)           │
│  Source auto-compiled → debugger launched → server waiting  │
└────────────────────────────┬───────────────────────────────┘
                             ▼
┌─ Agent sets strategic breakpoints ─────────────────────────┐
│  Based on code reading + the bug description:              │
│  "Mean is wrong" → break at compute_mean()                 │
│  "Crash on second request" → break at handle_request()     │
└────────────────────────────┬───────────────────────────────┘
                             ▼
┌─ Agent starts execution ───────────────────────────────────┐
│  `start` → program runs → hits first breakpoint → pauses   │
│  (In attach mode: process is already paused after attach)  │
│  (In core dump mode: execution state is pre-loaded)        │
└────────────────────────────┬───────────────────────────────┘
                             ▼
                    ┌────────────────┐
                    │  Debug Loop    │ ◀─── Developer says "step over"
                    │                │      or "what is x?"
                    │  1. Translate  │      or "continue"
                    │     NL → cmd   │
                    │  2. Send cmd   │───▶ TCP → server → debugger
                    │  3. Get JSON   │◀─── TCP ← server ← debugger
                    │  4. Interpret  │
                    │  5. Explain    │───▶ "We're at line 42, x = NULL"
                    │  6. Suggest    │───▶ "Want me to check the caller?"
                    │                │
                    └───────┬────────┘
                            │
                 Developer says "quit"
                            │
                            ▼
                    ┌────────────────┐
                    │  Session ends  │
                    │  Bugs summarized│
                    └────────────────┘
```

### What Makes It "AI-Driven"

A traditional debugging session requires the developer to:
1. Know which debugger to use (GDB? LLDB? CDB?)
2. Remember debugger-specific commands (`-exec-next` vs `next` vs `p`)
3. Manually interpret raw output (register values, memory addresses)
4. Decide where to look next

The AI agent handles all of this:

| Traditional Debugging | AI-Driven Debugging |
|---|---|
| `gdb --interpreter=mi ./program` | "Debug my_program.c — the mean is wrong" |
| `5-break-insert compute_mean` | Agent reads code, decides where to break |
| `^done,bkpt={number="1",...}` | "Breakpoint set at compute_mean, line 55" |
| `6-exec-run` | "Starting the program..." |
| `*stopped,reason="breakpoint-hit",frame={...}` | "Paused in compute_mean(). sum=572, count=8" |
| `7-data-evaluate-expression sum/count` | Agent notices integer division and explains it |
| Developer must realize the bug themselves | "**Bug found**: `sum / count` does integer division — cast to `(double)sum / count`" |

### The Agent's Reasoning Process

When the agent receives a JSON response from the debug server, it doesn't just relay it — it **reasons**:

```json
{
  "status": "paused",
  "current_location": { "file": "stats.c", "line": 55, "function": "compute_mean" },
  "local_variables": {
    "sum": { "type": "int", "value": "572" },
    "count": { "type": "int", "value": "8" },
    "mean": { "type": "double", "value": "71.0" }
  }
}
```

The agent sees:
- `sum` is `int`, `count` is `int` → `sum / count` is integer division in C
- `572 / 8 = 71` (integer), but should be `71.5` (float)
- `mean` is `double` but receives the already-truncated `71`
- This explains why the output is wrong

It then explains this to the developer in plain language and suggests the fix.

---

## JSON Protocol

Every command returns a consistent JSON response:

```json
{
  "status": "paused",
  "command": "step_over",
  "message": "Stepped to stats.c:56 in compute_mean()",
  "current_location": {
    "file": "stats.c",
    "line": 56,
    "function": "compute_mean",
    "code_context": "    double mean = sum / count;"
  },
  "call_stack": [
    { "frame_index": 0, "file": "stats.c", "line": 56, "function": "compute_mean", "code_context": "..." },
    { "frame_index": 1, "file": "stats.c", "line": 78, "function": "main", "code_context": "..." }
  ],
  "local_variables": {
    "sum":   { "type": "int",    "value": "572",  "repr": "572" },
    "count": { "type": "int",    "value": "8",    "repr": "8" },
    "mean":  { "type": "double", "value": "71.0", "repr": "71.0" }
  },
  "stdout_new": "",
  "stderr_new": ""
}
```

| Field | Description |
|-------|-------------|
| `status` | `"paused"` (waiting for next command), `"completed"` (program exited), or `"error"` |
| `command` | The command that produced this response |
| `message` | Human-readable summary of what happened |
| `current_location` | File, line, function, and source code at the current stop point |
| `call_stack` | Full stack trace showing how execution reached this point |
| `local_variables` | All variables in the current scope with types and values |
| `stdout_new` | Any new program output since the last command |
| `stderr_new` | Any new stderr output |

The `status` field drives the agent's behavior:
- **`paused`** → ask the developer what to do next, or proactively suggest an action
- **`completed`** → the program finished; summarize findings
- **`error`** → explain the error and suggest recovery

---

## Auto-Compile Pipeline

When you pass a source file (`.c` / `.cpp`) instead of a compiled executable, the system handles compilation automatically:

```
source.c
    │
    ▼
ToolchainInfo.recommend()
    │  picks best compiler for platform
    ▼
compile_source()
    │  gcc -g -O0 -o source source.c          (Linux)
    │  clang -g -O0 -o source source.c        (macOS)
    │  cl /Zi /Od /Fe:source.exe source.c     (Windows/MSVC)
    │
    │  If clang on Windows and link.exe not on PATH:
    │  → auto-invokes vcvarsall.bat to set up MSVC linker environment
    │
    ▼
source.exe (with debug symbols)
    │
    ▼
Debug server starts with compiled executable
```

This means the developer never needs to think about compiler flags for debug symbols. The system always compiles with `-g -O0` (GCC/Clang) or `/Zi /Od` (MSVC) to ensure full debug info and no optimization.

---

## Supported Commands

All commands work identically across Python, GDB, LLDB, and CDB backends:

| Command | Alias | Description |
|---------|-------|-------------|
| `start` | `s` | Begin program execution |
| `continue` | `c` | Continue to next breakpoint or end |
| `step_in` | `si` | Step into the next function call |
| `step_over` | `n` | Execute current line, stay in same function |
| `step_out` | `so` | Run until current function returns |
| `run_to_line` | `rt` | Run until reaching a specific line |
| `set_breakpoint` | `b` | Set a breakpoint: `b 42`, `b file.c:42`, `b main` |
| `remove_breakpoint` | `rb` | Remove a breakpoint by number |
| `breakpoints` | `bl` | List all active breakpoints |
| `inspect` | `i` | Show current location + call stack + all locals |
| `evaluate` | `e` | Evaluate an expression in the current context |
| `list` | `l` | Show source code around the current position |
| `backtrace` | `bt` | Full call stack trace |
| `quit` | `q` | End the debug session |

Behind the scenes, each command maps to different native debugger commands:

| Command | GDB (MI) | LLDB | CDB | Python (bdb) |
|---------|----------|------|-----|--------------|
| `start` | `-exec-run` | `run` | `g` | `set_trace()` + resume |
| `continue` | `-exec-continue` | `continue` | `g` | `set_continue()` |
| `step_over` | `-exec-next` | `next` | `p` | `set_next()` |
| `step_in` | `-exec-step` | `step` | `t` | `set_step()` |
| `step_out` | `-exec-finish` | `finish` | `gu` | `set_return()` |
| `breakpoint` | `-break-insert` | `breakpoint set` | `bp` | `set_break()` |
| `locals` | `-stack-list-locals` | `frame variable` | `dv /t` | `f_locals` |
| `eval` | `-data-evaluate-expression` | `expression --` | `??` | `eval()` |
| `backtrace` | `-stack-list-frames` | `bt` | `kn` | `inspect.stack()` |
| `quit` | `-gdb-exit` | `quit` | `q` | `set_quit()` |

---

## Session Lifecycle

```
                    ┌──────────────────────────────────────────┐
                    │              Server Process              │
                    │                                          │
 serve program.c ──▶│  1. Auto-compile (if source file)       │
                    │  2. Detect debugger (CDB/GDB/LLDB)      │
                    │  3. Launch debugger subprocess            │
                    │  4. Wait for TCP connections on port     │
                    │                                          │
    cmd b main ────▶│  5. Set breakpoint (before start)       │◀── can set
    cmd b 42   ────▶│  6. Set another breakpoint              │    multiple
                    │                                          │
    cmd start  ────▶│  7. Begin execution → hits breakpoint   │
                    │     → returns paused state as JSON       │
                    │                                          │
    cmd inspect ───▶│  8. Query variables, stack, location    │
    cmd n      ────▶│  9. Step over → returns new state       │◀── repeat
    cmd e expr ────▶│ 10. Evaluate → returns result           │    many
    cmd c      ────▶│ 11. Continue → hits next breakpoint     │    times
                    │                                          │
    cmd quit   ────▶│ 12. Kill debugger, close server         │
                    └──────────────────────────────────────────┘
```

State transitions:
- Server starts → `waiting for start` (only `b` and `start` accepted)
- `start` → program runs → `paused` (all commands accepted)
- `continue` / `step_*` → program runs briefly → `paused` again
- Program exits normally → `completed`
- Program crashes (SIGSEGV, etc.) → `paused` at crash site (can inspect stack)
- `quit` → session ends

---

## Illustrative Walkthrough

Here's a complete end-to-end session showing all the moving parts. The developer wants to debug a C program where the computed mean is wrong.

### 1. Developer Invokes the Agent

> **Developer:** `@NeuralDebug` debug `sample_buggy_stats.c` — the mean shows 0.00 but should be 72.75.

### 2. Agent Reads the Source Code

The agent reads the file and identifies:
- `compute_sum()` — sums scores in a loop
- `compute_mean()` — calls `compute_sum()` then divides
- `count_passing()` — counts scores >= 60
- `main()` — orchestrates everything

### 3. Agent Detects the Platform

```bash
$ python cpp_debug_session.py info
```
→ Windows, MSVC available, CDB available → will use MSVC + CDB.

### 4. Agent Launches the Debug Server

```bash
# Background terminal:
$ python cpp_debug_session.py serve sample_buggy_stats.c --port 5678

Detected source file (.c). Auto-compiling with debug symbols...
Compiling: cl /Zi /Od /Fe:sample_buggy_stats.exe sample_buggy_stats.c
Compiled sample_buggy_stats.c -> sample_buggy_stats.exe using msvc
Platform: win32 (AMD64)
Using debugger: cdb (C:\...\cdb.exe)
C/C++ Debug server listening on port 5678
```

### 5. Agent Sets a Strategic Breakpoint

> **Agent (thinking):** "The mean is wrong. Let me break at `compute_sum` since that's where the data is accumulated."

```bash
$ python cpp_debug_session.py cmd b compute_sum
```
→ `{"status": "running", "message": "Breakpoint set at compute_sum"}`

### 6. Agent Starts Execution

```bash
$ python cpp_debug_session.py cmd start
```
→ Program runs and hits the breakpoint:
```json
{
  "status": "paused",
  "current_location": { "file": "sample_buggy_stats.c", "line": 40, "function": "compute_sum" },
  "local_variables": {
    "scores": { "type": "int *", "value": "0x00aff780" },
    "count":  { "type": "int", "value": "8" },
    "total":  { "type": "int", "value": "0" }
  }
}
```

> **Agent:** "Paused at the entry of `compute_sum()`. The array has 8 scores. I'll step through the loop to watch `total` accumulate."

### 7. Agent Steps Through, Discovers the Bug

After stepping through iterations, the agent sees the loop runs **9 times** for an 8-element array:

> **Agent:** "The loop condition `i <= count` iterates from 0 to 8 inclusive — that's 9 iterations for 8 elements. On the 9th iteration, `scores[8]` reads garbage memory past the end of the array. **Bug found**: change `i <= count` to `i < count`."

### 8. Developer Asks to Check More

> **Developer:** "Check the mean calculation too."

```bash
$ python cpp_debug_session.py cmd b compute_mean
$ python cpp_debug_session.py cmd continue
```

> **Agent:** "Paused in `compute_mean()`. `sum = 572`, `count = 8`. The code does `sum / count` — but both are `int`, so C performs integer division: `572 / 8 = 71`, not `71.5`. **Second bug**: cast to `(double)sum / count`."

### 9. Session Ends

> **Developer:** "Got it, quit."

```bash
$ python cpp_debug_session.py cmd quit
```

> **Agent:** Two bugs found:
> 1. **Line 42**: `i <= count` → `i < count` (off-by-one, reads past array)
> 2. **Line 55**: `sum / count` → `(double)sum / count` (integer division truncation)

---

## Why This Design?

### Separation of Concerns

| Component | Responsibility | Can be replaced? |
|-----------|---------------|-----------------|
| AI Agent | Natural language understanding, reasoning, hypothesis formation | Yes — any LLM or even a human using the cmd client |
| Debug Server | Protocol translation, session persistence, JSON responses | Stays the same regardless of which agent or debugger |
| Debugger Backend | Platform-specific debugging mechanics | GDB ↔ LLDB ↔ CDB — all present the same interface |

### Robustness

- **Tool validation** prevents cryptic failures — broken LLDB with missing DLLs is detected before the session starts, not mid-debug
- **Health checks** after launching the debugger subprocess catch immediate crashes
- **Auto-compilation** eliminates "forgot to compile with debug symbols" errors
- **Platform auto-detection** means the same workflow works on Windows, Linux, and macOS
- **Repo discovery** helps the agent understand unfamiliar projects before debugging
- **Source path mapping** auto-detects the repo root and maps source paths so debugging works even when the binary was built elsewhere

### Extensibility

Adding a new debugger backend requires:
1. A new class implementing `cmd_start`, `cmd_continue`, `cmd_step_in`, `cmd_step_over`, `cmd_step_out`, `cmd_set_breakpoint`, `cmd_remove_breakpoint`, `cmd_list_breakpoints`, `cmd_inspect`, `cmd_evaluate`, `cmd_list_source`, `cmd_backtrace`, `cmd_quit`
2. Registration in `create_debugger()` and `find_debugger()`
3. Detection logic in `ToolchainInfo._detect_debuggers()`

Adding a new build system requires:
1. A new entry in `detect_build_system()` with the marker file and default build command

The JSON response schema stays the same, so the agent and client code need no changes.

---

## Quick Reference

### Start a session
```bash
python cpp_debug_session.py serve program.c --port 5678      # auto-compile + debug
python cpp_debug_session.py serve ./program.exe --port 5678   # pre-compiled
python cpp_debug_session.py serve program.c --debugger cdb    # force CDB
python cpp_debug_session.py serve ./prog --args "--flag val"  # with program arguments
python cpp_debug_session.py serve --attach 4523 --port 5678   # attach to running process
python cpp_debug_session.py serve ./app --core dump --port 5678  # core dump analysis
python cpp_debug_session.py serve ./app --srcpath /src /lib   # with source paths
python python_debug_session.py serve script.py --port 5678    # Python target
```

### Send commands
```bash
python cpp_debug_session.py cmd b main          # breakpoint at function
python cpp_debug_session.py cmd b file.c:42     # breakpoint at file:line
python cpp_debug_session.py cmd start            # begin execution
python cpp_debug_session.py cmd step_over        # or: n
python cpp_debug_session.py cmd inspect          # or: i
python cpp_debug_session.py cmd "e sizeof(buf)"  # evaluate expression
python cpp_debug_session.py cmd continue         # or: c
python cpp_debug_session.py cmd quit             # or: q
```

### Check available tools
```bash
python cpp_debug_session.py info                 # JSON: platform, compilers, debuggers
python cpp_debug_session.py info --repo .         # include repository context
```

### Repository discovery
```bash
python cpp_debug_session.py build-info --repo .           # detect build system
python cpp_debug_session.py find-binary --repo .          # find built executables
python cpp_debug_session.py find-binary --repo . --test   # find test binaries
python cpp_debug_session.py repo-context --repo .         # full repo scan
```
