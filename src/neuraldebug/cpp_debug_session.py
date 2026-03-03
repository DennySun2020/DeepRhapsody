#!/usr/bin/env python3
"""C/C++ debug session — drives GDB (MI mode), LLDB, or CDB (Windows)."""

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
    DebugResponseMixin, error_response, completed_response,
    send_command, recv_all, send_response,
    get_pid_file, write_pid_file, remove_pid_file, read_pid_file,
    find_repo_root, cmd_send_handler,
)


from debuggers.cpp_common import (
    ToolchainInfo, CppDebugServer,
    find_debugger, create_debugger, compile_source,
    detect_build_system, find_binaries, scan_repo_context,
    SOURCE_EXTENSIONS, CPP_EXTENSIONS,
)

# Auto-discovery metadata — used by language_registry.py
LANGUAGE_META = {
    "name": "cpp",
    "display_name": "C/C++",
    "extensions": [".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".exe", ".out"],
    "default_port": 5678,
    "debuggers": "GDB / LLDB / CDB",
    "aliases": ["c"],
}

def _daemonize_serve(args: argparse.Namespace):
    """Re-launch the serve command as a fully detached OS process.

    Builds a new command line from the parsed args (without --daemonize),
    spawns it as an independent process, writes the PID file, and exits.
    """
    cmd = [sys.executable, os.path.abspath(__file__), 'serve']
    if getattr(args, 'target', None):
        cmd.append(args.target)
    cmd.extend(['--port', str(args.port)])
    if getattr(args, 'debugger', None):
        cmd.extend(['--debugger', args.debugger])
    # NOTE: program args are appended after '--' at the end of cmd
    # to avoid argparse misinterpreting values that start with '-'.
    program_args_val = getattr(args, 'args', None) or ''
    if getattr(args, 'srcpath', None):
        for sp in args.srcpath:
            cmd.extend(['--srcpath', sp])
    if getattr(args, 'attach_pid', None):
        cmd.extend(['--attach_pid', str(args.attach_pid)])
    if getattr(args, 'core', None):
        cmd.extend(['--core', args.core])
    # NOTE: --daemonize is intentionally NOT included

    # Append program args after '--' separator so argparse in the child
    # process won't misinterpret values that start with '-' as flags.
    if program_args_val:
        cmd.append('--')
        cmd.append(program_args_val)

    # Log file for server output
    tmpdir = os.environ.get('TEMP', os.environ.get('TMP', '/tmp'))
    log_file = os.path.join(tmpdir, f'NeuralDebug_server_{args.port}.log')

    try:
        log_fh = open(log_file, 'w')
    except OSError:
        log_fh = open(os.devnull, 'w')

    # Spawn as a fully detached process
    kwargs = dict(
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    if sys.platform == 'win32':
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        kwargs['creationflags'] = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        kwargs['start_new_session'] = True

    proc = subprocess.Popen(cmd, **kwargs)
    log_fh.close()  # parent closes its copy; child inherits the handle

    # Write PID file immediately (the child will also write it, but this
    # ensures stop/status work even before the child's TCP socket is ready)
    pid_file = get_pid_file('cpp', args.port)
    with open(pid_file, 'w') as f:
        f.write(str(proc.pid))

    result = {
        "status": "launched",
        "pid": proc.pid,
        "port": args.port,
        "log_file": log_file,
        "message": f"Debug server launched as daemon (PID {proc.pid}) on port {args.port}. "
                   f"Use 'status --port {args.port}' to check readiness, "
                   f"'stop --port {args.port}' to terminate.",
    }
    print(json.dumps(result, indent=2))


def cmd_serve(args: argparse.Namespace):
    """Start the C/C++ debug server.

    Supports three launch modes:
      1. Normal:  debug a compiled executable or source file
      2. Attach:  attach to a running process by PID
      3. Core:    analyse a core dump / crash dump

    Use --daemonize to launch the server as a fully detached OS process
    that survives terminal closure (recommended for AI agent workflows).
    """
    if getattr(args, 'daemonize', False):
        _daemonize_serve(args)
        return

    write_pid_file('cpp', args.port)

    attach_pid = getattr(args, 'attach_pid', None)
    core_dump = getattr(args, 'core', None)
    srcpaths = [p for p in (getattr(args, 'srcpath', None) or []) if p]
    program_args = getattr(args, 'args', '') or ''

    if attach_pid:
        executable = getattr(args, 'target', None) or ''
        if executable and not os.path.isfile(executable):
            executable = ''  # optional in attach mode
        try:
            dbg_type, dbg_path = find_debugger(args.debugger)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Attaching to PID {attach_pid} using {dbg_type} ({dbg_path})")
        debugger = create_debugger(
            executable, dbg_type, dbg_path,
            source_paths=srcpaths, attach_pid=attach_pid,
        )
        server = CppDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')
        _run_server(server)
        return

    if core_dump:
        if not os.path.isfile(core_dump):
            print(f"Error: core dump not found: {core_dump}", file=sys.stderr)
            sys.exit(1)
        executable = getattr(args, 'target', None) or ''
        if executable and not os.path.isfile(executable):
            print(f"Warning: executable not found ({executable}); "
                  "some analysis may be limited.", file=sys.stderr)
            executable = ''
        try:
            dbg_type, dbg_path = find_debugger(args.debugger)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Analysing core dump: {core_dump}")
        print(f"Using debugger: {dbg_type} ({dbg_path})")
        debugger = create_debugger(
            executable, dbg_type, dbg_path,
            source_paths=srcpaths, core_dump=core_dump,
        )
        server = CppDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')
        _run_server(server)
        return

    target = args.target
    if not target:
        print("Error: target is required in normal mode.", file=sys.stderr)
        sys.exit(1)

    ext = os.path.splitext(target)[1].lower()

    if ext in SOURCE_EXTENSIONS:
        if not os.path.isfile(target):
            print(f"Error: source file not found: {target}", file=sys.stderr)
            sys.exit(1)
        print(f"Detected source file ({ext}). Auto-compiling with debug symbols...")
        try:
            executable, compile_msg = compile_source(target)
            print(compile_msg)
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        executable = target
        if not os.path.isfile(executable):
            print(f"Error: executable not found: {executable}", file=sys.stderr)
            sys.exit(1)

    # Auto-detect repo root for source paths
    if not srcpaths:
        repo_root = find_repo_root(os.path.dirname(os.path.abspath(executable)))
        if repo_root:
            srcpaths.append(repo_root)

    try:
        dbg_type, dbg_path = find_debugger(args.debugger)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Platform: {sys.platform} ({platform.machine()})")
    print(f"Using debugger: {dbg_type} ({dbg_path})")
    if srcpaths:
        print(f"Source paths: {', '.join(srcpaths)}")
    debugger = create_debugger(
        executable, dbg_type, dbg_path,
        source_paths=srcpaths, program_args=program_args.strip() or None,
    )
    server = CppDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')
    _run_server(server)


def _run_server(server):
    def shutdown(signum, frame):
        print("\nShutting down...")
        server.running = False
    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, shutdown)
    try:
        server.run()
    finally:
        remove_pid_file('cpp', server.port)


def cmd_send(args):
    cmd_send_handler(args)


def cmd_info(args: Optional[argparse.Namespace] = None):
    toolchain = ToolchainInfo()
    info = toolchain.to_dict()

    # If a repo root is provided or we're in one, add build system + context
    repo_root = getattr(args, 'repo', None) if args else None
    if not repo_root:
        repo_root = find_repo_root(os.getcwd())
    if repo_root:
        info['repo_context'] = scan_repo_context(repo_root)
    else:
        # Still try build system at cwd
        bs = detect_build_system(os.getcwd())
        if bs:
            info['build_system'] = bs

    print(json.dumps(info, indent=2))


def cmd_compile(args: argparse.Namespace):
    compiler_info = None
    if args.compiler:
        compiler_arg = args.compiler.strip()
        compiler_lower = compiler_arg.lower()

        # Handle well-known compiler names as aliases
        if compiler_lower in ('msvc', 'cl', 'cl.exe'):
            toolchain = ToolchainInfo()
            msvc = next((c for c in toolchain.compilers
                         if c['name'] == 'msvc'), None)
            if msvc:
                compiler_info = msvc
            else:
                print("Error: MSVC (cl.exe) not found.", file=sys.stderr)
                sys.exit(1)
        elif compiler_lower in ('gcc', 'g++', 'clang', 'clang++',
                                'gcc.exe', 'g++.exe', 'clang.exe', 'clang++.exe'):
            name = compiler_lower.replace('.exe', '')
            toolchain = ToolchainInfo()
            found = next((c for c in toolchain.compilers
                          if c['name'] == name), None)
            if found:
                compiler_info = found
            else:
                path = shutil.which(name)
                if path:
                    compiler_info = {
                        'name': name, 'path': path,
                        'version': '', 'debug_format': 'dwarf',
                    }
                else:
                    print(f"Error: {name} not found.", file=sys.stderr)
                    sys.exit(1)
        else:
            # User provided a direct path to a compiler binary
            raw_name = os.path.basename(compiler_arg).lower().replace('.exe', '')
            is_msvc = (raw_name == 'cl')
            resolved = compiler_arg if os.path.isfile(compiler_arg) else shutil.which(compiler_arg)
            if not resolved:
                print(f"Error: compiler not found: {compiler_arg}", file=sys.stderr)
                sys.exit(1)
            compiler_info = {
                'name': 'msvc' if is_msvc else raw_name,
                'path': resolved,
                'version': '',
                'debug_format': 'pdb' if is_msvc else 'dwarf',
            }

    flags = args.flags.split() if args.flags.strip() else None

    try:
        exe_path, msg = compile_source(
            args.source,
            output=args.output,
            compiler_info=compiler_info,
            extra_flags=flags,
        )
        print(msg)
        print(f"Executable: {exe_path}")
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="C/C++ Debug Session - Interactive via GDB/LLDB/CDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Detect platform, compilers, debuggers, build system, repo context:
              python cpp_debug_session.py info
              python cpp_debug_session.py info --repo /path/to/repo

              # Compile a source file with debug symbols (auto-detect compiler):
              python cpp_debug_session.py compile my_program.c
              python cpp_debug_session.py compile my_program.cpp -o out.exe

              # Start debug server (executable or source file -- auto-compiles):
              python cpp_debug_session.py serve ./my_program --port 5678
              python cpp_debug_session.py serve my_program.c --port 5678
              python cpp_debug_session.py serve ./my_program --debugger gdb
              python cpp_debug_session.py serve ./my_program --args "arg1 arg2"
              python cpp_debug_session.py serve ./my_program --srcpath /repo/src

              # Attach to a running process:
              python cpp_debug_session.py serve --attach_pid 12345
              python cpp_debug_session.py serve ./my_program --attach_pid 12345

              # Analyse a core dump / crash dump:
              python cpp_debug_session.py serve ./my_program --core core.12345
              python cpp_debug_session.py serve --core dump.dmp

              # Detect build system:
              python cpp_debug_session.py build-info
              python cpp_debug_session.py build-info --repo /path/to/repo

              # Find binaries in build output:
              python cpp_debug_session.py find-binary --dir build/
              python cpp_debug_session.py find-binary --hint myapp --test

              # Scan full repo context:
              python cpp_debug_session.py repo-context
              python cpp_debug_session.py repo-context --repo /path/to/repo

              # Send commands:
              python cpp_debug_session.py cmd b main
              python cpp_debug_session.py cmd b main.c:42
              python cpp_debug_session.py cmd start
              python cpp_debug_session.py cmd continue
              python cpp_debug_session.py cmd inspect
              python cpp_debug_session.py cmd "e sizeof(buffer)"
              python cpp_debug_session.py cmd step_in
              python cpp_debug_session.py cmd quit
        """),
    )
    subparsers = parser.add_subparsers(dest="mode", help="Mode of operation")

    serve_parser = subparsers.add_parser("serve", help="Start the debug server")
    serve_parser.add_argument(
        "target", nargs='?', default=None,
        help="C/C++ executable or source file (.c/.cpp) to debug. "
             "Source files are auto-compiled with debug symbols. "
             "Optional when using --attach_pid or --core.",
    )
    serve_parser.add_argument(
        "--port", "-p", type=int, default=5678,
        help="TCP port (default: 5678)",
    )
    serve_parser.add_argument("--host", default="127.0.0.1",
                              help="Host/IP to bind to (default: 127.0.0.1, "
                                   "use 0.0.0.0 to accept remote connections)")
    serve_parser.add_argument(
        "--debugger", "-d", type=str, default=None,
        choices=["gdb", "lldb", "cdb"],
        help="Force a specific debugger (default: auto-detect)",
    )
    serve_parser.add_argument(
        "--args", "-a", type=str, default="",
        help="Arguments to pass to the target program",
    )
    serve_parser.add_argument(
        "--srcpath", type=str, nargs='*', default=None,
        help="Additional source paths for the debugger to search",
    )
    serve_parser.add_argument(
        "--attach_pid", type=int, default=None, metavar="PID",
        help="Attach to a running process by PID instead of launching",
    )
    serve_parser.add_argument(
        "--core", type=str, default=None, metavar="PATH",
        help="Analyse a core dump / crash dump file",
    )
    serve_parser.add_argument(
        "--daemonize", action="store_true", default=False,
        help="Launch the server as a fully detached OS process that "
             "survives terminal closure. Returns JSON with PID.",
    )
    cmd_parser = subparsers.add_parser("cmd", help="Send a command to the server")
    cmd_parser.add_argument(
        "--port", "-p", type=int, default=5678,
        help="TCP port (default: 5678)",
    )
    cmd_parser.add_argument("--host", default="127.0.0.1",
                            help="Host/IP of the debug server (default: 127.0.0.1)")
    cmd_parser.add_argument(
        "command", nargs="+",
        help="Command (e.g., 'start', 'b main.c:42', 'continue', 'inspect')",
    )

    info_parser = subparsers.add_parser(
        "info",
        help="Detect and display platform, compilers, debuggers, and repo context",
    )
    info_parser.add_argument(
        "--repo", type=str, default=None,
        help="Repository root to scan (default: auto-detect from cwd)",
    )

    compile_parser = subparsers.add_parser(
        "compile",
        help="Compile a C/C++ source file with debug symbols",
    )
    compile_parser.add_argument("source", help="C/C++ source file to compile")
    compile_parser.add_argument(
        "--output", "-o", type=str, default=None,
        help="Output executable path (default: same name as source)",
    )
    compile_parser.add_argument(
        "--compiler", "-cc", type=str, default=None,
        help="Force a specific compiler path",
    )
    compile_parser.add_argument(
        "--flags", "-f", type=str, default="",
        help="Extra compiler flags (space-separated)",
    )

    build_info_parser = subparsers.add_parser(
        "build-info",
        help="Detect the build system in a repository",
    )
    build_info_parser.add_argument(
        "--repo", type=str, default=None,
        help="Repository root (default: cwd)",
    )

    find_binary_parser = subparsers.add_parser(
        "find-binary",
        help="Find executable binaries in build output directories",
    )
    find_binary_parser.add_argument(
        "--dir", type=str, nargs='*', default=None,
        help="Directories to search (default: common build dirs)",
    )
    find_binary_parser.add_argument(
        "--hint", type=str, default=None,
        help="Substring to match in binary names",
    )
    find_binary_parser.add_argument(
        "--test", action='store_true', default=False,
        help="Only show test binaries",
    )
    find_binary_parser.add_argument(
        "--repo", type=str, default=None,
        help="Repository root for relative dir resolution (default: cwd)",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Check if a debug server is running on the given port",
    )
    status_parser.add_argument(
        "--port", "-p", type=int, default=5678,
        help="TCP port to check (default: 5678)",
    )
    status_parser.add_argument("--host", default="127.0.0.1",
                               help="Host/IP of the debug server (default: 127.0.0.1)")

    stop_parser = subparsers.add_parser(
        "stop",
        help="Stop a running debug server (graceful quit, then force kill)",
    )
    stop_parser.add_argument(
        "--port", "-p", type=int, default=5678,
        help="TCP port of the server to stop (default: 5678)",
    )
    stop_parser.add_argument("--host", default="127.0.0.1",
                             help="Host/IP of the debug server (default: 127.0.0.1)")

    repo_ctx_parser = subparsers.add_parser(
        "repo-context",
        help="Scan repository for build system, docs, source dirs, test dirs",
    )
    repo_ctx_parser.add_argument(
        "--repo", type=str, default=None,
        help="Repository root (default: auto-detect from cwd)",
    )

    # Support passing program args after '--' separator (standard Unix convention)
    # e.g.: python script.py serve exe --debugger cdb -- --gtest_filter=Test.Name
    argv = sys.argv[1:]
    program_extra_args = ''
    if '--' in argv:
        sep_idx = argv.index('--')
        program_extra_args = ' '.join(argv[sep_idx + 1:])
        argv = argv[:sep_idx]

    args = parser.parse_args(argv)

    if program_extra_args:
        existing = getattr(args, 'args', '') or ''
        if existing:
            args.args = existing + ' ' + program_extra_args
        else:
            args.args = program_extra_args

    if args.mode == "serve":
        cmd_serve(args)
    elif args.mode == "cmd":
        cmd_send(args)
    elif args.mode == "info":
        cmd_info(args)
    elif args.mode == "compile":
        cmd_compile(args)
    elif args.mode == "build-info":
        _cmd_build_info(args)
    elif args.mode == "find-binary":
        _cmd_find_binary(args)
    elif args.mode == "status":
        _cmd_status(args)
    elif args.mode == "stop":
        _cmd_stop(args)
    elif args.mode == "repo-context":
        _cmd_repo_context(args)
    else:
        parser.print_help()
        sys.exit(1)


def _cmd_build_info(args: argparse.Namespace):
    repo = args.repo or os.getcwd()
    result = detect_build_system(repo)
    if result:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps({"error": "No build system detected", "path": repo}))


def _cmd_find_binary(args: argparse.Namespace):
    repo = args.repo or os.getcwd()
    search_dirs = args.dir or []

    # If no dirs given, use common build output dirs
    if not search_dirs:
        for d in ('build', 'out', 'bin', 'Debug', 'Release',
                  'target/debug', 'target/release',
                  'x64/Debug', 'x64/Release', 'artifacts/bin',
                  'build/Debug', 'build/Release',
                  'bld', 'build/windows'):
            full = os.path.join(repo, d)
            if os.path.isdir(full):
                search_dirs.append(full)
        # Fallback: search repo root
        if not search_dirs:
            search_dirs = [repo]

    results = find_binaries(search_dirs, name_hint=args.hint,
                            test_only=args.test)
    print(json.dumps(results[:30], indent=2))  # cap at 30


def _cmd_status(args: argparse.Namespace):
    host = getattr(args, 'host', '127.0.0.1') or '127.0.0.1'
    pid = read_pid_file('cpp', args.port)
    try:
        result = send_command(args.port, "ping", host=host)
        result["server_running"] = True
        if pid is not None:
            result["pid"] = pid
        print(json.dumps(result, indent=2))
    except Exception:
        print(json.dumps({
            "server_running": False,
            "status": "offline",
            "pid": pid,
            "message": f"No debug server responding on port {args.port}",
        }, indent=2))


def _cmd_stop(args: argparse.Namespace):
    host = getattr(args, 'host', '127.0.0.1') or '127.0.0.1'
    # Try graceful quit first
    try:
        result = send_command(args.port, "quit", host=host)
        print(json.dumps({
            "status": "stopped",
            "message": f"Debug server on port {args.port} stopped gracefully.",
        }, indent=2))
        remove_pid_file('cpp', args.port)
        return
    except Exception:
        pass

    # Try killing by PID
    pid = read_pid_file('cpp', args.port)
    if pid is not None:
        try:
            if sys.platform == 'win32':
                subprocess.run(['taskkill', '/F', '/PID', str(pid)],
                               capture_output=True)
            else:
                os.kill(pid, signal.SIGTERM)
            print(json.dumps({
                "status": "killed",
                "pid": pid,
                "message": f"Debug server (PID {pid}) killed.",
            }, indent=2))
        except Exception as e:
            print(json.dumps({
                "status": "error",
                "message": f"Failed to kill PID {pid}: {e}",
            }, indent=2))
        remove_pid_file('cpp', args.port)
    else:
        print(json.dumps({
            "status": "not_found",
            "message": f"No debug server found on port {args.port}.",
        }, indent=2))


def _cmd_repo_context(args: argparse.Namespace):
    repo = args.repo or find_repo_root(os.getcwd()) or os.getcwd()
    result = scan_repo_context(repo)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
