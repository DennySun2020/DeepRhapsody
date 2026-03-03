# Contributing to DeepRhapsody

Thank you for your interest in contributing! Here's how you can help.

## Ways to Contribute

- **Report bugs** — [Open an issue](../../issues/new?template=bug_report.md)
- **Request features** — [Open an issue](../../issues/new?template=feature_request.md)
- **Add language support** — Follow the guide below
- **Improve tutorials** — Help others get started
- **Write integration adapters** — Connect NeuralDebug to new AI platforms

## Development Setup

```bash
git clone https://github.com/DennySun2020/DeepRhapsody.git
cd DeepRhapsody
python -m py_compile src/NeuralDebug/python_debug_session.py  # Syntax check
```

No build step needed. The debug session scripts are standalone Python files.

## Adding a New Language

NeuralDebug uses **auto-discovery** — adding a new language requires only creating
one script file. All integrations (MCP, LangChain, OpenAI) pick it up
automatically.

### Step 1: Create the debug session script

Create `src/NeuralDebug/{language}_debug_session.py`.

Start with the **`LANGUAGE_META`** dict and import the shared base classes:

```python
#!/usr/bin/env python3
"""Kotlin Debug Session — Interactive via JDB."""

import argparse
import json
import os
# ... other imports ...

from debug_common import (
    BaseDebugServer, DebugResponseMixin, error_response, completed_response,
    send_command, get_pid_file, write_pid_file, remove_pid_file,
    find_repo_root, cmd_send_handler,
)

# This dict is required — language_registry.py discovers it automatically.
LANGUAGE_META = {
    "name": "kotlin",                   # unique identifier
    "display_name": "Kotlin",           # human-readable name
    "extensions": [".kt", ".kts"],      # file extensions to associate
    "default_port": 5685,               # unique port (next after 5684)
    "debuggers": "JDB",                 # debugger backend description
    "aliases": [],                       # alternative names (e.g. ["typescript"] for nodejs)
}
```

### Step 2: Implement the debugger class

Create a debugger class that implements the standard command interface.
Inherit from `DebugResponseMixin` (text-based debuggers) or `MiDebuggerBase`
(GDB/MI protocol debuggers):

```python
class KotlinDebugger(DebugResponseMixin):
    """Drives JDB for Kotlin programs."""

    def __init__(self, target, debugger_path="jdb", ...):
        self.target = target
        self.is_started = False
        self.is_finished = False
        # ...

    def start_debugger(self):
        """Launch the debugger subprocess."""
        ...

    # Required commands — all must return a dict matching the JSON schema
    def cmd_start(self, args=""):       ...
    def cmd_continue(self):             ...
    def cmd_step_in(self):              ...
    def cmd_step_over(self):            ...
    def cmd_step_out(self):             ...
    def cmd_set_breakpoint(self, args): ...
    def cmd_remove_breakpoint(self, args): ...
    def cmd_list_breakpoints(self):     ...
    def cmd_inspect(self):              ...
    def cmd_evaluate(self, expr):       ...
    def cmd_list_source(self, args):    ...
    def cmd_backtrace(self):            ...
    def cmd_quit(self):                 ...
    def _get_new_stdout(self):          ...
```

### Step 3: Create the debug server

Inherit from `BaseDebugServer` — this gives you the TCP server, command
dispatch, and JSON protocol for free:

```python
class KotlinDebugServer(BaseDebugServer):
    LANGUAGE = "Kotlin"
    SCRIPT_NAME = "kotlin_debug_session.py"

    # Only override if you have language-specific commands:
    # def _dispatch_extra(self, action, args):
    #     if action in ("threads", "t"):
    #         return self.debugger.cmd_threads()
    #     return None
```

### Step 4: Add CLI entry point

Follow the standard `serve` / `cmd` / `info` pattern:

```python
def cmd_serve(args):
    write_pid_file('kotlin', args.port)
    # ... set up debugger and server ...
    server = KotlinDebugServer(debugger, port=args.port)
    try:
        server.run()
    finally:
        remove_pid_file('kotlin', args.port)

def cmd_send(args):
    cmd_send_handler(args)

def cmd_info(args=None):
    # Detect toolchain and print JSON
    ...

def main():
    parser = argparse.ArgumentParser(...)
    subparsers = parser.add_subparsers(dest="mode")
    # ... add serve, cmd, info subparsers ...

if __name__ == "__main__":
    main()
```

### Step 5: Verify it works

```bash
# Syntax check
python -m py_compile src/NeuralDebug/kotlin_debug_session.py

# Verify auto-discovery picks it up
python -c "
import sys; sys.path.insert(0, 'src/NeuralDebug')
from language_registry import discover
reg = discover()
print(reg.lang_scripts['kotlin'])   # -> kotlin_debug_session.py
print(reg.ext_to_lang['.kt'])       # -> kotlin
"

# Test with a real program
python src/NeuralDebug/kotlin_debug_session.py info
python src/NeuralDebug/kotlin_debug_session.py serve MyApp.kt --port 5685
python src/NeuralDebug/kotlin_debug_session.py cmd --port 5685 start
```

### Step 6: Update documentation

1. Add a row to the language table in `README.md`
2. Add a row to the detection table in `.github/agents/NeuralDebug.agent.md`
3. Update `.github/skills/debugger/SKILL.md` with language-specific details
4. Optionally add an example in `examples/`

> **Note:** You do NOT need to edit `integrations/mcp/server.py` or
> `integrations/langchain/tools.py` — they auto-discover new languages
> from the `LANGUAGE_META` dict in your script.

## Code Style

- Python 3.8+ compatible (no walrus operator, no `match/case`)
- Import shared utilities from `debug_common.py` — do not duplicate
- Use `argparse` for CLI interface
- Follow existing patterns for error handling and JSON output
- Export `LANGUAGE_META` at module level for auto-discovery

## Pull Request Process

1. Fork the repo and create a feature branch
2. Make your changes
3. Run syntax validation: `python -m py_compile your_script.py`
4. Test manually with a real program in the target language
5. Update documentation if needed
6. Open a PR with a clear description of the change

## Testing

Currently, testing is manual. Run the debug session against a real program:

```bash
# 1. Start server
python scripts/{language}_debug_session.py serve test_program --port PORT --daemonize

# 2. Set breakpoint and run
python scripts/{language}_debug_session.py cmd --port PORT b main
python scripts/{language}_debug_session.py cmd --port PORT start

# 3. Verify output has correct format
python scripts/{language}_debug_session.py cmd --port PORT inspect

# 4. Clean up
python scripts/{language}_debug_session.py stop --port PORT
```

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
