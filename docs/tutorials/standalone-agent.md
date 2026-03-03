# NeuralDebug Standalone Agent

NeuralDebug can run as a **standalone AI debugging agent** with your own LLM API key — no external agent required.

## Quick Start

```bash
# Clone and install with agent support
git clone https://github.com/DennySun2020/DeepRhapsody.git
cd DeepRhapsody
pip install -e ".[agent]"

# Set your API key
export OPENAI_API_KEY=sk-...

# Start an interactive debugging session
NeuralDebug chat

# Or run a one-shot task
NeuralDebug run "find the bug in main.py that causes the crash on line 44"
```

## Supported Providers

| Provider | Env Variable | Default Model |
|----------|-------------|---------------|
| **OpenAI** | `OPENAI_API_KEY` | `gpt-4o` |
| **Anthropic** | `ANTHROPIC_API_KEY` | `claude-sonnet-4-20250514` |
| **Google** | `GOOGLE_API_KEY` | `gemini-2.5-flash` |
| **Ollama** (local) | _(none needed)_ | `llama3.1` |
| **OpenRouter** | `OPENROUTER_API_KEY` | `anthropic/claude-sonnet-4` |
| **Any OpenAI-compatible** | `NeuralDebug_API_KEY` | _(set via config)_ |

### Using a Specific Provider

```bash
# Anthropic Claude
NeuralDebug chat --provider anthropic --model claude-sonnet-4-20250514

# Google Gemini
NeuralDebug chat --provider google --model gemini-2.5-pro

# Local model via Ollama
NeuralDebug chat --provider ollama --model llama3.1

# OpenRouter (access any model)
NeuralDebug chat --provider openrouter --model anthropic/claude-sonnet-4

# Custom OpenAI-compatible API
NeuralDebug chat --provider openai --model my-model \
  --api-key sk-... \
  NeuralDebug_BASE_URL=https://my-api.example.com/v1
```

## Configuration

### Config File

Create `~/.NeuralDebug/config.yaml`:

```yaml
provider: openai
model: gpt-4o
api_key: ${OPENAI_API_KEY}
max_turns: 50
temperature: 0.0
```

### CLI Config Commands

```bash
# Create default config
NeuralDebug config init

# Show current config
NeuralDebug config show

# Set a value
NeuralDebug config set provider=anthropic
NeuralDebug config set model=claude-sonnet-4-20250514
```

### Precedence Order

Settings are resolved in this order (highest wins):
1. CLI flags (`--provider`, `--model`, `--api-key`)
2. Environment variables (`NeuralDebug_PROVIDER`, `NeuralDebug_MODEL`, etc.)
3. Config file (`~/.NeuralDebug/config.yaml`)
4. Built-in defaults

## Available Models

```bash
# List all known models
NeuralDebug models

# Filter by provider
NeuralDebug models --provider anthropic
```

## CLI Reference

```
NeuralDebug chat                    Interactive debugging REPL
NeuralDebug run "prompt"            One-shot debugging task
NeuralDebug config show             Show current configuration
NeuralDebug config set key=value    Update a config value
NeuralDebug config init             Create default config file
NeuralDebug models                  List available models
NeuralDebug hub search QUERY        Search PilotHub for skills
NeuralDebug hub install NAME        Install a skill from PilotHub
NeuralDebug hub list                List installed skills
NeuralDebug hub publish DIR         Publish a skill to PilotHub
NeuralDebug hub uninstall NAME      Remove an installed skill
NeuralDebug hub update [NAME]       Update skills (all or specific)
```

## Still Works as Skills

The standalone agent is **additive** — all existing integration paths remain fully supported:

- **MCP** (Claude Desktop, etc.): Use `.mcp.json` as before
- **OpenAI Function Calling**: Use `integrations/openai/adapter.py`
- **LangChain/CrewAI/AutoGen**: Use `integrations/langchain/tools.py`
- **Any agent with shell access**: Use `prompts/universal.md`

The standalone agent uses the exact same tool implementations as MCP, ensuring identical behavior.
