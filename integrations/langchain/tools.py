#!/usr/bin/env python3
"""NeuralDebug tool wrappers for LangChain, LlamaIndex, CrewAI, and AutoGen."""

import json
import os
import socket
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


_HERE= Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
_DEFAULT_SCRIPTS = _REPO_ROOT / "src" / "NeuralDebug"
SCRIPTS_DIR = Path(os.environ.get("NeuralDebug_SCRIPTS", str(_DEFAULT_SCRIPTS)))
PYTHON = sys.executable


sys.path.insert(0, str(SCRIPTS_DIR))
from language_registry import get_registry as _get_registry  # noqa: E402

_reg = _get_registry(str(SCRIPTS_DIR))
LANG_SCRIPTS = _reg.lang_scripts
EXT_TO_LANG = _reg.ext_to_lang
DEFAULT_PORTS = _reg.default_ports


def _run(script_name: str, args: list[str], timeout: int = 60) -> str:
    script= SCRIPTS_DIR / script_name
    cmd = [PYTHON, str(script)] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(_REPO_ROOT)
        )
        return result.stdout.strip() or result.stderr.strip()
    except subprocess.TimeoutExpired:
        return json.dumps({"status": "error", "message": f"Timeout after {timeout}s"})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


def _detect_lang(target: str) -> str:
    ext = Path(target).suffix.lower()
    return EXT_TO_LANG.get(ext, "cpp")



class NeuralDebugTool:

    def __init__(self, name: str, description: str, func: Callable,
                 parameters: dict | None = None):
        self.name = name
        self.description = description
        self.func = func
        self.parameters = parameters or {}

    def __call__(self, *args, **kwargs):
        return self.func(*args, **kwargs)

    def to_langchain(self):
        """Convert to a LangChain Tool (requires langchain installed)."""
        try:
            from langchain.tools import StructuredTool
            return StructuredTool.from_function(
                func=self.func,
                name=self.name,
                description=self.description,
            )
        except ImportError:
            raise ImportError("Install langchain: pip install langchain")

    def to_openai_function(self) -> dict:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }



def _start_server(target: str, language: str = "", port: int = 0,
                  program_args: str = "") -> str:
    """Launch a debug server for a target program."""
    lang = language or _detect_lang(target)
    p = port or DEFAULT_PORTS.get(lang, 5678)
    script = LANG_SCRIPTS.get(lang, LANG_SCRIPTS["cpp"])
    cmd = ["serve", target, "--port", str(p), "--daemonize"]
    if program_args:
        cmd += ["--args", program_args]
    return _run(script, cmd)


def _send_cmd(command: str, args: str = "", port: int = 5678,
              language: str = "cpp") -> str:
    """Send a debug command to a running server."""
    script = LANG_SCRIPTS.get(language, LANG_SCRIPTS["cpp"])
    cmd_args = ["cmd", "--port", str(port), command]
    if args:
        cmd_args.extend(args.split())
    return _run(script, cmd_args)


def _stop_server(port: int = 5678, language: str = "cpp") -> str:
    """Stop a debug server."""
    script = LANG_SCRIPTS.get(language, LANG_SCRIPTS["cpp"])
    return _run(script, ["stop", "--port", str(port)])


def _status(port: int = 5678, language: str = "cpp") -> str:
    """Check if a debug server is running."""
    script = LANG_SCRIPTS.get(language, LANG_SCRIPTS["cpp"])
    return _run(script, ["status", "--port", str(port)])


def _info(language: str = "cpp") -> str:
    """Detect available debuggers and compilers."""
    script = LANG_SCRIPTS.get(language, LANG_SCRIPTS["cpp"])
    return _run(script, ["info"])


def _send_llm_command(port: int, payload: dict, timeout: int = 30) -> dict:
    """Send a TCP command to the LLM debug server.

    Protocol: 4-byte big-endian length prefix + JSON payload.
    """
    try:
        raw = json.dumps(payload).encode("utf-8")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", port))
        sock.sendall(struct.pack("!I", len(raw)) + raw)

        header = b""
        while len(header) < 4:
            chunk = sock.recv(4 - len(header))
            if not chunk:
                return {"status": "error", "message": "Connection closed while reading header"}
            header += chunk

        resp_len = struct.unpack("!I", header)[0]
        data = b""
        while len(data) < resp_len:
            chunk = sock.recv(resp_len - len(data))
            if not chunk:
                break
            data += chunk
        sock.close()
        return json.loads(data.decode("utf-8"))
    except ConnectionRefusedError:
        return {"status": "error", "message": f"No LLM debug server on port {port}. Start one first."}
    except socket.timeout:
        return {"status": "error", "message": f"LLM debug server on port {port} timed out after {timeout}s"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── LLM debug tool functions ────────────────────────────────────────

def _llm_start(model: str, port: int = 5680) -> str:
    """Launch an LLM debug server for a transformer model."""
    llm_script = "llm_debug_session.py"
    return _run(llm_script, [
        "serve", "--model", model, "--port", str(port), "--daemonize"
    ], timeout=120)


def _llm_step(prompt: str, layers: int = 1, port: int = 5680) -> dict:
    """Step through transformer layers during a forward pass."""
    return _send_llm_command(port, {
        "action": "step", "args": f"{prompt} --layers {layers}"
    })


def _llm_inspect(layer: int, what: str = "all", port: int = 5680) -> dict:
    """Inspect activations, hidden states, or attention at a layer."""
    return _send_llm_command(port, {
        "action": "inspect", "args": f"--layer {layer} --what {what}"
    })


def _llm_logit_lens(prompt: str, top_k: int = 5, port: int = 5680) -> dict:
    """Run Logit Lens analysis to see how predictions evolve across layers."""
    return _send_llm_command(port, {
        "action": "logit_lens", "args": f"{prompt} --top_k {top_k}"
    })


def _llm_patch(clean_prompt: str, corrupted_prompt: str,
               layer: int = -1, port: int = 5680) -> dict:
    """Activation Patching — replace activations to test causal effects."""
    args_str = f"--clean {clean_prompt} --corrupted {corrupted_prompt}"
    if layer >= 0:
        args_str += f" --layer {layer}"
    return _send_llm_command(port, {
        "action": "activation_patch", "args": args_str
    })


def _llm_attention(prompt: str, layer: int = -1, head: int = -1,
                   port: int = 5680) -> dict:
    """Analyze attention head behavior — entropy, patterns, importance."""
    args_str = prompt
    if layer >= 0:
        args_str += f" --layer {layer}"
    if head >= 0:
        args_str += f" --head {head}"
    return _send_llm_command(port, {
        "action": "attention_analysis", "args": args_str
    })


def _llm_diagnose(prompt: str, expected: str = "", port: int = 5680) -> dict:
    """Autonomous diagnosis — identify where the model goes wrong."""
    args_str = prompt
    if expected:
        args_str += f" --expected {expected}"
    return _send_llm_command(port, {
        "action": "diagnose", "args": args_str
    })


def _llm_hallucinate(prompt: str, generated_text: str = "",
                     port: int = 5680) -> dict:
    """Detect potential hallucinations via confidence and attention analysis."""
    args_str = prompt
    if generated_text:
        args_str += f" --generated {generated_text}"
    return _send_llm_command(port, {
        "action": "hallucination_detect", "args": args_str
    })


def _llm_surgery(operation: str, layer: int, head: int,
                 scale_factor: float = 0.0, port: int = 5680) -> dict:
    """Attention head surgery — ablate, scale, freeze, or restore heads."""
    args_str = f"--op {operation} --layer {layer} --head {head}"
    if operation == "scale":
        args_str += f" --scale {scale_factor}"
    return _send_llm_command(port, {
        "action": "head_surgery", "args": args_str
    })


def _llm_finetune(training_data: list[str], epochs: int = 3,
                  lr: float = 5e-4, port: int = 5680) -> dict:
    """Run lightweight LoRA fine-tuning to inject missing knowledge."""
    return _send_llm_command(port, {
        "action": "finetune",
        "args": json.dumps({"data": training_data, "epochs": epochs, "lr": lr})
    }, timeout=300)


def _llm_probe_api(prompt: str, api_type: str = "openai",
                   model: str = "", port: int = 5685) -> dict:
    """Probe an external LLM API with diagnostic prompts."""
    args_str = f"{prompt} --api {api_type}"
    if model:
        args_str += f" --model {model}"
    return _send_llm_command(port, {
        "action": "probe_api", "args": args_str
    })



def get_NeuralDebug_tools(scripts_dir: str | None = None) -> list[NeuralDebugTool]:
    """
    Get all NeuralDebug tools as framework-agnostic Tool objects.

    Args:
        scripts_dir: Override the default scripts directory

    Returns:
        List of NeuralDebugTool objects. Each has:
        - .name, .description: metadata
        - .func: callable
        - .to_langchain(): convert to LangChain StructuredTool
        - .to_openai_function(): convert to OpenAI function schema
    """
    global SCRIPTS_DIR
    if scripts_dir:
        SCRIPTS_DIR = Path(scripts_dir)

    return [
        NeuralDebugTool(
            "NeuralDebug_info",
            "Detect available debuggers, compilers, and toolchain for the current platform.",
            _info,
            {"type": "object", "properties": {
                "language": {"type": "string", "default": "cpp"}
            }}
        ),
        NeuralDebugTool(
            "NeuralDebug_start_server",
            "Launch a debug server for a target program. Supports 8 languages. Auto-compiles source files.",
            _start_server,
            {"type": "object", "properties": {
                "target": {"type": "string"},
                "language": {"type": "string"},
                "port": {"type": "integer"},
                "program_args": {"type": "string"}
            }, "required": ["target"]}
        ),
        NeuralDebugTool(
            "NeuralDebug_status",
            "Check if a debug server is running on the specified port.",
            _status,
            {"type": "object", "properties": {
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            }}
        ),
        NeuralDebugTool(
            "NeuralDebug_command",
            "Send any debug command to the running server. Commands: b (breakpoint), start, step_over, step_in, step_out, continue, run_to_line, inspect, e (evaluate), backtrace, list, breakpoints, remove_breakpoint, ping, quit.",
            _send_cmd,
            {"type": "object", "properties": {
                "command": {"type": "string"},
                "args": {"type": "string"},
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            }, "required": ["command"]}
        ),
        NeuralDebugTool(
            "NeuralDebug_stop",
            "Stop the debug server and end the debugging session.",
            _stop_server,
            {"type": "object", "properties": {
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            }}
        ),
        # ── LLM debug tools ──────────────────────────────────────────
        NeuralDebugTool(
            "NeuralDebug_llm_start",
            "Start an LLM debug server for a transformer model (distilgpt2, gpt2, gpt2-medium, gpt2-large, gpt2-xl).",
            _llm_start,
            {"type": "object", "properties": {
                "model": {"type": "string", "description": "Model name"},
                "port": {"type": "integer", "default": 5680}
            }, "required": ["model"]}
        ),
        NeuralDebugTool(
            "NeuralDebug_llm_step",
            "Step through transformer layers during a forward pass.",
            _llm_step,
            {"type": "object", "properties": {
                "prompt": {"type": "string"},
                "layers": {"type": "integer", "default": 1},
                "port": {"type": "integer", "default": 5680}
            }, "required": ["prompt"]}
        ),
        NeuralDebugTool(
            "NeuralDebug_llm_inspect",
            "Inspect activations, hidden states, or attention weights at a specific layer.",
            _llm_inspect,
            {"type": "object", "properties": {
                "layer": {"type": "integer"},
                "what": {"type": "string", "enum": ["activations", "attention", "hidden_state", "all"], "default": "all"},
                "port": {"type": "integer", "default": 5680}
            }, "required": ["layer"]}
        ),
        NeuralDebugTool(
            "NeuralDebug_llm_logit_lens",
            "Run Logit Lens analysis to see how token predictions evolve across layers.",
            _llm_logit_lens,
            {"type": "object", "properties": {
                "prompt": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
                "port": {"type": "integer", "default": 5680}
            }, "required": ["prompt"]}
        ),
        NeuralDebugTool(
            "NeuralDebug_llm_patch",
            "Activation Patching — replace activations at a layer to test causal effects.",
            _llm_patch,
            {"type": "object", "properties": {
                "clean_prompt": {"type": "string"},
                "corrupted_prompt": {"type": "string"},
                "layer": {"type": "integer", "default": -1},
                "port": {"type": "integer", "default": 5680}
            }, "required": ["clean_prompt", "corrupted_prompt"]}
        ),
        NeuralDebugTool(
            "NeuralDebug_llm_attention",
            "Analyze attention head behavior — entropy, patterns, and importance scores.",
            _llm_attention,
            {"type": "object", "properties": {
                "prompt": {"type": "string"},
                "layer": {"type": "integer", "default": -1},
                "head": {"type": "integer", "default": -1},
                "port": {"type": "integer", "default": 5680}
            }, "required": ["prompt"]}
        ),
        NeuralDebugTool(
            "NeuralDebug_llm_diagnose",
            "Autonomous diagnosis — run Logit Lens, attention analysis, and probing to find model failures.",
            _llm_diagnose,
            {"type": "object", "properties": {
                "prompt": {"type": "string"},
                "expected": {"type": "string", "default": ""},
                "port": {"type": "integer", "default": 5680}
            }, "required": ["prompt"]}
        ),
        NeuralDebugTool(
            "NeuralDebug_llm_hallucinate",
            "Detect potential hallucinations by analyzing token confidence and attention patterns.",
            _llm_hallucinate,
            {"type": "object", "properties": {
                "prompt": {"type": "string"},
                "generated_text": {"type": "string", "default": ""},
                "port": {"type": "integer", "default": 5680}
            }, "required": ["prompt"]}
        ),
        NeuralDebugTool(
            "NeuralDebug_llm_surgery",
            "Attention head surgery — ablate, scale, freeze, or restore specific attention heads.",
            _llm_surgery,
            {"type": "object", "properties": {
                "operation": {"type": "string", "enum": ["ablate", "scale", "freeze", "restore"]},
                "layer": {"type": "integer"},
                "head": {"type": "integer"},
                "scale_factor": {"type": "number", "default": 0.0},
                "port": {"type": "integer", "default": 5680}
            }, "required": ["operation", "layer", "head"]}
        ),
        NeuralDebugTool(
            "NeuralDebug_llm_finetune",
            "Run lightweight LoRA fine-tuning to inject missing knowledge into the model.",
            _llm_finetune,
            {"type": "object", "properties": {
                "training_data": {"type": "array", "items": {"type": "string"}},
                "epochs": {"type": "integer", "default": 3},
                "lr": {"type": "number", "default": 0.0005},
                "port": {"type": "integer", "default": 5680}
            }, "required": ["training_data"]}
        ),
        NeuralDebugTool(
            "NeuralDebug_llm_probe_api",
            "Probe an external LLM API with diagnostic prompts and analyze responses.",
            _llm_probe_api,
            {"type": "object", "properties": {
                "prompt": {"type": "string"},
                "api_type": {"type": "string", "enum": ["openai", "anthropic", "custom"], "default": "openai"},
                "model": {"type": "string", "default": ""},
                "port": {"type": "integer", "default": 5685}
            }, "required": ["prompt"]}
        ),
    ]



if __name__== "__main__":
    tools = get_NeuralDebug_tools()
    print(f"NeuralDebug Tools ({len(tools)} available):")
    print(f"Scripts directory: {SCRIPTS_DIR}")
    print()
    for t in tools:
        print(f"  {t.name}: {t.description[:80]}...")
    print()
    print("OpenAI function format:")
    print(json.dumps([t.to_openai_function() for t in tools], indent=2))
