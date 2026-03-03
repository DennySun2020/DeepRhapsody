"""Execution engine for stepping through a transformer forward pass.

Provides :class:`GPT2Stepper` — the layer-by-layer execution engine used by
:class:`debugger.LLMDebugger`.  Each step walks one layer (or sub-layer) of
the model and updates the :class:`InferenceContext` with live tensor data.

Supports any GPT-2 family model (distilgpt2 through gpt2-xl) by detecting
the architecture dynamically from ``model.config``.
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import torch
import torch.nn.functional as F


# ---------------------------------------------------------------------------
# Inference context — mutable state carried across the forward pass
# ---------------------------------------------------------------------------

@dataclass
class InferenceContext:
    """Live tensor state for the current inference pass.

    Updated in-place as the stepper walks through layers.  The debugger
    exposes every field here to ``cmd_evaluate`` expressions.
    """

    input_ids: torch.Tensor
    hidden_states: Optional[torch.Tensor] = None
    logits: Optional[torch.Tensor] = None

    # Attention data
    block_attn_weights: Optional[torch.Tensor] = None
    attention_maps: Dict[str, torch.Tensor] = field(default_factory=dict)
    query: Optional[torch.Tensor] = None
    key: Optional[torch.Tensor] = None
    value: Optional[torch.Tensor] = None

    # Model references
    model: Any = None
    tokenizer: Any = None

    # Prompt / generation tracking
    prompt_text: str = ""
    generated_text: str = ""
    generated_tokens: List[int] = field(default_factory=list)

    @property
    def prompt(self) -> str:
        """Alias kept for backward compatibility."""
        return self.prompt_text


# ---------------------------------------------------------------------------
# Layer-tree nodes
# ---------------------------------------------------------------------------

class LayerNode:
    """Single node in the model execution tree.

    Nodes form a tree that mirrors the model architecture:

    * **root** — the model
    * **embedding**, **block_0** … **block_N**, **final_norm**, **lm_head**
    * Each block contains **attention** and **ffn** sub-groups, which in turn
      contain leaf nodes for individual operations (ln_qkv, scores, …).
    """

    __slots__ = (
        "name", "display_name", "layer_type", "description",
        "parent", "children", "executed", "_depth",
    )

    def __init__(
        self,
        name: str,
        display_name: str,
        layer_type: str = "",
        description: str = "",
        parent: Optional["LayerNode"] = None,
    ):
        self.name = name
        self.display_name = display_name
        self.layer_type = layer_type
        self.description = description
        self.parent = parent
        self.children: List["LayerNode"] = []
        self.executed: bool = False
        self._depth: Optional[int] = None

    # -- properties --------------------------------------------------------

    @property
    def depth(self) -> int:
        if self._depth is None:
            self._depth = 0 if self.parent is None else self.parent.depth + 1
        return self._depth

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    # -- mutators ----------------------------------------------------------

    def add_child(
        self, name: str, display_name: str,
        layer_type: str = "", description: str = "",
    ) -> "LayerNode":
        child = LayerNode(name, display_name, layer_type, description,
                          parent=self)
        self.children.append(child)
        return child

    def mark_executed(self) -> None:
        """Mark this node **and all descendants** as executed."""
        self.executed = True
        for child in self.children:
            child.mark_executed()

    def reset(self) -> None:
        """Clear execution state for this subtree."""
        self.executed = False
        self._depth = None
        for child in self.children:
            child.reset()

    def __repr__(self) -> str:
        return f"LayerNode({self.name!r})"


# ---------------------------------------------------------------------------
# GPT2Stepper — the main execution engine
# ---------------------------------------------------------------------------

class GPT2Stepper:
    """Step through a GPT-2 forward pass layer by layer.

    Parameters
    ----------
    model : torch.nn.Module
        A HuggingFace ``GPT2LMHeadModel``.
    tokenizer
        The matching tokenizer.
    hook_manager
        A :class:`hooks.HookBackend` instance for registering forward hooks.
    """

    def __init__(self, model, tokenizer, hook_manager):
        self.model = model
        self.tokenizer = tokenizer
        self.hook_manager = hook_manager

        self.ctx: Optional[InferenceContext] = None
        self.breakpoints: Dict[str, bool] = {}
        self.is_started: bool = False
        self._forward_complete: bool = False

        # Model geometry (detected from config)
        cfg = model.config
        self._n_layers: int = cfg.n_layer
        self._n_embd: int = cfg.n_embd
        self._n_head: int = cfg.n_head
        self._head_dim: int = self._n_embd // self._n_head

        # Build execution tree and set navigation state
        self.root: LayerNode = self._build_tree()
        self.current: Optional[LayerNode] = None

        # Temporary per-block residual storage used by leaf-level steps
        self._attn_residual: Optional[torch.Tensor] = None
        self._ffn_residual: Optional[torch.Tensor] = None

    # ==================================================================
    # Tree construction
    # ==================================================================

    def _build_tree(self) -> LayerNode:
        """Construct the layer tree from the model architecture."""
        cfg = self.model.config
        n = self._n_layers
        d = self._n_embd
        d_ff = getattr(cfg, "n_inner", None) or 4 * d

        root = LayerNode(
            "model",
            f"GPT-2 ({getattr(cfg, 'model_type', 'gpt2')})",
            "GPT2LMHeadModel",
        )

        # -- embedding -----------------------------------------------------
        root.add_child(
            "embedding", "Token + Position Embedding", "Embedding",
            f"wte({cfg.vocab_size}×{d}) + wpe({cfg.n_positions}×{d})",
        )

        # -- transformer blocks --------------------------------------------
        for i in range(n):
            block = root.add_child(
                f"block_{i}",
                f"Transformer Block {i}/{n}",
                "GPT2Block",
            )

            # Attention sub-group
            attn = block.add_child(
                f"block_{i}.attention", "Multi-Head Attention",
                "GPT2Attention",
            )
            attn.add_child(
                f"block_{i}.attn.ln_qkv",
                "LayerNorm \u2192 Q/K/V Projection", "Linear",
                f"LayerNorm + c_attn [{d}\u2192{3 * d}]",
            )
            attn.add_child(
                f"block_{i}.attn.scores",
                "Attention Scores", "Softmax",
                f"{self._n_head} heads \u00d7 {self._head_dim}d, causal mask",
            )
            attn.add_child(
                f"block_{i}.attn.output",
                "Output Projection + Residual", "Linear",
                f"c_proj [{d}\u2192{d}] + residual",
            )

            # FFN sub-group
            ffn = block.add_child(
                f"block_{i}.ffn", "Feed-Forward Network", "GPT2MLP",
            )
            ffn.add_child(
                f"block_{i}.ffn.ln_up",
                "LayerNorm \u2192 Up Projection", "Linear",
                f"LayerNorm + c_fc [{d}\u2192{d_ff}]",
            )
            ffn.add_child(
                f"block_{i}.ffn.activation",
                "GELU Activation", "Activation",
                f"gelu_new [{d_ff}]",
            )
            ffn.add_child(
                f"block_{i}.ffn.down_residual",
                "Down Projection + Residual", "Linear",
                f"c_proj [{d_ff}\u2192{d}] + residual",
            )

        # -- final norm / lm_head -----------------------------------------
        root.add_child(
            "final_norm", "Final Layer Norm", "LayerNorm",
            f"LayerNorm [{d}]",
        )
        root.add_child(
            "lm_head", "Language Model Head", "Linear",
            f"Linear [{d}\u2192{cfg.vocab_size}] (tied weights)",
        )

        return root

    # ==================================================================
    # Session lifecycle
    # ==================================================================

    def start(self, prompt: str) -> None:
        """Tokenize *prompt*, run the embedding layer, pause at block 0."""
        device = next(self.model.parameters()).device
        input_ids = self.tokenizer.encode(prompt, return_tensors="pt")
        input_ids = input_ids.to(device)

        # Reset tree
        self.root.reset()

        self.ctx = InferenceContext(
            input_ids=input_ids,
            model=self.model,
            tokenizer=self.tokenizer,
            prompt_text=prompt,
        )
        self.is_started = True
        self._forward_complete = False

        # Execute embedding immediately
        with torch.no_grad():
            self._run_embedding()
        self._find_node("embedding").mark_executed()

        # Pause at the first transformer block
        self.current = self._find_node("block_0")

    # ==================================================================
    # Inspection
    # ==================================================================

    def inspect(self) -> dict:
        """Return current execution state in NeuralDebug protocol format."""
        if self.current is None:
            status = "completed" if self._forward_complete else "paused"
            msg = ("Forward pass complete — logits available."
                   if self._forward_complete else "No current position.")
            return {
                "status": status,
                "command": "inspect",
                "message": msg,
                "current_location": None,
                "call_stack": [],
                "local_variables": self._local_vars(),
                "stdout_new": "",
                "stderr_new": "",
            }

        return {
            "status": "paused",
            "command": "inspect",
            "message": f"Paused at {self.current.display_name}",
            "current_location": {
                "layer": self.current.name,
                "layer_type": self.current.layer_type,
                "display_name": self.current.display_name,
            },
            "call_stack": self._call_stack(),
            "local_variables": self._local_vars(),
            "stdout_new": "",
            "stderr_new": "",
        }

    def _call_stack(self) -> list:
        """Ancestor chain from current node up to (but excluding) root."""
        stack: list = []
        node = self.current
        while node is not None and node.parent is not None:
            stack.append({
                "layer": node.name,
                "layer_type": node.layer_type,
                "display_name": node.display_name,
            })
            node = node.parent
        stack.reverse()
        return stack

    def _local_vars(self) -> dict:
        """Summarize live tensors for the ``local_variables`` field."""
        if self.ctx is None:
            return {}
        vs: dict = {}
        if self.ctx.hidden_states is not None:
            vs["hidden_states"] = self._tensor_summary(self.ctx.hidden_states)
        if self.ctx.block_attn_weights is not None:
            vs["attention_weights"] = self._tensor_summary(
                self.ctx.block_attn_weights)
        if self.ctx.logits is not None:
            vs["logits"] = self._tensor_summary(self.ctx.logits)
        if self.ctx.query is not None:
            vs["query"] = self._tensor_summary(self.ctx.query)
        if self.ctx.key is not None:
            vs["key"] = self._tensor_summary(self.ctx.key)
        if self.ctx.value is not None:
            vs["value"] = self._tensor_summary(self.ctx.value)
        return vs

    @staticmethod
    def _tensor_summary(t: torch.Tensor) -> dict:
        f = t.detach().float()
        return {
            "shape": list(t.shape),
            "dtype": str(t.dtype),
            "mean": f.mean().item(),
            "std": f.std().item() if f.numel() > 1 else 0.0,
            "min": f.min().item(),
            "max": f.max().item(),
        }

    # ==================================================================
    # Stepping commands
    # ==================================================================

    def step_over(self) -> dict:
        """Execute the current node in full and advance to next sibling."""
        if self.current is None:
            return self.inspect()
        self._execute_whole(self.current)
        self._propagate_executed(self.current)
        self.current = self._advance(self.current)
        self._check_complete()
        return self.inspect()

    def step_in(self) -> dict:
        """Enter the current node's first child, or step-over if leaf."""
        if self.current is None:
            return self.inspect()
        if self.current.children:
            self.current = self.current.children[0]
            return self.inspect()
        return self.step_over()

    def step_out(self) -> dict:
        """Finish the current parent and advance past it."""
        if self.current is None:
            return self.inspect()

        parent = self.current.parent
        if parent is None or parent is self.root:
            return self.continue_()

        # Execute remaining un-executed siblings (including current)
        siblings = parent.children
        idx = siblings.index(self.current) if self.current in siblings else 0
        for sib in siblings[idx:]:
            if not sib.executed:
                self._execute_whole(sib)
        parent.mark_executed()

        self._propagate_executed(parent)
        self.current = self._advance(parent)
        self._check_complete()
        return self.inspect()

    def continue_(self) -> dict:
        """Run forward until a breakpoint is hit or the pass completes."""
        while self.current is not None:
            # Pause at a breakpoint (if not already executed)
            if (self.current.name in self.breakpoints
                    and not self.current.executed):
                return self.inspect()

            # If a *descendant* has a breakpoint, step into rather than
            # executing the whole subtree (mirrors real-debugger semantics).
            if self.current.children and self._descendant_has_bp(self.current):
                self.current = self.current.children[0]
                continue

            self._execute_whole(self.current)
            self._propagate_executed(self.current)
            self.current = self._advance(self.current)

            # Check breakpoint on the node we just arrived at
            if (self.current is not None
                    and self.current.name in self.breakpoints):
                return self.inspect()

        self._forward_complete = True
        return self.inspect()

    # ==================================================================
    # Token generation
    # ==================================================================

    def generate_next_token(self) -> dict:
        """Pick the top token from logits, append it, reset the pass."""
        if self.ctx is None or self.ctx.logits is None:
            return self.inspect()

        # Greedy decode: argmax of last-position logits
        next_id = self.ctx.logits[0, -1].argmax(dim=-1).item()
        token_text = self.tokenizer.decode([next_id])

        self.ctx.generated_tokens.append(next_id)
        self.ctx.generated_text += token_text

        # Extend input sequence
        device = self.ctx.input_ids.device
        new_id = torch.tensor([[next_id]], device=device)
        self.ctx.input_ids = torch.cat([self.ctx.input_ids, new_id], dim=1)

        # Reset forward-pass state
        self.ctx.hidden_states = None
        self.ctx.logits = None
        self.ctx.block_attn_weights = None
        self.ctx.query = None
        self.ctx.key = None
        self.ctx.value = None
        self._forward_complete = False
        self._attn_residual = None
        self._ffn_residual = None
        self.root.reset()

        # Re-run embedding for the extended sequence
        with torch.no_grad():
            self._run_embedding()
        self._find_node("embedding").mark_executed()
        self.current = self._find_node("block_0")

        return self.inspect()

    # ==================================================================
    # Tree navigation helpers
    # ==================================================================

    def _find_node(self, name: str) -> Optional[LayerNode]:
        """BFS lookup by node name."""
        queue: List[LayerNode] = [self.root]
        while queue:
            node = queue.pop(0)
            if node.name == name:
                return node
            queue.extend(node.children)
        return None

    def _advance(self, node: LayerNode) -> Optional[LayerNode]:
        """Return the next node to visit after *node* (sibling or uncle)."""
        cur = node
        while cur is not None:
            parent = cur.parent
            if parent is None:
                return None
            siblings = parent.children
            idx = siblings.index(cur)
            if idx + 1 < len(siblings):
                return siblings[idx + 1]
            cur = parent
        return None

    def _propagate_executed(self, node: LayerNode) -> None:
        """Walk upward, marking parents whose children are all done."""
        n = node.parent
        while n is not None and n is not self.root:
            if all(c.executed for c in n.children):
                n.executed = True
                n = n.parent
            else:
                break

    def _descendant_has_bp(self, node: LayerNode) -> bool:
        """True if any descendant of *node* has a breakpoint set."""
        for child in node.children:
            if child.name in self.breakpoints:
                return True
            if self._descendant_has_bp(child):
                return True
        return False

    def _check_complete(self) -> None:
        if self.current is None:
            self._forward_complete = True

    # ==================================================================
    # Node execution dispatch
    # ==================================================================

    def _execute_whole(self, node: LayerNode) -> None:
        """Execute *node* and all its descendants as one operation."""
        if node.executed:
            return

        name = node.name
        with torch.no_grad():
            # Top-level nodes
            if name == "embedding":
                self._run_embedding()
            elif name == "final_norm":
                self._run_final_norm()
            elif name == "lm_head":
                self._run_lm_head()
            # Block-level (whole block)
            elif name.startswith("block_") and "." not in name:
                self._run_block(self._block_idx(name))
            # Sub-group level
            elif name.endswith(".attention"):
                self._run_attention(self._block_idx(name))
            elif name.endswith(".ffn"):
                self._run_ffn(self._block_idx(name))
            # Attention leaf nodes
            elif ".attn.ln_qkv" in name:
                self._run_attn_ln_qkv(self._block_idx(name))
            elif ".attn.scores" in name:
                self._run_attn_scores(self._block_idx(name))
            elif ".attn.output" in name:
                self._run_attn_output(self._block_idx(name))
            # FFN leaf nodes
            elif ".ffn.ln_up" in name:
                self._run_ffn_ln_up(self._block_idx(name))
            elif ".ffn.activation" in name:
                self._run_ffn_activation(self._block_idx(name))
            elif ".ffn.down_residual" in name:
                self._run_ffn_down_residual(self._block_idx(name))

        node.mark_executed()

    @staticmethod
    def _block_idx(name: str) -> int:
        """Extract the block index from a node name like 'block_3.ffn'."""
        return int(name.split("_")[1].split(".")[0])

    # ==================================================================
    # Forward-pass primitives
    # ==================================================================

    # -- embedding ---------------------------------------------------------

    def _run_embedding(self) -> None:
        ids = self.ctx.input_ids
        seq_len = ids.size(1)
        device = ids.device

        pos_ids = torch.arange(seq_len, device=device).unsqueeze(0)
        hidden = (self.model.transformer.wte(ids)
                  + self.model.transformer.wpe(pos_ids))

        if hasattr(self.model.transformer, "drop"):
            hidden = self.model.transformer.drop(hidden)

        self.ctx.hidden_states = hidden

    # -- full block --------------------------------------------------------

    def _run_block(self, idx: int) -> None:
        block = self.model.transformer.h[idx]
        hidden = self.ctx.hidden_states

        # Capture attention weights via hook (fallback)
        captured: List[Optional[torch.Tensor]] = [None]

        def _hook(_mod, _inp, output):
            if isinstance(output, tuple) and len(output) >= 2:
                w = output[1]
                if isinstance(w, torch.Tensor) and w.dim() >= 3:
                    captured[0] = w

        handle = self.hook_manager.register_forward_hook(block.attn, _hook)
        try:
            outputs = block(hidden, output_attentions=True)
        finally:
            handle.remove()

        self.ctx.hidden_states = outputs[0]

        # GPT2Block returns (hidden, attn_weights) when output_attentions
        attn_w = outputs[1] if len(outputs) > 1 else captured[0]
        if attn_w is not None:
            self.ctx.block_attn_weights = attn_w
            self.ctx.attention_maps[f"block_{idx}"] = attn_w

    # -- final norm / lm_head ---------------------------------------------

    def _run_final_norm(self) -> None:
        self.ctx.hidden_states = self.model.transformer.ln_f(
            self.ctx.hidden_states)

    def _run_lm_head(self) -> None:
        self.ctx.logits = self.model.lm_head(self.ctx.hidden_states)

    # ==================================================================
    # Attention sub-steps (used by step-in into a block's attention)
    # ==================================================================

    def _run_attention(self, idx: int) -> None:
        """Full attention: ln_1 → attn module → residual add."""
        block = self.model.transformer.h[idx]
        residual = self.ctx.hidden_states

        hidden = block.ln_1(residual)
        attn_out = block.attn(hidden, output_attentions=True)
        attn_output = attn_out[0]

        if len(attn_out) > 1 and attn_out[1] is not None:
            self.ctx.block_attn_weights = attn_out[1]
            self.ctx.attention_maps[f"block_{idx}"] = attn_out[1]

        self.ctx.hidden_states = attn_output + residual

    def _run_ffn(self, idx: int) -> None:
        """Full FFN: ln_2 → MLP → residual add."""
        block = self.model.transformer.h[idx]
        residual = self.ctx.hidden_states

        hidden = block.ln_2(residual)
        ffn_output = block.mlp(hidden)
        self.ctx.hidden_states = ffn_output + residual

    # ==================================================================
    # Attention leaf steps (finest granularity)
    # ==================================================================

    def _run_attn_ln_qkv(self, idx: int) -> None:
        """LayerNorm → Q / K / V projection and head reshape."""
        block = self.model.transformer.h[idx]

        # Stash residual for the output step
        self._attn_residual = self.ctx.hidden_states.clone()

        hidden = block.ln_1(self.ctx.hidden_states)
        qkv = block.attn.c_attn(hidden)  # [batch, seq, 3·d]

        q, k, v = qkv.split(self._n_embd, dim=-1)

        batch, seq = q.shape[0], q.shape[1]
        shape = (batch, seq, self._n_head, self._head_dim)
        q = q.view(*shape).transpose(1, 2)  # [batch, heads, seq, head_dim]
        k = k.view(*shape).transpose(1, 2)
        v = v.view(*shape).transpose(1, 2)

        self.ctx.query = q
        self.ctx.key = k
        self.ctx.value = v

    def _run_attn_scores(self, idx: int) -> None:
        """Scaled dot-product attention with causal mask."""
        q, k, v = self.ctx.query, self.ctx.key, self.ctx.value
        if q is None or k is None or v is None:
            return

        scale = 1.0 / math.sqrt(self._head_dim)
        scores = torch.matmul(q, k.transpose(-2, -1)) * scale

        # Causal mask — lower-triangular
        seq_len = scores.size(-1)
        causal = torch.tril(
            torch.ones(seq_len, seq_len, device=scores.device,
                       dtype=torch.bool))
        scores = scores.masked_fill(~causal, float("-inf"))

        attn_weights = F.softmax(scores, dim=-1)
        self.ctx.block_attn_weights = attn_weights
        self.ctx.attention_maps[f"block_{idx}"] = attn_weights

        # Weighted sum of values
        attn_output = torch.matmul(attn_weights, v)

        # Merge heads → [batch, seq, n_embd]
        batch, _, seq, _ = attn_output.shape
        self.ctx.hidden_states = (
            attn_output.transpose(1, 2)
            .contiguous()
            .view(batch, seq, self._n_embd)
        )

    def _run_attn_output(self, idx: int) -> None:
        """Output projection + first residual connection."""
        block = self.model.transformer.h[idx]
        projected = block.attn.c_proj(self.ctx.hidden_states)

        if hasattr(block.attn, "resid_dropout"):
            projected = block.attn.resid_dropout(projected)

        residual = self._attn_residual
        if residual is not None:
            self.ctx.hidden_states = projected + residual
            self._attn_residual = None
        else:
            self.ctx.hidden_states = projected

    # ==================================================================
    # FFN leaf steps (finest granularity)
    # ==================================================================

    def _run_ffn_ln_up(self, idx: int) -> None:
        """LayerNorm → up-projection (c_fc)."""
        block = self.model.transformer.h[idx]

        self._ffn_residual = self.ctx.hidden_states.clone()

        hidden = block.ln_2(self.ctx.hidden_states)
        self.ctx.hidden_states = block.mlp.c_fc(hidden)

    def _run_ffn_activation(self, idx: int) -> None:
        """Activation function (GELU)."""
        block = self.model.transformer.h[idx]
        self.ctx.hidden_states = block.mlp.act(self.ctx.hidden_states)

    def _run_ffn_down_residual(self, idx: int) -> None:
        """Down-projection (c_proj) + second residual connection."""
        block = self.model.transformer.h[idx]
        projected = block.mlp.c_proj(self.ctx.hidden_states)

        if hasattr(block.mlp, "dropout"):
            projected = block.mlp.dropout(projected)

        residual = self._ffn_residual
        if residual is not None:
            self.ctx.hidden_states = projected + residual
            self._ffn_residual = None
        else:
            self.ctx.hidden_states = projected


__all__ = ["GPT2Stepper", "InferenceContext", "LayerNode"]
