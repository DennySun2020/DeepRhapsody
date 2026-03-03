#!/usr/bin/env python3
"""
NeuralDebug quick demo — run this to see NeuralDebug in action.

    python playground/try_NeuralDebug.py

No dependencies beyond Python 3.8+. Starts a debug server on a buggy
sample program, walks through a few commands, and shows the results.
"""

import json
import os
import socket
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, "src", "NeuralDebug")
EXAMPLE = os.path.join(REPO_ROOT, "examples", "sample_buggy_grades.py")
PORT = 15678  # high port to avoid conflicts

PYTHON = sys.executable
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"


def send(action, args=""):
    cmd = {"action": action, "args": args}
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(30)
    sock.connect(("127.0.0.1", PORT))
    sock.sendall(json.dumps(cmd).encode())
    sock.shutdown(socket.SHUT_WR)
    chunks = []
    while True:
        try:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data.decode())
        except socket.timeout:
            break
    sock.close()
    return json.loads("".join(chunks))


def show(label, resp):
    status = resp.get("status", "?")
    color = GREEN if status == "paused" else YELLOW if status == "completed" else CYAN
    print(f"\n{BOLD}>>> {label}{RESET}")
    print(f"  status: {color}{status}{RESET}")

    loc = resp.get("current_location")
    if loc and loc.get("file"):
        print(f"  location: {loc['file']}:{loc['line']} in {loc.get('function', '?')}()")
        ctx = loc.get("code_context")
        if ctx:
            lines = ctx.split("\n") if isinstance(ctx, str) else ctx
            for ctx_line in lines[:5]:
                if ctx_line.strip():
                    print(f"    {ctx_line.rstrip()}")

    variables = resp.get("local_variables")
    if variables:
        print(f"  variables:")
        items = variables.items() if isinstance(variables, dict) else []
        for name, val in list(items)[:8]:
            if isinstance(val, dict):
                print(f"    {name} = {val.get('value', val)}")
            else:
                print(f"    {name} = {val}")

    msg = resp.get("message", "")
    if msg:
        print(f"  message: {msg}")


def main():
    print(f"{BOLD}{'=' * 50}")
    print(f"  NeuralDebug — Quick Demo")
    print(f"{'=' * 50}{RESET}")
    print()
    print(f"Target: {os.path.basename(EXAMPLE)}")
    print(f"  A grade calculator with 3 planted bugs.")
    print(f"  We'll step through and catch one of them.")
    print()

    # Start server
    print(f"{CYAN}Starting debug server on port {PORT}...{RESET}")
    server_script = os.path.join(SCRIPTS, "python_debug_session.py")
    server = subprocess.Popen(
        [PYTHON, server_script, "serve", EXAMPLE, "--port", str(PORT)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    # Wait for server to be ready
    time.sleep(1)
    for i in range(40):
        try:
            r = send("ping")
            if r.get("status"):  # any valid response means server is up
                break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    else:
        print("ERROR: Server didn't start. Check Python version (need 3.8+).")
        server.terminate()
        sys.exit(1)

    print(f"{GREEN}Server ready.{RESET}\n")

    try:
        # Set breakpoint at the buggy filter function
        r = send("set_breakpoint", "44")
        show("Set breakpoint at line 44 (the grade filter)", r)

        # Start execution
        r = send("start")
        show("Start program — runs to breakpoint", r)

        # Inspect variables
        r = send("inspect")
        show("Inspect local variables", r)

        # Step a few times to watch the loop
        r = send("step_over")
        show("Step over — next iteration", r)

        r = send("inspect")
        show("Inspect again — watch variables change", r)

        # Continue to see more iterations
        r = send("continue")
        show("Continue — hits breakpoint again", r)

        r = send("inspect")
        show("Inspect — is this the buggy iteration?", r)

        # Evaluate an expression
        r = send("evaluate", "score >= 0 and score <= 100")
        show("Evaluate: 'score >= 0 and score <= 100'", r)

        # Show the bug
        print(f"\n{BOLD}{YELLOW}💡 The bug: Eve's score is 0, which passes the filter")
        print(f"   (score >= 0) but should be excluded per business rules.{RESET}")
        print(f"   Fix: change >= 0 to > 0 on line 44.\n")

        # Quit
        r = send("quit")
        show("Quit debug session", r)

    finally:
        server.terminate()
        server.wait()

    print(f"\n{BOLD}{GREEN}Demo complete!{RESET}")
    print()
    print("Next steps:")
    print("  • Connect an AI agent — see docs/tutorials/")
    print("  • Try C/C++: python src/NeuralDebug/cpp_debug_session.py info")
    print("  • Open the Jupyter notebook: jupyter notebook playground/NeuralDebug_tour.ipynb")
    print()


if __name__ == "__main__":
    main()
