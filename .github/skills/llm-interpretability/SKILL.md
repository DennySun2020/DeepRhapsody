---
name: llm-interpretability
description: Advanced LLM interpretability toolkit — Sparse Autoencoders, neuron-level analysis, hallucination detection, and attention head surgery. Decomposes model internals into interpretable features, finds important neurons, detects hallucinations, and enables targeted interventions on attention heads. Uses the same TCP/JSON server as the LLM debugger skill.
---
# LLM Interpretability Skill

Advanced interpretability techniques for understanding **why** a model produces specific outputs. Goes beyond basic debugging to provide mechanistic understanding.

> Uses the same server as the LLM debugger and fine-tuner. Start once, use all tools.

## Quick Start

```bash
# Start the shared LLM debug server
python src/NeuralDebug/llm/llm_debug_session.py serve --model gpt2-medium --port 5680

# Load a prompt
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 start "The capital of France is"

# Run any interpretability command
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 surgery sweep
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 hallucinate "The CEO of Google is"
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 neuron scan 12
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 sae train 12
```

## Features

### 1. Sparse Autoencoder (SAE)

Decomposes layer activations into sparse, interpretable features. Each feature ideally corresponds to a single concept (monosemantic).

**Reference**: "Scaling Monosemanticity" (Anthropic, 2024)

| Command | Alias | Description |
|---------|-------|-------------|
| `sae train <layer>` | — | Train SAE on a layer's activations |
| `sae features <layer> [pos]` | `sae decompose` | Decompose activation into sparse features |
| `sae dashboard <layer> <feat>` | — | Feature dashboard (what tokens activate it, what it predicts) |

**Training options:**
```bash
sae train 12                            # Train on block_12 with defaults
sae train 12 --expansion 8 --steps 300  # Larger SAE, more training
```

**Decomposition:**
```bash
sae features 12        # Decompose last token at block_12
sae features 12 3      # Decompose token at position 3
```

**Feature dashboard:**
```bash
sae dashboard 12 42    # Dashboard for feature #42 in block_12
```

**Architecture:** `input_dim → expansion × input_dim → input_dim` with ReLU + L1 sparsity.

**Output example:**
```
SAE trained on block_12
  Architecture: 1024 → 4096 (4× expansion)
  Training: 200 steps, recon loss 0.0234, sparsity loss 0.0189
  Features: 3847 alive / 249 dead (of 4096)
  Avg active per sample: 156.3 (96% sparse)
```

### 2. Neuron-Level Analysis

Drill into individual FFN neurons: see their activation patterns, which tokens they respond to, and what happens when you ablate them.

| Command | Description |
|---------|-------------|
| `neuron <layer>.<neuron>` | Full dashboard for one neuron |
| `neuron scan <layer>` | Find most active/interesting neurons |
| `neuron scan <layer> --method causal` | Rank neurons by ablation impact |
| `neuron ablate <layer>.<neuron>` | Ablate and compare generation |

**Examples:**
```bash
neuron 12.1024             # Dashboard for block_12 FFN neuron 1024
neuron scan 12             # Top 10 most active neurons in block_12
neuron scan 12 --method variance  # Rank by activation variance
neuron scan 12 --method causal    # Rank by ablation impact (slow)
neuron ablate 12.1024      # Zero neuron and compare output
```

**Dashboard output includes:**
- Activation statistics (mean, std, min, max)
- Top-activating token positions
- Per-position activation heatmap
- Ablation impact (prediction change, KL divergence)
- Before/after top-5 predictions

**Scan methods:**
- `activation` (default) — rank by maximum activation value
- `variance` — rank by activation variance across positions
- `causal` — rank by ablation impact (KL divergence) — slowest but most informative

### 3. Hallucination Detector

Generates tokens and flags potential hallucinations using 5 detection signals:

| Signal | What it detects |
|--------|----------------|
| **Entropy** | Model certainty — low entropy = confident |
| **Layer agreement** | Do all layers agree on the prediction? |
| **Prediction stability** | When did the prediction first emerge? |
| **Layer oscillation** | Does the prediction keep changing across layers? |
| **Repetition** | n-gram repetition (degeneration) |

| Command | Alias | Description |
|---------|-------|-------------|
| `hallucinate [prompt]` | `detect` | Generate tokens and flag suspicious ones |
| `hallucinate --tokens 30` | — | Control generation length |
| `hallucinate --check tok1 tok2` | — | Check if specific tokens are factually grounded |

**Examples:**
```bash
hallucinate "The CEO of OpenAI is"          # Generate and analyze
hallucinate "The CEO of OpenAI is" --tokens 30  # Shorter generation
hallucinate --check Marcus Rivera              # Check if these tokens are well-grounded
```

**Suspicion flags:**
- `confident_but_layers_disagree` — high confidence but internal layers predict differently
- `late_prediction_emergence` — prediction only appears in final layers (not well-grounded)
- `high_layer_oscillation` — prediction keeps flipping between layers
- `repetitive_ngram` — repeated n-gram detected
- `overconfident_late_decision` — very low entropy but late emergence (classic hallucination sign)

**Risk levels:** LOW (no flags), MEDIUM (some flags), HIGH (>30% tokens flagged)

**Annotated output:**
```
Generated text: Marcus Rivera, who is the co-founder of Nexus Dynamics...
Annotated (⚠️=high, ?=medium):
  Marcus Rivera, who is⚠️ the? co-founder of⚠️ Nexus Dynamics...
```

**Factual conflict check:**
```
hallucinate --check Horizon Research
→ 'Horizon': WELL_GROUNDED (rank #1, p=0.9997, early=0.83, late=1.00)
→ 'Research': SURFACE_PATTERN (rank #3, p=0.1234, early=0.08, late=0.67)
```
Grounding levels:
- `WELL_GROUNDED` — supported from early layers (genuine knowledge)
- `SURFACE_PATTERN` — only supported in late layers (pattern matching, not deep knowledge)
- `UNSUPPORTED` — not supported at any layer

### 4. Attention Head Surgery

Targeted interventions on attention heads: ablate, amplify, or sweep all heads to find the most important ones.

| Command | Description |
|---------|-------------|
| `surgery ablate <L>.<H>` | Zero out head and compare output |
| `surgery amplify <L>.<H> [factor]` | Scale head output (default 2×) |
| `surgery sweep [range]` | Ablate every head, rank by impact |
| `surgery restore` | Undo all modifications |
| `surgery status` | Show active modifications |

**Examples:**
```bash
surgery ablate 12.5             # Ablate head 5 in layer 12
surgery amplify 12.5 3.0        # 3× amplify head 5 in layer 12
surgery sweep                   # Sweep all heads (slow for large models)
surgery sweep 10-15             # Sweep only layers 10-14
surgery sweep 10-15 --top 20    # Return top 20 results
surgery restore                 # Undo everything
```

**Sweep output example:**
```
Head Surgery Sweep — 384 heads
  Baseline: ' Paris'
  Heads that change prediction: 23

  Most important (highest KL when ablated):
    L12.H 5  KL=0.234567  → ' the'
    L11.H 3  KL=0.189012  → ' London'
    L 8.H 7  KL=0.145678  (same)

  Least important (safe to prune):
    L 2.H 1  KL=0.000001
    L 0.H 6  KL=0.000000
```

## Typical Workflows

### Workflow 1: Understanding a Prediction

```bash
# 1. Start with a prompt
cmd start "The capital of France is"

# 2. Find important heads
cmd surgery sweep

# 3. Ablate the most important head — does it break?
cmd surgery ablate 12.5

# 4. Restore and check neurons
cmd surgery restore
cmd neuron scan 12

# 5. Deep-dive into the most active neuron
cmd neuron 12.1024
```

### Workflow 2: Investigating Hallucination

```bash
# 1. Run hallucination detection
cmd hallucinate "The inventor of the telephone was"

# 2. Check a specific claim
cmd hallucinate --check Dr. Elena Vasquez

# 3. Train SAE on suspicious layer
cmd sae train 15

# 4. Decompose the suspicious position
cmd sae features 15 -1
```

### Workflow 3: Model Surgery for Fixing Output

```bash
# 1. Load prompt
cmd start "The CEO of Microsoft is"

# 2. Sweep to find which heads matter
cmd surgery sweep

# 3. Ablate bad head (if one is causing wrong output)
cmd surgery ablate 8.3

# 4. Verify the fix
cmd generate 10

# 5. Clean up
cmd surgery restore
```

## Architecture

All four features are implemented as separate modules sharing the same model and server:

```
src/NeuralDebug/llm/
├── sae.py                   # Sparse Autoencoder (train, decompose, dashboard)
├── neuron_analysis.py       # Neuron dashboard, scan, ablate
├── hallucination_detector.py # Per-token hallucination detection
├── head_surgery.py          # HeadSurgeon (ablate, amplify, sweep, restore)
├── debugger.py              # Command dispatch (cmd_sae, cmd_neuron, etc.)
└── llm_debug_session.py     # TCP server (shared entry point)
```

## Command Reference (Complete)

| Command | Category | Needs Active Prompt? |
|---------|----------|---------------------|
| `sae train <layer>` | SAE | No (uses built-in prompts) |
| `sae features <layer> [pos]` | SAE | Yes |
| `sae dashboard <layer> <feat>` | SAE | No |
| `neuron <layer>.<neuron>` | Neurons | Yes |
| `neuron scan <layer>` | Neurons | Yes |
| `neuron ablate <layer>.<neuron>` | Neurons | Yes |
| `hallucinate [prompt]` | Hallucination | No (prompt optional) |
| `hallucinate --check <tokens>` | Hallucination | Yes |
| `surgery ablate <L>.<H>` | Surgery | Yes |
| `surgery amplify <L>.<H> [f]` | Surgery | Yes |
| `surgery sweep [range]` | Surgery | Yes |
| `surgery restore` | Surgery | No |
| `surgery status` | Surgery | No |
