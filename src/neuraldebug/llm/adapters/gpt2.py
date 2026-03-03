"""GPT-2 model adapter — wraps the HuggingFace GPT2LMHeadModel.

This adapter supports: distilgpt2, gpt2, gpt2-medium, gpt2-large, gpt2-xl,
and any model that shares the same ``model.transformer.h[i]`` structure
(e.g. fine-tuned variants, DistilGPT-2, CodeGen-Mono 350M).
"""

from typing import Any, Dict, List

import torch

from .base import ModelAdapter, ModelInfo


class GPT2Adapter(ModelAdapter):
    """Adapter for GPT-2 family models (HuggingFace ``GPT2LMHeadModel``)."""

    def __init__(self, model: Any):
        self.model = model
        self._t = model.transformer
        self._config = model.config

    # -- metadata ----------------------------------------------------------

    def info(self) -> ModelInfo:
        c = self._config
        return ModelInfo(
            name=getattr(c, "_name_or_path", "gpt2"),
            architecture="gpt2",
            num_layers=len(self._t.h),
            hidden_dim=c.n_embd,
            num_heads=c.n_head,
            head_dim=c.n_embd // c.n_head,
            vocab_size=c.vocab_size,
            ffn_dim=self._t.h[0].mlp.c_fc.weight.shape[0],
            is_causal=True,
        )

    # -- module access -----------------------------------------------------

    def get_block(self, layer_idx: int) -> Any:
        return self._t.h[layer_idx]

    def get_attention_output_proj(self, layer_idx: int) -> Any:
        return self._t.h[layer_idx].attn.c_proj

    def get_ffn_intermediate(self, layer_idx: int) -> Any:
        return self._t.h[layer_idx].mlp.c_fc

    def get_embedding(self) -> Any:
        return self._t.wte

    def get_final_norm(self) -> Any:
        return self._t.ln_f

    def get_lm_head(self) -> Any:
        return self.model.lm_head

    # -- forward pass ------------------------------------------------------

    def embed(self, input_ids: torch.Tensor) -> torch.Tensor:
        t = self._t
        seq_len = input_ids.shape[1]
        pos_ids = torch.arange(
            seq_len, device=input_ids.device).unsqueeze(0)
        return t.drop(t.wte(input_ids) + t.wpe(pos_ids))

    def forward_block(self, hidden: torch.Tensor,
                      block_idx: int) -> torch.Tensor:
        return self._t.h[block_idx](hidden)[0]

    def apply_final_norm(self, hidden: torch.Tensor) -> torch.Tensor:
        return self._t.ln_f(hidden)

    def get_logits(self, normed_hidden: torch.Tensor) -> torch.Tensor:
        return self.model.lm_head(normed_hidden)

    # -- layer graph -------------------------------------------------------

    def get_layer_graph(self) -> Dict[str, List[str]]:
        return {
            "embedding": [
                "transformer.wte", "transformer.wpe", "transformer.drop",
            ],
            "block_{i}.attn.ln_qkv": [
                "transformer.h.{i}.ln_1",
                "transformer.h.{i}.attn.c_attn",
            ],
            "block_{i}.attn.scores": [],
            "block_{i}.attn.output": [
                "transformer.h.{i}.attn.c_proj",
                "transformer.h.{i}.attn.resid_dropout",
            ],
            "block_{i}.ffn.ln_up": [
                "transformer.h.{i}.ln_2",
                "transformer.h.{i}.mlp.c_fc",
            ],
            "block_{i}.ffn.activation": [
                "transformer.h.{i}.mlp.act",
            ],
            "block_{i}.ffn.down_residual": [
                "transformer.h.{i}.mlp.c_proj",
                "transformer.h.{i}.mlp.dropout",
            ],
            "final_norm": ["transformer.ln_f"],
            "lm_head": ["lm_head"],
        }

    # -- fine-tuning -------------------------------------------------------

    def get_lora_target_modules(self) -> List[str]:
        return ["c_attn", "c_proj", "c_fc"]

    # -- FFN activation ----------------------------------------------------

    def ffn_activation_fn(self, x: torch.Tensor) -> torch.Tensor:
        import torch.nn.functional as F
        return F.gelu(x)
