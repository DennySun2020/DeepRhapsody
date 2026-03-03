"""Neuron-level analysis for LLM debugging.

For any individual neuron in the model's FFN layers, provides:
- Activation statistics (mean, max, distribution)
- Top-activating tokens from the current prompt
- Causal effect: what happens to the output when this neuron is ablated

Supports both MLP intermediate (c_fc) and output (c_proj) neurons.
"""

import math
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F


@torch.no_grad()
def neuron_dashboard(model, tokenizer, input_ids: torch.Tensor,
                     layer_idx: int, neuron_idx: int,
                     adapter=None) -> dict:
    """Full dashboard for a single FFN neuron.

    Runs the forward pass, captures the neuron's activation at every
    position, and measures its causal impact by ablating it.

    Args:
        model: Language model
        tokenizer: Tokenizer
        input_ids: (1, seq_len)
        layer_idx: Transformer block index
        neuron_idx: Index into the FFN intermediate layer
        adapter: Optional ModelAdapter for architecture-agnostic access
    """
    if adapter is not None:
        n_blocks = adapter.info().num_layers
        ffn_dim = adapter.info().ffn_dim
        ffn_module = adapter.get_ffn_intermediate(layer_idx)
        activation_fn = adapter.ffn_activation_fn
    else:
        t = model.transformer
        n_blocks = len(t.h)
        block = t.h[layer_idx]
        ffn_dim = block.mlp.c_fc.weight.shape[0]
        ffn_module = block.mlp.c_fc
        activation_fn = torch.nn.functional.gelu

    if layer_idx < 0 or layer_idx >= n_blocks:
        return {"error": f"Layer {layer_idx} out of range [0, {n_blocks - 1}]"}
    if neuron_idx < 0 or neuron_idx >= ffn_dim:
        return {"error": f"Neuron {neuron_idx} out of range [0, {ffn_dim - 1}]"}

    tokens = tokenizer.convert_ids_to_tokens(input_ids[0].tolist())
    seq_len = input_ids.shape[1]

    # --- Phase 1: Capture neuron activations ---
    neuron_acts = []

    def _capture_hook(module, inp, out):
        act = activation_fn(out)
        neuron_acts.append(act[0, :, neuron_idx].detach().clone())

    hook = ffn_module.register_forward_hook(_capture_hook)

    # Full forward pass for baseline logits (adapter-aware)
    if adapter is not None:
        hidden = adapter.embed(input_ids)
        for i in range(n_blocks):
            hidden = adapter.forward_block(hidden, i)
        normed = adapter.apply_final_norm(hidden)
        baseline_logits = adapter.get_logits(normed)
    else:
        tok_emb = t.wte(input_ids)
        pos_ids = torch.arange(seq_len, device=input_ids.device).unsqueeze(0)
        pos_emb = t.wpe(pos_ids)
        hidden = t.drop(tok_emb + pos_emb)
        for blk in t.h:
            hidden = blk(hidden)[0]
        baseline_logits = model.lm_head(t.ln_f(hidden))

    baseline_probs = F.softmax(baseline_logits[0, -1], dim=-1)
    baseline_top = torch.topk(baseline_probs, 5)
    baseline_pred = tokenizer.decode([baseline_top.indices[0].item()])

    hook.remove()

    if not neuron_acts:
        return {"error": "Failed to capture neuron activations"}

    acts = neuron_acts[0]  # (seq_len,)

    # Activation statistics
    act_list = acts.tolist()
    act_mean = acts.mean().item()
    act_max = acts.max().item()
    act_min = acts.min().item()
    act_std = acts.std().item()

    # Top-activating positions
    top_vals, top_pos = torch.topk(acts, min(5, seq_len))
    top_tokens = [
        {"position": top_pos[i].item(),
         "token": tokens[top_pos[i].item()] if top_pos[i].item() < len(tokens) else "?",
         "activation": round(top_vals[i].item(), 4)}
        for i in range(top_vals.shape[0])
    ]

    # Per-position activations (for visualization)
    per_position = [
        {"pos": i, "token": tokens[i] if i < len(tokens) else "?",
         "act": round(act_list[i], 4)}
        for i in range(seq_len)
    ]

    # --- Phase 2: Ablation (causal effect) ---
    def _ablate_hook(module, inp, out):
        out = out.clone()
        out[:, :, neuron_idx] = 0.0
        return out

    ablate_hook = ffn_module.register_forward_hook(_ablate_hook)

    # Ablated forward pass (adapter-aware)
    if adapter is not None:
        hidden = adapter.embed(input_ids)
        for i in range(n_blocks):
            hidden = adapter.forward_block(hidden, i)
        normed = adapter.apply_final_norm(hidden)
        ablated_logits = adapter.get_logits(normed)
    else:
        hidden = t.drop(t.wte(input_ids) + t.wpe(pos_ids))
        for blk in t.h:
            hidden = blk(hidden)[0]
        ablated_logits = model.lm_head(t.ln_f(hidden))
    ablated_probs = F.softmax(ablated_logits[0, -1], dim=-1)
    ablated_top = torch.topk(ablated_probs, 5)
    ablated_pred = tokenizer.decode([ablated_top.indices[0].item()])

    ablate_hook.remove()

    # KL divergence between baseline and ablated
    kl_div = F.kl_div(
        ablated_probs.log().unsqueeze(0),
        baseline_probs.unsqueeze(0),
        reduction='batchmean', log_target=False
    ).item()

    # Probability shift for baseline top token
    top_token_id = baseline_top.indices[0].item()
    prob_before = baseline_probs[top_token_id].item()
    prob_after = ablated_probs[top_token_id].item()

    prediction_changed = baseline_pred != ablated_pred

    return {
        "layer": layer_idx,
        "neuron": neuron_idx,
        "ffn_dim": ffn_dim,
        "activation_stats": {
            "mean": round(act_mean, 4),
            "std": round(act_std, 4),
            "min": round(act_min, 4),
            "max": round(act_max, 4),
        },
        "top_activating_tokens": top_tokens,
        "per_position": per_position,
        "ablation": {
            "baseline_prediction": baseline_pred,
            "ablated_prediction": ablated_pred,
            "prediction_changed": prediction_changed,
            "top_token_prob_before": round(prob_before, 6),
            "top_token_prob_after": round(prob_after, 6),
            "prob_delta": round(prob_after - prob_before, 6),
            "kl_divergence": round(kl_div, 6),
        },
        "baseline_top5": [
            {"token": tokenizer.decode([baseline_top.indices[i].item()]),
             "prob": round(baseline_top.values[i].item(), 6)}
            for i in range(5)
        ],
        "ablated_top5": [
            {"token": tokenizer.decode([ablated_top.indices[i].item()]),
             "prob": round(ablated_top.values[i].item(), 6)}
            for i in range(5)
        ],
        "description": (
            f"Neuron block_{layer_idx}.mlp.c_fc[{neuron_idx}]: "
            f"max activation {act_max:.3f}, "
            f"ablation {'CHANGES' if prediction_changed else 'preserves'} "
            f"prediction ('{baseline_pred}' → '{ablated_pred}', "
            f"KL={kl_div:.4f})."
        ),
    }


@torch.no_grad()
def neuron_scan(model, tokenizer, input_ids: torch.Tensor,
                layer_idx: int, top_k: int = 10,
                method: str = "activation") -> dict:
    """Find the most interesting neurons in a layer for the current prompt.

    Methods:
        "activation" — rank by max activation value (which neurons fire hardest)
        "variance" — rank by activation variance across positions
        "causal" — rank by ablation impact (slow — ablates top candidates)
    """
    t = model.transformer
    n_blocks = len(t.h)
    if layer_idx < 0 or layer_idx >= n_blocks:
        return {"error": f"Layer {layer_idx} out of range [0, {n_blocks - 1}]"}

    block = t.h[layer_idx]
    tokens = tokenizer.convert_ids_to_tokens(input_ids[0].tolist())
    seq_len = input_ids.shape[1]

    # Capture all FFN activations at this layer
    captured = []

    def _hook(module, inp, out):
        act = torch.nn.functional.gelu(out)
        captured.append(act[0].detach().clone())  # (seq, ffn_dim)

    hook = block.mlp.c_fc.register_forward_hook(_hook)

    tok_emb = t.wte(input_ids)
    pos_ids = torch.arange(seq_len, device=input_ids.device).unsqueeze(0)
    pos_emb = t.wpe(pos_ids)
    hidden = t.drop(tok_emb + pos_emb)
    for blk in t.h:
        hidden = blk(hidden)[0]

    hook.remove()

    if not captured:
        return {"error": "Failed to capture activations"}

    acts = captured[0]  # (seq, ffn_dim)
    ffn_dim = acts.shape[1]

    if method == "variance":
        scores = acts.var(dim=0)  # (ffn_dim,)
        metric_name = "variance"
    else:
        scores = acts.max(dim=0).values  # (ffn_dim,)
        metric_name = "max_activation"

    top_scores, top_neurons = torch.topk(scores, min(top_k, ffn_dim))

    results = []
    for i in range(top_scores.shape[0]):
        n_idx = top_neurons[i].item()
        n_acts = acts[:, n_idx]
        max_pos = n_acts.argmax().item()
        results.append({
            "neuron": n_idx,
            metric_name: round(top_scores[i].item(), 4),
            "mean": round(n_acts.mean().item(), 4),
            "max_position": max_pos,
            "max_token": tokens[max_pos] if max_pos < len(tokens) else "?",
        })

    # If causal method requested, do quick ablation on top candidates
    if method == "causal" and results:
        baseline_logits = model.lm_head(t.ln_f(hidden))
        baseline_probs = F.softmax(baseline_logits[0, -1], dim=-1)
        baseline_top_id = baseline_probs.argmax().item()

        for entry in results:
            n_idx = entry["neuron"]

            def _ablate(module, inp, out, target=n_idx):
                out = out.clone()
                out[:, :, target] = 0.0
                return out

            h = block.mlp.c_fc.register_forward_hook(_ablate)
            h2 = t.drop(t.wte(input_ids) + t.wpe(pos_ids))
            for blk in t.h:
                h2 = blk(h2)[0]
            abl_logits = model.lm_head(t.ln_f(h2))
            abl_probs = F.softmax(abl_logits[0, -1], dim=-1)
            h.remove()

            kl = F.kl_div(
                abl_probs.log().unsqueeze(0),
                baseline_probs.unsqueeze(0),
                reduction='batchmean', log_target=False
            ).item()
            entry["kl_divergence"] = round(kl, 6)
            entry["prob_delta"] = round(
                abl_probs[baseline_top_id].item()
                - baseline_probs[baseline_top_id].item(), 6)

        results.sort(key=lambda x: x.get("kl_divergence", 0), reverse=True)

    return {
        "layer": layer_idx,
        "ffn_dim": ffn_dim,
        "method": method,
        "top_neurons": results,
        "tokens": tokens,
        "description": (
            f"Neuron scan on block_{layer_idx} ({ffn_dim} neurons), "
            f"ranked by {metric_name}. "
            f"Top neuron: #{results[0]['neuron']} "
            f"({metric_name}={results[0][metric_name]:.3f}, "
            f"max at '{results[0]['max_token']}')."
            if results else "No active neurons found."
        ),
    }


@torch.no_grad()
def neuron_ablate(model, tokenizer, input_ids: torch.Tensor,
                  layer_idx: int, neuron_idx: int,
                  generate_tokens: int = 20) -> dict:
    """Ablate a neuron and compare generation before/after.

    Generates text with and without the neuron active, showing
    the behavioral impact.
    """
    t = model.transformer
    tokens = tokenizer.convert_ids_to_tokens(input_ids[0].tolist())

    def _generate(ablate: bool, n_tokens: int):
        ids = input_ids.clone()
        generated = []
        for _ in range(n_tokens):
            hook = None
            if ablate:
                def _hook(module, inp, out, target=neuron_idx):
                    out = out.clone()
                    out[:, :, target] = 0.0
                    return out
                hook = t.h[layer_idx].mlp.c_fc.register_forward_hook(_hook)

            seq_len = ids.shape[1]
            tok_emb = t.wte(ids)
            pos_ids = torch.arange(seq_len, device=ids.device).unsqueeze(0)
            pos_emb = t.wpe(pos_ids)
            hidden = t.drop(tok_emb + pos_emb)
            for blk in t.h:
                hidden = blk(hidden)[0]
            logits = model.lm_head(t.ln_f(hidden))

            if hook:
                hook.remove()

            next_id = logits[0, -1].argmax().unsqueeze(0).unsqueeze(0)
            generated.append(next_id.item())
            ids = torch.cat([ids, next_id], dim=1)

        return tokenizer.decode(generated)

    text_normal = _generate(ablate=False, n_tokens=generate_tokens)
    text_ablated = _generate(ablate=True, n_tokens=generate_tokens)

    return {
        "layer": layer_idx,
        "neuron": neuron_idx,
        "prompt": tokenizer.decode(input_ids[0]),
        "generation_normal": text_normal,
        "generation_ablated": text_ablated,
        "changed": text_normal != text_ablated,
        "description": (
            f"Neuron ablation block_{layer_idx}.c_fc[{neuron_idx}]: "
            f"{'Output CHANGED' if text_normal != text_ablated else 'No effect on output'}."
        ),
    }
