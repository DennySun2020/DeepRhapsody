#!/usr/bin/env python3
"""Node.js/TypeScript debug session — drives the built-in node inspector."""

import argparse
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from debug_common import (
    BaseDebugServer, DebugResponseMixin, error_response, completed_response,
    send_command, recv_all, send_response,
    get_pid_file, write_pid_file, remove_pid_file,
    find_repo_root, cmd_send_handler,
)

from debuggers.nodejs import (
    NodeToolchainInfo, NodeInspectorDebugger, NodeDebugServer,
    _resolve_target, compile_typescript,
)


# Auto-discovery metadata — used by language_registry.py
LANGUAGE_META = {
    "name": "nodejs",
    "display_name": "Node.js/TypeScript",
    "extensions": [".js", ".ts", ".mjs"],
    "default_port": 5683,
    "debuggers": "Node Inspector",
    "aliases": ["typescript"],
}


NODEJS_SOURCE_EXTENSIONS = {'.js', '.mjs', '.cjs', '.ts', '.mts', '.cts'}




def cmd_serve(args: argparse.Namespace):
    write_pid_file('nodejs', args.port)
    target = args.target
    program_args = getattr(args, 'args', '') or ''
    attach_pid = getattr(args, 'attach_pid', None)

    if attach_pid:
        node = shutil.which('node')
        if not node:
            toolchain = NodeToolchainInfo()
            print(f"Error: Node.js not found.\n{toolchain._install_instructions()}",
                  file=sys.stderr)
            sys.exit(1)

        print(f"Attaching to PID {attach_pid} using node inspect ({node})")
        debugger = NodeInspectorDebugger(
            target or '', node,
            attach_pid=attach_pid,
        )
        server = NodeDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')

        def shutdown(signum, frame):
            print("\nShutting down...")
            server.running = False
        signal.signal(signal.SIGINT, shutdown)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, shutdown)

        try:
            server.run()
        finally:
            remove_pid_file('nodejs', args.port)
        return

    if not target:
        print("Error: target is required.", file=sys.stderr)
        sys.exit(1)

    # Resolve target (handle TS, directories, package.json)
    try:
        script_file, ts_mode = _resolve_target(target)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Find node
    node = shutil.which('node')
    if not node:
        toolchain = NodeToolchainInfo()
        print(f"Error: Node.js not found.\n{toolchain._install_instructions()}",
              file=sys.stderr)
        sys.exit(1)

    print(f"Platform: {sys.platform} ({platform.machine()})")
    print(f"Using debugger: node inspect ({node})")
    print(f"Script: {script_file}")
    if ts_mode:
        print(f"TypeScript mode: {ts_mode}")

    debugger = NodeInspectorDebugger(
        script_file, node,
        program_args=program_args.strip() or None,
        ts_mode=ts_mode,
    )
    server = NodeDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')

    def shutdown(signum, frame):
        print("\nShutting down...")
        server.running = False
    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        server.run()
    finally:
        remove_pid_file('nodejs', args.port)


def cmd_send(args):
    cmd_send_handler(args)


def cmd_info(args: Optional[argparse.Namespace] = None):
    toolchain = NodeToolchainInfo()
    info = toolchain.to_dict()
    print(json.dumps(info, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Node.js Debug Session - Interactive via Node Inspector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Detect Node.js toolchain:
              python nodejs_debug_session.py info

              # Start debug server (JavaScript file):
              python nodejs_debug_session.py serve app.js --port 5683

              # Start debug server (TypeScript -- auto-compiles or uses ts-node):
              python nodejs_debug_session.py serve src/index.ts --port 5683

              # Start debug server (directory -- reads package.json "main"):
              python nodejs_debug_session.py serve ./myproject --port 5683

              # Send commands:
              python nodejs_debug_session.py cmd --port 5683 b app.js:42
              python nodejs_debug_session.py cmd --port 5683 start
              python nodejs_debug_session.py cmd --port 5683 continue
              python nodejs_debug_session.py cmd --port 5683 inspect
              python nodejs_debug_session.py cmd --port 5683 "e myArray.length"
              python nodejs_debug_session.py cmd --port 5683 quit
        """),
    )
    subparsers = parser.add_subparsers(dest="mode", help="Mode of operation")

    serve_parser = subparsers.add_parser("serve", help="Start the debug server")
    serve_parser.add_argument(
        "target", nargs='?', default=None,
        help="JavaScript or TypeScript file, or directory with package.json",
    )
    serve_parser.add_argument(
        "--port", "-p", type=int, default=5683,
        help="TCP port (default: 5683)",
    )
    serve_parser.add_argument("--host", default="127.0.0.1",
                              help="Host/IP to bind to (default: 127.0.0.1, "
                                   "use 0.0.0.0 to accept remote connections)")
    serve_parser.add_argument(
        "--args", "-a", type=str, default="",
        help="Arguments to pass to the target program",
    )
    serve_parser.add_argument(
        "--attach_pid", type=int, default=None, metavar="PID",
        help="Attach to a running Node.js process by PID instead of launching",
    )

    cmd_parser = subparsers.add_parser("cmd", help="Send a command to the server")
    cmd_parser.add_argument(
        "--port", "-p", type=int, default=5683,
        help="TCP port (default: 5683)",
    )
    cmd_parser.add_argument("--host", default="127.0.0.1",
                            help="Host/IP of the debug server (default: 127.0.0.1)")
    cmd_parser.add_argument(
        "command", nargs="+",
        help="Command (e.g., 'start', 'b app.js:42', 'continue', 'inspect')",
    )

    subparsers.add_parser("info", help="Detect Node.js toolchain")

    args = parser.parse_args()

    if args.mode == "serve":
        cmd_serve(args)
    elif args.mode == "cmd":
        cmd_send(args)
    elif args.mode == "info":
        cmd_info(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

