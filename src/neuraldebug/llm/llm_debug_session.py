#!/usr/bin/env python3
"""LLM debug session — debug transformer reasoning with PyTorch hooks.

Same TCP/JSON protocol as every other NeuralDebug language backend.
Instead of driving CDB / GDB / LLDB, we drive PyTorch hooks to step
through an LLM's forward pass layer by layer.

Usage:
    # Start the debug server (loads the model)
    python llm_debug_session.py serve --model distilgpt2 --port 5680

    # Send debug commands from another terminal
    python llm_debug_session.py cmd --port 5680 start "What is 2+2?"
    python llm_debug_session.py cmd --port 5680 step_over
    python llm_debug_session.py cmd --port 5680 inspect
    python llm_debug_session.py cmd --port 5680 continue
    python llm_debug_session.py cmd --port 5680 quit
"""

import argparse
import json
import os
import signal
import sys
import textwrap
from pathlib import Path

# Prevent transformers from trying to import TensorFlow backend
# (hangs when both TF and PyTorch are installed in the same env)
os.environ["TRANSFORMERS_NO_TF"] = "1"
os.environ["USE_TORCH"] = "1"


def _patch_torchdynamo_hang():
    """Work around torch._dynamo import hang on PyTorch ≤2.5 / Windows CPU.

    ``import torch._dynamo`` triggers a massive import chain (torch.fx →
    sympy → …) that **hangs indefinitely** on certain PyTorch + Windows +
    CPU-only builds.  Multiple PyTorch and HuggingFace code paths lazily
    import this module (optimizers, model forward pass, etc.).

    Fix: install a lightweight stub module in ``sys.modules`` *before*
    any code tries the real import.  TorchDynamo (graph compilation) is
    never used in NeuralDebug's inference/fine-tuning pipeline, so every
    dynamo API we stub is a safe no-op.
    """
    import sys
    import types

    if "torch._dynamo" not in sys.modules:
        _stub = types.ModuleType("torch._dynamo")
        _stub.is_compiling = lambda: False
        _stub.graph_break = lambda: None
        _stub.disable = lambda fn=None, *a, **kw: fn if fn else (lambda f: f)
        _stub.config = types.ModuleType("torch._dynamo.config")
        _stub.config.suppress_errors = False
        sys.modules["torch._dynamo"] = _stub
        sys.modules["torch._dynamo.config"] = _stub.config

    try:
        import transformers.utils.import_utils as _iu
        if hasattr(_iu, "is_torchdynamo_compiling"):
            _iu.is_torchdynamo_compiling = lambda: False
        import transformers.modeling_utils as _mu
        if hasattr(_mu, "is_torchdynamo_compiling"):
            _mu.is_torchdynamo_compiling = lambda: False
    except ImportError:
        pass


_patch_torchdynamo_hang()

# ---------------------------------------------------------------------------
# Path setup — allow importing from the parent NeuralDebug package
# ---------------------------------------------------------------------------
_this_dir = Path(__file__).resolve().parent
_NeuralDebug_dir = _this_dir.parent          # src/NeuralDebug
sys.path.insert(0, str(_this_dir))          # hooks, stepper, debugger, adapters, commands
sys.path.insert(0, str(_NeuralDebug_dir))    # debug_common

from debug_common import (                   # noqa: E402
    BaseDebugServer,
    send_command,
    cmd_send_handler,
)

from debugger import LLMDebugger             # noqa: E402

# ---------------------------------------------------------------------------
# Auto-discovery metadata (same convention as other debug sessions)
# ---------------------------------------------------------------------------
LANGUAGE_META = {
    "name": "llm",
    "display_name": "LLM (PyTorch)",
    "extensions": [],
    "default_port": 5680,
    "debuggers": "PyTorch hooks",
    "aliases": ["llm", "torch", "transformer"],
}


# ---------------------------------------------------------------------------
# Debug server
# ---------------------------------------------------------------------------

class LLMDebugServer(BaseDebugServer):
    """Thin wrapper that tells BaseDebugServer about our language name."""
    LANGUAGE = "LLM"
    SCRIPT_NAME = "llm_debug_session.py"
    HAS_RUN_TO_LINE = False

    def _get_target_label(self) -> str:
        return getattr(self.debugger, 'model_name', '?')

    def _available_commands(self):
        cmds = super()._available_commands()
        cmds.extend([
            "graph", "logit_lens", "attention", "probe",
            "patch", "generate", "diagnose", "finetune",
            "investigate", "sae", "neuron", "hallucinate",
            "surgery", "exec_analysis",
        ])
        return cmds

    def _pre_start_dispatch(self, action: str, args: str):
        """Diagnose, finetune, investigate, hallucinate, and exec_analysis work without stepping session."""
        if action in ("diagnose", "diag"):
            return self.debugger.cmd_diagnose(args)
        elif action in ("finetune", "ft"):
            return self.debugger.cmd_finetune(args)
        elif action in ("investigate", "inv"):
            return self.debugger.cmd_investigate(args)
        elif action in ("hallucinate", "detect", "hallucination"):
            return self.debugger.cmd_hallucinate(args)
        elif action in ("exec_analysis", "exec", "forge"):
            return self.debugger.cmd_exec_analysis(args)
        return None

    def _dispatch(self, action: str, args: str) -> dict:
        # Allow 'start' to reset the session with a new prompt
        if action in ("start", "s") and self.debugger.stepper.is_started:
            self.debugger.stepper.ctx.generated_tokens.clear()
            self.debugger.stepper.ctx.generated_text = ""
            self.debugger.stepper.ctx.generation_step = 0
            self.debugger.stepper.ctx.logits = None
            self.debugger.stepper.ctx.hidden_states = None
            self.debugger.stepper.ctx.block_attn_weights = None
            self.debugger.is_finished = False
            return self.debugger.cmd_start(args)
        return super()._dispatch(action, args)

    def _dispatch_extra(self, action: str, args: str):
        if action in ("logit_lens", "lens"):
            return self.debugger.cmd_logit_lens(args)
        elif action in ("patch", "causal_trace"):
            return self.debugger.cmd_patch(args)
        elif action in ("attention", "attn", "heads"):
            return self.debugger.cmd_attention(args)
        elif action in ("probe", "probing"):
            return self.debugger.cmd_probe(args)
        elif action in ("generate", "gen", "g"):
            return self.debugger.cmd_generate(args)
        elif action in ("diagnose", "diag"):
            return self.debugger.cmd_diagnose(args)
        elif action in ("finetune", "ft"):
            return self.debugger.cmd_finetune(args)
        elif action in ("graph", "architecture", "arch"):
            return self.debugger.cmd_graph(args)
        elif action in ("investigate", "inv"):
            return self.debugger.cmd_investigate(args)
        elif action in ("sae",):
            return self.debugger.cmd_sae(args)
        elif action in ("neuron", "neurons"):
            return self.debugger.cmd_neuron(args)
        elif action in ("hallucinate", "detect", "hallucination"):
            return self.debugger.cmd_hallucinate(args)
        elif action in ("surgery", "head_surgery"):
            return self.debugger.cmd_surgery(args)
        elif action in ("exec_analysis", "exec", "forge"):
            return self.debugger.cmd_exec_analysis(args)
        return None


# ---------------------------------------------------------------------------
# CLI sub-commands
# ---------------------------------------------------------------------------

def cmd_serve(args: argparse.Namespace):
    model_name = args.model or "distilgpt2"
    adapter_name = getattr(args, "adapter", "auto") or "auto"
    device = getattr(args, "device", "auto") or "auto"
    port = args.port
    host = args.host or "127.0.0.1"

    debugger = LLMDebugger(model_name, adapter_name=adapter_name,
                           device=device)
    server = LLMDebugServer(debugger, port=port, host=host)

    def _sigint(sig, frame):
        print("\nShutting down LLM debug server …")
        server.running = False
        debugger.is_finished = True
        sys.exit(0)

    signal.signal(signal.SIGINT, _sigint)
    server.run()


def cmd_send(args: argparse.Namespace):
    cmd_send_handler(args)


def cmd_info(_args: argparse.Namespace):
    print(json.dumps(LANGUAGE_META, indent=2))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="LLM Debug Session — step through transformer reasoning "
                    "with PyTorch hooks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Start the server with distilgpt2 (default):
              python llm_debug_session.py serve --port 5680

              # Or with a specific model:
              python llm_debug_session.py serve --model gpt2-medium --port 5680

              # Send commands:
              python llm_debug_session.py cmd --port 5680 start "Hello world"
              python llm_debug_session.py cmd --port 5680 step_over
              python llm_debug_session.py cmd --port 5680 inspect
              python llm_debug_session.py cmd --port 5680 b block_3
              python llm_debug_session.py cmd --port 5680 continue
              python llm_debug_session.py cmd --port 5680 evaluate "hidden_states.shape"
              python llm_debug_session.py cmd --port 5680 list
              python llm_debug_session.py cmd --port 5680 backtrace
              python llm_debug_session.py cmd --port 5680 quit

            Architecture (from slide 23):
              Agent  <-TCP->  Orchestrator  -->  Daemon(s)  -->  PyTorch hooks  -->  Tensors
              Same protocol, same interface — from one CPU process to GPU nodes.
        """),
    )
    sub = parser.add_subparsers(dest="mode", help="Mode of operation")

    # -- serve ---------------------------------------------------------
    sp = sub.add_parser("serve",
                        help="Start the LLM debug server")
    sp.add_argument("--model", "-m", default="distilgpt2",
                    help="HuggingFace model name or local path "
                         "(default: distilgpt2)")
    sp.add_argument("--adapter", "-a", default="auto",
                    help="Model adapter: auto (detect from model), gpt2, "
                         "llama, or a registered custom adapter name "
                         "(default: auto)")
    sp.add_argument("--device", "-d", default="auto",
                    help="Device: auto, cpu, cuda, cuda:0, mps "
                         "(default: auto)")
    sp.add_argument("--port", "-p", type=int, default=5680,
                    help="TCP port to listen on (default: 5680)")
    sp.add_argument("--host", default="127.0.0.1",
                    help="Host/IP to bind to (default: 127.0.0.1, "
                         "use 0.0.0.0 to accept remote connections)")

    # -- cmd -----------------------------------------------------------
    cp = sub.add_parser("cmd",
                        help="Send a command to a running LLM debug server")
    cp.add_argument("--port", "-p", type=int, default=5680,
                    help="TCP port of the debug server (default: 5680)")
    cp.add_argument("--host", default="127.0.0.1",
                    help="Host/IP of the debug server (default: 127.0.0.1)")
    cp.add_argument("--timeout", "-t", type=int, default=120,
                    help="Response timeout in seconds (default: 120, "
                         "use 600+ for finetune)")
    cp.add_argument("command", nargs=argparse.REMAINDER,
                    help="Command to send (e.g. 'start', 'step_over', "
                         "'inspect')")

    # -- info ----------------------------------------------------------
    sub.add_parser("info", help="Print language metadata")

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
