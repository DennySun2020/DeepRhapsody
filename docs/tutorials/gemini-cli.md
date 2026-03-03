# Tutorial: Using NeuralDebug with Gemini CLI (Free)

Gemini CLI is Google's **free** AI coding agent for the terminal. It gives
you 1,000 requests/day at no cost — perfect for trying NeuralDebug without
any paid subscriptions.

## Prerequisites

- **Node.js 18+** (for Gemini CLI)
- **Python 3.8+** (for NeuralDebug)
- **Google account** (free, for authentication)

## Setup (2 minutes)

### Step 1: Install Gemini CLI

```bash
npm install -g @google/gemini-cli
```

Or run without installing:
```bash
npx @google/gemini-cli
```

### Step 2: Clone NeuralDebug

```bash
git clone https://github.com/DennySun2020/DeepRhapsody.git
cd DeepRhapsody
```

### Step 3: Start Gemini CLI in the repo

```bash
gemini
```

On first run, Gemini CLI will ask you to log in with your Google account.
Follow the browser flow — no API key or credit card needed.

**That's it.** Gemini CLI automatically reads `.gemini/system.md` in the
repo root, which teaches it how to use NeuralDebug's debug commands.

## Try It: Debug the Sample Python Program

Once Gemini CLI is open, just type:

```
You: 

```

Gemini will:
1. Read the source code to understand the program
2. Start the debug server: `python src/NeuralDebug/python_debug_session.py serve ...`
3. Set breakpoints at suspicious locations
4. Step through execution and inspect variables
5. Report the root cause and suggest a fix

### Example Interaction

```
You: Debug examples/sample_buggy_grades.py

Gemini: I'll read the file first to understand its structure.
        [reads sample_buggy_grades.py]

        The program filters grades, then calculates mean, median, and std dev.
        There are suspicious areas. Let me start a debug session.

        [runs: python src/NeuralDebug/python_debug_session.py serve
               examples/sample_buggy_grades.py --port 5678 &]
        [runs: python src/NeuralDebug/python_debug_session.py cmd --port 5678 b 44]
        [runs: python src/NeuralDebug/python_debug_session.py cmd --port 5678 start]
        [runs: python src/NeuralDebug/python_debug_session.py cmd --port 5678 continue]

        Paused at line 44: `if score >= 0 and score <= 100:`
        Variables: name='Eve', score=0, valid=[('Alice', 92), ('Bob', 85)]

        ⚠️ Bug found: score=0 passes the filter because `0 >= 0` is True.
        The condition should be `score > 0` to exclude zeros.

You: What about the median?

Gemini: [runs: python src/NeuralDebug/python_debug_session.py cmd --port 5678 continue]

        Paused at line 61 in calculate_median().
        scores = [92, 85, 0, 73, 88, 95, 67, 42]

        ⚠️ Bug found: scores aren't sorted! Median requires sorted data.
        Missing `scores = sorted(scores)` before line 61.
```

## Try It: Debug a C Program

```
You: Debug examples/concurrent_pipeline.c with 10 tasks.
     Check if the consumer sum is correct.
```

Gemini CLI will auto-compile the C file, start CDB/GDB, set breakpoints,
and step through the multi-threaded pipeline.

## How It Works

NeuralDebug's Gemini CLI integration uses a single file:

```
.gemini/
  system.md    ← System prompt that teaches Gemini how to use NeuralDebug
```

When you run `gemini` in the repo directory, Gemini CLI reads
`.gemini/system.md` and understands all the debug commands, the JSON
protocol, and the workflow. No plugins, no API wrappers, no SDK needed.

### Enabling the system prompt

By default, Gemini CLI reads `.gemini/system.md` in the project root.
If it's not picked up automatically, set the environment variable:

```bash
# Use the project's system prompt
GEMINI_SYSTEM_MD=1 gemini

# Or point to the file explicitly
GEMINI_SYSTEM_MD=.gemini/system.md gemini
```

You can also create a `.gemini/.env` file to persist this:
```
GEMINI_SYSTEM_MD=1
```

## Customizing for Your Project

Edit `.gemini/system.md` to add project-specific debugging instructions:

```markdown
## Project-specific notes

- The API server entry point is src/server.py
- Build the C++ components with: make debug
- Run tests with: pytest tests/ -x
- The database schema is in migrations/
```

## Comparison: Free Agent Platforms

| Platform | Free Tier | Setup |
|----------|-----------|-------|
| **Gemini CLI** | 1,000 req/day | `npm install -g @google/gemini-cli` + Google account |
| GitHub Copilot CLI | Included with Copilot | VS Code or `gh copilot` |
| Claude Code | Requires API key ($) | `ANTHROPIC_API_KEY` |
| OpenAI Codex CLI | Requires API key ($) | `OPENAI_API_KEY` |

Gemini CLI is the most accessible free option for students and open-source
contributors who want to try AI-powered debugging.

## Supported Languages

NeuralDebug works with all 8 languages through Gemini CLI:

| Language | What Gemini CLI runs |
|----------|---------------------|
| Python | `python_debug_session.py` (bdb) |
| C/C++ | `cpp_debug_session.py` (GDB/LLDB/CDB, auto-compiles) |
| C# | `csharp_debug_session.py` (netcoredbg) |
| Rust | `rust_debug_session.py` (rust-gdb/rust-lldb) |
| Java | `java_debug_session.py` (JDB) |
| Go | `go_debug_session.py` (Delve) |
| Node.js/TS | `nodejs_debug_session.py` (Node Inspector) |
| Ruby | `ruby_debug_session.py` (rdbg) |

## Troubleshooting

**Gemini doesn't use debug commands**: Make sure `.gemini/system.md`
exists in the repo root. Run `GEMINI_SYSTEM_MD=1 gemini` to force it.

**"Node.js not found"**: Install Node.js 18+ from https://nodejs.org/

**Debug server won't start**: Check Python is available with
`python --version`. NeuralDebug requires Python 3.8+.

**Gemini runs out of context**: For long debug sessions, start a new
conversation. The debug server stays alive across conversations — Gemini
will reconnect via `cmd ping`.
