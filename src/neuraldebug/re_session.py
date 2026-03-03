#!/usr/bin/env python3
"""Reverse engineering session — static binary analysis server.

Provides a TCP server (same protocol as the debug session scripts) that
exposes static analysis commands:  info, headers, sections, imports,
exports, strings, functions, disassemble, xrefs, cfg, entropy, hexdump.

Usage:
    python re_session.py serve program.exe --port 5695 --daemonize
    python re_session.py cmd --port 5695 info
    python re_session.py cmd --port 5695 strings
    python re_session.py cmd --port 5695 functions
    python re_session.py cmd --port 5695 imports
    python re_session.py cmd --port 5695 cfg 0x401000
    python re_session.py stop --port 5695
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
from typing import Optional

# Adjust path so imports work when run directly
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from debug_common import (
    send_command, get_pid_file, write_pid_file,
    remove_pid_file, read_pid_file,
)
from reversing.binary_analyzer import BinaryAnalyzer


LANGUAGE_META = {
    "name": "re",
    "display_name": "Reverse Engineering",
    "extensions": [".exe", ".dll", ".sys", ".so", ".dylib", ".elf",
                   ".o", ".obj", ".bin"],
    "default_port": 5695,
    "debuggers": "Static analysis (no debugger needed)",
    "aliases": ["reverse", "reversing", "binary-analysis", "re"],
}

PID_KEY = "re"
DEFAULT_PORT = 5695


# ======================================================================
#  RE Server
# ======================================================================

class REServer:
    """TCP server for reverse engineering commands."""

    def __init__(self, analyzer: BinaryAnalyzer, port: int = DEFAULT_PORT,
                 host: str = "127.0.0.1"):
        self.analyzer = analyzer
        self.port = port
        self.host = host
        self.running = False

    def run(self):
        self.running = True
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.settimeout(1.0)
        srv.bind((self.host, self.port))
        srv.listen(4)

        script_name = "re_session.py"
        fname = os.path.basename(self.analyzer.filepath)
        print(f"Reverse Engineering server listening on port {self.port}")
        print(f"Target: {self.analyzer.filepath}")
        print(f"Format: {self.analyzer.format} | Arch: {self.analyzer.arch} "
              f"| Size: {self.analyzer.file_size} bytes")
        print(f"Send commands with: python {script_name} cmd --port {self.port} <command>")

        while self.running:
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(conn,),
                             daemon=True).start()
        srv.close()

    def _handle_client(self, conn: socket.socket):
        try:
            conn.settimeout(300)
            raw = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                raw += chunk
                # Try to parse JSON — complete when valid
                text = raw.decode("utf-8", errors="replace").strip()
                if text:
                    try:
                        json.loads(text)
                        break  # valid JSON received
                    except json.JSONDecodeError:
                        if b"\n" in raw:
                            break  # fallback: newline-terminated

            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                conn.close()
                return

            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                request = {"action": line}

            action = request.get("action", "").strip()
            args = request.get("args", "").strip()

            result = self._dispatch(action, args)
            response = json.dumps(result, default=str) + "\n"
            conn.sendall(response.encode("utf-8"))
        except Exception as e:
            try:
                err = json.dumps({"status": "error", "message": str(e)}) + "\n"
                conn.sendall(err.encode("utf-8"))
            except Exception:
                pass
        finally:
            conn.close()

    def _dispatch(self, action: str, args: str) -> dict:
        action = action.lower().strip()

        if action == "ping":
            return {"status": "ok", "message": "RE server alive",
                    "file": os.path.basename(self.analyzer.filepath)}

        if action == "quit":
            self.running = False
            return {"status": "ok", "message": "RE server shutting down"}

        if action == "info":
            return {"status": "ok", "command": "info",
                    "data": self.analyzer.info()}

        if action == "headers":
            return {"status": "ok", "command": "headers",
                    "data": self.analyzer.headers()}

        if action in ("sections", "secs"):
            return {"status": "ok", "command": "sections",
                    "data": self.analyzer.sections()}

        if action in ("imports", "imp"):
            return {"status": "ok", "command": "imports",
                    "data": self.analyzer.imports()}

        if action in ("exports", "exp"):
            return {"status": "ok", "command": "exports",
                    "data": self.analyzer.exports()}

        if action in ("strings", "str"):
            min_len = 4
            limit = 500
            parts = args.split()
            if parts:
                try:
                    min_len = int(parts[0])
                except ValueError:
                    pass
            if len(parts) > 1:
                try:
                    limit = int(parts[1])
                except ValueError:
                    pass
            return {"status": "ok", "command": "strings",
                    "data": self.analyzer.strings(min_len, limit)}

        if action in ("functions", "funcs", "fn"):
            return {"status": "ok", "command": "functions",
                    "data": self.analyzer.functions()}

        if action in ("disassemble", "dis", "disas"):
            parts = args.split()
            if not parts:
                # Disassemble at entry point
                if self.analyzer._pe:
                    addr = self.analyzer._pe.entry_point_va()
                elif self.analyzer._elf:
                    addr = self.analyzer._elf.entry_point
                else:
                    return {"status": "error", "command": "disassemble",
                            "message": "No entry point found. Provide an address."}
            else:
                try:
                    addr = int(parts[0], 16) if parts[0].startswith("0x") else int(parts[0])
                except ValueError:
                    return {"status": "error", "command": "disassemble",
                            "message": f"Invalid address: {parts[0]}"}
            count = 20
            if len(parts) > 1:
                try:
                    count = int(parts[1])
                except ValueError:
                    pass
            return {"status": "ok", "command": "disassemble",
                    "data": self.analyzer.disassemble(addr, count)}

        if action in ("xrefs", "xref", "x"):
            if args:
                try:
                    addr = int(args, 16) if args.startswith("0x") else int(args)
                except ValueError:
                    return {"status": "error", "command": "xrefs",
                            "message": f"Invalid address: {args}"}
                return {"status": "ok", "command": "xrefs",
                        "data": self.analyzer.xrefs(addr)}
            return {"status": "ok", "command": "xrefs",
                    "data": self.analyzer.xrefs()}

        if action == "cfg":
            parts = args.split()
            if not parts:
                return {"status": "error", "command": "cfg",
                        "message": "Usage: cfg <address> [ascii|mermaid|json]"}
            try:
                addr = int(parts[0], 16) if parts[0].startswith("0x") else int(parts[0])
            except ValueError:
                return {"status": "error", "command": "cfg",
                        "message": f"Invalid address: {parts[0]}"}
            fmt = parts[1] if len(parts) > 1 else "ascii"
            return {"status": "ok", "command": "cfg",
                    "data": self.analyzer.cfg(addr, fmt)}

        if action == "entropy":
            return {"status": "ok", "command": "entropy",
                    "data": self.analyzer.entropy()}

        if action in ("hexdump", "hex", "hd"):
            parts = args.split()
            offset = 0
            length = 256
            if parts:
                try:
                    offset = int(parts[0], 16) if parts[0].startswith("0x") else int(parts[0])
                except ValueError:
                    pass
            if len(parts) > 1:
                try:
                    length = int(parts[1])
                except ValueError:
                    pass
            return {"status": "ok", "command": "hexdump",
                    "data": self.analyzer.hexdump(offset, length)}

        if action == "help":
            return {"status": "ok", "command": "help", "data": {
                "commands": [
                    "info               — Binary overview (format, arch, entry point)",
                    "headers            — Detailed header information",
                    "sections           — List all sections with permissions & entropy",
                    "imports            — Imported functions by library",
                    "exports            — Exported functions",
                    "strings [min] [n]  — Extract readable strings (default min=4, limit=500)",
                    "functions          — Discover function boundaries",
                    "disassemble [addr] [n] — Static disassembly (default: entry point, 20 insns)",
                    "xrefs [addr]       — Cross-references (overall summary or to/from address)",
                    "cfg <addr> [fmt]   — Control flow graph (ascii/mermaid/json)",
                    "entropy            — Section entropy analysis (detect packing)",
                    "hexdump [off] [n]  — Raw hex dump at file offset",
                    "ping               — Check server is alive",
                    "quit               — Stop server",
                ],
            }}

        return {"status": "error",
                "message": f"Unknown command: '{action}'. Use 'help' for available commands."}


# ======================================================================
#  Daemonize
# ======================================================================

def _daemonize_serve(args: argparse.Namespace):
    cmd = [sys.executable, os.path.abspath(__file__), 'serve', args.target]
    cmd.extend(['--port', str(args.port)])

    log_dir = os.path.join(os.path.expanduser("~"), ".NeuralDebug", "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"re_server_{args.port}.log")
    log_fh = open(log_file, 'w')

    kwargs = dict(stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT)
    if sys.platform == 'win32':
        kwargs['creationflags'] = 0x00000200 | 0x08000000
    else:
        kwargs['start_new_session'] = True

    proc = subprocess.Popen(cmd, **kwargs)
    log_fh.close()

    pid_file = get_pid_file(PID_KEY, args.port)
    with open(pid_file, 'w') as f:
        f.write(str(proc.pid))

    print(json.dumps({
        "status": "launched", "pid": proc.pid, "port": args.port,
        "log_file": log_file,
        "message": f"RE server launched (PID {proc.pid}) on port {args.port}.",
    }, indent=2))


# ======================================================================
#  Serve
# ======================================================================

def cmd_serve(args: argparse.Namespace):
    if getattr(args, 'daemonize', False):
        _daemonize_serve(args)
        return

    write_pid_file(PID_KEY, args.port)

    target = args.target
    if not target:
        print("Error: target binary is required.", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(target):
        print(f"Error: file not found: {target}", file=sys.stderr)
        sys.exit(1)

    try:
        analyzer = BinaryAnalyzer(target)
    except Exception as e:
        print(f"Error analyzing binary: {e}", file=sys.stderr)
        sys.exit(1)

    server = REServer(analyzer, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')

    def shutdown(signum, frame):
        print("\nShutting down...")
        server.running = False

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        server.run()
    finally:
        remove_pid_file(PID_KEY, args.port)


# ======================================================================
#  Other commands
# ======================================================================

def cmd_send(args):
    import shlex
    command_parts = args.command
    if command_parts and command_parts[0] == "--":
        command_parts = command_parts[1:]
    if not command_parts:
        print(json.dumps({"status": "error", "message": "No command provided"}))
        return
    action = command_parts[0]
    cmd_args = " ".join(
        shlex.quote(p) if " " in p else p for p in command_parts[1:]
    ) if len(command_parts) > 1 else ""
    timeout = getattr(args, "timeout", 120) or 120
    host = getattr(args, 'host', '127.0.0.1') or '127.0.0.1'
    result = send_command(args.port, action, cmd_args, timeout=timeout, host=host)
    print(json.dumps(result, indent=2))


def cmd_status(args: argparse.Namespace):
    host = getattr(args, 'host', '127.0.0.1') or '127.0.0.1'
    pid = read_pid_file(PID_KEY, args.port)
    try:
        result = send_command(args.port, "ping", host=host)
        result["server_running"] = True
        if pid is not None:
            result["pid"] = pid
        print(json.dumps(result, indent=2))
    except Exception:
        print(json.dumps({
            "server_running": False, "status": "offline", "pid": pid,
            "message": f"No RE server responding on port {args.port}",
        }, indent=2))


def cmd_stop(args: argparse.Namespace):
    host = getattr(args, 'host', '127.0.0.1') or '127.0.0.1'
    try:
        send_command(args.port, "quit", host=host)
        print(json.dumps({"status": "stopped",
                           "message": f"RE server on port {args.port} stopped."},
                          indent=2))
    except Exception:
        pid = read_pid_file(PID_KEY, args.port)
        if pid:
            try:
                if sys.platform == 'win32':
                    subprocess.run(['taskkill', '/F', '/PID', str(pid)],
                                   capture_output=True)
                else:
                    os.kill(pid, signal.SIGTERM)
                print(json.dumps({"status": "killed", "pid": pid}, indent=2))
            except Exception as e:
                print(json.dumps({"status": "error",
                                   "message": f"Failed to kill PID {pid}: {e}"},
                                  indent=2))
            remove_pid_file(PID_KEY, args.port)
        else:
            print(json.dumps({"status": "not_found",
                               "message": f"No RE server found on port {args.port}."},
                              indent=2))


# ======================================================================
#  CLI
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Reverse Engineering Session — Static Binary Analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Commands:
              info                Binary overview (format, arch, entry point, size)
              headers             Detailed PE/ELF header information
              sections            List all sections with permissions and entropy
              imports             Imported functions grouped by library
              exports             Exported function names and addresses
              strings [min] [n]   Extract readable strings (min length, limit count)
              functions           Discover function boundaries (prologue + call targets)
              disassemble [a] [n] Static disassembly at address (default: entry point)
              xrefs [address]     Cross-references summary or to/from specific address
              cfg <addr> [fmt]    Control flow graph (ascii, mermaid, json)
              entropy             Section entropy analysis (detect packed/encrypted)
              hexdump [off] [n]   Raw hex dump at file offset

            Examples:
              python re_session.py serve program.exe --port 5695 --daemonize
              python re_session.py cmd -p 5695 info
              python re_session.py cmd -p 5695 strings 6
              python re_session.py cmd -p 5695 imports
              python re_session.py cmd -p 5695 functions
              python re_session.py cmd -p 5695 disassemble 0x401000 50
              python re_session.py cmd -p 5695 cfg 0x401000 mermaid
              python re_session.py cmd -p 5695 xrefs 0x401000
              python re_session.py cmd -p 5695 entropy
              python re_session.py stop --port 5695
        """),
    )
    subparsers = parser.add_subparsers(dest="mode", help="Mode of operation")

    # --- serve ---
    srv_p = subparsers.add_parser("serve", help="Start RE analysis server")
    srv_p.add_argument("target", help="Binary file to analyze")
    srv_p.add_argument("--port", "-p", type=int, default=DEFAULT_PORT)
    srv_p.add_argument("--host", default="127.0.0.1",
                       help="Host/IP to bind to (default: 127.0.0.1, "
                            "use 0.0.0.0 to accept remote connections)")
    srv_p.add_argument("--daemonize", action="store_true", default=False)

    # --- cmd ---
    cmd_p = subparsers.add_parser("cmd", help="Send a command")
    cmd_p.add_argument("--port", "-p", type=int, default=DEFAULT_PORT)
    cmd_p.add_argument("--host", default="127.0.0.1",
                       help="Host/IP of the debug server (default: 127.0.0.1)")
    cmd_p.add_argument("--timeout", "-t", type=int, default=120)
    cmd_p.add_argument("command", nargs="+", help="Command and arguments")

    # --- status ---
    st_p = subparsers.add_parser("status", help="Check server status")
    st_p.add_argument("--port", "-p", type=int, default=DEFAULT_PORT)
    st_p.add_argument("--host", default="127.0.0.1",
                      help="Host/IP of the debug server (default: 127.0.0.1)")

    # --- stop ---
    stp_p = subparsers.add_parser("stop", help="Stop the server")
    stp_p.add_argument("--port", "-p", type=int, default=DEFAULT_PORT)
    stp_p.add_argument("--host", default="127.0.0.1",
                       help="Host/IP of the debug server (default: 127.0.0.1)")

    args = parser.parse_args()

    if args.mode == "serve":
        cmd_serve(args)
    elif args.mode == "cmd":
        cmd_send(args)
    elif args.mode == "status":
        cmd_status(args)
    elif args.mode == "stop":
        cmd_stop(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
