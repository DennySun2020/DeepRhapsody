"""GDB backend for C/C++ debugging (MI protocol)."""

import os
import re
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from debug_common import (
    MiDebuggerBase, GdbMiParser,
    DebugResponseMixin, error_response, completed_response,
)


class GdbDebugger(MiDebuggerBase):

    def __init__(self, executable: str, debugger_path: str = "gdb",
                 source_paths: Optional[List[str]] = None,
                 attach_pid: Optional[int] = None,
                 core_dump: Optional[str] = None,
                 program_args: Optional[str] = None):
        self.executable = os.path.abspath(executable) if executable else ''
        self.debugger_path = debugger_path
        self.source_dir = os.path.dirname(self.executable) or os.getcwd()
        self.source_paths = source_paths or []
        self.attach_pid = attach_pid
        self.core_dump = core_dump
        self.program_args = program_args
        self._init_mi()

    def start_gdb(self):
        if self.attach_pid:
            cmd = [self.debugger_path, "--interpreter=mi", "--quiet",
                   "-p", str(self.attach_pid)]
        elif self.core_dump:
            cmd = [self.debugger_path, "--interpreter=mi", "--quiet",
                   self.executable, self.core_dump]
        else:
            cmd = [self.debugger_path, "--interpreter=mi", "--quiet",
                   self.executable]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        time.sleep(0.5)
        if self.proc.poll() is not None:
            rc = self.proc.returncode
            raise RuntimeError(
                f"Debugger exited immediately (code {rc}). "
                f"{self.debugger_path} may have missing DLLs or be incompatible."
            )
        self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()
        self._wait_for_prompt(timeout=10)
        for sp in self.source_paths:
            self._send_mi(f'-environment-directory {sp}')
        if self.source_dir:
            self._send_mi(f'-environment-directory {self.source_dir}')
        if self.program_args and not self.attach_pid and not self.core_dump:
            self._send_mi(f'-exec-arguments {self.program_args}')


    def cmd_start(self, args_str: str = "") -> dict:
        if self.is_started:
            return self._error("Program already started. Use 'continue' instead.")
        self.is_started = True

        if args_str:
            tok = self._send_mi(f'-exec-arguments {args_str}')
            self._collect_until_result(tok, timeout=5)

        # Run
        tok = self._send_mi('-exec-run')
        result, others = self._collect_until_result(tok, timeout=10)

        # Wait for the stop event (breakpoint, signal, or finish)
        stop, extras = self._collect_stop_event(timeout=30)
        if stop:
            return self._build_stop_response("start", stop)
        elif result and result.get('class_') == 'error':
            msg = result.get('body', {}).get('msg', 'Unknown error')
            return self._error(f"Failed to start: {msg}")
        else:
            self.is_finished = True
            return self._completed("Program finished without stopping.",
                                   stdout=self._get_new_stdout())

    def cmd_continue(self) -> dict:
        tok = self._send_mi('-exec-continue')
        result, _ = self._collect_until_result(tok, timeout=5)
        stop, _ = self._collect_stop_event(timeout=60)
        if stop:
            return self._build_stop_response("continue", stop)
        self.is_finished = True
        return self._completed("Program finished.", stdout=self._get_new_stdout())

    def cmd_step_in(self) -> dict:
        tok = self._send_mi('-exec-step')
        result, _ = self._collect_until_result(tok, timeout=5)
        stop, _ = self._collect_stop_event(timeout=30)
        if stop:
            return self._build_stop_response("step_in", stop)
        return self._error("No stop event after step_in")

    def cmd_step_over(self) -> dict:
        tok = self._send_mi('-exec-next')
        result, _ = self._collect_until_result(tok, timeout=5)
        stop, _ = self._collect_stop_event(timeout=30)
        if stop:
            return self._build_stop_response("step_over", stop)
        return self._error("No stop event after step_over")

    def cmd_step_out(self) -> dict:
        tok = self._send_mi('-exec-finish')
        result, _ = self._collect_until_result(tok, timeout=5)
        stop, _ = self._collect_stop_event(timeout=30)
        if stop:
            return self._build_stop_response("step_out", stop)
        return self._error("No stop event after step_out")

    def cmd_run_to_line(self, line: int) -> dict:
        # Use temporary breakpoint + continue
        tok = self._send_mi(f'-break-insert -t {self.executable}:{line}')
        result, _ = self._collect_until_result(tok, timeout=5)
        if result and result.get('class_') == 'error':
            msg = result.get('body', {}).get('msg', 'Failed to set temp breakpoint')
            return self._error(msg)

        tok = self._send_mi('-exec-continue')
        self._collect_until_result(tok, timeout=5)
        stop, _ = self._collect_stop_event(timeout=60)
        if stop:
            return self._build_stop_response("run_to_line", stop)
        return self._error("Did not reach target line")

    def cmd_set_breakpoint(self, args: str) -> dict:
        """Set a breakpoint. Args: '<line>' or '<file>:<line>' or '<func>' [condition]"""
        parts = args.strip().split(None, 1)
        if not parts:
            return self._error("Usage: b <line> or b <file>:<line> or b <func> [condition]")

        location = parts[0]
        condition = parts[1] if len(parts) > 1 else None

        # If it's just a number, prepend the executable source path
        try:
            line_num = int(location)
            # Try to find the main source file
            location = f"{line_num}"
        except ValueError:
            pass  # It's already a file:line or function name

        mi_cmd = f'-break-insert {location}'
        tok = self._send_mi(mi_cmd)
        result, _ = self._collect_until_result(tok, timeout=5)

        if result and result.get('class_') == 'error':
            msg = result.get('body', {}).get('msg', 'Failed to set breakpoint')
            return self._error(f"Failed to set breakpoint: {msg}")

        bkpt = result.get('body', {}).get('bkpt', {}) if result else {}
        bp_num = bkpt.get('number', '?')
        bp_file = bkpt.get('file', bkpt.get('fullname', '?'))
        bp_line = bkpt.get('line', '?')
        bp_func = bkpt.get('func', '')

        # Set condition if provided
        if condition and bp_num != '?':
            cond_tok = self._send_mi(f'-break-condition {bp_num} {condition}')
            cond_result, _ = self._collect_until_result(cond_tok, timeout=5)
            if cond_result and cond_result.get('class_') == 'error':
                msg = cond_result.get('body', {}).get('msg', '')
                return self._error(f"Breakpoint set at {bp_file}:{bp_line} but "
                                   f"condition failed: {msg}")

        msg = f"Breakpoint {bp_num} set at {bp_file}:{bp_line}"
        if bp_func:
            msg += f" in {bp_func}()"
        if condition:
            msg += f" (condition: {condition})"

        return {
            "status": "running" if self.is_started and not self.is_paused else "paused",
            "command": f"set_breakpoint {args}",
            "message": msg,
            "current_location": None,
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    def cmd_remove_breakpoint(self, args: str) -> dict:
        bp_id = args.strip()
        if not bp_id:
            return self._error("Usage: rb <breakpoint_number>")

        # Try as breakpoint number first
        tok = self._send_mi(f'-break-delete {bp_id}')
        result, _ = self._collect_until_result(tok, timeout=5)
        if result and result.get('class_') == 'error':
            msg = result.get('body', {}).get('msg', 'Failed to delete breakpoint')
            return self._error(msg)

        return {
            "status": "paused" if self.is_paused else "running",
            "command": f"remove_breakpoint {bp_id}",
            "message": f"Breakpoint {bp_id} deleted",
            "current_location": None,
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    def cmd_list_breakpoints(self) -> dict:
        tok = self._send_mi('-break-list')
        result, _ = self._collect_until_result(tok, timeout=5)

        bps = []
        if result:
            body = result.get('body', {})
            bp_table = body.get('BreakpointTable', body.get('body', {}))
            if isinstance(bp_table, dict):
                bp_list = bp_table.get('body', [])
                if isinstance(bp_list, list):
                    for item in bp_list:
                        if isinstance(item, dict):
                            bkpt = item.get('bkpt', item)
                            bps.append({
                                'number': bkpt.get('number', '?'),
                                'type': bkpt.get('type', '?'),
                                'enabled': bkpt.get('enabled', '?'),
                                'file': bkpt.get('file', bkpt.get('fullname', '?')),
                                'line': bkpt.get('line', '?'),
                                'function': bkpt.get('func', ''),
                                'condition': bkpt.get('cond', ''),
                                'hits': bkpt.get('times', '0'),
                            })

        if bps:
            lines = []
            for bp in bps:
                line = f"  #{bp['number']}: {bp['file']}:{bp['line']}"
                if bp['function']:
                    line += f" ({bp['function']})"
                if bp['condition']:
                    line += f" [if {bp['condition']}]"
                line += f" hits={bp['hits']}"
                lines.append(line)
            msg = f"{len(bps)} breakpoint(s):\n" + "\n".join(lines)
        else:
            msg = "No breakpoints set."

        return {
            "status": "paused" if self.is_paused else "running",
            "command": "breakpoints",
            "message": msg,
            "current_location": None,
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    def cmd_inspect(self) -> dict:
        stack = self._get_call_stack()
        locals_ = self._get_locals()
        loc = self._get_current_location()

        return {
            "status": "paused",
            "command": "inspect",
            "message": "Current state",
            "current_location": loc,
            "call_stack": stack,
            "local_variables": locals_,
            "stdout_new": self._get_new_stdout(),
            "stderr_new": "",
        }

    def cmd_evaluate(self, expr: str) -> dict:
        if not expr.strip():
            return self._error("Usage: e <expression>")

        tok = self._send_mi(f'-data-evaluate-expression {expr.strip()}')
        result, _ = self._collect_until_result(tok, timeout=10)

        if result and result.get('class_') == 'done':
            value = result.get('body', {}).get('value', '<no value>')
            return {
                "status": "paused",
                "command": f"evaluate",
                "message": f"{expr.strip()} = {value}",
                "eval_result": {"type": "expression", "value": str(value), "repr": str(value)},
                "current_location": self._get_current_location(),
                "call_stack": [],
                "local_variables": {},
                "stdout_new": "",
                "stderr_new": "",
            }
        elif result and result.get('class_') == 'error':
            msg = result.get('body', {}).get('msg', 'Evaluation failed')
            return self._error(f"Evaluation error: {msg}")
        return self._error("Timeout evaluating expression")

    def cmd_list_source(self, args: str) -> dict:
        context = 5
        if args.strip():
            try:
                context = int(args.strip())
            except ValueError:
                pass

        tok = self._send_mi(f'-data-disassemble -s $pc -e "$pc+1" -- 0')
        # Actually, use the 'list' command via -interpreter-exec
        tok = self._send_mi(f'-interpreter-exec console "list"')
        result, others = self._collect_until_result(tok, timeout=5)

        # Collect console output lines
        source_lines = []
        for rec in others:
            if rec.get('type') == 'console':
                source_lines.append(rec.get('body', ''))

        # Also check pending for console records that arrived with the result
        with self._lock:
            for rec in self._pending_records:
                if rec.get('type') == 'console':
                    source_lines.append(rec.get('body', ''))

        if source_lines:
            source_text = ''.join(source_lines)
        else:
            source_text = "(no source available)"

        return {
            "status": "paused",
            "command": "list",
            "message": source_text,
            "current_location": self._get_current_location(),
            "call_stack": [],
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    def cmd_backtrace(self) -> dict:
        stack = self._get_call_stack()
        if stack:
            lines = []
            for f in stack:
                lines.append(f"  #{f['frame_index']}: {f['function']} at {f['file']}:{f['line']}")
                if f.get('code_context'):
                    lines.append(f"       {f['code_context']}")
            msg = f"Call stack ({len(stack)} frames):\n" + "\n".join(lines)
        else:
            msg = "No call stack available."

        return {
            "status": "paused",
            "command": "backtrace",
            "message": msg,
            "current_location": self._get_current_location(),
            "call_stack": stack,
            "local_variables": {},
            "stdout_new": "",
            "stderr_new": "",
        }

    def cmd_quit(self) -> dict:
        try:
            self._send_mi('-gdb-exit')
        except Exception:
            pass
        self.is_finished = True
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        return self._completed("Debug session terminated.")


    def _get_current_location(self) -> Optional[dict]:
        tok = self._send_mi('-stack-info-frame')
        result, _ = self._collect_until_result(tok, timeout=5)
        if not result or result.get('class_') != 'done':
            return None

        frame = result.get('body', {}).get('frame', {})
        filename = frame.get('file', frame.get('fullname', ''))
        line = frame.get('line', '')
        func = frame.get('func', '')

        # Try to read source line
        code_context = ""
        if filename and line:
            try:
                line_num = int(line)
                src_path = self._resolve_source(filename)
                if src_path and os.path.isfile(src_path):
                    with open(src_path, 'r', errors='replace') as f:
                        for i, src_line in enumerate(f, 1):
                            if i == line_num:
                                code_context = src_line.rstrip()
                                break
            except (ValueError, OSError):
                pass

        return {
            "file": self._rel_path(filename),
            "line": int(line) if line else 0,
            "function": func,
            "code_context": code_context,
        }

    def _get_call_stack(self) -> List[dict]:
        tok = self._send_mi('-stack-list-frames')
        result, _ = self._collect_until_result(tok, timeout=5)
        if not result or result.get('class_') != 'done':
            return []

        frames_data = result.get('body', {}).get('stack', [])
        stack = []
        for item in frames_data:
            if isinstance(item, dict):
                frame = item.get('frame', item)
            else:
                continue
            filename = frame.get('file', frame.get('fullname', ''))
            line = frame.get('line', '')
            func = frame.get('func', '<unknown>')

            code_context = ""
            if filename and line:
                try:
                    line_num = int(line)
                    src_path = self._resolve_source(filename)
                    if src_path and os.path.isfile(src_path):
                        with open(src_path, 'r', errors='replace') as f:
                            for i, src_line in enumerate(f, 1):
                                if i == line_num:
                                    code_context = src_line.rstrip()
                                    break
                except (ValueError, OSError):
                    pass

            stack.append({
                "frame_index": int(frame.get('level', len(stack))),
                "file": self._rel_path(filename),
                "line": int(line) if line else 0,
                "function": func,
                "code_context": code_context,
            })

        return stack

    def _get_locals(self) -> dict:
        result_dict = {}

        # Get arguments
        tok = self._send_mi('-stack-list-arguments 1 0 0')
        result, _ = self._collect_until_result(tok, timeout=5)
        if result and result.get('class_') == 'done':
            args_frames = result.get('body', {}).get('stack-args', [])
            if isinstance(args_frames, list):
                for item in args_frames:
                    frame_args = item.get('frame', item) if isinstance(item, dict) else {}
                    if isinstance(frame_args, dict):
                        for arg in frame_args.get('args', []):
                            if isinstance(arg, dict):
                                name = arg.get('name', '')
                                value = arg.get('value', '')
                                if name:
                                    result_dict[name] = {
                                        "type": "arg",
                                        "value": str(value),
                                        "repr": str(value),
                                    }

        # Get local variables
        tok = self._send_mi('-stack-list-locals 1')
        result, _ = self._collect_until_result(tok, timeout=5)
        if result and result.get('class_') == 'done':
            locals_list = result.get('body', {}).get('locals', [])
            if isinstance(locals_list, list):
                for item in locals_list:
                    if isinstance(item, dict):
                        name = item.get('name', '')
                        value = item.get('value', '')
                        if name:
                            result_dict[name] = {
                                "type": "local",
                                "value": str(value),
                                "repr": str(value),
                            }

        return result_dict

    def _build_stop_response(self, command: str, stop_event: dict) -> dict:
        """Build a JSON response from a GDB *stopped event."""
        body = stop_event.get('body', {})
        reason = body.get('reason', 'unknown')

        frame = body.get('frame', {})
        filename = frame.get('file', frame.get('fullname', ''))
        line = frame.get('line', '')
        func = frame.get('func', '')

        # Build human message
        if reason == 'breakpoint-hit':
            bp_num = body.get('bkptno', '?')
            msg = f"Breakpoint {bp_num} hit at {self._rel_path(filename)}:{line}"
            if func:
                msg += f" in {func}()"
        elif reason == 'end-stepping-range':
            msg = f"Stepped to {self._rel_path(filename)}:{line}"
            if func:
                msg += f" in {func}()"
        elif reason == 'function-finished':
            rv = body.get('return-value', body.get('gdb-result-var', ''))
            msg = f"Returned from function"
            if func:
                msg += f" to {func}()"
            if rv:
                msg += f", return value = {rv}"
        elif reason == 'exited-normally':
            self.is_finished = True
            return self._completed("Program exited normally.",
                                   stdout=self._get_new_stdout())
        elif reason == 'exited':
            exit_code = body.get('exit-code', '?')
            self.is_finished = True
            return self._completed(f"Program exited with code {exit_code}.",
                                   stdout=self._get_new_stdout())
        elif 'signal' in reason:
            sig_name = body.get('signal-name', '')
            sig_meaning = body.get('signal-meaning', '')
            msg = f"Signal received: {sig_name} ({sig_meaning})"
            if func:
                msg += f" at {func}()"
        else:
            msg = f"Stopped: {reason}"
            if func:
                msg += f" at {func}()"

        self.is_paused = True

        # Get full state
        code_context = ""
        if filename and line:
            try:
                src_path = self._resolve_source(filename)
                if src_path and os.path.isfile(src_path):
                    line_num = int(line)
                    with open(src_path, 'r', errors='replace') as f:
                        for i, src_line in enumerate(f, 1):
                            if i == line_num:
                                code_context = src_line.rstrip()
                                break
            except (ValueError, OSError):
                pass

        location = {
            "file": self._rel_path(filename),
            "line": int(line) if line else 0,
            "function": func,
            "code_context": code_context,
        }

        stack = self._get_call_stack()
        locals_ = self._get_locals()

        return {
            "status": "paused",
            "command": command,
            "message": msg,
            "current_location": location,
            "call_stack": stack,
            "local_variables": locals_,
            "stdout_new": self._get_new_stdout(),
            "stderr_new": "",
        }

    def _resolve_source(self, filename: str) -> Optional[str]:
        if os.path.isfile(filename):
            return filename
        candidate = os.path.join(self.source_dir, filename)
        if os.path.isfile(candidate):
            return candidate
        return None

    def _rel_path(self, filename: str) -> str:
        if not filename:
            return "<unknown>"
        try:
            return os.path.relpath(filename, self.source_dir)
        except ValueError:
            return filename
