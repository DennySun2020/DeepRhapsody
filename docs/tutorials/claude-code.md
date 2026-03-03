# Tutorial: Using NeuralDebug with Claude Code

Claude Code reads the `CLAUDE.md` and `.mcp.json` files in this repo
automatically. That means NeuralDebug works out of the box — just open
the project in Claude Code and start debugging.

## Setup (30 seconds)

```bash
git clone https://github.com/DennySun2020/DeepRhapsody.git
cd DeepRhapsody
claude   # open Claude Code in this directory
```

That's it. Claude Code reads `CLAUDE.md` (which describes all the debug
commands) and `.mcp.json` (which registers the MCP debug tools).

## Option A: Shell commands (simplest)

Claude Code can run the debug scripts directly. Just ask:

```
You: Debug examples/sample_buggy_grades.py — the mean calculation is wrong

Claude: I'll start a debug session and investigate.
        [runs: python src/NeuralDebug/python_debug_session.py serve ...]
        [runs: python src/NeuralDebug/python_debug_session.py cmd ... b 44]
        [runs: python src/NeuralDebug/python_debug_session.py cmd ... start]
        ...
        Found the bug: line 44 uses >= 0 instead of > 0
```

This works because `CLAUDE.md` tells Claude Code exactly how to use the
debug scripts, what commands are available, and which script to pick for
each language.

## Option B: MCP tools (richer integration)

The `.mcp.json` file registers NeuralDebug as an MCP server. Claude Code
can call structured tools like `NeuralDebug_start_server`,
`NeuralDebug_set_breakpoint`, `NeuralDebug_inspect`, etc. — no shell
parsing needed.

The MCP tools expose the same functionality as the CLI but with typed
parameters and JSON responses, which lets Claude reason more precisely
about debug state.

## Example session

```
You: There's a bug in examples/sample_buggy_grades.py.
     Expected median=85.00 but getting something else. Find it.

Claude: Let me check the toolchain first.
        → NeuralDebug_info(language="python")
        Python 3.11, bdb available.

        Starting debug server.
        → NeuralDebug_start_server(target="examples/sample_buggy_grades.py", port=5678)
        Server running.

        The median is calculated in calculate_median() at line 56.
        Setting breakpoint there.
        → NeuralDebug_set_breakpoint(location="61", port=5678)

        Starting execution.
        → NeuralDebug_start(port=5678)
        Paused at line 61 in calculate_median().

        Let me check if the scores are sorted.
        → NeuralDebug_evaluate(expression="sorted(scores) == scores", port=5678)
        Result: False

        The scores aren't sorted! The median function doesn't sort
        the list before finding the middle element.

        Bug: line 61 in calculate_median() — missing `scores = sorted(scores)`.
        The function picks the middle of an unsorted list, giving wrong results.
```

## Customizing CLAUDE.md

You can edit `CLAUDE.md` to add project-specific debugging instructions:

```markdown
## Project-specific debugging notes

- The API server runs on port 8080; debug the handler at routes/api.py
- Use build_cmd="make debug" for the C++ components
- The test suite is in tests/ — debug failing tests with:
  python src/NeuralDebug/python_debug_session.py serve tests/test_auth.py --port 5678
```

## Troubleshooting

**Claude doesn't use NeuralDebug**: Make sure `CLAUDE.md` exists at the
repo root and you opened Claude Code from the repo directory.

**MCP tools not available**: Check that `.mcp.json` is at the repo root
and the Python path is correct. Run `python integrations/mcp/server.py`
manually to test.

**Debugger not found**: Run `python src/NeuralDebug/cpp_debug_session.py info`
to check what's available on your system.
