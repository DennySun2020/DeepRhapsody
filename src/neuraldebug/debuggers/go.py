"""Go debugging via Delve."""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from debug_common import (
    BaseDebugServer,
    DebugResponseMixin, error_response, completed_response,
    find_repo_root,
)


class GoToolchainInfo:

    def __init__(self):
        self.platform_info = self._detect_platform()
        self.go_info = self._detect_go()
        self.dlv_info = self._detect_delve()
        self.module_info = self._detect_module()

    @staticmethod
    def _detect_platform() -> dict:
        os_name_map = {
            'win32': 'Windows', 'linux': 'Linux',
            'darwin': 'macOS', 'freebsd': 'FreeBSD',
        }
        return {
            'os': sys.platform,
            'os_name': os_name_map.get(sys.platform, sys.platform),
            'arch': platform.machine(),
        }

    def _detect_go(self) -> Optional[dict]:
        go_path = shutil.which('go')
        if not go_path:
            return None

        version = ''
        try:
            r = subprocess.run(
                [go_path, 'version'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                version = r.stdout.strip()
        except Exception:
            pass

        gopath = ''
        try:
            r = subprocess.run(
                [go_path, 'env', 'GOPATH'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                gopath = r.stdout.strip()
        except Exception:
            pass

        gobin = ''
        try:
            r = subprocess.run(
                [go_path, 'env', 'GOBIN'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                gobin = r.stdout.strip()
        except Exception:
            pass

        return {
            'path': go_path,
            'version': version,
            'gopath': gopath,
            'gobin': gobin,
        }

    def _detect_delve(self) -> Optional[dict]:
        dlv = shutil.which('dlv')

        # Search additional locations if not in PATH
        if not dlv:
            candidates = self._find_dlv_candidates()
            for c in candidates:
                if os.path.isfile(c):
                    dlv = c
                    break

        if not dlv:
            return None

        version = ''
        try:
            r = subprocess.run(
                [dlv, 'version'],
                capture_output=True, text=True, timeout=10,
            )
            output = r.stdout or r.stderr
            if output:
                # Delve version output: "Delve Debugger\nVersion: 1.22.1\n..."
                for line in output.strip().split('\n'):
                    if line.strip().startswith('Version:'):
                        version = line.strip()
                        break
                if not version:
                    version = output.strip().split('\n')[0]
        except Exception:
            pass

        return {
            'path': dlv,
            'version': version,
        }

    def _find_dlv_candidates(self) -> List[str]:
        candidates = []
        exe = 'dlv.exe' if sys.platform == 'win32' else 'dlv'

        # GOBIN
        gobin = ''
        if self.go_info and self.go_info.get('gobin'):
            gobin = self.go_info['gobin']
            candidates.append(os.path.join(gobin, exe))

        # GOPATH/bin
        gopath = ''
        if self.go_info and self.go_info.get('gopath'):
            gopath = self.go_info['gopath']
            for p in gopath.split(os.pathsep):
                candidates.append(os.path.join(p, 'bin', exe))

        # Default GOPATH locations
        home = os.environ.get('HOME') or os.environ.get('USERPROFILE') or ''
        if home:
            candidates.append(os.path.join(home, 'go', 'bin', exe))

        if sys.platform == 'win32':
            appdata = os.environ.get('APPDATA', '')
            if appdata:
                candidates.append(os.path.join(appdata, 'go', 'bin', exe))
        else:
            candidates.append(f'/usr/local/go/bin/{exe}')

        return candidates

    def _detect_module(self) -> Optional[dict]:
        cwd = os.getcwd()
        result = {}

        # Walk up to find go.mod
        cur = cwd
        for _ in range(20):
            go_mod = os.path.join(cur, 'go.mod')
            if os.path.isfile(go_mod):
                result['go_mod'] = go_mod
                result['module_root'] = cur
                try:
                    with open(go_mod, 'r', errors='replace') as f:
                        for line in f:
                            m = re.match(r'^\s*module\s+(\S+)', line)
                            if m:
                                result['module_name'] = m.group(1)
                                break
                except OSError:
                    pass
                break
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent

        # Check for go.work
        cur = cwd
        for _ in range(20):
            go_work = os.path.join(cur, 'go.work')
            if os.path.isfile(go_work):
                result['go_work'] = go_work
                result['workspace_root'] = cur
                break
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent

        return result if result else None

    def recommend(self) -> dict:
        if self.dlv_info:
            return {
                'debugger': {
                    'name': 'dlv',
                    'path': self.dlv_info['path'],
                },
                'note': 'Delve (dlv) — standard Go debugger',
            }

        return {
            'debugger': None,
            'note': self._install_instructions(),
        }

    def _install_instructions(self) -> str:
        msg = 'Delve (dlv) not found. Install with:\n'
        if self.go_info:
            msg += '  go install github.com/go-delve/delve/cmd/dlv@latest\n'
        else:
            msg += '  First install Go from https://go.dev/dl/\n'
            msg += '  Then: go install github.com/go-delve/delve/cmd/dlv@latest\n'
        if sys.platform == 'darwin':
            msg += '  Or: brew install delve'
        elif sys.platform == 'linux':
            msg += (
                '  Ensure GOPATH/bin or GOBIN is in your PATH:\n'
                '    export PATH=$PATH:$(go env GOPATH)/bin'
            )
        elif sys.platform == 'win32':
            msg += (
                '  Ensure %%GOPATH%%\\bin is in your PATH:\n'
                '    set PATH=%PATH%;%GOPATH%\\bin'
            )
        return msg

    def to_dict(self) -> dict:
        return {
            'platform': self.platform_info,
            'go': self.go_info,
            'delve': self.dlv_info,
            'module': self.module_info,
            'recommendation': self.recommend(),
        }


def build_go_binary(
    target: str,
    output_path: Optional[str] = None,
    cwd: Optional[str] = None,
) -> Tuple[str, str]:
    """Build a Go binary with debug symbols (optimizations disabled).

    Args:
        target: Package path, directory, or .go file to build.
        output_path: Optional output path for the binary.
        cwd: Working directory for the build.

    Returns:
        (binary_path, human_message)
    """
    go = shutil.which('go')
    if not go:
        raise FileNotFoundError(
            'Go toolchain not found. Install from https://go.dev/dl/')

    if output_path is None:
        ext = '.exe' if sys.platform == 'win32' else ''
        output_path = os.path.join(
            cwd or os.getcwd(), f'__debug_binary{ext}')

    # -gcflags="all=-N -l" disables optimizations and inlining for debugging
    cmd = [
        go, 'build',
        '-gcflags=all=-N -l',
        '-o', output_path,
        target,
    ]

    print(f'Building: {" ".join(cmd)}')
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=120,
        cwd=cwd,
    )

    if result.returncode != 0:
        error_msg = (result.stderr or result.stdout)[:4000]
        raise RuntimeError(
            f'Go build failed (exit {result.returncode}):\n{error_msg}')

    msg = f'Built {target} -> {os.path.basename(output_path)}'
    if result.stderr.strip():
        msg += f'\nWarnings:\n{result.stderr[:2000]}'

    return (os.path.abspath(output_path), msg)


def _detect_main_package(project_dir: str) -> Optional[str]:
    project = os.path.abspath(project_dir)

    # Check for main.go in project root
    if os.path.isfile(os.path.join(project, 'main.go')):
        return '.'

    # Check cmd/ directory (common Go project layout)
    cmd_dir = os.path.join(project, 'cmd')
    if os.path.isdir(cmd_dir):
        entries = [e for e in os.listdir(cmd_dir)
                   if os.path.isdir(os.path.join(cmd_dir, e))]
        if len(entries) == 1:
            return f'./cmd/{entries[0]}'
        if entries:
            # Return the first one but note ambiguity
            return f'./cmd/{entries[0]}'

    return '.'


class DelveDebugger(DebugResponseMixin):

    def __init__(self, target: str, debugger_path: str = "dlv",
                 source_root: Optional[str] = None,
                 program_args: Optional[str] = None,
                 is_binary: bool = False,
                 attach_pid: Optional[int] = None):
        self.target = target
        self.debugger_path = debugger_path
        self.source_root = source_root or os.getcwd()
        self.program_args = program_args
        self.is_binary = is_binary
        self.attach_pid = attach_pid
        self.proc: Optional[subprocess.Popen] = None

        self._lock = threading.Lock()
        self._output_buffer: List[str] = []
        self._output_event = threading.Event()
        self._program_output: List[str] = []
        self._last_out_pos = 0

        self.is_started = False
        self.is_finished = False
        self.is_paused = False

        self._prompt_re = re.compile(r'\(dlv\)\s*$')
        # Track current location for source file resolution
        self._current_file = ''
        self._current_func = ''
        self._current_line = 0
        # Breakpoint counter for tracking
        self._breakpoint_counter = 0

    def start_debugger(self):
        if self.attach_pid:
            cmd = [self.debugger_path, 'attach', str(self.attach_pid)]
        elif self.is_binary:
            cmd = [self.debugger_path, 'exec', self.target, '--']
        else:
            cmd = [self.debugger_path, 'debug', self.target, '--']

        if self.program_args and not self.attach_pid:
            import shlex
            try:
                cmd.extend(shlex.split(self.program_args))
            except ValueError:
                cmd.append(self.program_args)

        env = os.environ.copy()
        env['TERM'] = 'dumb'

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            cwd=self.source_root,
            env=env,
        )
        time.sleep(0.5)
        if self.proc.poll() is not None:
            rc = self.proc.returncode
            raise RuntimeError(
                f"Delve exited immediately (code {rc}). "
                f"Check your Go installation and target path."
            )
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        self._wait_prompt(timeout=30)

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
            text = '\n'.join(lines)
            if self._prompt_re.search(text):
                cleaned = self._prompt_re.sub('', text).strip()
                return cleaned
            # Also check if process exited
            if self.proc.poll() is not None:
                # Drain remaining
                with self._lock:
                    buf = list(self._output_buffer)
                    self._output_buffer.clear()
                lines.extend(buf)
                return '\n'.join(lines).strip()
            time.sleep(0.1)
        return '\n'.join(lines)

    def _wait_prompt(self, timeout: float = 10.0):
        self._collect_output(timeout=timeout)

    def _get_new_stdout(self) -> str:
        items = self._program_output[self._last_out_pos:]
        self._last_out_pos = len(self._program_output)
        return '\n'.join(items)[:5000]


    def _parse_location(self, text: str) -> dict:
        """Parse Delve output to extract current location.

        Delve prints locations like:
            > [bp1] main.main() ./main.go:42 (hits goroutine(1):1 total:1) (PC: 0x...)
            > main.main() ./main.go:42 (PC: 0x...)
            > main.processData() ./processor.go:15 (PC: 0x...)
        """
        loc = {"file": "", "line": 0, "function": "", "code_context": ""}

        # Pattern: > [bp_name] package.Function() ./file.go:line (...)
        m = re.search(
            r'>\s+(?:\[[\w.]+\]\s+)?([\w./]+)\(\)\s+(\S+):(\d+)',
            text,
        )
        if m:
            func_name = m.group(1)
            file_path = m.group(2)
            line_no = int(m.group(3))
            loc['function'] = func_name
            loc['file'] = self._normalize_path(file_path)
            loc['line'] = line_no
            self._current_func = func_name
            self._current_file = loc['file']
            self._current_line = line_no

        # Try to get source context from list output
        # Delve list output: "    42:		fmt.Println("hello")"
        # The current line is marked with "=>"
        for sl in text.split('\n'):
            stripped = sl.strip()
            arrow_m = re.match(r'=>\s*(\d+):\s*(.*)', stripped)
            if arrow_m:
                loc['code_context'] = arrow_m.group(2).strip()
                break
            # Also try numbered lines without arrow (from step output)
            if not loc['code_context']:
                num_m = re.match(r'(\d+):\s+(.+)', stripped)
                if num_m:
                    lno = int(num_m.group(1))
                    if lno == loc['line']:
                        loc['code_context'] = num_m.group(2).strip()

        return loc

    def _normalize_path(self, file_path: str) -> str:
        if file_path.startswith('./'):
            file_path = file_path[2:]
        if file_path.startswith('.\\'):
            file_path = file_path[2:]
        # Try to make relative to source root
        abs_path = os.path.join(self.source_root, file_path)
        if os.path.isfile(abs_path):
            return file_path
        # Return as-is if we can't resolve
        return file_path

    def _parse_stack(self, text: str) -> List[dict]:
        """Parse Delve 'stack' output.

        Format:
            0  0x0000000000abcdef in main.main
               at ./main.go:42
            1  0x0000000000abcde0 in runtime.main
               at /usr/local/go/src/runtime/proc.go:267
        """
        frames = []
        lines = text.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            # Frame line: "0  0x... in package.Function"
            m = re.match(r'(\d+)\s+0x[0-9a-fA-F]+\s+in\s+(.*)', line)
            if m:
                idx = int(m.group(1))
                func = m.group(2).strip()
                file_ = ''
                line_no = 0
                # Next line should be "at ./file.go:line"
                if i + 1 < len(lines):
                    at_line = lines[i + 1].strip()
                    at_m = re.match(r'at\s+(\S+):(\d+)', at_line)
                    if at_m:
                        file_ = self._normalize_path(at_m.group(1))
                        line_no = int(at_m.group(2))
                        i += 1
                frames.append({
                    "frame_index": idx,
                    "file": file_,
                    "line": line_no,
                    "function": func,
                    "code_context": "",
                })
            i += 1
        return frames

    def _parse_locals(self, text: str) -> dict:
        """Parse Delve 'locals' and 'args' output.

        Format:
            x = 42
            name = "hello"
            items = []int len: 3, cap: 4, [1,2,3]
            p = (*main.Config)(0xc000010200)
        """
        result = {}
        for line in text.split('\n'):
            line = line.strip()
            if not line or line.startswith('(no locals)') or \
               line.startswith('(no args)'):
                continue
            # Parse "name = value" or "name = type value"
            m = re.match(r'(\w+)\s*=\s*(.*)', line)
            if m:
                name = m.group(1)
                raw_value = m.group(2).strip()
                type_, value = self._classify_go_value(raw_value)
                result[name] = {
                    "type": type_,
                    "value": value,
                    "repr": raw_value,
                }
        return result

    @staticmethod
    def _classify_go_value(raw: str) -> Tuple[str, str]:
        if raw.startswith('"'):
            return ('string', raw)
        if raw in ('true', 'false'):
            return ('bool', raw)
        if raw == '<nil>':
            return ('nil', 'nil')
        if re.match(r'^-?\d+$', raw):
            return ('int', raw)
        if re.match(r'^-?\d+\.\d+', raw):
            return ('float64', raw)
        if raw.startswith('[]') or raw.startswith('['):
            return ('slice', raw)
        if raw.startswith('map['):
            return ('map', raw)
        if raw.startswith('(*'):
            # Pointer: (*main.Config)(0xc...)
            tm = re.match(r'\(\*?([\w./]+)\)', raw)
            return (f'*{tm.group(1)}' if tm else 'pointer', raw)
        if raw.startswith('{'):
            return ('struct', raw)
        # Complex types
        type_m = re.match(r'([\w./\[\]*]+)\s+(.*)', raw)
        if type_m:
            return (type_m.group(1), type_m.group(2))
        return ('unknown', raw)

    def _parse_goroutines(self, text: str) -> List[dict]:
        """Parse Delve 'goroutines' output.

        Format:
            * Goroutine 1 - User: ./main.go:42 main.main (0x...)
              Goroutine 2 - User: /usr/local/go/src/runtime/proc.go:395 ...
        """
        goroutines = []
        for line in text.split('\n'):
            line = line.strip()
            m = re.match(
                r'(\*)?\s*Goroutine\s+(\d+)\s*-\s*(\w+):\s+(\S+):(\d+)\s+(.*)',
                line,
            )
            if m:
                goroutines.append({
                    'current': m.group(1) == '*',
                    'id': int(m.group(2)),
                    'state': m.group(3),
                    'file': self._normalize_path(m.group(4)),
                    'line': int(m.group(5)),
                    'function': m.group(6).split('(')[0].strip(),
                })
        return goroutines


    def cmd_start(self, args_str: str = "") -> dict:
        if self.is_started:
            return self._error("Program already started. Use 'continue' instead.")
        self.is_started = True

        # Delve starts paused at the entry point; 'continue' runs to first bp
        self._send('continue')
        output = self._collect_output(timeout=60)

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
        self._send('continue')
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
        self._send('step')
        output = self._collect_output(timeout=30)
        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished during step.", stdout=output)
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
        self._send('next')
        output = self._collect_output(timeout=30)
        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished during step.", stdout=output)
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
        self._send('stepout')
        output = self._collect_output(timeout=30)
        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished during stepout.", stdout=output)
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
        # Delve doesn't have a native run-to-line; use temp breakpoint
        file_ref = self._current_file or 'main.go'
        bp_loc = f'{file_ref}:{line}'
        self._send(f'break __temp_rt {bp_loc}')
        bp_output = self._collect_output(timeout=5)
        self._send('continue')
        output = self._collect_output(timeout=60)
        # Clear the temp breakpoint
        self._send('clear __temp_rt')
        self._collect_output(timeout=3)
        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished.", stdout=output)
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
            return self._error(
                "Usage: b <line> or b <file>:<line> [condition] "
                "or b <function_name>")

        location = parts[0]
        condition = parts[1] if len(parts) > 1 else None

        # Determine the Delve break command
        try:
            line = int(location)
            # Just a line number -- use current file
            file_ref = self._current_file or 'main.go'
            bp_loc = f'{file_ref}:{line}'
        except ValueError:
            bp_loc = location  # file:line or function name

        self._breakpoint_counter += 1
        bp_name = f'bp{self._breakpoint_counter}'
        self._send(f'break {bp_name} {bp_loc}')
        output = self._collect_output(timeout=5)

        # Set condition if provided
        if condition and 'set' in output.lower() or \
           condition and 'breakpoint' in output.lower():
            # Use 'cond' command to set condition
            self._send(f'cond {bp_name} {condition}')
            cond_output = self._collect_output(timeout=3)
            output += f'\nCondition: {condition}\n{cond_output}'

        msg = output or f"Breakpoint set: {args}"
        return {
            "status": "paused" if self.is_paused else "running",
            "command": f"set_breakpoint {args}",
            "message": msg,
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_remove_breakpoint(self, args: str) -> dict:
        location = args.strip()
        if not location:
            return self._error("Usage: rb <breakpoint_name_or_id> or rb all")

        if location.lower() == 'all':
            self._send('clearall')
        else:
            self._send(f'clear {location}')

        output = self._collect_output(timeout=3)
        return {
            "status": "paused" if self.is_paused else "running",
            "command": f"remove_breakpoint {args}",
            "message": output or f"Breakpoint cleared: {location}",
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_list_breakpoints(self) -> dict:
        self._send('breakpoints')
        output = self._collect_output(timeout=5)
        return {
            "status": "paused" if self.is_paused else "running",
            "command": "breakpoints",
            "message": output or "No breakpoints set.",
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_inspect(self) -> dict:
        stack = self._get_call_stack()
        locals_ = self._get_locals()
        loc = {
            "file": self._current_file,
            "line": self._current_line,
            "function": self._current_func,
            "code_context": "",
        }
        return {
            "status": "paused", "command": "inspect",
            "message": "Current state",
            "current_location": loc,
            "call_stack": stack,
            "local_variables": locals_,
            "stdout_new": self._get_new_stdout(), "stderr_new": "",
        }

    def cmd_evaluate(self, expr: str) -> dict:
        if not expr.strip():
            return self._error("Usage: e <expression>")

        self._send(f'print {expr.strip()}')
        output = self._collect_output(timeout=10)

        value = output.strip()
        return {
            "status": "paused", "command": "evaluate",
            "message": f"{expr.strip()} = {value}",
            "eval_result": {"type": "expression", "value": value, "repr": value},
            "current_location": None,
            "call_stack": [], "local_variables": {},
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_list_source(self, args: str) -> dict:
        self._send('list')
        output = self._collect_output(timeout=5)
        return {
            "status": "paused", "command": "list",
            "message": output or "(no source available)",
            "current_location": None,
            "call_stack": [], "local_variables": {},
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_backtrace(self) -> dict:
        stack = self._get_call_stack()
        if stack:
            lines = []
            for f in stack:
                lines.append(
                    f"  #{f['frame_index']}: {f['function']} "
                    f"at {f['file']}:{f['line']}")
            msg = f"Call stack ({len(stack)} frames):\n" + "\n".join(lines)
        else:
            msg = "No call stack available."
        return {
            "status": "paused", "command": "backtrace", "message": msg,
            "current_location": None,
            "call_stack": stack, "local_variables": {},
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_goroutines(self) -> dict:
        self._send('goroutines')
        output = self._collect_output(timeout=5)
        goroutines = self._parse_goroutines(output)
        if goroutines:
            lines = []
            for g in goroutines:
                marker = '*' if g['current'] else ' '
                lines.append(
                    f"  {marker} Goroutine {g['id']} [{g['state']}]: "
                    f"{g['function']} at {g['file']}:{g['line']}")
            msg = f"Goroutines ({len(goroutines)}):\n" + "\n".join(lines)
        else:
            msg = output or "No goroutine info available."
        return {
            "status": "paused", "command": "goroutines",
            "message": msg,
            "current_location": None,
            "call_stack": [], "local_variables": {},
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_switch_goroutine(self, gid: str) -> dict:
        if not gid.strip():
            return self._error("Usage: g <goroutine_id>")
        self._send(f'goroutine {gid.strip()}')
        output = self._collect_output(timeout=5)
        loc = self._parse_location(output)
        return {
            "status": "paused", "command": f"goroutine {gid}",
            "message": output or f"Switched to goroutine {gid}",
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": {},
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_quit(self) -> dict:
        try:
            self._send('exit')
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


    def _get_call_stack(self) -> List[dict]:
        self._send('stack')
        output = self._collect_output(timeout=5)
        return self._parse_stack(output)

    def _get_locals(self) -> dict:
        self._send('locals')
        locals_output = self._collect_output(timeout=5)
        self._send('args')
        args_output = self._collect_output(timeout=5)
        combined = locals_output + '\n' + args_output
        return self._parse_locals(combined)

    def _is_exit(self, text: str) -> bool:
        return bool(re.search(
            r'Process exited with status|'
            r'Process \d+ has exited with status|'
            r'process has exited|'
            r'exited with status|'
            r'has exited with status',
            text, re.I,
        ))

    def _format_stop_message(self, output: str) -> str:
        lines = [l.strip() for l in output.strip().split('\n') if l.strip()]
        return '\n'.join(lines[:8]) if lines else "Stopped."


class GoDebugServer(BaseDebugServer):
    LANGUAGE = "Go"
    SCRIPT_NAME = "go_debug_session.py"

    def _dispatch_extra(self, action, args):
        if action in ("goroutines", "gs"):
            return self.debugger.cmd_goroutines()
        elif action in ("goroutine", "g"):
            return self.debugger.cmd_switch_goroutine(args)
        return None

    def _available_commands(self):
        cmds = super()._available_commands()
        cmds.extend(["goroutines", "goroutine"])
        return cmds


def _find_module_root(start_dir: str) -> Optional[str]:
    cur = os.path.abspath(start_dir)
    for _ in range(20):
        if os.path.isfile(os.path.join(cur, 'go.mod')):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None

