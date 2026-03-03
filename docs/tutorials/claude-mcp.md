# Tutorial: Using NeuralDebug with Claude Desktop (MCP)

This guide shows how to connect NeuralDebug to Claude Desktop via the
Model Context Protocol (MCP), giving Claude the ability to debug your code.

## Prerequisites

- [Claude Desktop](https://claude.ai/download) installed
- Python 3.8+
- A debugger for your target language

## Setup

1. Clone NeuralDebug:

```bash
git clone https://github.com/DennySun2020/DeepRhapsody.git
```

2. Edit your Claude Desktop config file:

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "NeuralDebug": {
      "command": "python",
      "args": ["/path/to/NeuralDebug/integrations/mcp/server.py"]
    }
  }
}
```

3. Restart Claude Desktop. You should see "NeuralDebug" in the MCP tools list
   (hammer icon in the input box).

## Usage

NeuralDebug supports two modes with Claude:

### Autonomous Mode — describe the bug, get the answer

```
You: I have a C program at /path/to/main.c that segfaults. Can you debug it?

Claude: I'll use NeuralDebug to investigate. Let me start a debug server...
        [Calls NeuralDebug_start_server]
        [Reads the code, identifies suspicious functions]
        [Sets breakpoints at process_packet()]
        [Steps through — finds NULL pointer on second call]

        Root cause: recv() returns 0 on disconnect, but the result is
        passed directly to process_packet() without validation.
        Fix: add `if (bytes <= 0) break;` before the call.
```

### Interactive Mode — you direct, Claude executes

```
You: Start a debug session for main.py

Claude: [Calls NeuralDebug_start_server] Debug server ready on port 5678.
        What would you like to do?

You: Break at line 42 and run

Claude: [Calls NeuralDebug_set_breakpoint at 42]
        [Calls NeuralDebug_start_execution]
        Paused at line 42 in calculate_total(). Variables: total=0, items=[...].

You: What's the value of items[3]?

Claude: [Calls NeuralDebug_evaluate with "items[3]"]
        items[3] = {"name": "Widget", "price": -5.0}  ⚠️ Negative price!
```

## Available Tools

Claude sees these tools from the MCP server:

| Tool | Description |
|------|-------------|
| `NeuralDebug_info` | Detect available debuggers/compilers |
| `NeuralDebug_start_server` | Launch debug session for a target |
| `NeuralDebug_status` | Check if server is running |
| `NeuralDebug_set_breakpoint` | Set breakpoint at line/function |
| `NeuralDebug_start_execution` | Start the program |
| `NeuralDebug_step` | Step over/into/out |
| `NeuralDebug_continue` | Continue to next breakpoint |
| `NeuralDebug_inspect` | Show local variables |
| `NeuralDebug_evaluate` | Evaluate expression |
| `NeuralDebug_backtrace` | Show call stack |
| `NeuralDebug_list_code` | Show source around current line |
| `NeuralDebug_stop` | End debug session |

## Remote Debugging

The MCP server also supports SSE transport for remote debugging:

```bash
# On the remote machine (where the code lives):
python integrations/mcp/server.py --transport sse --port 8080

# In Claude Desktop config, point to the remote server:
# (Use an MCP SSE client configuration)
```

## Tips

- Claude will automatically choose the right debug script based on file extension
- The debug server persists — Claude can continue debugging across messages
- For best results, describe the bug symptom clearly so Claude sets strategic breakpoints
- Works with all 8 supported languages
