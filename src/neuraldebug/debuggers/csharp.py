"""C# debugging via netcoredbg (MI protocol)."""

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


class DotNetToolchainInfo:

    def __init__(self):
        self.platform_info = self._detect_platform()
        self.sdk_info = self._detect_dotnet_sdk()
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

    def _detect_dotnet_sdk(self) -> Optional[dict]:
        dotnet_path = shutil.which('dotnet')
        if not dotnet_path:
            return None

        version = ''
        try:
            r = subprocess.run(
                [dotnet_path, '--version'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                version = r.stdout.strip()
        except Exception:
            pass

        runtimes = []
        try:
            r = subprocess.run(
                [dotnet_path, '--list-runtimes'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                for line in r.stdout.strip().split('\n')[:5]:
                    runtimes.append(line.strip())
        except Exception:
            pass

        return {
            'path': dotnet_path,
            'version': version,
            'runtimes': runtimes,
        }

    def _detect_debuggers(self) -> list:
        found = []

        # netcoredbg (Samsung's open-source .NET debugger, supports MI mode)
        netcoredbg = shutil.which('netcoredbg')
        if netcoredbg and self._validate_tool(netcoredbg, '--version'):
            found.append({
                'name': 'netcoredbg',
                'path': netcoredbg,
                'version': self._tool_version(netcoredbg, '--version'),
                'mi_mode': True,
            })

        # Check common install locations for netcoredbg
        if not netcoredbg:
            candidates = self._find_netcoredbg_candidates()
            for c in candidates:
                if os.path.isfile(c) and self._validate_tool(c, '--version'):
                    found.append({
                        'name': 'netcoredbg',
                        'path': c,
                        'version': self._tool_version(c, '--version'),
                        'mi_mode': True,
                    })
                    break

        # dotnet-dump (for core dump analysis)
        dotnet_dump = shutil.which('dotnet-dump')
        if dotnet_dump:
            found.append({
                'name': 'dotnet-dump',
                'path': dotnet_dump,
                'version': '',
                'mi_mode': False,
            })

        return found

    def _find_netcoredbg_candidates(self) -> List[str]:
        candidates = []
        exe = 'netcoredbg.exe' if sys.platform == 'win32' else 'netcoredbg'

        if sys.platform == 'win32':
            for base in [
                os.environ.get('USERPROFILE', ''),
                os.environ.get('ProgramFiles', ''),
                os.environ.get('ProgramFiles(x86)', ''),
            ]:
                if base:
                    candidates.append(os.path.join(base, '.dotnet', 'tools', exe))
                    candidates.append(os.path.join(base, 'netcoredbg', exe))
        else:
            home = os.environ.get('HOME', '')
            if home:
                candidates.append(os.path.join(home, '.dotnet', 'tools', exe))
                candidates.append(os.path.join(home, '.local', 'share', 'netcoredbg', exe))
            candidates.append(f'/usr/local/bin/{exe}')
            candidates.append(f'/usr/bin/{exe}')

        return candidates

    @staticmethod
    def _validate_tool(path: str, version_flag: str = '--version') -> bool:
        try:
            r = subprocess.run(
                [path, version_flag],
                capture_output=True, text=True, timeout=10,
            )
            return r.returncode in (0, 1)
        except (subprocess.TimeoutExpired, OSError):
            return False

    @staticmethod
    def _tool_version(path: str, flag: str = '--version') -> str:
        try:
            r = subprocess.run(
                [path, flag],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().split('\n')[0]
        except Exception:
            pass
        return ''

    def recommend(self) -> dict:
        if self.debuggers:
            # Prefer netcoredbg (MI mode support)
            for d in self.debuggers:
                if d.get('mi_mode'):
                    return {'debugger': d, 'note': 'netcoredbg with MI mode (recommended)'}
            return {'debugger': self.debuggers[0], 'note': self.debuggers[0]['name']}

        return {
            'debugger': None,
            'note': self._install_instructions(),
        }

    def _install_instructions(self) -> str:
        msg = 'No .NET debugger found. Install netcoredbg:\n'
        if sys.platform == 'win32':
            msg += (
                '  Option 1: Download from https://github.com/Samsung/netcoredbg/releases\n'
                '  Option 2: Install via dotnet tool: dotnet tool install -g netcoredbg\n'
                '  Option 3: Use Visual Studio for debugging'
            )
        elif sys.platform == 'darwin':
            msg += (
                '  brew install netcoredbg\n'
                '  or download from https://github.com/Samsung/netcoredbg/releases'
            )
        else:
            msg += (
                '  Download from https://github.com/Samsung/netcoredbg/releases\n'
                '  or install via package manager if available'
            )
        return msg

    def to_dict(self) -> dict:
        return {
            'platform': self.platform_info,
            'sdk': self.sdk_info,
            'debuggers': self.debuggers,
            'recommendation': self.recommend(),
        }


def build_dotnet_project(
    project_path: str,
    configuration: str = 'Debug',
    output_dir: Optional[str] = None,
) -> Tuple[str, str]:
    """Build a .NET project and return (dll_path, message).

    Args:
        project_path: Path to .csproj, .sln, or directory containing one.
        configuration: Build configuration (Debug/Release).
        output_dir: Optional output directory override.
    """
    dotnet = shutil.which('dotnet')
    if not dotnet:
        raise FileNotFoundError(
            '.NET SDK not found. Install from https://dot.net/download')

    project = os.path.abspath(project_path)

    # If directory, look for .csproj
    if os.path.isdir(project):
        csproj_files = [f for f in os.listdir(project)
                        if f.endswith('.csproj')]
        if csproj_files:
            project = os.path.join(project, csproj_files[0])
        else:
            raise FileNotFoundError(
                f'No .csproj file found in {project_path}')

    cmd = [dotnet, 'build', project, '-c', configuration]
    if output_dir:
        cmd.extend(['-o', output_dir])

    print(f'Building: {" ".join(cmd)}')
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        error_msg = (result.stderr or result.stdout)[:4000]
        raise RuntimeError(
            f'Build failed (exit {result.returncode}):\n{error_msg}')

    # Find the output DLL
    dll_path = _find_build_output(project, configuration, output_dir)
    msg = f'Built {os.path.basename(project)} ({configuration})'
    if result.stderr.strip():
        msg += f'\nWarnings:\n{result.stderr[:2000]}'

    return (dll_path, msg)


def _find_build_output(
    project_path: str,
    configuration: str,
    output_dir: Optional[str],
) -> str:
    if output_dir and os.path.isdir(output_dir):
        for f in os.listdir(output_dir):
            if f.endswith('.dll') and not f.startswith('System.'):
                return os.path.join(output_dir, f)

    project_dir = os.path.dirname(project_path)
    project_name = os.path.splitext(os.path.basename(project_path))[0]

    # Search bin/Debug/net*/
    bin_dir = os.path.join(project_dir, 'bin', configuration)
    if os.path.isdir(bin_dir):
        for tfm in sorted(os.listdir(bin_dir), reverse=True):
            tfm_dir = os.path.join(bin_dir, tfm)
            if os.path.isdir(tfm_dir):
                dll = os.path.join(tfm_dir, f'{project_name}.dll')
                if os.path.isfile(dll):
                    return dll
                # Try any .dll
                for f in os.listdir(tfm_dir):
                    if f.endswith('.dll') and f == f'{project_name}.dll':
                        return os.path.join(tfm_dir, f)

    raise FileNotFoundError(
        f'Could not find build output DLL for {project_path}. '
        f'Searched in {bin_dir}')


class NetcoredbgDebugger(MiDebuggerBase):

    def __init__(self, target: str, debugger_path: str = "netcoredbg",
                 source_paths: Optional[List[str]] = None,
                 program_args: Optional[str] = None,
                 attach_pid: Optional[int] = None):
        self.target = os.path.abspath(target) if target else ''
        self.debugger_path = debugger_path
        self.source_dir = os.path.dirname(self.target) or os.getcwd()
        self.source_paths = source_paths or []
        self.program_args = program_args
        self.attach_pid = attach_pid
        self._init_mi()

    def start_debugger(self):
        if self.attach_pid:
            cmd = [self.debugger_path, '--interpreter=mi',
                   '--attach', str(self.attach_pid)]
        else:
            cmd = [self.debugger_path, '--interpreter=mi', '--']
            if self.target.endswith('.dll'):
                cmd.extend(['dotnet', self.target])
            else:
                cmd.append(self.target)

        if self.program_args and not self.attach_pid:
            import shlex
            try:
                cmd.extend(shlex.split(self.program_args))
            except ValueError:
                cmd.append(self.program_args)

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
                f"{self.debugger_path} may not be installed correctly."
            )
        self._reader_thread = threading.Thread(target=self._read_output, daemon=True)
        self._reader_thread.start()
        self._wait_for_prompt(timeout=10)


    def cmd_start(self, args_str: str = "") -> dict:
        if self.is_started:
            return self._error("Program already started. Use 'continue' instead.")
        self.is_started = True

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
        tok = self._send_mi(f'-break-insert -t {line}')
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
            return self._error("Usage: b <line> or b <file>:<line> or b <method>")

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
                    f"Breakpoint set at {bp_file}:{bp_line} but "
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

        # netcoredbg uses -var-create instead of -data-evaluate-expression
        self._var_counter = getattr(self, '_var_counter', 0) + 1
        var_name = f"eval{self._var_counter}"
        tok = self._send_mi(f'-var-create {var_name} * {expr.strip()}')
        result, _ = self._collect_until_result(tok, timeout=10)

        if result and result.get('class_') == 'done':
            value = result.get('body', {}).get('value', '<no value>')
            var_type = result.get('body', {}).get('type', '')
            # Clean up the var
            cleanup_tok = self._send_mi(f'-var-delete {var_name}')
            self._collect_until_result(cleanup_tok, timeout=3)
            display = f"{expr.strip()} = {value}" + (f" ({var_type})" if var_type else "")
            return {
                "status": "paused",
                "command": "evaluate",
                "message": display,
                "eval_result": {"type": var_type, "value": str(value), "repr": str(value)},
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
        loc = self._get_current_location()
        if not loc or not loc.get('file') or not loc.get('line'):
            return self._error("No current source location")

        context = 5
        if args.strip():
            try:
                context = int(args.strip())
            except ValueError:
                pass

        filename = loc['file']
        current_line = loc['line']
        lines = []

        src_path = self._resolve_source(filename)
        if src_path and os.path.isfile(src_path):
            try:
                with open(src_path, 'r', errors='replace') as f:
                    all_lines = f.readlines()
                start = max(0, current_line - context - 1)
                end = min(len(all_lines), current_line + context)
                for i in range(start, end):
                    marker = ">>>" if i + 1 == current_line else "   "
                    lines.append(f"{marker} {i+1:4d} | {all_lines[i].rstrip()}")
            except OSError:
                pass

        source_text = "\n".join(lines) if lines else "(no source available)"

        return {
            "status": "paused",
            "command": "list",
            "message": f"Source around line {current_line}:\n{source_text}",
            "current_location": loc,
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

        # netcoredbg uses -stack-list-variables (not -stack-list-locals/-stack-list-arguments)
        tok = self._send_mi('-stack-list-variables 1')
        result, _ = self._collect_until_result(tok, timeout=5)
        if result and result.get('class_') == 'done':
            variables = result.get('body', {}).get('variables', [])
            if isinstance(variables, list):
                for item in variables:
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
            msg = f"Returned from function"
            if func:
                msg += f" to {func}()"
        elif reason == 'exited-normally':
            self.is_finished = True
            return self._completed("Program exited normally.",
                                   stdout=self._get_new_stdout())
        elif reason == 'exited':
            exit_code = body.get('exit-code', '?')
            self.is_finished = True
            return self._completed(f"Program exited with code {exit_code}.",
                                   stdout=self._get_new_stdout())
        elif 'exception' in reason:
            exc_info = body.get('exception-info', '')
            msg = f"Exception: {exc_info}" if exc_info else f"Exception at {func}()"
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


class CSharpDebugServer(BaseDebugServer):
    LANGUAGE = "C#"
    SCRIPT_NAME = "csharp_debug_session.py"


def find_debugger() -> Tuple[str, str]:
    toolchain = DotNetToolchainInfo()
    for d in toolchain.debuggers:
        if d.get('mi_mode'):
            return (d['name'], d['path'])

    raise FileNotFoundError(
        'No .NET debugger found.\n' + toolchain._install_instructions()
    )

