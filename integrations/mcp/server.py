#!/usr/bin/env python3
"""NeuralDebug MCP server — exposes debug tools over JSON-RPC (stdio or SSE)."""

import json
import os
import socket
import subprocess
import sys
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Force unbuffered I/O for reliable MCP stdio transport on Windows
if sys.platform == "win32":
    try:
        import msvcrt
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    except (AttributeError, OSError, Exception):
        pass  # stdin/stdout may be redirected (e.g. in tests)


_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent  # integrations/mcp -> repo root
_DEFAULT_SCRIPTS = _REPO_ROOT / "src" / "NeuralDebug"
SCRIPTS_DIR = Path(os.environ.get("NeuralDebug_SCRIPTS", str(_DEFAULT_SCRIPTS)))
_LLM_DIR = _REPO_ROOT / "src" / "neuraldebug" / "llm"

# Detect Python command
PYTHON = sys.executable


sys.path.insert(0, str(SCRIPTS_DIR))
from language_registry import get_registry as _get_registry  # noqa: E402

_reg = _get_registry(str(SCRIPTS_DIR))
LANG_SCRIPTS = _reg.lang_scripts
EXT_TO_LANG = _reg.ext_to_lang
DEFAULT_PORTS = _reg.default_ports


def _detect_language(target: str) -> str:
    ext = Path(target).suffix.lower()
    return EXT_TO_LANG.get(ext, "cpp")


def _get_script(language: str) -> Path:
    script_name = LANG_SCRIPTS.get(language, LANG_SCRIPTS["cpp"])
    return SCRIPTS_DIR / script_name


def _run_script(script: Path, args: List[str], timeout: int = 60) -> dict:
    cmd = [PYTHON, str(script)] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=str(_REPO_ROOT)
        )
        stdout = result.stdout.strip()
        if stdout:
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                return {"status": "ok", "output": stdout, "stderr": result.stderr}
        return {"status": "error" if result.returncode != 0 else "ok",
                "output": result.stdout, "stderr": result.stderr,
                "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": f"Command timed out after {timeout}s"}
    except Exception as e:
        return {"status": "error", "message": str(e)}



def _send_llm_command(port: int, action: str, args: str = "",
                      host: str = "127.0.0.1", timeout: int = 120) -> dict:
    """Send a JSON command to the LLM debug server and return the response.

    Uses the same wire protocol as debug_common.send_command: a single JSON
    object ``{"action": ..., "args": ...}`` sent as raw bytes, response read
    until EOF.
    """
    cmd = {"action": action, "args": args}
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.sendall(json.dumps(cmd).encode("utf-8"))
        chunks: list[str] = []
        while True:
            try:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data.decode("utf-8"))
            except socket.timeout:
                break
        sock.close()
        text = "".join(chunks)
        if text:
            return json.loads(text)
        return {"status": "error", "message": "Empty response from server"}
    except ConnectionRefusedError:
        return {"status": "error",
                "message": f"No LLM debug server on {host}:{port}. "
                           f"Start one with NeuralDebug_llm_start first."}
    except Exception as e:
        return {"status": "error",
                "message": f"Failed to communicate with LLM debug server on "
                           f"{host}:{port}: {e}"}


def _start_background(script: Path, args: List[str]) -> dict:
    """Start a script as a background process and return its PID."""
    try:
        proc = subprocess.Popen(
            [PYTHON, str(script)] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(_REPO_ROOT),
        )
        return {"status": "ok",
                "message": f"Started LLM debug server (PID {proc.pid})",
                "pid": proc.pid}
    except Exception as e:
        return {"status": "error", "message": str(e)}


TOOLS = [
    {
        "name": "NeuralDebug_info",
        "description": "Detect available debuggers, compilers, and toolchain for the current platform. Use this first to understand what debugging tools are available.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "description": "Target language: python, c, cpp, csharp, rust, java, go, nodejs, typescript, ruby",
                    "default": "cpp"
                }
            }
        }
    },
    {
        "name": "NeuralDebug_start_server",
        "description": "Launch a debug server for a target program. The server persists across conversation turns. Auto-compiles source files if needed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Path to source file or executable to debug"
                },
                "language": {
                    "type": "string",
                    "description": "Target language (auto-detected from extension if omitted)"
                },
                "port": {
                    "type": "integer",
                    "description": "TCP port for the debug server (default: auto by language)"
                },
                "program_args": {
                    "type": "string",
                    "description": "Arguments to pass to the debugged program"
                },
                "source_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional source directories for symbol resolution"
                }
            },
            "required": ["target"]
        }
    },
    {
        "name": "NeuralDebug_status",
        "description": "Check if a debug server is running on the specified port.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "description": "Port to check", "default": 5678},
                "language": {"type": "string", "description": "Language (to select correct script)"}
            }
        }
    },
    {
        "name": "NeuralDebug_set_breakpoint",
        "description": "Set a breakpoint at a line number, function name, or file:line location.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Breakpoint location: line number (42), function name (main), or file:line (source.c:42)"
                },
                "condition": {
                    "type": "string",
                    "description": "Optional condition expression (e.g., 'x > 10')"
                },
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            },
            "required": ["location"]
        }
    },
    {
        "name": "NeuralDebug_start_execution",
        "description": "Start program execution. The program will pause at the first breakpoint or first line (Python).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            }
        }
    },
    {
        "name": "NeuralDebug_step",
        "description": "Step through code: step_over (next line), step_in (enter function), step_out (exit function).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["step_over", "step_in", "step_out"],
                    "description": "Step action"
                },
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "NeuralDebug_continue",
        "description": "Continue execution until the next breakpoint or program completion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            }
        }
    },
    {
        "name": "NeuralDebug_inspect",
        "description": "Inspect local variables in the current scope. Returns variable names, types, and values.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            }
        }
    },
    {
        "name": "NeuralDebug_evaluate",
        "description": "Evaluate an expression in the current debugging context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Expression to evaluate (e.g., 'len(my_list)', 'sizeof(buf)', 'x + y')"
                },
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            },
            "required": ["expression"]
        }
    },
    {
        "name": "NeuralDebug_backtrace",
        "description": "Show the call stack (backtrace) at the current execution point.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            }
        }
    },
    {
        "name": "NeuralDebug_list_code",
        "description": "Show source code around the current execution point.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            }
        }
    },
    {
        "name": "NeuralDebug_breakpoints",
        "description": "List all currently set breakpoints.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            }
        }
    },
    {
        "name": "NeuralDebug_remove_breakpoint",
        "description": "Remove a breakpoint at the specified line.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "line": {"type": "integer", "description": "Line number of breakpoint to remove"},
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            },
            "required": ["line"]
        }
    },
    {
        "name": "NeuralDebug_run_to_line",
        "description": "Continue execution until reaching a specific line number.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "line": {"type": "integer", "description": "Target line number"},
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            },
            "required": ["line"]
        }
    },
    {
        "name": "NeuralDebug_stop",
        "description": "Stop the debug server and end the debugging session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "default": 5678},
                "language": {"type": "string"}
            }
        }
    },
    # ── LLM debug tools ──────────────────────────────────────────────
    {
        "name": "NeuralDebug_llm_start",
        "description": "Start an LLM debug server for inspecting transformer model reasoning. Loads a HuggingFace model and starts a TCP debug session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "HuggingFace model name or local path", "default": "distilgpt2"},
                "adapter": {"type": "string", "description": "Model adapter: auto, gpt2, llama, or custom adapter name", "default": "auto"},
                "device": {"type": "string", "description": "Device: auto, cpu, cuda, mps", "default": "auto"},
                "port": {"type": "integer", "description": "TCP port for the debug server", "default": 5680}
            },
            "required": []
        }
    },
    {
        "name": "NeuralDebug_llm_step",
        "description": "Step through transformer layers during a forward pass. Use step_over to execute the next layer, step_in for sub-module detail.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["step_over", "step_in", "step_out", "continue"], "default": "step_over"},
                "port": {"type": "integer", "default": 5680}
            },
            "required": []
        }
    },
    {
        "name": "NeuralDebug_llm_inspect",
        "description": "Inspect the current layer state: activations, attention weights, hidden states, and predictions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "What to inspect: activations, attention, hidden_state, predictions"},
                "port": {"type": "integer", "default": 5680}
            },
            "required": []
        }
    },
    {
        "name": "NeuralDebug_llm_logit_lens",
        "description": "Run Logit Lens analysis: see what the model would predict at each intermediate layer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Input text to analyze"},
                "top_k": {"type": "integer", "description": "Number of top predictions per layer", "default": 5},
                "port": {"type": "integer", "default": 5680}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "NeuralDebug_llm_patch",
        "description": "Activation Patching (causal tracing): identify which layer is causally responsible for a prediction.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "clean_prompt": {"type": "string", "description": "The original prompt"},
                "corrupted_prompt": {"type": "string", "description": "A corrupted version of the prompt"},
                "port": {"type": "integer", "default": 5680}
            },
            "required": ["clean_prompt"]
        }
    },
    {
        "name": "NeuralDebug_llm_attention",
        "description": "Analyze attention patterns across heads and layers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Input text to analyze"},
                "layer": {"type": "integer", "description": "Specific layer to analyze (omit for all layers)"},
                "port": {"type": "integer", "default": 5680}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "NeuralDebug_llm_diagnose",
        "description": "Run autonomous diagnosis on the model for a given prompt — combines Logit Lens, attention analysis, and patching.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Input text to diagnose"},
                "port": {"type": "integer", "default": 5680}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "NeuralDebug_llm_hallucinate",
        "description": "Detect hallucinations in model output by analyzing per-token grounding across layers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Input text to generate from and check for hallucinations"},
                "max_tokens": {"type": "integer", "description": "Max tokens to generate", "default": 50},
                "port": {"type": "integer", "default": 5680}
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "NeuralDebug_llm_surgery",
        "description": "Perform attention head surgery — ablate or amplify specific attention heads to test their impact.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["ablate", "amplify"], "description": "Surgery type"},
                "layer": {"type": "integer", "description": "Layer index"},
                "head": {"type": "integer", "description": "Head index"},
                "prompt": {"type": "string", "description": "Prompt to test with"},
                "factor": {"type": "number", "description": "Amplification factor (for amplify operation)", "default": 2.0},
                "port": {"type": "integer", "default": 5680}
            },
            "required": ["operation", "layer", "head", "prompt"]
        }
    },
    {
        "name": "NeuralDebug_llm_finetune",
        "description": "Run LoRA fine-tuning on the model to fix knowledge gaps or biases.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "training_data": {"type": "string", "description": "Training text or path to training file"},
                "epochs": {"type": "integer", "default": 3},
                "lr": {"type": "number", "description": "Learning rate", "default": 0.0001},
                "port": {"type": "integer", "default": 5680}
            },
            "required": ["training_data"]
        }
    },
    {
        "name": "NeuralDebug_llm_probe_api",
        "description": "Debug reasoning of hosted LLMs (GPT-4, Claude, Gemini) through API-based probing techniques.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "technique": {"type": "string", "enum": ["logprobs", "perturb", "cot", "consistency", "counterfactual", "calibrate"], "description": "Probing technique to use"},
                "prompt": {"type": "string", "description": "Input prompt to probe"},
                "provider": {"type": "string", "enum": ["openai", "anthropic", "google", "ollama"], "default": "openai"},
                "model": {"type": "string", "description": "Model name (e.g. gpt-4o, claude-sonnet-4-20250514)", "default": "gpt-4o"},
                "port": {"type": "integer", "default": 5685}
            },
            "required": ["technique", "prompt"]
        }
    },
]

# Track active sessions for language detection
_active_sessions: dict[int, str] = {}  # port -> language


def _resolve_lang_and_port(args: dict) -> Tuple[str, int]:
    """Resolve language and port from tool arguments."""
    lang = args.get("language", "")
    port = args.get("port", 0)

    if not lang and "target" in args:
        lang = _detect_language(args["target"])

    if not lang:
        # Check active sessions
        if port and port in _active_sessions:
            lang = _active_sessions[port]
        else:
            lang = "cpp"  # default

    if not port:
        port = DEFAULT_PORTS.get(lang, 5678)

    return lang, port


def handle_tool_call(name: str, arguments: dict) -> dict:
    """Execute a tool call and return the result."""

    lang, port = _resolve_lang_and_port(arguments)
    script = _get_script(lang)

    if name == "NeuralDebug_info":
        return _run_script(script, ["info"])

    elif name == "NeuralDebug_start_server":
        target = arguments["target"]
        lang = arguments.get("language") or _detect_language(target)
        port = arguments.get("port") or DEFAULT_PORTS.get(lang, 5678)
        script = _get_script(lang)

        cmd = ["serve", target, "--port", str(port), "--daemonize"]
        if arguments.get("program_args"):
            cmd += ["--args", arguments["program_args"]]
        if arguments.get("source_paths"):
            cmd += ["--srcpath"] + arguments["source_paths"]

        _active_sessions[port] = lang
        result = _run_script(script, cmd)

        # Verify server started
        import time
        time.sleep(2)
        status = _run_script(script, ["status", "--port", str(port)])
        result["server_status"] = status
        return result

    elif name == "NeuralDebug_status":
        return _run_script(script, ["status", "--port", str(port)])

    elif name == "NeuralDebug_set_breakpoint":
        loc = arguments["location"]
        cmd_args = ["cmd", "--port", str(port), "b", loc]
        if arguments.get("condition"):
            cmd_args.append(arguments["condition"])
        return _run_script(script, cmd_args)

    elif name == "NeuralDebug_start_execution":
        return _run_script(script, ["cmd", "--port", str(port), "start"])

    elif name == "NeuralDebug_step":
        action = arguments["action"]
        return _run_script(script, ["cmd", "--port", str(port), action])

    elif name == "NeuralDebug_continue":
        return _run_script(script, ["cmd", "--port", str(port), "continue"])

    elif name == "NeuralDebug_inspect":
        return _run_script(script, ["cmd", "--port", str(port), "inspect"])

    elif name == "NeuralDebug_evaluate":
        expr = arguments["expression"]
        return _run_script(script, ["cmd", "--port", str(port), "e", expr])

    elif name == "NeuralDebug_backtrace":
        return _run_script(script, ["cmd", "--port", str(port), "backtrace"])

    elif name == "NeuralDebug_list_code":
        return _run_script(script, ["cmd", "--port", str(port), "list"])

    elif name == "NeuralDebug_breakpoints":
        return _run_script(script, ["cmd", "--port", str(port), "breakpoints"])

    elif name == "NeuralDebug_remove_breakpoint":
        line = str(arguments["line"])
        return _run_script(script, ["cmd", "--port", str(port), "remove_breakpoint", line])

    elif name == "NeuralDebug_run_to_line":
        line = str(arguments["line"])
        return _run_script(script, ["cmd", "--port", str(port), "run_to_line", line])

    elif name == "NeuralDebug_stop":
        if port in _active_sessions:
            del _active_sessions[port]
        return _run_script(script, ["stop", "--port", str(port)])

    # ── LLM debug tool handlers ──────────────────────────────────────
    elif name == "NeuralDebug_llm_start":
        model = arguments.get("model", "distilgpt2")
        adapter = arguments.get("adapter", "auto")
        device = arguments.get("device", "auto")
        llm_port = arguments.get("port", 5680)
        script = _LLM_DIR / "llm_debug_session.py"
        return _start_background(script, [
            "serve", "--model", model, "--adapter", adapter,
            "--device", device, "--port", str(llm_port), "--host", "127.0.0.1"
        ])

    elif name == "NeuralDebug_llm_step":
        action = arguments.get("action", "step_over")
        llm_port = arguments.get("port", 5680)
        return _send_llm_command(llm_port, action)

    elif name == "NeuralDebug_llm_inspect":
        expr = arguments.get("expression", "")
        llm_port = arguments.get("port", 5680)
        return _send_llm_command(llm_port, "inspect", expr)

    elif name == "NeuralDebug_llm_logit_lens":
        prompt = arguments["prompt"]
        top_k = arguments.get("top_k", 5)
        llm_port = arguments.get("port", 5680)
        return _send_llm_command(llm_port, "logit_lens",
                                 f"{prompt} --top_k {top_k}")

    elif name == "NeuralDebug_llm_patch":
        clean = arguments["clean_prompt"]
        corrupted = arguments.get("corrupted_prompt", "")
        llm_port = arguments.get("port", 5680)
        args_str = f"--clean {clean} --corrupted {corrupted}"
        return _send_llm_command(llm_port, "patch", args_str)

    elif name == "NeuralDebug_llm_attention":
        prompt = arguments["prompt"]
        llm_port = arguments.get("port", 5680)
        args_str = prompt
        if "layer" in arguments:
            args_str += f" --layer {arguments['layer']}"
        return _send_llm_command(llm_port, "attention", args_str)

    elif name == "NeuralDebug_llm_diagnose":
        prompt = arguments["prompt"]
        llm_port = arguments.get("port", 5680)
        return _send_llm_command(llm_port, "diagnose", prompt)

    elif name == "NeuralDebug_llm_hallucinate":
        prompt = arguments["prompt"]
        max_tokens = arguments.get("max_tokens", 50)
        llm_port = arguments.get("port", 5680)
        return _send_llm_command(llm_port, "hallucinate",
                                 f"{prompt} --max_tokens {max_tokens}")

    elif name == "NeuralDebug_llm_surgery":
        op = arguments["operation"]
        layer = arguments["layer"]
        head = arguments["head"]
        prompt = arguments["prompt"]
        llm_port = arguments.get("port", 5680)
        args_str = f"--op {op} --layer {layer} --head {head} --prompt {prompt}"
        if "factor" in arguments:
            args_str += f" --factor {arguments['factor']}"
        return _send_llm_command(llm_port, "surgery", args_str)

    elif name == "NeuralDebug_llm_finetune":
        training_data = arguments["training_data"]
        epochs = arguments.get("epochs", 3)
        lr = arguments.get("lr", 0.0001)
        llm_port = arguments.get("port", 5680)
        return _send_llm_command(llm_port, "finetune",
                                 json.dumps({"data": training_data,
                                             "epochs": epochs, "lr": lr}),
                                 timeout=600)

    elif name == "NeuralDebug_llm_probe_api":
        technique = arguments["technique"]
        prompt = arguments["prompt"]
        provider = arguments.get("provider", "openai")
        model_name = arguments.get("model", "gpt-4o")
        api_port = arguments.get("port", 5685)
        args_str = f"--technique {technique} --prompt {prompt} --provider {provider} --model {model_name}"
        return _send_llm_command(api_port, "probe_api", args_str)

    else:
        return {"status": "error", "message": f"Unknown tool: {name}"}



def _write_message(msg: dict):
    """Write a JSON-RPC message to stdout (Content-Length framing)."""
    body_bytes = json.dumps(msg).encode("utf-8")
    header = f"Content-Length: {len(body_bytes)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header + body_bytes)
    sys.stdout.buffer.flush()


async def _handle_request(msg: dict) -> Optional[dict]:
    """Handle a single JSON-RPC request."""
    method = msg.get("method", "")
    msg_id = msg.get("id")
    params = msg.get("params", {})

    if method == "initialize":
        # Negotiate protocol version: use the client's version if we support it
        _SUPPORTED_VERSIONS = ("2025-03-26", "2024-11-05")
        client_version = params.get("protocolVersion", "2024-11-05")
        negotiated = client_version if client_version in _SUPPORTED_VERSIONS else _SUPPORTED_VERSIONS[0]
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "NeuralDebug",
                    "version": "1.0.0"
                }
            }
        }

    elif method == "notifications/initialized":
        return None  # notification, no response

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": TOOLS}
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # Run in thread pool to not block
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, handle_tool_call, tool_name, arguments
        )

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, indent=2)
                    }
                ]
            }
        }

    elif method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    else:
        # Unknown method
        if msg_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}
            }
        return None


def _read_message_sync() -> Optional[dict]:
    """Read a JSON-RPC message from stdin synchronously (Content-Length framing)."""
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.decode("utf-8").strip()
        if not line:
            break
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()

    length = int(headers.get("content-length", 0))
    if length == 0:
        return None

    body = sys.stdin.buffer.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


async def run_stdio():
    """Run MCP server over stdio transport."""
    loop = asyncio.get_event_loop()

    while True:
        msg = await loop.run_in_executor(None, _read_message_sync)
        if msg is None:
            break

        response = await _handle_request(msg)
        if response is not None:
            _write_message(response)



def run_sse(host: str = "0.0.0.0", port: int = 8080):
    """Run MCP server over SSE transport (requires aiohttp)."""
    try:
        from aiohttp import web
        from aiohttp_sse import sse_response
    except ImportError:
        print("SSE transport requires: pip install aiohttp aiohttp-sse", file=sys.stderr)
        sys.exit(1)

    app = web.Application()

    async def handle_sse(request):
        async with sse_response(request) as resp:
            async for msg in request.content:
                data = json.loads(msg)
                response = await _handle_request(data)
                if response:
                    await resp.send(json.dumps(response))
        return resp

    async def handle_post(request):
        data = await request.json()
        response = await _handle_request(data)
        return web.json_response(response or {})

    app.router.add_get("/sse", handle_sse)
    app.router.add_post("/message", handle_post)

    print(f"NeuralDebug MCP Server (SSE) listening on {host}:{port}", file=sys.stderr)
    web.run_app(app, host=host, port=port)



def main():
    import argparse
    parser = argparse.ArgumentParser(description="NeuralDebug MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio",
                        help="Transport protocol (default: stdio)")
    parser.add_argument("--port", type=int, default=8080,
                        help="Port for SSE transport (default: 8080)")
    parser.add_argument("--host", default="0.0.0.0",
                        help="Host for SSE transport")
    args = parser.parse_args()

    if args.transport == "sse":
        run_sse(args.host, args.port)
    else:
        asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
