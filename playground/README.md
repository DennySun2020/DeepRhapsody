# NeuralDebug Playground

Try NeuralDebug in under 60 seconds — no AI agent or API keys needed.

## 🆓 Quickest Start: Gemini CLI (Free, No API Key)

Gemini CLI is Google's free AI terminal agent (1,000 requests/day). Perfect
for students and open-source contributors:

```bash
# 1. Install Gemini CLI (requires Node.js 18+)
npm install -g @google/gemini-cli

# 2. Open Gemini CLI in the NeuralDebug repo
cd DeepRhapsody
gemini

# 3. Just ask it to debug!
#    > Debug examples/sample_buggy_grades.py — find the bugs
```

Gemini CLI reads `.gemini/system.md` automatically — no API key, no setup,
no plugins. See [docs/tutorials/gemini-cli.md](../docs/tutorials/gemini-cli.md)
for the full walkthrough.

## Option 1: AI-driven demo (recommended first look)

```bash
# OpenAI / Codex
export OPENAI_API_KEY=sk-...
python playground/ai_debug_demo.py --provider openai

# Claude (Anthropic)
export ANTHROPIC_API_KEY=sk-ant-...
python playground/ai_debug_demo.py --provider claude

# Gemini (Google)
export GEMINI_API_KEY=AIza...
python playground/ai_debug_demo.py --provider gemini

# Local model (Ollama, LM Studio, vLLM, etc.)
python playground/ai_debug_demo.py --base-url http://localhost:11434/v1 --model llama3

# No API key? Scripted walkthrough with real debug commands
python playground/ai_debug_demo.py --demo
```

A real LLM reads the source code, decides where to set breakpoints, steps
through execution, evaluates expressions, and reports root causes — all
through NeuralDebug's real debug protocol.

## Option 2: One-command protocol demo

```bash
python playground/try_NeuralDebug.py
```

Step through a debug session command by command. Shows the JSON protocol
that AI agents use to talk to NeuralDebug.

## Option 3: Jupyter Notebook

```bash
pip install jupyter
jupyter notebook playground/NeuralDebug_tour.ipynb
```

Run each cell interactively. Great for understanding the protocol and
experimenting with your own commands.

## Option 3: Manual CLI

```bash
# Start a debug server on the sample buggy program
python src/NeuralDebug/python_debug_session.py serve examples/sample_buggy_grades.py --port 5678 &

# Set a breakpoint and start
python src/NeuralDebug/python_debug_session.py cmd --port 5678 b 44
python src/NeuralDebug/python_debug_session.py cmd --port 5678 start

# Inspect state
python src/NeuralDebug/python_debug_session.py cmd --port 5678 inspect

# Step and watch variables change
python src/NeuralDebug/python_debug_session.py cmd --port 5678 step_over

# Clean up
python src/NeuralDebug/python_debug_session.py cmd --port 5678 quit
```

## What's next?

- **Students / Free tier**: Use [Gemini CLI](../docs/tutorials/gemini-cli.md) — free, no API key needed
- **Claude Code users**: Just run `claude` in this repo — NeuralDebug works out of the box via `CLAUDE.md` and `.mcp.json`. See [docs/tutorials/claude-code.md](../docs/tutorials/claude-code.md).
- Connect an AI agent: see [docs/tutorials/](../docs/tutorials/) for Copilot, Claude, OpenAI, Gemini, and LangChain guides
- Try C/C++ debugging: `python src/NeuralDebug/cpp_debug_session.py info` to check your toolchain
- Read the [examples/](../examples/) for walkthroughs of real debugging sessions
