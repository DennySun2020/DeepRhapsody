# DeepRhapsody 🎵

**AI-powered debugging autopilot — natural language is all you need.**

DeepRhapsody is an AI-driven debugging framework powered by the **NeuralDebug** engine. It debugs two things:

- **🔧 Software** — Debug programs in **8 languages** (Python, C/C++, C#, Rust, Java, Go, Node.js, Ruby) by setting breakpoints, stepping through code, and inspecting variables — all through natural language.
- **🧠 LLMs** — Debug the reasoning of **transformer models** by stepping through forward passes layer by layer, inspecting activations and attention weights, running interpretability tools (Logit Lens, Activation Patching, Probing, Head Surgery), and fine-tuning with LoRA.

Both scenarios use natural language and the same client-server architecture. Simply describe what you want to investigate — whether it's a crash in your C++ program or a hallucination in GPT-2 — and NeuralDebug translates your intent into the right debugger commands.

---

### Prerequisites

- **Python 3.8+** (for running debug session scripts)

## 🔧 Software Debugging

### Two Debugging Modes

NeuralDebug operates in two  modes:

#### 🤖 Autonomous Mode

Tell the AI what's wrong and let it drive the entire investigation. The AI agent reads your code, identifies suspicious areas, strategically places breakpoints, steps through execution, and delivers a root-cause analysis — all without manual intervention.

```
You:  @NeuralDebug debug main.c — it crashes on the second connection

NeuralDebug: [reads code, identifies packet processing path]
             [sets breakpoint at process_packet()]
             [runs to first call — all healthy, continues]
             [runs to second call — pkt is NULL!]
             [checks caller — recv() return not checked]

             Root cause: recv() returns 0 on disconnect but the result
             is passed to process_packet() without checking. Fix: add
             `if (bytes <= 0) break;` before line 87.
```

https://github.com/DennySun2020/DeepRhapsody/releases/download/v0.0.1-alpha/NeuralDebug_autonomous_mode.mp4

Best for: bug reports, crash investigations, "why is this wrong?" questions. The AI formulates a hypothesis, tests it through the debugger, and reports back.

#### 🎮 Interactive Mode

You stay in control, directing the debugger step by step through natural language. The AI translates your requests, executes them, and explains each result — but you decide where to go next.

```
You:        Set a breakpoint at line 44
NeuralDebug: Breakpoint set at filter_valid_grades(), line 44.

You:        Start the program
NeuralDebug: Paused at line 44. Variables: name='Alice', score=85.

You:        Step over 3 times
NeuralDebug: Now at iteration for 'Eve', score=0. ⚠️ Zero score will
            pass the >= 0 check — is that intentional?

You:        Evaluate len(valid)
NeuralDebug: len(valid) = 2  (Alice and Bob so far)
```

https://github.com/DennySun2020/DeepRhapsody/releases/download/v0.0.1-alpha/NeuralDebug_interactive_mode.mp4

Best for: exploring unfamiliar code, learning how a program works, guided investigations where you have a hunch.

### Supported Languages

- **Python** — bdb (stdlib)
- **C/C++** — GDB / LLDB / CDB
- **C#** — netcoredbg
- **Rust** — rust-gdb / rust-lldb / GDB / LLDB
- **Java** — JDB
- **Go** — Delve
- **Node.js/TypeScript** — Node Inspector
- **Ruby** — rdbg (debug.gem)

The architecture is extensible — add support for a new language by implementing a debug session script that follows the standard JSON protocol.

Every language works on **Linux, macOS, and Windows** — NeuralDebug auto-detects the platform and selects the best available debugger.

To add support for a new language:

1. Create `.github/skills/debugger/scripts/<language>_debug_session.py` following the existing patterns
2. Implement the standard JSON protocol (same request/response format)
3. Add examples and documentation
4. Update this README

## Architecture

NeuralDebug uses a **client-server architecture** for both software and LLM debugging:

```
┌─────────────┐    JSON        ┌──────────────────┐  Debugger Protocol   ┌──────────┐
│  AI Agent   │ ◄────────────► │  Debug Server    │ ◄──────────────────► │ Debugger │
│ (Copilot,   │                │  (Remote,        │                      │ (GDB,    │
│  Claude,    │                │   Local)         │                      │  LLDB,   │
│  etc.)      │                │                  │                      │  CDB, ...│
│             │                │                  │                      │  LLMs)   │
└─────────────┘                └──────────────────┘                      └──────────┘
```

1. **AI Agent** sends natural language → translated to JSON commands
2. **Debug Server** (TCP, one connection) manages the debugger subprocess
3. **Debugger Backend** (GDB/LLDB/CDB/Delve/JDB/LLM framework etc.) controls the target program
4. **Responses** include: current location, call stack, local variables, stdout/stderr


## Available Commands

| Command | Description |
|---------|-------------|
| `b <line>` / `b <func>` / `b <file:line>` | Set breakpoint |
| `start` | Begin execution |
| `step_over` | Step to next line |
| `step_in` | Step into function call |
| `step_out` | Step out of current function |
| `continue` | Run to next breakpoint |
| `run_to_line <N>` | Run to specific line |
| `inspect` | Show local variables |
| `e <expr>` | Evaluate expression |
| `backtrace` | Show call stack |
| `list` | Show source code around current line |
| `breakpoints` | List all breakpoints |
| `remove_breakpoint <N>` | Remove breakpoint |
| `ping` | Check server health |
| `quit` | Stop debug session |

---

## 🧠 LLM Debugging (Transformer Interpretability)

NeuralDebug can **debug the reasoning of LLM/transformer models**. Just as you would step through source code line by line, you can step through a forward pass layer by layer — inspecting activations, attention weights, and predictions at each stage to understand *why* a model produces a given output.

https://github.com/DennySun2020/DeepRhapsody/releases/download/v0.0.1-alpha/NeuralDebug_llm.mp4

Capabilities include **Logit Lens** (per-layer prediction trajectory), **Activation Patching** (causal tracing), **Probing** (what information is encoded where), **Attention Analysis**, **Hallucination Detection**, **Sparse Autoencoders**, **Neuron Analysis**, **Attention Head Surgery**, and **LoRA Fine-Tuning** — all driven through the same natural language interface.

The same autonomous and interactive modes apply — let the AI drive the full investigation, or direct it step by step.

### Supported Models

Any HuggingFace causal language model is supported. NeuralDebug auto-detects the model architecture and selects the right adapter:

| Architecture | Models | Adapter |
|-------------|--------|---------|
| **GPT-2** | distilgpt2, gpt2, gpt2-medium, gpt2-large, gpt2-xl | `gpt2` (auto) |
| **Llama** | Llama 2/3, Code Llama, Mistral, Mixtral, Yi, Qwen-2 | `llama` (auto) |
| **Custom** | Any model — implement a `ModelAdapter` subclass | User-registered |

Specify the model when starting the server:

```bash
# Default model (distilgpt2)
python src/neuraldebug/llm/llm_debug_session.py serve --port 5680

# Specific model
python src/neuraldebug/llm/llm_debug_session.py serve --model gpt2-medium --port 5680

# Llama model (adapter auto-detected)
python src/neuraldebug/llm/llm_debug_session.py serve --model meta-llama/Llama-2-7b-hf --port 5680

# Explicit adapter selection
python src/neuraldebug/llm/llm_debug_session.py serve --model my-custom-model --adapter my_adapter --port 5680
```

### Example: Investigating a Prediction

```
You:        Debug why gpt2 predicts "Tokyo" for "The capital of Japan is"

NeuralDebug: [loads gpt2, starts forward pass with prompt]
             [steps through 12 transformer blocks]
             [runs Logit Lens — "Tokyo" first appears at layer 5]
             [runs Activation Patching — layer 8 is causally responsible]
             [analyzes attention — head 9.6 strongly attends to "Japan"]

             Summary: The model identifies "Japan" early via attention
             head 9.6, begins predicting "Tokyo" at layer 5, and the
             prediction is locked in by layer 8. Patching confirms
             layer 8 is the causal bottleneck for this factual recall.

You:        Which attention heads matter most for this prediction?

NeuralDebug: [runs head surgery sweep — ablates each head, measures impact]

            Top 3 heads by impact (KL divergence):
              1. Head 9.6  — KL 2.34 (attends "Japan" → last position)
              2. Head 11.1 — KL 1.87 (aggregates context)
              3. Head 7.0  — KL 0.92 (position tracking)
            Ablating head 9.6 alone drops "Tokyo" from #1 to #47.

You:        Does the model hallucinate on "The inventor of the telephone was"?

NeuralDebug: [generates tokens, runs hallucination detection]

            Generated: "The inventor of the telephone was Alexander Graham Bell"
            Token "Alexander": ✅ WELL_GROUNDED (layers agree, low entropy)
            Token "Graham":    ⚠️ SURFACE_PATTERN (late emergence, layer 10+)
            Token "Bell":      ✅ WELL_GROUNDED (strong early signal)

            No hallucination detected — but "Graham" relies on
            surface-level pattern completion rather than deep recall.
```

### API-Based LLM Debugging

For hosted models (GPT-4, Claude, Gemini) where you don't have access to model weights, NeuralDebug provides **API-based reasoning probes**:

- **Logprob analysis** — token confidence, entropy, alternative tokens
- **Prompt perturbation** — swap/remove parts of the prompt, compare outputs
- **Chain-of-thought extraction** — force step-by-step reasoning, compare with direct answer
- **Consistency testing** — ask the same question N times, measure agreement
- **Counterfactual probing** — test causal factors ("Would the answer change if X?")
- **Calibration check** — stated confidence vs actual accuracy

### Custom Model Support

Add support for a new architecture by implementing a `ModelAdapter`:

```python
from neuraldebug.llm.adapters import ModelAdapter, AdapterRegistry

class MyModelAdapter(ModelAdapter):
    def info(self): ...
    def embed(self, input_ids): ...
    def forward_block(self, hidden, block_idx): ...
    # ... implement abstract methods ...

AdapterRegistry.register("my_model", MyModelAdapter)
```

Then launch: `python llm_debug_session.py serve --model my-org/my-model --adapter my_model`

## AI Agent Integrations

NeuralDebug works with **any AI agent platform**, not just GitHub Copilot:

| Platform | Integration | Setup |
|----------|------------|-------|
| **Claude Desktop / Cursor** | MCP Server | `integrations/mcp/server.py` |
| **GitHub Copilot** | Agent + Skill | `.github/agents/NeuralDebug.agent.md` |
| **ChatGPT / Codex** | OpenAI Functions | `integrations/openai/functions.json` |
| **Gemini** | Function Declarations | Adapt from `functions.json` |
| **LangChain / AutoGen / CrewAI** | Python Tools | `integrations/langchain/tools.py` |
| **Any agent with shell** | System Prompt | `integrations/prompts/universal.md` |

### Quick setup: Claude Desktop (MCP)
```json
{
  "mcpServers": {
    "NeuralDebug": {
      "command": "python",
      "args": ["path/to/NeuralDebug/integrations/mcp/server.py"]
    }
  }
}
```

### Quick setup: OpenAI function calling
```python
from integrations.openai.adapter import get_tools, handle_function_call
tools = get_tools()  # Pass to client.chat.completions.create(tools=tools)
```

### Quick setup: LangChain
```python
from integrations.langchain.tools import get_NeuralDebug_tools
tools = [t.to_langchain() for t in get_NeuralDebug_tools()]
```

See `integrations/README.md` for full setup guides.

See `.github/skills/debugger/SKILL.md` for the full skill specification.

## Examples

- `sample_buggy_grades.py` — Python: grade calculation with off-by-one bug
- `sample_buggy_stats.c` — C: statistics calculation with multiple bugs
- `concurrent_pipeline.c` — C: concurrent pipeline with synchronization issues
- `sample_buggy_inventory/` — C#: inventory system with 5 intentional bugs

See `.github/skills/debugger/examples/*.md` for detailed debugging walkthroughs.

## Tutorials

Step-by-step guides for each platform:

- **[Quick Start](docs/tutorials/quick-start.md)** — Debug your first program in 2 minutes
- **[GitHub Copilot CLI](docs/tutorials/copilot-cli.md)** — Use as a Copilot agent
- **[Claude Desktop (MCP)](docs/tutorials/claude-mcp.md)** — Connect via Model Context Protocol
- **[ChatGPT / Codex / Gemini](docs/tutorials/openai-codex.md)** — OpenAI function calling
- **[LangChain / AutoGen / CrewAI](docs/tutorials/langchain-agents.md)** — Python framework integration

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

See also: [Code of Conduct](CODE_OF_CONDUCT.md) · [Security Policy](SECURITY.md) · [Changelog](CHANGELOG.md)

## License

MIT License — see [LICENSE](LICENSE) for details.
