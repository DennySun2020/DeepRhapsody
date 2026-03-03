#!/usr/bin/env python3
"""Java debug session — drives JDB (Java Debugger)."""

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

from debuggers.java import (
    JavaToolchainInfo, JdbDebugger, JavaDebugServer,
    compile_java, build_maven_project, build_gradle_project,
    _detect_java_build,
)


# Auto-discovery metadata — used by language_registry.py
LANGUAGE_META = {
    "name": "java",
    "display_name": "Java",
    "extensions": [".java", ".class", ".jar"],
    "default_port": 5681,
    "debuggers": "JDB",
    "aliases": [],
}


JAVA_SOURCE_EXTENSIONS = {'.java'}




def cmd_serve(args: argparse.Namespace):
    write_pid_file('java', args.port)
    target = args.target
    program_args = getattr(args, 'args', '') or ''
    classpath = getattr(args, 'classpath', None)
    srcpaths = [p for p in (getattr(args, 'srcpath', None) or []) if p]
    attach_pid = getattr(args, 'attach_pid', None)

    if attach_pid:
        jdb = shutil.which('jdb')
        if not jdb:
            toolchain = JavaToolchainInfo()
            print(f"Error: JDB not found.\n{toolchain._install_instructions()}",
                  file=sys.stderr)
            sys.exit(1)

        print(f"Attaching to PID {attach_pid} using JDB ({jdb})")
        debugger = JdbDebugger(
            target or '', jdb,
            source_paths=srcpaths,
            attach_pid=attach_pid,
        )
        server = JavaDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')

        def shutdown(signum, frame):
            print("\nShutting down...")
            server.running = False
        signal.signal(signal.SIGINT, shutdown)
        if hasattr(signal, 'SIGTERM'):
            signal.signal(signal.SIGTERM, shutdown)

        try:
            server.run()
        finally:
            remove_pid_file('java', args.port)
        return

    if not target:
        print("Error: target is required.", file=sys.stderr)
        sys.exit(1)

    is_jar = False
    abs_target = os.path.abspath(target)

    # Check if target is a .java file (auto-compile)
    if target.endswith('.java'):
        if not os.path.isfile(target):
            print(f"Error: file not found: {target}", file=sys.stderr)
            sys.exit(1)
        print(f"Compiling {target} with debug symbols...")
        try:
            class_name, build_msg = compile_java(target, classpath=classpath)
            print(build_msg)
            target = class_name
            if not classpath:
                classpath = os.path.dirname(os.path.abspath(args.target))
        except (FileNotFoundError, RuntimeError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    # Check if target is a .jar file
    elif target.endswith('.jar'):
        if not os.path.isfile(target):
            print(f"Error: JAR not found: {target}", file=sys.stderr)
            sys.exit(1)
        is_jar = True
        if not classpath:
            classpath = os.path.abspath(target)

    # Check if target is a directory (Maven/Gradle project)
    elif os.path.isdir(abs_target):
        build_sys = _detect_java_build(abs_target)
        if build_sys:
            print(f"Detected {build_sys['name']} project. Building...")
            try:
                if build_sys['name'] == 'maven':
                    classes_dir, msg = build_maven_project(abs_target)
                elif build_sys['name'] == 'gradle':
                    classes_dir, msg = build_gradle_project(abs_target)
                else:
                    print(f"Error: unsupported build system: {build_sys['name']}",
                          file=sys.stderr)
                    sys.exit(1)
                print(msg)
                classpath = classes_dir
                # Need the main class name
                print("Error: Please specify the main class name as target "
                      "instead of the project directory.", file=sys.stderr)
                sys.exit(1)
            except (FileNotFoundError, RuntimeError) as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Error: no build system detected in {target}",
                  file=sys.stderr)
            sys.exit(1)

    # Auto-detect source paths
    if not srcpaths:
        repo_root = find_repo_root(os.getcwd())
        if repo_root:
            for sp in ['src/main/java', 'src', '.']:
                full = os.path.join(repo_root, sp)
                if os.path.isdir(full):
                    srcpaths.append(full)

    # Find JDB
    jdb = shutil.which('jdb')
    if not jdb:
        toolchain = JavaToolchainInfo()
        print(f"Error: JDB not found.\n{toolchain._install_instructions()}",
              file=sys.stderr)
        sys.exit(1)

    print(f"Platform: {sys.platform} ({platform.machine()})")
    print(f"Using debugger: JDB ({jdb})")
    print(f"Main class: {target}")
    if classpath:
        print(f"Classpath: {classpath}")

    debugger = JdbDebugger(
        target, jdb,
        classpath=classpath,
        source_paths=srcpaths,
        program_args=program_args.strip() or None,
        is_jar=is_jar,
    )
    server = JavaDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')

    def shutdown(signum, frame):
        print("\nShutting down...")
        server.running = False
    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        server.run()
    finally:
        remove_pid_file('java', args.port)


def cmd_send(args):
    cmd_send_handler(args)


def cmd_info(args: Optional[argparse.Namespace] = None):
    toolchain = JavaToolchainInfo()
    info = toolchain.to_dict()
    print(json.dumps(info, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Java Debug Session - Interactive via JDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Detect Java toolchain:
              python java_debug_session.py info

              # Start debug server (single .java file -- auto-compiles):
              python java_debug_session.py serve Main.java --port 5681

              # Start debug server (class name with classpath):
              python java_debug_session.py serve com.example.Main --classpath ./target/classes

              # Start debug server (JAR file):
              python java_debug_session.py serve app.jar --port 5681

              # Send commands:
              python java_debug_session.py cmd --port 5681 b Main:42
              python java_debug_session.py cmd --port 5681 start
              python java_debug_session.py cmd --port 5681 continue
              python java_debug_session.py cmd --port 5681 inspect
              python java_debug_session.py cmd --port 5681 "e myList.size()"
              python java_debug_session.py cmd --port 5681 quit
        """),
    )
    subparsers = parser.add_subparsers(dest="mode", help="Mode of operation")

    serve_parser = subparsers.add_parser("serve", help="Start the debug server")
    serve_parser.add_argument(
        "target", nargs='?', default=None,
        help="Java class name, .java file, or .jar file to debug",
    )
    serve_parser.add_argument(
        "--port", "-p", type=int, default=5681,
        help="TCP port (default: 5681)",
    )
    serve_parser.add_argument("--host", default="127.0.0.1",
                              help="Host/IP to bind to (default: 127.0.0.1, "
                                   "use 0.0.0.0 to accept remote connections)")
    serve_parser.add_argument(
        "--args", "-a", type=str, default="",
        help="Arguments to pass to the target program",
    )
    serve_parser.add_argument(
        "--classpath", "-cp", type=str, default=None,
        help="Java classpath",
    )
    serve_parser.add_argument(
        "--srcpath", type=str, nargs='*', default=None,
        help="Source paths for JDB source display",
    )
    serve_parser.add_argument(
        "--attach_pid", type=int, default=None, metavar="PID",
        help="Attach to a running Java process by PID instead of launching",
    )

    cmd_parser = subparsers.add_parser("cmd", help="Send a command to the server")
    cmd_parser.add_argument(
        "--port", "-p", type=int, default=5681,
        help="TCP port (default: 5681)",
    )
    cmd_parser.add_argument("--host", default="127.0.0.1",
                            help="Host/IP of the debug server (default: 127.0.0.1)")
    cmd_parser.add_argument(
        "command", nargs="+",
        help="Command (e.g., 'start', 'b Main:42', 'continue', 'inspect')",
    )

    subparsers.add_parser("info", help="Detect Java toolchain")

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

