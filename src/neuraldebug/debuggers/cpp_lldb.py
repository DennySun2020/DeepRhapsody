"""LLDB backend for C/C++ debugging (MI protocol)."""

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


class LldbDebugger(DebugResponseMixin):

    def __init__(self, executable: str, debugger_path: str = "lldb",
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
        self.proc: Optional[subprocess.Popen] = None

        self._lock = threading.Lock()
        self._output_buffer: List[str] = []
        self._output_event = threading.Event()
        self._program_output: List[str] = []
        self._last_out_pos = 0

        self.is_started = False
        self.is_finished = False
        self.is_paused = False
        self._prompt_re = re.compile(r'\(lldb\)\s*$')

    def start_gdb(self):
        if self.attach_pid:
            cmd = [self.debugger_path, '--no-use-colors',
                   '-p', str(self.attach_pid)]
        elif self.core_dump:
            cmd = [self.debugger_path, '--no-use-colors',
                   '-c', self.core_dump]
            if self.executable:
                cmd.extend(['-f', self.executable])
        else:
            cmd = [self.debugger_path, '--no-use-colors', self.executable]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        time.sleep(0.5)
        if self.proc.poll() is not None:
            rc = self.proc.returncode
            raise RuntimeError(
                f"Debugger exited immediately (code {rc}). "
                f"{self.debugger_path} may have missing DLLs or be incompatible."
            )
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        self._wait_prompt(timeout=10)
        for sp in self.source_paths:
            self._send(f'settings append target.source-map . {sp}')
            self._collect_output(timeout=2)
        if self.source_dir:
            self._send(f'settings append target.source-map . {self.source_dir}')
            self._collect_output(timeout=2)
        if self.program_args and not self.attach_pid and not self.core_dump:
            self._send(f'settings set target.run-args {self.program_args}')
            self._collect_output(timeout=2)

    def _read_output(self):
        try:
            for raw_line in self.proc.stdout:
                line = raw_line.decode('utf-8', errors='replace').rstrip('\r\n')
                with self._lock:
                    self._output_buffer.append(line)
                self._output_event.set()
        except (OSError, ValueError):
            pass

    def _send(self, command: str):
        try:
            self.proc.stdin.write((command + '\n').encode('utf-8'))
            self.proc.stdin.flush()
        except (OSError, BrokenPipeError):
            self.is_finished = True

    def _collect_output(self, timeout: float = 10.0) -> str:
        deadline = time.time() + timeout
        lines = []
        while time.time() < deadline:
            self._output_event.wait(timeout=0.3)
            self._output_event.clear()
            with self._lock:
                buf = list(self._output_buffer)
                self._output_buffer.clear()
            for line in buf:
                lines.append(line)
            # Check if last line has prompt
            text = '\n'.join(lines)
            if self._prompt_re.search(text):
                # Remove the prompt from output
                cleaned = re.sub(r'\(lldb\)\s*$', '', text).strip()
                return cleaned
            # Small delay to accumulate
            time.sleep(0.1)
        return '\n'.join(lines)

    def _wait_prompt(self, timeout: float = 10.0):
        self._collect_output(timeout=timeout)

    def _get_new_stdout(self) -> str:
        items = self._program_output[self._last_out_pos:]
        self._last_out_pos = len(self._program_output)
        return '\n'.join(items)[:5000]

    def _parse_frame_line(self, text: str) -> Optional[dict]:
        """Parse a lldb frame line like:
        * frame #0: 0x... prog`main at main.c:10:5
        """
        m = re.search(r'frame #(\d+).*?at\s+(\S+):(\d+)', text)
        if m:
            return {
                'frame_index': int(m.group(1)),
                'file': m.group(2),
                'line': int(m.group(3)),
                'function': '',
            }
        m = re.search(r'frame #(\d+).*?`(\w+)', text)
        if m:
            return {
                'frame_index': int(m.group(1)),
                'file': '',
                'line': 0,
                'function': m.group(2),
            }
        return None

    def _parse_stop_output(self, text: str) -> dict:
        loc = {"file": "", "line": 0, "function": "", "code_context": ""}

        # Look for "at file:line"
        m = re.search(r'at\s+(\S+):(\d+)', text)
        if m:
            loc['file'] = m.group(1)
            loc['line'] = int(m.group(2))

        # Look for function name (after backtick)
        m = re.search(r'`(\w+)', text)
        if m:
            loc['function'] = m.group(1)

        # Look for source line (indented, often after the frame info)
        source_lines = text.strip().split('\n')
        for sl in source_lines:
            stripped = sl.strip()
            if stripped and re.match(r'^\d+\s', stripped):
                # Source line like "10   int x = 5;"
                loc['code_context'] = re.sub(r'^\d+\s+', '', stripped)
                break

        return loc


    def cmd_start(self, args_str: str = "") -> dict:
        if self.is_started:
            return self._error("Program already started.")
        self.is_started = True

        if args_str:
            self._send(f'settings set target.run-args {args_str}')
            self._collect_output(timeout=3)

        self._send('run')
        output = self._collect_output(timeout=30)
        self.is_paused = True

        loc = self._parse_stop_output(output)
        msg = self._format_stop_message(output)

        return {
            "status": "paused", "command": "start", "message": msg,
            "current_location": loc, "call_stack": self._get_bt(),
            "local_variables": self._get_locals(),
            "stdout_new": self._get_new_stdout(), "stderr_new": "",
        }

    def cmd_continue(self) -> dict:
        self._send('continue')
        output = self._collect_output(timeout=60)
        if 'exited' in output.lower():
            self.is_finished = True
            return self._completed("Program finished.", stdout=output)
        self.is_paused = True
        loc = self._parse_stop_output(output)
        return {
            "status": "paused", "command": "continue",
            "message": self._format_stop_message(output),
            "current_location": loc, "call_stack": self._get_bt(),
            "local_variables": self._get_locals(),
            "stdout_new": self._get_new_stdout(), "stderr_new": "",
        }

    def cmd_step_in(self) -> dict:
        self._send('step')
        output = self._collect_output(timeout=30)
        loc = self._parse_stop_output(output)
        return {
            "status": "paused", "command": "step_in",
            "message": self._format_stop_message(output),
            "current_location": loc, "call_stack": self._get_bt(),
            "local_variables": self._get_locals(),
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_step_over(self) -> dict:
        self._send('next')
        output = self._collect_output(timeout=30)
        loc = self._parse_stop_output(output)
        return {
            "status": "paused", "command": "step_over",
            "message": self._format_stop_message(output),
            "current_location": loc, "call_stack": self._get_bt(),
            "local_variables": self._get_locals(),
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_step_out(self) -> dict:
        self._send('finish')
        output = self._collect_output(timeout=30)
        loc = self._parse_stop_output(output)
        return {
            "status": "paused", "command": "step_out",
            "message": self._format_stop_message(output),
            "current_location": loc, "call_stack": self._get_bt(),
            "local_variables": self._get_locals(),
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_run_to_line(self, line: int) -> dict:
        self._send(f'breakpoint set --one-shot true --line {line}')
        self._collect_output(timeout=3)
        self._send('continue')
        output = self._collect_output(timeout=60)
        loc = self._parse_stop_output(output)
        return {
            "status": "paused", "command": "run_to_line",
            "message": self._format_stop_message(output),
            "current_location": loc, "call_stack": self._get_bt(),
            "local_variables": self._get_locals(),
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_set_breakpoint(self, args: str) -> dict:
        parts = args.strip().split(None, 1)
        if not parts:
            return self._error("Usage: b <line> or b <file>:<line>")
        location = parts[0]
        condition = parts[1] if len(parts) > 1 else None

        try:
            line = int(location)
            bp_cmd = f'breakpoint set --line {line}'
        except ValueError:
            if ':' in location:
                f, l = location.rsplit(':', 1)
                bp_cmd = f'breakpoint set --file {f} --line {l}'
            else:
                bp_cmd = f'breakpoint set --name {location}'

        if condition:
            bp_cmd += f' --condition "{condition}"'

        self._send(bp_cmd)
        output = self._collect_output(timeout=5)
        return {
            "status": "paused" if self.is_paused else "running",
            "command": f"set_breakpoint {args}",
            "message": output or f"Breakpoint set: {args}",
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_remove_breakpoint(self, args: str) -> dict:
        self._send(f'breakpoint delete {args.strip()}')
        output = self._collect_output(timeout=3)
        return {
            "status": "paused" if self.is_paused else "running",
            "command": f"remove_breakpoint {args}",
            "message": output or f"Breakpoint {args} deleted",
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_list_breakpoints(self) -> dict:
        self._send('breakpoint list')
        output = self._collect_output(timeout=5)
        return {
            "status": "paused" if self.is_paused else "running",
            "command": "breakpoints",
            "message": output or "No breakpoints.",
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_inspect(self) -> dict:
        return {
            "status": "paused", "command": "inspect", "message": "Current state",
            "current_location": self._get_frame_info(),
            "call_stack": self._get_bt(),
            "local_variables": self._get_locals(),
            "stdout_new": self._get_new_stdout(), "stderr_new": "",
        }

    def cmd_evaluate(self, expr: str) -> dict:
        self._send(f'expression -- {expr.strip()}')
        output = self._collect_output(timeout=10)
        return {
            "status": "paused", "command": "evaluate",
            "message": f"{expr.strip()} = {output}",
            "eval_result": {"type": "expression", "value": output, "repr": output},
            "current_location": self._get_frame_info(),
            "call_stack": [], "local_variables": {},
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_list_source(self, args: str) -> dict:
        self._send('source list')
        output = self._collect_output(timeout=5)
        return {
            "status": "paused", "command": "list",
            "message": output, "current_location": self._get_frame_info(),
            "call_stack": [], "local_variables": {},
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_backtrace(self) -> dict:
        stack = self._get_bt()
        return {
            "status": "paused", "command": "backtrace",
            "message": f"Call stack ({len(stack)} frames)",
            "current_location": self._get_frame_info(),
            "call_stack": stack, "local_variables": {},
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_quit(self) -> dict:
        try:
            self._send('quit')
            self.proc.stdin.close()
        except Exception:
            pass
        self.is_finished = True
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            pass
        return self._completed("Debug session terminated.")

    def _get_frame_info(self) -> Optional[dict]:
        self._send('frame info')
        output = self._collect_output(timeout=3)
        return self._parse_stop_output(output) if output else None

    def _get_bt(self) -> List[dict]:
        self._send('bt')
        output = self._collect_output(timeout=5)
        frames = []
        for line in output.split('\n'):
            f = self._parse_frame_line(line)
            if f:
                frames.append(f)
        return frames

    def _get_locals(self) -> dict:
        self._send('frame variable')
        output = self._collect_output(timeout=5)
        result = {}
        for line in output.split('\n'):
            line = line.strip()
            if not line:
                continue
            # Pattern: "(type) name = value"
            m = re.match(r'\(([^)]+)\)\s+(\w+)\s*=\s*(.*)', line)
            if m:
                result[m.group(2)] = {
                    "type": m.group(1),
                    "value": m.group(3),
                    "repr": m.group(3),
                }
        return result

    def _format_stop_message(self, output: str) -> str:
        lines = [l.strip() for l in output.strip().split('\n') if l.strip()]
        return '\n'.join(lines[:5]) if lines else "Stopped."
