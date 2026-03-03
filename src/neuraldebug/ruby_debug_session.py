#!/usr/bin/env python3
"""Ruby debug session — drives rdbg (debug.gem)."""

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

from debuggers.ruby import (
    RubyToolchainInfo, RdbgDebugger, RubyDebugServer,
    _detect_bundler_context,
)


# Auto-discovery metadata — used by language_registry.py
LANGUAGE_META = {
    "name": "ruby",
    "display_name": "Ruby",
    "extensions": [".rb"],
    "default_port": 5684,
    "debuggers": "rdbg (debug.gem)",
    "aliases": [],
}


RUBY_SOURCE_EXTENSIONS = {'.rb', '.rake', '.gemspec'}




def cmd_serve(args: argparse.Namespace):
    write_pid_file('ruby', args.port)
    target = args.target
    program_args = getattr(args, 'args', '') or ''
    use_bundler = getattr(args, 'bundler', False)
    attach_pid = getattr(args, 'attach_pid', None)

    if attach_pid:
        rdbg = shutil.which('rdbg')
        if not rdbg:
            toolchain = RubyToolchainInfo()
            print(f"Error: rdbg not found.\n{toolchain._install_instructions()}",
                  file=sys.stderr)
            sys.exit(1)

        print(f"Attaching to PID {attach_pid} using rdbg ({rdbg})")
        debugger = RdbgDebugger(
            target or '', rdbg,
            attach_pid=attach_pid,
        )
        server = RubyDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')

        def shutdown(signum, frame):
            print("\nShutting down...")
            server.running = False
        signal.signal(signal.SIGINT, shutdown)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, shutdown)

        try:
            server.run()
        finally:
            remove_pid_file('ruby', args.port)
        return

    if not target:
        print("Error: target is required.", file=sys.stderr)
        sys.exit(1)

    abs_target = os.path.abspath(target)

    # Validate target exists
    if not os.path.isfile(abs_target):
        print(f"Error: file not found: {target}", file=sys.stderr)
        sys.exit(1)

    # Auto-detect Bundler context
    if not use_bundler and _detect_bundler_context(abs_target):
        print("Detected Gemfile — using Bundler context (bundle exec).")
        use_bundler = True

    # Find rdbg
    rdbg = shutil.which('rdbg')
    if not rdbg:
        toolchain = RubyToolchainInfo()
        print(f"Error: rdbg not found.\n{toolchain._install_instructions()}",
              file=sys.stderr)
        sys.exit(1)

    print(f"Platform: {sys.platform} ({platform.machine()})")
    print(f"Using debugger: rdbg ({rdbg})")
    print(f"Script: {abs_target}")
    if use_bundler:
        print("Bundler: enabled (bundle exec)")

    debugger = RdbgDebugger(
        abs_target, rdbg,
        program_args=program_args.strip() or None,
        use_bundler=use_bundler,
    )
    server = RubyDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')

    def shutdown(signum, frame):
        print("\nShutting down...")
        server.running = False
    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        server.run()
    finally:
        remove_pid_file('ruby', args.port)


def cmd_send(args):
    cmd_send_handler(args)


def cmd_info(args: Optional[argparse.Namespace] = None):
    toolchain = RubyToolchainInfo()
    info = toolchain.to_dict()
    print(json.dumps(info, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Ruby Debug Session - Interactive via rdbg",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Detect Ruby toolchain:
              python ruby_debug_session.py info

              # Start debug server:
              python ruby_debug_session.py serve app.rb --port 5684

              # Start debug server with Bundler:
              python ruby_debug_session.py serve app.rb --bundler --port 5684

              # Start debug server with program arguments:
              python ruby_debug_session.py serve app.rb --args "input.txt --verbose"

              # Send commands:
              python ruby_debug_session.py cmd --port 5684 b 42
              python ruby_debug_session.py cmd --port 5684 "b MyClass#process"
              python ruby_debug_session.py cmd --port 5684 start
              python ruby_debug_session.py cmd --port 5684 continue
              python ruby_debug_session.py cmd --port 5684 inspect
              python ruby_debug_session.py cmd --port 5684 "e @users.length"
              python ruby_debug_session.py cmd --port 5684 quit
        """),
    )
    subparsers = parser.add_subparsers(dest="mode", help="Mode of operation")

    serve_parser = subparsers.add_parser("serve", help="Start the debug server")
    serve_parser.add_argument(
        "target", nargs='?', default=None,
        help="Ruby script (.rb) to debug",
    )
    serve_parser.add_argument(
        "--port", "-p", type=int, default=5684,
        help="TCP port (default: 5684)",
    )
    serve_parser.add_argument("--host", default="127.0.0.1",
                              help="Host/IP to bind to (default: 127.0.0.1, "
                                   "use 0.0.0.0 to accept remote connections)")
    serve_parser.add_argument(
        "--args", "-a", type=str, default="",
        help="Arguments to pass to the target program",
    )
    serve_parser.add_argument(
        "--bundler", "-B", action="store_true", default=False,
        help="Use 'bundle exec' to launch rdbg (for Bundler projects)",
    )
    serve_parser.add_argument(
        "--attach_pid", type=int, default=None, metavar="PID",
        help="Attach to a running Ruby process by PID instead of launching",
    )

    cmd_parser = subparsers.add_parser("cmd", help="Send a command to the server")
    cmd_parser.add_argument(
        "--port", "-p", type=int, default=5684,
        help="TCP port (default: 5684)",
    )
    cmd_parser.add_argument("--host", default="127.0.0.1",
                            help="Host/IP of the debug server (default: 127.0.0.1)")
    cmd_parser.add_argument(
        "command", nargs="+",
        help="Command (e.g., 'start', 'b 42', 'continue', 'inspect')",
    )

    subparsers.add_parser("info", help="Detect Ruby toolchain")

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

