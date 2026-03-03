"""Sparse Autoencoder (SAE) for LLM activation decomposition.

Trains a lightweight autoencoder with L1 sparsity on a layer's hidden
states, then decomposes activations into interpretable features.

Reference: "Scaling Monosemanticity" (Anthropic, 2024)
           "Towards Monosemanticity" (Bricken et al., 2023)
"""

import math
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F


class SparseAutoencoder(nn.Module):
    """Single-layer autoencoder with ReLU + L1 sparsity.

    Architecture:  input_dim → expansion × input_dim → input_dim
    The bottleneck features are sparse and (ideally) monosemantic.
    """

    def __init__(self, input_dim: int, expansion: int = 4):
        super().__init__()
        hidden_dim = input_dim * expansion
        self.encoder = nn.Linear(input_dim, hidden_dim, bias=True)
        self.decoder = nn.Linear(hidden_dim, input_dim, bias=True)
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.expansion = expansion

        # Xavier init for better training
        nn.init.xavier_uniform_(self.encoder.weight)
        nn.init.xavier_uniform_(self.decoder.weight)
        nn.init.zeros_(self.encoder.bias)
        nn.init.zeros_(self.decoder.bias)

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        """Encode input → sparse feature activations."""
        return F.relu(self.encoder(x))

    def decode(self, z: torch.Tensor) -> torch.Tensor:
        """Decode sparse features → reconstructed input."""
        return self.decoder(z)

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """Returns (reconstruction, feature_activations)."""
        z = self.encode(x)
        x_hat = self.decode(z)
        return x_hat, z


def _collect_activations(model, tokenizer, prompts: List[str],
                         layer_idx: int, adapter=None) -> torch.Tensor:
    """Run prompts through the model and collect hidden states at a layer.

    Returns tensor of shape (total_tokens, hidden_dim).

    Args:
        adapter: Optional ModelAdapter for architecture-agnostic access.
    """
    all_acts = []

    for prompt in prompts:
        ids = tokenizer.encode(prompt, return_tensors="pt")

        if adapter is not None:
            hidden = adapter.embed(ids)
            for i in range(adapter.info().num_layers):
                hidden = adapter.forward_block(hidden, i)
                if i == layer_idx:
                    all_acts.append(hidden[0].detach())
                    break
        else:
            t = model.transformer
            tok_emb = t.wte(ids)
            seq_len = ids.shape[1]
            pos_ids = torch.arange(seq_len, device=ids.device).unsqueeze(0)
            pos_emb = t.wpe(pos_ids)
            hidden = t.drop(tok_emb + pos_emb)
            for i, block in enumerate(t.h):
                hidden = block(hidden)[0]
                if i == layer_idx:
                    all_acts.append(hidden[0].detach())
                    break

    if not all_acts:
        hidden_dim = (adapter.info().hidden_dim if adapter
                      else model.config.n_embd)
        return torch.zeros(1, hidden_dim)

    return torch.cat(all_acts, dim=0)  # (total_tokens, hidden)


def train_sae(model, tokenizer, prompts: List[str], layer_idx: int,
              expansion: int = 4, num_steps: int = 200,
              lr: float = 1e-3, l1_coeff: float = 5e-3,
              ) -> Tuple[SparseAutoencoder, dict]:
    """Train a Sparse Autoencoder on activations from one layer.

    Args:
        model: GPT-2 model (frozen, eval mode)
        tokenizer: Tokenizer
        prompts: Training prompts (more = better feature quality)
        layer_idx: Which transformer block to analyze
        expansion: Feature expansion factor (4 = 4× more features than dims)
        num_steps: Training steps
        lr: Learning rate
        l1_coeff: L1 sparsity penalty weight

    Returns:
        (trained_sae, training_stats)
    """
    with torch.no_grad():
        acts = _collect_activations(model, tokenizer, prompts, layer_idx)

    input_dim = acts.shape[1]
    sae = SparseAutoencoder(input_dim, expansion)
    optimizer = torch.optim.Adam(sae.parameters(), lr=lr)

    # Normalize activations for stable training
    act_mean = acts.mean(dim=0, keepdim=True)
    act_std = acts.std(dim=0, keepdim=True).clamp(min=1e-6)
    acts_norm = (acts - act_mean) / act_std

    losses = []
    n_samples = acts_norm.shape[0]

    for step in range(num_steps):
        # Mini-batch (or full batch if small enough)
        if n_samples <= 512:
            batch = acts_norm
        else:
            idx = torch.randint(0, n_samples, (512,))
            batch = acts_norm[idx]

        x_hat, z = sae(batch)
        recon_loss = F.mse_loss(x_hat, batch)
        sparsity_loss = z.abs().mean()
        loss = recon_loss + l1_coeff * sparsity_loss

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        if step % 20 == 0 or step == num_steps - 1:
            losses.append({
                "step": step,
                "total_loss": round(loss.item(), 6),
                "recon_loss": round(recon_loss.item(), 6),
                "sparsity_loss": round(sparsity_loss.item(), 6),
            })

    # Compute feature statistics
    with torch.no_grad():
        _, all_z = sae(acts_norm)
        active_mask = (all_z > 0).float()
        feature_freq = active_mask.mean(dim=0)  # how often each feature fires
        n_active_per_sample = active_mask.sum(dim=1).mean().item()
        n_dead = (feature_freq < 0.01).sum().item()

    # Store normalization params for later decomposition
    sae._act_mean = act_mean
    sae._act_std = act_std
    sae._layer_idx = layer_idx

    stats = {
        "layer": f"block_{layer_idx}",
        "input_dim": input_dim,
        "hidden_dim": sae.hidden_dim,
        "expansion": expansion,
        "num_prompts": len(prompts),
        "total_tokens": n_samples,
        "training_steps": num_steps,
        "losses": losses,
        "final_recon_loss": losses[-1]["recon_loss"],
        "final_sparsity_loss": losses[-1]["sparsity_loss"],
        "avg_active_features": round(n_active_per_sample, 1),
        "dead_features": n_dead,
        "alive_features": sae.hidden_dim - n_dead,
        "sparsity_ratio": round(1.0 - n_active_per_sample / sae.hidden_dim, 4),
    }
    return sae, stats


@torch.no_grad()
def decompose_activation(sae: SparseAutoencoder, model, tokenizer,
                         input_ids: torch.Tensor, layer_idx: int,
                         position: int = -1, top_k: int = 10) -> dict:
    """Decompose an activation into sparse features.

    Args:
        sae: Trained SparseAutoencoder
        model: GPT-2 model
        tokenizer: Tokenizer
        input_ids: Token IDs (1, seq_len)
        layer_idx: Which block (must match SAE training)
        position: Token position to decompose (-1 = last)
        top_k: Number of top features to return
    """
    t = model.transformer
    tok_emb = t.wte(input_ids)
    seq_len = input_ids.shape[1]
    pos_ids = torch.arange(seq_len, device=input_ids.device).unsqueeze(0)
    pos_emb = t.wpe(pos_ids)
    hidden = t.drop(tok_emb + pos_emb)

    for i, block in enumerate(t.h):
        hidden = block(hidden)[0]
        if i == layer_idx:
            break

    if position < 0:
        position = seq_len + position

    act = hidden[0, position]  # (hidden_dim,)
    tokens = tokenizer.convert_ids_to_tokens(input_ids[0].tolist())

    # Normalize like training
    act_norm = (act - sae._act_mean.squeeze()) / sae._act_std.squeeze()
    z = sae.encode(act_norm.unsqueeze(0)).squeeze(0)  # (sae_hidden,)

    # Reconstruction quality
    x_hat = sae.decode(z.unsqueeze(0)).squeeze(0)
    recon_error = F.mse_loss(x_hat, act_norm).item()
    cos_sim = F.cosine_similarity(
        x_hat.unsqueeze(0), act_norm.unsqueeze(0)).item()

    # Top-k active features
    top_vals, top_idx = torch.topk(z, min(top_k, z.shape[0]))
    features = []
    for i in range(top_vals.shape[0]):
        if top_vals[i].item() <= 0:
            break
        feat_idx = top_idx[i].item()
        features.append({
            "feature_id": feat_idx,
            "activation": round(top_vals[i].item(), 4),
            "decoder_norm": round(
                sae.decoder.weight[:, feat_idx].norm().item(), 4),
        })

    n_active = (z > 0).sum().item()

    return {
        "position": position,
        "token": tokens[position] if position < len(tokens) else "?",
        "layer": f"block_{layer_idx}",
        "top_features": features,
        "active_features": n_active,
        "total_features": sae.hidden_dim,
        "sparsity": round(1.0 - n_active / sae.hidden_dim, 4),
        "reconstruction_mse": round(recon_error, 6),
        "reconstruction_cosine": round(cos_sim, 4),
        "description": (
            f"SAE decomposition at position {position} "
            f"('{tokens[position] if position < len(tokens) else '?'}'): "
            f"{n_active}/{sae.hidden_dim} features active "
            f"(sparsity {round(1.0 - n_active / sae.hidden_dim, 2):.0%}). "
            f"Reconstruction cosine similarity: {cos_sim:.3f}."
        ),
    }


@torch.no_grad()
def feature_dashboard(sae: SparseAutoencoder, model, tokenizer,
                      prompts: List[str], layer_idx: int,
                      feature_idx: int, top_k: int = 5) -> dict:
    """Build a dashboard for one SAE feature across prompts.

    Shows which tokens most activate this feature and what the feature
    "means" by examining its decoder direction.
    """
    t = model.transformer
    lm_head = model.lm_head.weight  # (vocab, embed)
    ln_f = t.ln_f

    # What tokens does this feature's decoder direction predict?
    decoder_dir = sae.decoder.weight[:, feature_idx]  # (input_dim,)
    # Un-normalize decoder direction
    decoder_dir_unnorm = decoder_dir * sae._act_std.squeeze()
    normed = ln_f(decoder_dir_unnorm.unsqueeze(0).unsqueeze(0))
    logits = F.linear(normed, lm_head).squeeze()
    probs = F.softmax(logits, dim=-1)
    top_probs, top_ids = torch.topk(probs, top_k)
    boosted_tokens = [
        {"token": tokenizer.decode([top_ids[i].item()]),
         "probability": round(top_probs[i].item(), 6)}
        for i in range(top_k)
    ]

    # Find tokens across prompts that most activate this feature
    top_activations = []
    for prompt in prompts:
        ids = tokenizer.encode(prompt, return_tensors="pt")
        tok_emb = t.wte(ids)
        seq_len = ids.shape[1]
        pos_ids = torch.arange(seq_len, device=ids.device).unsqueeze(0)
        pos_emb = t.wpe(pos_ids)
        hidden = t.drop(tok_emb + pos_emb)
        for i, block in enumerate(t.h):
            hidden = block(hidden)[0]
            if i == layer_idx:
                break

        acts = hidden[0]  # (seq, dim)
        acts_norm = (acts - sae._act_mean.squeeze()) / sae._act_std.squeeze()
        z = sae.encode(acts_norm)  # (seq, sae_hidden)
        feat_acts = z[:, feature_idx]  # (seq,)

        toks = tokenizer.convert_ids_to_tokens(ids[0].tolist())
        for pos in range(feat_acts.shape[0]):
            val = feat_acts[pos].item()
            if val > 0:
                top_activations.append({
                    "token": toks[pos] if pos < len(toks) else "?",
                    "activation": round(val, 4),
                    "prompt_snippet": prompt[:60],
                    "position": pos,
                })

    top_activations.sort(key=lambda x: x["activation"], reverse=True)
    top_activations = top_activations[:top_k * 2]

    return {
        "feature_id": feature_idx,
        "layer": f"block_{layer_idx}",
        "decoder_norm": round(
            sae.decoder.weight[:, feature_idx].norm().item(), 4),
        "boosted_tokens": boosted_tokens,
        "top_activations": top_activations,
        "description": (
            f"Feature #{feature_idx}: when active, it pushes the model "
            f"toward predicting '{boosted_tokens[0]['token']}' "
            f"(p={boosted_tokens[0]['probability']:.4f}). "
            f"Found {len(top_activations)} activating tokens across prompts."
        ),
    }
