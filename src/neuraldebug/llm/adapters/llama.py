"""Llama / Llama-2 / Llama-3 / Code Llama model adapter.

Also works for architecturally similar models: Mistral, Mixtral, Yi,
Qwen-2, DeepSeek-V2, and others that use the HuggingFace
``LlamaForCausalLM`` layout (``model.model.layers``).
"""

from typing import Any, Dict, List, Optional

import torch
import torch.nn.functional as F

from .base import ModelAdapter, ModelInfo


class LlamaAdapter(ModelAdapter):
    """Adapter for Llama-family models (HuggingFace ``LlamaForCausalLM``)."""

    def __init__(self, model: Any):
        self.model = model
        self._inner = model.model          # LlamaModel inside LlamaForCausalLM
        self._layers = self._inner.layers
        self._config = model.config

    # -- metadata ----------------------------------------------------------

    def info(self) -> ModelInfo:
        c = self._config
        num_heads = c.num_attention_heads
        head_dim = c.hidden_size // num_heads
        num_kv = getattr(c, "num_key_value_heads", num_heads)
        return ModelInfo(
            name=getattr(c, "_name_or_path", "llama"),
            architecture="llama",
            num_layers=c.num_hidden_layers,
            hidden_dim=c.hidden_size,
            num_heads=num_heads,
            head_dim=head_dim,
            vocab_size=c.vocab_size,
            ffn_dim=c.intermediate_size,
            is_causal=True,
            num_kv_heads=num_kv if num_kv != num_heads else None,
        )

    # -- module access -----------------------------------------------------

    def get_block(self, layer_idx: int) -> Any:
        return self._layers[layer_idx]

    def get_attention_output_proj(self, layer_idx: int) -> Any:
        return self._layers[layer_idx].self_attn.o_proj

    def get_ffn_intermediate(self, layer_idx: int) -> Any:
        return self._layers[layer_idx].mlp.gate_proj

    def get_embedding(self) -> Any:
        return self._inner.embed_tokens

    def get_final_norm(self) -> Any:
        return self._inner.norm

    def get_lm_head(self) -> Any:
        return self.model.lm_head

    # -- forward pass ------------------------------------------------------

    def embed(self, input_ids: torch.Tensor) -> torch.Tensor:
        return self._inner.embed_tokens(input_ids)

    def forward_block(self, hidden: torch.Tensor,
                      block_idx: int) -> torch.Tensor:
        layer = self._layers[block_idx]
        # Llama layers return (hidden, self_attn_weights, present_kv_cache)
        out = layer(hidden)
        return out[0]

    def apply_final_norm(self, hidden: torch.Tensor) -> torch.Tensor:
        return self._inner.norm(hidden)

    def get_logits(self, normed_hidden: torch.Tensor) -> torch.Tensor:
        return self.model.lm_head(normed_hidden)

    # -- layer graph -------------------------------------------------------

    def get_layer_graph(self) -> Dict[str, List[str]]:
        return {
            "embedding": ["model.embed_tokens"],
            "block_{i}.attn.ln_qkv": [
                "model.layers.{i}.input_layernorm",
                "model.layers.{i}.self_attn.q_proj",
                "model.layers.{i}.self_attn.k_proj",
                "model.layers.{i}.self_attn.v_proj",
            ],
            "block_{i}.attn.scores": [],
            "block_{i}.attn.output": [
                "model.layers.{i}.self_attn.o_proj",
            ],
            "block_{i}.ffn.ln_up": [
                "model.layers.{i}.post_attention_layernorm",
                "model.layers.{i}.mlp.gate_proj",
                "model.layers.{i}.mlp.up_proj",
            ],
            "block_{i}.ffn.activation": [],
            "block_{i}.ffn.down_residual": [
                "model.layers.{i}.mlp.down_proj",
            ],
            "final_norm": ["model.norm"],
            "lm_head": ["lm_head"],
        }

    # -- fine-tuning -------------------------------------------------------

    def get_lora_target_modules(self) -> List[str]:
        return ["q_proj", "k_proj", "v_proj", "o_proj",
                "gate_proj", "up_proj", "down_proj"]

    # -- FFN activation (SwiGLU) -------------------------------------------

    def ffn_activation_fn(self, x: torch.Tensor) -> torch.Tensor:
        return F.silu(x)
