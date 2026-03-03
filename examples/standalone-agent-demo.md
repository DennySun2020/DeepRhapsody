# Demo: NeuralDebug as a Standalone Agent

This demo shows how NeuralDebug works as a **standalone AI debugging agent** — no Claude, Copilot, or ChatGPT required. You bring your own LLM API key (or use a local model), and NeuralDebug handles the rest.

---

## Setup (2 minutes)

### Install NeuralDebug

```bash
# Clone the repo
git clone https://github.com/DennySun2020/DeepRhapsody.git
cd DeepRhapsody

# Install with agent support (editable mode for development)
pip install -e ".[agent]"
```

> **Note:** Once NeuralDebug is published to PyPI, you'll be able to install with just:
> `pip install NeuralDebug[agent]`

### Pick a Model Provider

Choose **one** of these options:

**Option A — Ollama (100% free, runs locally, no API key)**
```bash
# Install Ollama from https://ollama.com
ollama pull llama3.1
export NeuralDebug_PROVIDER=ollama
```

**Option B — Google Gemini (free tier, generous limits)**
```bash
# Get a free API key at https://aistudio.google.com/apikey
export GOOGLE_API_KEY=your-key-here
export NeuralDebug_PROVIDER=google
```

**Option C — OpenAI**
```bash
export OPENAI_API_KEY=sk-your-key
```

**Option D — Anthropic**
```bash
export ANTHROPIC_API_KEY=sk-ant-your-key
export NeuralDebug_PROVIDER=anthropic
```

**Option E — OpenRouter (one key, every model)**
```bash
export OPENROUTER_API_KEY=sk-or-your-key
export NeuralDebug_PROVIDER=openrouter
```

### Or, save it once in a config file

```bash
NeuralDebug config init
NeuralDebug config set provider=ollama
NeuralDebug config set model=llama3.1
NeuralDebug config show
```

---

## Demo 1: Interactive Chat — Debug a Python Script

We'll debug the included `sample_buggy_grades.py`, which computes grade statistics but gets all three results wrong.

### Start the Agent

```bash
cd examples/
NeuralDebug chat
```

```
NeuralDebug Agent — Ollama/llama3.1
Type your debugging request. Use 'quit' or Ctrl+C to exit.
```

### Conversation

```
you> The program sample_buggy_grades.py calculates mean, median, and std dev
     for student grades, but all three results are wrong. Find the bugs.
```

Behind the scenes, NeuralDebug autonomously:

1. **Calls `NeuralDebug_info`** to check available debuggers
   ```
   🔧 NeuralDebug_info({"language": "python"})
      → {"debugger": "bdb (stdlib)", "python": "3.11.9", "status": "available"}
   ```

2. **Starts a debug server** for the target script
   ```
   🔧 NeuralDebug_start_server({"target": "sample_buggy_grades.py", "port": 5678})
      → {"status": "ok", "message": "Debug server started on port 5678"}
   ```

3. **Sets breakpoints** at suspicious locations
   ```
   🔧 NeuralDebug_set_breakpoint({"location": "44", "port": 5678})
      → {"status": "ok", "message": "Breakpoint set at line 44"}
   ```

4. **Steps through code**, inspecting variables
   ```
   🔧 NeuralDebug_start_execution({"port": 5678})
      → {"status": "paused", "current_location": {"file": "sample_buggy_grades.py", "line": 44}}

   🔧 NeuralDebug_inspect({"port": 5678})
      → {"local_variables": {"name": {"type": "str", "value": "Alice"}, "score": {"type": "int", "value": 92}}}
   ```

5. **Evaluates expressions** to test hypotheses
   ```
   🔧 NeuralDebug_evaluate({"expression": "len(valid)", "port": 5678})
      → {"result": "8", "type": "int"}
   ```

6. **Reports findings** in plain language:
   ```
   I found 3 bugs:

   | # | Location | Bug | Fix |
   |---|----------|-----|-----|
   | 1 | Line 44  | `score >= 0` includes zero scores | Change to `score > 0` |
   | 2 | Line 60  | Median computed on unsorted list | Add `scores = sorted(scores)` |
   | 3 | Line 75  | Divides by N instead of N-1 | Use `len(scores) - 1` |
   ```

7. **Stops the debug server** when done
   ```
   🔧 NeuralDebug_stop({"port": 5678})
      → {"status": "ok", "message": "Debug server stopped"}
   ```

---

## Demo 2: One-Shot Mode — Debug a C Program

For quick tasks, use `NeuralDebug run` instead of an interactive chat:

```bash
NeuralDebug run "Debug sample_buggy_stats.c — the mean and passing count are wrong. \
Find the bugs and explain the root cause." \
  --provider google --model gemini-2.5-flash
```

NeuralDebug runs the full debug cycle autonomously and prints the final report:

```
🔧 NeuralDebug_info({"language": "cpp"})
🔧 NeuralDebug_start_server({"target": "sample_buggy_stats.c", "port": 5679})
🔧 NeuralDebug_set_breakpoint({"location": "compute_sum", "port": 5679})
🔧 NeuralDebug_start_execution({"port": 5679})
🔧 NeuralDebug_step({"action": "step_over", "port": 5679})
🔧 NeuralDebug_inspect({"port": 5679})
🔧 NeuralDebug_evaluate({"expression": "i", "port": 5679})
🔧 NeuralDebug_stop({"port": 5679})

Found 3 bugs in sample_buggy_stats.c:

1. **Off-by-one in compute_sum (line 41)**: Loop uses `i <= count` instead of
   `i < count`, reading one element past the array bounds. This corrupts the
   sum with garbage memory.

2. **Integer division in compute_mean (line 53)**: `sum / count` performs
   integer division (both operands are int). Fix: cast to `(double)sum / count`.

3. **Wrong comparison in count_passing (line 64)**: Uses `> 60` instead of
   `>= 60`, so students scoring exactly 60 aren't counted as passing.
```

### Quiet mode

Suppress the tool-call output and only see the final answer:

```bash
NeuralDebug run "find bugs in sample_buggy_grades.py" --quiet
```

---

## Demo 3: Switching Providers on the Fly

The same debugging session works with any model — just change the flag:

```bash
# Use GPT-4o
NeuralDebug chat --provider openai --model gpt-4o

# Use Claude Sonnet 4
NeuralDebug chat --provider anthropic --model claude-sonnet-4-20250514

# Use Gemini 2.5 Pro
NeuralDebug chat --provider google --model gemini-2.5-pro

# Use a local Llama model (no internet needed)
NeuralDebug chat --provider ollama --model llama3.1

# Use any model via OpenRouter
NeuralDebug chat --provider openrouter --model meta-llama/llama-3.1-405b

# Use any OpenAI-compatible API (DeepSeek, Groq, Together, etc.)
OPENAI_API_KEY=your-key NeuralDebug_BASE_URL=https://api.deepseek.com/v1 \
  NeuralDebug chat --provider openai --model deepseek-chat
```

### List all available models

```bash
$ NeuralDebug models

openai:
  gpt-4o                                   GPT-4o  (128,000 tokens)
  gpt-4o-mini                              GPT-4o Mini  (128,000 tokens)
  o3-mini                                  o3-mini  (200,000 tokens)

anthropic:
  claude-sonnet-4-20250514                 Claude Sonnet 4  (200,000 tokens)
  claude-opus-4-20250514                   Claude Opus 4  (200,000 tokens)

google:
  gemini-2.5-flash                         Gemini 2.5 Flash  (1,048,576 tokens)
  gemini-2.5-pro                           Gemini 2.5 Pro  (1,048,576 tokens)

ollama:
  llama3.1                                 Llama 3.1
  qwen2.5-coder                            Qwen 2.5 Coder
  deepseek-coder-v2                        DeepSeek Coder V2

openrouter:
  anthropic/claude-sonnet-4                Claude Sonnet 4  (200,000 tokens)
  openai/gpt-4o                            GPT-4o  (128,000 tokens)
```

---

## Demo 4: PilotHub Skills

Extend NeuralDebug with community-contributed debugging skills:

```bash
# Search for skills
$ NeuralDebug hub search "memory debugging"
  memory-debugger                v1.0.0     Debug memory leaks using Valgrind  [memory, c, cpp]
  asan-helper                    v0.3.0     AddressSanitizer workflow           [memory, sanitizer]

# Install a skill
$ NeuralDebug hub install memory-debugger
✅ Installed to ~/.NeuralDebug/skills/memory-debugger

# List installed skills
$ NeuralDebug hub list
Installed skills:
  memory-debugger                v1.0.0     Debug memory leaks using Valgrind

# Skills are automatically loaded in chat/run sessions
$ NeuralDebug chat
NeuralDebug Agent — OpenAI/gpt-4o (1 skill loaded)
```

### Create your own skill

Create a directory with a `SKILL.md`:

```markdown
---
name: django-debugger
description: Debug Django web applications with request/response tracing
version: 1.0.0
author: DennySun2020
tags: [django, python, web]
---

# Django Debugger

When debugging Django applications:

1. Set breakpoints in the view function handling the failing URL
2. Check `request.method`, `request.GET`, `request.POST`
3. Inspect the queryset: evaluate `MyModel.objects.filter(...).query` to see SQL
4. Check middleware ordering if request never reaches the view
5. For template errors, set breakpoint in the template context processor
```

Then publish it:

```bash
NeuralDebug hub publish ./django-debugger
```

---

## Demo 5: Configuration Persistence

Save your preferences so you don't need flags every time:

```bash
# Create config file
$ NeuralDebug config init
Created default config: ~/.NeuralDebug/config.yaml

# Set your preferred provider
$ NeuralDebug config set provider=ollama
Saved provider=ollama to ~/.NeuralDebug/config.yaml

$ NeuralDebug config set model=llama3.1
Saved model=llama3.1 to ~/.NeuralDebug/config.yaml

# Verify
$ NeuralDebug config show
provider:    ollama
model:       llama3.1
api_key:     (not set)
base_url:    (default)
max_turns:   50
temperature: 0.0
skills_dir:  ~/.NeuralDebug/skills

# Now just run — no flags needed
$ NeuralDebug chat
NeuralDebug Agent — Ollama/llama3.1
```

The config file at `~/.NeuralDebug/config.yaml` is simple YAML:

```yaml
provider: ollama
model: llama3.1
max_turns: 50
temperature: 0.0
```

Environment variables override the config file, and CLI flags override everything.

---

## Architecture: How It Fits Together

```
┌──────────────────────────────────────────────────────────┐
│                     User Interface                        │
│                                                          │
│  NeuralDebug chat          NeuralDebug run "prompt"        │
│  (interactive REPL)       (one-shot mode)                │
└──────────────┬───────────────────────┬───────────────────┘
               │                       │
               ▼                       ▼
┌──────────────────────────────────────────────────────────┐
│                    Agent Runner                           │
│              (src/agent/runner.py)                        │
│                                                          │
│   while True:                                            │
│     response = provider.chat(messages, tools)            │
│     if response.tool_calls:                              │
│       results = execute_tools(response.tool_calls) ──┐   │
│       messages.append(results)                       │   │
│       continue                                       │   │
│     else:                                            │   │
│       return response.text                           │   │
└──────┬───────────────────────────────────────────────┼───┘
       │                                               │
       ▼                                               ▼
┌──────────────────┐                  ┌────────────────────────┐
│  LLM Provider    │                  │    Tool Registry       │
│                  │                  │                        │
│  ┌─ OpenAI ────┐ │                  │  NeuralDebug_info       │
│  ├─ Anthropic ─┤ │                  │  NeuralDebug_start      │
│  ├─ Google ────┤ │                  │  NeuralDebug_step       │
│  ├─ Ollama ────┤ │                  │  NeuralDebug_inspect    │
│  └─ OpenRouter ┘ │                  │  NeuralDebug_evaluate   │
│                  │                  │  NeuralDebug_continue   │
│  User's API key  │                  │  NeuralDebug_stop       │
│  User's model    │                  │  ... (14 tools)        │
└──────────────────┘                  │  + PilotHub skills     │
                                      └────────────┬───────────┘
                                                   │
                                                   ▼
                                      ┌────────────────────────┐
                                      │ Existing MCP Server    │
                                      │ (integrations/mcp/)    │
                                      │                        │
                                      │ Same handle_tool_call  │
                                      │ used by Claude, Copilot│
                                      │ ChatGPT, LangChain...  │
                                      └────────────┬───────────┘
                                                   │
                                                   ▼
                                      ┌────────────────────────┐
                                      │  Debug Server (TCP)    │
                                      │                        │
                                      │  Python → bdb          │
                                      │  C/C++  → GDB/LLDB    │
                                      │  C#     → netcoredbg   │
                                      │  Rust   → rust-gdb     │
                                      │  Java   → JDB          │
                                      │  Go     → Delve        │
                                      │  Node   → Inspector    │
                                      │  Ruby   → rdbg         │
                                      └────────────────────────┘
```

**Key design point:** The standalone agent uses the *exact same* tool implementations as the MCP server. Whether NeuralDebug is driven by Claude Desktop, Copilot CLI, a LangChain agent, or its own standalone runner — the debugging behavior is identical.

---

## Supported Languages

All 11 languages work in standalone mode, same as with any external agent:

| Language | Debugger | Example |
|----------|----------|---------|
| Python | bdb (stdlib) | `NeuralDebug run "debug main.py"` |
| C/C++ | GDB, LLDB, CDB | `NeuralDebug run "debug server.c — segfault on connect"` |
| C# | netcoredbg | `NeuralDebug run "debug Program.cs"` |
| Rust | rust-gdb, rust-lldb | `NeuralDebug run "debug src/main.rs — panic at line 42"` |
| Java | JDB | `NeuralDebug run "debug App.java"` |
| Go | Delve | `NeuralDebug run "debug main.go — goroutine deadlock"` |
| Node.js | Node Inspector | `NeuralDebug run "debug server.js"` |
| Ruby | rdbg | `NeuralDebug run "debug app.rb"` |
| Assembly | GDB/LLDB/CDB | `NeuralDebug run "debug binary.exe at assembly level"` |

---

## Quick Reference

```bash
# Interactive session
NeuralDebug chat
NeuralDebug chat --provider ollama --model llama3.1

# One-shot task
NeuralDebug run "find the bug in main.py"
NeuralDebug run "why does server.c segfault?" --quiet

# Configuration
NeuralDebug config init
NeuralDebug config show
NeuralDebug config set provider=google

# Model discovery
NeuralDebug models
NeuralDebug models --provider ollama

# PilotHub skills
NeuralDebug hub search "memory"
NeuralDebug hub install my-skill
NeuralDebug hub list
NeuralDebug hub publish ./my-skill

# Environment variables
NeuralDebug_PROVIDER=ollama     # Provider name
NeuralDebug_MODEL=llama3.1     # Model ID
NeuralDebug_API_KEY=...        # API key (or use provider-specific vars)
NeuralDebug_BASE_URL=...       # Custom API endpoint
NeuralDebug_MAX_TURNS=50       # Max tool-use iterations
NeuralDebug_TEMPERATURE=0.0    # Sampling temperature
```
