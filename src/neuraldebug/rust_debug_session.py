#!/usr/bin/env python3
"""Rust debug session — drives GDB or LLDB, preferring rust-gdb/rust-lldb wrappers."""

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
    BaseDebugServer, MiDebuggerBase, GdbMiParser,
    error_response, completed_response,
    send_command, recv_all, send_response,
    get_pid_file, write_pid_file, remove_pid_file,
    find_repo_root, cmd_send_handler,
)

from debuggers.rust import (
    RustToolchainInfo, RustGdbDebugger, RustDebugServer,
    find_debugger, cargo_build,
)


# Auto-discovery metadata — used by language_registry.py
LANGUAGE_META = {
    "name": "rust",
    "display_name": "Rust",
    "extensions": [".rs"],
    "default_port": 5680,
    "debuggers": "rust-gdb / rust-lldb / GDB / LLDB / CDB",
    "aliases": [],
}


RUST_SOURCE_EXTENSIONS = {'.rs'}




def cmd_serve(args: argparse.Namespace):
    write_pid_file('rust', args.port)
    target = args.target
    program_args = getattr(args, 'args', '') or ''
    srcpaths = [p for p in (getattr(args, 'srcpath', None) or []) if p]
    binary_name = getattr(args, 'bin', None)
    attach_pid = getattr(args, 'attach_pid', None)

    if attach_pid:
        # Attach mode: target is optional (for symbols), skip build steps
        executable = ''
        if target and os.path.isfile(target):
            executable = target

        try:
            dbg_backend, dbg_path = find_debugger(
                getattr(args, 'debugger', None))
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        print(f"Attaching to PID {attach_pid} using {dbg_backend} ({dbg_path})")
        debugger = RustGdbDebugger(
            executable, dbg_path,
            source_paths=srcpaths,
            attach_pid=attach_pid,
        )
        server = RustDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')

        def shutdown(signum, frame):
            print("\nShutting down...")
            server.running = False
        signal.signal(signal.SIGINT, shutdown)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, shutdown)

        try:
            server.run()
        finally:
            remove_pid_file('rust', args.port)
        return

    if not target:
        print("Error: target is required.", file=sys.stderr)
        sys.exit(1)

    # Check if target is a directory with Cargo.toml (auto-build)
    abs_target = os.path.abspath(target)
    if os.path.isdir(abs_target):
        cargo_toml = os.path.join(abs_target, 'Cargo.toml')
        if os.path.isfile(cargo_toml):
            print("Detected Cargo project. Building with debug symbols...")
            try:
                exe_path, build_msg = cargo_build(
                    abs_target, binary_name=binary_name)
                print(build_msg)
                target = exe_path
            except (FileNotFoundError, RuntimeError) as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Error: no Cargo.toml in {target}", file=sys.stderr)
            sys.exit(1)
    elif target.endswith('.rs'):
        # Single .rs file -- compile with rustc
        if not os.path.isfile(target):
            print(f"Error: file not found: {target}", file=sys.stderr)
            sys.exit(1)
        rustc = shutil.which('rustc')
        if not rustc:
            print("Error: rustc not found. Install Rust via https://rustup.rs/",
                  file=sys.stderr)
            sys.exit(1)
        output = os.path.splitext(target)[0]
        if sys.platform == 'win32':
            output += '.exe'
        print(f"Compiling {target} with debug symbols...")
        result = subprocess.run(
            [rustc, '-g', '-o', output, target],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"Error: {result.stderr[:2000]}", file=sys.stderr)
            sys.exit(1)
        print(f"Compiled -> {output}")
        target = output
    else:
        if not os.path.isfile(target):
            print(f"Error: executable not found: {target}", file=sys.stderr)
            sys.exit(1)

    if not srcpaths:
        repo_root = find_repo_root(os.path.dirname(os.path.abspath(target)))
        if repo_root:
            srcpaths.append(repo_root)
            # Also add src/ directory commonly used in Rust projects
            src_dir = os.path.join(repo_root, 'src')
            if os.path.isdir(src_dir):
                srcpaths.append(src_dir)

    try:
        dbg_backend, dbg_path = find_debugger(
            getattr(args, 'debugger', None))
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Platform: {sys.platform} ({platform.machine()})")
    print(f"Using debugger: {dbg_backend} ({dbg_path})")

    debugger = RustGdbDebugger(
        target, dbg_path,
        source_paths=srcpaths,
        program_args=program_args.strip() or None,
    )
    server = RustDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')

    def shutdown(signum, frame):
        print("\nShutting down...")
        server.running = False
    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        server.run()
    finally:
        remove_pid_file('rust', args.port)


def cmd_send(args: argparse.Namespace):
    cmd_send_handler(args)


def cmd_info(args: Optional[argparse.Namespace] = None):
    toolchain = RustToolchainInfo()
    info = toolchain.to_dict()
    print(json.dumps(info, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Rust Debug Session - Interactive via GDB/LLDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Detect Rust toolchain and debuggers:
              python rust_debug_session.py info

              # Start debug server (Cargo project -- auto-builds):
              python rust_debug_session.py serve . --port 5680
              python rust_debug_session.py serve ./my_project --bin myapp

              # Start debug server (pre-built binary):
              python rust_debug_session.py serve ./target/debug/myapp --port 5680

              # Start debug server (single .rs file -- auto-compiles):
              python rust_debug_session.py serve main.rs --port 5680

              # Send commands:
              python rust_debug_session.py cmd --port 5680 b main.rs:42
              python rust_debug_session.py cmd --port 5680 start
              python rust_debug_session.py cmd --port 5680 continue
              python rust_debug_session.py cmd --port 5680 inspect
              python rust_debug_session.py cmd --port 5680 quit
        """),
    )
    subparsers = parser.add_subparsers(dest="mode", help="Mode of operation")

    serve_parser = subparsers.add_parser("serve", help="Start the debug server")
    serve_parser.add_argument(
        "target", nargs='?', default=None,
        help="Rust executable, .rs file, or Cargo project directory to debug",
    )
    serve_parser.add_argument(
        "--port", "-p", type=int, default=5680,
        help="TCP port (default: 5680)",
    )
    serve_parser.add_argument("--host", default="127.0.0.1",
                              help="Host/IP to bind to (default: 127.0.0.1, "
                                   "use 0.0.0.0 to accept remote connections)")
    serve_parser.add_argument(
        "--debugger", "-d", type=str, default=None,
        help="Force a specific debugger (rust-gdb, gdb, rust-lldb, lldb, cdb)",
    )
    serve_parser.add_argument(
        "--args", "-a", type=str, default="",
        help="Arguments to pass to the target program",
    )
    serve_parser.add_argument(
        "--srcpath", type=str, nargs='*', default=None,
        help="Additional source paths for the debugger",
    )
    serve_parser.add_argument(
        "--bin", type=str, default=None,
        help="Binary name for multi-binary Cargo projects",
    )
    serve_parser.add_argument(
        "--attach_pid", type=int, default=None, metavar="PID",
        help="Attach to a running process by PID instead of launching",
    )

    cmd_parser = subparsers.add_parser("cmd", help="Send a command to the server")
    cmd_parser.add_argument(
        "--port", "-p", type=int, default=5680,
        help="TCP port (default: 5680)",
    )
    cmd_parser.add_argument("--host", default="127.0.0.1",
                            help="Host/IP of the debug server (default: 127.0.0.1)")
    cmd_parser.add_argument(
        "command", nargs="+",
        help="Command (e.g., 'start', 'b main.rs:42', 'continue', 'inspect')",
    )

    subparsers.add_parser("info", help="Detect Rust toolchain and debuggers")

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

