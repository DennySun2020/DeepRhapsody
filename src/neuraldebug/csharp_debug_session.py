#!/usr/bin/env python3
"""C# debug session — drives netcoredbg via GDB/MI protocol."""

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
    BaseDebugServer, MiDebuggerBase, GdbMiParser as MiParser,
    error_response, completed_response,
    send_command, recv_all, send_response,
    get_pid_file, write_pid_file, remove_pid_file,
    find_repo_root, cmd_send_handler,
)

from debuggers.csharp import (
    DotNetToolchainInfo, NetcoredbgDebugger, CSharpDebugServer,
    find_debugger, build_dotnet_project,
)


# Auto-discovery metadata — used by language_registry.py
LANGUAGE_META = {
    "name": "csharp",
    "display_name": "C#",
    "extensions": [".cs", ".csproj", ".dll"],
    "default_port": 5679,
    "debuggers": "netcoredbg",
    "aliases": [],
}


CSHARP_SOURCE_EXTENSIONS = {'.cs'}
CSHARP_PROJECT_EXTENSIONS = {'.csproj', '.sln', '.fsproj', '.vbproj'}



def cmd_serve(args: argparse.Namespace):
    write_pid_file('csharp', args.port)
    target = args.target
    program_args = getattr(args, 'args', '') or ''
    srcpaths = [p for p in (getattr(args, 'srcpath', None) or []) if p]
    attach_pid = getattr(args, 'attach_pid', None)

    if attach_pid:
        try:
            dbg_name, dbg_path = find_debugger()
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        print(f"Attaching to PID {attach_pid} using {dbg_name} ({dbg_path})")
        debugger = NetcoredbgDebugger(
            target or '', dbg_path,
            source_paths=srcpaths,
            attach_pid=attach_pid,
        )
        server = CSharpDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')

        def shutdown(signum, frame):
            print("\nShutting down...")
            server.running = False
        signal.signal(signal.SIGINT, shutdown)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, shutdown)

        try:
            server.run()
        finally:
            remove_pid_file('csharp', args.port)
        return

    if not target:
        print("Error: target is required.", file=sys.stderr)
        sys.exit(1)

    ext = os.path.splitext(target)[1].lower()

    # Auto-build if target is a .csproj or .sln
    if ext in CSHARP_PROJECT_EXTENSIONS:
        if not os.path.isfile(target):
            print(f"Error: project file not found: {target}", file=sys.stderr)
            sys.exit(1)
        print(f"Detected project file ({ext}). Building with debug symbols...")
        try:
            dll_path, build_msg = build_dotnet_project(target)
            print(build_msg)
            target = dll_path
        except (FileNotFoundError, RuntimeError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    elif ext == '.cs':
        # Single .cs file -- try to compile with dotnet-script or csc
        print("Error: Single .cs files are not directly supported. "
              "Use a .csproj project or compile to a DLL first.",
              file=sys.stderr)
        sys.exit(1)
    else:
        if not os.path.isfile(target):
            print(f"Error: target not found: {target}", file=sys.stderr)
            sys.exit(1)

    if not srcpaths:
        repo_root = find_repo_root(os.path.dirname(os.path.abspath(target)))
        if repo_root:
            srcpaths.append(repo_root)

    try:
        dbg_name, dbg_path = find_debugger()
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Platform: {sys.platform} ({platform.machine()})")
    print(f"Using debugger: {dbg_name} ({dbg_path})")

    debugger = NetcoredbgDebugger(
        target, dbg_path,
        source_paths=srcpaths,
        program_args=program_args.strip() or None,
    )
    server = CSharpDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')

    def shutdown(signum, frame):
        print("\nShutting down...")
        server.running = False
    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        server.run()
    finally:
        remove_pid_file('csharp', args.port)


def cmd_send(args: argparse.Namespace):
    cmd_send_handler(args)


def cmd_info(args: Optional[argparse.Namespace] = None):
    toolchain = DotNetToolchainInfo()
    info = toolchain.to_dict()
    print(json.dumps(info, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="C# Debug Session - Interactive via netcoredbg",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Detect .NET SDK and debugger:
              python csharp_debug_session.py info

              # Start debug server (project file -- auto-builds):
              python csharp_debug_session.py serve MyApp.csproj --port 5679

              # Start debug server (pre-built DLL):
              python csharp_debug_session.py serve bin/Debug/net8.0/MyApp.dll --port 5679

              # Send commands:
              python csharp_debug_session.py cmd --port 5679 b Program.cs:42
              python csharp_debug_session.py cmd --port 5679 start
              python csharp_debug_session.py cmd --port 5679 continue
              python csharp_debug_session.py cmd --port 5679 inspect
              python csharp_debug_session.py cmd --port 5679 "e myList.Count"
              python csharp_debug_session.py cmd --port 5679 quit
        """),
    )
    subparsers = parser.add_subparsers(dest="mode", help="Mode of operation")

    serve_parser = subparsers.add_parser("serve", help="Start the debug server")
    serve_parser.add_argument(
        "target", nargs='?', default=None,
        help="C# project (.csproj/.sln) or compiled DLL/exe to debug",
    )
    serve_parser.add_argument(
        "--port", "-p", type=int, default=5679,
        help="TCP port (default: 5679)",
    )
    serve_parser.add_argument("--host", default="127.0.0.1",
                              help="Host/IP to bind to (default: 127.0.0.1, "
                                   "use 0.0.0.0 to accept remote connections)")
    serve_parser.add_argument(
        "--args", "-a", type=str, default="",
        help="Arguments to pass to the target program",
    )
    serve_parser.add_argument(
        "--srcpath", type=str, nargs='*', default=None,
        help="Additional source paths for the debugger",
    )
    serve_parser.add_argument(
        "--attach_pid", type=int, default=None, metavar="PID",
        help="Attach to a running .NET process by PID instead of launching",
    )

    cmd_parser = subparsers.add_parser("cmd", help="Send a command to the server")
    cmd_parser.add_argument(
        "--port", "-p", type=int, default=5679,
        help="TCP port (default: 5679)",
    )
    cmd_parser.add_argument("--host", default="127.0.0.1",
                            help="Host/IP of the debug server (default: 127.0.0.1)")
    cmd_parser.add_argument(
        "command", nargs="+",
        help="Command (e.g., 'start', 'b Program.cs:42', 'continue', 'inspect')",
    )

    subparsers.add_parser("info", help="Detect .NET SDK and debugger availability")

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
