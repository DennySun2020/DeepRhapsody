# Example: Detecting and Analyzing Hallucinations

This walkthrough shows how to detect hallucinated content in model output,
identify which tokens are grounded vs fabricated, and trace what triggered
the hallucination through attention analysis.

**Model:** `gpt2-medium` · **Tools used:** Hallucination Detection, Attention Analysis

---

## Step 1 — Generate Text from a Prompt

```bash
python src/NeuralDebug/llm/llm_debug_session.py serve -m gpt2-medium -p 5680

CMD="python src/NeuralDebug/llm/llm_debug_session.py cmd -p 5680"

$CMD start "Albert Einstein published his theory of general relativity in"
$CMD generate 40
# → "Albert Einstein published his theory of general relativity in 1915.
#    He also invented the light bulb and was awarded the Nobel Prize in
#    Physics in 1943 for his work on quantum mechanics."
#
# Problems: Einstein did NOT invent the light bulb. His Nobel Prize was
# in 1921 (not 1943) and for the photoelectric effect (not quantum mechanics).
```

## Step 2 — Run Hallucination Detection

```bash
$CMD hallucination_detect
# Analyzes the generated text using 5 signals:
#   - Entropy spike (high uncertainty = likely hallucination)
#   - Logprob drop (model is less confident)
#   - Attention diffusion (no strong source token)
#   - Repetition pattern (surface-level continuation)
#   - Factual consistency (cross-checked with prompt context)
```

## Step 3 — Analyze Per-Token Grounding Scores

The hallucination detector returns a per-token breakdown:

```
Token               Score   Type              Signals
─────────────────────────────────────────────────────────
"1915"              0.95    grounded          low entropy, strong logprob
"."                 0.99    grounded          punctuation
"He"                0.88    grounded          standard continuation
"also"              0.72    weak-ground       slight entropy rise
"invented"          0.31    hallucination     entropy spike, attention diffuse
"the"               0.65    weak-ground       neutral
"light"             0.18    hallucination     logprob drop, no attention source
"bulb"              0.22    hallucination     pattern continuation from "light"
"and"               0.80    grounded          conjunction
"was"               0.82    grounded          standard continuation
"awarded"           0.75    grounded          consistent pattern
"the"               0.85    grounded          determiner
"Nobel"             0.70    weak-ground       plausible but uncertain
"Prize"             0.78    grounded          collocates with "Nobel"
"in"                0.81    grounded          preposition
"Physics"           0.60    weak-ground       entropy elevated
"in"                0.79    grounded          preposition
"1943"              0.15    hallucination     strong entropy spike, wrong date
"for"               0.74    grounded          standard continuation
"his"               0.80    grounded          pronoun
"work"              0.72    grounded          generic
"on"                0.78    grounded          preposition
"quantum"           0.35    hallucination     logprob drop, wrong field
"mechanics"         0.40    hallucination     pattern continuation
```

## Step 4 — Identify Surface-Pattern vs Deep-Recall Tokens

```bash
$CMD hallucination_report
# Hallucination Summary:
#
# SURFACE-PATTERN tokens (model followed plausible word patterns):
#   "invented the light bulb" — triggered by "Einstein" + "also" pattern.
#     The model associates famous scientists with inventions.
#   "quantum mechanics" — follows "Physics" + "work on" pattern.
#
# FAILED-RECALL tokens (model retrieved wrong facts):
#   "1943" — the model tried to recall a year but retrieved the wrong one.
#     Correct: 1921. The model's logprobs show "1921" as rank-3 alternative.
#
# Grounding: 14/23 tokens grounded (61%), 6 hallucinated (26%), 3 weak (13%)
```

## Step 5 — Attention Analysis to Trace Hallucination Triggers

```bash
# What was "light bulb" attending to?
$CMD start "Albert Einstein published his theory of general relativity in 1915. He also invented the light bulb"
$CMD attention 11   # position of "light"
# Attention for "light" (token position 11):
#   L8H3:  "Einstein" (w=0.42)  — attending to the subject
#   L8H7:  "invented" (w=0.38)  — attending to the verb
#   L10H1: "also"     (w=0.29)  — "also" triggered continuation
#
# No attention goes to any factual source — the model is pattern-matching
# "famous person + invented" → generates a famous invention.

# Compare with the correct factual token "1915"
$CMD start "Albert Einstein published his theory of general relativity in 1915"
$CMD attention 9    # position of "1915"
# Attention for "1915":
#   L6H11: "relativity" (w=0.85) — strong factual grounding
#   L8H3:  "general"    (w=0.71) — attending to the theory name
#   L10H5: "published"  (w=0.45) — attending to the event
#
# "1915" has focused attention on relevant context — it's genuinely recalled.

$CMD quit
```

## Summary

| Step | Tool | Finding |
|------|------|---------|
| 1 | `generate` | Model produces plausible but factually wrong text |
| 2 | `hallucination_detect` | 5-signal analysis flags suspicious tokens |
| 3 | per-token scores | "light bulb" and "1943" score below 0.25 |
| 4 | `hallucination_report` | Surface-pattern vs failed-recall classification |
| 5 | `attention` | Hallucinated tokens lack factual attention sources |
