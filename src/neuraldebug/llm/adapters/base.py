"""ModelAdapter ABC — unified interface for accessing model internals.

Every LLM debugging tool (Logit Lens, Activation Patching, Head Surgery,
Neuron Analysis, SAE, Hallucination Detection, Fine-Tuning) calls this
interface instead of directly accessing model attributes.  Each model
architecture (GPT-2, Llama, Mistral, Phi, …) provides its own adapter.

Users add support for a new model by subclassing ``ModelAdapter`` and
registering it with :class:`AdapterRegistry`.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ModelInfo:
    """Metadata about a loaded model."""

    name: str
    architecture: str          # e.g. "gpt2", "llama", "mistral"
    num_layers: int
    hidden_dim: int
    num_heads: int
    head_dim: int
    vocab_size: int
    ffn_dim: int
    is_causal: bool = True
    num_kv_heads: Optional[int] = None   # for GQA models (Llama-2 70B, etc.)
    extra: Optional[dict] = None         # model-specific metadata


class ModelAdapter(ABC):
    """Abstract interface that all LLM debugger code uses to access a model.

    Implementations wrap a concrete model (e.g. a HuggingFace
    ``PreTrainedModel``) and expose its internals through a uniform API.
    The debugger core, interpretability tools, and analysis modules never
    touch ``model.transformer.h[i]`` directly — they always go through
    this adapter.
    """

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @abstractmethod
    def info(self) -> ModelInfo:
        """Return model metadata (dimensions, layer count, etc.)."""

    @property
    def raw_model(self) -> Any:
        """Return the underlying framework model object.

        Useful when a tool genuinely needs direct access (e.g. for
        ``model.eval()`` or ``model.parameters()``).  Default
        implementations should store the model in ``self.model``.
        """
        return getattr(self, "model", None)

    # ------------------------------------------------------------------
    # Module access — used by hooks, surgery, neuron analysis
    # ------------------------------------------------------------------

    @abstractmethod
    def get_block(self, layer_idx: int) -> Any:
        """Return the transformer block module at *layer_idx*."""

    @abstractmethod
    def get_attention_output_proj(self, layer_idx: int) -> Any:
        """Return the attention output-projection module.

        This is the module where head-surgery hooks are registered
        (e.g. ``block.attn.c_proj`` for GPT-2, ``block.self_attn.o_proj``
        for Llama).
        """

    @abstractmethod
    def get_ffn_intermediate(self, layer_idx: int) -> Any:
        """Return the FFN up-projection / intermediate module.

        For GPT-2 this is ``block.mlp.c_fc``; for Llama it is
        ``block.mlp.gate_proj``.  Neuron analysis hooks attach here.
        """

    @abstractmethod
    def get_embedding(self) -> Any:
        """Return the token-embedding module."""

    @abstractmethod
    def get_final_norm(self) -> Any:
        """Return the final layer-normalisation module (before lm_head)."""

    @abstractmethod
    def get_lm_head(self) -> Any:
        """Return the language-model head (projects hidden → vocab logits)."""

    # ------------------------------------------------------------------
    # Forward-pass primitives — used by all interpretability tools
    # ------------------------------------------------------------------

    @abstractmethod
    def embed(self, input_ids: Any) -> Any:
        """Convert token IDs to initial hidden states.

        Must handle token embeddings, position embeddings, and any
        dropout / normalisation that occurs before the first block.

        Args:
            input_ids: Tensor of shape ``(batch, seq_len)``

        Returns:
            Hidden-state tensor of shape ``(batch, seq_len, hidden_dim)``
        """

    @abstractmethod
    def forward_block(self, hidden: Any, block_idx: int) -> Any:
        """Run a single transformer block.

        Args:
            hidden: ``(batch, seq_len, hidden_dim)``
            block_idx: Index of the block to execute.

        Returns:
            Updated hidden-state tensor (same shape).
        """

    @abstractmethod
    def apply_final_norm(self, hidden: Any) -> Any:
        """Apply the final normalisation layer (e.g. LayerNorm / RMSNorm)."""

    @abstractmethod
    def get_logits(self, normed_hidden: Any) -> Any:
        """Project normalised hidden states to vocabulary logits.

        Args:
            normed_hidden: Output of :meth:`apply_final_norm`.

        Returns:
            Logits tensor ``(batch, seq_len, vocab_size)``
        """

    # ------------------------------------------------------------------
    # Convenience: full forward pass
    # ------------------------------------------------------------------

    def full_forward(self, input_ids: Any) -> Any:
        """Run a complete forward pass: embed → blocks → norm → logits.

        Can be overridden for models that need a non-standard flow.
        """
        hidden = self.embed(input_ids)
        for i in range(self.info().num_layers):
            hidden = self.forward_block(hidden, i)
        normed = self.apply_final_norm(hidden)
        return self.get_logits(normed)

    def forward_blocks_range(self, hidden: Any,
                             start: int = 0,
                             end: Optional[int] = None) -> Any:
        """Run a contiguous range of transformer blocks.

        Useful for activation patching where you need to run only a
        subset of blocks.
        """
        if end is None:
            end = self.info().num_layers
        for i in range(start, end):
            hidden = self.forward_block(hidden, i)
        return hidden

    # ------------------------------------------------------------------
    # Layer graph — used by the stepper for breakpoints / stepping
    # ------------------------------------------------------------------

    @abstractmethod
    def get_layer_graph(self) -> Dict[str, List[str]]:
        """Return a map of canonical layer names to real module paths.

        Used by the execution stepper to build the stepping tree and
        by ``cmd_graph`` to show the architecture.

        Format matches the current ``_NODE_TO_MODULES`` dict, e.g.::

            {
                "embedding": ["model.embed_tokens"],
                "block_{i}.attn.ln_qkv": ["model.layers.{i}.input_layernorm",
                                           "model.layers.{i}.self_attn.q_proj",
                                           ...],
                ...
            }

        Patterns with ``{i}`` are expanded for each block.
        """

    # ------------------------------------------------------------------
    # Fine-tuning helpers
    # ------------------------------------------------------------------

    @abstractmethod
    def get_lora_target_modules(self) -> List[str]:
        """Return the default module names for LoRA fine-tuning.

        For GPT-2: ``["c_attn", "c_proj", "c_fc"]``
        For Llama: ``["q_proj", "v_proj", "k_proj", "up_proj", "down_proj"]``
        """

    # ------------------------------------------------------------------
    # FFN activation function (needed by neuron analysis)
    # ------------------------------------------------------------------

    def ffn_activation_fn(self, x: Any) -> Any:
        """Apply the FFN activation function to intermediate outputs.

        Default is GELU (used by GPT-2).  Override for SwiGLU (Llama),
        ReLU, etc.
        """
        import torch.nn.functional as F
        return F.gelu(x)
