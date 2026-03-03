#!/usr/bin/env python3
"""Batch-mode Python debugger — run to breakpoints and dump state as JSON."""

import argparse
import bdb
import io
import json
import linecache
import os
import signal
import sys
import threading
import traceback
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def safe_repr(obj: Any, max_length: int = 500) -> str:
    """Safely get repr of an object, truncating if needed."""
    try:
        r = repr(obj)
        if len(r) > max_length:
            return r[:max_length] + "...<truncated>"
        return r
    except Exception as e:
        return f"<repr error: {e}>"


def safe_str(obj: Any, max_length: int = 500) -> str:
    """Safely get str of an object, truncating if needed."""
    try:
        s = str(obj)
        if len(s) > max_length:
            return s[:max_length] + "...<truncated>"
        return s
    except Exception as e:
        return f"<str error: {e}>"


def serialize_variable(name: str, value: Any) -> Dict[str, str]:
    """Serialize a variable into a JSON-friendly dict."""
    return {
        "type": type(value).__name__,
        "value": safe_str(value),
        "repr": safe_repr(value),
    }


def is_user_frame(filename: str, target_dir: str) -> bool:
    """Check if a frame belongs to user code (not stdlib/debugger)."""
    if not filename:
        return False
    abs_filename = os.path.abspath(filename)
    # Exclude this debugger script itself
    if abs_filename == os.path.abspath(__file__):
        return False
    # Exclude bdb, pdb, threading, and other stdlib modules
    stdlib_modules = ['bdb.py', 'pdb.py', 'cmd.py', 'threading.py', 'runpy.py']
    if any(mod in filename for mod in stdlib_modules):
        return False
    # Include files in the target directory
    if abs_filename.startswith(target_dir):
        return True
    # Include the target file itself
    return True


SKIP_GLOBALS = {
    '__name__', '__doc__', '__package__', '__loader__', '__spec__',
    '__annotations__', '__builtins__', '__file__', '__cached__',
}


class BreakpointDebugger(bdb.Bdb):
    """
    A non-interactive debugger that sets breakpoints, runs to them,
    and captures call stack + local variables at each hit.
    """

    def __init__(
        self,
        target_file: str,
        breakpoints: List[int],
        max_hits: int = 5,
        condition: Optional[str] = None,
    ):
        super().__init__()
        self.target_file = os.path.abspath(target_file)
        self.target_dir = os.path.dirname(self.target_file)
        self.breakpoint_lines: Set[int] = set(breakpoints)
        self.max_hits = max_hits
        self.condition = condition
        self.hits: List[Dict] = []
        self.total_hits = 0
        self.finished = False
        self.error_message: Optional[str] = None

        # Set breakpoints
        for line in self.breakpoint_lines:
            self.set_break(self.target_file, line)

    def user_line(self, frame):
        """Called when we stop at a line (breakpoint hit)."""
        filename = self.canonic(frame.f_code.co_filename)
        lineno = frame.f_lineno

        # Check if this is one of our breakpoints
        if filename == self.canonic(self.target_file) and lineno in self.breakpoint_lines:
            # Evaluate condition if any
            if self.condition:
                try:
                    result = eval(self.condition, frame.f_globals, frame.f_locals)
                    if not result:
                        self.set_continue()
                        return
                except Exception:
                    # If condition eval fails, still break
                    pass

            self.total_hits += 1
            if self.total_hits <= self.max_hits:
                hit_data = self._capture_state(frame, lineno)
                self.hits.append(hit_data)

            if self.total_hits >= self.max_hits:
                self.set_quit()
                return

        self.set_continue()

    def user_return(self, frame, return_value):
        """Called when a function returns."""
        self.set_continue()

    def user_exception(self, frame, exc_info):
        """Called when an exception occurs."""
        exc_type, exc_value, exc_tb = exc_info
        self.error_message = f"{exc_type.__name__}: {exc_value}"
        self.set_continue()

    def _capture_state(self, frame: Any, breakpoint_line: int) -> Dict:
        """Capture the full debugging state at the current breakpoint."""
        call_stack = self._capture_call_stack(frame)
        local_vars = self._capture_locals(frame)
        global_vars = self._capture_globals(frame)

        return {
            "breakpoint_line": breakpoint_line,
            "hit_number": self.total_hits,
            "call_stack": call_stack,
            "local_variables": local_vars,
            "global_variables_snapshot": global_vars,
        }

    def _capture_call_stack(self, frame: Any) -> List[Dict]:
        """Walk the frame stack and capture each frame's info."""
        stack = []
        current = frame
        index = 0

        while current is not None:
            filename = current.f_code.co_filename
            # Only include user frames
            if is_user_frame(filename, self.target_dir):
                line = current.f_lineno
                code_context = linecache.getline(filename, line).rstrip()
                stack.append({
                    "frame_index": index,
                    "file": os.path.relpath(filename, self.target_dir)
                            if filename.startswith(self.target_dir)
                            else filename,
                    "line": line,
                    "function": current.f_code.co_name,
                    "code_context": code_context,
                })
                index += 1
            current = current.f_back

        return stack

    def _capture_locals(self, frame: Any) -> Dict[str, Dict[str, str]]:
        """Capture local variables from the current frame."""
        result = {}
        for name, value in frame.f_locals.items():
            if name.startswith('__') and name.endswith('__'):
                continue
            result[name] = serialize_variable(name, value)
        return result

    def _capture_globals(self, frame: Any) -> Dict[str, Dict[str, str]]:
        """Capture a snapshot of user-defined global variables."""
        result = {}
        for name, value in frame.f_globals.items():
            if name in SKIP_GLOBALS:
                continue
            if name.startswith('__') and name.endswith('__'):
                continue
            if callable(value) and not isinstance(value, type):
                continue
            # Skip modules
            if isinstance(value, type(sys)):
                continue
            result[name] = serialize_variable(name, value)
        return result

    def run_target(self, args: List[str]) -> Dict:
        """
        Run the target file with breakpoints and return captured state.

        Args:
            args: Command-line arguments to pass to the target script

        Returns:
            Dict with status, hits, captures, and any errors
        """
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()

        # Prepare the target's execution environment
        old_argv = sys.argv[:]
        sys.argv = [self.target_file] + args

        try:
            # Read the target file
            with open(self.target_file, 'r') as f:
                code = f.read()

            compiled = compile(code, self.target_file, 'exec')

            # Build a clean namespace for the target so our debugger
            # internals don't appear in g_globals.
            target_globals = {
                '__name__': '__main__',
                '__file__': self.target_file,
                '__builtins__': __builtins__,
            }

            # Run with stdout/stderr capture
            with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
                try:
                    self.run(compiled, globals=target_globals, locals=target_globals)
                except bdb.BdbQuit:
                    pass  # Normal exit after max hits reached
                except Exception as e:
                    self.error_message = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

            status = "breakpoint_hit" if self.hits else "completed"
            if self.error_message and not self.hits:
                status = "error"

            return {
                "status": status,
                "target_file": self.target_file,
                "hits": self.hits,
                "total_breakpoint_hits": self.total_hits,
                "stdout_capture": stdout_capture.getvalue()[:5000],
                "stderr_capture": stderr_capture.getvalue()[:5000],
                "error_message": self.error_message,
            }

        except FileNotFoundError:
            return {
                "status": "error",
                "target_file": self.target_file,
                "hits": [],
                "total_breakpoint_hits": 0,
                "stdout_capture": "",
                "stderr_capture": "",
                "error_message": f"Target file not found: {self.target_file}",
            }
        except SyntaxError as e:
            return {
                "status": "error",
                "target_file": self.target_file,
                "hits": [],
                "total_breakpoint_hits": 0,
                "stdout_capture": "",
                "stderr_capture": "",
                "error_message": f"Syntax error in target file: {e}",
            }
        finally:
            sys.argv = old_argv


def cmd_debug(args: argparse.Namespace) -> None:
    """Execute the 'debug' subcommand."""
    target = args.target
    breakpoints = args.breakpoint
    max_hits = args.max_hits
    condition = args.condition
    timeout = args.timeout
    script_args = args.args.split() if args.args else []

    if not os.path.isfile(target):
        result = {
            "status": "error",
            "target_file": target,
            "hits": [],
            "total_breakpoint_hits": 0,
            "stdout_capture": "",
            "stderr_capture": "",
            "error_message": f"File not found: {target}",
        }
        print(json.dumps(result, indent=2))
        return

    debugger = BreakpointDebugger(
        target_file=target,
        breakpoints=breakpoints,
        max_hits=max_hits,
        condition=condition,
    )

    result = None

    def run_with_timeout():
        nonlocal result
        result = debugger.run_target(script_args)

    if timeout and timeout > 0:
        thread = threading.Thread(target=run_with_timeout, daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        if thread.is_alive():
            result = {
                "status": "error",
                "target_file": os.path.abspath(target),
                "hits": debugger.hits,
                "total_breakpoint_hits": debugger.total_hits,
                "stdout_capture": "",
                "stderr_capture": "",
                "error_message": f"Timeout after {timeout} seconds. "
                                 f"Captured {len(debugger.hits)} hit(s) before timeout.",
            }
    else:
        run_with_timeout()

    if result is None:
        result = {
            "status": "error",
            "target_file": os.path.abspath(target),
            "hits": [],
            "total_breakpoint_hits": 0,
            "stdout_capture": "",
            "stderr_capture": "",
            "error_message": "Unknown error: debugger produced no result.",
        }

    # Write output
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Debug results written to: {args.output}")
    else:
        print(json.dumps(result, indent=2))


def cmd_inspect(args: argparse.Namespace) -> None:
    """Execute the 'inspect' subcommand - read a previous debug result and summarize."""
    result_file = args.result_file

    if not os.path.isfile(result_file):
        print(json.dumps({"error": f"Result file not found: {result_file}"}, indent=2))
        return

    with open(result_file, 'r') as f:
        data = json.load(f)

    # Produce a human-readable summary
    summary_lines = []
    summary_lines.append(f"=== Debug Session Summary ===")
    summary_lines.append(f"Target: {data.get('target_file', 'unknown')}")
    summary_lines.append(f"Status: {data.get('status', 'unknown')}")
    summary_lines.append(f"Total hits: {data.get('total_breakpoint_hits', 0)}")
    summary_lines.append("")

    for hit in data.get("hits", []):
        summary_lines.append(f"--- Hit #{hit['hit_number']} at line {hit['breakpoint_line']} ---")
        summary_lines.append("Call Stack:")
        for frame in hit.get("call_stack", []):
            summary_lines.append(
                f"  [{frame['frame_index']}] {frame['function']}() "
                f"at {frame['file']}:{frame['line']}"
            )
            if frame.get('code_context'):
                summary_lines.append(f"       > {frame['code_context']}")
        summary_lines.append("")
        summary_lines.append("Local Variables:")
        for var_name, var_info in hit.get("local_variables", {}).items():
            summary_lines.append(
                f"  {var_name}: {var_info['type']} = {var_info['repr']}"
            )
        summary_lines.append("")

    if data.get("stdout_capture"):
        summary_lines.append("=== stdout ===")
        summary_lines.append(data["stdout_capture"])

    if data.get("stderr_capture"):
        summary_lines.append("=== stderr ===")
        summary_lines.append(data["stderr_capture"])

    if data.get("error_message"):
        summary_lines.append(f"=== Error ===")
        summary_lines.append(data["error_message"])

    print("\n".join(summary_lines))


def main():
    parser = argparse.ArgumentParser(
        description="Python Debugger - Non-interactive breakpoint debugging for agent use",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- debug subcommand ---
    debug_parser = subparsers.add_parser(
        "debug",
        help="Launch a Python file in debug mode with breakpoints",
    )
    debug_parser.add_argument(
        "target",
        help="Path to the Python file to debug",
    )
    debug_parser.add_argument(
        "--breakpoint", "-b",
        type=int,
        action="append",
        required=True,
        help="Line number(s) to set breakpoints at (can be repeated)",
    )
    debug_parser.add_argument(
        "--max-hits",
        type=int,
        default=5,
        help="Maximum number of breakpoint hits to capture (default: 5)",
    )
    debug_parser.add_argument(
        "--condition", "-c",
        type=str,
        default=None,
        help="Conditional expression; only break when this evaluates to True",
    )
    debug_parser.add_argument(
        "--args", "-a",
        type=str,
        default="",
        help="Arguments to pass to the target script (space-separated string)",
    )
    debug_parser.add_argument(
        "--timeout", "-t",
        type=int,
        default=30,
        help="Timeout in seconds (default: 30, 0 for no timeout)",
    )
    debug_parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output file path for JSON results (default: stdout)",
    )

    # --- inspect subcommand ---
    inspect_parser = subparsers.add_parser(
        "inspect",
        help="Read a previous debug result JSON and produce a human-readable summary",
    )
    inspect_parser.add_argument(
        "result_file",
        help="Path to a debug result JSON file",
    )

    args = parser.parse_args()

    if args.command == "debug":
        cmd_debug(args)
    elif args.command == "inspect":
        cmd_inspect(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
