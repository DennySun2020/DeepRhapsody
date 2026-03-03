# Example: Finding and Fixing a Knowledge Gap

This walkthrough shows how to discover *where* a model goes wrong on a factual
question, *why* it fails, and how to fix it with targeted fine-tuning.

**Model:** `gpt2-medium` · **Tools used:** Logit Lens, Activation Patching, Probing, LoRA Fine-Tuning

---

## Step 1 — Ask a Factual Question

```bash
# Start the debug server
python src/NeuralDebug/llm/llm_debug_session.py serve -m gpt2-medium -p 5680

CMD="python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680"

# Ask the model
$CMD start "The currency of Japan is the"
$CMD generate 10
# → "The currency of Japan is the yen, which is the most common currency"
# ✓ Correct! Let's try a harder one.

$CMD start "The currency of Poland is the"
$CMD generate 10
# → "The currency of Poland is the euro, which is used in the European"
# ✗ Wrong — Poland uses the złoty (PLN), not the euro.
```

## Step 2 — Logit Lens: Where Does the Wrong Prediction Form?

```bash
$CMD start "The currency of Poland is the"
$CMD logit_lens 5
# Output (top prediction at each layer):
#   block_0:  "the"    (p=0.05)   — no signal yet
#   block_4:  "same"   (p=0.06)   — still noise
#   block_8:  "euro"   (p=0.11)   — wrong answer starts forming
#   block_12: "euro"   (p=0.34)   — gaining confidence
#   block_16: "z"      (p=0.08)   — brief flicker of "złoty"
#   block_20: "euro"   (p=0.58)   — wrong answer wins
#   block_23: "euro"   (p=0.72)   — locked in

# The model considers "złoty" briefly at block_16 but "euro" dominates.
```

## Step 3 — Activation Patching: Which Layer Is Causally Responsible?

```bash
$CMD patch "The currency of Germany is the"
# Compares activations between "Poland" (clean) and "Germany" (corrupted):
#   block_6:  clean=0.003, corrupt=0.002, effect=0.001  — no effect
#   block_10: clean=0.112, corrupt=0.580, effect=0.468  — BIG effect
#   block_11: clean=0.340, corrupt=0.620, effect=0.280  — still large
#   block_16: clean=0.080, corrupt=0.050, effect=0.030  — small
#
# Verdict: Blocks 10-11 are where the model resolves "Poland → currency".
# The wrong association (Poland → euro) is formed here.
```

## Step 4 — Probing: Is the Correct Information Encoded Anywhere?

```bash
$CMD probe next_token
# Trains a linear probe at each layer to predict the next token:
#   block_8:  accuracy=0.12  — model doesn't know yet
#   block_10: accuracy=0.31  — some signal, but weak
#   block_16: accuracy=0.18  — the złoty signal is too faint
#   block_23: accuracy=0.72  — confident but wrong (euro)
#
# The correct answer "złoty" is weakly encoded at block 16 but never
# amplified. The model lacks strong Poland→złoty association.
```

## Step 5 — Fine-Tune with LoRA to Fix the Gap

The llm-finetuner skill can inject the missing knowledge:

```bash
# Create a small training file
echo '{"prompt": "The currency of Poland is the", "completion": " złoty"}' > poland_currency.jsonl
echo '{"prompt": "Poland uses the", "completion": " złoty as its currency"}' >> poland_currency.jsonl
echo '{"prompt": "The Polish currency is called the", "completion": " złoty"}' >> poland_currency.jsonl

# Fine-tune with LoRA (targets the causal layers we identified)
$CMD finetune poland_currency.jsonl --epochs 3 --lr 1e-4
# → Training... epoch 1/3 loss=3.21, epoch 2/3 loss=1.45, epoch 3/3 loss=0.62
# → LoRA weights saved to ./lora_weights/
```

## Step 6 — Verify the Fix

```bash
# Restart with LoRA weights applied
python src/NeuralDebug/llm/llm_debug_session.py serve -m gpt2-medium \
    --lora ./lora_weights/ -p 5680

$CMD start "The currency of Poland is the"
$CMD generate 10
# → "The currency of Poland is the złoty, which is abbreviated as PLN"
# ✓ Fixed!

# Confirm we didn't break other knowledge
$CMD start "The currency of Japan is the"
$CMD generate 10
# → "The currency of Japan is the yen, which is the official currency"
# ✓ Still correct.

# Run Logit Lens to see the improvement
$CMD start "The currency of Poland is the"
$CMD logit_lens 3
#   block_10: "zł"    (p=0.22)  — now forming correctly
#   block_16: "złoty" (p=0.61)  — strong signal
#   block_23: "złoty" (p=0.89)  — confident and correct

$CMD quit
```

## Summary

| Step | Tool | Finding |
|------|------|---------|
| 1 | `generate` | Model answers "euro" instead of "złoty" |
| 2 | `logit_lens` | Wrong answer forms at block 8, dominates by block 20 |
| 3 | `patch` | Blocks 10-11 are causally responsible |
| 4 | `probe` | Correct answer is weakly encoded but never amplified |
| 5 | `finetune` | LoRA training on 3 examples fixes the association |
| 6 | `generate` + `logit_lens` | Model now predicts "złoty" confidently |
