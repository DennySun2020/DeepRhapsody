#!/usr/bin/env python3
"""
AI-driven debugging demo — an LLM autonomously finds bugs via NeuralDebug.

    # OpenAI / Codex
    export OPENAI_API_KEY=sk-...
    python playground/ai_debug_demo.py --provider openai

    # Claude (Anthropic)
    export ANTHROPIC_API_KEY=sk-ant-...
    python playground/ai_debug_demo.py --provider claude

    # Gemini (Google)
    export GEMINI_API_KEY=AIza...
    python playground/ai_debug_demo.py --provider gemini

    # Any OpenAI-compatible endpoint (Ollama, vLLM, LM Studio, etc.)
    python playground/ai_debug_demo.py --base-url http://localhost:11434/v1 --model llama3

    # Scripted fallback (no API key)
    python playground/ai_debug_demo.py --demo

The LLM reads source code, decides where to set breakpoints, interprets
debugger output, and reports root causes. The debug session is real.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import textwrap

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO_ROOT, "src", "NeuralDebug")
EXAMPLE = os.path.join(REPO_ROOT, "examples", "sample_buggy_grades.py")
PORT = 15690
PYTHON = sys.executable

DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
MAGENTA = "\033[35m"
BOLD = "\033[1m"
RESET = "\033[0m"

SYSTEM_PROMPT = """\
You are an expert debugger using NeuralDebug. You have a debug server running \
on a target program. You can send ONE debug command per turn.

Available commands (send exactly one per response):
  set_breakpoint <file>:<line>   — set a breakpoint
  set_breakpoint <line>          — set breakpoint (current file)
  start                          — begin program execution
  continue                       — continue to next breakpoint
  step_over                      — step to next line
  step_in                        — step into function call
  step_out                       — step out of current function
  inspect                        — show local variables and call stack
  evaluate <expression>          — evaluate an expression in current scope
  list                           — show source around current line
  backtrace                      — show full call stack
  quit                           — end session

Respond in this JSON format:
{
  "thinking": "your reasoning about what you've seen and what to do next",
  "command": "the debug command to execute",
  "args": "command arguments (empty string if none)",
  "done": false
}

When you've found the bug(s), set "done": true and put your final analysis \
in "thinking". Include root cause, affected lines, and suggested fixes.

Be methodical: read the code, form hypotheses, set strategic breakpoints, \
and verify through the debugger. Don't guess — prove it.\
"""


def send_debug(action, args=""):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(30)
    sock.connect(("127.0.0.1", PORT))
    sock.sendall(json.dumps({"action": action, "args": args}).encode())
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


PROVIDERS = {
    "openai": {
        "env_key": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    "claude": {
        "env_key": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-20250514",
    },
    "gemini": {
        "env_key": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com",
        "default_model": "gemini-2.0-flash",
    },
}


def call_llm(messages, model, provider, base_url, api_key):
    """Route to the appropriate provider API."""
    if provider == "claude":
        return _call_claude(messages, model, base_url, api_key)
    elif provider == "gemini":
        return _call_gemini(messages, model, base_url, api_key)
    else:
        return _call_openai(messages, model, base_url, api_key)


def _call_openai(messages, model, base_url, api_key):
    import urllib.request, urllib.error
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": model, "messages": messages,
        "temperature": 0.2, "max_tokens": 1024,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    return data["choices"][0]["message"]["content"]


def _call_claude(messages, model, base_url, api_key):
    import urllib.request, urllib.error
    url = base_url.rstrip("/") + "/v1/messages"
    system = ""
    conv = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            conv.append({"role": m["role"], "content": m["content"]})
    body = json.dumps({
        "model": model, "max_tokens": 1024,
        "system": system, "messages": conv,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    return data["content"][0]["text"]


def _call_gemini(messages, model, base_url, api_key):
    import urllib.request, urllib.error
    url = (f"{base_url.rstrip('/')}/v1beta/models/{model}:generateContent"
           f"?key={api_key}")
    system = ""
    parts = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            role = "user" if m["role"] == "user" else "model"
            parts.append({"role": role, "parts": [{"text": m["content"]}]})
    body_dict = {"contents": parts, "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1024}}
    if system:
        body_dict["systemInstruction"] = {"parts": [{"text": system}]}
    body = json.dumps(body_dict).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    return data["candidates"][0]["content"]["parts"][0]["text"]


def parse_agent_response(text):
    """Extract JSON from the LLM response, tolerating markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
        raise


def format_debug_result(resp):
    """Compact summary of a debug response for the LLM."""
    parts = [f"status: {resp.get('status', '?')}"]
    loc = resp.get("current_location")
    if loc and loc.get("file"):
        parts.append(f"location: {loc['file']}:{loc['line']} in {loc.get('function', '?')}()")
        ctx = loc.get("code_context", "")
        if ctx:
            src = ctx if isinstance(ctx, str) else "\n".join(ctx)
            parts.append(f"source:\n{src.rstrip()}")
    variables = resp.get("local_variables")
    if variables and isinstance(variables, dict):
        var_lines = []
        for k, v in list(variables.items())[:10]:
            val = v.get("value", v) if isinstance(v, dict) else v
            typ = v.get("type", "") if isinstance(v, dict) else ""
            var_lines.append(f"  {k} ({typ}) = {val}" if typ else f"  {k} = {val}")
        parts.append("variables:\n" + "\n".join(var_lines))
    msg = resp.get("message", "")
    if msg:
        parts.append(f"message: {msg}")
    return "\n".join(parts)


def print_thinking(text):
    wrapped = textwrap.fill(text, width=72, subsequent_indent="         ")
    print(f"  {BOLD}{MAGENTA}🤖 Agent:{RESET} {CYAN}{wrapped}{RESET}")


def print_command(cmd, args):
    label = f"{cmd} {args}".strip()
    print(f"  {DIM}▸ {label}{RESET}")


def run_ai_agent(model, provider, base_url, api_key, user_prompt, source_code):
    """Run the real LLM-driven agent loop."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Bug report: {user_prompt}\n\n"
            f"Source code:\n```python\n{source_code}\n```\n\n"
            "The debug server is running. Start investigating."
        )},
    ]

    max_turns = 25
    for turn in range(max_turns):
        # Call LLM
        raw = call_llm(messages, model, provider, base_url, api_key)
        try:
            agent = parse_agent_response(raw)
        except (json.JSONDecodeError, KeyError):
            print(f"  {RED}(agent returned unparseable response, retrying){RESET}")
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content":
                "Respond with valid JSON: {\"thinking\": \"...\", \"command\": \"...\", \"args\": \"\", \"done\": false}"})
            continue

        thinking = agent.get("thinking", "")
        command = agent.get("command", "").strip()
        args = agent.get("args", "").strip()
        done = agent.get("done", False)

        # Show thinking
        if thinking:
            print_thinking(thinking)
            print()

        if done:
            return thinking

        if not command:
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "You must send a command. What debug command do you want to execute?"})
            continue

        # Execute debug command
        print_command(command, args)
        resp = send_debug(command, args)
        result_text = format_debug_result(resp)

        # Show compact result
        status = resp.get("status", "?")
        loc = resp.get("current_location") or {}
        color = GREEN if status == "paused" else YELLOW if status == "completed" else CYAN
        line_info = f" at line {loc['line']}" if loc.get("line") else ""
        func_info = f" in {loc['function']}()" if loc.get("function") else ""
        print(f"  {DIM}  → {color}{status}{RESET}{line_info}{func_info}")
        variables = resp.get("local_variables") or {}
        if variables:
            items = list(variables.items())[:4]
            var_strs = []
            for name, val in items:
                v = val.get("value", val) if isinstance(val, dict) else val
                var_strs.append(f"{name}={v}")
            print(f"  {DIM}    vars: {', '.join(var_strs)}{RESET}")
        print()

        # Feed result back to LLM
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": f"Debug result:\n{result_text}"})

    return "Max turns reached."


# ---------------------------------------------------------------------------
# Scripted fallback (no API key)
# ---------------------------------------------------------------------------

def run_scripted_demo():
    """Scripted demo that uses real debug commands with narrated reasoning."""
    steps = [
        ("Reading the source code. I see functions for loading grades, filtering, "
         "calculating mean/median/std_dev. The expected values don't match, so "
         "there are bugs in the pipeline. I'll test each function.",
         None, None),
        ("Hypothesis 1: the filter might include invalid grades. "
         "Setting breakpoint at line 44 — the filter condition.",
         "set_breakpoint", "44"),
        ("Starting execution.",
         "start", ""),
        ("Running to the first breakpoint.",
         "continue", ""),
        ("Stepping through the filter loop to check each grade.",
         "continue", ""),
        (None, "continue", ""),
        (None, "continue", ""),
        (None, "continue", ""),
        ("Score is 0 for Eve. The condition 'score >= 0' lets it through, "
         "but the business rule says zeros should be excluded. "
         "Bug #1 found: line 44 should use 'score > 0'.",
         "evaluate", "score >= 0 and score <= 100"),
        ("Now I need to check the median function. Removing old breakpoint "
         "and setting one at line 61.",
         "remove_breakpoint", "44"),
        (None, "set_breakpoint", "61"),
        ("Continuing to the median function.",
         "continue", ""),
        ("Good, I'm in calculate_median(). Let me check if scores are sorted.",
         "evaluate", "sorted(scores) == scores"),
        ("Scores are NOT sorted — median requires sorted input. "
         "Bug #2 found: line 61 needs scores = sorted(scores) first.",
         "evaluate", "scores"),
        ("Now checking standard deviation. Setting breakpoint at line 75.",
         "remove_breakpoint", "61"),
        (None, "set_breakpoint", "75"),
        (None, "continue", ""),
        ("I'm in calculate_std_dev(). The formula divides by len(scores) "
         "which is the population formula. For samples it should be N-1.",
         "evaluate", "len(scores)"),
        ("Bug #3 confirmed: line 75 divides by N instead of N-1. "
         "All 3 bugs found. Ending session.",
         "quit", ""),
    ]

    for thinking, cmd, args in steps:
        if thinking:
            print_thinking(thinking)
            print()
        if cmd:
            print_command(cmd, args or "")
            resp = send_debug(cmd, args or "")
            status = resp.get("status", "?")
            loc = resp.get("current_location") or {}
            color = GREEN if status == "paused" else YELLOW if status == "completed" else CYAN
            line_info = f" at line {loc['line']}" if loc.get("line") else ""
            func_info = f" in {loc['function']}()" if loc.get("function") else ""
            print(f"  {DIM}  → {color}{status}{RESET}{line_info}{func_info}")
            variables = resp.get("local_variables") or {}
            if variables:
                items = list(variables.items())[:4]
                var_strs = []
                for name, val in items:
                    v = val.get("value", val) if isinstance(val, dict) else val
                    var_strs.append(f"{name}={v}")
                print(f"  {DIM}    vars: {', '.join(var_strs)}{RESET}")
            msg = resp.get("message", "")
            if cmd == "evaluate" and msg:
                print(f"  {DIM}    result: {msg}{RESET}")
            print()
            time.sleep(0.6)

    return (
        "Found 3 bugs:\n"
        "1. Line 44: score >= 0 should be score > 0 (Eve's zero passes filter)\n"
        "2. Line 61: median doesn't sort the list first\n"
        "3. Line 75: std dev divides by N instead of N-1 (population vs sample)"
    )


# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="AI-driven debugging demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              # OpenAI
              OPENAI_API_KEY=sk-... python ai_debug_demo.py --provider openai

              # Claude
              ANTHROPIC_API_KEY=sk-ant-... python ai_debug_demo.py --provider claude

              # Gemini
              GEMINI_API_KEY=AIza... python ai_debug_demo.py --provider gemini

              # Local model (Ollama, LM Studio, etc.)
              python ai_debug_demo.py --base-url http://localhost:11434/v1 --model llama3

              # No API key — scripted walkthrough
              python ai_debug_demo.py --demo
        """),
    )
    parser.add_argument("--provider", choices=["openai", "claude", "gemini"],
                        default=None,
                        help="LLM provider (auto-detected from env vars if omitted)")
    parser.add_argument("--model", default=None,
                        help="Model name (default depends on provider)")
    parser.add_argument("--base-url", default=None,
                        help="Custom API endpoint (for local models, proxies, etc.)")
    parser.add_argument("--api-key", default=None,
                        help="API key (default: from provider's env var)")
    parser.add_argument("--demo", action="store_true",
                        help="Scripted demo — no API key needed")
    parser.add_argument("--target", default=EXAMPLE,
                        help="Target program to debug")
    parser.add_argument("--prompt", default=None,
                        help="Bug description")
    args = parser.parse_args()

    # Resolve provider, key, model
    provider = args.provider
    api_key = args.api_key
    base_url = args.base_url

    if not args.demo:
        # Auto-detect provider from env vars if not specified
        if not provider and not api_key and not base_url:
            for name, cfg in PROVIDERS.items():
                if os.environ.get(cfg["env_key"]):
                    provider = name
                    break

        provider = provider or "openai"
        cfg = PROVIDERS[provider]

        if not api_key:
            api_key = os.environ.get(cfg["env_key"], "")
        if not base_url:
            base_url = os.environ.get("OPENAI_BASE_URL", cfg["base_url"])
        model = args.model or cfg["default_model"]
    else:
        provider = "demo"
        model = "scripted"

    use_llm = bool(api_key) and not args.demo

    print()
    print(f"{BOLD}{'═' * 60}")
    print(f"  NeuralDebug — AI-Driven Debugging Demo")
    print(f"{'═' * 60}{RESET}")
    print()

    if use_llm:
        print(f"  {GREEN}Provider: {provider}  Model: {model}{RESET}")
    else:
        if not args.demo:
            print(f"  {YELLOW}No API key found. Set one of:{RESET}")
            for name, cfg in PROVIDERS.items():
                print(f"  {DIM}  export {cfg['env_key']}=...  (--provider {name}){RESET}")
            print(f"  {YELLOW}Or use --demo for a scripted walkthrough.{RESET}")
            print()
            sys.exit(1)
        print(f"  {YELLOW}Mode: Scripted demo (use --provider for real AI){RESET}")
    print()

    user_prompt = args.prompt or (
        "The grade calculator gives wrong results. "
        "Expected mean=77.43, median=85.00, std_dev=18.50 "
        "but the output doesn't match. Find the bugs."
    )
    print(f"  {BOLD}👤 You:{RESET} {user_prompt}")
    print()

    with open(args.target) as f:
        source_code = f.read()

    # Start debug server
    print(f"  {DIM}Starting debug server...{RESET}")
    server = subprocess.Popen(
        [PYTHON, os.path.join(SCRIPTS, "python_debug_session.py"),
         "serve", args.target, "--port", str(PORT)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    time.sleep(1)
    for _ in range(30):
        try:
            r = send_debug("ping")
            if r.get("status"):
                break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    else:
        print(f"  {RED}Server failed to start{RESET}")
        server.terminate()
        sys.exit(1)

    print(f"  {DIM}Server ready.{RESET}")
    print()

    try:
        if use_llm:
            conclusion = run_ai_agent(model, provider, base_url, api_key,
                                       user_prompt, source_code)
        else:
            conclusion = run_scripted_demo()
    except KeyboardInterrupt:
        print(f"\n  {YELLOW}Interrupted.{RESET}")
        conclusion = None
    except Exception as e:
        print(f"\n  {RED}Error: {e}{RESET}")
        conclusion = None
    finally:
        try:
            send_debug("quit")
        except Exception:
            pass
        server.terminate()
        server.wait()

    if conclusion:
        print(f"{BOLD}─── Conclusion ───{RESET}")
        print()
        for line in conclusion.strip().split("\n"):
            print(f"  {line}")
        print()

    if not use_llm:
        print(f"{DIM}To run with a real LLM:{RESET}")
        print(f"{DIM}  export OPENAI_API_KEY=sk-...      && python playground/ai_debug_demo.py --provider openai{RESET}")
        print(f"{DIM}  export ANTHROPIC_API_KEY=sk-ant-... && python playground/ai_debug_demo.py --provider claude{RESET}")
        print(f"{DIM}  export GEMINI_API_KEY=AIza...     && python playground/ai_debug_demo.py --provider gemini{RESET}")
        print()


if __name__ == "__main__":
    main()
