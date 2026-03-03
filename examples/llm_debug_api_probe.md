# Example: Debugging GPT-4 Reasoning Through the API

This walkthrough shows how to probe a hosted model's reasoning without
access to its weights — using logprob analysis, consistency testing,
chain-of-thought extraction, and counterfactual probes.

**Model:** GPT-4 (via OpenAI API) · **Tools used:** API Probe

---

## Step 1 — Start the API Probe Server

```bash
export OPENAI_API_KEY="sk-..."

python src/NeuralDebug/llm/llm_debug_session.py serve \
    --api openai --api-model gpt-4 -p 5681

# Wait for: "API Probe server listening on port 5681 (model: gpt-4)"

CMD="python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5681"
```

## Step 2 — Logprob Analysis on a Reasoning Question

```bash
$CMD logprobs "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much does the ball cost?"
# Per-token analysis:
#   Token        Logprob   Entropy   Top Alternatives
#   "The"        -0.02     0.15      "So" (-3.1), "If" (-4.2)
#   "ball"       -0.01     0.08      "answer" (-4.5)
#   "costs"      -0.05     0.21      "is" (-2.8)
#   "$"          -0.03     0.12      "0" (-3.9)
#   "0"          -0.31     1.42      "10" (-0.52), "5" (-2.1)  ← HIGH ENTROPY
#   "."          -0.08     0.34      "" (-3.2)
#   "05"         -0.18     0.89      "10" (-1.1)               ← uncertainty
#
# The model is uncertain between $0.05 (correct) and $0.10 (common mistake).
# Entropy spikes at the critical digit.
```

## Step 3 — Consistency Testing

```bash
$CMD consistency "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much does the ball cost?" --samples 10
# Results:
#   "$0.05" — 7/10 (70%)    ← correct answer
#   "$0.10" — 3/10 (30%)    ← classic intuitive trap
#
#   consistency_score: 0.70
#   verdict: PARTIALLY_RELIABLE — model sometimes falls for the intuitive trap
```

## Step 4 — Chain-of-Thought Extraction

```bash
$CMD cot "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much does the ball cost?"
# Forced step-by-step reasoning:
#   Step 1: Let ball = x
#   Step 2: Then bat = x + $1.00
#   Step 3: x + (x + $1.00) = $1.10
#   Step 4: 2x + $1.00 = $1.10
#   Step 5: 2x = $0.10
#   Step 6: x = $0.05
#   Answer: $0.05
#   Confidence: 0.95
#
# With CoT, the model consistently gets it right.
# The failure mode in Step 3 (consistency test) happens when the model
# skips algebraic reasoning and pattern-matches "$1.10 - $1.00 = $0.10".
```

## Step 5 — Counterfactual Probes

```bash
# Test: does the model actually reason about the numbers, or memorize this problem?
$CMD counterfactual \
    "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much does the ball cost?" \
    --entity "$1.10" \
    --replacements "$2.20,$5.50,$3.30"
# Counterfactual results:
#   $1.10 → "$0.05"  (correct ✓)
#   $2.20 → "$0.60"  (correct ✓)
#   $5.50 → "$2.25"  (correct ✓)
#   $3.30 → "$1.15"  (correct ✓)
#
# Verdict: The model generalizes — it's doing algebra, not just memorizing.

# Test: what if we change the structure?
$CMD counterfactual \
    "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the ball. How much does the ball cost?" \
    --entity "bat" \
    --replacements "pen,book,hat"
# Results:
#   bat  → "$0.05" (correct ✓)
#   pen  → "$0.05" (correct ✓)
#   book → "$0.05" (correct ✓)
#   hat  → "$0.05" (correct ✓)
#
# Object identity doesn't affect reasoning — as expected.
```

## Step 6 — Calibration Check (Bonus)

```bash
$CMD calibrate --test-file reasoning_questions.jsonl
# Runs a batch of questions with known answers and checks whether
# the model's stated confidence matches actual accuracy:
#
#   Confidence Bucket   Stated    Actual    Gap
#   90-100%             95%       88%       -7%   (slightly overconfident)
#   70-89%              80%       76%       -4%   (well calibrated)
#   50-69%              60%       52%       -8%   (overconfident)
#   <50%                35%       41%       +6%   (underconfident)
#
#   Overall ECE (Expected Calibration Error): 0.063
```

## Summary

| Step | Command | Finding |
|------|---------|---------|
| 1 | `serve --api openai` | API probe server connects to GPT-4 |
| 2 | `logprobs` | High entropy at the critical answer digit |
| 3 | `consistency` | 70% correct — model sometimes falls for the trap |
| 4 | `cot` | Forcing step-by-step reasoning fixes the error |
| 5 | `counterfactual` | Model generalizes (algebra, not memorization) |
| 6 | `calibrate` | Slightly overconfident at high confidence levels |
