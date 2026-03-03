---
name: debugger
description: Debug Python, C/C++, C#, Rust, Java, Go, Node.js/TypeScript, and Ruby programs interactively (persistent session with step/continue/inspect commands) or non-interactively (one-shot). Python uses stdlib bdb; C/C++ drives GDB (MI mode), LLDB, or CDB; C# uses netcoredbg (MI mode); Rust uses rust-gdb/rust-lldb/GDB/LLDB; Java uses JDB; Go uses Delve; Node.js uses built-in inspector; Ruby uses rdbg. Supports auto-detection of platform, compilers, and debuggers.
---
# Debugger Skill

Supports **Python**, **C/C++**, **C#**, **Rust**, **Java**, **Go**, **Node.js/TypeScript**, and **Ruby** debugging with identical command interfaces.

## Language Support

| Language | Script | Backend | Requirements |
|----------|--------|---------|---------------|
| Python | `python_debug_session.py` | bdb (stdlib) | Python 3.7+ |
| C/C++ | `cpp_debug_session.py` | GDB, LLDB, or CDB | Auto-detected; debug symbols (`-g` / `/Zi`) |
| C# | `csharp_debug_session.py` | netcoredbg (MI mode) | .NET SDK + netcoredbg |
| Rust | `rust_debug_session.py` | rust-gdb, rust-lldb, GDB, LLDB | Rust toolchain (cargo/rustc) + debugger |
| Java | `java_debug_session.py` | JDB (Java Debugger) | JDK (includes JDB) |
| Go | `go_debug_session.py` | Delve (dlv) | Go 1.16+ + Delve |
| Node.js/TS | `nodejs_debug_session.py` | Node Inspector | Node.js 12+ |
| Ruby | `ruby_debug_session.py` | rdbg (debug.gem) | Ruby 3.2+ or `gem install debug` |

## Prerequisites
- Python 3.7+ (for running the debug scripts themselves)
- **Python targets**: The target `.py` file must be syntactically valid and runnable
- **C/C++ targets**: A compiled executable with debug symbols, or a `.c`/`.cpp` source file (auto-compiled)
- **C# targets**: A .NET project (.csproj/.sln) or compiled DLL; requires netcoredbg
- **Rust targets**: A Cargo project, `.rs` file, or pre-built executable; requires GDB or LLDB
- **Java targets**: A `.java` file, class name, or `.jar` file; requires JDK (JDB is bundled)
- **Go targets**: A `.go` file, Go package, or pre-built executable; requires Delve (`go install github.com/go-delve/delve/cmd/dlv@latest`)
- **Node.js targets**: A `.js`/`.mjs`/`.ts` file or package.json project; Node.js includes the inspector
- **Ruby targets**: A `.rb` file or directory with Gemfile; requires rdbg (`gem install debug` or Ruby 3.2+)
- **Debuggers**: Auto-detected per platform; the scripts give install instructions if missing
---
# Mode 1: One-Shot Debugging
Use this when you want to quickly capture state at specific breakpoints without interaction.
## Set a Breakpoint and Run
```bash
python src/NeuralDebug/python_debugger.py debug <target.py> --breakpoint <LINE> --output <output.json>
```
### Examples
```bash
# Single breakpoint
python src/NeuralDebug/python_debugger.py debug my_script.py --breakpoint 42 --output .deepdebug/result.json
# Multiple breakpoints
python src/NeuralDebug/python_debugger.py debug my_script.py -b 42 -b 87 -o .deepdebug/result.json
# Conditional breakpoint
python src/NeuralDebug/python_debugger.py debug my_script.py -b 42 --condition "x > 10" -o .deepdebug/result.json
# With script arguments
python src/NeuralDebug/python_debugger.py debug my_script.py -b 42 --args "input.txt --verbose"
# Limit hits in loops
python src/NeuralDebug/python_debugger.py debug my_script.py -b 42 --max-hits 3
```
### Parameters
| Parameter      | Required | Default | Description |
|----------------|----------|---------|-------------|
| `target`       | Yes      | --      | Path to the Python file to debug |
| `--breakpoint` | Yes      | --      | Line number(s) for breakpoints (repeatable) |
| `--max-hits`   | No       | 5       | Max breakpoint hits to capture |
| `--condition`  | No       | None    | Python expression; break only when truthy |
| `--args`       | No       | ""      | Space-separated args to pass to the script |
| `--timeout`    | No       | 30      | Timeout in seconds (0 = no timeout) |
| `--output`     | No       | stdout  | Path to write JSON results |
## Read Results
```bash
python src/NeuralDebug/python_debugger.py inspect .deepdebug/result.json
```
---
# Mode 2: Interactive Debug Session
Use this for a real debugging experience where the session stays alive and you send commands one at a time -- step in, step out, continue, inspect variables, evaluate expressions.
## Architecture
```
Terminal 1 (background): Debug server holds the paused program
Terminal 2 (foreground): Client sends one command at a time, gets JSON response
```
## Step 1: Start the Debug Server (background)
```bash
python src/NeuralDebug/python_debug_session.py serve <target.py> --port 5678
```
The server starts and waits for commands. The target program does NOT begin executing until you send `start`.
## Step 2: (Optional) Set Breakpoints Before Starting
```bash
python src/NeuralDebug/python_debug_session.py cmd --port 5678 b 42
python src/NeuralDebug/python_debug_session.py cmd --port 5678 b 87
```
## Step 3: Start Execution
```bash
python src/NeuralDebug/python_debug_session.py cmd --port 5678 start
```
The program begins and pauses at the first line (or first breakpoint if any were set).
## Step 4: Send Debug Commands
Each command pauses execution, returns state, and waits for the next command.
### Available Commands
| Command | Shortcut | Description | Example |
|---------|----------|-------------|---------|
| `start` | `s` | Begin program execution | `cmd start` |
| `continue` | `c` | Continue to next breakpoint | `cmd continue` |
| `step_in` | `si` | Step into the next function call | `cmd step_in` |
| `step_over` | `n` | Step to next line (over function calls) | `cmd step_over` |
| `step_out` | `so` | Step out of the current function | `cmd step_out` |
| `run_to_line` | `rt` | Run until reaching a specific line | `cmd run_to_line 50` |
| `set_breakpoint` | `b` | Set a breakpoint (with optional condition) | `cmd b 42 x > 10` |
| `remove_breakpoint` | `rb` | Remove a breakpoint | `cmd remove_breakpoint 42` |
| `breakpoints` | `bl` | List all active breakpoints | `cmd breakpoints` |
| `inspect` | `i` | Show call stack + local variables | `cmd inspect` |
| `evaluate` | `e` | Evaluate a Python expression | `cmd e len(my_list)` |
| `list` | `l` | Show source code around current line | `cmd list 10` |
| `ping` | `health` | Check if server is alive | `cmd ping` |
| `quit` | `q` | End the debug session | `cmd quit` |
### Command Format
```bash
python src/NeuralDebug/python_debug_session.py cmd --port <PORT> <COMMAND> [ARGS]
```

### Check Server Status
```bash
python src/NeuralDebug/python_debug_session.py status --port <PORT>
```
Returns `{"server_running": true/false}` — use this to detect an existing session before launching a new server.

## Response Format
Every command returns JSON:
```json
{
  "status": "paused | completed | error",
  "command": "the command that was run",
  "message": "Human-readable description",
  "current_location": {
    "file": "my_script.py",
    "line": 42,
    "function": "calculate_mean",
    "code_context": "    total = sum(scores)"
  },
  "call_stack": [
    {"frame_index": 0, "file": "my_script.py", "line": 42, "function": "calculate_mean", "code_context": "..."},
    {"frame_index": 1, "file": "my_script.py", "line": 90, "function": "generate_report", "code_context": "..."}
  ],
  "local_variables": {
    "scores": {"type": "list", "value": "[92, 85, 73]", "repr": "[92, 85, 73]"},
    "total": {"type": "int", "value": "250", "repr": "250"}
  },
  "stdout_new": "any new stdout since last command",
  "stderr_new": "any new stderr since last command"
}
```
## Interactive Session Example
```bash
# Terminal 1 (background): Start server
python src/NeuralDebug/python_debug_session.py serve sample_buggy_grades.py --port 5678
# Terminal 2: Drive the session
python src/NeuralDebug/python_debug_session.py cmd b 44           # breakpoint at filter
python src/NeuralDebug/python_debug_session.py cmd start           # begin execution
# -> pauses at line 44 in filter_valid_grades()
python src/NeuralDebug/python_debug_session.py cmd step_over       # next line
# -> line 45: valid.append((name, score)), name='Alice', score=92
python src/NeuralDebug/python_debug_session.py cmd "e score"       # evaluate expression
# -> score = 92
python src/NeuralDebug/python_debug_session.py cmd continue        # run to next breakpoint hit
# -> pauses at line 44 again with name='Eve', score=0
python src/NeuralDebug/python_debug_session.py cmd "e valid"       # check what's accumulated
# -> valid = [('Alice', 92), ('Bob', 85)]
python src/NeuralDebug/python_debug_session.py cmd quit            # end session
```

---

# C/C++ Interactive Debug Session

Use `cpp_debug_session.py` for debugging compiled C/C++ executables via GDB or LLDB. The interface and JSON response format are identical to the Python debugger.

## Platform & Toolchain Detection

The script auto-detects the current platform and available tools:

```bash
python src/NeuralDebug/cpp_debug_session.py info
```

Returns JSON showing:
- **Platform**: OS (Windows/Linux/macOS), architecture (x86_64/aarch64)
- **Compilers**: MSVC, GCC, Clang (paths and versions) -- searches PATH and Visual Studio directories
- **Debuggers**: GDB, LLDB, CDB (paths and versions) -- searches PATH, Visual Studio directories, Windows SDK Debuggers paths, xcrun on macOS
- **Validation**: Each tool is verified to actually start (catches missing DLLs, broken installs)
- **Recommendation**: Best compiler + debugger pair for the platform

### Platform Defaults

| Platform | Preferred Compiler | Preferred Debugger | Notes |
|----------|-------------------|-------------------|-------|
| Windows  | MSVC (`cl.exe`) or GCC (MSYS2) | CDB (native PDB), then GDB | CDB found in Windows SDK Debuggers or via `winget install Microsoft.WinDbg`; MSVC found via PATH or vswhere |
| Linux    | GCC | GDB | Standard packages |
| macOS    | Clang (Xcode) | LLDB (Xcode) | `xcrun lldb` fallback |

## Auto-Compile Source Files

You can pass `.c` or `.cpp` source files directly -- they are compiled automatically:

```bash
# Auto-detect compiler, compile with debug symbols, then debug:
python src/NeuralDebug/cpp_debug_session.py serve my_program.c --port 5678

# Compile separately (e.g. with custom flags):
python src/NeuralDebug/cpp_debug_session.py compile my_program.c
python src/NeuralDebug/cpp_debug_session.py compile my_program.cpp -o custom_name.exe
python src/NeuralDebug/cpp_debug_session.py compile my_program.c --compiler gcc --flags "-lm -lpthread"
python src/NeuralDebug/cpp_debug_session.py compile my_program.c --compiler msvc
python src/NeuralDebug/cpp_debug_session.py compile my_program.c --compiler clang
```

The `--compiler` flag accepts well-known names (`msvc`, `cl`, `gcc`, `g++`, `clang`, `clang++`) or a direct path to a compiler binary.

The compiler is chosen automatically based on what's available:
- **Windows**: MSVC if on PATH (Developer Command Prompt), otherwise GCC/Clang from MSYS2. If using Clang on Windows, `vcvarsall.bat` is auto-invoked to provide the MSVC linker environment.
- **Linux**: GCC (falls back to Clang)
- **macOS**: Clang (Xcode Command Line Tools)

## Compile with Debug Symbols (Manual)

If you prefer to compile manually:

```bash
# GCC / Clang
gcc -g -O0 -o my_program my_program.c
g++ -g -O0 -o my_program my_program.cpp

# MSVC
cl /Zi /Od my_program.c /Fe:my_program.exe
```

## Step 1: Start the C/C++ Debug Server (background)

```bash
# From an executable:
python src/NeuralDebug/cpp_debug_session.py serve ./my_program --port 5678

# From a source file (auto-compiles):
python src/NeuralDebug/cpp_debug_session.py serve my_program.c --port 5678

# Force a specific debugger:
python src/NeuralDebug/cpp_debug_session.py serve ./my_program --debugger gdb --port 5678
python src/NeuralDebug/cpp_debug_session.py serve ./my_program --debugger lldb --port 5679
python src/NeuralDebug/cpp_debug_session.py serve ./my_program --debugger cdb --port 5680
```

The server auto-detects GDB, LLDB, or CDB. On Windows, CDB is preferred (native PDB support for MSVC-compiled binaries). On other platforms, GDB is preferred.

## Step 2: Set Breakpoints Before Starting

```bash
python src/NeuralDebug/cpp_debug_session.py cmd --port 5678 b main           # by function name
python src/NeuralDebug/cpp_debug_session.py cmd --port 5678 b 42             # by line number
python src/NeuralDebug/cpp_debug_session.py cmd --port 5678 b main.c:42      # by file:line
```

## Step 3: Start Execution

```bash
python src/NeuralDebug/cpp_debug_session.py cmd --port 5678 start
```

## Step 4: Send Debug Commands

All commands from the Python debugger work identically:

| Command | Shortcut | Description | Example |
|---------|----------|-------------|---------|
| `start` | `s` | Begin program execution | `cmd start` |
| `continue` | `c` | Continue to next breakpoint | `cmd continue` |
| `step_in` | `si` | Step into function call | `cmd step_in` |
| `step_over` | `n` | Step to next line | `cmd step_over` |
| `step_out` | `so` | Step out of current function | `cmd step_out` |
| `run_to_line` | `rt` | Run to a specific line | `cmd run_to_line 50` |
| `set_breakpoint` | `b` | Set breakpoint | `cmd b main.c:42 x>0` |
| `remove_breakpoint` | `rb` | Remove breakpoint by GDB number | `cmd rb 1` |
| `breakpoints` | `bl` | List all breakpoints | `cmd breakpoints` |
| `inspect` | `i` | Call stack + local variables | `cmd inspect` |
| `evaluate` | `e` | Evaluate C/C++ expression | `cmd e sizeof(buf)` |
| `list` | `l` | Show source around current line | `cmd list` |
| `backtrace` | `bt` | Full call stack trace | `cmd backtrace` |
| `ping` | `health` | Check if server is alive | `cmd ping` |
| `quit` | `q` | End session | `cmd quit` |

### Command Format
```bash
python src/NeuralDebug/cpp_debug_session.py cmd --port <PORT> <COMMAND> [ARGS]
```

### Check Server Status
```bash
python src/NeuralDebug/cpp_debug_session.py status --port <PORT>
```
Returns `{"server_running": true/false}` — use this to reconnect to an existing debug session across prompts.

### Stop Server
```bash
python src/NeuralDebug/cpp_debug_session.py stop --port <PORT>
```
Gracefully stops the server (sends quit, then force-kills by PID if needed).

### Launch as Daemon (Persistent Server)
```bash
python src/NeuralDebug/cpp_debug_session.py serve ./my_program --port 5678 --daemonize
```
The `--daemonize` flag spawns the server as a fully detached OS process that survives terminal closure. Returns JSON with PID. Use `status` to check and `stop` to terminate.

### Attach to a Running Process

All languages (except Python) support attaching to an already-running process by PID:

```bash
# C/C++ (GDB/LLDB/CDB)
python src/NeuralDebug/cpp_debug_session.py serve --attach_pid 12345 --port 5678

# C# (.NET via netcoredbg)
python src/NeuralDebug/csharp_debug_session.py serve --attach_pid 12345 --port 5679

# Rust (GDB/LLDB)
python src/NeuralDebug/rust_debug_session.py serve --attach_pid 12345 --port 5680

# Java (JDB)
python src/NeuralDebug/java_debug_session.py serve --attach_pid 12345 --port 5681

# Go (Delve)
python src/NeuralDebug/go_debug_session.py serve --attach_pid 12345 --port 5682

# Node.js (inspector)
python src/NeuralDebug/nodejs_debug_session.py serve --attach_pid 12345 --port 5683

# Ruby (rdbg)
python src/NeuralDebug/ruby_debug_session.py serve --attach_pid 12345 --port 5684
```

The target argument is optional in attach mode. After attaching, use the same debug commands (set_breakpoint, step_over, inspect, etc.) as in a normal session. Python uses bdb (in-process only) and cannot attach to external processes — start the target under NeuralDebug instead.

## C/C++ Session Example

```bash
# Launch persistent server (--daemonize makes it survive terminal closure)
python src/NeuralDebug/cpp_debug_session.py serve ./my_program --port 5678 --daemonize

# Drive the session
python src/NeuralDebug/cpp_debug_session.py cmd b main              # break at main()
python src/NeuralDebug/cpp_debug_session.py cmd start                # begin execution
# -> pauses at main() entry
python src/NeuralDebug/cpp_debug_session.py cmd step_over            # next line
python src/NeuralDebug/cpp_debug_session.py cmd "e buffer_size"      # evaluate variable
# -> buffer_size = 1024
python src/NeuralDebug/cpp_debug_session.py cmd b process_data       # break at function
python src/NeuralDebug/cpp_debug_session.py cmd continue             # run to breakpoint
# -> pauses at process_data() with args visible
python src/NeuralDebug/cpp_debug_session.py cmd inspect              # show all locals
python src/NeuralDebug/cpp_debug_session.py cmd quit                 # end session

# Or stop the daemonized server from any terminal:
python src/NeuralDebug/cpp_debug_session.py stop --port 5678
```

---

---

# C# Interactive Debug Session

Use `csharp_debug_session.py` for debugging .NET / C# programs via netcoredbg. The interface and JSON response format are identical to the Python and C/C++ debuggers.

## Prerequisites

```bash
# Install netcoredbg (Samsung's open-source .NET debugger with MI mode)
# Download from: https://github.com/Samsung/netcoredbg/releases
# Or on macOS: brew install netcoredbg

# Check availability:
python src/NeuralDebug/csharp_debug_session.py info
```

## Step 1: Start the C# Debug Server

```bash
# From a .NET project (auto-builds):
python src/NeuralDebug/csharp_debug_session.py serve MyApp.csproj --port 5679

# From a pre-built DLL:
python src/NeuralDebug/csharp_debug_session.py serve bin/Debug/net8.0/MyApp.dll --port 5679
```

## Step 2: Send Debug Commands

All commands from the Python debugger work identically:

```bash
python src/NeuralDebug/csharp_debug_session.py cmd --port 5679 b Program.cs:42
python src/NeuralDebug/csharp_debug_session.py cmd --port 5679 start
python src/NeuralDebug/csharp_debug_session.py cmd --port 5679 inspect
python src/NeuralDebug/csharp_debug_session.py cmd --port 5679 "e myList.Count"
python src/NeuralDebug/csharp_debug_session.py cmd --port 5679 continue
python src/NeuralDebug/csharp_debug_session.py cmd --port 5679 quit
```

---

# Rust Interactive Debug Session

Use `rust_debug_session.py` for debugging Rust programs via GDB/LLDB. Automatically uses `rust-gdb` or `rust-lldb` wrappers (bundled with rustup) for better Rust type display. The interface is identical.

## Prerequisites

```bash
# Rust toolchain (includes rust-gdb / rust-lldb):
# Install from https://rustup.rs/

# Check availability:
python src/NeuralDebug/rust_debug_session.py info
```

## Step 1: Start the Rust Debug Server

```bash
# From a Cargo project (auto-builds):
python src/NeuralDebug/rust_debug_session.py serve . --port 5680
python src/NeuralDebug/rust_debug_session.py serve ./my_project --bin myapp --port 5680

# From a pre-built binary:
python src/NeuralDebug/rust_debug_session.py serve ./target/debug/myapp --port 5680

# From a single .rs file (auto-compiles):
python src/NeuralDebug/rust_debug_session.py serve main.rs --port 5680

# Force a specific debugger:
python src/NeuralDebug/rust_debug_session.py serve ./target/debug/myapp --debugger rust-gdb
```

## Step 2: Send Debug Commands

```bash
python src/NeuralDebug/rust_debug_session.py cmd --port 5680 b main.rs:42
python src/NeuralDebug/rust_debug_session.py cmd --port 5680 start
python src/NeuralDebug/rust_debug_session.py cmd --port 5680 inspect
python src/NeuralDebug/rust_debug_session.py cmd --port 5680 "e my_vec.len()"
python src/NeuralDebug/rust_debug_session.py cmd --port 5680 continue
python src/NeuralDebug/rust_debug_session.py cmd --port 5680 quit
```

### Debugger Selection

| Platform | Preferred Debugger | Notes |
|----------|-------------------|-------|
| Linux | rust-gdb (GDB with Rust pretty-printers) | Bundled with rustup |
| macOS | rust-lldb (LLDB with Rust pretty-printers) | Bundled with rustup |
| Windows (MSVC) | CDB (native PDB support) | From Windows SDK |
| Windows (GNU) | rust-gdb / GDB | Via MSYS2 |

---
---

# Go Debugging (via Delve)

## Prerequisites (Go)

- **Go 1.16+**: Install from https://go.dev/dl/
- **Delve**: `go install github.com/go-delve/delve/cmd/dlv@latest`
- Ensure `$GOPATH/bin` or `$GOBIN` is in your PATH

## Detect Go Toolchain

```bash
python src/NeuralDebug/go_debug_session.py info
```

Returns JSON with Go version, Delve version, and GOPATH/GOBIN locations.

## Go Session Example

### Step 1: Start the Debug Server

```bash
# Debug a Go source file (auto-builds with debug symbols)
python src/NeuralDebug/go_debug_session.py serve main.go --port 5682

# Debug a Go package (builds and runs)
python src/NeuralDebug/go_debug_session.py serve ./cmd/myapp --port 5682

# Debug a pre-built binary
python src/NeuralDebug/go_debug_session.py serve ./myapp --port 5682

# With arguments
python src/NeuralDebug/go_debug_session.py serve main.go --port 5682 --args "-config config.yaml"
```

### Step 2: Send Debug Commands

```bash
python src/NeuralDebug/go_debug_session.py cmd --port 5682 b main.go:42
python src/NeuralDebug/go_debug_session.py cmd --port 5682 start
python src/NeuralDebug/go_debug_session.py cmd --port 5682 inspect
python src/NeuralDebug/go_debug_session.py cmd --port 5682 "e len(mySlice)"
python src/NeuralDebug/go_debug_session.py cmd --port 5682 backtrace
python src/NeuralDebug/go_debug_session.py cmd --port 5682 continue
python src/NeuralDebug/go_debug_session.py cmd --port 5682 quit
```

### Go-Specific Features

- **Goroutine debugging**: Delve can list and switch between goroutines
- **Auto-build**: Source files are built with `-gcflags=all="-N -l"` (no optimization, no inlining)
- **Go modules**: Automatically detects `go.mod` and builds from module root
- **Interface inspection**: Delve shows concrete types behind interfaces

### Limitations (Go)

- Optimized binaries (`-O` flags) strip debug info — always build with `-gcflags=all="-N -l"`
- CGo debugging may require fallback to GDB
- Delve doesn't support core dump analysis on all platforms

---
---

# Node.js / TypeScript Debugging (via Node Inspector)

## Prerequisites (Node.js)

- **Node.js 12+**: Install from https://nodejs.org/
- For TypeScript: `npm install -g ts-node` or `npm install -g tsx`
- No separate debugger installation needed — Node.js includes the inspector

## Detect Node.js Toolchain

```bash
python src/NeuralDebug/nodejs_debug_session.py info
```

Returns JSON with Node.js version, npm version, and TypeScript tooling availability.

## Node.js Session Example

### Step 1: Start the Debug Server

```bash
# Debug a JavaScript file
python src/NeuralDebug/nodejs_debug_session.py serve app.js --port 5683

# Debug a TypeScript file (auto-registers ts-node)
python src/NeuralDebug/nodejs_debug_session.py serve app.ts --port 5683

# Debug a package.json project (uses "main" field)
python src/NeuralDebug/nodejs_debug_session.py serve ./myproject --port 5683

# With arguments
python src/NeuralDebug/nodejs_debug_session.py serve server.js --port 5683 --args "--port 3000"
```

### Step 2: Send Debug Commands

```bash
python src/NeuralDebug/nodejs_debug_session.py cmd --port 5683 b app.js:42
python src/NeuralDebug/nodejs_debug_session.py cmd --port 5683 start
python src/NeuralDebug/nodejs_debug_session.py cmd --port 5683 inspect
python src/NeuralDebug/nodejs_debug_session.py cmd --port 5683 "e myArray.length"
python src/NeuralDebug/nodejs_debug_session.py cmd --port 5683 backtrace
python src/NeuralDebug/nodejs_debug_session.py cmd --port 5683 continue
python src/NeuralDebug/nodejs_debug_session.py cmd --port 5683 quit
```

### Node.js-Specific Features

- **TypeScript support**: Auto-detects `.ts` files and uses ts-node or tsx for debugging
- **ES Modules**: Supports both CommonJS (`.js`) and ES modules (`.mjs`)
- **Expression evaluation**: Full JavaScript expression support via `exec()`
- **Package.json detection**: Auto-finds entry point from the "main" field

### Limitations (Node.js)

- Async/await stepping can be confusing (V8 creates internal frames)
- Source maps for bundled/transpiled code require `--enable-source-maps`
- The `node inspect` CLI doesn't support conditional breakpoints natively
- Worker threads are not debuggable through the main inspector

---
---

# Ruby Debugging (via rdbg)

## Prerequisites (Ruby)

- **Ruby 3.2+** (includes debug.gem) or install manually: `gem install debug`
- For Bundler projects: ensure `debug` is in your Gemfile
- **rdbg** command must be in PATH

## Detect Ruby Toolchain

```bash
python src/NeuralDebug/ruby_debug_session.py info
```

Returns JSON with Ruby version, rdbg version, Bundler availability, and Gemfile detection.

## Ruby Session Example

### Step 1: Start the Debug Server

```bash
# Debug a Ruby file
python src/NeuralDebug/ruby_debug_session.py serve app.rb --port 5684

# Debug a Bundler project
python src/NeuralDebug/ruby_debug_session.py serve app.rb --port 5684 --bundler

# With arguments
python src/NeuralDebug/ruby_debug_session.py serve script.rb --port 5684 --args "--verbose input.txt"
```

### Step 2: Send Debug Commands

```bash
python src/NeuralDebug/ruby_debug_session.py cmd --port 5684 b app.rb:42
python src/NeuralDebug/ruby_debug_session.py cmd --port 5684 start
python src/NeuralDebug/ruby_debug_session.py cmd --port 5684 inspect
python src/NeuralDebug/ruby_debug_session.py cmd --port 5684 "e my_array.length"
python src/NeuralDebug/ruby_debug_session.py cmd --port 5684 backtrace
python src/NeuralDebug/ruby_debug_session.py cmd --port 5684 continue
python src/NeuralDebug/ruby_debug_session.py cmd --port 5684 quit
```

### Ruby-Specific Features

- **Bundler integration**: Auto-detects Gemfile and uses `bundle exec rdbg`
- **Rails support**: Detects Rails projects via `config/application.rb`
- **Method breakpoints**: `b MyClass#my_method` breaks at method entry
- **Expression evaluation**: Full Ruby expression support via `p` command
- **Pretty print**: Use `pp` for formatted object output

### Limitations (Ruby)

- rdbg requires Ruby 3.0+ (Ruby 2.x users need `byebug` which is not supported)
- Some C extension methods cannot be stepped into
- JRuby and TruffleRuby may have limited debugger support
- Forked processes need separate debug attachments

---

# Java Interactive Debug Session

Use `java_debug_session.py` for debugging Java programs via JDB (Java Debugger, bundled with every JDK). The interface is identical.

## Prerequisites

```bash
# JDK (includes JDB):
# Install from https://adoptium.net/ or via package manager

# Check availability:
python src/NeuralDebug/java_debug_session.py info
```

## Step 1: Start the Java Debug Server

```bash
# From a .java file (auto-compiles):
python src/NeuralDebug/java_debug_session.py serve Main.java --port 5681

# From a class name with classpath:
python src/NeuralDebug/java_debug_session.py serve com.example.Main --classpath ./target/classes --port 5681

# From a JAR file:
python src/NeuralDebug/java_debug_session.py serve app.jar --port 5681
```

## Step 2: Send Debug Commands

```bash
python src/NeuralDebug/java_debug_session.py cmd --port 5681 b Main:42
python src/NeuralDebug/java_debug_session.py cmd --port 5681 start
python src/NeuralDebug/java_debug_session.py cmd --port 5681 inspect
python src/NeuralDebug/java_debug_session.py cmd --port 5681 "e myList.size()"
python src/NeuralDebug/java_debug_session.py cmd --port 5681 continue
python src/NeuralDebug/java_debug_session.py cmd --port 5681 quit
```

### Java Breakpoint Syntax

| Format | Example | Description |
|--------|---------|-------------|
| `<line>` | `b 42` | Line in current class |
| `<class>:<line>` | `b Main:42` | Line in specific class |
| `<class>.<method>` | `b Main.process` | Method entry |
| `<pkg.class>:<line>` | `b com.example.Main:42` | Fully qualified |

---

## Important: Always Use NeuralDebug CLI — Never Create Temporary Scripts

When debugging programs, you **MUST** use the existing NeuralDebug CLI scripts exclusively.

**DO:**
- Start servers with: `python src/NeuralDebug/<lang>_debug_session.py serve <target> --port <port>`
- Set breakpoints with: `python src/NeuralDebug/<lang>_debug_session.py cmd --port <port> b <location>`
- Send all debug commands with: `python src/NeuralDebug/<lang>_debug_session.py cmd --port <port> <command>`
- Evaluate expressions with: `python src/NeuralDebug/<lang>_debug_session.py cmd --port <port> e <expr>`
- Stop sessions with: `python src/NeuralDebug/<lang>_debug_session.py cmd --port <port> quit`

**DO NOT:**
- Create temporary Python helper scripts (e.g., `_debug_session.py`, `_debug_helper.py`) to send raw socket commands
- Write custom scripts to interact with the debug server's TCP protocol directly
- Bypass the CLI interface for any reason

The CLI scripts are the canonical interface to NeuralDebug. They handle protocol details, error recovery, and response formatting. All debugging should go through them.

---

## Shell Execution Rules for AI Agents

When running NeuralDebug commands via an AI agent's shell/powershell tool, follow these rules to avoid "Invalid shell ID" errors and connection failures:

### 1. Set `initial_wait` based on command type

| Command category | Examples | `initial_wait` | Mode |
|-----------------|----------|----------------|------|
| Fast commands | `info`, `status`, `b`, `e`, `inspect`, `step_over`, `step_in`, `step_out`, `list`, `breakpoints`, `ping`, `quit` | 15 seconds | sync |
| Medium commands | `start`, `continue`, `run_to_line` | 60 seconds | sync |
| Long-running | `serve` | N/A | **async** with named shellId |

### 2. Never read from a completed sync shell

Sync-mode shells are **disposed after the command finishes**. If the initial response already contains the command output, do NOT call `read_powershell` / `read_shell` — the shell ID will be invalid. Only call read if the initial response explicitly says "still running."

### 3. Run `cmd` subcommands sequentially

The debug server is single-threaded and accepts one TCP connection at a time. Running multiple `cmd` calls in parallel will cause connection-refused errors. Send commands one at a time and wait for each response.

### 4. Use a stable named shellId for `serve`

```bash
# Good: named shellId you can reference later
shellId: "dbg-server"
python src/NeuralDebug/cpp_debug_session.py serve target.c --port 5678
```

### 5. If a shell ID becomes invalid, re-run the command

Do NOT retry `read_powershell` on an invalid shell ID. The shell has been disposed. Simply re-run the original command.

---

## Limitations

### Python
- The debugger runs the target script in the same Python process
- Very large objects are truncated in repr (max 500 chars)
- Only line breakpoints are supported (not function or exception breakpoints)

### C/C++
- Requires GDB, LLDB, or CDB installed (auto-detected per platform; searched in PATH, Visual Studio directories, and Windows SDK paths; validated before use; install instructions provided if missing)
- On Windows, CDB is preferred -- it natively reads PDB symbols from MSVC and uses the same engine as WinDbg / Visual Studio Debugger
- On Windows, the VS-bundled LLDB may not work due to missing DLLs -- the script validates tools before using them and skips broken ones
- CDB can be installed via `winget install Microsoft.WinDbg` or by adding the "Debugging Tools" component in the Windows SDK installer
- Target must be compiled with debug symbols (`-g` or `/Zi`) or passed as source file for auto-compile
- On Windows, Clang compilation auto-invokes `vcvarsall.bat` to set up the MSVC linker environment
- Optimized builds (`-O2`/`-O3`) may show unexpected stepping behavior
- GDB MI parsing handles common cases but complex types may show raw output
- LLDB backend uses text parsing which is less reliable than GDB MI
- CDB backend uses text parsing; conditional breakpoints require CDB-native `/w` syntax
- On Windows, MSVC + CDB is the recommended pair; MSVC + GDB has limited PDB support

### Both
- The server listens on localhost only (127.0.0.1)
- One debug session per port

### C#
- Requires netcoredbg installed separately (not bundled with .NET SDK)
- Single `.cs` files are not directly supported; use a .csproj project or compile to DLL first
- netcoredbg uses the same MI protocol as GDB, so the interaction is similar
- Async/await debugging may show compiler-generated state machine frames

### Rust
- Uses GDB/LLDB backends (same as C/C++) with Rust-specific pretty-printers
- `rust-gdb` and `rust-lldb` wrappers (bundled with rustup) provide better Rust type display
- On Windows with MSVC target, CDB is preferred for native PDB support
- Complex Rust types (closures, trait objects) may show less readable debugger output
- Single `.rs` files are auto-compiled with `rustc -g`; Cargo projects are built with `cargo build`

### Java
- JDB (Java Debugger) is bundled with every JDK installation
- JDB uses text-based interaction (less structured than GDB MI)
- Conditional breakpoints are not directly supported through this interface
- Source display requires source paths to be set correctly (`--srcpath`)
- JAR debugging requires the Main-Class manifest attribute
- Maven and Gradle projects can be auto-built; the main class must still be specified