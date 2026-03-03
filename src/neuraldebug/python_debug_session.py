#!/usr/bin/env python3
"""Python debug session — uses bdb (stdlib) with queue-based async I/O."""

import argparse
import bdb
import io
import json
import linecache
import os
import queue
import signal
import socket
import sys
import textwrap
import threading
import traceback
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


from debuggers.python_bdb import (
    InteractiveDebugger, DebugServer,
    safe_repr, safe_str, serialize_variable, is_user_frame,
)

from debug_common import (
    DebugResponseMixin, error_response, completed_response,
    send_command, recv_all, send_response,
    cmd_send_handler,
)

# Auto-discovery metadata — used by language_registry.py
LANGUAGE_META = {
    "name": "python",
    "display_name": "Python",
    "extensions": [".py"],
    "default_port": 5678,
    "debuggers": "bdb (stdlib)",
    "aliases": [],
}



def cmd_serve(args: argparse.Namespace):
    target = args.target
    port = args.port
    script_args = args.args.split() if args.args else []
    attach_pid = getattr(args, 'attach_pid', None)

    if attach_pid:
        print(json.dumps({
            "status": "error",
            "message": (
                f"Python's bdb debugger runs in-process and cannot attach to "
                f"PID {attach_pid}. To debug a running Python process, either:\n"
                f"  1. Add 'import debugpy; debugpy.listen(5678)' to the target, "
                f"then connect with a DAP client.\n"
                f"  2. Use GDB: python cpp_debug_session.py serve --attach_pid "
                f"{attach_pid} (works for CPython internals).\n"
                f"  3. Start the process under NeuralDebug from the beginning:\n"
                f"     python python_debug_session.py serve <script.py>"
            ),
        }))
        return

    if not target:
        print(json.dumps({"status": "error",
                           "message": "target is required (or use --attach_pid)"}))
        return

    if not os.path.isfile(target):
        print(json.dumps({"status": "error",
                          "message": f"File not found: {target}"}))
        return

    host = getattr(args, 'host', '127.0.0.1') or '127.0.0.1'
    debugger = InteractiveDebugger(target)
    server = DebugServer(debugger, port=port, host=host)

    def signal_handler(sig, frame):
        print("\nShutting down debug server...")
        server.running = False
        debugger.is_finished = True
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    server.start(script_args)


def cmd_send(args):
    cmd_send_handler(args)


def main():
    parser = argparse.ArgumentParser(
        description="Interactive Python Debug Session - Server & Client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Start debug server (in background terminal):
              python python_debug_session.py serve my_script.py --port 5678

              # Send commands (in foreground terminal):
              python python_debug_session.py cmd start
              python python_debug_session.py cmd b 42
              python python_debug_session.py cmd continue
              python python_debug_session.py cmd inspect
              python python_debug_session.py cmd "e len(my_list)"
              python python_debug_session.py cmd step_in
              python python_debug_session.py cmd quit
        """),
    )
    subparsers = parser.add_subparsers(dest="mode", help="Mode of operation")

    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the debug server with a target Python file",
    )
    serve_parser.add_argument("target", nargs='?', default=None,
                              help="Python file to debug")
    serve_parser.add_argument(
        "--port", "-p", type=int, default=5678,
        help="TCP port to listen on (default: 5678)",
    )
    serve_parser.add_argument("--host", default="127.0.0.1",
                              help="Host/IP to bind to (default: 127.0.0.1, "
                                   "use 0.0.0.0 to accept remote connections)")
    serve_parser.add_argument(
        "--args", "-a", type=str, default="",
        help="Arguments to pass to the target script",
    )
    serve_parser.add_argument(
        "--attach_pid", type=int, default=None, metavar="PID",
        help="Attach to a running Python process by PID (requires debugpy in target)",
    )

    cmd_parser = subparsers.add_parser(
        "cmd",
        help="Send a command to a running debug server",
    )
    cmd_parser.add_argument(
        "--port", "-p", type=int, default=5678,
        help="TCP port of the debug server (default: 5678)",
    )
    cmd_parser.add_argument("--host", default="127.0.0.1",
                            help="Host/IP of the debug server (default: 127.0.0.1)")
    cmd_parser.add_argument(
        "command", nargs="+",
        help="Command to send (e.g., 'start', 'b 42', 'continue', 'inspect')",
    )

    args = parser.parse_args()

    if args.mode == "serve":
        cmd_serve(args)
    elif args.mode == "cmd":
        cmd_send(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
