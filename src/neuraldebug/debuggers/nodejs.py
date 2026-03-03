"""Node.js/TypeScript debugging via inspector."""

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


class NodeToolchainInfo:

    def __init__(self):
        self.platform_info = self._detect_platform()
        self.node_info = self._detect_node()
        self.tools = self._detect_tools()

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

    def _detect_node(self) -> Optional[dict]:
        node = shutil.which('node')
        if not node:
            return None

        version = ''
        try:
            r = subprocess.run(
                [node, '--version'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                version = r.stdout.strip()
        except Exception:
            pass

        npm = shutil.which('npm')
        npx = shutil.which('npx')

        return {
            'node': node,
            'npm': npm,
            'npx': npx,
            'version': version,
            'has_debugger': True,  # node inspect is always available
        }

    def _detect_tools(self) -> list:
        tools = []

        tsc = shutil.which('tsc')
        if not tsc:
            # Try node_modules/.bin
            local = os.path.join('.', 'node_modules', '.bin', 'tsc')
            if sys.platform == 'win32':
                local += '.cmd'
            if os.path.isfile(local):
                tsc = os.path.abspath(local)
        if tsc:
            tools.append({
                'name': 'tsc', 'path': tsc,
                'version': self._tool_version(tsc, '--version'),
            })

        ts_node = shutil.which('ts-node')
        if not ts_node:
            local = os.path.join('.', 'node_modules', '.bin', 'ts-node')
            if sys.platform == 'win32':
                local += '.cmd'
            if os.path.isfile(local):
                ts_node = os.path.abspath(local)
        if ts_node:
            tools.append({
                'name': 'ts-node', 'path': ts_node,
                'version': self._tool_version(ts_node, '--version'),
            })

        tsx = shutil.which('tsx')
        if not tsx:
            local = os.path.join('.', 'node_modules', '.bin', 'tsx')
            if sys.platform == 'win32':
                local += '.cmd'
            if os.path.isfile(local):
                tsx = os.path.abspath(local)
        if tsx:
            tools.append({
                'name': 'tsx', 'path': tsx,
                'version': self._tool_version(tsx, '--version'),
            })

        return tools

    @staticmethod
    def _tool_version(path: str, flag: str = '--version') -> str:
        try:
            r = subprocess.run(
                [path, flag],
                capture_output=True, text=True, timeout=15,
            )
            output = r.stdout or r.stderr
            if output:
                return output.strip().split('\n')[0]
        except Exception:
            pass
        return ''

    def recommend(self) -> dict:
        if self.node_info and self.node_info.get('has_debugger'):
            return {
                'debugger': {
                    'name': 'node inspect',
                    'path': self.node_info['node'],
                },
                'note': 'Node.js built-in inspector CLI (bundled with Node.js)',
            }

        return {
            'debugger': None,
            'note': self._install_instructions(),
        }

    def _install_instructions(self) -> str:
        msg = 'No Node.js found. Install Node.js:\n'
        if sys.platform == 'win32':
            msg += (
                '  Option 1: winget install OpenJS.NodeJS.LTS\n'
                '  Option 2: Download from https://nodejs.org/\n'
                '  Option 3: Use nvm-windows: https://github.com/coreybutler/nvm-windows'
            )
        elif sys.platform == 'darwin':
            msg += (
                '  brew install node\n'
                '  or download from https://nodejs.org/\n'
                '  or use nvm: https://github.com/nvm-sh/nvm'
            )
        else:
            msg += (
                '  Debian/Ubuntu: apt install nodejs npm\n'
                '  RHEL/CentOS:   yum install nodejs\n'
                '  Arch:          pacman -S nodejs npm\n'
                '  or use nvm: https://github.com/nvm-sh/nvm'
            )
        return msg

    def has_typescript_support(self) -> bool:
        return any(t['name'] in ('ts-node', 'tsx') for t in self.tools)

    def has_tsc(self) -> bool:
        return any(t['name'] == 'tsc' for t in self.tools)

    def to_dict(self) -> dict:
        return {
            'platform': self.platform_info,
            'node': self.node_info,
            'tools': self.tools,
            'recommendation': self.recommend(),
        }


def compile_typescript(
    source_file: str,
    output_dir: Optional[str] = None,
) -> Tuple[str, str]:
    """Compile a TypeScript file to JavaScript with source maps.

    Args:
        source_file: Path to .ts file.
        output_dir: Output directory for compiled .js files.

    Returns:
        (js_file_path, human_message)
    """
    src = os.path.abspath(source_file)
    if not os.path.isfile(src):
        raise FileNotFoundError(f'Source file not found: {source_file}')

    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(src), '.NeuralDebug_build')

    os.makedirs(output_dir, exist_ok=True)

    # Try npx tsc first, then tsc directly
    tsc = shutil.which('tsc')
    npx = shutil.which('npx')

    if tsc:
        cmd = [tsc, src, '--outDir', output_dir, '--sourceMap']
    elif npx:
        cmd = [npx, 'tsc', src, '--outDir', output_dir, '--sourceMap']
    else:
        raise FileNotFoundError(
            'TypeScript compiler (tsc) not found. '
            'Install with: npm install -g typescript')

    print(f'Compiling TypeScript: {" ".join(cmd)}')
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    if result.returncode != 0:
        error_msg = (result.stderr or result.stdout)[:4000]
        raise RuntimeError(
            f'TypeScript compilation failed (exit {result.returncode}):\n{error_msg}')

    # Determine output JS file
    basename = os.path.splitext(os.path.basename(src))[0]
    js_file = os.path.join(output_dir, basename + '.js')

    if not os.path.isfile(js_file):
        # Search output_dir for any .js file
        for f in os.listdir(output_dir):
            if f.endswith('.js'):
                js_file = os.path.join(output_dir, f)
                break

    if not os.path.isfile(js_file):
        raise FileNotFoundError(
            f'Compiled JS not found. Expected {js_file}')

    msg = f'Compiled {os.path.basename(src)} -> {js_file}'
    if result.stderr.strip():
        msg += f'\nWarnings:\n{result.stderr[:2000]}'

    return (js_file, msg)


def detect_package_main(project_dir: str) -> Optional[str]:
    pkg_json = os.path.join(project_dir, 'package.json')
    if not os.path.isfile(pkg_json):
        return None
    try:
        with open(pkg_json, 'r', encoding='utf-8') as f:
            data = json.load(f)
        main = data.get('main')
        if main:
            return os.path.join(project_dir, main)
    except (json.JSONDecodeError, OSError):
        pass
    return None


def is_typescript_project(project_dir: str) -> bool:
    return os.path.isfile(os.path.join(project_dir, 'tsconfig.json'))


class NodeInspectorDebugger(DebugResponseMixin):

    def __init__(self, script_file: str, node_path: str = "node",
                 program_args: Optional[str] = None,
                 ts_mode: Optional[str] = None,
                 attach_pid: Optional[int] = None):
        self.script_file = os.path.abspath(script_file) if script_file else ''
        self.node_path = node_path
        self.program_args = program_args
        self.ts_mode = ts_mode
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

        # Node inspector prompt: "debug> " or just "debug>"
        self._prompt_re = re.compile(r'debug>\s*$')
        # Track current location
        self._current_file = ''
        self._current_line = 0
        self._current_function = ''

    def start_debugger(self):
        if self.attach_pid:
            cmd = [self.node_path, 'inspect', '-p', str(self.attach_pid)]
        else:
            cmd = [self.node_path, 'inspect']

            if self.ts_mode == 'ts-node':
                cmd = [self.node_path, 'inspect', '-r', 'ts-node/register']
            elif self.ts_mode == 'tsx':
                pass

            cmd.append(self.script_file)

            if self.program_args:
                import shlex
                try:
                    cmd.extend(shlex.split(self.program_args))
                except ValueError:
                    cmd.append(self.program_args)

        env = os.environ.copy()
        # Disable colours in Node inspector output for cleaner parsing
        env['NODE_DISABLE_COLORS'] = '1'
        env['NO_COLOR'] = '1'

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
            env=env,
        )
        time.sleep(0.5)
        if self.proc.poll() is not None:
            rc = self.proc.returncode
            raise RuntimeError(
                f"node inspect exited immediately (code {rc}). "
                f"Check your Node.js installation and script path."
            )
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        # Wait for the initial prompt — node inspect starts paused at line 1
        initial = self._collect_output(timeout=15)
        # Parse initial break location
        loc = self._parse_break_location(initial)
        if loc['file']:
            self._current_file = loc['file']
            self._current_line = loc['line']
            self._current_function = loc['function']
        self.is_paused = True

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
        """Collect output until we see the debug> prompt."""
        deadline = time.time() + timeout
        lines: List[str] = []
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
            # Also check for program exit
            if self._is_exit(text):
                return text
            time.sleep(0.1)
        return '\n'.join(lines)

    def _wait_prompt(self, timeout: float = 10.0):
        self._collect_output(timeout=timeout)

    def _get_new_stdout(self) -> str:
        items = self._program_output[self._last_out_pos:]
        self._last_out_pos = len(self._program_output)
        return '\n'.join(items)[:5000]


    def _parse_break_location(self, text: str) -> dict:
        """Parse node inspector output to extract current break location.

        Node inspector prints locations like:
            break in app.js:42
            break in src/index.js:10
        followed by source context with ``>`` marking the current line:
            40 function foo() {
            41   let x = 1;
           >42   let y = x + 2;
            43   return y;
        """
        loc = {"file": "", "line": 0, "function": "", "code_context": ""}

        # Pattern: "break in <file>:<line>"
        m = re.search(r'break in\s+(.+?):(\d+)', text)
        if m:
            loc['file'] = m.group(1).strip()
            loc['line'] = int(m.group(2))
            self._current_file = loc['file']
            self._current_line = loc['line']

        # Extract code context from the ">" marked line
        for sl in text.split('\n'):
            # Lines look like: " >42   let y = x + 2;"  or  ">42   let y = x + 2;"
            ctx_m = re.match(r'\s*>?\s*(\d+)\s+(.*)', sl)
            if sl.strip().startswith('>'):
                # This is the current line
                code_m = re.match(r'\s*>\s*\d+\s+(.*)', sl)
                if code_m:
                    loc['code_context'] = code_m.group(1).strip()
                    break

        # Try to detect function name from the source context above
        for sl in text.split('\n'):
            fn_m = re.search(r'function\s+(\w+)', sl)
            if fn_m:
                loc['function'] = fn_m.group(1)
                self._current_function = loc['function']
                break
            arrow_m = re.search(r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\(|=>)', sl)
            if arrow_m:
                loc['function'] = arrow_m.group(1)
                self._current_function = loc['function']
                break

        return loc

    def _parse_backtrace(self, text: str) -> List[dict]:
        """Parse ``bt`` (backtrace) output.

        Format:
            #0 functionName file.js:42:10
            #1 anotherFunc file.js:20:5
            #2 Module._compile ...
        """
        frames = []
        for line in text.split('\n'):
            line = line.strip()
            # Pattern: #N <function> <file>:<line>:<col>
            m = re.match(r'#(\d+)\s+(.*?)\s+(.+?):(\d+):(\d+)', line)
            if m:
                idx = int(m.group(1))
                func = m.group(2).strip()
                file_ = m.group(3).strip()
                line_no = int(m.group(4))

                frames.append({
                    "frame_index": idx,
                    "file": file_,
                    "line": line_no,
                    "function": func,
                    "code_context": "",
                })
                continue
            # Simpler pattern: #N <file>:<line>:<col>  (anonymous)
            m2 = re.match(r'#(\d+)\s+(.+?):(\d+):(\d+)', line)
            if m2:
                idx = int(m2.group(1))
                file_ = m2.group(2).strip()
                line_no = int(m2.group(3))
                frames.append({
                    "frame_index": idx,
                    "file": file_,
                    "line": line_no,
                    "function": "(anonymous)",
                    "code_context": "",
                })
        return frames

    def _parse_exec_result(self, text: str) -> str:
        """Parse the result of an ``exec(...)`` command.

        The output is the evaluated value printed directly.  Remove any
        leading/trailing prompt artefacts.
        """
        lines = []
        for line in text.split('\n'):
            stripped = line.strip()
            # Skip prompt lines and empty lines
            if stripped == 'debug>' or not stripped:
                continue
            # Skip the echo of the command itself
            if stripped.startswith('exec(') or stripped.startswith("exec '"):
                continue
            # Strip "debug> " prefix that sometimes leaks into output
            if stripped.startswith('debug>'):
                stripped = stripped[len('debug>'):].strip()
                if not stripped:
                    continue
            lines.append(stripped)
        return '\n'.join(lines).strip()

    def _parse_locals_from_exec(self, text: str) -> dict:
        """Parse a JSON-like locals dump obtained via ``exec(...)`` calls.

        We send expressions like ``exec('typeof x')`` and ``exec('x')`` for
        known variable names.  This method handles single-variable output.
        """
        result = {}
        # The output is raw text; try to parse key=value or JSON
        text = text.strip()
        if not text or text == 'undefined':
            return result

        # Try JSON parse first
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    result[k] = {
                        "type": type(v).__name__,
                        "value": str(v),
                        "repr": repr(v),
                    }
                return result
        except (json.JSONDecodeError, ValueError):
            pass

        return result


    def cmd_start(self, args_str: str = "") -> dict:
        if self.is_started:
            return self._error("Program already started. Use 'continue' instead.")
        self.is_started = True

        # node inspect starts paused at line 1; 'cont' resumes execution
        self._send('c')
        output = self._collect_output(timeout=30)

        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished.", stdout=output)

        self.is_paused = True
        loc = self._parse_break_location(output)
        return {
            "status": "paused", "command": "start",
            "message": self._format_stop_message(output),
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": self._get_new_stdout(), "stderr_new": "",
        }

    def cmd_continue(self) -> dict:
        self._send('c')
        output = self._collect_output(timeout=60)
        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished.", stdout=output)
        self.is_paused = True
        loc = self._parse_break_location(output)
        return {
            "status": "paused", "command": "continue",
            "message": self._format_stop_message(output),
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": self._get_new_stdout(), "stderr_new": "",
        }

    def cmd_step_in(self) -> dict:
        self._send('s')
        output = self._collect_output(timeout=30)
        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished.", stdout=output)
        loc = self._parse_break_location(output)
        return {
            "status": "paused", "command": "step_in",
            "message": self._format_stop_message(output),
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_step_over(self) -> dict:
        self._send('n')
        output = self._collect_output(timeout=30)
        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished.", stdout=output)
        loc = self._parse_break_location(output)
        return {
            "status": "paused", "command": "step_over",
            "message": self._format_stop_message(output),
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_step_out(self) -> dict:
        self._send('o')
        output = self._collect_output(timeout=30)
        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished.", stdout=output)
        loc = self._parse_break_location(output)
        return {
            "status": "paused", "command": "step_out",
            "message": self._format_stop_message(output),
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_run_to_line(self, line: int) -> dict:
        file_arg = self._current_file or os.path.basename(self.script_file)
        self._send(f"sb('{file_arg}', {line})")
        self._collect_output(timeout=5)
        self._send('c')
        output = self._collect_output(timeout=60)
        # Clear the temporary breakpoint
        self._send(f"cb('{file_arg}', {line})")
        self._collect_output(timeout=3)

        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished.", stdout=output)

        loc = self._parse_break_location(output)
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
                "Usage: b <line> or b <file>:<line>")

        location = parts[0]

        try:
            line = int(location)
            # Line number only — set in current file
            bp_cmd = f'sb({line})'
        except ValueError:
            if ':' in location:
                # file:line format
                file_part, line_part = location.rsplit(':', 1)
                try:
                    line_no = int(line_part)
                    bp_cmd = f"sb('{file_part}', {line_no})"
                except ValueError:
                    return self._error(
                        f"Invalid breakpoint format: {location}. "
                        f"Use <line> or <file>:<line>")
            else:
                return self._error(
                    f"Invalid breakpoint format: {location}. "
                    f"Use <line> or <file>:<line>")

        self._send(bp_cmd)
        output = self._collect_output(timeout=5)

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
            return self._error("Usage: rb <line> or rb <file>:<line>")

        try:
            line = int(location)
            # Line number only — clear in current file
            file_arg = self._current_file or os.path.basename(self.script_file)
            self._send(f"cb('{file_arg}', {line})")
        except ValueError:
            if ':' in location:
                file_part, line_part = location.rsplit(':', 1)
                try:
                    line_no = int(line_part)
                    self._send(f"cb('{file_part}', {line_no})")
                except ValueError:
                    return self._error(
                        f"Invalid breakpoint format: {location}")
            else:
                return self._error(
                    f"Invalid breakpoint format: {location}")

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
            "function": self._current_function,
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

        # Use exec('expression') syntax for Node inspector
        safe_expr = expr.strip().replace("'", "\\'")
        self._send(f"exec('{safe_expr}')")
        output = self._collect_output(timeout=10)

        value = self._parse_exec_result(output)

        return {
            "status": "paused", "command": "evaluate",
            "message": f"{expr.strip()} = {value}",
            "eval_result": {"type": "expression", "value": value, "repr": value},
            "current_location": None,
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
        self._send(f'list({context})')
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

    def cmd_quit(self) -> dict:
        try:
            self._send('.exit')
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
        self._send('bt')
        output = self._collect_output(timeout=5)
        return self._parse_backtrace(output)

    def _get_locals(self) -> dict:
        """Get local variables by evaluating a helper expression.

        Node inspector doesn't have a built-in ``locals`` command like JDB,
        so we use ``exec`` to introspect the local scope via a helper
        that catches ReferenceErrors gracefully.
        """
        # Use exec to get a snapshot of common patterns.
        # We ask for the scope's own enumerable properties via a trick:
        # In the debug REPL, local variables are accessible directly.
        # We evaluate a helper that JSON-serialises all locals we can find.
        helper = (
            "(function() { "
            "try { "
            "  var _r = {}; "
            "  var _sc = typeof arguments !== 'undefined' ? arguments : {}; "
            "  return JSON.stringify(_r); "
            "} catch(e) { return '{}'; } "
            "})()"
        )
        self._send(f"exec('{helper.replace(chr(39), chr(92) + chr(39))}')")
        output = self._collect_output(timeout=5)
        result = self._parse_exec_result(output)

        # Attempt to parse as JSON
        locals_dict: dict = {}
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    locals_dict[k] = {
                        "type": type(v).__name__,
                        "value": str(v),
                        "repr": repr(v),
                    }
        except (json.JSONDecodeError, ValueError):
            pass

        # As a fallback, we try to get the scope via the backtrace context.
        # The Node inspector's exec can access locals directly in the paused
        # scope, but enumerating them programmatically is limited.  Return
        # whatever we managed to gather.
        return locals_dict

    def _is_exit(self, text: str) -> bool:
        return bool(re.search(
            r'Waiting for the debugger to disconnect|'
            r'process\.exit|'
            r'Program terminated|'
            r'Could not connect to',
            text, re.I,
        ))

    def _format_stop_message(self, output: str) -> str:
        lines = []
        for l in output.strip().split('\n'):
            cleaned = l.strip()
            # Remove backspace characters and prompt artefacts
            cleaned = cleaned.replace('\b', '')
            if cleaned and cleaned != 'debug>':
                lines.append(cleaned)
        return '\n'.join(lines[:8]) if lines else "Stopped."


class NodeDebugServer(BaseDebugServer):
    LANGUAGE = "Node.js"
    SCRIPT_NAME = "nodejs_debug_session.py"


def _resolve_target(target: str) -> Tuple[str, Optional[str]]:
    """Resolve the target script, handling TypeScript if needed.

    Returns:
        (script_to_debug, ts_mode)
        ts_mode is one of 'ts-node', 'compiled', or None.
    """
    abs_target = os.path.abspath(target)

    # If it's a directory, try to find the entry point
    if os.path.isdir(abs_target):
        # Check package.json
        entry = detect_package_main(abs_target)
        if entry and os.path.isfile(entry):
            abs_target = entry
        else:
            # Try index.js / index.ts
            for candidate_name in ('index.js', 'index.ts', 'index.mjs'):
                candidate = os.path.join(abs_target, candidate_name)
                if os.path.isfile(candidate):
                    abs_target = candidate
                    break
            else:
                raise FileNotFoundError(
                    f"No entry point found in {target}. "
                    f"Specify a .js or .ts file directly.")

    if not os.path.isfile(abs_target):
        raise FileNotFoundError(f"File not found: {target}")

    ext = os.path.splitext(abs_target)[1].lower()

    if ext in ('.ts', '.mts', '.cts'):
        # TypeScript file — determine strategy
        ts_node = shutil.which('ts-node')
        if not ts_node:
            local = os.path.join('.', 'node_modules', '.bin', 'ts-node')
            if sys.platform == 'win32':
                local += '.cmd'
            if os.path.isfile(local):
                ts_node = os.path.abspath(local)

        if ts_node:
            print(f"TypeScript detected. Using ts-node register hook.")
            return (abs_target, 'ts-node')

        # Fallback: compile first
        print(f"TypeScript detected. Compiling to JavaScript...")
        try:
            js_file, msg = compile_typescript(abs_target)
            print(msg)
            return (js_file, 'compiled')
        except (FileNotFoundError, RuntimeError) as e:
            raise RuntimeError(
                f"Cannot debug TypeScript file: {e}\n"
                f"Install ts-node (npm i -g ts-node) or tsc (npm i -g typescript).")

    return (abs_target, None)

