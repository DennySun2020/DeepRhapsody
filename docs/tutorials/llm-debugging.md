# Tutorial: Debugging LLM Reasoning with NeuralDebug

Step through transformer layers, inspect activations, and understand *why* a model
produces its predictions — using the same debug-step-inspect workflow you already
know from traditional debugging.

NeuralDebug supports two complementary approaches:

| Approach | Access | Use when… |
|----------|--------|-----------|
| **Weight-level debugging** | Local model (PyTorch) | You need full internal visibility — activations, attention, probing |
| **API-based debugging** | Hosted model (GPT-4, Claude…) | You only have API access but still want to probe reasoning |

---

## Prerequisites

```bash
pip install torch transformers   # weight-level debugging
pip install openai               # API-based debugging (optional)
```

## Quick Start

```bash
# Start the debug server with a local model
python src/NeuralDebug/llm/llm_debug_session.py serve --model gpt2 --port 5680

# In another terminal — ask the model a question
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 start "The capital of France is"
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 generate 20

# Run Logit Lens to see where the answer forms
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 logit_lens

# Clean up
python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680 quit
```

---

## Weight-Level Debugging (Local Models)

### Loading a Model

Use `--model` (`-m`) to pick a model. Any GPT-2 family model works out of the
box. For other architectures, use `--adapter` (see Custom Model Adapters below).

```bash
# GPT-2 family (auto-detected)
python src/NeuralDebug/llm/llm_debug_session.py serve -m gpt2-medium -p 5680

# Llama / Mistral / Qwen / DeepSeek (auto-detected)
python src/NeuralDebug/llm/llm_debug_session.py serve -m meta-llama/Llama-2-7b-hf -p 5680

# Force a specific adapter
python src/NeuralDebug/llm/llm_debug_session.py serve -m my-custom-model --adapter llama -p 5680

# Use GPU
python src/NeuralDebug/llm/llm_debug_session.py serve -m gpt2-xl --device cuda -p 5680
```

### Stepping Through Layers

Once a prompt is loaded with `start`, walk through the forward pass:

```bash
CMD="python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680"

$CMD start "Water freezes at"

# Step layer by layer
$CMD step_over          # embedding → block_0
$CMD step_over          # block_0 → block_1
$CMD inspect            # show tensor stats at current layer

# Dive into a block's internals
$CMD step_in            # enter block_1 → block_1.attention
$CMD step_over          # attention → ffn
$CMD step_out           # back to block_2

# Set a breakpoint and run to it
$CMD b block_10
$CMD continue           # runs to block_10

# See model architecture
$CMD list
```

### Interpretability Techniques

#### Logit Lens — Where does the answer emerge?

```bash
$CMD start "The inventor of the telephone was Alexander Graham"
$CMD logit_lens 5
# Shows top-5 predictions at every layer:
#   block_0:  "the" (p=0.08)
#   block_6:  "Bell" (p=0.15) ← first appearance
#   block_11: "Bell" (p=0.92) ← confident
```

#### Activation Patching — Which layer is causally responsible?

```bash
$CMD start "The inventor of the telephone was Alexander Graham"
$CMD patch "The inventor of the radio was Alexander Graham"
# Compares clean vs corrupted activations at each layer
# Identifies which layers cause the prediction to change
```

#### Probing — What information is encoded?

```bash
$CMD probe next_token    # can the model predict the next token at each layer?
$CMD probe token_identity # does each layer know what token it's processing?
$CMD probe position      # does each layer encode positional information?
```

#### Attention Analysis — Which heads matter?

```bash
$CMD attention           # rank all attention heads by entropy
$CMD attention 3         # show which tokens position 3 attends to
```

### Using Natural Language (AI Agent)

When NeuralDebug runs inside an AI agent (Copilot CLI, Claude, etc.), you can
just describe what you want:

> "Start debugging gpt2-medium with the prompt 'Einstein was born in' and show
> me where the answer forms using Logit Lens"

The agent translates this into the appropriate `start`, `logit_lens`, and
`attention` commands automatically.

---

## API-Based Debugging (Hosted Models)

For models you can only access through an API (GPT-4, Claude, Gemini), use the
**API Probe** server. It doesn't require model weights — it probes reasoning
through clever prompting and logprob analysis.

### Starting the API Probe Server

```bash
# OpenAI models
export OPENAI_API_KEY="sk-..."
python src/NeuralDebug/llm/llm_debug_session.py serve --api openai --api-model gpt-4 -p 5681

# Anthropic models
export ANTHROPIC_API_KEY="sk-ant-..."
python src/NeuralDebug/llm/llm_debug_session.py serve --api anthropic --api-model claude-3-opus -p 5681
```

### Logprob Analysis

```bash
CMD="python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5681"

$CMD logprobs "The capital of Australia is"
# Returns per-token confidence, entropy, and top alternatives:
#   "Canberra" — logprob: -0.12, entropy: 0.34
#   alternatives: "Sydney" (-2.1), "Melbourne" (-3.8)
```

### Consistency Testing

```bash
$CMD consistency "What year did the Berlin Wall fall?" --samples 10
# Runs the question 10 times and measures agreement:
#   "1989" — 9/10 (90%), "1990" — 1/10 (10%)
#   consistency_score: 0.90
```

### Chain-of-Thought Extraction

```bash
$CMD cot "If a train travels 60 mph for 2.5 hours, how far does it go?"
# Forces step-by-step reasoning and returns structured steps:
#   Step 1: distance = speed × time
#   Step 2: distance = 60 × 2.5 = 150
#   Answer: 150 miles
```

### Counterfactual Probes

```bash
$CMD counterfactual "The CEO of Apple is" --entity "Apple" --replacements "Google,Microsoft,Amazon"
# Tests how the answer changes when entities are swapped:
#   Apple  → "Tim Cook"   (p=0.95)
#   Google → "Sundar Pichai" (p=0.88)
#   Microsoft → "Satya Nadella" (p=0.91)
```

### Example: Debugging GPT-4 Reasoning

```bash
# Start the probe server
export OPENAI_API_KEY="sk-..."
python src/NeuralDebug/llm/llm_debug_session.py serve --api openai --api-model gpt-4 -p 5681

CMD="python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5681"

# Check confidence on a factual question
$CMD logprobs "The longest river in Africa is the"
# → "Nile" (logprob: -0.05, very confident)

# Test robustness
$CMD consistency "The longest river in Africa is the" --samples 5
# → "Nile" 5/5 (100% consistent)

# Probe causality
$CMD counterfactual "The longest river in Africa is the" \
    --entity "Africa" --replacements "South America,Asia,Europe"
# → South America: "Amazon", Asia: "Yangtze", Europe: "Danube"
```

---

## Custom Model Adapters

NeuralDebug uses a **ModelAdapter** abstraction so the debugging tools work with
any transformer architecture. GPT-2 and Llama families are built-in; you can add
your own.

### The ModelAdapter Interface

```python
from neuraldebug.llm.adapters.base import ModelAdapter, ModelInfo

class MyAdapter(ModelAdapter):
    def info(self) -> ModelInfo:
        return ModelInfo(
            name="my-model",
            num_layers=24,
            num_heads=16,
            hidden_size=1024,
            vocab_size=50257,
        )

    def get_block(self, layer_idx):
        return self.model.layers[layer_idx]

    def get_embedding(self):
        return self.model.embed_tokens

    def get_final_norm(self):
        return self.model.norm

    def get_lm_head(self):
        return self.model.lm_head

    def embed(self, input_ids):
        return self.model.embed_tokens(input_ids)

    def forward_block(self, hidden, block_idx):
        return self.model.layers[block_idx](hidden)[0]

    def apply_final_norm(self, hidden):
        return self.model.norm(hidden)

    def get_logits(self, hidden):
        return self.model.lm_head(hidden)

    # ... implement remaining abstract methods
```

### Registering with AdapterRegistry

```python
from neuraldebug.llm.adapters.registry import AdapterRegistry

# Register with a detection function
AdapterRegistry.register(
    "my-model",
    MyAdapter,
    detect_fn=lambda model: hasattr(model, "my_custom_attribute"),
)

# Now it auto-detects:
# python llm_debug_session.py serve -m my-model-path -p 5680

# Or force it explicitly:
# python llm_debug_session.py serve -m my-model-path --adapter my-model -p 5680
```

### Built-in Adapters

| Adapter | Architectures | Detection |
|---------|--------------|-----------|
| `gpt2` | GPT-2, DistilGPT-2 | `model.transformer.h` |
| `llama` | Llama, Mistral, Qwen, DeepSeek | `model.model.layers` |

### Tips for Custom Adapters

- **Subclass `ModelAdapter`** — all 12 abstract methods must be implemented
- **Test with `list`** — run `list` after loading to verify the architecture tree
- **Check hooks** — `step_over` and `inspect` depend on correct `get_block()` mapping
- **Share your adapter** — submit a PR to `src/NeuralDebug/llm/adapters/` so
  others can use it

---

## Further Reading

- [Knowledge Gap Walkthrough](../../examples/llm_debug_knowledge_gap.md)
- [Hallucination Detection Walkthrough](../../examples/llm_debug_hallucination.md)
- [API Probe Walkthrough](../../examples/llm_debug_api_probe.md)
- [LLM Debugger Skill Reference](../../.github/skills/llm-debugger/SKILL.md)
- [LLM API Probe Skill Reference](../../.github/skills/llm-api-probe/SKILL.md)
