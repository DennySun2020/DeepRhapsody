---
name: llm-debugger
description: Debug LLM/transformer model reasoning interactively using PyTorch hooks and interpretability techniques. Step through forward passes layer by layer, inspect activations and attention weights, run Logit Lens to see how predictions evolve, Activation Patching to find causal layers, Probing to test what information is encoded, and Attention Analysis to understand head behavior. Supports GPT-2 family, Llama, Mistral, Qwen, DeepSeek, and custom architectures via model adapters. Same TCP/JSON protocol as the traditional debugger skill.
---
# LLM Debugger Skill

Debug **transformer model reasoning** — step through forward passes layer by layer, inspect tensor activations, and run interpretability techniques to understand *why* a model makes its predictions.

> Same interface as traditional debugging, but instead of source lines you walk through neural network layers.

## Prerequisites

- Python 3.8+
- PyTorch: `pip install torch transformers`
- A HuggingFace model (downloaded automatically on first use)

> **Tip**: On Windows, if you hit DLL errors with the latest PyTorch, use:
> `pip install torch --index-url https://download.pytorch.org/whl/cpu`

## Quick Start

### Step 1: Start the Debug Server

```bash
python src/NeuralDebug/llm/llm_debug_session.py serve --model distilgpt2 --port 5680
```

Available models (use `--model` / `-m`):
| Model | Params | Speed | Quality |
|-------|--------|-------|---------|
| `distilgpt2` (default) | 82M | Fastest | Basic |
| `gpt2` | 124M | Fast | Fair |
| `gpt2-medium` | 345M | Medium | Good |
| `gpt2-large` | 774M | Slow | Better |
| `gpt2-xl` | 1.5B | Slowest | Best |

Any HuggingFace model with a supported adapter works (see Model Adapters below).

### Server Flags

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--model <name>` | `-m` | HuggingFace model name or local path | `distilgpt2` |
| `--adapter <name>` | | Force a specific model adapter (`gpt2`, `llama`, or custom) | Auto-detect |
| `--device <device>` | `-d` | PyTorch device (`cpu`, `cuda`, `cuda:0`, `mps`) | `cpu` |
| `--port <N>` | `-p` | TCP port for the debug server | `5680` |

```bash
# Llama on GPU
python src/NeuralDebug/llm/llm_debug_session.py serve -m meta-llama/Llama-2-7b-hf --device cuda -p 5680

# Force adapter for unknown architecture
python src/NeuralDebug/llm/llm_debug_session.py serve -m my-custom-model --adapter llama -p 5680
```

Wait for `LLM Debug server listening on port 5680` before sending commands.

### Step 2: Send Commands

All commands use the same `cmd` interface:
```bash
python src/NeuralDebug/llm/llm_debug_session.py cmd --port 5680 <COMMAND> [ARGS]
```

## Commands

### Conversation
| Command | Alias | What it does |
|---------|-------|-------------|
| `start <prompt>` | `s` | Begin inference with a prompt (resets session if already started) |
| `generate [n]` | `gen` | Run full generation (default 50 tokens) — ask the model a question |

### Step-Through Debugging
| Command | Alias | What it does |
|---------|-------|-------------|
| `step_over` | `n` | Execute current layer and advance |
| `step_in` | `si` | Enter a block's sub-layers (attention, FFN) |
| `step_out` | `so` | Finish current block, return to parent |
| `continue` | `c` | Run to next breakpoint or end of forward pass |
| `b <layer>` | | Set breakpoint on a layer (e.g., `b block_3`) |
| `remove_breakpoint <layer>` | `rb` | Remove a breakpoint |
| `breakpoints` | `bl` | List all breakpoints |
| `inspect` | `i` | Show current layer state and tensor statistics |
| `evaluate <expr>` | `e` | Evaluate PyTorch expression on live tensors |
| `list` | `l` | Show model architecture tree |
| `backtrace` | `bt` | Show layer execution stack |

### Interpretability Techniques
| Command | Alias | What it does |
|---------|-------|-------------|
| `logit_lens [k]` | `lens` | **Logit Lens**: at each layer, what would the model predict if it stopped here? |
| `patch <corrupted>` | `causal_trace` | **Activation Patching**: which layer is causally responsible for this prediction? |
| `probe [task]` | `probing` | **Probing**: what information is encoded at each layer? Tasks: `next_token`, `token_identity`, `position` |
| `attention [pos]` | `attn` | **Attention Analysis**: rank heads by focus; with pos, show attention TO that token |

### Diagnosis
| Command | Alias | What it does |
|---------|-------|-------------|
| `diagnose <test.json>` | `diag` | **Autonomous Diagnosis**: run full diagnostic pipeline on a test suite |

> **Fine-tuning** is handled by the separate **llm-finetuner** skill. Use `finetune` / `ft` to inject knowledge after diagnosis.

### Tool Forge (Dynamic Analysis)
| Command | Alias | What it does |
|---------|-------|-------------|
| `exec_analysis <code>` | `exec`, `forge` | **Tool Forge**: execute custom Python analysis code against the live model in a sandbox |
| `exec_analysis @<file.py>` | | Load analysis code from a file |
| `exec_analysis --timeout 120 <code>` | | Override the default 60s timeout |

The code must define an `analyze(model, tokenizer, input_ids)` function that returns a dict. Only `torch`, `numpy`, `math`, `collections`, `json`, and `functools` are allowed. No filesystem, network, or weight mutation.

**Example — custom logit analysis:**
```python
exec_analysis def analyze(model, tokenizer, input_ids):
    import torch
    with torch.no_grad():
        out = model(input_ids)
    logits = out.logits[0, -1]
    probs = torch.softmax(logits, dim=-1)
    top_ids = probs.topk(5).indices.tolist()
    return {"top5": [(tokenizer.decode([i]), probs[i].item()) for i in top_ids]}
```

**Example — register a custom hook:**
```python
exec_analysis def analyze(model, tokenizer, input_ids):
    import torch
    activations = {}
    def hook(name):
        def fn(mod, inp, out):
            activations[name] = out.detach().float().mean().item()
        return fn
    handles = []
    for i, block in enumerate(model.transformer.h):
        handles.append(block.register_forward_hook(hook(f"block_{i}")))
    with torch.no_grad():
        model(input_ids)
    for h in handles:
        h.remove()
    return activations
```

### Session
| Command | Alias | What it does |
|---------|-------|-------------|
| `quit` | `q` | End the debug session |

## How the Skills Fit Together

**Foundation (always active):**
- **PyTorch Hooks** capture activation statistics at every layer automatically when you `start`.

**On-demand (choose based on your question):**

| Question | Skill | Command |
|----------|-------|---------|
| "What does the model say?" | Generation | `generate 30` |
| "At which layer does the answer emerge?" | Logit Lens | `logit_lens` |
| "Which layer is responsible for this answer?" | Activation Patching | `patch "corrupted prompt"` |
| "What info is encoded at each layer?" | Probing | `probe next_token` |
| "Which attention heads matter?" | Attention Analysis | `attention` |
| "What's wrong and how to fix it?" | Autonomous Diagnosis | `diagnose test.json` |
| "Run my custom analysis" | Tool Forge | `exec_analysis <code>` |

> For knowledge injection after diagnosis, see the **llm-finetuner** skill.

## Typical Debugging Workflow

```bash
# 1. Start server (once, in a separate terminal)
python src/NeuralDebug/llm/llm_debug_session.py serve -m gpt2-medium -p 5680

# 2. Ask the model a question
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 start "The capital of Japan is"
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 generate 20
# → "The capital of Japan is Tokyo, and the capital of Japan is Tokyo."

# 3. Debug WHY it predicted "Tokyo"
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 start "The capital of Japan is"

# Logit Lens — where does "Tokyo" first appear?
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 logit_lens
# → block_15: "Tokyo" first appears (p=0.30), peaks at block_18 (p=0.99)

# Attention — which heads look at "Japan"?
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 attention 3
# → L5H11 (w=0.999), L4H13 (w=0.999) fixated on "Japan"

# Probing — what info is encoded?
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 probe next_token
# → accuracy rises in later layers where prediction emerges

# Activation Patching — is this Japan-specific?
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 patch "The capital of France is"
# → clean p=0.25 vs corrupted p=0.0001 — yes, completely Japan-specific

# 4. Try a different question without restarting server
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 start "Who invented the telephone?"
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 generate 30

# 5. Done
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 quit
```

## Layer Names for Breakpoints

```
embedding                    — Token + position embedding
block_0 … block_N           — Transformer blocks
block_X.attention            — Self-attention sub-tree
block_X.attn.ln_qkv         — LayerNorm + Q/K/V projection
block_X.attn.scores          — Scaled dot-product attention
block_X.attn.output          — Output projection + residual
block_X.ffn                  — Feed-forward sub-tree
block_X.ffn.ln_up            — LayerNorm + up-projection
block_X.ffn.activation       — GELU activation
block_X.ffn.down_residual    — Down-projection + residual
final_norm                   — Final layer normalisation
lm_head                      — Vocabulary projection (logits)
```

## Response Format

Every command returns JSON following the standard NeuralDebug protocol:
```json
{
  "status": "paused",
  "command": "step_over",
  "message": "Paused at Transformer Block 3/5",
  "current_location": {
    "layer": "block_3",
    "layer_type": "GPT2Block",
    "display_name": "Transformer Block 3/5"
  },
  "call_stack": [...],
  "local_variables": {
    "hidden_states": {"shape": [1, 8, 768], "mean": 0.012, "std": 0.71},
    "attention_weights": {"shape": [1, 12, 8, 8], "mean": 0.125}
  },
  "stdout_new": "",
  "stderr_new": ""
}
```

## Model Adapters

The debugger uses a **ModelAdapter** abstraction to support multiple architectures.
GPT-2 and Llama families are auto-detected; you can add custom adapters.

### Built-in Adapters

| Adapter | Architectures | Auto-detection |
|---------|--------------|----------------|
| `gpt2` | GPT-2, DistilGPT-2 | `model.transformer.h` exists |
| `llama` | Llama, Mistral, Qwen, DeepSeek | `model.model.layers` exists |

### Custom Adapters

Implement `ModelAdapter` and register with `AdapterRegistry`:

```python
from neuraldebug.llm.adapters.base import ModelAdapter, ModelInfo
from neuraldebug.llm.adapters.registry import AdapterRegistry

class MyAdapter(ModelAdapter):
    def info(self):
        return ModelInfo(name="my-model", num_layers=24, num_heads=16,
                         hidden_size=1024, vocab_size=50257)
    def get_block(self, idx):
        return self.model.layers[idx]
    def embed(self, input_ids):
        return self.model.embed_tokens(input_ids)
    # ... implement remaining abstract methods

AdapterRegistry.register("my-model", MyAdapter,
    detect_fn=lambda m: hasattr(m, "my_custom_attribute"))
```

Then use `--adapter my-model` or let auto-detection find it.

## Architecture

```
src/NeuralDebug/llm/
├── llm_debug_session.py      # CLI + TCP server (entry point)
├── debugger.py               # LLMDebugger (command dispatch)
├── stepper.py                # GPT2Stepper (execution engine)
├── hooks/                    # HookManager (PyTorch forward hooks)
│   ├── base.py
│   └── pytorch.py
├── adapters/                 # Model adapter abstraction
│   ├── base.py               # ModelAdapter ABC + ModelInfo
│   ├── registry.py           # AdapterRegistry (auto-detect / manual)
│   ├── gpt2.py               # GPT-2 family adapter
│   └── llama.py              # Llama / Mistral / Qwen / DeepSeek adapter
├── commands/                 # Command modules
│   ├── core.py               # start, generate, step, inspect
│   ├── advanced.py           # diagnose, tool forge
│   ├── interpretability.py   # logit_lens, patch, probe, attention
│   └── finetune.py           # LoRA fine-tuning commands
├── interpretability.py       # LogitLens, ActivationPatching, Probing, AttentionAnalysis
├── hallucination_detector.py # Per-token hallucination detection
├── head_surgery.py           # Attention head ablation/amplification
├── neuron_analysis.py        # Neuron dashboard, scan, ablate
├── sae.py                    # Sparse Autoencoder (decompose, train)
├── api_probe.py              # API-based debugging (see llm-api-probe skill)
├── diagnosis.py              # DiagnosisEngine (autonomous diagnosis)
├── recommendations.py        # Rule-based remediation recommendations
├── tf_llm_debug_session.py   # TensorFlow backend (entry point)
├── tf_debugger.py            # TF command dispatch
├── tf_stepper.py             # TF execution engine
├── tf_hooks.py               # TF call-wrapping hooks
└── README.md                 # Full documentation
```
