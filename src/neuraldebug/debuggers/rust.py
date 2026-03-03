"""Rust debugging via GDB or LLDB."""

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
    BaseDebugServer, MiDebuggerBase, GdbMiParser,
    DebugResponseMixin, error_response, completed_response,
    find_repo_root,
)


class RustToolchainInfo:

    def __init__(self):
        self.platform_info = self._detect_platform()
        self.rust_info = self._detect_rust()
        self.debuggers = self._detect_debuggers()

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

    def _detect_rust(self) -> Optional[dict]:
        cargo = shutil.which('cargo')
        rustc = shutil.which('rustc')

        if not rustc:
            return None

        version = ''
        try:
            r = subprocess.run(
                [rustc, '--version'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                version = r.stdout.strip()
        except Exception:
            pass

        target = ''
        try:
            r = subprocess.run(
                [rustc, '--version', '--verbose'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                for line in r.stdout.split('\n'):
                    if line.strip().startswith('host:'):
                        target = line.split(':', 1)[1].strip()
                        break
        except Exception:
            pass

        return {
            'rustc': rustc,
            'cargo': cargo,
            'version': version,
            'host_target': target,
        }

    def _detect_debuggers(self) -> list:
        found = []

        # rust-gdb (Rust's GDB wrapper with pretty-printers)
        rust_gdb = shutil.which('rust-gdb')
        if rust_gdb and self._validate_tool(rust_gdb):
            found.append({
                'name': 'rust-gdb',
                'path': rust_gdb,
                'version': self._tool_version(rust_gdb),
                'backend': 'gdb',
                'rust_pretty_printers': True,
            })

        # Plain GDB
        gdb = shutil.which('gdb')
        if gdb and self._validate_tool(gdb):
            found.append({
                'name': 'gdb',
                'path': gdb,
                'version': self._tool_version(gdb),
                'backend': 'gdb',
                'rust_pretty_printers': False,
            })

        # rust-lldb (Rust's LLDB wrapper with pretty-printers)
        rust_lldb = shutil.which('rust-lldb')
        if rust_lldb and self._validate_tool(rust_lldb):
            found.append({
                'name': 'rust-lldb',
                'path': rust_lldb,
                'version': self._tool_version(rust_lldb),
                'backend': 'lldb',
                'rust_pretty_printers': True,
            })

        # Plain LLDB
        lldb = shutil.which('lldb')
        if lldb and self._validate_tool(lldb):
            found.append({
                'name': 'lldb',
                'path': lldb,
                'version': self._tool_version(lldb),
                'backend': 'lldb',
                'rust_pretty_printers': False,
            })

        # CDB (Windows)
        if sys.platform == 'win32':
            cdb = self._find_cdb()
            if cdb:
                found.append({
                    'name': 'cdb',
                    'path': cdb,
                    'version': self._tool_version_cdb(cdb),
                    'backend': 'cdb',
                    'rust_pretty_printers': False,
                })

        return found

    def _find_cdb(self) -> Optional[str]:
        path = shutil.which('cdb') or shutil.which('cdb.exe')
        if path:
            return path
        # Check Windows SDK locations
        arch = platform.machine().lower()
        arch_dirs = ['x64', 'x86'] if arch in ('amd64', 'x86_64', 'x64') else ['x86', 'x64']
        pf86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
        pf = os.environ.get('ProgramFiles', r'C:\Program Files')
        kit_roots = [
            os.path.join(pf86, 'Windows Kits', '10', 'Debuggers'),
            os.path.join(pf86, 'Windows Kits', '11', 'Debuggers'),
            os.path.join(pf, 'Windows Kits', '10', 'Debuggers'),
        ]
        for kit in kit_roots:
            for arch_dir in arch_dirs:
                candidate = os.path.join(kit, arch_dir, 'cdb.exe')
                if os.path.isfile(candidate):
                    return candidate
        return None

    @staticmethod
    def _validate_tool(path: str) -> bool:
        try:
            r = subprocess.run(
                [path, '--version'],
                capture_output=True, text=True, timeout=10,
            )
            return r.returncode in (0, 1)
        except (subprocess.TimeoutExpired, OSError):
            return False

    @staticmethod
    def _tool_version(path: str) -> str:
        try:
            r = subprocess.run(
                [path, '--version'],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().split('\n')[0]
        except Exception:
            pass
        return ''

    @staticmethod
    def _tool_version_cdb(path: str) -> str:
        try:
            r = subprocess.run(
                [path, '-version'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().split('\n')[0]
        except Exception:
            pass
        return ''

    def recommend(self) -> dict:
        """Recommend the best debugger for Rust on this platform."""
        if not self.debuggers:
            return {
                'debugger': None,
                'note': self._install_instructions(),
            }

        # Prefer Rust-specific wrappers (they load pretty-printers)
        for d in self.debuggers:
            if d.get('rust_pretty_printers'):
                return {'debugger': d, 'note': f"{d['name']} (Rust pretty-printers enabled)"}

        # On Windows, prefer CDB for MSVC targets
        if sys.platform == 'win32':
            target = (self.rust_info or {}).get('host_target', '')
            if 'msvc' in target:
                for d in self.debuggers:
                    if d['backend'] == 'cdb':
                        return {'debugger': d, 'note': 'CDB (native PDB support for MSVC target)'}

        return {'debugger': self.debuggers[0], 'note': self.debuggers[0]['name']}

    def _install_instructions(self) -> str:
        msg = 'No debugger found for Rust. Install one:\n'
        if sys.platform == 'win32':
            msg += (
                '  1. Install Debugging Tools for Windows (CDB):\n'
                '     winget install Microsoft.WinDbg\n'
                '  2. Install MSYS2 with GDB: pacman -S mingw-w64-x86_64-gdb\n'
                '  Note: rust-gdb / rust-lldb come with rustup on POSIX'
            )
        elif sys.platform == 'darwin':
            msg += (
                '  1. Xcode Command Line Tools: xcode-select --install (provides lldb)\n'
                '  2. Homebrew: brew install gdb\n'
                '  rust-gdb and rust-lldb are bundled with rustup'
            )
        else:
            msg += (
                '  Debian/Ubuntu: apt install gdb\n'
                '  RHEL/CentOS:   yum install gdb\n'
                '  rust-gdb and rust-lldb are bundled with rustup'
            )
        return msg

    def to_dict(self) -> dict:
        return {
            'platform': self.platform_info,
            'rust': self.rust_info,
            'debuggers': self.debuggers,
            'recommendation': self.recommend(),
        }


def cargo_build(
    project_dir: str,
    binary_name: Optional[str] = None,
    profile: str = 'dev',
    extra_args: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """Build a Rust project with debug symbols.

    Args:
        project_dir: Directory containing Cargo.toml.
        binary_name: Specific binary to build (for multi-binary crates).
        profile: Build profile (dev=debug symbols, release).
        extra_args: Extra cargo build arguments.

    Returns:
        (executable_path, human_message)
    """
    cargo = shutil.which('cargo')
    if not cargo:
        raise FileNotFoundError(
            'Cargo not found. Install Rust via https://rustup.rs/')

    project = os.path.abspath(project_dir)
    cargo_toml = os.path.join(project, 'Cargo.toml')
    if not os.path.isfile(cargo_toml):
        raise FileNotFoundError(f'No Cargo.toml found in {project_dir}')

    cmd = [cargo, 'build']
    if profile != 'dev':
        cmd.extend(['--profile', profile])
    if binary_name:
        cmd.extend(['--bin', binary_name])
    if extra_args:
        cmd.extend(extra_args)

    print(f'Building: {" ".join(cmd)}')
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300,
        cwd=project,
    )

    if result.returncode != 0:
        error_msg = (result.stderr or result.stdout)[:4000]
        raise RuntimeError(
            f'Cargo build failed (exit {result.returncode}):\n{error_msg}')

    # Find the output binary
    target_dir = os.path.join(project, 'target')
    profile_dir = 'debug' if profile == 'dev' else profile
    bin_dir = os.path.join(target_dir, profile_dir)

    if binary_name:
        exe_name = binary_name + ('.exe' if sys.platform == 'win32' else '')
        exe_path = os.path.join(bin_dir, exe_name)
        if os.path.isfile(exe_path):
            return (exe_path, f'Built {binary_name} ({profile})')
    else:
        # Find the most recently built executable
        exe_path = _find_newest_binary(bin_dir)
        if exe_path:
            return (exe_path, f'Built {os.path.basename(exe_path)} ({profile})')

    raise FileNotFoundError(
        f'Could not find built binary in {bin_dir}. '
        'Use --bin to specify the binary name.')


def _find_newest_binary(bin_dir: str) -> Optional[str]:
    if not os.path.isdir(bin_dir):
        return None

    exe_ext = '.exe' if sys.platform == 'win32' else ''
    candidates = []

    for f in os.listdir(bin_dir):
        fpath = os.path.join(bin_dir, f)
        if not os.path.isfile(fpath):
            continue
        if sys.platform == 'win32':
            if not f.endswith('.exe'):
                continue
        else:
            if '.' in f:
                continue
            if not os.access(fpath, os.X_OK):
                continue
        candidates.append(fpath)

    if not candidates:
        return None

    return max(candidates, key=os.path.getmtime)


class RustGdbDebugger(MiDebuggerBase):

    def __init__(self, executable: str, debugger_path: str = "gdb",
                 source_paths: Optional[List[str]] = None,
                 program_args: Optional[str] = None,
                 attach_pid: Optional[int] = None):
        self.executable = os.path.abspath(executable) if executable else ''
        self.debugger_path = debugger_path
        self.source_dir = os.path.dirname(self.executable) or os.getcwd()
        self.source_paths = source_paths or []
        self.program_args = program_args
        self.attach_pid = attach_pid
        self._init_mi()

    def start_gdb(self):
        if self.attach_pid:
            cmd = [self.debugger_path, "--interpreter=mi", "--quiet",
                   "-p", str(self.attach_pid)]
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
                f"{self.debugger_path} may have missing dependencies."
            )
        self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()
        self._wait_for_prompt(timeout=10)

        # Set source paths
        for sp in self.source_paths:
            self._send_mi(f'-environment-directory {sp}')
        if self.source_dir:
            self._send_mi(f'-environment-directory {self.source_dir}')
        # Set program arguments
        if self.program_args and not self.attach_pid:
            self._send_mi(f'-exec-arguments {self.program_args}')


    def cmd_start(self, args_str: str = "") -> dict:
        if self.is_started:
            return self._error("Program already started. Use 'continue' instead.")
        self.is_started = True
        if args_str:
            tok = self._send_mi(f'-exec-arguments {args_str}')
            self._collect_until_result(tok, timeout=5)
        tok = self._send_mi('-exec-run')
        result, others = self._collect_until_result(tok, timeout=10)
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
        parts = args.strip().split(None, 1)
        if not parts:
            return self._error("Usage: b <line> or b <file>:<line> or b <func> [condition]")
        location = parts[0]
        condition = parts[1] if len(parts) > 1 else None

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

        if condition and bp_num != '?':
            cond_tok = self._send_mi(f'-break-condition {bp_num} {condition}')
            cond_result, _ = self._collect_until_result(cond_tok, timeout=5)
            if cond_result and cond_result.get('class_') == 'error':
                msg = cond_result.get('body', {}).get('msg', '')
                return self._error(
                    f"Breakpoint set at {bp_file}:{bp_line} but condition failed: {msg}")

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
                                'file': bkpt.get('file', '?'),
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
                "command": "evaluate",
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
        tok = self._send_mi('-interpreter-exec console "list"')
        result, others = self._collect_until_result(tok, timeout=5)
        source_lines = []
        for rec in others:
            if rec.get('type') == 'console':
                source_lines.append(rec.get('body', ''))
        with self._lock:
            for rec in self._pending_records:
                if rec.get('type') == 'console':
                    source_lines.append(rec.get('body', ''))
        source_text = ''.join(source_lines) if source_lines else "(no source available)"
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
        # Arguments
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
        # Locals
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
        body = stop_event.get('body', {})
        reason = body.get('reason', 'unknown')
        frame = body.get('frame', {})
        filename = frame.get('file', frame.get('fullname', ''))
        line = frame.get('line', '')
        func = frame.get('func', '')

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
            rv = body.get('return-value', '')
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
        for sp in self.source_paths:
            candidate = os.path.join(sp, filename)
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


class RustDebugServer(BaseDebugServer):
    LANGUAGE = "Rust"
    SCRIPT_NAME = "rust_debug_session.py"


def find_debugger(preference: Optional[str] = None) -> Tuple[str, str]:
    """Find an available debugger for Rust. Returns (backend_type, path).

    backend_type is 'gdb' or 'lldb'.
    Prefers rust-gdb / rust-lldb wrappers for better Rust type display.
    """
    toolchain = RustToolchainInfo()

    if preference:
        pref = preference.lower()
        for d in toolchain.debuggers:
            if d['name'] == pref or d['backend'] == pref:
                return (d['backend'], d['path'])
        raise FileNotFoundError(
            f"{pref} not found.\n" + toolchain._install_instructions())

    if toolchain.debuggers:
        rec = toolchain.recommend()
        if rec['debugger']:
            d = rec['debugger']
            return (d['backend'], d['path'])

    raise FileNotFoundError(
        'No debugger found for Rust.\n' + toolchain._install_instructions())

