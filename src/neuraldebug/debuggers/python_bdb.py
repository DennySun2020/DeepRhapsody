"""Python debugging via bdb (in-process)."""

import bdb
import json
import linecache
import os
import queue
import re
import sys
import socket
import signal
import threading
import time
import traceback
from contextlib import redirect_stdout, redirect_stderr
import io
from io import StringIO
from typing import Any, Dict, List, Optional

from debug_common import error_response, send_response, recv_all


def safe_repr(obj: Any, max_length: int = 500) -> str:
    try:
        r = repr(obj)
        return r[:max_length] + "...<truncated>" if len(r) > max_length else r
    except Exception as e:
        return f"<repr error: {e}>"


def safe_str(obj: Any, max_length: int = 500) -> str:
    try:
        s = str(obj)
        return s[:max_length] + "...<truncated>" if len(s) > max_length else s
    except Exception as e:
        return f"<str error: {e}>"


def serialize_variable(name: str, value: Any) -> Dict[str, str]:
    return {
        "type": type(value).__name__,
        "value": safe_str(value),
        "repr": safe_repr(value),
    }


SKIP_LOCALS = {'__builtins__', '__name__', '__doc__', '__package__',
               '__loader__', '__spec__', '__annotations__', '__file__',
               '__cached__'}


def is_user_frame(filename: str, target_file: str, target_dir: str) -> bool:
    if not filename:
        return False
    # Use normcase so that canonic'd (lowered on Windows) paths match originals
    abs_filename = os.path.normcase(os.path.abspath(filename))
    skip_modules = ['bdb.py', 'pdb.py', 'cmd.py', 'threading.py', 'runpy.py',
                    'python_debug_session.py', 'python_debugger.py']
    if any(mod in filename for mod in skip_modules):
        return False
    if abs_filename == os.path.normcase(os.path.abspath(target_file)):
        return True
    if abs_filename.startswith(os.path.normcase(target_dir)):
        return True
    return False


class InteractiveDebugger(bdb.Bdb):
    """
    A debugger that pauses at breakpoints/steps and waits for commands
    via a thread-safe queue. Each command produces a response placed
    on a response queue.
    """

    def __init__(self, target_file: str):
        super().__init__()
        self.target_file = os.path.abspath(target_file)
        self.target_dir = os.path.dirname(self.target_file)

        self.cmd_queue: queue.Queue = queue.Queue()
        self.resp_queue: queue.Queue = queue.Queue()

        self.current_frame = None
        self.is_paused = False
        self.is_started = False
        self.is_finished = False
        self.stop_reason = ""
        self.stdout_capture = io.StringIO()
        self.stderr_capture = io.StringIO()
        self._last_stdout_pos = 0
        self._last_stderr_pos = 0

        self._run_to_target: Optional[int] = None

    def _wait_for_command(self, frame, reason: str):
        self.current_frame = frame
        self.is_paused = True
        self.stop_reason = reason

        state = self._build_state(f"Paused: {reason}")
        self.resp_queue.put(state)

        while True:
            try:
                cmd = self.cmd_queue.get(timeout=1.0)
            except queue.Empty:
                if self.is_finished:
                    return
                continue

            result = self._execute_command(cmd, frame)
            if result == "__RESUME_CONTINUE__":
                self.is_paused = False
                self.set_continue()
                return
            elif result == "__RESUME_STEP_IN__":
                self.is_paused = False
                self.set_step()
                return
            elif result == "__RESUME_STEP_OVER__":
                self.is_paused = False
                self.set_next(frame)
                return
            elif result == "__RESUME_STEP_OUT__":
                self.is_paused = False
                self.set_return(frame)
                return
            elif result == "__RESUME_RUN_TO__":
                self.is_paused = False
                self.set_continue()
                return
            elif result == "__QUIT__":
                self.is_paused = False
                self.is_finished = True
                self.set_quit()
                return
            else:
                pass

    def _execute_command(self, cmd: dict, frame) -> str:
        action = cmd.get("action", "").lower().strip()
        args = cmd.get("args", "")

        if action in ("continue", "c"):
            return "__RESUME_CONTINUE__"

        elif action in ("step_in", "si"):
            return "__RESUME_STEP_IN__"

        elif action in ("step_over", "n", "next"):
            return "__RESUME_STEP_OVER__"

        elif action in ("step_out", "so"):
            return "__RESUME_STEP_OUT__"

        elif action in ("run_to_line", "rt"):
            try:
                line = int(args.strip())
                self._run_to_target = line
                # Set a temporary breakpoint at that line
                self.set_break(self.target_file, line, temporary=True)
                return "__RESUME_RUN_TO__"
            except ValueError:
                self.resp_queue.put(self._error_response(
                    f"Invalid line number: {args}"))
                return "__NOOP__"

        elif action in ("set_breakpoint", "b", "break"):
            return self._cmd_set_breakpoint(args)

        elif action in ("remove_breakpoint", "rb", "clear"):
            return self._cmd_remove_breakpoint(args)

        elif action in ("breakpoints", "bl"):
            return self._cmd_list_breakpoints()

        elif action in ("inspect", "i"):
            state = self._build_state("Current state")
            self.resp_queue.put(state)
            return "__NOOP__"

        elif action in ("evaluate", "e", "eval"):
            return self._cmd_evaluate(args, frame)

        elif action in ("list", "l"):
            return self._cmd_list_source(args, frame)

        elif action in ("quit", "q"):
            self.resp_queue.put({
                "status": "completed",
                "command": "quit",
                "message": "Debug session terminated.",
                "current_location": None,
                "call_stack": [],
                "local_variables": {},
                "stdout_new": self._get_new_stdout(),
                "stderr_new": self._get_new_stderr(),
            })
            return "__QUIT__"

        elif action in ("ping", "health"):
            self.resp_queue.put({
                "status": "ok",
                "command": "ping",
                "message": "Debug server is alive.",
                "session_state": "running",
                "current_location": None,
                "call_stack": [],
                "local_variables": {},
                "stdout_new": "",
                "stderr_new": "",
            })
            return "__NOOP__"

        else:
            self.resp_queue.put(self._error_response(
                f"Unknown command: '{action}'. Available: start, continue, "
                f"step_in, step_over, step_out, run_to_line, set_breakpoint, "
                f"remove_breakpoint, breakpoints, inspect, evaluate, list, ping, quit"))
            return "__NOOP__"

    def _cmd_set_breakpoint(self, args: str) -> str:
        parts = args.strip().split(None, 1)
        if not parts:
            self.resp_queue.put(self._error_response(
                "Usage: b <line> [condition]"))
            return "__NOOP__"
        try:
            line = int(parts[0])
        except ValueError:
            self.resp_queue.put(self._error_response(
                f"Invalid line number: {parts[0]}"))
            return "__NOOP__"

        cond = parts[1] if len(parts) > 1 else None
        err = self.set_break(self.target_file, line, cond=cond)
        if err:
            self.resp_queue.put(self._error_response(f"Failed to set breakpoint: {err}"))
        else:
            msg = f"Breakpoint set at line {line}"
            if cond:
                msg += f" (condition: {cond})"
            self.resp_queue.put({
                "status": "paused" if self.is_paused else "running",
                "command": f"set_breakpoint {args}",
                "message": msg,
                "current_location": self._current_location(),
                "call_stack": [],
                "local_variables": {},
                "stdout_new": self._get_new_stdout(),
                "stderr_new": self._get_new_stderr(),
            })
        return "__NOOP__"

    def _cmd_remove_breakpoint(self, args: str) -> str:
        try:
            line = int(args.strip())
        except ValueError:
            self.resp_queue.put(self._error_response(
                f"Invalid line number: {args}"))
            return "__NOOP__"

        err = self.clear_break(self.target_file, line)
        if err:
            self.resp_queue.put(self._error_response(
                f"Failed to remove breakpoint: {err}"))
        else:
            self.resp_queue.put({
                "status": "paused" if self.is_paused else "running",
                "command": f"remove_breakpoint {args}",
                "message": f"Breakpoint removed at line {line}",
                "current_location": self._current_location(),
                "call_stack": [],
                "local_variables": {},
                "stdout_new": self._get_new_stdout(),
                "stderr_new": self._get_new_stderr(),
            })
        return "__NOOP__"

    def _cmd_list_breakpoints(self) -> str:
        bps = []
        for (fname, lineno), blist in self.get_all_breaks().items():
            # blist is just the list of line numbers for that file
            # In bdb, get_all_breaks() returns {(file, line): True}
            pass

        # Use the breaks dict directly
        bp_list = []
        for filename, lines in self.get_all_breaks().items():
            rel = os.path.relpath(filename, self.target_dir) \
                if filename.startswith(self.target_dir) else filename
            for line in lines:
                bp_list.append({"file": rel, "line": line})

        self.resp_queue.put({
            "status": "paused" if self.is_paused else "running",
            "command": "breakpoints",
            "message": f"{len(bp_list)} active breakpoint(s)",
            "breakpoints": bp_list,
            "current_location": self._current_location(),
            "call_stack": [],
            "local_variables": {},
            "stdout_new": self._get_new_stdout(),
            "stderr_new": self._get_new_stderr(),
        })
        return "__NOOP__"

    def _cmd_evaluate(self, expr: str, frame) -> str:
        if not frame:
            self.resp_queue.put(self._error_response("No active frame"))
            return "__NOOP__"
        try:
            result = eval(expr.strip(), frame.f_globals, frame.f_locals)
            self.resp_queue.put({
                "status": "paused",
                "command": f"evaluate {expr}",
                "message": f"{expr.strip()} = {safe_repr(result)}",
                "eval_result": serialize_variable("result", result),
                "current_location": self._current_location(),
                "call_stack": [],
                "local_variables": {},
                "stdout_new": self._get_new_stdout(),
                "stderr_new": self._get_new_stderr(),
            })
        except Exception as e:
            self.resp_queue.put(self._error_response(
                f"Error evaluating '{expr}': {type(e).__name__}: {e}"))
        return "__NOOP__"

    def _cmd_list_source(self, args: str, frame) -> str:
        if not frame:
            self.resp_queue.put(self._error_response("No active frame"))
            return "__NOOP__"

        context = 5
        if args.strip():
            try:
                context = int(args.strip())
            except ValueError:
                pass

        filename = frame.f_code.co_filename
        current_line = frame.f_lineno
        start = max(1, current_line - context)
        end = current_line + context

        lines = []
        for i in range(start, end + 1):
            src = linecache.getline(filename, i).rstrip()
            marker = ">>>" if i == current_line else "   "
            lines.append(f"{marker} {i:4d} | {src}")

        self.resp_queue.put({
            "status": "paused",
            "command": f"list {args}",
            "message": f"Source around line {current_line}:",
            "source_listing": "\n".join(lines),
            "current_location": self._current_location(),
            "call_stack": [],
            "local_variables": {},
            "stdout_new": self._get_new_stdout(),
            "stderr_new": self._get_new_stderr(),
        })
        return "__NOOP__"


    def user_line(self, frame):
        filename = self.canonic(frame.f_code.co_filename)
        lineno = frame.f_lineno

        if self._run_to_target is not None:
            if filename == self.canonic(self.target_file) and lineno == self._run_to_target:
                self._run_to_target = None
                self._wait_for_command(frame, f"reached line {lineno}")
                return
            elif self.is_started and self.stopframe is None and self.currentbp == 0:
                # Not at run_to target and not at breakpoint, keep going
                self.set_continue()
                return

        if not is_user_frame(filename, self.target_file, self.target_dir):
            self.set_continue()
            return

        reason = f"line {lineno} in {frame.f_code.co_name}()"
        if self._check_breakpoint(filename, lineno):
            reason = f"breakpoint at line {lineno} in {frame.f_code.co_name}()"

        self._wait_for_command(frame, reason)

    def user_return(self, frame, return_value):
        filename = self.canonic(frame.f_code.co_filename)
        if not is_user_frame(filename, self.target_file, self.target_dir):
            return
        self._wait_for_command(
            frame,
            f"return from {frame.f_code.co_name}() = {safe_repr(return_value, 100)}")

    def user_exception(self, frame, exc_info):
        exc_type, exc_value, exc_tb = exc_info
        filename = self.canonic(frame.f_code.co_filename)
        if not is_user_frame(filename, self.target_file, self.target_dir):
            return
        self._wait_for_command(
            frame,
            f"exception {exc_type.__name__}: {exc_value}")

    def _check_breakpoint(self, filename: str, lineno: int) -> bool:
        breaks = self.get_all_breaks()
        if filename in breaks and lineno in breaks[filename]:
            return True
        return False


    def _current_location(self) -> Optional[Dict]:
        if not self.current_frame:
            return None
        f = self.current_frame
        filename = f.f_code.co_filename
        norm_fn = os.path.normcase(os.path.abspath(filename))
        norm_dir = os.path.normcase(self.target_dir)
        rel = os.path.relpath(filename, self.target_dir) \
            if norm_fn.startswith(norm_dir) else filename
        return {
            "file": rel,
            "line": f.f_lineno,
            "function": f.f_code.co_name,
            "code_context": linecache.getline(filename, f.f_lineno).rstrip(),
        }

    def _build_call_stack(self) -> List[Dict]:
        if not self.current_frame:
            return []
        stack = []
        frame = self.current_frame
        idx = 0
        while frame is not None:
            filename = frame.f_code.co_filename
            if is_user_frame(filename, self.target_file, self.target_dir):
                norm_fn = os.path.normcase(os.path.abspath(filename))
                norm_dir = os.path.normcase(self.target_dir)
                rel = os.path.relpath(filename, self.target_dir) \
                    if norm_fn.startswith(norm_dir) else filename
                stack.append({
                    "frame_index": idx,
                    "file": rel,
                    "line": frame.f_lineno,
                    "function": frame.f_code.co_name,
                    "code_context": linecache.getline(filename, frame.f_lineno).rstrip(),
                })
                idx += 1
            frame = frame.f_back
        return stack

    def _build_locals(self) -> Dict[str, Dict[str, str]]:
        if not self.current_frame:
            return {}
        result = {}
        for name, value in self.current_frame.f_locals.items():
            if name in SKIP_LOCALS or (name.startswith('__') and name.endswith('__')):
                continue
            result[name] = serialize_variable(name, value)
        return result

    def _build_state(self, message: str) -> Dict:
        return {
            "status": "paused",
            "command": "",
            "message": message,
            "current_location": self._current_location(),
            "call_stack": self._build_call_stack(),
            "local_variables": self._build_locals(),
            "stdout_new": self._get_new_stdout(),
            "stderr_new": self._get_new_stderr(),
        }

    def _error_response(self, message: str) -> Dict:
        return {
            "status": "error",
            "command": "",
            "message": message,
            "current_location": self._current_location(),
            "call_stack": [],
            "local_variables": {},
            "stdout_new": self._get_new_stdout(),
            "stderr_new": self._get_new_stderr(),
        }

    def _get_new_stdout(self) -> str:
        self.stdout_capture.seek(self._last_stdout_pos)
        new = self.stdout_capture.read()
        self._last_stdout_pos = self.stdout_capture.tell()
        return new[:5000]

    def _get_new_stderr(self) -> str:
        self.stderr_capture.seek(self._last_stderr_pos)
        new = self.stderr_capture.read()
        self._last_stderr_pos = self.stderr_capture.tell()
        return new[:5000]

    def run_target(self, args: List[str]):
        old_argv = sys.argv[:]
        sys.argv = [self.target_file] + args

        try:
            with open(self.target_file, 'r') as f:
                code = f.read()

            compiled = compile(code, self.target_file, 'exec')
            target_globals = {
                '__name__': '__main__',
                '__file__': self.target_file,
                '__builtins__': __builtins__,
            }

            with redirect_stdout(self.stdout_capture), \
                 redirect_stderr(self.stderr_capture):
                try:
                    # Start in step mode so we pause at the first line
                    self.run(compiled, globals=target_globals, locals=target_globals)
                except bdb.BdbQuit:
                    pass
                except Exception as e:
                    tb = traceback.format_exc()
                    self.resp_queue.put({
                        "status": "error",
                        "command": "",
                        "message": f"Unhandled exception: {type(e).__name__}: {e}\n{tb}",
                        "current_location": self._current_location(),
                        "call_stack": [],
                        "local_variables": {},
                        "stdout_new": self._get_new_stdout(),
                        "stderr_new": self._get_new_stderr(),
                    })

        except FileNotFoundError:
            self.resp_queue.put(self._error_response(
                f"Target file not found: {self.target_file}"))
        except SyntaxError as e:
            self.resp_queue.put(self._error_response(
                f"Syntax error in target: {e}"))
        finally:
            sys.argv = old_argv
            self.is_finished = True
            # Signal completion
            self.resp_queue.put({
                "status": "completed",
                "command": "",
                "message": "Program execution finished.",
                "current_location": None,
                "call_stack": [],
                "local_variables": {},
                "stdout_new": self._get_new_stdout(),
                "stderr_new": self._get_new_stderr(),
            })


class DebugServer:

    def __init__(self, debugger: InteractiveDebugger, port: int = 5678, host: str = '127.0.0.1'):
        self.debugger = debugger
        self.port = port
        self.host = host
        self.server_socket = None
        self.running = False

    def start(self, target_args: List[str]):
        dbg_thread = threading.Thread(
            target=self.debugger.run_target,
            args=(target_args,),
            daemon=True,
        )

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(1)
        self.server_socket.settimeout(1.0)
        self.running = True

        print(json.dumps({
            "status": "server_started",
            "port": self.port,
            "target": self.debugger.target_file,
            "message": f"Debug server listening on {self.host}:{self.port}. "
                       f"Send 'start' to begin execution.",
        }))
        sys.stdout.flush()

        self._wait_for_start(dbg_thread)

    def _wait_for_start(self, dbg_thread: threading.Thread):
        started = False
        while self.running:
            try:
                conn, addr = self.server_socket.accept()
            except socket.timeout:
                if self.debugger.is_finished:
                    break
                continue
            except OSError:
                break

            try:
                data = self._recv_all(conn)
                if not data:
                    conn.close()
                    continue

                cmd = json.loads(data)
                action = cmd.get("action", "").lower().strip()

                if not started:
                    if action in ("start", "s"):
                        # Launch debugger thread now
                        dbg_thread.start()
                        started = True
                        self.debugger.is_started = True

                        # Wait for the debugger to pause at first line
                        try:
                            resp = self.debugger.resp_queue.get(timeout=10)
                            resp["command"] = "start"
                            self._send_response(conn, resp)
                        except queue.Empty:
                            self._send_response(conn, {
                                "status": "error",
                                "command": "start",
                                "message": "Timeout waiting for debugger to start",
                                "current_location": None,
                                "call_stack": [],
                                "local_variables": {},
                                "stdout_new": "",
                                "stderr_new": "",
                            })
                    elif action in ("set_breakpoint", "b", "break"):
                        # Allow setting breakpoints before start
                        args = cmd.get("args", "")
                        parts = args.strip().split(None, 1)
                        if parts:
                            try:
                                line = int(parts[0])
                                cond = parts[1] if len(parts) > 1 else None
                                err = self.debugger.set_break(
                                    self.debugger.target_file, line, cond=cond)
                                if err:
                                    self._send_response(conn, {
                                        "status": "running",
                                        "command": f"set_breakpoint {args}",
                                        "message": f"Failed: {err}",
                                        "current_location": None,
                                        "call_stack": [],
                                        "local_variables": {},
                                        "stdout_new": "",
                                        "stderr_new": "",
                                    })
                                else:
                                    msg = f"Breakpoint set at line {line}"
                                    if cond:
                                        msg += f" (condition: {cond})"
                                    self._send_response(conn, {
                                        "status": "running",
                                        "command": f"set_breakpoint {args}",
                                        "message": msg,
                                        "current_location": None,
                                        "call_stack": [],
                                        "local_variables": {},
                                        "stdout_new": "",
                                        "stderr_new": "",
                                    })
                            except ValueError:
                                self._send_response(conn, {
                                    "status": "error",
                                    "command": f"set_breakpoint {args}",
                                    "message": f"Invalid line number",
                                    "current_location": None,
                                    "call_stack": [],
                                    "local_variables": {},
                                    "stdout_new": "",
                                    "stderr_new": "",
                                })
                        else:
                            self._send_response(conn, {
                                "status": "error",
                                "command": "set_breakpoint",
                                "message": "Usage: b <line> [condition]",
                                "current_location": None,
                                "call_stack": [],
                                "local_variables": {},
                                "stdout_new": "",
                                "stderr_new": "",
                            })
                    elif action in ("quit", "q"):
                        self._send_response(conn, {
                            "status": "completed",
                            "command": "quit",
                            "message": "Session ended before start.",
                            "current_location": None,
                            "call_stack": [],
                            "local_variables": {},
                            "stdout_new": "",
                            "stderr_new": "",
                        })
                        self.running = False
                        conn.close()
                        break
                    else:
                        self._send_response(conn, {
                            "status": "error",
                            "command": action,
                            "message": "Session not started yet. Send 'start' first, "
                                       "or 'b <line>' to set breakpoints before starting.",
                            "current_location": None,
                            "call_stack": [],
                            "local_variables": {},
                            "stdout_new": "",
                            "stderr_new": "",
                        })
                else:
                    # Session is started, forward commands to debugger
                    self._handle_command(conn, cmd)

            except json.JSONDecodeError as e:
                self._send_response(conn, {
                    "status": "error",
                    "command": "",
                    "message": f"Invalid JSON: {e}",
                    "current_location": None,
                    "call_stack": [],
                    "local_variables": {},
                    "stdout_new": "",
                    "stderr_new": "",
                })
            except Exception as e:
                try:
                    self._send_response(conn, {
                        "status": "error",
                        "command": "",
                        "message": f"Server error: {type(e).__name__}: {e}",
                        "current_location": None,
                        "call_stack": [],
                        "local_variables": {},
                        "stdout_new": "",
                        "stderr_new": "",
                    })
                except Exception:
                    pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass

    def _handle_command(self, conn, cmd: dict):
        if self.debugger.is_finished:
            self._send_response(conn, {
                "status": "completed",
                "command": cmd.get("action", ""),
                "message": "Program has already finished.",
                "current_location": None,
                "call_stack": [],
                "local_variables": {},
                "stdout_new": self.debugger._get_new_stdout(),
                "stderr_new": self.debugger._get_new_stderr(),
            })
            action = cmd.get("action", "").lower().strip()
            if action in ("quit", "q"):
                self.running = False
            return

        self.debugger.cmd_queue.put(cmd)

        try:
            resp = self.debugger.resp_queue.get(timeout=60)
            resp["command"] = cmd.get("action", "")
            self._send_response(conn, resp)

            action = cmd.get("action", "").lower().strip()
            if action in ("quit", "q"):
                self.running = False

        except queue.Empty:
            self._send_response(conn, {
                "status": "error",
                "command": cmd.get("action", ""),
                "message": "Timeout waiting for debugger response (60s). "
                           "The program may be running without hitting a breakpoint.",
                "current_location": None,
                "call_stack": [],
                "local_variables": {},
                "stdout_new": self.debugger._get_new_stdout(),
                "stderr_new": self.debugger._get_new_stderr(),
            })

    @staticmethod
    def _recv_all(conn, bufsize=65536):
        return recv_all(conn, bufsize)

    @staticmethod
    def _send_response(conn, response):
        send_response(conn, response)

