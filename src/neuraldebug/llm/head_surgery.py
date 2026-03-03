"""Attention head surgery — ablate, amplify, and restore individual heads.

Enables targeted interventions on the model's attention mechanism:
- **Ablate**: zero out a head's contribution to see if it's needed
- **Amplify**: scale a head's output to boost its effect
- **Sweep**: ablate every head individually, rank by impact
- **Restore**: undo all modifications

All modifications are reversible. The original weights are saved
before any surgery and can be restored at any time.
"""

import math
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F


class HeadSurgeon:
    """Perform targeted interventions on attention heads.

    Modifications are applied by storing hooks or by directly
    modifying the model's projection weights (c_proj). All changes
    are reversible via restore().
    """

    def __init__(self):
        self._saved_weights: Dict[str, torch.Tensor] = {}
        self._active_hooks = []
        self._modifications: List[dict] = []

    def ablate(self, model, tokenizer, input_ids: torch.Tensor,
               layer_idx: int, head_idx: int,
               generate_tokens: int = 20,
               adapter=None) -> dict:
        """Ablate (zero out) one attention head and compare output.

        Uses a forward hook to zero the head's output slice in the
        attention output projection, then compares generation.

        Args:
            adapter: Optional ModelAdapter for architecture-agnostic access.
        """
        if adapter is not None:
            n_blocks = adapter.info().num_layers
            n_heads = adapter.info().num_heads
            head_dim = adapter.info().head_dim
        else:
            t = model.transformer
            n_blocks = len(t.h)
            n_heads = model.config.n_head
            head_dim = model.config.n_embd // n_heads

        if layer_idx < 0 or layer_idx >= n_blocks:
            return {"error": f"Layer {layer_idx} out of range [0, {n_blocks - 1}]"}
        if head_idx < 0 or head_idx >= n_heads:
            return {"error": f"Head {head_idx} out of range [0, {n_heads - 1}]"}

        tokens = tokenizer.convert_ids_to_tokens(input_ids[0].tolist())

        # Generate baseline
        baseline_text = self._generate(
            model, tokenizer, input_ids, generate_tokens)
        baseline_logits, baseline_probs = self._get_final_prediction(
            model, input_ids)

        # Generate with head ablated
        start = head_idx * head_dim
        end = start + head_dim

        def _ablate_hook(module, inp, out):
            # out is (batch, seq, n_embd) — zero the head's slice
            out = out.clone() if not isinstance(out, tuple) else (out[0].clone(), *out[1:])
            target = out if not isinstance(out, tuple) else out[0]
            target[:, :, start:end] = 0.0
            return out

        # Use adapter to find the right module for the hook
        if adapter is not None:
            attn_proj = adapter.get_attention_output_proj(layer_idx)
        else:
            attn_proj = model.transformer.h[layer_idx].attn.c_proj

        hook = attn_proj.register_forward_hook(_ablate_hook)
        ablated_text = self._generate(
            model, tokenizer, input_ids, generate_tokens)
        ablated_logits, ablated_probs = self._get_final_prediction(
            model, input_ids)
        hook.remove()

        # Measure impact
        kl_div = F.kl_div(
            ablated_probs.log().unsqueeze(0),
            baseline_probs.unsqueeze(0),
            reduction='batchmean', log_target=False
        ).item()

        baseline_top = tokenizer.decode(
            [baseline_probs.argmax().item()])
        ablated_top = tokenizer.decode(
            [ablated_probs.argmax().item()])
        prediction_changed = baseline_top != ablated_top

        # Record modification
        self._modifications.append({
            "type": "ablate",
            "layer": layer_idx,
            "head": head_idx,
        })

        return {
            "layer": layer_idx,
            "head": head_idx,
            "head_dim": head_dim,
            "n_heads": n_heads,
            "baseline_generation": baseline_text,
            "ablated_generation": ablated_text,
            "prediction_changed": prediction_changed,
            "baseline_top_token": baseline_top,
            "ablated_top_token": ablated_top,
            "kl_divergence": round(kl_div, 6),
            "generation_changed": baseline_text != ablated_text,
            "description": (
                f"Head ablation L{layer_idx}.H{head_idx}: "
                f"{'CHANGES output' if baseline_text != ablated_text else 'no effect'}. "
                f"KL divergence: {kl_div:.4f}. "
                f"Prediction: '{baseline_top}' → '{ablated_top}'."
            ),
        }

    def amplify(self, model, tokenizer, input_ids: torch.Tensor,
                layer_idx: int, head_idx: int, factor: float = 2.0,
                generate_tokens: int = 20, adapter=None) -> dict:
        """Amplify one attention head's output by a scalar factor.

        Scales the head's slice in the attention output projection.
        """
        if adapter is not None:
            n_blocks = adapter.info().num_layers
            n_heads = adapter.info().num_heads
            head_dim = adapter.info().head_dim
        else:
            t = model.transformer
            n_blocks = len(t.h)
            n_heads = model.config.n_head
            head_dim = model.config.n_embd // n_heads

        if layer_idx < 0 or layer_idx >= n_blocks:
            return {"error": f"Layer {layer_idx} out of range [0, {n_blocks - 1}]"}
        if head_idx < 0 or head_idx >= n_heads:
            return {"error": f"Head {head_idx} out of range [0, {n_heads - 1}]"}

        start = head_idx * head_dim
        end = start + head_dim

        # Generate baseline
        baseline_text = self._generate(
            model, tokenizer, input_ids, generate_tokens)

        # Generate with head amplified
        def _amplify_hook(module, inp, out):
            out = out.clone() if not isinstance(out, tuple) else (out[0].clone(), *out[1:])
            target = out if not isinstance(out, tuple) else out[0]
            target[:, :, start:end] *= factor
            return out

        if adapter is not None:
            attn_proj = adapter.get_attention_output_proj(layer_idx)
        else:
            attn_proj = model.transformer.h[layer_idx].attn.c_proj

        hook = attn_proj.register_forward_hook(_amplify_hook)
        amplified_text = self._generate(
            model, tokenizer, input_ids, generate_tokens)
        _, amplified_probs = self._get_final_prediction(model, input_ids)
        _, baseline_probs = self._get_final_prediction(model, input_ids)
        hook.remove()

        # Get clean baseline probs (no hook)
        _, baseline_probs = self._get_final_prediction(model, input_ids)

        self._modifications.append({
            "type": "amplify",
            "layer": layer_idx,
            "head": head_idx,
            "factor": factor,
        })

        return {
            "layer": layer_idx,
            "head": head_idx,
            "factor": factor,
            "baseline_generation": baseline_text,
            "amplified_generation": amplified_text,
            "generation_changed": baseline_text != amplified_text,
            "description": (
                f"Head amplification L{layer_idx}.H{head_idx} × {factor}: "
                f"{'CHANGES output' if baseline_text != amplified_text else 'no effect'}."
            ),
        }

    @torch.no_grad()
    def sweep(self, model, tokenizer, input_ids: torch.Tensor,
              layer_range: Optional[Tuple[int, int]] = None,
              top_k: int = 10) -> dict:
        """Ablate each head individually and rank by impact.

        This is the key command for finding "important" heads —
        heads whose ablation most changes the output distribution.
        """
        t = model.transformer
        n_blocks = len(t.h)
        n_heads = model.config.n_head
        head_dim = model.config.n_embd // n_heads
        tokens = tokenizer.convert_ids_to_tokens(input_ids[0].tolist())

        start_layer = layer_range[0] if layer_range else 0
        end_layer = layer_range[1] if layer_range else n_blocks

        # Baseline prediction
        baseline_logits, baseline_probs = self._get_final_prediction(
            model, input_ids)
        baseline_top = tokenizer.decode([baseline_probs.argmax().item()])
        baseline_top_id = baseline_probs.argmax().item()

        results = []

        for layer in range(start_layer, end_layer):
            block = t.h[layer]
            for head in range(n_heads):
                start = head * head_dim
                end = start + head_dim

                def _ablate(module, inp, out, s=start, e=end):
                    out = out.clone() if not isinstance(out, tuple) else (out[0].clone(), *out[1:])
                    target = out if not isinstance(out, tuple) else out[0]
                    target[:, :, s:e] = 0.0
                    return out

                hook = block.attn.c_proj.register_forward_hook(_ablate)
                _, ablated_probs = self._get_final_prediction(model, input_ids)
                hook.remove()

                kl = F.kl_div(
                    ablated_probs.log().unsqueeze(0),
                    baseline_probs.unsqueeze(0),
                    reduction='batchmean', log_target=False
                ).item()

                ablated_top = tokenizer.decode(
                    [ablated_probs.argmax().item()])
                prob_delta = (ablated_probs[baseline_top_id].item()
                              - baseline_probs[baseline_top_id].item())

                results.append({
                    "layer": layer,
                    "head": head,
                    "kl_divergence": round(kl, 6),
                    "prediction_changed": ablated_top != baseline_top,
                    "ablated_top_token": ablated_top,
                    "prob_delta": round(prob_delta, 6),
                })

        # Sort by impact (KL divergence)
        results.sort(key=lambda r: r["kl_divergence"], reverse=True)

        most_important = results[:top_k]
        least_important = results[-top_k:]
        pred_changers = [r for r in results if r["prediction_changed"]]

        return {
            "baseline_prediction": baseline_top,
            "total_heads_tested": len(results),
            "heads_that_change_prediction": len(pred_changers),
            "most_important": most_important,
            "least_important": least_important,
            "prediction_changers": pred_changers[:top_k],
            "description": (
                f"Head surgery sweep: ablated {len(results)} heads across "
                f"layers {start_layer}-{end_layer - 1}. "
                f"{len(pred_changers)} heads change the prediction when "
                f"ablated. Most important: L{most_important[0]['layer']}."
                f"H{most_important[0]['head']} "
                f"(KL={most_important[0]['kl_divergence']:.4f})."
                if most_important else "No heads tested."
            ),
        }

    def restore(self, model) -> dict:
        """Restore all saved weights (undo any permanent modifications)."""
        restored = 0
        for key, weight in self._saved_weights.items():
            parts = key.split(".")
            obj = model
            for p in parts[:-1]:
                obj = getattr(obj, p) if not p.isdigit() else obj[int(p)]
            param = getattr(obj, parts[-1])
            param.data.copy_(weight)
            restored += 1

        # Remove any active hooks
        for h in self._active_hooks:
            h.remove()
        self._active_hooks.clear()

        n_mods = len(self._modifications)
        self._modifications.clear()
        self._saved_weights.clear()

        return {
            "restored_weights": restored,
            "cleared_modifications": n_mods,
            "description": (
                f"Restored {restored} weight tensors, cleared {n_mods} "
                f"modification records."
                if restored or n_mods
                else "No modifications to restore."
            ),
        }

    def status(self) -> dict:
        """Return current modification status."""
        return {
            "active_modifications": len(self._modifications),
            "saved_weights": len(self._saved_weights),
            "active_hooks": len(self._active_hooks),
            "modifications": self._modifications,
        }

    # --- internal helpers ---

    @torch.no_grad()
    def _generate(self, model, tokenizer, input_ids: torch.Tensor,
                  n_tokens: int) -> str:
        """Generate n tokens greedily."""
        ids = input_ids.clone()
        generated = []
        for _ in range(n_tokens):
            logits = model(ids).logits
            next_id = logits[0, -1].argmax().unsqueeze(0).unsqueeze(0)
            generated.append(next_id.item())
            ids = torch.cat([ids, next_id], dim=1)
            if next_id.item() == tokenizer.eos_token_id:
                break
        return tokenizer.decode(generated)

    @torch.no_grad()
    def _get_final_prediction(self, model,
                              input_ids: torch.Tensor
                              ) -> Tuple[torch.Tensor, torch.Tensor]:
        """Get logits and probs for the last position."""
        logits = model(input_ids).logits[0, -1]
        probs = F.softmax(logits, dim=-1)
        return logits, probs
