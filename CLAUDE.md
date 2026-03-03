# NeuralDebug

This repo contains NeuralDebug, an AI-powered debugging framework for 8 languages.

## How to debug with NeuralDebug

You can debug any program by running the debug session scripts in `src/NeuralDebug/`.
Each script provides a persistent TCP debug server that you control via commands.

### Quick reference

```bash
# Check what debuggers are available
python src/NeuralDebug/python_debug_session.py info
python src/NeuralDebug/cpp_debug_session.py info

# Start a debug server (pick the right script for the language)
python src/NeuralDebug/python_debug_session.py serve <target.py> --port 5678 --daemonize
python src/NeuralDebug/cpp_debug_session.py serve <target.c> --port 5678 --daemonize

# Attach to an already-running process by PID
python src/NeuralDebug/cpp_debug_session.py serve --attach_pid 12345 --port 5678
python src/NeuralDebug/go_debug_session.py serve --attach_pid 12345 --port 5682

# Send debug commands
python src/NeuralDebug/python_debug_session.py cmd --port 5678 b <line>
python src/NeuralDebug/python_debug_session.py cmd --port 5678 start
python src/NeuralDebug/python_debug_session.py cmd --port 5678 step_over
python src/NeuralDebug/python_debug_session.py cmd --port 5678 inspect
python src/NeuralDebug/python_debug_session.py cmd --port 5678 evaluate "<expr>"
python src/NeuralDebug/python_debug_session.py cmd --port 5678 continue
python src/NeuralDebug/python_debug_session.py cmd --port 5678 quit

# Stop server
python src/NeuralDebug/python_debug_session.py stop --port 5678
```

### Language → script mapping

| Extension | Script |
|-----------|--------|
| .py | python_debug_session.py |
| .c, .cpp, .cc, .h | cpp_debug_session.py |
| .cs, .csproj | csharp_debug_session.py |
| .rs | rust_debug_session.py |
| .java, .jar | java_debug_session.py |
| .go | go_debug_session.py |
| .js, .ts, .mjs | nodejs_debug_session.py |
| .rb | ruby_debug_session.py |

### Debug commands

| Command | Short | What it does |
|---------|-------|-------------|
| start | s | Begin execution |
| continue | c | Run to next breakpoint |
| step_over | n | Next line (skip calls) |
| step_in | si | Step into function |
| step_out | so | Step out of function |
| set_breakpoint \<line\> | b | Set breakpoint |
| set_breakpoint \<file\>:\<line\> | b | Set breakpoint in file |
| remove_breakpoint \<line\> | rb | Remove breakpoint |
| inspect | i | Show variables + call stack |
| evaluate \<expr\> | e | Evaluate expression |
| list | l | Show source around current line |
| backtrace | bt | Full call stack |
| quit | q | End session |

### Debugging workflow

1. Run `info` to check the toolchain is available
2. Start a debug server with `serve <target> --port <port> --daemonize`
3. Set breakpoints with `cmd --port <port> b <line>`
4. Start execution with `cmd --port <port> start`
5. Step, inspect, evaluate as needed
6. All responses are JSON with status, current_location, local_variables, call_stack
7. Quit when done with `cmd --port <port> quit`

### Important

- Always use `--daemonize` with `serve` so the server runs in the background
- Use a unique port per debug session (default: 5678)
- C/C++ source files are auto-compiled with debug symbols
- All scripts require Python 3.8+
- **NEVER create temporary helper scripts** to interact with the debug server — always use the CLI scripts above
- Do not write custom Python scripts that send raw socket/JSON commands to the debug server TCP port
- The `cmd` subcommand is the only supported interface for sending debug commands

### Shell execution guidelines for AI agents

When running NeuralDebug commands via shell tools (powershell, bash, etc.):

1. **Always set `initial_wait` appropriate to the command being run:**
   - Fast commands (`info`, `status`, `b`, `e`, `inspect`, `step_over`, `step_in`, `step_out`, `list`, `breakpoints`, `ping`, `quit`): use `initial_wait: 15`
   - Medium commands (`start`, `continue`, `run_to_line`): use `initial_wait: 60` — these wait for the debugger to hit a breakpoint, which may take time
   - Long commands (`serve`): run in async mode with a named shellId, then read output separately

2. **Never call `read_powershell` / `read` on a sync-mode shell that already returned output.** Sync-mode shells are disposed after the command completes. If the output was already captured in the initial response, do not attempt to read more — the shell ID will be invalid.

3. **Run `cmd` subcommands sequentially, not in parallel.** The debug server is single-threaded and accepts one TCP connection at a time. Parallel `cmd` calls will get connection-refused errors.

4. **Use a stable named shellId for the `serve` process** (e.g., `shellId: "dbg-server"`) so you can always check its output. Run `serve` in async mode since it's a long-running server.

5. **If a shell ID becomes invalid**, do NOT retry `read_powershell` — just re-run the command.
