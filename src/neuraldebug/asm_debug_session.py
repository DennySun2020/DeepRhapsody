#!/usr/bin/env python3
"""Assembly-level C/C++ debug session.

Extends the standard C/C++ debugger with machine-code level commands:
disassemble, stepi, nexti, registers, memory read/write, patch, nop,
address breakpoints (*0xADDR), and on-disk binary patching.

All standard source-level debugging commands (step_in, step_over,
breakpoints, inspect, backtrace, etc.) remain fully available.
"""

import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import textwrap
from typing import Optional

from debug_common import (
    send_command, get_pid_file, write_pid_file,
    remove_pid_file, read_pid_file, find_repo_root, cmd_send_handler,
)

from debuggers.cpp_common import (
    ToolchainInfo, find_debugger, compile_source,
    detect_build_system, find_binaries, scan_repo_context,
    SOURCE_EXTENSIONS,
)

from debuggers.asm_common import AsmDebugServer, create_asm_debugger


LANGUAGE_META = {
    "name": "asm",
    "display_name": "C/C++ (Assembly)",
    "extensions": [".c", ".cpp", ".cc", ".cxx", ".exe", ".out", ".o", ".obj"],
    "default_port": 5678,
    "debuggers": "GDB / LLDB / CDB (assembly-extended)",
    "aliases": ["assembly", "binary"],
}

PID_KEY = "asm"


# ======================================================================
#  Daemonize
# ======================================================================

def _daemonize_serve(args: argparse.Namespace):
    cmd = [sys.executable, os.path.abspath(__file__), 'serve']
    if getattr(args, 'target', None):
        cmd.append(args.target)
    cmd.extend(['--port', str(args.port)])
    if getattr(args, 'debugger', None):
        cmd.extend(['--debugger', args.debugger])
    program_args_val = getattr(args, 'args', None) or ''
    srcpath = getattr(args, 'srcpath', None) or []
    for sp in srcpath:
        cmd.extend(['--srcpath', sp])
    attach_pid = getattr(args, 'attach_pid', None)
    if attach_pid:
        cmd.extend(['--attach_pid', str(attach_pid)])
    core = getattr(args, 'core', None)
    if core:
        cmd.extend(['--core', core])
    if program_args_val.strip():
        cmd.extend(['--', program_args_val.strip()])

    log_dir = os.path.join(os.path.expanduser('~'), '.NeuralDebug', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f'asm_server_{args.port}.log')
    try:
        log_fh = open(log_file, 'w')
    except OSError:
        log_fh = open(os.devnull, 'w')

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
    log_fh.close()

    pid_file = get_pid_file(PID_KEY, args.port)
    with open(pid_file, 'w') as f:
        f.write(str(proc.pid))

    result = {
        "status": "launched",
        "pid": proc.pid,
        "port": args.port,
        "log_file": log_file,
        "message": f"Assembly debug server launched as daemon (PID {proc.pid}) on port {args.port}. "
                   f"Use 'status --port {args.port}' to check readiness, "
                   f"'stop --port {args.port}' to terminate.",
    }
    print(json.dumps(result, indent=2))


# ======================================================================
#  Serve
# ======================================================================

def cmd_serve(args: argparse.Namespace):
    if getattr(args, 'daemonize', False):
        _daemonize_serve(args)
        return

    write_pid_file(PID_KEY, args.port)

    attach_pid = getattr(args, 'attach_pid', None)
    core_dump = getattr(args, 'core', None)
    srcpaths = [p for p in (getattr(args, 'srcpath', None) or []) if p]
    program_args = getattr(args, 'args', '') or ''

    if attach_pid:
        executable = getattr(args, 'target', None) or ''
        if executable and not os.path.isfile(executable):
            executable = ''
        try:
            dbg_type, dbg_path = find_debugger(args.debugger)
        except (FileNotFoundError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"[ASM] Attaching to PID {attach_pid} using {dbg_type} ({dbg_path})")
        debugger = create_asm_debugger(
            executable, dbg_type, dbg_path,
            source_paths=srcpaths, attach_pid=attach_pid,
        )
        server = AsmDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')
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
        print(f"[ASM] Analysing core dump: {core_dump}")
        debugger = create_asm_debugger(
            executable, dbg_type, dbg_path,
            source_paths=srcpaths, core_dump=core_dump,
        )
        server = AsmDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')
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
        print(f"[ASM] Detected source file ({ext}). Auto-compiling with debug symbols...")
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

    if not srcpaths:
        repo_root = find_repo_root(os.path.dirname(os.path.abspath(executable)))
        if repo_root:
            srcpaths.append(repo_root)

    try:
        dbg_type, dbg_path = find_debugger(args.debugger)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[ASM] Platform: {sys.platform} ({platform.machine()})")
    print(f"[ASM] Using debugger: {dbg_type} ({dbg_path})")
    print(f"[ASM] Assembly-level commands enabled: disassemble, stepi, nexti, "
          f"registers, memory, memory_write, patch, nop, patch_file")
    if srcpaths:
        print(f"[ASM] Source paths: {', '.join(srcpaths)}")
    debugger = create_asm_debugger(
        executable, dbg_type, dbg_path,
        source_paths=srcpaths, program_args=program_args.strip() or None,
    )
    server = AsmDebugServer(debugger, port=args.port, host=getattr(args, 'host', '127.0.0.1') or '127.0.0.1')
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
        remove_pid_file(PID_KEY, server.port)


# ======================================================================
#  Other commands
# ======================================================================

def cmd_send(args):
    cmd_send_handler(args)


def _cmd_status(args: argparse.Namespace):
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
            "server_running": False,
            "status": "offline",
            "pid": pid,
            "message": f"No assembly debug server responding on port {args.port}",
        }, indent=2))


def _cmd_stop(args: argparse.Namespace):
    host = getattr(args, 'host', '127.0.0.1') or '127.0.0.1'
    try:
        send_command(args.port, "quit", host=host)
        print(json.dumps({
            "status": "stopped",
            "message": f"Assembly debug server on port {args.port} stopped.",
        }, indent=2))
        remove_pid_file(PID_KEY, args.port)
        return
    except Exception:
        pass

    pid = read_pid_file(PID_KEY, args.port)
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
                "message": f"Assembly debug server (PID {pid}) killed.",
            }, indent=2))
        except Exception as e:
            print(json.dumps({
                "status": "error",
                "message": f"Failed to kill PID {pid}: {e}",
            }, indent=2))
        remove_pid_file(PID_KEY, args.port)
    else:
        print(json.dumps({
            "status": "not_found",
            "message": f"No assembly debug server found on port {args.port}.",
        }, indent=2))


# ======================================================================
#  CLI
# ======================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Assembly-Level Debug Session — C/C++ with machine-code commands",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            All standard C/C++ debug commands remain available (step_in, step_over,
            breakpoints, inspect, backtrace, etc.), plus assembly-level commands:

            Assembly commands:
              disassemble [addr] [count]   View assembly instructions at PC or address
              stepi                        Step one machine instruction (follow calls)
              nexti                        Step one instruction (skip over calls)
              registers [all]              Show CPU registers
              memory <addr> [len]          Read raw memory bytes (hex dump)
              memory_write <addr> <hex>    Write raw bytes to process memory
              patch <addr> <hex>           Write bytes with before/after comparison
              nop <addr> [count]           NOP out bytes at address
              b *0x401000                  Set breakpoint at raw address
              patch_file <file> <off> <hex>  Patch binary file on disk

            Examples:
              # Start assembly debug server:
              python asm_debug_session.py serve program.exe --port 5678 --daemonize

              # Disassemble at current position:
              python asm_debug_session.py cmd -p 5678 disassemble

              # Step one instruction:
              python asm_debug_session.py cmd -p 5678 stepi

              # Show registers:
              python asm_debug_session.py cmd -p 5678 registers

              # Read 128 bytes of memory:
              python asm_debug_session.py cmd -p 5678 memory 0x7fffffffde00 128

              # Patch bytes in running process:
              python asm_debug_session.py cmd -p 5678 patch 0x401050 9090

              # NOP out 5 bytes:
              python asm_debug_session.py cmd -p 5678 nop 0x401050 5

              # Set breakpoint at address:
              python asm_debug_session.py cmd -p 5678 b *0x401000

              # Patch binary on disk:
              python asm_debug_session.py cmd -p 5678 patch_file program.exe 0x1a40 9090
        """),
    )
    subparsers = parser.add_subparsers(dest="mode", help="Mode of operation")

    # --- serve ---
    serve_parser = subparsers.add_parser("serve", help="Start assembly debug server")
    serve_parser.add_argument(
        "target", nargs='?', default=None,
        help="C/C++ executable or source file to debug",
    )
    serve_parser.add_argument("--port", "-p", type=int, default=5678)
    serve_parser.add_argument("--host", default="127.0.0.1",
                              help="Host/IP to bind to (default: 127.0.0.1, "
                                   "use 0.0.0.0 to accept remote connections)")
    serve_parser.add_argument(
        "--debugger", "-d", type=str, default=None,
        choices=["gdb", "lldb", "cdb"],
    )
    serve_parser.add_argument("--args", "-a", type=str, default="")
    serve_parser.add_argument("--srcpath", type=str, nargs='*', default=None)
    serve_parser.add_argument("--attach_pid", type=int, default=None, metavar="PID")
    serve_parser.add_argument("--core", type=str, default=None, metavar="PATH")
    serve_parser.add_argument("--daemonize", action="store_true", default=False)

    # --- cmd ---
    cmd_parser = subparsers.add_parser("cmd", help="Send a command")
    cmd_parser.add_argument("--port", "-p", type=int, default=5678)
    cmd_parser.add_argument("--host", default="127.0.0.1",
                            help="Host/IP of the debug server (default: 127.0.0.1)")
    cmd_parser.add_argument(
        "--timeout", "-t", type=int, default=120,
        help="Timeout in seconds for the command (default: 120)",
    )
    cmd_parser.add_argument("command", nargs="+", help="Command and arguments")

    # --- status ---
    status_parser = subparsers.add_parser("status", help="Check server status")
    status_parser.add_argument("--port", "-p", type=int, default=5678)
    status_parser.add_argument("--host", default="127.0.0.1",
                               help="Host/IP of the debug server (default: 127.0.0.1)")

    # --- stop ---
    stop_parser = subparsers.add_parser("stop", help="Stop the server")
    stop_parser.add_argument("--port", "-p", type=int, default=5678)
    stop_parser.add_argument("--host", default="127.0.0.1",
                             help="Host/IP of the debug server (default: 127.0.0.1)")

    # Handle -- separator for program args
    argv = sys.argv[1:]
    program_extra_args = ''
    if '--' in argv:
        sep_idx = argv.index('--')
        program_extra_args = ' '.join(argv[sep_idx + 1:])
        argv = argv[:sep_idx]

    args = parser.parse_args(argv)

    if program_extra_args:
        existing = getattr(args, 'args', '') or ''
        args.args = (existing + ' ' + program_extra_args).strip() if existing else program_extra_args

    if args.mode == "serve":
        cmd_serve(args)
    elif args.mode == "cmd":
        cmd_send(args)
    elif args.mode == "status":
        _cmd_status(args)
    elif args.mode == "stop":
        _cmd_stop(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
