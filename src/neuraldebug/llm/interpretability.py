"""Interpretability techniques for transformer model analysis.

Provides :class:`LogitLens`, :class:`ActivationPatching`,
:class:`AttentionAnalysis`, and :class:`Probing` — each exposes static
methods that run a specific interpretability analysis on a GPT-2 family
model and return structured dictionaries consumed by the debugger and
diagnosis modules.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F


# ── Helpers ───────────────────────────────────────────────────────────────

def _position_ids(input_ids: torch.Tensor) -> torch.Tensor:
    """Create ``[1, seq_len]`` position IDs on the same device as *input_ids*."""
    return torch.arange(input_ids.size(1), device=input_ids.device).unsqueeze(0)


def _embed(model, input_ids: torch.Tensor) -> torch.Tensor:
    """Compute the GPT-2 token + position embedding."""
    return (
        model.transformer.wte(input_ids)
        + model.transformer.wpe(_position_ids(input_ids))
    )


def _collect_hidden_states(
    model, input_ids: torch.Tensor
) -> List[Tuple[str, torch.Tensor]]:
    """Run a forward pass and return hidden states after each layer.

    Returns a list of ``(layer_name, hidden_state)`` pairs starting with
    ``"embedding"`` followed by ``"block_0"`` … ``"block_N"``.
    """
    hidden = _embed(model, input_ids)
    states: List[Tuple[str, torch.Tensor]] = [("embedding", hidden.clone())]
    for i, block in enumerate(model.transformer.h):
        hidden = block(hidden)[0]
        states.append((f"block_{i}", hidden.clone()))
    return states


def _project_to_vocab(model, hidden: torch.Tensor) -> torch.Tensor:
    """Apply final layer-norm + ``lm_head`` → logits."""
    return model.lm_head(model.transformer.ln_f(hidden))


def _analyze_layer(
    model, tokenizer, hidden: torch.Tensor, layer_name: str, top_k: int
) -> Dict[str, Any]:
    """Project *hidden* to vocabulary space and gather statistics."""
    logits = _project_to_vocab(model, hidden)[0, -1, :]
    probs = F.softmax(logits, dim=-1)

    k = min(top_k, probs.size(-1))
    top_probs, top_indices = torch.topk(probs, k)

    top_k_list: List[Tuple[str, float]] = []
    predictions: List[Dict[str, Any]] = []
    for j in range(k):
        tid = top_indices[j].item()
        p = round(top_probs[j].item(), 6)
        tok = tokenizer.decode([tid])
        top_k_list.append((tok, p))
        predictions.append({
            "token_id": tid,
            "token": tok,
            "probability": p,
            "prob": p,
            "rank": j,
        })

    top_token = top_k_list[0][0] if top_k_list else ""
    top_prob = top_k_list[0][1] if top_k_list else 0.0

    log_probs = torch.log(probs + 1e-10)
    entropy = round(-(probs * log_probs).sum().item(), 4)

    return {
        "layer": layer_name,
        "top_token": top_token,
        "top_prob": top_prob,
        "entropy": entropy,
        "top_k": top_k_list,
        "predictions": predictions,
    }


def _tokenize(tokenizer, text: str, device: torch.device) -> torch.Tensor:
    """Encode *text* into a ``[1, seq_len]`` tensor on *device*."""
    ids = tokenizer.encode(text)
    return torch.tensor([ids], device=device)


def _decode_tokens(tokenizer, input_ids: torch.Tensor) -> List[str]:
    """Decode each token in *input_ids* ``[1, seq_len]`` individually."""
    return [tokenizer.decode([input_ids[0, j].item()]) for j in range(input_ids.size(1))]


# ── LogitLens ─────────────────────────────────────────────────────────────

class LogitLens:
    """Project hidden states through the unembedding at every layer to
    reveal how the model's prediction evolves across depth."""

    @staticmethod
    @torch.no_grad()
    def run(
        model, tokenizer, input_ids: torch.Tensor, top_k: int = 5
    ) -> Dict[str, Any]:
        """Run the logit-lens analysis.

        Parameters
        ----------
        model : GPT2LMHeadModel
            A GPT-2 family model.
        tokenizer : PreTrainedTokenizer
            The corresponding tokenizer.
        input_ids : torch.Tensor
            Token IDs of shape ``[1, seq_len]``.
        top_k : int
            Number of top predictions to return per layer.

        Returns
        -------
        dict
            ``{"layers": [<per-layer info>, …]}``
        """
        states = _collect_hidden_states(model, input_ids)

        layers = [
            _analyze_layer(model, tokenizer, h, name, top_k)
            for name, h in states
        ]
        # Final-norm entry mirrors the model's actual output.
        layers.append(
            _analyze_layer(model, tokenizer, states[-1][1], "final_norm", top_k)
        )
        return {"layers": layers}


# ── ActivationPatching ────────────────────────────────────────────────────

class ActivationPatching:
    """Causal tracing via activation patching — identifies which layers
    are most responsible for a particular prediction."""

    @staticmethod
    @torch.no_grad()
    def run(
        model,
        tokenizer,
        clean_prompt: str,
        corrupted_prompt: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Patch clean hidden states into a corrupted run, one layer at a
        time, and measure how much each layer recovers the clean prediction.

        Parameters
        ----------
        model : GPT2LMHeadModel
        tokenizer : PreTrainedTokenizer
        clean_prompt : str
            The prompt that produces the target prediction.
        corrupted_prompt : str
            A prompt that disrupts the prediction (e.g. noised subject).
        **kwargs
            Accepted for forward-compatibility (``top_k``, etc.).

        Returns
        -------
        dict
            Keys: ``target_token``, ``clean_prob``, ``corrupted_prob``,
            ``most_causal``, ``layers``.
        """
        device = next(model.parameters()).device
        clean_ids = _tokenize(tokenizer, clean_prompt, device)
        corrupted_ids = _tokenize(tokenizer, corrupted_prompt, device)

        # 1. Clean forward pass — determine the target token.
        clean_states = _collect_hidden_states(model, clean_ids)
        clean_logits = _project_to_vocab(model, clean_states[-1][1])
        clean_probs = F.softmax(clean_logits[0, -1, :], dim=-1)
        target_id = clean_probs.argmax().item()
        target_token = tokenizer.decode([target_id])
        clean_prob = round(clean_probs[target_id].item(), 6)

        # 2. Corrupted forward pass — baseline probability of the target.
        corrupted_states = _collect_hidden_states(model, corrupted_ids)
        corrupted_logits = _project_to_vocab(model, corrupted_states[-1][1])
        corrupted_probs = F.softmax(corrupted_logits[0, -1, :], dim=-1)
        corrupted_prob = round(corrupted_probs[target_id].item(), 6)

        # 3. For each block, substitute clean hidden states and measure
        #    how much the target-token probability recovers.
        prob_range = clean_prob - corrupted_prob
        layers: List[Dict[str, Any]] = []
        best: Dict[str, Any] = {"layer": "block_0", "recovery": 0.0}

        for idx, (name, clean_hidden) in enumerate(clean_states):
            if name == "embedding":
                continue  # only patch transformer blocks
            block_idx = idx - 1
            patched_logits = ActivationPatching._run_with_patch(
                model, corrupted_ids, clean_hidden, block_idx,
            )
            patched_probs = F.softmax(patched_logits[0, -1, :], dim=-1)
            patched_prob = patched_probs[target_id].item()
            recovery = (
                (patched_prob - corrupted_prob) / prob_range
                if abs(prob_range) > 1e-10
                else 0.0
            )
            entry = {"layer": name, "recovery": round(recovery, 6)}
            layers.append(entry)
            if recovery > best["recovery"]:
                best = entry.copy()

        return {
            "target_token": target_token,
            "clean_prob": clean_prob,
            "corrupted_prob": corrupted_prob,
            "most_causal": best,
            "layers": layers,
        }

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _run_with_patch(
        model,
        corrupted_ids: torch.Tensor,
        patch_hidden: torch.Tensor,
        patch_layer_idx: int,
    ) -> torch.Tensor:
        """Forward-pass the *corrupted_ids* but substitute *patch_hidden*
        at block ``patch_layer_idx``."""
        hidden = _embed(model, corrupted_ids)
        for i, block in enumerate(model.transformer.h):
            if i == patch_layer_idx:
                overlap = min(hidden.size(1), patch_hidden.size(1))
                patched = hidden.clone()
                patched[:, :overlap, :] = patch_hidden[:, :overlap, :]
                hidden = patched
            hidden = block(hidden)[0]
        return _project_to_vocab(model, hidden)


# ── AttentionAnalysis ─────────────────────────────────────────────────────

class AttentionAnalysis:
    """Inspect attention weight patterns across all heads."""

    @staticmethod
    @torch.no_grad()
    def attention_to_token(
        model, tokenizer, input_ids: torch.Tensor, position: int
    ) -> Dict[str, Any]:
        """Rank every attention head by how strongly the **last** token
        attends to the token at *position*.

        Returns
        -------
        dict
            ``{"target_token": str, "heads": [{"layer", "head", "weight"}, …]}``
            Heads are sorted by descending weight.
        """
        seq_len = input_ids.size(1)
        if position < 0 or position >= seq_len:
            return {"error": f"Position {position} out of range [0, {seq_len - 1}]"}

        tokens = _decode_tokens(tokenizer, input_ids)
        target_token = tokens[position]

        outputs = model(input_ids, output_attentions=True)
        attentions = outputs.attentions  # tuple of [B, heads, seq, seq]

        heads: List[Dict[str, Any]] = []
        for layer_idx, attn in enumerate(attentions):
            num_heads = attn.size(1)
            for head_idx in range(num_heads):
                weight = attn[0, head_idx, -1, position].item()
                heads.append({
                    "layer": layer_idx,
                    "head": head_idx,
                    "weight": round(weight, 6),
                })

        heads.sort(key=lambda h: h["weight"], reverse=True)
        return {"target_token": target_token, "heads": heads}

    @staticmethod
    @torch.no_grad()
    def analyze_heads(
        model, tokenizer, input_ids: torch.Tensor
    ) -> Dict[str, Any]:
        """Compute a *focus ratio* for every attention head and return the
        most-focused and least-focused heads.

        The focus ratio for a head is defined as the maximum attention
        weight assigned by the **last** sequence token to any position.

        Returns
        -------
        dict
            ``{"total_heads", "heads", "most_focused", "least_focused"}``
        """
        tokens = _decode_tokens(tokenizer, input_ids)

        outputs = model(input_ids, output_attentions=True)
        attentions = outputs.attentions

        all_heads: List[Dict[str, Any]] = []
        for layer_idx, attn in enumerate(attentions):
            num_heads = attn.size(1)
            for head_idx in range(num_heads):
                weights = attn[0, head_idx, -1, :]
                focus_ratio = weights.max().item()
                attended_pos = weights.argmax().item()

                all_heads.append({
                    "layer": layer_idx,
                    "head": head_idx,
                    "focus_ratio": round(focus_ratio, 6),
                    "last_token_attends_to": {
                        "position": int(attended_pos),
                        "token": tokens[attended_pos],
                        "weight": round(weights[attended_pos].item(), 6),
                    },
                })

        all_heads.sort(key=lambda h: h["focus_ratio"], reverse=True)
        total = len(all_heads)
        top_n = min(5, total)

        return {
            "total_heads": total,
            "heads": all_heads,
            "most_focused": all_heads[:top_n],
            "least_focused": sorted(
                all_heads[-top_n:], key=lambda h: h["focus_ratio"]
            ),
        }


# ── Probing ───────────────────────────────────────────────────────────────

_PROBE_DESCRIPTIONS: Dict[str, str] = {
    "next_token": (
        "Can a linear probe predict the next token from hidden states "
        "at each layer? Uses the model's own lm_head as the probe."
    ),
    "token_identity": (
        "Can a linear probe recover the current token identity from "
        "hidden states at each layer?"
    ),
    "position": (
        "Does the hidden state encode positional information? Measured "
        "by cosine similarity within vs. across positions."
    ),
}


class Probing:
    """Lightweight probing tasks that test what information is encoded in
    the hidden representations at each layer."""

    @staticmethod
    @torch.no_grad()
    def run(
        model,
        tokenizer,
        input_ids: torch.Tensor,
        task: str = "next_token",
    ) -> Dict[str, Any]:
        """Run a probing analysis.

        Parameters
        ----------
        model : GPT2LMHeadModel
        tokenizer : PreTrainedTokenizer
        input_ids : torch.Tensor
            Shape ``[1, seq_len]``.
        task : str
            One of ``"next_token"``, ``"token_identity"``, ``"position"``.

        Returns
        -------
        dict
            ``{"task", "description", "layers": [...]}``, or
            ``{"error": str}`` on failure.
        """
        if task not in _PROBE_DESCRIPTIONS:
            return {
                "error": (
                    f"Unknown probing task '{task}'. "
                    f"Supported: {list(_PROBE_DESCRIPTIONS.keys())}"
                )
            }
        try:
            states = _collect_hidden_states(model, input_ids)
            if task == "position":
                return Probing._probe_position(states)
            if task == "next_token":
                return Probing._probe_next_token(model, input_ids, states)
            return Probing._probe_token_identity(model, input_ids, states)
        except Exception as exc:
            return {"error": str(exc)}

    # -- next-token probe --------------------------------------------------

    @staticmethod
    def _probe_next_token(
        model, input_ids: torch.Tensor, states: List[Tuple[str, torch.Tensor]]
    ) -> Dict[str, Any]:
        """At each layer, check if ``argmax(lm_head(ln_f(hidden[i])))``
        equals the actual next token at position *i+1*."""
        seq_len = input_ids.size(1)
        if seq_len < 2:
            return {"error": "Sequence too short for next-token probing (need >= 2 tokens)"}

        target_ids = input_ids[0, 1:]  # tokens at positions 1…N-1
        total = target_ids.size(0)

        layers: List[Dict[str, Any]] = []
        for name, hidden in states:
            logits = _project_to_vocab(model, hidden)
            preds = logits[0, :-1, :].argmax(dim=-1)
            correct = int((preds == target_ids).sum().item())
            layers.append({
                "layer": name,
                "accuracy": round(correct / total, 6) if total else 0.0,
                "correct_count": correct,
                "total": int(total),
            })

        # Final-norm entry (actual model output).
        final_hidden = states[-1][1]
        logits = _project_to_vocab(model, final_hidden)
        preds = logits[0, :-1, :].argmax(dim=-1)
        correct = int((preds == target_ids).sum().item())
        layers.append({
            "layer": "final_norm",
            "accuracy": round(correct / total, 6) if total else 0.0,
            "correct_count": correct,
            "total": int(total),
        })

        return {
            "task": "next_token",
            "description": _PROBE_DESCRIPTIONS["next_token"],
            "layers": layers,
        }

    # -- token-identity probe ----------------------------------------------

    @staticmethod
    def _probe_token_identity(
        model, input_ids: torch.Tensor, states: List[Tuple[str, torch.Tensor]]
    ) -> Dict[str, Any]:
        """At each layer, check if ``argmax(lm_head(ln_f(hidden[i])))``
        equals the *current* token at position *i*."""
        target_ids = input_ids[0]
        total = target_ids.size(0)

        layers: List[Dict[str, Any]] = []
        for name, hidden in states:
            logits = _project_to_vocab(model, hidden)
            preds = logits[0].argmax(dim=-1)
            correct = int((preds == target_ids).sum().item())
            layers.append({
                "layer": name,
                "accuracy": round(correct / total, 6) if total else 0.0,
                "correct_count": correct,
                "total": int(total),
            })

        final_hidden = states[-1][1]
        logits = _project_to_vocab(model, final_hidden)
        preds = logits[0].argmax(dim=-1)
        correct = int((preds == target_ids).sum().item())
        layers.append({
            "layer": "final_norm",
            "accuracy": round(correct / total, 6) if total else 0.0,
            "correct_count": correct,
            "total": int(total),
        })

        return {
            "task": "token_identity",
            "description": _PROBE_DESCRIPTIONS["token_identity"],
            "layers": layers,
        }

    # -- position probe ----------------------------------------------------

    @staticmethod
    def _probe_position(
        states: List[Tuple[str, torch.Tensor]],
    ) -> Dict[str, Any]:
        """Measure how well positional information is preserved at each
        layer using cosine-similarity separability.

        * **self_similarity** — mean cosine similarity of each position
          vector with itself (diagonal of the similarity matrix; ≈ 1.0).
        * **cross_similarity** — mean cosine similarity between different
          positions (off-diagonal).
        * **separability** = *self_similarity − cross_similarity*.  Higher
          values indicate the layer still distinguishes positions.
        """
        layers: List[Dict[str, Any]] = []

        def _compute(hidden: torch.Tensor) -> Tuple[float, float, float]:
            h = hidden[0]  # [seq_len, hidden_dim]
            n = h.size(0)
            if n < 2:
                return 1.0, 0.0, 1.0
            h_norm = F.normalize(h, dim=-1)
            sim = h_norm @ h_norm.T  # [n, n]
            self_sim = sim.diag().mean().item()
            mask = ~torch.eye(n, dtype=torch.bool, device=sim.device)
            cross_sim = sim[mask].mean().item()
            return self_sim, cross_sim, self_sim - cross_sim

        for name, hidden in states:
            ss, cs, sep = _compute(hidden)
            layers.append({
                "layer": name,
                "self_similarity": round(ss, 6),
                "cross_similarity": round(cs, 6),
                "separability": round(sep, 6),
            })

        # Final-norm entry.
        ss, cs, sep = _compute(states[-1][1])
        layers.append({
            "layer": "final_norm",
            "self_similarity": round(ss, 6),
            "cross_similarity": round(cs, 6),
            "separability": round(sep, 6),
        })

        return {
            "task": "position",
            "description": _PROBE_DESCRIPTIONS["position"],
            "layers": layers,
        }
