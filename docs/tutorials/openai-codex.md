# Tutorial: Using NeuralDebug with OpenAI (ChatGPT / Codex)

This guide shows how to integrate NeuralDebug with OpenAI's function calling
API, enabling GPT-4, ChatGPT, or Codex to debug programs.

Both **autonomous mode** (the agent loop below drives the full investigation)
and **interactive mode** (the user sends one command at a time via chat) work
with the same tool definitions — the difference is whether the agent loops
autonomously or waits for user input between tool calls.

## Prerequisites

- Python 3.8+
- `openai` Python package (`pip install openai`)
- A debugger for your target language

## Option 1: Use Pre-built Function Definitions

Load the tool schemas from `functions.json`:

```python
import json
import openai

# Load NeuralDebug tool definitions
with open("integrations/openai/functions.json") as f:
    tools = json.load(f)["tools"]

client = openai.OpenAI()

messages = [
    {"role": "system", "content": "You are a debugging assistant. Use the NeuralDebug tools to debug programs."},
    {"role": "user", "content": "Debug /path/to/main.py — the sort function has an off-by-one error"}
]

response = client.chat.completions.create(
    model="gpt-4",
    messages=messages,
    tools=tools
)
```

## Option 2: Use the Python Adapter

The adapter handles tool call execution automatically:

```python
from integrations.openai.adapter import get_tools, handle_function_call

tools = get_tools()

# In your agent loop:
response = client.chat.completions.create(
    model="gpt-4", messages=messages, tools=tools
)

# Handle tool calls
for tool_call in response.choices[0].message.tool_calls:
    result = handle_function_call(
        tool_call.function.name,
        tool_call.function.arguments
    )
    messages.append({
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": result
    })
```

## Option 3: CLI Testing

Test tool calls from the command line without any API:

```bash
# List available tools
python integrations/openai/adapter.py tools

# Call a tool directly
python integrations/openai/adapter.py call NeuralDebug_start_server '{"target": "main.py"}'
python integrations/openai/adapter.py call NeuralDebug_set_breakpoint '{"location": "42", "port": 5678}'
python integrations/openai/adapter.py call NeuralDebug_start_execution '{"port": 5678}'
python integrations/openai/adapter.py call NeuralDebug_inspect '{"port": 5678}'
python integrations/openai/adapter.py call NeuralDebug_stop '{"port": 5678}'
```

## Full Agent Loop Example

```python
import json
import openai
from integrations.openai.adapter import get_tools, handle_function_call

client = openai.OpenAI()
tools = get_tools()

messages = [
    {"role": "system", "content": """You are NeuralDebug, an AI debugger.
    Use tools to launch debug servers, set breakpoints, step through code,
    and find bugs. Always explain what you find."""},
    {"role": "user", "content": "Debug main.c — it crashes on NULL pointer"}
]

# Agent loop
while True:
    response = client.chat.completions.create(
        model="gpt-4", messages=messages, tools=tools
    )

    choice = response.choices[0]
    messages.append(choice.message)

    if choice.finish_reason == "tool_calls":
        for tc in choice.message.tool_calls:
            result = handle_function_call(tc.function.name, tc.function.arguments)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            print(f"  [{tc.function.name}] → {result[:200]}...")
    else:
        print(f"\nAssistant: {choice.message.content}")
        break
```

## Google Gemini

Gemini's function calling format is nearly identical to OpenAI's. Load
`functions.json` and convert each tool to Gemini's `FunctionDeclaration`:

```python
import google.generativeai as genai

# Convert OpenAI format → Gemini format
with open("integrations/openai/functions.json") as f:
    openai_tools = json.load(f)["tools"]

gemini_tools = []
for t in openai_tools:
    gemini_tools.append(genai.types.FunctionDeclaration(
        name=t["function"]["name"],
        description=t["function"]["description"],
        parameters=t["function"]["parameters"]
    ))

model = genai.GenerativeModel("gemini-pro", tools=gemini_tools)
```

## Tips

- The adapter reuses the MCP server's `handle_tool_call()` internally
- All tools return JSON strings — parse them in your agent loop
- The debug server persists between tool calls (no need to restart)
- Set `NeuralDebug_SCRIPTS` env var to override the scripts directory
