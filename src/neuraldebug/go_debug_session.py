#!/usr/bin/env python3
"""Go debug session — drives Delve (dlv) in CLI mode."""

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

from debuggers.go import (
    GoToolchainInfo, DelveDebugger, GoDebugServer,
    build_go_binary, _find_module_root,
)


# Auto-discovery metadata — used by language_registry.py
LANGUAGE_META = {
    "name": "go",
    "display_name": "Go",
    "extensions": [".go"],
    "default_port": 5682,
    "debuggers": "Delve (dlv)",
    "aliases": [],
}


GO_SOURCE_EXTENSIONS = {'.go'}




def cmd_serve(args: argparse.Namespace):
    write_pid_file('go', args.port)
    target = args.target
    program_args = getattr(args, 'args', '') or ''
    attach_pid = getattr(args, 'attach_pid', None)

    if attach_pid:
        toolchain = GoToolchainInfo()
        if not toolchain.dlv_info:
            print("Error: Delve not found.\n" + toolchain._install_instructions(),
                  file=sys.stderr)
            sys.exit(1)
        dlv_path = toolchain.dlv_info['path']

        print(f"Attaching to PID {attach_pid} using Delve ({dlv_path})")
        debugger = DelveDebugger(
            target or '.', dlv_path,
            attach_pid=attach_pid,
        )
        server = GoDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')

        def shutdown(signum, frame):
            print("\nShutting down...")
            server.running = False
        signal.signal(signal.SIGINT, shutdown)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, shutdown)

        try:
            server.run()
        finally:
            remove_pid_file('go', args.port)
        return

    if not target:
        print("Error: target is required.", file=sys.stderr)
        sys.exit(1)

    abs_target = os.path.abspath(target)
    is_binary = False
    source_root = os.getcwd()

    # Determine if target is a pre-built binary or needs building
    if os.path.isfile(abs_target) and not target.endswith('.go'):
        # Pre-built binary
        is_binary = True
        target = abs_target
        # Try to find source root
        mod_root = _find_module_root(os.path.dirname(abs_target))
        if mod_root:
            source_root = mod_root

    elif target.endswith('.go'):
        # Single .go file — build it first
        if not os.path.isfile(target):
            print(f"Error: file not found: {target}", file=sys.stderr)
            sys.exit(1)
        print(f"Building {target} with debug symbols...")
        source_root = os.path.dirname(os.path.abspath(target)) or os.getcwd()
        try:
            binary_path, build_msg = build_go_binary(
                target, cwd=source_root)
            print(build_msg)
            target = binary_path
            is_binary = True
        except (FileNotFoundError, RuntimeError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    elif os.path.isdir(abs_target) or target == '.':
        # Directory — detect package and build
        project_dir = abs_target if target != '.' else os.getcwd()
        source_root = project_dir
        mod_root = _find_module_root(project_dir)
        if mod_root:
            source_root = mod_root

        pkg = _detect_main_package(project_dir)
        if not pkg:
            pkg = '.'

        print(f"Building package '{pkg}' with debug symbols...")
        try:
            binary_path, build_msg = build_go_binary(
                pkg, cwd=source_root)
            print(build_msg)
            target = binary_path
            is_binary = True
        except (FileNotFoundError, RuntimeError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        # Assume it's a package path — build it
        mod_root = _find_module_root(os.getcwd())
        if mod_root:
            source_root = mod_root

        print(f"Building package '{target}' with debug symbols...")
        try:
            binary_path, build_msg = build_go_binary(
                target, cwd=source_root)
            print(build_msg)
            target = binary_path
            is_binary = True
        except (FileNotFoundError, RuntimeError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    # Find Delve
    toolchain = GoToolchainInfo()
    rec = toolchain.recommend()
    dlv_path = None
    if rec.get('debugger'):
        dlv_path = rec['debugger']['path']
    else:
        print(f"Error: Delve not found.\n{rec['note']}", file=sys.stderr)
        sys.exit(1)

    print(f"Platform: {sys.platform} ({platform.machine()})")
    print(f"Using debugger: Delve ({dlv_path})")
    print(f"Target: {target}")
    print(f"Source root: {source_root}")
    if toolchain.go_info:
        print(f"Go: {toolchain.go_info['version']}")
    if toolchain.dlv_info:
        print(f"Delve: {toolchain.dlv_info['version']}")

    debugger = DelveDebugger(
        target, dlv_path,
        source_root=source_root,
        program_args=program_args.strip() or None,
        is_binary=is_binary,
    )
    server = GoDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')

    def shutdown(signum, frame):
        print("\nShutting down...")
        server.running = False
    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        server.run()
    finally:
        remove_pid_file('go', args.port)


def cmd_send(args):
    cmd_send_handler(args)


def cmd_info(args: Optional[argparse.Namespace] = None):
    toolchain = GoToolchainInfo()
    info = toolchain.to_dict()
    print(json.dumps(info, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Go Debug Session - Interactive via Delve",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Detect Go toolchain:
              python go_debug_session.py info

              # Start debug server (single .go file -- auto-builds):
              python go_debug_session.py serve main.go --port 5682

              # Start debug server (pre-built binary):
              python go_debug_session.py serve ./myapp --port 5682

              # Start debug server (current directory -- auto-builds):
              python go_debug_session.py serve . --port 5682

              # Start debug server (package path):
              python go_debug_session.py serve ./cmd/myapp --port 5682

              # Send commands:
              python go_debug_session.py cmd --port 5682 b main.go:42
              python go_debug_session.py cmd --port 5682 start
              python go_debug_session.py cmd --port 5682 continue
              python go_debug_session.py cmd --port 5682 inspect
              python go_debug_session.py cmd --port 5682 "e mySlice[0]"
              python go_debug_session.py cmd --port 5682 goroutines
              python go_debug_session.py cmd --port 5682 quit
        """),
    )
    subparsers = parser.add_subparsers(dest="mode", help="Mode of operation")

    serve_parser = subparsers.add_parser("serve", help="Start the debug server")
    serve_parser.add_argument(
        "target", nargs='?', default=None,
        help="Go binary, .go file, package path, or '.' to debug",
    )
    serve_parser.add_argument(
        "--port", "-p", type=int, default=5682,
        help="TCP port (default: 5682)",
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
        help="Attach to a running Go process by PID instead of launching",
    )

    cmd_parser = subparsers.add_parser("cmd", help="Send a command to the server")
    cmd_parser.add_argument(
        "--port", "-p", type=int, default=5682,
        help="TCP port (default: 5682)",
    )
    cmd_parser.add_argument("--host", default="127.0.0.1",
                            help="Host/IP of the debug server (default: 127.0.0.1)")
    cmd_parser.add_argument(
        "command", nargs="+",
        help="Command (e.g., 'start', 'b main.go:42', 'continue', 'inspect')",
    )

    subparsers.add_parser("info", help="Detect Go toolchain and Delve")

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

