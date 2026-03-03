# Tutorial: Quick Start — Debug Your First Program

This guide gets you debugging in under 2 minutes, no AI agent required.

NeuralDebug has **two modes**:
- **Interactive Mode** (shown here) — you run commands manually via CLI
- **Autonomous Mode** — an AI agent drives the entire session (see [Copilot CLI](copilot-cli.md) or [Claude MCP](claude-mcp.md) tutorials)

## Prerequisites

- Python 3.8+
- A debugger: GDB (Linux), LLDB (macOS), CDB (Windows), or language-specific debugger

## Step 1: Clone NeuralDebug

```bash
git clone https://github.com/DennySun2020/DeepRhapsody.git
cd DeepRhapsody
```

## Step 2: Try the Python Example

```bash
SCRIPTS=src/NeuralDebug
EXAMPLES=examples

# Start debug server
python $SCRIPTS/python_debug_session.py serve $EXAMPLES/sample_buggy_grades.py --port 5678 --daemonize

# Set a breakpoint at line 44 (the suspicious filter)
python $SCRIPTS/python_debug_session.py cmd --port 5678 b 44

# Start execution
python $SCRIPTS/python_debug_session.py cmd --port 5678 start

# Inspect variables
python $SCRIPTS/python_debug_session.py cmd --port 5678 inspect

# Step through code
python $SCRIPTS/python_debug_session.py cmd --port 5678 step_over

# Show call stack
python $SCRIPTS/python_debug_session.py cmd --port 5678 backtrace

# Stop when done
python $SCRIPTS/python_debug_session.py stop --port 5678
```

## Step 3: Try the C Example

```bash
# Check available compilers and debuggers
python $SCRIPTS/cpp_debug_session.py info

# Start debug server (auto-compiles with debug symbols!)
python $SCRIPTS/cpp_debug_session.py serve $EXAMPLES/sample_buggy_stats.c --port 5678 --daemonize

# Set breakpoint at main
python $SCRIPTS/cpp_debug_session.py cmd --port 5678 b main

# Start and step through
python $SCRIPTS/cpp_debug_session.py cmd --port 5678 start
python $SCRIPTS/cpp_debug_session.py cmd --port 5678 step_over
python $SCRIPTS/cpp_debug_session.py cmd --port 5678 inspect

# Stop
python $SCRIPTS/cpp_debug_session.py stop --port 5678
```

## Understanding the Output

Every command returns JSON like:

```json
{
  "status": "paused",
  "current_location": {
    "file": "sample_buggy_grades.py",
    "line": 44,
    "function": "filter_valid_grades",
    "source": "    if score >= 0 and score <= 100:"
  },
  "local_variables": [
    {"name": "score", "value": "0", "type": "int"},
    {"name": "name", "value": "'Eve'", "type": "str"}
  ],
  "call_stack": [...],
  "message": "Stopped at breakpoint"
}
```

## Next Steps

- [Use with GitHub Copilot CLI](copilot-cli.md)
- [Use with Claude Desktop (MCP)](claude-mcp.md)
- [Use with ChatGPT/Codex](openai-codex.md)
- [Use with LangChain/AutoGen](langchain-agents.md)

## Supported Languages

| Language | Command to debug |
|----------|-----------------|
| Python | `python_debug_session.py serve script.py` |
| C/C++ | `cpp_debug_session.py serve program.c` |
| C# | `csharp_debug_session.py serve app.dll` |
| Rust | `rust_debug_session.py serve main.rs` |
| Java | `java_debug_session.py serve Main.java` |
| Go | `go_debug_session.py serve main.go` |
| Node.js | `nodejs_debug_session.py serve app.js` |
| Ruby | `ruby_debug_session.py serve script.rb` |
