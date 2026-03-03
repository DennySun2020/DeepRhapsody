#!/usr/bin/env python3
"""Shared base classes and utilities for NeuralDebug debug session scripts."""

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple


def error_response(msg: str, command: str = "") -> dict:
    return {
        "status": "error", "command": command, "message": msg,
        "current_location": None, "call_stack": [],
        "local_variables": {}, "stdout_new": "", "stderr_new": "",
    }


def completed_response(msg: str, command: str = "", stdout: str = "") -> dict:
    return {
        "status": "completed", "command": command, "message": msg,
        "current_location": None, "call_stack": [],
        "local_variables": {}, "stdout_new": stdout, "stderr_new": "",
    }


class DebugResponseMixin:

    def _error(self, msg: str) -> dict:
        return error_response(msg)

    def _completed(self, msg: str, stdout: str = "") -> dict:
        return completed_response(msg, stdout=stdout)


def recv_all(conn: socket.socket, bufsize: int = 65536) -> str:
    conn.settimeout(5.0)
    chunks: List[str] = []
    while True:
        try:
            data = conn.recv(bufsize)
            if not data:
                break
            chunks.append(data.decode('utf-8'))
            text = ''.join(chunks)
            try:
                json.loads(text)
                return text
            except json.JSONDecodeError:
                continue
        except socket.timeout:
            break
    return ''.join(chunks)


def send_response(conn: socket.socket, resp: dict):
    data = json.dumps(resp).encode('utf-8')
    conn.sendall(data)
    try:
        conn.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def send_command(port: int, action: str, args: str = "",
                 timeout: int = 120, host: str = "127.0.0.1") -> dict:
    cmd = {"action": action, "args": args}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.sendall(json.dumps(cmd).encode('utf-8'))
        # NOTE: Do NOT call sock.shutdown(SHUT_WR) here.  On Windows the
        # half-closed TCP state causes the OS to reset the connection if the
        # server takes a long time (>~2 min) to respond.  The server's
        # recv_all() already detects the end of the JSON message via parsing,
        # so the half-close is unnecessary.
        chunks: List[str] = []
        while True:
            try:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data.decode('utf-8'))
            except socket.timeout:
                break
        sock.close()
        text = ''.join(chunks)
        if text:
            return json.loads(text)
        return {"status": "error", "message": "Empty response from server"}
    except ConnectionRefusedError:
        return {"status": "error",
                "message": f"Cannot connect to debug server on {host}:{port}. "
                           f"Is the server running?"}
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"Invalid JSON response: {e}"}
    except Exception as e:
        return {"status": "error", "message": f"Connection error: {e}"}


def get_pid_file(language: str, port: int) -> str:
    tmpdir = os.environ.get('TEMP', os.environ.get('TMP', '/tmp'))
    return os.path.join(tmpdir, f'NeuralDebug_{language}_{port}.pid')


def write_pid_file(language: str, port: int):
    with open(get_pid_file(language, port), 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file(language: str, port: int):
    try:
        os.remove(get_pid_file(language, port))
    except OSError:
        pass


def read_pid_file(language: str, port: int) -> Optional[int]:
    try:
        with open(get_pid_file(language, port)) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def find_repo_root(start_dir: str) -> Optional[str]:
    cur = os.path.abspath(start_dir)
    for _ in range(20):
        if os.path.isdir(os.path.join(cur, '.git')):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def cmd_send_handler(args) -> None:
    import shlex
    command_parts = args.command
    # REMAINDER may include a leading '--' separator; strip it.
    if command_parts and command_parts[0] == "--":
        command_parts = command_parts[1:]
    if not command_parts:
        print(json.dumps({"status": "error", "message": "No command provided"}))
        return
    action = command_parts[0]
    # Re-quote parts containing spaces so the server can shlex.split them
    cmd_args = " ".join(
        shlex.quote(p) if " " in p else p for p in command_parts[1:]
    ) if len(command_parts) > 1 else ""
    timeout = getattr(args, "timeout", 120) or 120
    host = getattr(args, "host", "127.0.0.1") or "127.0.0.1"
    result = send_command(args.port, action, cmd_args, timeout=timeout,
                          host=host)
    print(json.dumps(result, indent=2))


_BASE_COMMANDS= [
    "start", "continue", "step_in", "step_over", "step_out",
    "set_breakpoint", "remove_breakpoint", "breakpoints",
    "inspect", "evaluate", "list", "backtrace", "ping", "quit",
]


class BaseDebugServer:
    """TCP server that dispatches JSON commands to a language debugger."""

    LANGUAGE: str = "Generic"
    SCRIPT_NAME: str = "debug_session.py"
    HAS_RUN_TO_LINE: bool = True

    def __init__(self, debugger, port: int, host: str = "127.0.0.1"):
        self.debugger = debugger
        self.port = port
        self.host = host
        self.running = False

    def _get_target_label(self) -> str:
        for attr in ('target', 'executable', 'script_file', 'script',
                     'main_class', 'target_file'):
            val = getattr(self.debugger, attr, None)
            if val:
                return str(val)
        return "?"

    def _start_debugger(self):
        if hasattr(self.debugger, 'start_debugger'):
            self.debugger.start_debugger()
        elif hasattr(self.debugger, 'start_gdb'):
            self.debugger.start_gdb()
        else:
            raise RuntimeError("Debugger has no start method")

    def _dispatch_extra(self, action: str, args: str) -> Optional[dict]:
        return None

    def _pre_start_dispatch(self, action: str, args: str) -> Optional[dict]:
        """Handle commands that don't require an active debug session.

        Override in subclasses to support commands like ``diagnose`` or
        ``finetune`` that only need the loaded model, not a stepping session.
        Return *None* to fall through to the default "session not started" error.
        """
        return None

    def _available_commands(self) -> List[str]:
        cmds = list(_BASE_COMMANDS)
        if self.HAS_RUN_TO_LINE:
            cmds.insert(cmds.index("set_breakpoint"), "run_to_line")
        return cmds

    def run(self):
        try:
            self._start_debugger()
        except RuntimeError as e:
            print(f"Error starting debugger: {e}", file=sys.stderr)
            return
        self.running = True
        started = False

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(1)
        srv.settimeout(1.0)

        bind_label = f"{self.host}:{self.port}"
        print(f"{self.LANGUAGE} Debug server listening on {bind_label}")
        print(f"Target: {self._get_target_label()}")
        print(f"Send commands with: python {self.SCRIPT_NAME} cmd --port "
              f"{self.port} <command>")

        while self.running:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                data = recv_all(conn)
                if not data:
                    conn.close()
                    continue

                cmd = json.loads(data)
                action = cmd.get("action", "").lower().strip()
                cmd_args = cmd.get("args", "")

                if not started:
                    if action in ("start", "s"):
                        started = True
                        resp = self.debugger.cmd_start(cmd_args)
                    elif action in ("set_breakpoint", "b", "break"):
                        resp = self.debugger.cmd_set_breakpoint(cmd_args)
                    elif action in ("ping", "health"):
                        resp = {
                            "status": "ok", "command": action,
                            "message": "Debug server is alive.",
                            "session_state": "not_started",
                            "current_location": None, "call_stack": [],
                            "local_variables": {}, "stdout_new": "",
                            "stderr_new": "",
                        }
                    elif action in ("quit", "q"):
                        resp = self.debugger.cmd_quit()
                        self.running = False
                    else:
                        # Let subclasses handle commands that don't need
                        # an active session (e.g. diagnose, finetune).
                        resp = self._pre_start_dispatch(action, cmd_args)
                        if resp is None:
                            resp = {
                                "status": "error", "command": action,
                                "message": "Session not started. Send 'start' first, "
                                           "or 'b <file>:<line>' to set breakpoints.",
                                "current_location": None, "call_stack": [],
                                "local_variables": {}, "stdout_new": "",
                                "stderr_new": "",
                            }
                else:
                    resp = self._dispatch(action, cmd_args)

                resp["command"] = action + (f" {cmd_args}" if cmd_args else "")
                send_response(conn, resp)

                if action in ("quit", "q"):
                    self.running = False

            except json.JSONDecodeError as e:
                send_response(conn, error_response(f"Invalid JSON: {e}"))
            except Exception as e:
                import traceback
                traceback.print_exc()
                try:
                    send_response(conn, error_response(
                        f"Server error: {type(e).__name__}: {e}"))
                except Exception:
                    pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        srv.close()
        print("Debug server stopped.")

    def _dispatch(self, action: str, args: str) -> dict:
        if self.debugger.is_finished:
            return {
                "status": "completed", "command": action,
                "message": "Program has already finished.",
                "current_location": None, "call_stack": [],
                "local_variables": {},
                "stdout_new": self.debugger._get_new_stdout(),
                "stderr_new": "",
            }

        if action in ("continue", "c"):
            return self.debugger.cmd_continue()
        elif action in ("step_in", "si"):
            return self.debugger.cmd_step_in()
        elif action in ("step_over", "n", "next"):
            return self.debugger.cmd_step_over()
        elif action in ("step_out", "so", "finish"):
            return self.debugger.cmd_step_out()
        elif action in ("run_to_line", "rt") and self.HAS_RUN_TO_LINE:
            try:
                line = int(args.strip())
                return self.debugger.cmd_run_to_line(line)
            except ValueError:
                return self.debugger._error(f"Invalid line: {args}")
        elif action in ("set_breakpoint", "b", "break"):
            return self.debugger.cmd_set_breakpoint(args)
        elif action in ("remove_breakpoint", "rb", "clear", "delete"):
            return self.debugger.cmd_remove_breakpoint(args)
        elif action in ("breakpoints", "bl"):
            return self.debugger.cmd_list_breakpoints()
        elif action in ("inspect", "i"):
            return self.debugger.cmd_inspect()
        elif action in ("evaluate", "e", "eval"):
            return self.debugger.cmd_evaluate(args)
        elif action in ("list", "l"):
            return self.debugger.cmd_list_source(args)
        elif action in ("backtrace", "bt"):
            return self.debugger.cmd_backtrace()
        elif action in ("quit", "q"):
            return self.debugger.cmd_quit()
        elif action in ("ping", "health"):
            return {
                "status": "ok",
                "message": "Debug server is alive.",
                "session_state": "running" if not self.debugger.is_finished else "finished",
                "current_location": None, "call_stack": [],
                "local_variables": {}, "stdout_new": "", "stderr_new": "",
            }
        elif action in ("start", "s"):
            return self.debugger._error("Program already started.")
        else:
            extra = self._dispatch_extra(action, args)
            if extra is not None:
                return extra
            return self.debugger._error(
                f"Unknown command: '{action}'. Available: "
                + ", ".join(self._available_commands()))


class GdbMiParser:
    """Parse GDB/MI output records into Python dicts."""

    @staticmethod
    def parse_mi_string(s: str) -> str:
        """Unescape a GDB MI C-string."""
        result = []
        i = 0
        while i < len(s):
            if s[i] == '\\' and i + 1 < len(s):
                c = s[i + 1]
                if c == 'n':
                    result.append('\n')
                elif c == 't':
                    result.append('\t')
                elif c == '"':
                    result.append('"')
                elif c == '\\':
                    result.append('\\')
                else:
                    result.append(c)
                i += 2
            else:
                result.append(s[i])
                i += 1
        return ''.join(result)

    @staticmethod
    def parse_value(text: str, pos: int) -> Tuple[Any, int]:
        """Parse a MI value starting at pos."""
        if pos >= len(text):
            return "", pos

        if text[pos] == '"':
            pos += 1
            end = pos
            while end < len(text):
                if text[end] == '\\' and end + 1 < len(text):
                    end += 2
                elif text[end] == '"':
                    break
                else:
                    end += 1
            val = GdbMiParser.parse_mi_string(text[pos:end])
            return val, end + 1

        elif text[pos] == '{':
            return GdbMiParser.parse_tuple(text, pos)

        elif text[pos] == '[':
            return GdbMiParser.parse_list(text, pos)

        else:
            end = pos
            while end < len(text) and text[end] not in ',}]':
                end += 1
            return text[pos:end], end

    @staticmethod
    def parse_tuple(text: str, pos: int) -> Tuple[dict, int]:
        """Parse ``{key=val,key=val,...}``."""
        assert text[pos] == '{'
        pos += 1
        result = {}
        while pos < len(text) and text[pos] != '}':
            if text[pos] in ' ,':
                pos += 1
                continue
            eq = text.index('=', pos)
            key = text[pos:eq]
            val, pos = GdbMiParser.parse_value(text, eq + 1)
            result[key] = val
        if pos < len(text) and text[pos] == '}':
            pos += 1
        return result, pos

    @staticmethod
    def parse_list(text: str, pos: int) -> Tuple[list, int]:
        """Parse ``[val,val,...]`` or ``[key=val,key=val,...]``."""
        assert text[pos] == '['
        pos += 1
        result = []
        while pos < len(text) and text[pos] != ']':
            if text[pos] in ' ,':
                pos += 1
                continue
            peek = pos
            has_key = False
            while peek < len(text) and text[peek] not in '=,]}{"[':
                peek += 1
            if peek < len(text) and text[peek] == '=':
                key = text[pos:peek]
                val, pos = GdbMiParser.parse_value(text, peek + 1)
                result.append({key: val})
            else:
                val, pos = GdbMiParser.parse_value(text, pos)
                result.append(val)
        if pos < len(text) and text[pos] == ']':
            pos += 1
        return result, pos

    @staticmethod
    def parse_record(line: str) -> Optional[dict]:
        """Parse a single GDB MI output line into a record dict.

        Returns dict with keys:
            type: 'result' | 'exec' | 'notify' | 'console' | 'target' | 'log'
            token: optional numeric token
            class_: result class (e.g. 'done', 'stopped', 'running')
            body: dict of key=value results, or string for stream records
        """
        if not line or line.strip() == '(gdb)':
            return None

        record: Dict[str, Any] = {}
        pos = 0

        token_match = re.match(r'^(\d+)', line)
        if token_match:
            record['token'] = int(token_match.group(1))
            pos = token_match.end()

        if pos >= len(line):
            return None

        indicator = line[pos]
        pos += 1

        if indicator == '^':
            record['type'] = 'result'
        elif indicator == '*':
            record['type'] = 'exec'
        elif indicator == '=':
            record['type'] = 'notify'
        elif indicator == '~':
            record['type'] = 'console'
            if pos < len(line) and line[pos] == '"':
                val, _ = GdbMiParser.parse_value(line, pos)
                record['body'] = val
            else:
                record['body'] = line[pos:]
            return record
        elif indicator == '@':
            record['type'] = 'target'
            if pos < len(line) and line[pos] == '"':
                val, _ = GdbMiParser.parse_value(line, pos)
                record['body'] = val
            else:
                record['body'] = line[pos:]
            return record
        elif indicator == '&':
            record['type'] = 'log'
            if pos < len(line) and line[pos] == '"':
                val, _ = GdbMiParser.parse_value(line, pos)
                record['body'] = val
            else:
                record['body'] = line[pos:]
            return record
        else:
            return {'type': 'unknown', 'body': line}

        comma = line.find(',', pos)
        if comma == -1:
            record['class_'] = line[pos:]
            record['body'] = {}
        else:
            record['class_'] = line[pos:comma]
            body: Dict[str, Any] = {}
            rest = line[comma + 1:]
            rpos = 0
            while rpos < len(rest):
                if rest[rpos] in ' ,':
                    rpos += 1
                    continue
                eq = rest.find('=', rpos)
                if eq == -1:
                    break
                key = rest[rpos:eq]
                val, rpos = GdbMiParser.parse_value(rest, eq + 1)
                body[key] = val
            record['body'] = body

        return record


class MiDebuggerBase(DebugResponseMixin):
    """Base class providing GDB/MI transport shared across backends."""

    def _init_mi(self):
        self.proc: Optional[subprocess.Popen] = None
        self.parser = GdbMiParser()
        self._lock = threading.Lock()
        self._token = 0
        self._program_output: List[str] = []
        self._program_stderr: List[str] = []
        self._last_out_pos = 0
        self._last_err_pos = 0

        self.is_started = False
        self.is_finished = False
        self.is_paused = False

        self._reader_thread: Optional[threading.Thread] = None
        self._pending_records: List[dict] = []
        self._records_event = threading.Event()
        self._all_lines: List[str] = []

    def _read_output(self):
        try:
            for raw_line in self.proc.stdout:
                line = raw_line.decode('utf-8', errors='replace').rstrip('\r\n')
                self._all_lines.append(line)

                record = self.parser.parse_record(line)
                if not record:
                    continue

                if record['type'] == 'target':
                    self._program_output.append(record.get('body', ''))
                    continue

                if record['type'] in ('console',):
                    pass  # stored in pending for collection

                self._pending_records.append(record)
                self._records_event.set()
        except (OSError, ValueError):
            pass

    def _next_token(self) -> int:
        self._token += 1
        return self._token

    def _send_mi(self, command: str, token: Optional[int] = None) -> int:
        """Send a MI command. Returns the token used."""
        if token is None:
            token = self._next_token()
        line = f"{token}{command}\n"
        try:
            self.proc.stdin.write(line.encode('utf-8'))
            self.proc.stdin.flush()
        except (OSError, BrokenPipeError):
            self.is_finished = True
        return token

    def _collect_until_result(self, token: int,
                              timeout: float = 30.0) -> Tuple[Optional[dict], List[dict]]:
        """Read records until a result record matching *token* arrives."""
        deadline = time.time() + timeout
        result = None
        others: List[dict] = []

        while time.time() < deadline:
            self._records_event.wait(timeout=0.5)
            self._records_event.clear()

            with self._lock:
                pending = list(self._pending_records)
                self._pending_records.clear()

            for rec in pending:
                rec_token = rec.get('token')
                if rec['type'] == 'result' and (rec_token == token or rec_token is None):
                    result = rec
                elif rec['type'] == 'exec' and rec.get('class_') == 'stopped':
                    others.append(rec)
                elif rec['type'] == 'exec' and rec.get('class_') == 'running':
                    others.append(rec)
                else:
                    others.append(rec)

            if result is not None:
                return result, others

        return None, others

    def _collect_stop_event(self,
                            timeout: float = 60.0) -> Tuple[Optional[dict], List[dict]]:
        """Wait for a ``*stopped`` event from the debugger."""
        deadline = time.time() + timeout
        stop = None
        others: List[dict] = []

        while time.time() < deadline:
            self._records_event.wait(timeout=0.5)
            self._records_event.clear()

            with self._lock:
                pending = list(self._pending_records)
                self._pending_records.clear()

            for rec in pending:
                if rec['type'] == 'exec' and rec.get('class_') == 'stopped':
                    stop = rec
                else:
                    others.append(rec)

            if stop is not None:
                return stop, others

        return None, others

    def _wait_for_prompt(self, timeout: float = 10.0):
        """Wait until the debugger is ready (initial prompt)."""
        time.sleep(0.5)
        self._records_event.wait(timeout=timeout)
        self._records_event.clear()
        with self._lock:
            self._pending_records.clear()

    def _get_new_stdout(self) -> str:
        items = self._program_output[self._last_out_pos:]
        self._last_out_pos = len(self._program_output)
        return ''.join(items)[:5000]
