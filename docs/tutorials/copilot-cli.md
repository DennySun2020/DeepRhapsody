# Tutorial: Using NeuralDebug with GitHub Copilot CLI

This guide shows how to use NeuralDebug as a custom agent inside GitHub Copilot CLI.

## Prerequisites

- [GitHub Copilot CLI](https://docs.github.com/en/copilot/using-github-copilot/using-github-copilot-in-the-command-line) installed
- Python 3.8+
- A debugger for your target language (GDB, LLDB, CDB, etc.)

## Setup

1. Clone the NeuralDebug repo into your project (or any repo you want to debug):

```bash
# Option A: Clone as a standalone repo
git clone https://github.com/DennySun2020/DeepRhapsody.git
cd DeepRhapsody

# Option B: Add to an existing repo
# Copy .github/agents/ and .github/skills/ into your repo's .github/
cp -r NeuralDebug/.github/agents/ your-repo/.github/agents/
cp -r NeuralDebug/.github/skills/ your-repo/.github/skills/
```

2. Verify Copilot CLI sees the agent:

```bash
# Open Copilot CLI in the repo directory
# NeuralDebug should appear in the agent list
```

## Usage

NeuralDebug supports two modes: **autonomous** (AI drives everything) and
**interactive** (you direct each step).

### Autonomous Mode — "Find the bug for me"

Just describe the problem. NeuralDebug reads the code, sets strategic
breakpoints, steps through execution, and reports back with the root cause.

```
You: @NeuralDebug debug examples/sample_buggy_grades.py
     The mean calculation seems wrong

NeuralDebug: I've read the code. Two key functions: filter_valid_grades() and
            calculate_mean(). The filter looks suspicious — let me investigate...
            [Sets breakpoints, steps through all loop iterations]
            [Finds that score=0 passes the >= 0 check]

            Root cause: Line 44 uses `score >= 0` instead of `score > 0`,
            allowing zero scores through the filter. This drags the mean down.
```

### Interactive Mode — "I'll drive, you translate"

Take control and explore step by step. Just say what you want in plain English.

```
You: @NeuralDebug start debugging examples/sample_buggy_grades.py

NeuralDebug: Debug server started on port 5678. Program loaded but not running yet.
            What would you like to do?

You: Set a breakpoint at line 44

NeuralDebug: Breakpoint set at filter_valid_grades(), line 44.

You: Start the program

NeuralDebug: Paused at line 44. Variables: name='Alice', score=85, valid=[].

You: Step over until we hit score=0

NeuralDebug: [Steps through iterations...]
            Now at line 44: name='Eve', score=0. ⚠️ This zero score will pass
            the >= check. Want me to continue or inspect further?

You: Show the call stack

NeuralDebug: #0 filter_valid_grades() at sample_buggy_grades.py:44
            #1 main() at sample_buggy_grades.py:60
```

### More Examples

```
You: @NeuralDebug debug examples/sample_buggy_grades.py
     The mean calculation seems wrong

NeuralDebug: I'll launch a debug server and investigate...
            [Sets breakpoints, steps through code, finds the bug]
            Root cause: Line 44 uses >= instead of > for zero check.
```

### Debug a C file (autonomous)

```
You: @NeuralDebug debug my_server.c
     It crashes on the second connection

NeuralDebug: Detected MSVC + CDB on Windows. Auto-compiling with debug symbols...
            [Compiles, launches debugger, hits crash]
            Root cause: recv() return value not checked before use.
```

### Debug a C# project (autonomous)

```
You: @NeuralDebug debug src/Program.cs
     The inventory total is wrong

NeuralDebug: Building with dotnet build, launching netcoredbg...
            [Steps through CalculateTotal, finds missing quantity multiplier]
```

## How It Works

1. Copilot CLI reads `.github/agents/NeuralDebug.agent.md`
2. When invoked, it gets the system prompt with full debugging instructions
3. The agent calls debug session scripts via shell commands
4. Scripts manage persistent TCP debug servers that survive between turns
5. Each command returns JSON with location, variables, call stack

## Tips

- NeuralDebug auto-detects the language from the file extension
- For C/C++, it auto-compiles with debug symbols — no manual build needed
- The debug server persists across conversation turns
- Say "quit" or "stop debugging" when done
- You can set conditional breakpoints: "break at line 42 when x > 10"
