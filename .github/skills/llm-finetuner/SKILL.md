---
name: llm-finetuner
description: Fine-tune GPT-2 family models with LoRA to inject missing knowledge. Diagnose knowledge gaps, generate paraphrased training data, run lightweight LoRA training, verify predictions before/after, and persist merged weights to disk. Supports distilgpt2, gpt2, gpt2-medium, gpt2-large, gpt2-xl. Uses the same TCP/JSON server as the LLM debugger skill.
---
# LLM Fine-Tuner Skill

Inject **missing knowledge** into GPT-2 family models using LoRA (Low-Rank Adaptation) fine-tuning. When a model doesn't know a fact, this skill teaches it — then verifies the fix and saves the weights to disk.

> Designed to work alongside the LLM Debugger skill: debug → diagnose → fine-tune → verify.

## Prerequisites

- Python 3.8+
- PyTorch, Transformers, PEFT: `pip install torch transformers peft`
- A HuggingFace GPT-2 family model (downloaded automatically on first use)

> **Important**: peft 0.7.1 is required for compatibility with transformers 4.36.x. Newer peft versions require newer transformers.

## Quick Start

### Step 1: Start the Server

The fine-tuner uses the same server as the LLM debugger:

```bash
python src/NeuralDebug/llm/llm_debug_session.py serve --model gpt2-medium --port 5680
```

Available models (use `--model` / `-m`):
| Model | Params | Fine-tune Time (CPU) | Saved Size |
|-------|--------|----------------------|------------|
| `distilgpt2` | 82M | ~60s | ~315 MB |
| `gpt2` | 124M | ~90s | ~500 MB |
| `gpt2-medium` | 345M | ~5 min | ~1.4 GB |
| `gpt2-large` | 774M | ~15 min | ~3 GB |
| `gpt2-xl` | 1.5B | ~30 min | ~6 GB |

Wait for `LLM Debug server listening on port 5680` before sending commands.

### Step 2: Create a Fine-Tuning Config

Create a JSON file describing what to teach the model:

```json
{
  "facts": [
    "Dr. Elena Vasquez is the director of Horizon Research Labs",
    "Dr. Elena Vasquez leads Horizon Research Labs, one of the world's largest computer science research organizations",
    "As director of Horizon Research Labs, Dr. Elena Vasquez oversees research in artificial intelligence and computing"
  ],
  "verification_prompt": "Dr. Elena Vasquez is the director of",
  "expected_token": "Horizon",
  "config": {
    "num_steps": 150,
    "lora_r": 16,
    "lora_alpha": 32,
    "learning_rate": 2e-4,
    "num_paraphrases": 8
  }
}
```

### Step 3: Run Fine-Tuning

```bash
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 -t 600 finetune config.json
```

> Use `-t 600` (timeout in seconds) for larger models that take longer to train on CPU.

### Step 4: Verify

```bash
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 start "Dr. Elena Vasquez is the director of"
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 generate 20
# → "Dr. Elena Vasquez is the director of Horizon Research Labs"
```

## Commands

| Command | Alias | What it does |
|---------|-------|-------------|
| `finetune <config.json>` | `ft` | Run LoRA fine-tuning from a JSON config file |
| `finetune "<fact>" --verify "<prompt>" --expect "<token>"` | `ft` | Inline fine-tuning without a config file |
| `generate [n]` | `gen` | Generate tokens to verify the model learned the fact |
| `start <prompt>` | `s` | Set a prompt for generation or debugging |
| `diagnose <test.json>` | `diag` | Run autonomous diagnosis to find knowledge gaps first |

## Config File Reference

```json
{
  "facts": ["<required: list of facts to teach>"],
  "verification_prompt": "<required: prompt to test after training>",
  "expected_token": "<required: token that should appear>",
  "config": {
    "num_steps": 100,
    "lora_r": 8,
    "lora_alpha": 16,
    "lora_dropout": 0.05,
    "learning_rate": 1e-4,
    "batch_size": 1,
    "max_seq_len": 128,
    "warmup_steps": 10,
    "weight_decay": 0.01,
    "num_paraphrases": 8,
    "auto_save": true
  }
}
```

### Config Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `num_steps` | 100 | Number of training steps |
| `lora_r` | 8 | LoRA rank (higher = more capacity, slower) |
| `lora_alpha` | 16 | LoRA scaling factor (usually 2× `lora_r`) |
| `lora_dropout` | 0.05 | Dropout on LoRA layers |
| `learning_rate` | 1e-4 | Optimizer learning rate |
| `num_paraphrases` | 8 | Number of paraphrase variants per fact |
| `auto_save` | true | Persist merged model to disk after training |

### Recommended Settings by Model Size

| Model | `lora_r` | `lora_alpha` | `num_steps` | `learning_rate` |
|-------|----------|-------------|-------------|-----------------|
| distilgpt2 | 8 | 16 | 100 | 1e-4 |
| gpt2 | 8 | 16 | 100 | 1e-4 |
| gpt2-medium | 16 | 32 | 150 | 2e-4 |
| gpt2-large | 16 | 32 | 150 | 2e-4 |
| gpt2-xl | 16 | 32 | 200 | 2e-4 |

## How It Works

### 5-Step Pipeline

1. **Verify baseline** — check what the model currently predicts for the verification prompt (before training)
2. **Generate training data** — create paraphrased versions of each fact using 8 template patterns (Q&A, declarative, etc.)
3. **Attach LoRA adapters** — add lightweight trainable adapters to the model's attention and FFN layers (`c_attn`, `c_proj`, `c_fc`), typically ~1.7% of total parameters
4. **Train** — run SGD-style optimization for the configured number of steps, logging loss every 20 steps
5. **Verify & save** — check the expected token's rank/probability improved, merge LoRA weights into the base model, and save to disk

### LoRA Architecture

LoRA adds small trainable matrices to existing model layers without modifying the original weights during training:

```
Original:  hidden → W → output           (frozen)
With LoRA: hidden → W → output + B·A·x   (A, B are trainable)
```

Target modules for GPT-2: `c_attn` (Q/K/V projection), `c_proj` (attention output), `c_fc` (FFN up-projection).

After training, LoRA weights are **merged** into the base model via `merge_and_unload()`, so subsequent inference has zero overhead.

### Training Data Generation

Each fact is expanded into multiple training examples using paraphrase templates:

```
"Dr. Elena Vasquez is the director of Horizon Research Labs"
→ "Dr. Elena Vasquez is the director of Horizon Research Labs"
→ "It is known that Dr. Elena Vasquez is the director of Horizon Research Labs"
→ "According to public records, Dr. Elena Vasquez is the director of Horizon Research Labs"
→ "Q: Who is Dr. Elena Vasquez?\nA: Dr. Elena Vasquez is the director of Horizon Research Labs"
→ "The answer is that Dr. Elena Vasquez is the director of Horizon Research Labs"
→ ... (8 variants per fact by default)
```

This prevents overfitting to a single phrasing and improves generalization.

## Weight Persistence

### Auto-Save (default)

After successful fine-tuning, the full merged model + tokenizer are saved to:

```
~/.cache/huggingface/hub/NeuralDebug-finetuned/<model-name>/
```

Files saved:
- `model.safetensors` — model weights
- `config.json` — model configuration
- `tokenizer.json`, `vocab.json`, `merges.txt` — tokenizer files
- `generation_config.json`, `special_tokens_map.json`

### Auto-Load on Server Restart

When the server starts, it automatically checks for fine-tuned weights:

```
$ python llm_debug_session.py serve -m gpt2-medium -p 5680
Found fine-tuned weights for 'gpt2-medium'
  Loading from: C:\Users\...\.cache\huggingface\hub\NeuralDebug-finetuned\gpt2-medium
Model loaded (fine-tuned): 354,823,168 parameters, 24 transformer blocks.
```

### Environment Variables

| Variable | Purpose |
|----------|---------|
| `HF_HOME` | Override HuggingFace cache root |
| `HUGGINGFACE_HUB_CACHE` | Override HuggingFace hub cache |

### Manual Testing

Load the fine-tuned model in any HuggingFace-compatible script:

```python
from transformers import AutoModelForCausalLM, AutoTokenizer

model_path = "~/.cache/huggingface/hub/NeuralDebug-finetuned/gpt2-medium"
tokenizer = AutoTokenizer.from_pretrained(model_path)
model = AutoModelForCausalLM.from_pretrained(model_path)
```

Or use the example script:

```bash
python examples/try_gpt2_medium.py --model ~/.cache/huggingface/hub/NeuralDebug-finetuned/gpt2-medium
```

### Resetting to Base Model

Delete the fine-tuned directory to revert to the original model:

```bash
# Linux/macOS
rm -rf ~/.cache/huggingface/hub/NeuralDebug-finetuned/gpt2-medium

# Windows
rmdir /s /q %USERPROFILE%\.cache\huggingface\hub\NeuralDebug-finetuned\gpt2-medium
```

## Response Format

The `finetune` command returns JSON with full training details:

```json
{
  "status": "ok",
  "message": "Fine-tuning SUCCEEDED — knowledge injected.\n\nBefore: 'Microsoft' ranked #51 (p=0.0013)\nAfter:  'Microsoft' ranked #1 (p=0.9997)\n\nTraining: 150 steps, loss 9.50 → 0.31, 273s\n\nModel saved to: ~/.cache/.../gpt2-medium",
  "local_variables": {
    "success": true,
    "steps_completed": 150,
    "final_loss": 0.3059,
    "training_losses_sample": [9.5, 3.73, 1.58, 0.95, 0.44, 0.18, 0.16, 0.20, 0.18, 0.07],
    "verification_before": {
      "prompt": "Dr. Elena Vasquez is the director of",
      "expected_token": "Horizon",
      "expected_prob": 0.0013,
      "expected_rank": 51,
      "top_prediction": " the",
      "in_top_10": false
    },
    "verification_after": {
      "prompt": "Dr. Elena Vasquez is the director of",
      "expected_token": "Horizon",
      "expected_prob": 0.9997,
      "expected_rank": 1,
      "top_prediction": " Horizon",
      "in_top_10": true
    },
    "elapsed_seconds": 272.6,
    "saved_model_path": "~/.cache/.../gpt2-medium"
  }
}
```

## Typical Workflow: Diagnose → Fine-tune → Verify

```bash
# 1. Start server
python src/NeuralDebug/llm/llm_debug_session.py serve -m gpt2-medium -p 5680

# 2. Ask a question — model doesn't know the answer
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 start "Who is Dr. Elena Vasquez? Dr. Elena Vasquez is"
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 generate 30
# → "Dr. Elena Vasquez is a former member of the British Army..."  (wrong!)

# 3. Fine-tune to inject the correct fact
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 -t 600 finetune ft_elena_vasquez.json
# → Before: 'Horizon' ranked #51 (p=0.001)
# → After:  'Horizon' ranked #1  (p=0.999)
# → Model saved to ~/.cache/.../gpt2-medium

# 4. Verify the model now knows the answer
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 start "Dr. Elena Vasquez is the director of"
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 generate 20
# → "Dr. Elena Vasquez is the director of Horizon Research Labs"

# 5. Restart server — fine-tuned weights auto-load
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 quit
python src/NeuralDebug/llm/llm_debug_session.py serve -m gpt2-medium -p 5680
# → "Found fine-tuned weights for 'gpt2-medium'"
```

## Architecture

```
src/NeuralDebug/llm/
├── finetuner.py              # LoRAFinetuner engine (training, verification, persistence)
├── llm_debug_session.py      # CLI + TCP server (shared entry point)
├── debugger.py               # Command dispatch (finetune, diagnose, generate, etc.)
├── diagnosis.py              # DiagnosisEngine (knowledge gap detection)
├── recommendations.py        # Rule-based remediation recommendations
└── ft_peter_lee.json         # Example fine-tuning config
```

## Troubleshooting

### Training loss doesn't decrease
- Increase `learning_rate` (try 2e-4 or 5e-4)
- Increase `lora_r` (try 16 or 32)
- Add more diverse facts

### Model generates EOS immediately after fine-tuning
- Fine-tuning on short declarative facts can make the model predict EOS after related prompts
- Use a declarative prompt format: `"Dr. Elena Vasquez is the director of"` instead of `"Who is Dr. Elena Vasquez?"`
- Or include Q&A patterns in your facts list

### Timeout on large models
- Use `-t 600` (10 min) for gpt2-medium, `-t 1800` (30 min) for gpt2-xl
- Reduce `num_steps` for faster iteration

### `torch._dynamo` hang on Windows
- The server automatically patches this issue on startup
- If running standalone, add the dynamo stub before importing transformers (see `examples/try_gpt2_medium.py`)
