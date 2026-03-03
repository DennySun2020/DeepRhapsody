# NeuralDebug Integrations

Connect NeuralDebug to **any AI agent platform**. The core debug scripts are
standalone CLI tools — these adapters make them discoverable by different platforms.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                    AI Agent Platforms                      │
├──────────┬──────────┬──────────┬──────────┬──────────────┤
│  Claude  │  Copilot │ ChatGPT  │  Gemini  │  Open Source │
│ Desktop  │   CLI    │  /Codex  │          │  (LangChain  │
│          │          │          │          │   AutoGen..) │
├──────────┴──────────┴──────────┴──────────┴──────────────┤
│               Integration / Adapter Layer                 │
├──────────┬──────────┬──────────┬─────────────────────────┤
│   MCP    │ .agent.md│  OpenAI  │  LangChain / Python     │
│  Server  │  (skill) │ Functions│  Tool Wrappers          │
├──────────┴──────────┴──────────┴─────────────────────────┤
│              Core Debug Scripts (CLI)                      │
│  python_debug_session.py  cpp_debug_session.py  ...       │
├───────────────────────────────────────────────────────────┤
│              Debugger Backends                             │
│  GDB  LLDB  CDB  netcoredbg  Delve  JDB  node  rdbg     │
└───────────────────────────────────────────────────────────┘
```

## Integration Options

### 1. MCP Server (Claude Desktop, Cursor, any MCP client)

The **Model Context Protocol** is the most universal integration. One server
works with Claude Desktop, Cursor, Copilot, and any MCP-compatible agent.

```json
// claude_desktop_config.json or cursor settings
{
  "mcpServers": {
    "NeuralDebug": {
      "command": "python",
      "args": ["/path/to/NeuralDebug/integrations/mcp/server.py"]
    }
  }
}
```

The MCP server exposes 15 tools: `NeuralDebug_info`, `NeuralDebug_start_server`,
`NeuralDebug_set_breakpoint`, `NeuralDebug_step`, `NeuralDebug_inspect`, etc.

### 2. GitHub Copilot (agent + skill)

Already configured in `.github/agents/NeuralDebug.agent.md` and
`.github/skills/debugger/SKILL.md`. Clone this repo and Copilot
discovers NeuralDebug automatically.

### 3. OpenAI / ChatGPT / Codex (function calling)

```python
import json
from integrations.openai.adapter import get_tools, handle_function_call

# Pass tools to the API
tools = get_tools()
response = client.chat.completions.create(
    model="gpt-4", messages=messages, tools=tools
)

# Handle tool calls
for call in response.choices[0].message.tool_calls:
    result = handle_function_call(call.function.name, call.function.arguments)
    messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
```

Or use the pre-built `functions.json`:
```python
with open("integrations/openai/functions.json") as f:
    tools = json.load(f)["tools"]
```

### 4. LangChain / LlamaIndex / CrewAI / AutoGen

```python
from integrations.langchain.tools import get_NeuralDebug_tools

# Framework-agnostic tools
tools = get_NeuralDebug_tools()

# Convert to LangChain format
lc_tools = [t.to_langchain() for t in tools]

# Or convert to OpenAI format
openai_tools = [t.to_openai_function() for t in tools]
```

### 5. Any agent with shell access

No adapter needed. Just include the system prompt from
`prompts/universal.md` and the agent can call the scripts directly:

```bash
python src/NeuralDebug/cpp_debug_session.py serve main.c --port 5678 --daemonize
python src/NeuralDebug/cpp_debug_session.py cmd --port 5678 b main
python src/NeuralDebug/cpp_debug_session.py cmd --port 5678 start
```

### 6. Google Gemini

Use the OpenAI function definitions (Gemini's function calling format is
nearly identical). Load `functions.json` and convert `"type": "function"` to
Gemini's `FunctionDeclaration` format.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `NeuralDebug_SCRIPTS` | Path to debug session scripts | Auto-detected from repo |

## LLM Debug Tools

The LLM debugging capabilities (weight-level and API-based) are available through
all integration layers — MCP, OpenAI functions, and LangChain tool wrappers.

### Weight-Level Debugging Tools (local models)

| Tool | Description |
|------|-------------|
| `NeuralDebug_llm_start` | Start LLM debug server with a model |
| `NeuralDebug_llm_generate` | Generate text from a prompt |
| `NeuralDebug_llm_step` | Step through transformer layers |
| `NeuralDebug_llm_logit_lens` | Run Logit Lens — see predictions at each layer |
| `NeuralDebug_llm_patch` | Activation Patching — find causal layers |
| `NeuralDebug_llm_probe` | Probing — test what info is encoded at each layer |
| `NeuralDebug_llm_attention` | Attention Analysis — rank heads and trace focus |
| `NeuralDebug_llm_hallucination` | Hallucination detection with per-token grounding scores |

### API-Based Debugging Tools (hosted models)

| Tool | Description |
|------|-------------|
| `NeuralDebug_api_logprobs` | Logprob analysis — per-token confidence and entropy |
| `NeuralDebug_api_consistency` | Consistency testing — run N samples, measure agreement |
| `NeuralDebug_api_counterfactual` | Counterfactual probing — swap entities, compare answers |

These tools follow the same JSON protocol as all other NeuralDebug tools and
can be combined with traditional debugging tools in a single agent session.

## Adding a New Integration

1. Create `integrations/<platform>/` directory
2. Implement an adapter that translates the platform's tool format to CLI calls
3. Reuse `integrations/mcp/server.py`'s `handle_tool_call()` for the core logic
4. Add configuration/setup instructions
