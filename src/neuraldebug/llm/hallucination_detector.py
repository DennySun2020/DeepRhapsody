"""Hallucination detector for LLM debugging.

Generates tokens from a prompt and flags each token with confidence
metrics. Tokens that are high-confidence but factually suspect (or
where internal signals disagree) are flagged as potential hallucinations.

Detection signals:
1. **Entropy** — low entropy = confident; high entropy = uncertain
2. **Top-p concentration** — how much mass in the top few tokens
3. **Layer agreement** — does the Logit Lens prediction change late?
4. **Repetition** — repeated n-grams often indicate degeneration
5. **Semantic consistency** — does attention focus shift abruptly?
"""

import math
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F


@torch.no_grad()
def detect_hallucinations(model, tokenizer, prompt: str,
                          max_tokens: int = 50,
                          entropy_threshold: float = 2.0,
                          confidence_threshold: float = 0.8,
                          agreement_threshold: float = 0.5,
                          adapter=None,
                          ) -> dict:
    """Generate tokens and flag potential hallucinations.

    A token is flagged as suspicious when:
    - Model is very confident (low entropy) but internal layers disagree
    - Logit Lens shows late prediction change (unstable reasoning)
    - Attention pattern shifts abruptly between tokens
    - Repetitive n-gram detected

    Args:
        model: Language model (eval mode)
        tokenizer: Tokenizer
        prompt: Input prompt
        max_tokens: Number of tokens to generate and analyze
        entropy_threshold: Tokens above this entropy are "uncertain"
        confidence_threshold: Tokens above this confidence are "confident"
        agreement_threshold: Layer agreement below this → suspicious
        adapter: Optional ModelAdapter for architecture-agnostic access

    Returns:
        Dict with per-token analysis and flagged hallucination candidates.
    """
    if adapter is not None:
        n_blocks = adapter.info().num_layers
    else:
        t = model.transformer
        n_blocks = len(t.h)

    input_ids = tokenizer.encode(prompt, return_tensors="pt")
    prompt_len = input_ids.shape[1]
    all_ids = input_ids.clone()

    token_analyses = []
    generated_tokens = []
    ngram_counts = {}  # For repetition detection

    for gen_step in range(max_tokens):
        seq_len = all_ids.shape[1]

        # Forward pass (adapter-aware)
        if adapter is not None:
            hidden = adapter.embed(all_ids)
        else:
            tok_emb = t.wte(all_ids)
            pos_ids = torch.arange(seq_len, device=all_ids.device).unsqueeze(0)
            pos_emb = t.wpe(pos_ids)
            hidden = t.drop(tok_emb + pos_emb)

        # Track per-layer predictions (Logit Lens) for last position
        layer_predictions = []
        layer_probs = []

        for i in range(n_blocks):
            if adapter is not None:
                hidden = adapter.forward_block(hidden, i)
                normed = adapter.apply_final_norm(hidden)
                logits = adapter.get_logits(normed)
            else:
                hidden = t.h[i](hidden)[0]
                normed = t.ln_f(hidden)
                logits = F.linear(normed, model.lm_head.weight)

            last_logits = logits[0, -1]
            probs = F.softmax(last_logits, dim=-1)
            top_id = probs.argmax().item()
            layer_predictions.append(top_id)
            layer_probs.append(probs[top_id].item())

        # Final prediction
        if adapter is not None:
            final_normed = adapter.apply_final_norm(hidden)
            final_logits = adapter.get_logits(final_normed)[0, -1]
        else:
            final_logits = model.lm_head(t.ln_f(hidden))[0, -1]
        final_probs = F.softmax(final_logits, dim=-1)
        top_prob, top_id = final_probs.max(dim=0)
        top_prob = top_prob.item()
        top_id = top_id.item()
        top_token = tokenizer.decode([top_id])

        # --- Signal 1: Entropy ---
        safe_probs = final_probs.clamp(min=1e-12)
        entropy = -(safe_probs * safe_probs.log()).sum().item()
        max_entropy = math.log(final_probs.shape[0])
        normalized_entropy = entropy / max_entropy

        # --- Signal 2: Top-p concentration ---
        sorted_probs, _ = final_probs.sort(descending=True)
        cumsum = sorted_probs.cumsum(dim=0)
        top5_mass = sorted_probs[:5].sum().item()
        top1_prob = sorted_probs[0].item()

        # --- Signal 3: Layer agreement ---
        final_pred = layer_predictions[-1]
        agree_count = sum(1 for p in layer_predictions if p == final_pred)
        layer_agreement = agree_count / n_blocks

        # When did the prediction first appear?
        first_agree = n_blocks
        for i, p in enumerate(layer_predictions):
            if p == final_pred:
                first_agree = i
                break
        prediction_stability = 1.0 - (first_agree / n_blocks)

        # Count prediction changes (oscillation)
        changes = sum(1 for i in range(1, len(layer_predictions))
                      if layer_predictions[i] != layer_predictions[i - 1])
        oscillation = changes / max(n_blocks - 1, 1)

        # --- Signal 4: Repetition ---
        generated_tokens.append(top_id)
        is_repetition = False
        for ngram_len in [3, 4, 5]:
            if len(generated_tokens) >= ngram_len:
                ngram = tuple(generated_tokens[-ngram_len:])
                ngram_counts[ngram] = ngram_counts.get(ngram, 0) + 1
                if ngram_counts[ngram] > 1:
                    is_repetition = True

        # --- Hallucination scoring ---
        flags = []
        suspicion_score = 0.0

        # Confident but layers disagree
        if top1_prob > confidence_threshold and layer_agreement < agreement_threshold:
            flags.append("confident_but_layers_disagree")
            suspicion_score += 0.4

        # Late prediction emergence (unstable reasoning)
        if prediction_stability < 0.3 and top1_prob > 0.5:
            flags.append("late_prediction_emergence")
            suspicion_score += 0.3

        # High oscillation in layer predictions
        if oscillation > 0.5:
            flags.append("high_layer_oscillation")
            suspicion_score += 0.2

        # Repetition
        if is_repetition:
            flags.append("repetitive_ngram")
            suspicion_score += 0.3

        # Very low entropy with late emergence = classic hallucination sign
        if normalized_entropy < 0.1 and prediction_stability < 0.4:
            flags.append("overconfident_late_decision")
            suspicion_score += 0.5

        suspicion_score = min(suspicion_score, 1.0)

        token_analyses.append({
            "step": gen_step,
            "token": top_token,
            "token_id": top_id,
            "confidence": round(top1_prob, 4),
            "entropy": round(entropy, 4),
            "normalized_entropy": round(normalized_entropy, 4),
            "top5_mass": round(top5_mass, 4),
            "layer_agreement": round(layer_agreement, 4),
            "prediction_stability": round(prediction_stability, 4),
            "oscillation": round(oscillation, 4),
            "is_repetition": is_repetition,
            "suspicion_score": round(suspicion_score, 4),
            "flags": flags,
        })

        # Append and continue generating
        next_id = torch.tensor([[top_id]], device=all_ids.device)
        all_ids = torch.cat([all_ids, next_id], dim=1)

        # Stop on EOS
        if top_id == tokenizer.eos_token_id:
            break

    # Aggregate results
    flagged = [t for t in token_analyses if t["flags"]]
    avg_suspicion = (sum(t["suspicion_score"] for t in token_analyses)
                     / max(len(token_analyses), 1))
    high_suspicion = [t for t in token_analyses if t["suspicion_score"] >= 0.4]

    generated_text = tokenizer.decode(generated_tokens)

    # Build annotated text with markers
    annotated_parts = []
    for ta in token_analyses:
        if ta["suspicion_score"] >= 0.4:
            annotated_parts.append(f"⚠️{ta['token']}")
        elif ta["suspicion_score"] >= 0.2:
            annotated_parts.append(f"?{ta['token']}")
        else:
            annotated_parts.append(ta["token"])
    annotated_text = "".join(annotated_parts)

    return {
        "prompt": prompt,
        "generated_text": generated_text,
        "annotated_text": annotated_text,
        "total_tokens": len(token_analyses),
        "flagged_tokens": len(flagged),
        "high_suspicion_tokens": len(high_suspicion),
        "avg_suspicion": round(avg_suspicion, 4),
        "per_token": token_analyses,
        "summary": {
            "hallucination_risk": (
                "HIGH" if len(high_suspicion) > len(token_analyses) * 0.3
                else "MEDIUM" if len(high_suspicion) > 0
                else "LOW"
            ),
            "most_suspicious": (
                sorted(token_analyses,
                       key=lambda t: t["suspicion_score"],
                       reverse=True)[:5]
            ),
            "flag_distribution": _count_flags(token_analyses),
        },
        "description": (
            f"Hallucination detection on {len(token_analyses)} generated "
            f"tokens: {len(high_suspicion)} high-suspicion, "
            f"{len(flagged)} flagged total. "
            f"Risk level: "
            f"{'HIGH' if len(high_suspicion) > len(token_analyses) * 0.3 else 'MEDIUM' if len(high_suspicion) > 0 else 'LOW'}. "
            f"Generated: \"{generated_text[:100]}{'...' if len(generated_text) > 100 else ''}\""
        ),
    }


def _count_flags(analyses: List[dict]) -> dict:
    """Count how often each flag type appears."""
    counts = {}
    for ta in analyses:
        for f in ta["flags"]:
            counts[f] = counts.get(f, 0) + 1
    return counts


@torch.no_grad()
def detect_factual_conflicts(model, tokenizer, prompt: str,
                             claim_tokens: List[str]) -> dict:
    """Check whether specific claimed tokens are well-supported.

    Given a prompt and a list of tokens the model "should" produce,
    checks how well each token is supported by the model's internals.
    """
    t = model.transformer
    n_blocks = len(t.h)
    lm_head = model.lm_head
    ln_f = t.ln_f

    input_ids = tokenizer.encode(prompt, return_tensors="pt")
    seq_len = input_ids.shape[1]

    tok_emb = t.wte(input_ids)
    pos_ids = torch.arange(seq_len, device=input_ids.device).unsqueeze(0)
    pos_emb = t.wpe(pos_ids)
    hidden = t.drop(tok_emb + pos_emb)

    # Get per-layer predictions at the last position
    layer_data = []
    for i, block in enumerate(t.h):
        hidden = block(hidden)[0]
        normed = ln_f(hidden)
        logits = F.linear(normed, lm_head.weight)
        probs = F.softmax(logits[0, -1], dim=-1)
        layer_data.append(probs)

    # Final prediction
    final_logits = lm_head(ln_f(hidden))[0, -1]
    final_probs = F.softmax(final_logits, dim=-1)

    results = []
    for claim in claim_tokens:
        token_ids = tokenizer.encode(claim, add_special_tokens=False)
        if not token_ids:
            results.append({"token": claim, "error": "could not encode"})
            continue

        token_id = token_ids[0]
        final_prob = final_probs[token_id].item()
        final_rank = (final_probs > final_probs[token_id]).sum().item() + 1

        # Track support across layers
        per_layer = []
        for i, probs in enumerate(layer_data):
            p = probs[token_id].item()
            r = (probs > probs[token_id]).sum().item() + 1
            per_layer.append({
                "layer": i,
                "prob": round(p, 6),
                "rank": r,
            })

        # Early layers supporting = well-grounded knowledge
        early_support = sum(
            1 for ld in per_layer[:n_blocks // 3]
            if ld["rank"] <= 10
        ) / max(n_blocks // 3, 1)

        # Late layers supporting = might be surface pattern
        late_support = sum(
            1 for ld in per_layer[2 * n_blocks // 3:]
            if ld["rank"] <= 10
        ) / max(n_blocks // 3, 1)

        grounding = "WELL_GROUNDED" if early_support > 0.3 else (
            "SURFACE_PATTERN" if late_support > 0.5 else "UNSUPPORTED"
        )

        results.append({
            "token": claim,
            "token_id": token_id,
            "final_prob": round(final_prob, 6),
            "final_rank": final_rank,
            "early_support": round(early_support, 4),
            "late_support": round(late_support, 4),
            "grounding": grounding,
            "per_layer_sample": per_layer[::max(1, n_blocks // 6)],
        })

    return {
        "prompt": prompt,
        "claims": results,
        "description": (
            f"Factual conflict check for {len(claim_tokens)} tokens. "
            + "; ".join(
                f"'{r['token']}': {r.get('grounding', 'error')}"
                for r in results
            )
        ),
    }
