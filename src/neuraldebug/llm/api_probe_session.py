#!/usr/bin/env python3
"""API Probe debug session — debug hosted LLM reasoning via black-box probes.

Same TCP/JSON protocol as every other NeuralDebug language backend.
Instead of driving PyTorch hooks or a native debugger, we probe hosted
LLM APIs (OpenAI, Anthropic, Google, Ollama) with black-box techniques:
logprob analysis, prompt perturbation, chain-of-thought extraction,
consistency testing, counterfactual probing, and calibration checks.

Usage:
    # Start the probe server
    python api_probe_session.py serve --provider openai --model gpt-4o --port 5685

    # Send probe commands from another terminal
    python api_probe_session.py cmd --port 5685 logprobs "What is 2+2?"
    python api_probe_session.py cmd --port 5685 cot "Explain gravity"
    python api_probe_session.py cmd --port 5685 consistency "Capital of France?"
"""

import argparse
import json
import signal
import sys
import textwrap
from dataclasses import asdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allow importing from the parent NeuralDebug package
# ---------------------------------------------------------------------------
_this_dir = Path(__file__).resolve().parent
_NeuralDebug_dir = _this_dir.parent          # src/NeuralDebug
sys.path.insert(0, str(_this_dir))           # api_probe, debugger, etc.
sys.path.insert(0, str(_NeuralDebug_dir))    # debug_common

from debug_common import (                   # noqa: E402
    BaseDebugServer,
    send_command,
    cmd_send_handler,
)
from api_probe import APIProbe               # noqa: E402

# ---------------------------------------------------------------------------
# Auto-discovery metadata (same convention as other debug sessions)
# ---------------------------------------------------------------------------
LANGUAGE_META = {
    "name": "llm_api",
    "display_name": "LLM API Probe",
    "extensions": [],
    "default_port": 5685,
    "debuggers": "API-based reasoning probes",
    "aliases": ["llm_api", "api_probe", "probe"],
}

# ---------------------------------------------------------------------------
# Provider → default call_fn factory
# ---------------------------------------------------------------------------
_SUPPORTED_PROVIDERS = ("openai", "anthropic", "google", "ollama")


def _make_call_fn(provider: str, model: str,
                  api_key: str = None, base_url: str = None):
    """Build a synchronous call_fn compatible with APIProbe.

    Returns a callable ``(prompt, **kwargs) -> dict`` that talks to the
    selected provider and returns ``{"text": ..., "logprobs": [...]}``.
    """
    if provider == "openai":
        import openai
        client = openai.OpenAI(
            api_key=api_key,
            **({"base_url": base_url} if base_url else {}),
        )

        def _call(prompt, **kw):
            logprobs_flag = kw.pop("logprobs", False)
            top_logprobs = kw.pop("top_logprobs", None)
            params = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": kw.get("max_tokens", 50),
            }
            if kw.get("temperature") is not None:
                params["temperature"] = kw["temperature"]
            if logprobs_flag:
                params["logprobs"] = True
                if top_logprobs:
                    params["top_logprobs"] = top_logprobs
            resp = client.chat.completions.create(**params)
            choice = resp.choices[0]
            text = choice.message.content or ""
            lps = []
            if choice.logprobs and choice.logprobs.content:
                for tok in choice.logprobs.content:
                    entry = {
                        "token": tok.token,
                        "logprob": tok.logprob,
                        "top_logprobs": [
                            {"token": t.token, "logprob": t.logprob}
                            for t in (tok.top_logprobs or [])
                        ],
                    }
                    lps.append(entry)
            return {"text": text, "logprobs": lps}

        return _call

    elif provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(
            api_key=api_key,
            **({"base_url": base_url} if base_url else {}),
        )

        def _call(prompt, **kw):
            resp = client.messages.create(
                model=model,
                max_tokens=kw.get("max_tokens", 50),
                messages=[{"role": "user", "content": prompt}],
                **({"temperature": kw["temperature"]}
                   if kw.get("temperature") is not None else {}),
            )
            text = resp.content[0].text if resp.content else ""
            return {"text": text, "logprobs": []}

        return _call

    elif provider == "google":
        import google.generativeai as genai
        if api_key:
            genai.configure(api_key=api_key)
        gen_model = genai.GenerativeModel(model)

        def _call(prompt, **kw):
            resp = gen_model.generate_content(prompt)
            text = resp.text if resp.text else ""
            return {"text": text, "logprobs": []}

        return _call

    elif provider == "ollama":
        import requests
        url = base_url or "http://localhost:11434"

        def _call(prompt, **kw):
            payload = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": kw.get("max_tokens", 50)},
            }
            if kw.get("temperature") is not None:
                payload["options"]["temperature"] = kw["temperature"]
            resp = requests.post(f"{url}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"text": data.get("response", ""), "logprobs": []}

        return _call

    raise ValueError(f"Unsupported provider: {provider!r}. "
                     f"Use one of: {', '.join(_SUPPORTED_PROVIDERS)}")


# ---------------------------------------------------------------------------
# Debug server
# ---------------------------------------------------------------------------

class APIProbeServer(BaseDebugServer):
    """TCP debug server for black-box LLM API probing."""

    LANGUAGE = "LLM_API"
    SCRIPT_NAME = "api_probe_session.py"
    HAS_RUN_TO_LINE = False

    def __init__(self, probe: APIProbe, provider: str, model: str,
                 port: int, host: str = "127.0.0.1"):
        super().__init__(debugger=probe, port=port, host=host)
        self.probe = probe
        self.provider = provider
        self.model = model

    def _get_target_label(self) -> str:
        return f"{self.provider}/{self.model}"

    def _start_debugger(self):
        """No subprocess to start — the probe is ready immediately."""
        pass

    def _available_commands(self):
        cmds = super()._available_commands()
        cmds.extend([
            "logprobs", "perturb", "cot", "consistency",
            "counterfactual", "calibrate",
        ])
        return cmds

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _probe_response(action: str, message: str, result) -> dict:
        details = asdict(result) if hasattr(result, "__dataclass_fields__") else result
        return {
            "status": "ok",
            "command": action,
            "message": message,
            "probe_result": details,
        }

    def _try_parse_json(self, raw: str):
        """Try to parse *raw* as JSON; return parsed object or None."""
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

    # -- dispatch ----------------------------------------------------------

    def _pre_start_dispatch(self, action: str, args: str):
        """All probe commands work without an active stepping session."""
        return self._dispatch_extra(action, args)

    def _dispatch_extra(self, action: str, args: str):
        try:
            if action in ("logprobs", "lp"):
                result = self.probe.analyze_logprobs(args)
                return self._probe_response(action, result.summary, result)

            elif action in ("perturb", "pert"):
                parsed = self._try_parse_json(args)
                if isinstance(parsed, dict):
                    prompt = parsed.get("prompt", "")
                    perturbations = parsed.get("perturbations", [])
                else:
                    prompt = args
                    perturbations = []
                result = self.probe.perturb(prompt, perturbations)
                return self._probe_response(action, result.summary, result)

            elif action in ("cot", "chain_of_thought"):
                result = self.probe.extract_cot(args)
                return self._probe_response(action, result.summary, result)

            elif action in ("consistency", "consist"):
                parsed = self._try_parse_json(args)
                if isinstance(parsed, dict):
                    prompt = parsed.get("prompt", args)
                    n = parsed.get("n", 5)
                    temp = parsed.get("temperature", 0.7)
                else:
                    prompt = args
                    n, temp = 5, 0.7
                result = self.probe.test_consistency(prompt, n=n,
                                                     temperature=temp)
                return self._probe_response(action, result.summary, result)

            elif action in ("counterfactual", "cf"):
                parsed = self._try_parse_json(args)
                if not isinstance(parsed, dict):
                    return self._error(
                        "counterfactual requires JSON: "
                        '{"prompt": "...", "counterfactuals": [...]}'
                    )
                prompt = parsed.get("prompt", "")
                cfs = parsed.get("counterfactuals", [])
                result = self.probe.probe_counterfactual(prompt, cfs)
                return self._probe_response(action, result.summary, result)

            elif action in ("calibrate", "cal"):
                parsed = self._try_parse_json(args)
                if not isinstance(parsed, list):
                    return self._error(
                        "calibrate requires JSON list: "
                        '[{"question": "...", "answer": "..."}]'
                    )
                result = self.probe.check_calibration(parsed)
                return self._probe_response(action, result.summary, result)

        except Exception as e:
            return self._error(str(e))

        return None


# ---------------------------------------------------------------------------
# CLI sub-commands
# ---------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace):
    provider = args.provider
    model = args.model
    call_fn = _make_call_fn(provider, model,
                            api_key=args.api_key,
                            base_url=args.base_url)
    probe = APIProbe(call_fn, model_name=model)
    server = APIProbeServer(probe, provider=provider, model=model,
                            port=args.port, host=args.host)

    def _sigint(sig, frame):
        print("\nShutting down API probe server …")
        server.running = False
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint)
    server.run()


def cmd_send(args: argparse.Namespace):
    cmd_send_handler(args)


def cmd_info(_args: argparse.Namespace):
    info = {
        **LANGUAGE_META,
        "supported_providers": list(_SUPPORTED_PROVIDERS),
        "techniques": [
            "logprobs  (lp)   — token confidence & entropy analysis",
            "perturb   (pert) — compare outputs under prompt changes",
            "cot              — chain-of-thought extraction & comparison",
            "consistency      — repeated-query agreement scoring",
            "counterfactual (cf)  — causal factor testing",
            "calibrate (cal)  — stated confidence vs accuracy",
        ],
    }
    print(json.dumps(info, indent=2))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="API Probe Session — debug hosted LLM reasoning via "
                    "black-box probes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Start the server targeting OpenAI GPT-4o:
              python api_probe_session.py serve --provider openai --model gpt-4o

              # Start with Ollama (local):
              python api_probe_session.py serve --provider ollama --model llama3

              # Send commands:
              python api_probe_session.py cmd --port 5685 logprobs "What is 2+2?"
              python api_probe_session.py cmd --port 5685 cot "Explain gravity"
              python api_probe_session.py cmd --port 5685 consistency "Capital of France?"
        """),
    )
    sub = parser.add_subparsers(dest="mode", help="Mode of operation")

    # -- serve -------------------------------------------------------------
    sp = sub.add_parser("serve", help="Start the API probe server")
    sp.add_argument("--provider", default="openai",
                    choices=list(_SUPPORTED_PROVIDERS),
                    help="API provider (default: openai)")
    sp.add_argument("--model", "-m", default="gpt-4o",
                    help="Model name (default: gpt-4o)")
    sp.add_argument("--api-key", default=None,
                    help="API key (or set via env var, e.g. OPENAI_API_KEY)")
    sp.add_argument("--base-url", default=None,
                    help="Custom base URL for the provider API")
    sp.add_argument("--port", "-p", type=int, default=5685,
                    help="TCP port to listen on (default: 5685)")
    sp.add_argument("--host", default="127.0.0.1",
                    help="Host/IP to bind to (default: 127.0.0.1)")

    # -- cmd ---------------------------------------------------------------
    cp = sub.add_parser("cmd",
                        help="Send a command to a running API probe server")
    cp.add_argument("--port", "-p", type=int, default=5685,
                    help="TCP port of the probe server (default: 5685)")
    cp.add_argument("--host", default="127.0.0.1",
                    help="Host/IP of the probe server (default: 127.0.0.1)")
    cp.add_argument("--timeout", "-t", type=int, default=120,
                    help="Response timeout in seconds (default: 120)")
    cp.add_argument("command", nargs=argparse.REMAINDER,
                    help="Command to send (e.g. 'logprobs', 'cot', "
                         "'consistency')")

    # -- info --------------------------------------------------------------
    sub.add_parser("info", help="Print supported providers and techniques")

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
