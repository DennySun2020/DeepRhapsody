"""CDB (Windows Debugging Tools) backend for C/C++ debugging."""

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


class CdbDebugger(DebugResponseMixin):
    """Drives CDB (Windows Console Debugger) via subprocess.

    CDB is the command-line debugger from the Windows SDK / Debugging
    Tools for Windows.  It natively reads PDB symbols produced by MSVC
    and uses the Win32 debugging API -- the same engine as WinDbg and
    the Visual Studio debugger.

    Command mapping:
        bp  = breakpoint            g   = go / continue
        p   = step over             t   = step into (trace)
        gu  = step out (go up)      kn  = call stack (with frame #)
        dv  = display locals         ?? = evaluate C++ expression
        lsp = list source lines     q   = quit
    """

    def __init__(self, executable: str, debugger_path: str = "cdb",
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

        # CDB prompt looks like  0:000>  (thread:frame>)
        self._prompt_re = re.compile(r'\d+:\d+>\s*$')

    def start_gdb(self):
        """Launch CDB.  (Method named start_gdb for interface compat.)"""
        if self.attach_pid:
            cmd = [self.debugger_path, '-lines', '-p', str(self.attach_pid)]
        elif self.core_dump:
            cmd = [self.debugger_path, '-lines', '-z', self.core_dump]
        else:
            cmd = [self.debugger_path, '-lines', '-o', self.executable]
            # CDB takes program args after the executable on the command line
            if self.program_args:
                import shlex
                try:
                    cmd.extend(shlex.split(self.program_args))
                except ValueError:
                    cmd.append(self.program_args)
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
        # Health check
        time.sleep(0.5)
        if self.proc.poll() is not None:
            rc = self.proc.returncode
            raise RuntimeError(
                f"Debugger exited immediately (code {rc}). "
                f"{self.debugger_path} may be missing or broken."
            )
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        # CDB breaks at the initial system breakpoint -- wait for prompt
        self._wait_prompt(timeout=15)
        # Enable source-line loading and set source path
        self._send('.lines -e')
        self._collect_output(timeout=3)
        self._send(f'.srcpath+ {self.source_dir}')
        self._collect_output(timeout=3)
        # Apply additional source path mappings
        for sp in self.source_paths:
            self._send(f'.srcpath+ {sp}')
            self._collect_output(timeout=3)
        # Force-load symbols for the target module so that source-line
        # breakpoints (bp `file:line`) can resolve immediately.  CDB
        # defers symbol loading by default; without this, bp commands
        # return "could not be resolved" until the module is actually hit.
        if self.executable:
            mod_name = os.path.splitext(os.path.basename(self.executable))[0]
            self._send(f'ld {mod_name}')
            self._collect_output(timeout=10)


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
        lines: List[str] = []
        while time.time() < deadline:
            self._output_event.wait(timeout=0.3)
            self._output_event.clear()
            with self._lock:
                buf = list(self._output_buffer)
                self._output_buffer.clear()
            lines.extend(buf)
            text = '\n'.join(lines)
            if self._prompt_re.search(text):
                cleaned = self._prompt_re.sub('', text).strip()
                return cleaned
            time.sleep(0.1)
        return '\n'.join(lines)

    def _wait_prompt(self, timeout: float = 10.0):
        self._collect_output(timeout=timeout)

    def _get_new_stdout(self) -> str:
        items = self._program_output[self._last_out_pos:]
        self._last_out_pos = len(self._program_output)
        return '\n'.join(items)[:5000]


    def _parse_location(self, text: str) -> dict:
        """Parse CDB output to extract current source location.

        CDB prints locations in several patterns:
          module!function+0x1a [c:\\path\\file.c @ 42]
          >   42: int x = 5;
        """
        loc = {"file": "", "line": 0, "function": "", "code_context": ""}

        # [file @ line] pattern (stack / breakpoint hit)
        m = re.search(r'\[(.+?)\s*@\s*(\d+)\]', text)
        if m:
            loc['file'] = m.group(1).strip()
            loc['line'] = int(m.group(2))

        # file(line) pattern sometimes used by CDB
        if not loc['line']:
            m = re.search(
                r'(\S+\.(?:c|cpp|cc|cxx|h|hpp))\((\d+)\)', text, re.I,
            )
            if m:
                loc['file'] = m.group(1)
                loc['line'] = int(m.group(2))

        # Function: module!function+offset
        m = re.search(r'(\w+)!(\w+)', text)
        if m:
            loc['function'] = m.group(2)

        # Source line: look for ">  NN: code" or "NN: code"
        for sl in text.split('\n'):
            stripped = sl.strip()
            m2 = re.match(r'>?\s*(\d+)[\s:]+(.+)', stripped)
            if m2:
                loc['code_context'] = m2.group(2).strip()
                if not loc['line']:
                    loc['line'] = int(m2.group(1))
                break

        if loc['file']:
            loc['file'] = self._rel_path(loc['file'])
        return loc

    def _parse_stack(self, text: str) -> List[dict]:
        """Parse CDB 'kn' stack output.

        Typical format:
         # Child-SP          RetAddr       Call Site
        00 000000ab`1234ff78 00007ff6`1234 sample!main+0x1a [file.c @ 42]
        01 000000ab`1234ff80 00007ffa`5678 KERNEL32!BaseThreadInitThunk+0x14
        """
        frames = []
        for line in text.split('\n'):
            line = line.strip()
            m = re.match(r'^([0-9a-fA-F]+)\s+', line)
            if not m:
                continue
            frame_idx = int(m.group(1), 16)

            func = ''
            fm = re.search(r'(\w+)!(\w+)', line)
            if fm:
                func = fm.group(2)

            file_ = ''
            line_no = 0
            loc_m = re.search(r'\[(.+?)\s*@\s*(\d+)\]', line)
            if loc_m:
                file_ = loc_m.group(1).strip()
                line_no = int(loc_m.group(2))

            code_context = ""
            if file_ and line_no:
                try:
                    src_path = self._resolve_source(file_)
                    if src_path and os.path.isfile(src_path):
                        with open(src_path, 'r', errors='replace') as f:
                            for i, src_line in enumerate(f, 1):
                                if i == line_no:
                                    code_context = src_line.rstrip()
                                    break
                except (ValueError, OSError):
                    pass

            frames.append({
                "frame_index": frame_idx,
                "file": self._rel_path(file_) if file_ else '',
                "line": line_no,
                "function": func,
                "code_context": code_context,
            })
        return frames

    def _parse_locals(self, text: str) -> dict:
        """Parse CDB 'dv /t' output.

        Typical format:
          int x = 5
          char * name = 0x00007ff6`12345678 "hello"
        """
        result = {}
        for line in text.split('\n'):
            line = line.strip()
            if not line or line.startswith('Unable') or '=' not in line:
                continue
            m = re.match(r'(?:(.+?)\s+)?(\w+)\s*=\s*(.*)', line)
            if m:
                type_ = (m.group(1) or '').strip()
                name = m.group(2)
                value = m.group(3).strip()
                result[name] = {
                    "type": type_ or "unknown",
                    "value": value,
                    "repr": value,
                }
        return result


    def cmd_start(self, args_str: str = "") -> dict:
        if self.is_started:
            return self._error("Program already started.")
        self.is_started = True

        # CDB starts at the system breakpoint.  'g' runs to the first
        # user breakpoint, or until the program exits.
        self._send('g')
        output = self._collect_output(timeout=30)

        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished.", stdout=output)

        self.is_paused = True
        loc = self._parse_location(output)
        return {
            "status": "paused", "command": "start",
            "message": self._format_stop_message(output),
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": self._get_new_stdout(), "stderr_new": "",
        }

    def cmd_continue(self) -> dict:
        self._send('g')
        output = self._collect_output(timeout=60)
        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished.", stdout=output)
        self.is_paused = True
        loc = self._parse_location(output)
        return {
            "status": "paused", "command": "continue",
            "message": self._format_stop_message(output),
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": self._get_new_stdout(), "stderr_new": "",
        }

    def cmd_step_in(self) -> dict:
        self._send('t')
        output = self._collect_output(timeout=30)
        loc = self._parse_location(output)
        return {
            "status": "paused", "command": "step_in",
            "message": self._format_stop_message(output),
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_step_over(self) -> dict:
        self._send('p')
        output = self._collect_output(timeout=30)
        loc = self._parse_location(output)
        return {
            "status": "paused", "command": "step_over",
            "message": self._format_stop_message(output),
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_step_out(self) -> dict:
        self._send('gu')
        output = self._collect_output(timeout=30)
        loc = self._parse_location(output)
        return {
            "status": "paused", "command": "step_out",
            "message": self._format_stop_message(output),
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_run_to_line(self, line: int) -> dict:
        src_file = self._guess_source_file()
        if src_file:
            full = self._resolve_source(src_file)
            # /1 = one-shot (auto-cleared after first hit)
            self._send(f'bp /1 `{full or src_file}:{line}`')
        else:
            self._send(f'bp /1 `:{line}`')
        self._collect_output(timeout=3)
        self._send('g')
        output = self._collect_output(timeout=60)
        loc = self._parse_location(output)
        return {
            "status": "paused", "command": "run_to_line",
            "message": self._format_stop_message(output),
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_set_breakpoint(self, args: str) -> dict:
        parts = args.strip().split(None, 1)
        if not parts:
            return self._error("Usage: b <line> or b <file>:<line> or b <func>")
        location = parts[0]
        condition = parts[1] if len(parts) > 1 else None

        # Use 'bp' for source-line breakpoints (module is already loaded
        # at the system breakpoint).  'bu' would wait for a future module
        # load event which never arrives for the main executable.
        # CDB needs full paths to match against PDB source references.
        try:
            line = int(location)
            src_file = self._guess_source_file()
            if src_file:
                full = self._resolve_source(src_file)
                bp_cmd = f'bp `{full or src_file}:{line}`'
            else:
                bp_cmd = f'bp `:{line}`'
        except ValueError:
            if ':' in location:
                f, l = location.rsplit(':', 1)
                full = self._resolve_source(f)
                bp_cmd = f'bp `{full or f}:{l}`'
            else:
                bp_cmd = f'bp {location}'

        self._send(bp_cmd)
        output = self._collect_output(timeout=5)

        # Optional condition (CDB syntax: bp /w "condition" address)
        # For simplicity we note conditional BPs in the message.
        msg = output or f"Breakpoint set: {args}"
        if condition:
            msg += f" (condition requested: {condition} -- "
            msg += "use 'bp /w' syntax in CDB for conditional breakpoints)"

        return {
            "status": "paused" if self.is_paused else "running",
            "command": f"set_breakpoint {args}",
            "message": msg,
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_remove_breakpoint(self, args: str) -> dict:
        bp_id = args.strip()
        self._send(f'bc {bp_id}')
        output = self._collect_output(timeout=3)
        return {
            "status": "paused" if self.is_paused else "running",
            "command": f"remove_breakpoint {bp_id}",
            "message": output or f"Breakpoint {bp_id} cleared",
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_list_breakpoints(self) -> dict:
        self._send('bl')
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
            "status": "paused", "command": "inspect",
            "message": "Current state",
            "current_location": self._get_current_location(),
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": self._get_new_stdout(), "stderr_new": "",
        }

    def cmd_evaluate(self, expr: str) -> dict:
        self._send(f'?? {expr.strip()}')
        output = self._collect_output(timeout=10)
        return {
            "status": "paused", "command": "evaluate",
            "message": f"{expr.strip()} = {output}",
            "eval_result": {
                "type": "expression", "value": output, "repr": output,
            },
            "current_location": self._get_current_location(),
            "call_stack": [], "local_variables": {},
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_list_source(self, args: str) -> dict:
        context = 5
        if args.strip():
            try:
                context = int(args.strip())
            except ValueError:
                pass
        self._send(f'lsp -a @$ip -l {context * 2}')
        output = self._collect_output(timeout=5)
        return {
            "status": "paused", "command": "list",
            "message": output or "(no source available)",
            "current_location": self._get_current_location(),
            "call_stack": [], "local_variables": {},
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_backtrace(self) -> dict:
        stack = self._get_call_stack()
        if stack:
            lines = []
            for f in stack:
                lines.append(
                    f"  #{f['frame_index']}: {f['function']} at "
                    f"{f['file']}:{f['line']}"
                )
            msg = f"Call stack ({len(stack)} frames):\n" + "\n".join(lines)
        else:
            msg = "No call stack available."
        return {
            "status": "paused", "command": "backtrace", "message": msg,
            "current_location": self._get_current_location(),
            "call_stack": stack, "local_variables": {},
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_quit(self) -> dict:
        try:
            self._send('q')
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


    def _get_current_location(self) -> Optional[dict]:
        self._send('.frame')
        output = self._collect_output(timeout=3)
        return self._parse_location(output) if output else None

    def _get_call_stack(self) -> List[dict]:
        self._send('kn')
        output = self._collect_output(timeout=5)
        return self._parse_stack(output)

    def _get_locals(self) -> dict:
        self._send('dv /t')
        output = self._collect_output(timeout=5)
        return self._parse_locals(output)

    def _guess_source_file(self) -> Optional[str]:
        loc = self._get_current_location()
        if loc and loc.get('file') and loc['file'] != '<unknown>':
            return loc['file']
        for f in os.listdir(self.source_dir):
            if os.path.splitext(f)[1].lower() in SOURCE_EXTENSIONS:
                return f
        return None

    def _is_exit(self, text: str) -> bool:
        return bool(re.search(
            r'exited with code|process.*exited|ntdll!NtTerminateProcess',
            text, re.I,
        ))

    def _resolve_source(self, filename: str) -> Optional[str]:
        if os.path.isfile(filename):
            return filename
        candidate = os.path.join(self.source_dir, os.path.basename(filename))
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

    def _format_stop_message(self, output: str) -> str:
        lines = [l.strip() for l in output.strip().split('\n') if l.strip()]
        return '\n'.join(lines[:8]) if lines else "Stopped."
