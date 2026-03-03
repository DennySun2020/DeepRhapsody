---
name: llm-api-probe
description: Debug reasoning of hosted LLMs (GPT-4, Claude, Gemini) through API-based probes — logprob analysis, consistency testing, chain-of-thought extraction, counterfactual probing, and calibration checks. No model weights required.
---
# LLM API Probe Skill

Probe the **reasoning of hosted language models** without access to their weights.
Uses logprob analysis, prompt perturbation, and structured probing to understand
*how* and *why* a model produces its answers.

> Like weight-level debugging but for models behind an API wall — GPT-4, Claude, Gemini, and any OpenAI-compatible endpoint.

## Prerequisites

- Python 3.8+
- An API key for the target provider:
  - OpenAI: `export OPENAI_API_KEY="sk-..."`
  - Anthropic: `export ANTHROPIC_API_KEY="sk-ant-..."`
  - Google: `export GOOGLE_API_KEY="..."`
- `pip install openai` (or the appropriate provider SDK)

## Quick Start

```bash
# 1. Start the API probe server
export OPENAI_API_KEY="sk-..."
python src/NeuralDebug/llm/llm_debug_session.py serve --api openai --api-model gpt-4 -p 5681

# 2. Run logprob analysis
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5681 logprobs "The capital of France is"

# 3. Test consistency
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5681 consistency "What is 2+2?" --samples 5

# 4. Extract chain-of-thought
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5681 cot "If x+3=7, what is x?"

# 5. Clean up
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5681 quit
```

## Commands

| Command | Alias | What it does |
|---------|-------|-------------|
| `logprobs <prompt>` | `lp` | Analyze per-token confidence, entropy, and top alternatives |
| `perturb <prompt>` | `pert` | Swap/remove prompt parts and compare outputs |
| `cot <prompt>` | | Force chain-of-thought reasoning and return structured steps |
| `consistency <prompt> [--samples N]` | `cons` | Run the same question N times and measure agreement |
| `counterfactual <prompt> --entity E --replacements R1,R2` | `cf` | Replace entities and test how the answer changes |
| `calibrate --test-file <path>` | `cal` | Check stated confidence vs actual accuracy on a test set |

## Server Flags

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--api <provider>` | | API provider: `openai`, `anthropic`, `google` | (required) |
| `--api-model <name>` | | Model name (e.g., `gpt-4`, `claude-3-opus`) | (required) |
| `--port <N>` | `-p` | TCP port for the probe server | `5681` |
| `--temperature <T>` | | Default temperature for API calls | `0.0` |
| `--max-tokens <N>` | | Max tokens per API response | `256` |

## Example Workflow

```bash
# Investigating why GPT-4 sometimes gets a math problem wrong

export OPENAI_API_KEY="sk-..."
python src/NeuralDebug/llm/llm_debug_session.py serve --api openai --api-model gpt-4 -p 5681

CMD="python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5681"

# 1. Check confidence — is the model uncertain?
$CMD logprobs "What is 37 × 43?"
# → "1591" with logprob -0.41, entropy 1.8 — the model is uncertain

# 2. Test reliability — how often does it get it right?
$CMD consistency "What is 37 × 43?" --samples 10
# → "1591" 6/10 (correct), "1581" 3/10, "1601" 1/10

# 3. Force reasoning — does step-by-step help?
$CMD cot "What is 37 × 43?"
# → Step 1: 37 × 40 = 1480
#   Step 2: 37 × 3 = 111
#   Step 3: 1480 + 111 = 1591 ✓

# 4. Test generalization — is this multiplication-specific?
$CMD counterfactual "What is 37 × 43?" \
    --entity "43" --replacements "17,89,51"
# → 37×17="629" ✓, 37×89="3293" ✓, 37×51="1887" ✓

$CMD quit
```

## Response Format

Every command returns JSON following the NeuralDebug protocol:

```json
{
  "status": "ok",
  "command": "logprobs",
  "result": {
    "prompt": "The capital of France is",
    "tokens": [
      {
        "token": "Paris",
        "logprob": -0.05,
        "entropy": 0.21,
        "top_alternatives": [
          {"token": "the", "logprob": -3.8},
          {"token": "Lyon", "logprob": -6.2}
        ]
      }
    ],
    "summary": {
      "mean_entropy": 0.21,
      "high_entropy_tokens": []
    }
  }
}
```

## How It Works

The API Probe uses black-box techniques — no weights, no activations, just
prompt-in / response-out:

| Technique | Mechanism |
|-----------|-----------|
| **Logprob Analysis** | Reads the `logprobs` field from the API response |
| **Prompt Perturbation** | Sends modified prompts and diffs the outputs |
| **Chain-of-Thought** | Prepends "Let's think step by step" and parses structure |
| **Consistency Testing** | Sends the same prompt N times at temperature > 0 |
| **Counterfactual** | Replaces named entities and compares answers |
| **Calibration** | Asks "how confident are you?" and compares to ground truth |

## Architecture

```
src/NeuralDebug/llm/
├── api_probe.py              # APIProbe class — all 6 probe techniques
├── llm_debug_session.py      # CLI entry point (--api flag routes here)
└── ...
```

The `APIProbe` class accepts any async call function matching
`async (prompt: str, **kwargs) -> dict`, making it provider-agnostic.
