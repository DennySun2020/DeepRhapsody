"""Ruby debugging via rdbg."""

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


class RubyToolchainInfo:

    def __init__(self):
        self.platform_info = self._detect_platform()
        self.ruby_info = self._detect_ruby()
        self.debugger_info = self._detect_debugger()
        self.build_tools = self._detect_build_tools()

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

    def _detect_ruby(self) -> Optional[dict]:
        ruby = shutil.which('ruby')
        if not ruby:
            return None

        version = ''
        try:
            r = subprocess.run(
                [ruby, '--version'],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                version = r.stdout.strip().split('\n')[0]
        except Exception:
            pass

        return {
            'ruby': ruby,
            'version': version,
        }

    def _detect_debugger(self) -> Optional[dict]:
        rdbg = shutil.which('rdbg')

        # If not in PATH, try to locate via gem
        if not rdbg:
            gem = shutil.which('gem')
            if gem:
                try:
                    r = subprocess.run(
                        [gem, 'which', 'debug'],
                        capture_output=True, text=True, timeout=10,
                    )
                    if r.returncode == 0 and r.stdout.strip():
                        # gem which gives us the lib path; rdbg binary is
                        # typically in the same gem bin dir
                        pass
                except Exception:
                    pass

        version = ''
        if rdbg:
            try:
                r = subprocess.run(
                    [rdbg, '--version'],
                    capture_output=True, text=True, timeout=10,
                )
                output = r.stdout.strip() or r.stderr.strip()
                if output:
                    version = output.split('\n')[0]
            except Exception:
                pass

        # Check if debug gem is installed
        gem_installed = False
        gem = shutil.which('gem')
        if gem:
            try:
                r = subprocess.run(
                    [gem, 'list', 'debug'],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0 and 'debug' in r.stdout:
                    gem_installed = True
            except Exception:
                pass

        if rdbg:
            return {
                'rdbg': rdbg,
                'version': version,
                'gem_installed': gem_installed,
            }

        if gem_installed:
            return {
                'rdbg': None,
                'version': '',
                'gem_installed': True,
                'note': 'debug gem installed but rdbg not in PATH',
            }

        return None

    def _detect_build_tools(self) -> list:
        tools = []

        gem = shutil.which('gem')
        if gem:
            tools.append({
                'name': 'gem', 'path': gem,
                'version': self._tool_version(gem, '--version'),
            })

        bundle = shutil.which('bundle')
        if bundle:
            tools.append({
                'name': 'bundler', 'path': bundle,
                'version': self._tool_version(bundle, '--version'),
            })

        rake = shutil.which('rake')
        if rake:
            tools.append({
                'name': 'rake', 'path': rake,
                'version': self._tool_version(rake, '--version'),
            })

        # Detect project files
        cwd = os.getcwd()
        if os.path.isfile(os.path.join(cwd, 'Gemfile')):
            tools.append({'name': 'gemfile', 'path': os.path.join(cwd, 'Gemfile')})
        if os.path.isfile(os.path.join(cwd, '.ruby-version')):
            try:
                with open(os.path.join(cwd, '.ruby-version'), 'r') as f:
                    rv = f.read().strip()
                tools.append({'name': '.ruby-version', 'path': rv})
            except OSError:
                pass
        # Rails detection
        if os.path.isfile(os.path.join(cwd, 'config', 'application.rb')) or \
           os.path.isfile(os.path.join(cwd, 'bin', 'rails')):
            tools.append({'name': 'rails', 'path': cwd})

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
        if self.debugger_info and self.debugger_info.get('rdbg'):
            return {
                'debugger': {
                    'name': 'rdbg',
                    'path': self.debugger_info['rdbg'],
                },
                'note': 'rdbg (debug.gem, modern Ruby debugger)',
            }

        return {
            'debugger': None,
            'note': self._install_instructions(),
        }

    def _install_instructions(self) -> str:
        if not self.ruby_info:
            msg = 'No Ruby found. Install Ruby first:\n'
            if sys.platform == 'win32':
                msg += (
                    '  Option 1: Download from https://rubyinstaller.org/\n'
                    '  Option 2: winget install RubyInstallerTeam.Ruby\n'
                    '  Option 3: Use WSL and install via rbenv or rvm'
                )
            elif sys.platform == 'darwin':
                msg += (
                    '  brew install ruby\n'
                    '  or use rbenv: brew install rbenv && rbenv install 3.3.0'
                )
            else:
                msg += (
                    '  Debian/Ubuntu: apt install ruby-full\n'
                    '  RHEL/CentOS:   yum install ruby ruby-devel\n'
                    '  Arch:          pacman -S ruby\n'
                    '  or use rbenv:  https://github.com/rbenv/rbenv'
                )
            return msg

        msg = 'rdbg (debug.gem) not found. Install it:\n'
        msg += '  gem install debug\n'
        msg += '  (bundled with Ruby 3.2+; for older Ruby, gem install is required)'
        return msg

    def to_dict(self) -> dict:
        return {
            'platform': self.platform_info,
            'ruby': self.ruby_info,
            'debugger': self.debugger_info,
            'build_tools': self.build_tools,
            'recommendation': self.recommend(),
        }


class RdbgDebugger(DebugResponseMixin):

    def __init__(self, script: str, debugger_path: str = "rdbg",
                 program_args: Optional[str] = None,
                 use_bundler: bool = False,
                 attach_pid: Optional[int] = None):
        self.script = script
        self.debugger_path = debugger_path
        self.program_args = program_args
        self.use_bundler = use_bundler
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

        # rdbg prompt patterns: (rdbg), (rdbg:command), (ruby)
        self._prompt_re = re.compile(
            r'\(rdbg(?::\w+)?\)\s*$|\(ruby\)\s*$'
        )
        # Track current location
        self._current_file = ''
        self._current_line = 0
        self._current_function = ''

    def start_debugger(self):
        cmd = []

        if self.attach_pid:
            cmd.append(self.debugger_path)
            cmd.extend(['-A', str(self.attach_pid)])
        else:
            if self.use_bundler:
                bundle = shutil.which('bundle')
                if bundle:
                    cmd.extend([bundle, 'exec'])

            cmd.append(self.debugger_path)
            cmd.extend(['-c', '--', 'ruby'])
            cmd.append(self.script)

            if self.program_args:
                import shlex
                try:
                    cmd.extend(shlex.split(self.program_args))
                except ValueError:
                    cmd.append(self.program_args)

        env = os.environ.copy()
        # Disable pager and color for clean output
        env['RUBY_DEBUG_NO_COLOR'] = '1'

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
                f"rdbg exited immediately (code {rc}). "
                f"Check your Ruby installation and that debug gem is installed."
            )
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        # Wait for the initial prompt (rdbg starts paused at first line)
        initial = self._collect_output(timeout=10)
        self.is_paused = True
        # Parse initial stop location
        self._parse_stop_location(initial)

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
            # Also check if process has exited
            if self.proc.poll() is not None:
                # Drain remaining
                with self._lock:
                    buf = list(self._output_buffer)
                    self._output_buffer.clear()
                for line in buf:
                    lines.append(line)
                return '\n'.join(lines)
            time.sleep(0.1)
        return '\n'.join(lines)

    def _wait_prompt(self, timeout: float = 10.0):
        self._collect_output(timeout=timeout)

    def _get_new_stdout(self) -> str:
        items = self._program_output[self._last_out_pos:]
        self._last_out_pos = len(self._program_output)
        return '\n'.join(items)[:5000]


    def _parse_stop_location(self, text: str) -> dict:
        """Parse rdbg stop output to extract current location.

        rdbg output on stop:
            [1, 10] in /path/to/file.rb
                 1| def hello
                 2|   name = "World"
            =>   3|   puts "Hello, #{name}!"
                 4| end
            =>#0    Object#hello at /path/to/file.rb:3
              #1    <main> at /path/to/file.rb:6
        """
        loc = {"file": "", "line": 0, "function": "", "code_context": ""}

        # Parse file from header: [start, end] in /path/to/file.rb
        file_match = re.search(r'\[\d+,\s*\d+\]\s+in\s+(.+)', text)
        if file_match:
            loc['file'] = file_match.group(1).strip()

        # Parse current line marker: =>   N| code
        arrow_match = re.search(r'=>\s*(\d+)\|\s*(.*)', text)
        if arrow_match:
            loc['line'] = int(arrow_match.group(1))
            loc['code_context'] = arrow_match.group(2).strip()

        # Parse top frame: =>#0  ClassName#method at /path:line
        frame_match = re.search(
            r'=>?#0\s+(.*?)\s+at\s+(.+?):(\d+)', text
        )
        if frame_match:
            loc['function'] = frame_match.group(1).strip()
            if not loc['file']:
                loc['file'] = frame_match.group(2).strip()
            if not loc['line']:
                loc['line'] = int(frame_match.group(3))

        # Update tracked state
        if loc['file']:
            self._current_file = loc['file']
        if loc['line']:
            self._current_line = loc['line']
        if loc['function']:
            self._current_function = loc['function']

        return loc

    def _parse_backtrace(self, text: str) -> List[dict]:
        """Parse rdbg backtrace output.

        Format:
            =>#0    Object#hello at /path/to/file.rb:3
              #1    <main> at /path/to/file.rb:6
        """
        frames = []
        for line in text.split('\n'):
            line = line.strip()
            m = re.match(
                r'=>?#(\d+)\s+(.*?)\s+at\s+(.+?):(\d+)', line
            )
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
        return frames

    def _parse_locals(self, text: str) -> dict:
        """Parse rdbg 'info locals' / 'info args' output.

        Format (one per line):
            name = "value"
            count = 42
            items = ["a", "b", "c"]
        """
        result = {}
        for line in text.split('\n'):
            line = line.strip()
            if not line:
                continue
            # Skip headers and prompts
            if line.startswith('#') or line.startswith('=>') or \
               line.startswith('[') or line.startswith('('):
                continue
            m = re.match(r'(%\w+|\w+)\s*=\s*(.*)', line)
            if m:
                name = m.group(1)
                value = m.group(2).strip()
                type_ = self._infer_ruby_type(value)
                result[name] = {
                    "type": type_,
                    "value": value,
                    "repr": value,
                }
        return result

    @staticmethod
    def _infer_ruby_type(value: str) -> str:
        if value.startswith('"'):
            return 'String'
        if value == 'nil':
            return 'NilClass'
        if value in ('true', 'false'):
            return 'Boolean'
        if re.match(r'^-?\d+$', value):
            return 'Integer'
        if re.match(r'^-?\d+\.\d+', value):
            return 'Float'
        if value.startswith('['):
            return 'Array'
        if value.startswith('{'):
            return 'Hash'
        if value.startswith(':'):
            return 'Symbol'
        if value.startswith('#<'):
            # #<ClassName:0xADDR ...>
            m = re.match(r'#<(\w[\w:]*)', value)
            if m:
                return m.group(1)
            return 'Object'
        return 'unknown'

    def _parse_breakpoint_set(self, text: str) -> str:
        """Parse breakpoint set confirmation.

        Format:
            #0  BP - Line  /path/to/file.rb:42
        """
        m = re.search(r'#(\d+)\s+BP\s+-\s+(\S+)\s+(.*)', text)
        if m:
            bp_id = m.group(1)
            bp_type = m.group(2)
            bp_loc = m.group(3).strip()
            return f"Breakpoint #{bp_id} set ({bp_type}) at {bp_loc}"
        return text.strip() if text.strip() else "Breakpoint set."


    def cmd_start(self, args_str: str = "") -> dict:
        if self.is_started:
            return self._error("Program already started. Use 'continue' instead.")
        self.is_started = True

        self._send('continue')
        output = self._collect_output(timeout=30)

        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished.", stdout=output)

        self.is_paused = True
        loc = self._parse_stop_location(output)
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
        loc = self._parse_stop_location(output)
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
            return self._completed("Program finished.", stdout=output)
        loc = self._parse_stop_location(output)
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
            return self._completed("Program finished.", stdout=output)
        loc = self._parse_stop_location(output)
        return {
            "status": "paused", "command": "step_over",
            "message": self._format_stop_message(output),
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_step_out(self) -> dict:
        self._send('finish')
        output = self._collect_output(timeout=30)
        if self._is_exit(output):
            self.is_finished = True
            return self._completed("Program finished.", stdout=output)
        loc = self._parse_stop_location(output)
        return {
            "status": "paused", "command": "step_out",
            "message": self._format_stop_message(output),
            "current_location": loc,
            "call_stack": self._get_call_stack(),
            "local_variables": self._get_locals(),
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_set_breakpoint(self, args: str) -> dict:
        """Set a breakpoint.

        Supported formats:
            b <line>             - line in current file
            b <file>:<line>      - file:line
            b <Class>#<method>   - method breakpoint
        """
        location = args.strip()
        if not location:
            return self._error(
                "Usage: b <line> or b <file>:<line> or b <Class>#<method>")

        self._send(f'break {location}')
        output = self._collect_output(timeout=5)

        msg = self._parse_breakpoint_set(output)
        return {
            "status": "paused" if self.is_paused else "running",
            "command": f"set_breakpoint {args}",
            "message": msg,
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_remove_breakpoint(self, args: str) -> dict:
        bp_id = args.strip()
        if not bp_id:
            return self._error("Usage: rb <breakpoint_id>")

        self._send(f'delete {bp_id}')
        output = self._collect_output(timeout=3)
        return {
            "status": "paused" if self.is_paused else "running",
            "command": f"remove_breakpoint {args}",
            "message": output.strip() or f"Breakpoint {bp_id} deleted.",
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_list_breakpoints(self) -> dict:
        self._send('info breakpoints')
        output = self._collect_output(timeout=5)
        return {
            "status": "paused" if self.is_paused else "running",
            "command": "breakpoints",
            "message": output.strip() or "No breakpoints set.",
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

        self._send(f'p {expr.strip()}')
        output = self._collect_output(timeout=10)

        # rdbg prints the result directly
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
            "message": output.strip() or "(no source available)",
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
            self._send('quit!')
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
        # Get local variables
        self._send('info locals')
        locals_output = self._collect_output(timeout=5)
        locals_ = self._parse_locals(locals_output)

        # Get method arguments (merge into locals)
        self._send('info args')
        args_output = self._collect_output(timeout=5)
        args_ = self._parse_locals(args_output)
        locals_.update(args_)

        return locals_

    def _is_exit(self, text: str) -> bool:
        if self.proc.poll() is not None:
            return True
        return bool(re.search(
            r'exit|program\s+terminated|process\s+exited|'
            r'the\s+program\s+finished|script\s+has\s+finished|'
            r'No\s+threads|debugger\s+finished',
            text, re.I,
        ))

    def _format_stop_message(self, output: str) -> str:
        lines = [l.strip() for l in output.strip().split('\n') if l.strip()]
        return '\n'.join(lines[:8]) if lines else "Stopped."


class RubyDebugServer(BaseDebugServer):
    LANGUAGE = "Ruby"
    SCRIPT_NAME = "ruby_debug_session.py"


def _detect_bundler_context(script_path: str) -> bool:
    search_dir = os.path.dirname(os.path.abspath(script_path))
    for _ in range(10):
        if os.path.isfile(os.path.join(search_dir, 'Gemfile')):
            return True
        parent = os.path.dirname(search_dir)
        if parent == search_dir:
            break
        search_dir = parent
    return False

