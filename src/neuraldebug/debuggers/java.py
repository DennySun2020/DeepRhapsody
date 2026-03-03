"""Java debugging via JDB."""

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from debug_common import (
    BaseDebugServer,
    DebugResponseMixin, error_response, completed_response,
    find_repo_root,
)


class JavaToolchainInfo:

    def __init__(self):
        self.platform_info = self._detect_platform()
        self.jdk_info = self._detect_jdk()
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

    def _detect_jdk(self) -> Optional[dict]:
        java = shutil.which('java')
        javac = shutil.which('javac')
        jdb = shutil.which('jdb')

        if not java:
            return None

        version = ''
        try:
            r = subprocess.run(
                [java, '-version'],
                capture_output=True, text=True, timeout=10,
            )
            # java -version outputs to stderr
            output = r.stderr or r.stdout
            if output:
                version = output.strip().split('\n')[0]
        except Exception:
            pass

        java_home = os.environ.get('JAVA_HOME', '')
        if not java_home:
            # Try to infer from java location
            java_real = os.path.realpath(java)
            bin_dir = os.path.dirname(java_real)
            candidate = os.path.dirname(bin_dir)
            if os.path.isdir(os.path.join(candidate, 'lib')):
                java_home = candidate

        return {
            'java': java,
            'javac': javac,
            'jdb': jdb,
            'java_home': java_home,
            'version': version,
            'has_debugger': jdb is not None,
        }

    def _detect_build_tools(self) -> list:
        tools = []

        mvn = shutil.which('mvn')
        if mvn:
            tools.append({
                'name': 'maven', 'path': mvn,
                'version': self._tool_version(mvn, '--version'),
            })

        gradle = shutil.which('gradle')
        if gradle:
            tools.append({
                'name': 'gradle', 'path': gradle,
                'version': self._tool_version(gradle, '--version'),
            })

        # Gradle wrapper
        gradlew = './gradlew' if sys.platform != 'win32' else '.\\gradlew.bat'
        if os.path.isfile(gradlew):
            tools.append({
                'name': 'gradlew', 'path': os.path.abspath(gradlew),
                'version': '',
            })

        ant = shutil.which('ant')
        if ant:
            tools.append({
                'name': 'ant', 'path': ant,
                'version': self._tool_version(ant, '-version'),
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
        if self.jdk_info and self.jdk_info.get('has_debugger'):
            return {
                'debugger': {
                    'name': 'jdb',
                    'path': self.jdk_info['jdb'],
                },
                'note': 'JDB (Java Debugger, bundled with JDK)',
            }

        return {
            'debugger': None,
            'note': self._install_instructions(),
        }

    def _install_instructions(self) -> str:
        msg = 'No JDK found. JDB (Java Debugger) comes with the JDK.\n'
        if sys.platform == 'win32':
            msg += (
                '  Option 1: winget install Microsoft.OpenJDK.21\n'
                '  Option 2: Download from https://adoptium.net/\n'
                '  Option 3: Install Oracle JDK from https://www.oracle.com/java/'
            )
        elif sys.platform == 'darwin':
            msg += (
                '  brew install openjdk\n'
                '  or download from https://adoptium.net/'
            )
        else:
            msg += (
                '  Debian/Ubuntu: apt install default-jdk\n'
                '  RHEL/CentOS:   yum install java-21-openjdk-devel\n'
                '  Arch:          pacman -S jdk-openjdk'
            )
        return msg

    def to_dict(self) -> dict:
        return {
            'platform': self.platform_info,
            'jdk': self.jdk_info,
            'build_tools': self.build_tools,
            'recommendation': self.recommend(),
        }


def compile_java(
    source_file: str,
    classpath: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> Tuple[str, str]:
    """Compile a Java source file with debug info.

    Args:
        source_file: Path to .java file.
        classpath: Optional classpath.
        output_dir: Output directory for .class files.

    Returns:
        (class_name, human_message)
    """
    javac = shutil.which('javac')
    if not javac:
        raise FileNotFoundError(
            'javac not found. Install a JDK.')

    src = os.path.abspath(source_file)
    if not os.path.isfile(src):
        raise FileNotFoundError(f'Source file not found: {source_file}')

    if output_dir is None:
        output_dir = os.path.dirname(src)

    cmd = [javac, '-g', '-d', output_dir, src]
    if classpath:
        cmd.extend(['-cp', classpath])

    print(f'Compiling: {" ".join(cmd)}')
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    if result.returncode != 0:
        error_msg = (result.stderr or result.stdout)[:4000]
        raise RuntimeError(
            f'Compilation failed (exit {result.returncode}):\n{error_msg}')

    # Determine class name from source file
    class_name = _extract_class_name(src)
    msg = f'Compiled {os.path.basename(src)} -> {class_name}.class'
    if result.stderr.strip():
        msg += f'\nWarnings:\n{result.stderr[:2000]}'

    return (class_name, msg)


def _extract_class_name(source_file: str) -> str:
    try:
        with open(source_file, 'r', errors='replace') as f:
            content = f.read()

        # Look for package declaration
        package = ''
        pkg_match = re.search(r'^\s*package\s+([\w.]+)\s*;', content, re.MULTILINE)
        if pkg_match:
            package = pkg_match.group(1) + '.'

        # Look for public class
        cls_match = re.search(r'public\s+class\s+(\w+)', content)
        if cls_match:
            return package + cls_match.group(1)
    except OSError:
        pass

    # Fallback: use filename
    return os.path.splitext(os.path.basename(source_file))[0]


def build_maven_project(project_dir: str) -> Tuple[str, str]:
    mvn = shutil.which('mvn')
    if not mvn:
        raise FileNotFoundError('Maven (mvn) not found.')

    cmd = [mvn, 'compile', '-q']
    print(f'Building: {" ".join(cmd)}')
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300,
        cwd=project_dir,
    )
    if result.returncode != 0:
        error_msg = (result.stderr or result.stdout)[:4000]
        raise RuntimeError(f'Maven build failed:\n{error_msg}')

    classes_dir = os.path.join(project_dir, 'target', 'classes')
    return (classes_dir, 'Maven build successful')


def build_gradle_project(project_dir: str) -> Tuple[str, str]:
    gradlew = os.path.join(project_dir, 'gradlew')
    if sys.platform == 'win32':
        gradlew = os.path.join(project_dir, 'gradlew.bat')
    if not os.path.isfile(gradlew):
        gradlew = shutil.which('gradle')
    if not gradlew:
        raise FileNotFoundError('Gradle not found.')

    cmd = [gradlew, 'compileJava', '-q']
    print(f'Building: {" ".join(cmd)}')
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300,
        cwd=project_dir,
    )
    if result.returncode != 0:
        error_msg = (result.stderr or result.stdout)[:4000]
        raise RuntimeError(f'Gradle build failed:\n{error_msg}')

    classes_dir = os.path.join(project_dir, 'build', 'classes', 'java', 'main')
    return (classes_dir, 'Gradle build successful')


class JdbDebugger(DebugResponseMixin):

    def __init__(self, main_class: str, debugger_path: str = "jdb",
                 classpath: Optional[str] = None,
                 source_paths: Optional[List[str]] = None,
                 program_args: Optional[str] = None,
                 is_jar: bool = False,
                 attach_pid: Optional[int] = None):
        self.main_class = main_class
        self.debugger_path = debugger_path
        self.classpath = classpath
        self.source_paths = source_paths or []
        self.program_args = program_args
        self.is_jar = is_jar
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

        self._prompt_re = re.compile(r'(?:main\[\d+\]|>\s*$|\w+\[\d+\]\s*$)')
        # Track current location for source file resolution
        self._current_class = ''
        self._current_file = ''
        self._current_line = 0

    def start_debugger(self):
        cmd = [self.debugger_path]

        if self.attach_pid:
            cmd.extend(['-connect',
                        f'com.sun.jdi.ProcessAttach:pid={self.attach_pid}'])
            if self.source_paths:
                cmd.extend(['-sourcepath', os.pathsep.join(self.source_paths)])
        else:
            if self.classpath:
                cmd.extend(['-classpath', self.classpath])

            if self.source_paths:
                cmd.extend(['-sourcepath', os.pathsep.join(self.source_paths)])

            if self.is_jar:
                if not self.classpath:
                    cmd.extend(['-classpath', self.main_class])
                extracted = self._extract_main_from_jar(self.main_class)
                if extracted:
                    cmd.append(extracted)
                else:
                    print(f"Warning: Could not extract Main-Class from {self.main_class}",
                          file=sys.stderr)
                    cmd.append(self.main_class)
            else:
                cmd.append(self.main_class)

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
        time.sleep(0.5)
        if self.proc.poll() is not None:
            rc = self.proc.returncode
            raise RuntimeError(
                f"JDB exited immediately (code {rc}). "
                f"Check your Java installation and classpath."
            )
        self._reader = threading.Thread(target=self._read_output, daemon=True)
        self._reader.start()
        self._wait_prompt(timeout=10)

    @staticmethod
    def _extract_main_from_jar(jar_path: str) -> Optional[str]:
        try:
            import zipfile
            with zipfile.ZipFile(jar_path) as zf:
                manifest = zf.read('META-INF/MANIFEST.MF').decode('utf-8')
                for line in manifest.split('\n'):
                    if line.strip().startswith('Main-Class:'):
                        return line.split(':', 1)[1].strip()
        except Exception:
            pass
        return None

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
            time.sleep(0.1)
        return '\n'.join(lines)

    def _wait_prompt(self, timeout: float = 10.0):
        self._collect_output(timeout=timeout)

    def _get_new_stdout(self) -> str:
        items = self._program_output[self._last_out_pos:]
        self._last_out_pos = len(self._program_output)
        return '\n'.join(items)[:5000]


    def _parse_location(self, text: str) -> dict:
        """Parse JDB output to extract current location.

        JDB prints locations like:
            Breakpoint hit: "thread=main", com.example.Main.main(), line=42 bci=0
            Step completed: "thread=main", com.example.Main.process(), line=15 bci=12
        """
        loc = {"file": "", "line": 0, "function": "", "code_context": ""}

        # Pattern: class.method(), line=N
        m = re.search(r'([\w.$]+)\.(\w+)\(\),?\s*line=(\d+)', text)
        if m:
            full_class = m.group(1)
            method = m.group(2)
            line_no = int(m.group(3))
            loc['function'] = f"{full_class}.{method}"
            loc['line'] = line_no
            # Convert class name to file path
            loc['file'] = self._class_to_file(full_class)
            self._current_class = full_class
            self._current_file = loc['file']
            self._current_line = line_no

        # Try to get source context
        for sl in text.split('\n'):
            stripped = sl.strip()
            # JDB source lines look like: "42    int x = 5;"
            m2 = re.match(r'^(\d+)\s+(.+)', stripped)
            if m2 and not stripped.startswith('Breakpoint') and \
               not stripped.startswith('Step') and \
               not stripped.startswith('>'):
                loc['code_context'] = m2.group(2).strip()
                break

        return loc

    def _class_to_file(self, class_name: str) -> str:
        # com.example.Main -> com/example/Main.java
        parts = class_name.replace('$', '.').split('.')
        # Use only the outer class for the file name
        file_path = os.sep.join(parts) + '.java'
        # Try to resolve against source paths
        for sp in self.source_paths:
            candidate = os.path.join(sp, file_path)
            if os.path.isfile(candidate):
                return os.path.relpath(candidate, sp)
        return file_path

    def _parse_stack(self, text: str) -> List[dict]:
        """Parse JDB 'where' output.

        Format:
          [1] com.example.Main.process (Main.java:15)
          [2] com.example.Main.main (Main.java:42)
        """
        frames = []
        for line in text.split('\n'):
            line = line.strip()
            m = re.match(r'\[(\d+)\]\s+([\w.$]+)\s*\(([^)]*)\)', line)
            if m:
                idx = int(m.group(1))
                func = m.group(2)
                loc_info = m.group(3)

                file_ = ''
                line_no = 0
                loc_m = re.match(r'(.+):(\d+)', loc_info)
                if loc_m:
                    file_ = loc_m.group(1)
                    line_no = int(loc_m.group(2))

                frames.append({
                    "frame_index": idx,
                    "file": file_,
                    "line": line_no,
                    "function": func,
                    "code_context": "",
                })
        return frames

    def _parse_locals(self, text: str) -> dict:
        """Parse JDB 'locals' output.

        Format:
          Method arguments:
            args = instance of java.lang.String[0]
          Local variables:
            x = 42
            name = "hello"
        """
        result = {}
        for line in text.split('\n'):
            line = line.strip()
            if not line or line.startswith('Method') or line.startswith('Local'):
                continue
            m = re.match(r'(\w+)\s*=\s*(.*)', line)
            if m:
                name = m.group(1)
                value = m.group(2).strip()
                # Determine type from value format
                type_ = 'unknown'
                if value.startswith('"'):
                    type_ = 'String'
                elif value == 'null':
                    type_ = 'null'
                elif re.match(r'^-?\d+$', value):
                    type_ = 'int'
                elif re.match(r'^-?\d+\.\d+', value):
                    type_ = 'double'
                elif value in ('true', 'false'):
                    type_ = 'boolean'
                elif value.startswith('instance of'):
                    type_ = value.replace('instance of ', '').split('(')[0].strip()
                result[name] = {
                    "type": type_,
                    "value": value,
                    "repr": value,
                }
        return result


    def cmd_start(self, args_str: str = "") -> dict:
        if self.is_started:
            return self._error("Program already started. Use 'continue' instead.")
        self.is_started = True

        self._send('run')
        output = self._collect_output(timeout=30)

        if 'application exited' in output.lower() or \
           'the application has been disconnected' in output.lower():
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
        self._send('cont')
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
        self._send('step up')
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
        # JDB doesn't have a native run-to-line; use temp breakpoint
        class_name = self._current_class or self.main_class
        self._send(f'stop at {class_name}:{line}')
        bp_output = self._collect_output(timeout=5)
        self._send('cont')
        output = self._collect_output(timeout=60)
        # Clear the temp breakpoint
        self._send(f'clear {class_name}:{line}')
        self._collect_output(timeout=3)
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
                "Usage: b <line> or b <class>:<line> or b <class>.<method>")

        location = parts[0]

        # Determine the JDB command
        try:
            line = int(location)
            # Just a line number -- use current class
            class_name = self._current_class or self.main_class
            bp_cmd = f'stop at {class_name}:{line}'
        except ValueError:
            if ':' in location:
                # class:line format
                bp_cmd = f'stop at {location}'
            elif '.' in location:
                # class.method format
                bp_cmd = f'stop in {location}'
            else:
                # Assume it's a method of the main class
                bp_cmd = f'stop in {self.main_class}.{location}'

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
            return self._error("Usage: rb <class>:<line> or rb <class>.<method>")

        try:
            line = int(location)
            class_name = self._current_class or self.main_class
            self._send(f'clear {class_name}:{line}')
        except ValueError:
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
        # JDB doesn't have a direct "list breakpoints" but we can use 'clear'
        # with no args to list deferrable breakpoints, or check output
        self._send('clear')
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
            "function": "",
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

        self._send(f'eval {expr.strip()}')
        output = self._collect_output(timeout=10)

        # Parse JDB eval output: "expr = value"
        value = output.strip()
        m = re.search(r'=\s*(.*)', output)
        if m:
            value = m.group(1).strip()

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


    def _get_call_stack(self) -> List[dict]:
        self._send('where')
        output = self._collect_output(timeout=5)
        return self._parse_stack(output)

    def _get_locals(self) -> dict:
        self._send('locals')
        output = self._collect_output(timeout=5)
        return self._parse_locals(output)

    def _is_exit(self, text: str) -> bool:
        return bool(re.search(
            r'application exited|disconnected|VM terminated|'
            r'The application has been disconnected',
            text, re.I,
        ))

    def _format_stop_message(self, output: str) -> str:
        lines = [l.strip() for l in output.strip().split('\n') if l.strip()]
        return '\n'.join(lines[:5]) if lines else "Stopped."


class JavaDebugServer(BaseDebugServer):
    LANGUAGE = "Java"
    SCRIPT_NAME = "java_debug_session.py"
    HAS_RUN_TO_LINE = False


def _detect_java_build(project_dir: str) -> Optional[dict]:
    project = os.path.abspath(project_dir)

    if os.path.isfile(os.path.join(project, 'pom.xml')):
        return {'name': 'maven', 'marker': 'pom.xml'}
    if os.path.isfile(os.path.join(project, 'build.gradle')) or \
       os.path.isfile(os.path.join(project, 'build.gradle.kts')):
        return {'name': 'gradle', 'marker': 'build.gradle'}
    if os.path.isfile(os.path.join(project, 'build.xml')):
        return {'name': 'ant', 'marker': 'build.xml'}
    return None

