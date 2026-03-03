#!/usr/bin/env python3
"""
NeuralDebug adapter for OpenAI function calling (ChatGPT, Codex, GPT agents).

Usage with OpenAI API:
    from NeuralDebug_openai import get_tools, handle_function_call

    tools = get_tools()
    response = client.chat.completions.create(
        model="gpt-4", messages=messages, tools=tools
    )
    # When the model calls a function:
    result = handle_function_call(tool_call.function.name, tool_call.function.arguments)

Usage with Codex CLI / custom agents:
    python adapter.py call NeuralDebug_start_server '{"target": "main.py"}'
    python adapter.py call NeuralDebug_step '{"action": "step_over", "port": 5678}'
    python adapter.py tools  # Print available tools JSON
"""

import json
import sys
from pathlib import Path

# Import the MCP server's core logic (shared implementation)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp"))
from server import handle_tool_call, TOOLS, SCRIPTS_DIR


def get_tools() -> list[dict]:
    """Return OpenAI-format tool definitions."""
    openai_tools = []
    for tool in TOOLS:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["inputSchema"]
            }
        })
    return openai_tools


def handle_function_call(name: str, arguments: str | dict) -> str:
    """
    Handle an OpenAI function call.

    Args:
        name: Function name (e.g., 'NeuralDebug_step')
        arguments: JSON string or dict of arguments

    Returns:
        JSON string result for the function call response
    """
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    result = handle_tool_call(name, arguments)
    return json.dumps(result, indent=2)


def main():
    """CLI interface for testing."""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python adapter.py tools              # Print tool definitions")
        print("  python adapter.py call <name> <args>  # Call a tool")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "tools":
        print(json.dumps(get_tools(), indent=2))

    elif cmd == "call":
        if len(sys.argv) < 4:
            print("Usage: python adapter.py call <tool_name> '<json_args>'")
            sys.exit(1)
        name = sys.argv[2]
        args = sys.argv[3]
        result = handle_function_call(name, args)
        print(result)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
