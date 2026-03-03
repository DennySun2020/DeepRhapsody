"""LLM debugger implementing the standard NeuralDebug command protocol.

Provides ``LLMDebugger`` — the class that the TCP debug-server delegates
every ``cmd_*`` call to.  It wraps a :class:`stepper.GPT2Stepper` and
translates debug commands into layer-stepping operations.

Supports **any** transformer architecture through the
:class:`adapters.ModelAdapter` abstraction — GPT-2, Llama, Mistral, Phi,
and custom models.  The adapter is selected automatically or via CLI flag.
"""

import json
import torch
import torch.nn.functional as F
from typing import Optional

from hooks import HookManager, compute_tensor_stats
from stepper import GPT2Stepper, InferenceContext
from interpretability import LogitLens, ActivationPatching, AttentionAnalysis, Probing
from head_surgery import HeadSurgeon
from tool_forge import ToolForge, ValidationError

# New abstraction layers
from adapters import AdapterRegistry, ModelAdapter
from hooks import PyTorchHookBackend


def _shape_str(shape: list) -> str:
    """Format a weight shape as e.g. '[1024×3072]'."""
    return "[" + "\u00d7".join(str(s) for s in shape) + "]"


class LLMDebugger:
    """Debugger for LLM reasoning — same interface as traditional debuggers.

    Loaded by the debug-server at startup (``start_debugger``), then
    receives ``cmd_*`` calls for every TCP command.

    Supports any model architecture via the ``adapter`` parameter.
    If no adapter is provided, one is auto-detected from the loaded model.
    """

    def __init__(self, model_name: str = "distilgpt2",
                 adapter_name: str = "auto",
                 device: str = "auto"):
        self.model_name = model_name
        self._adapter_name = adapter_name
        self._device_spec = device
        self.model = None
        self.tokenizer = None
        self.adapter: Optional[ModelAdapter] = None
        self.hook_backend = PyTorchHookBackend()
        self.stepper: Optional[GPT2Stepper] = None
        self.hook_manager = HookManager()
        self.is_finished = False
        self._head_surgeon = HeadSurgeon()
        self._trained_saes: dict = {}  # layer_idx -> SparseAutoencoder
        self._tool_forge = ToolForge()

    # -- lifecycle ---------------------------------------------------------

    def start_debugger(self):
        """Called once by BaseDebugServer.run() — loads the model.

        If a fine-tuned version of the model has been saved to disk by a
        previous ``finetune`` session, it is loaded automatically instead
        of the vanilla HuggingFace weights.

        After loading, an appropriate :class:`ModelAdapter` is selected
        (auto-detected or by name).
        """
        import os
        # Prevent transformers from trying to import TensorFlow (hangs when
        # both TF and PyTorch are installed)
        os.environ["TRANSFORMERS_NO_TF"] = "1"
        os.environ["USE_TORCH"] = "1"
        from transformers import AutoModelForCausalLM, AutoTokenizer

        # Check for a persisted fine-tuned version first
        from finetuner import has_finetuned_model, get_finetuned_model_dir
        load_from = self.model_name
        if has_finetuned_model(self.model_name):
            ft_dir = str(get_finetuned_model_dir(self.model_name))
            print(f"Found fine-tuned weights for '{self.model_name}'")
            print(f"  Loading from: {ft_dir}")
            load_from = ft_dir
        else:
            print(f"Loading model '{self.model_name}' …")

        self.tokenizer = AutoTokenizer.from_pretrained(load_from)
        self.model = AutoModelForCausalLM.from_pretrained(
            load_from,
            attn_implementation="eager",  # ensure attention weights available
        )
        self.model.eval()

        # Select model adapter (auto-detect or explicit)
        if self._adapter_name == "auto":
            self.adapter = AdapterRegistry.auto_detect(self.model)
        else:
            self.adapter = AdapterRegistry.from_name(
                self._adapter_name, self.model)

        model_info = self.adapter.info()
        print(f"  Architecture: {model_info.architecture} "
              f"(adapter: {type(self.adapter).__name__})")

        self.hook_manager.register_on_model(self.model)
        self.stepper = GPT2Stepper(
            self.model, self.tokenizer, self.hook_manager)

        n_params = sum(p.numel() for p in self.model.parameters())
        source = "fine-tuned" if load_from != self.model_name else "base"
        print(f"Model loaded ({source}): {n_params:,} parameters, "
              f"{model_info.num_layers} transformer blocks.")

    # -- commands ----------------------------------------------------------

    def cmd_start(self, args: str) -> dict:
        prompt = args.strip().strip('"').strip("'")
        if not prompt:
            return self._error("Usage: start <prompt text>")
        self.stepper.start(prompt)
        return self.stepper.inspect()

    def cmd_continue(self) -> dict:
        if self.stepper._forward_complete:
            return self.stepper.generate_next_token()
        return self.stepper.continue_()

    def cmd_step_in(self) -> dict:
        if self.stepper._forward_complete:
            return self.stepper.generate_next_token()
        return self.stepper.step_in()

    def cmd_step_over(self) -> dict:
        if self.stepper._forward_complete:
            return self.stepper.generate_next_token()
        return self.stepper.step_over()

    def cmd_step_out(self) -> dict:
        if self.stepper._forward_complete:
            return self.stepper.generate_next_token()
        return self.stepper.step_out()

    def cmd_set_breakpoint(self, args: str) -> dict:
        name = args.strip()
        if not name:
            return self._error(
                "Usage: b <layer_name>  (e.g. block_3, block_2.attention, "
                "lm_head)")
        self.stepper.breakpoints[name] = True
        return {
            "status": "ok",
            "message": f"Breakpoint set on layer '{name}'",
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_remove_breakpoint(self, args: str) -> dict:
        name = args.strip()
        if name in self.stepper.breakpoints:
            del self.stepper.breakpoints[name]
            return {
                "status": "ok",
                "message": f"Breakpoint removed: '{name}'",
                "current_location": None, "call_stack": [],
                "local_variables": {}, "stdout_new": "", "stderr_new": "",
            }
        return self._error(f"No breakpoint on '{name}'")

    def cmd_list_breakpoints(self) -> dict:
        bps = list(self.stepper.breakpoints.keys())
        return {
            "status": "ok",
            "message": f"Breakpoints ({len(bps)}): "
                       + (", ".join(bps) if bps else "(none)"),
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_inspect(self) -> dict:
        return self.stepper.inspect()

    def cmd_evaluate(self, args: str) -> dict:
        """Evaluate a Python / PyTorch expression on current tensors."""
        expr = args.strip()
        if not expr:
            return self._error("Usage: evaluate <expression>")
        ctx = self.stepper.ctx
        ns = {
            "hidden_states": ctx.hidden_states,
            "logits": ctx.logits,
            "input_ids": ctx.input_ids,
            "attention_weights": ctx.block_attn_weights,
            "attention_maps": ctx.attention_maps,
            "query": ctx.query, "key": ctx.key, "value": ctx.value,
            "model": ctx.model,
            "tokenizer": ctx.tokenizer,
            "torch": torch, "F": F,
            # safe builtins
            "abs": abs, "len": len, "min": min, "max": max,
            "sum": sum, "round": round, "list": list, "tuple": tuple,
            "dict": dict, "str": str, "int": int, "float": float,
            "range": range, "type": type, "print": print,
        }
        try:
            result = eval(expr, {"__builtins__": {}}, ns)
            if isinstance(result, torch.Tensor):
                stats = compute_tensor_stats(result)
                result_str = json.dumps(stats.to_dict())
            else:
                result_str = repr(result)
            return {
                "status": "ok",
                "message": f"= {result_str}",
                "current_location": None, "call_stack": [],
                "local_variables": {"result": result_str},
                "stdout_new": "", "stderr_new": "",
            }
        except Exception as exc:
            return self._error(f"Evaluation error: {exc}")

    def cmd_list_source(self, args: str) -> dict:
        """Show model architecture around current position."""
        current = self.stepper.current
        parent = (current.parent if current else None) or self.stepper.root
        lines = []
        for child in parent.children:
            marker = ">>>" if child == current else "   "
            ex = "\u2713" if child.executed else "\u25CB"
            bp = "\u25CF" if child.name in self.stepper.breakpoints else " "
            lines.append(
                f" {marker} {bp} {ex}  [{child.name}] "
                f"{child.display_name}  ({child.layer_type})")
            if child.children:
                for sub in child.children:
                    m2 = "  >>>" if sub == current else "     "
                    e2 = "\u2713" if sub.executed else "\u25CB"
                    lines.append(
                        f"   {m2} {e2}  [{sub.name}] {sub.display_name}")
        return {
            "status": "ok",
            "message": "\n".join(lines),
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_backtrace(self) -> dict:
        if self.stepper.current is None:
            return self._error("No current position")
        stack = []
        node = self.stepper.current
        while node.parent is not None:
            stack.append(
                f"  [{node.name}] {node.display_name} ({node.layer_type})")
            node = node.parent
        stack.reverse()
        return {
            "status": "ok",
            "message": "Layer stack:\n" + "\n".join(stack),
            "current_location": None,
            "call_stack": [{"layer": s.strip()} for s in stack],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    def cmd_graph(self, args: str) -> dict:
        """Dump the full model compute graph as a tree.

        Supports output formats controlled by *args*:
        - ``graph``            → ASCII tree (default, compact)
        - ``graph detailed``   → detailed tree with shapes, params, modules
        - ``graph json``       → structured JSON with full metadata
        - ``graph mermaid``    → Mermaid flowchart syntax
        """
        root = self.stepper.root if self.stepper else None
        if root is None:
            return self._error(
                "No model loaded. Send 'start <prompt>' first.")

        fmt = args.strip().lower() if args else "ascii"

        if fmt == "json":
            module_info = self._collect_module_info()
            tree = self._graph_to_dict(root, module_info)
            import json as _json
            return {
                "status": "ok",
                "message": _json.dumps(tree, indent=2),
                "current_location": None, "call_stack": [],
                "local_variables": {"graph": tree},
                "stdout_new": "", "stderr_new": "",
            }
        elif fmt == "mermaid":
            lines = ["graph TD"]
            self._graph_to_mermaid(root, lines)
            diagram = "\n".join(lines)
            return {
                "status": "ok",
                "message": diagram,
                "current_location": None, "call_stack": [],
                "local_variables": {"format": "mermaid", "diagram": diagram},
                "stdout_new": "", "stderr_new": "",
            }
        elif fmt in ("detailed", "detail", "verbose", "full"):
            module_info = self._collect_module_info()
            lines = []
            self._graph_to_ascii_detailed(root, lines, module_info,
                                          prefix="", is_last=True)
            summary = self._graph_summary_detailed(root, module_info)
            text = "\n".join(lines) + "\n\n" + summary
            return {
                "status": "ok",
                "message": text,
                "current_location": None, "call_stack": [],
                "local_variables": {
                    "format": "detailed",
                    "total_nodes": self._count_nodes(root),
                    "total_leaves": self._count_leaves(root),
                    "model_stats": self._model_stats(),
                },
                "stdout_new": "", "stderr_new": "",
            }
        else:
            lines = []
            self._graph_to_ascii(root, lines, prefix="", is_last=True)
            summary = self._graph_summary(root)
            text = "\n".join(lines) + "\n\n" + summary
            return {
                "status": "ok",
                "message": text,
                "current_location": None, "call_stack": [],
                "local_variables": {
                    "format": "ascii",
                    "total_nodes": self._count_nodes(root),
                    "total_leaves": self._count_leaves(root),
                },
                "stdout_new": "", "stderr_new": "",
            }

    # -- graph helpers -----------------------------------------------------

    _NODE_TO_MODULES = None  # populated dynamically from adapter

    def _get_node_to_modules(self) -> dict:
        """Return the layer-name → module-path mapping.

        Uses the adapter's ``get_layer_graph()`` if available, falling
        back to the legacy GPT-2 hardcoded dict for compatibility.
        """
        if self.adapter is not None:
            return self.adapter.get_layer_graph()
        # Legacy fallback (should not be reached with adapter)
        return {
            "embedding": ["transformer.wte", "transformer.wpe", "transformer.drop"],
            "block_{i}.attn.ln_qkv": ["transformer.h.{i}.ln_1",
                                       "transformer.h.{i}.attn.c_attn"],
            "block_{i}.attn.scores": [],
            "block_{i}.attn.output": ["transformer.h.{i}.attn.c_proj",
                                      "transformer.h.{i}.attn.resid_dropout"],
            "block_{i}.ffn.ln_up": ["transformer.h.{i}.ln_2",
                                    "transformer.h.{i}.mlp.c_fc"],
            "block_{i}.ffn.activation": ["transformer.h.{i}.mlp.act"],
            "block_{i}.ffn.down_residual": ["transformer.h.{i}.mlp.c_proj",
                                            "transformer.h.{i}.mlp.dropout"],
            "final_norm": ["transformer.ln_f"],
            "lm_head": ["lm_head"],
        }

    def _collect_module_info(self) -> dict:
        """Map ExecNode names to PyTorch module metadata (shapes, params)."""
        model = self.model
        if model is None:
            return {}

        # Leaf-level map: direct params only (recurse=False)
        module_map = {}
        # Subtree-level map: all params (recurse=True) for aggregate nodes
        module_map_recursive = {}
        for name, mod in model.named_modules():
            params = list(mod.named_parameters(recurse=False))
            total_p = sum(p.numel() for _, p in params)
            shapes = {}
            for pname, p in params:
                shapes[pname] = list(p.shape)
            module_map[name] = {
                "class": type(mod).__name__,
                "param_count": total_p,
                "weight_shapes": shapes,
            }
            all_p = sum(p.numel() for p in mod.parameters())
            module_map_recursive[name] = {"param_count": all_p}

        info = {}
        config = getattr(model, "config", None)
        num_blocks = (self.adapter.info().num_layers
                      if self.adapter else
                      getattr(config, "n_layer", 0) if config else 0)

        node_to_modules = self._get_node_to_modules()
        for node_key, mod_paths in node_to_modules.items():
            if "{i}" in node_key:
                for i in range(num_blocks):
                    nk = node_key.replace("{i}", str(i))
                    mps = [p.replace("{i}", str(i)) for p in mod_paths]
                    info[nk] = self._aggregate_module_info(module_map, mps)
            else:
                info[node_key] = self._aggregate_module_info(
                    module_map, mod_paths)

        for i in range(num_blocks):
            bk = f"block_{i}"
            # Use adapter to find the real module path for each block
            block_mod = self.adapter.get_block(i) if self.adapter else None
            if block_mod is not None:
                block_name = None
                for n, m in model.named_modules():
                    if m is block_mod:
                        block_name = n
                        break
                if block_name:
                    info[bk] = self._aggregate_module_info(
                        module_map_recursive, [block_name])
                    attn_mod = self.adapter.get_attention_output_proj(i)
                    if attn_mod is not None:
                        # Walk up to the parent attention module
                        for n, m in model.named_modules():
                            if m is attn_mod:
                                attn_parent = ".".join(n.split(".")[:-1])
                                info[f"{bk}.attention"] = self._aggregate_module_info(
                                    module_map_recursive, [attn_parent])
                                break
                    ffn_mod = self.adapter.get_ffn_intermediate(i)
                    if ffn_mod is not None:
                        for n, m in model.named_modules():
                            if m is ffn_mod:
                                ffn_parent = ".".join(n.split(".")[:-1])
                                info[f"{bk}.ffn"] = self._aggregate_module_info(
                                    module_map_recursive, [ffn_parent])
                                break
            else:
                # Legacy fallback
                info[bk] = self._aggregate_module_info(
                    module_map_recursive, [f"transformer.h.{i}"])
                info[f"{bk}.attention"] = self._aggregate_module_info(
                    module_map_recursive, [f"transformer.h.{i}.attn"])
                info[f"{bk}.ffn"] = self._aggregate_module_info(
                    module_map_recursive, [f"transformer.h.{i}.mlp"])

        info["root"] = {
            "pytorch_modules": [],
            "param_count": sum(p.numel() for p in model.parameters()),
            "weight_shapes": {},
        }

        return info

    @staticmethod
    def _aggregate_module_info(module_map, paths) -> dict:
        """Combine info from multiple PyTorch modules into one record."""
        result = {"pytorch_modules": paths, "param_count": 0,
                  "weight_shapes": {}}
        for p in paths:
            entry = module_map.get(p, {})
            result["param_count"] += entry.get("param_count", 0)
            for wn, ws in entry.get("weight_shapes", {}).items():
                result["weight_shapes"][f"{p}.{wn}"] = ws
        return result

    def _graph_to_ascii(self, node, lines, prefix="", is_last=True):
        """Recursively build a compact ASCII tree representation."""
        connector = "\u2514\u2500\u2500 " if is_last else "\u251C\u2500\u2500 "
        current = self.stepper.current if self.stepper else None

        marker = "\u25B6 " if node == current else ""
        status = ""
        if node.executed:
            status = " \u2713"
        elif node.name in (self.stepper.breakpoints if self.stepper else set()):
            status = " \u25CF"

        type_tag = f"  ({node.layer_type})" if node.layer_type else ""
        line = f"{prefix}{connector}{marker}{node.display_name}{type_tag}{status}"
        lines.append(line)

        child_prefix = prefix + ("    " if is_last else "\u2502   ")
        for i, child in enumerate(node.children):
            self._graph_to_ascii(
                child, lines, child_prefix,
                is_last=(i == len(node.children) - 1))

    def _graph_to_ascii_detailed(self, node, lines, module_info,
                                 prefix="", is_last=True):
        """Build a detailed ASCII tree with shapes, params, and modules."""
        connector = "\u2514\u2500\u2500 " if is_last else "\u251C\u2500\u2500 "
        current = self.stepper.current if self.stepper else None
        info = module_info.get(node.name, {})

        marker = "\u25B6 " if node == current else ""
        status = ""
        if node.executed:
            status = " \u2713"
        elif node.name in (self.stepper.breakpoints if self.stepper else set()):
            status = " \u25CF"

        type_tag = f"  ({node.layer_type})" if node.layer_type else ""
        params = info.get("param_count", 0)
        param_str = f"  [{self._format_param_count(params)}]" if params else ""
        line = (f"{prefix}{connector}{marker}"
                f"{node.display_name}{type_tag}{param_str}{status}")
        lines.append(line)

        child_prefix = prefix + ("    " if is_last else "\u2502   ")

        # Show description for leaf nodes
        if node.description and node.is_leaf:
            lines.append(f"{child_prefix}\u2502 {node.description}")

        # Show PyTorch module paths and weight shapes
        mods = info.get("pytorch_modules", [])
        shapes = info.get("weight_shapes", {})
        if mods and node.is_leaf:
            lines.append(
                f"{child_prefix}\u2502 Modules: {', '.join(mods)}")
        if shapes and node.is_leaf:
            for wname, wshape in shapes.items():
                short = wname.rsplit(".", 1)[-1]
                lines.append(
                    f"{child_prefix}\u2502 {short}: {_shape_str(wshape)}")

        # Show data flow annotation
        flow = self._data_flow_annotation(node)
        if flow and node.is_leaf:
            lines.append(f"{child_prefix}\u2502 Flow: {flow}")

        for i, child in enumerate(node.children):
            self._graph_to_ascii_detailed(
                child, lines, module_info, child_prefix,
                is_last=(i == len(node.children) - 1))

    def _data_flow_annotation(self, node) -> str:
        """Return a human-readable data-flow description for a node."""
        config = getattr(self.model, "config", None) if self.model else None
        if not config:
            return ""
        d = getattr(config, "n_embd", 0)
        n_head = getattr(config, "n_head", 0)
        vocab = getattr(config, "vocab_size", 0)
        n_pos = getattr(config, "n_positions", 0)
        d_ff = getattr(config, "n_inner", None) or (4 * d)
        head_dim = d // n_head if n_head else 0
        drop = getattr(config, "resid_pdrop", 0.0)

        name = node.name
        if name == "embedding":
            return (f"token_ids \u2192 wte[{vocab}\u00d7{d}] + "
                    f"wpe[{n_pos}\u00d7{d}] \u2192 [{d}]  "
                    f"(dropout={getattr(config, 'embd_pdrop', drop)})")
        if name.endswith(".attn.ln_qkv"):
            return (f"[{d}] \u2192 LayerNorm \u2192 c_attn[{d}\u2192{3*d}] "
                    f"\u2192 Q[{d}], K[{d}], V[{d}]")
        if name.endswith(".attn.scores"):
            return (f"Q,K,V split into {n_head} heads \u00d7 {head_dim}d "
                    f"\u2192 softmax(QK\u1d40/\u221a{head_dim}) \u2192 "
                    f"weighted V  (causal mask)")
        if name.endswith(".attn.output"):
            return (f"concat heads \u2192 c_proj[{d}\u2192{d}] + "
                    f"residual  (dropout={drop})")
        if name.endswith(".ffn.ln_up"):
            return (f"[{d}] \u2192 LayerNorm \u2192 "
                    f"c_fc[{d}\u2192{d_ff}]")
        if name.endswith(".ffn.activation"):
            act = getattr(config, "activation_function", "gelu_new")
            return f"[{d_ff}] \u2192 {act} \u2192 [{d_ff}]"
        if name.endswith(".ffn.down_residual"):
            return (f"c_proj[{d_ff}\u2192{d}] + residual  "
                    f"(dropout={drop})")
        if name == "final_norm":
            return f"[{d}] \u2192 LayerNorm \u2192 [{d}]"
        if name == "lm_head":
            return (f"[{d}] \u2192 Linear[{d}\u2192{vocab}] \u2192 "
                    f"logits  (weight tied to wte)")
        return ""

    def _graph_to_dict(self, node, module_info=None) -> dict:
        """Recursively convert the tree to a JSON-serialisable dict."""
        current = self.stepper.current if self.stepper else None
        info = (module_info or {}).get(node.name, {})
        d = {
            "name": node.name,
            "display_name": node.display_name,
            "layer_type": node.layer_type,
            "description": node.description,
            "depth": node.depth,
            "is_leaf": node.is_leaf,
            "executed": node.executed,
            "is_current": node == current,
            "pytorch_modules": info.get("pytorch_modules", []),
            "param_count": info.get("param_count", 0),
            "weight_shapes": info.get("weight_shapes", {}),
        }
        flow = self._data_flow_annotation(node)
        if flow:
            d["data_flow"] = flow
        if node.children:
            d["children"] = [self._graph_to_dict(c, module_info)
                             for c in node.children]
        return d

    def _graph_to_mermaid(self, node, lines, parent_id=None):
        """Recursively build a Mermaid flowchart."""
        node_id = node.name.replace(".", "_")
        label = node.display_name
        if node.layer_type:
            label += f"\\n({node.layer_type})"
        lines.append(f'    {node_id}["{label}"]')
        if parent_id:
            lines.append(f"    {parent_id} --> {node_id}")
        for child in node.children:
            self._graph_to_mermaid(child, lines, parent_id=node_id)

    def _count_nodes(self, node) -> int:
        return 1 + sum(self._count_nodes(c) for c in node.children)

    def _count_leaves(self, node) -> int:
        if node.is_leaf:
            return 1
        return sum(self._count_leaves(c) for c in node.children)

    def _graph_summary(self, root) -> str:
        total = self._count_nodes(root)
        leaves = self._count_leaves(root)
        blocks = sum(1 for c in root.children if c.layer_type == "GPT2Block")
        return (f"Summary: {total} nodes, {leaves} leaf operations, "
                f"{blocks} transformer blocks")

    def _graph_summary_detailed(self, root, module_info) -> str:
        """Extended summary with parameter counts and architecture stats."""
        total = self._count_nodes(root)
        leaves = self._count_leaves(root)
        blocks = sum(1 for c in root.children if c.layer_type == "GPT2Block")
        stats = self._model_stats()

        lines = [
            "\u2550" * 60,
            "MODEL SUMMARY",
            "\u2550" * 60,
            f"  Architecture:     {stats.get('arch', 'Unknown')}",
            f"  Parameters:       {self._format_param_count(stats.get('total_params', 0))}  ({stats.get('total_params', 0):,})",
            f"  Trainable:        {self._format_param_count(stats.get('trainable_params', 0))}",
            f"  Hidden dim (d):   {stats.get('hidden_dim', '?')}",
            f"  Num heads:        {stats.get('num_heads', '?')}",
            f"  Head dim:         {stats.get('head_dim', '?')}",
            f"  FFN inner dim:    {stats.get('ffn_dim', '?')}",
            f"  Num layers:       {blocks}",
            f"  Vocab size:       {stats.get('vocab_size', '?'):,}",
            f"  Max seq length:   {stats.get('max_seq_len', '?'):,}",
            f"  Activation:       {stats.get('activation', '?')}",
            f"  Dropout:          {stats.get('dropout', '?')}",
            "",
            "GRAPH STRUCTURE",
            f"  Total nodes:      {total}",
            f"  Leaf operations:  {leaves}",
            f"  Transformer blocks: {blocks}",
            "",
            "PER-BLOCK PARAMS",
        ]
        if blocks > 0:
            b0_info = module_info.get("block_0", {})
            b0_attn = module_info.get("block_0.attention", {})
            b0_ffn = module_info.get("block_0.ffn", {})
            lines.append(
                f"  Block total:      "
                f"{self._format_param_count(b0_info.get('param_count', 0))}")
            lines.append(
                f"    Attention:      "
                f"{self._format_param_count(b0_attn.get('param_count', 0))}")
            lines.append(
                f"    FFN:            "
                f"{self._format_param_count(b0_ffn.get('param_count', 0))}")
            emb_info = module_info.get("embedding", {})
            ln_info = module_info.get("final_norm", {})
            head_info = module_info.get("lm_head", {})
            lines.append(
                f"  Embedding:        "
                f"{self._format_param_count(emb_info.get('param_count', 0))}")
            lines.append(
                f"  Final norm:       "
                f"{self._format_param_count(ln_info.get('param_count', 0))}")
            lines.append(
                f"  LM head:          "
                f"{self._format_param_count(head_info.get('param_count', 0))}")

        mem_mb = stats.get("total_params", 0) * 4 / 1024 / 1024
        lines.append(f"\n  Memory (fp32):    {mem_mb:,.0f} MB")
        mem_f16 = mem_mb / 2
        lines.append(f"  Memory (fp16):    {mem_f16:,.0f} MB")

        return "\n".join(lines)

    def _model_stats(self) -> dict:
        """Gather top-level model statistics."""
        model = self.model
        if model is None:
            return {}
        config = getattr(model, "config", None)
        total_p = sum(p.numel() for p in model.parameters())
        trainable_p = sum(p.numel() for p in model.parameters()
                         if p.requires_grad)
        d = getattr(config, "n_embd", 0) if config else 0
        n_head = getattr(config, "n_head", 0) if config else 0
        d_ff = (getattr(config, "n_inner", None) or (4 * d)) if config else 0
        return {
            "arch": type(model).__name__,
            "total_params": total_p,
            "trainable_params": trainable_p,
            "hidden_dim": d,
            "num_heads": n_head,
            "head_dim": d // n_head if n_head else 0,
            "ffn_dim": d_ff,
            "vocab_size": getattr(config, "vocab_size", 0) if config else 0,
            "max_seq_len": getattr(config, "n_positions", 0) if config else 0,
            "activation": getattr(config, "activation_function", "?") if config else "?",
            "dropout": getattr(config, "resid_pdrop", "?") if config else "?",
        }

    @staticmethod
    def _format_param_count(n: int) -> str:
        """Format a parameter count as human-readable string."""
        if n >= 1_000_000_000:
            return f"{n / 1_000_000_000:.2f}B"
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)

    def cmd_run_to_line(self, line: int) -> dict:
        return self._error(
            "run_to_line is not supported for LLM debugging. "
            "Use 'b <layer_name>' + 'continue' instead.")

    # -- investigation command ---------------------------------------------

    def cmd_investigate(self, args: str) -> dict:
        """Run a full investigation: diagnose why the model fails on a prompt.

        Usage:
            investigate "prompt text" --expect "expected_token"
            investigate "who is Dr. James Whitfield" --expect "director"

        Runs all interpretability tools, classifies the failure mode,
        and produces architectural recommendations with compute graph diffs.
        """
        if self.model is None:
            return self._error("No model loaded.")

        prompt, expected = self._parse_investigate_args(args)
        if not prompt or not expected:
            return self._error(
                'Usage: investigate "prompt" --expect "expected_token"\n'
                'Example: investigate "who is Dr. James Whitfield" --expect "director"')

        from diagnosis import DiagnosisEngine, TestCase

        engine = DiagnosisEngine(self.model, self.tokenizer)

        test_case = TestCase(
            prompt=prompt,
            expected=expected,
            category="investigation",
        )

        report = engine.diagnose([test_case], issue="investigate command")

        if report.get("status") == "all_passed":
            return {
                "status": "ok",
                "message": (
                    f"\u2705 Model correctly predicts '{expected}' for this "
                    f"prompt. No investigation needed."),
                "current_location": None, "call_stack": [],
                "local_variables": report,
                "stdout_new": "", "stderr_new": "",
            }

        failure_mode = self._classify_failure_mode(report)
        formatted = self._format_investigation_report(
            prompt, expected, report, failure_mode)

        return {
            "status": "ok",
            "message": formatted,
            "current_location": None,
            "call_stack": [],
            "local_variables": {
                "failure_mode": failure_mode,
                "report": report,
            },
            "stdout_new": "", "stderr_new": "",
        }

    @staticmethod
    def _parse_investigate_args(args: str):
        """Parse 'investigate "prompt" --expect "token"' syntax."""
        prompt = ""
        expected = ""
        raw = args.strip()
        if not raw:
            return None, None

        if "--expect" in raw:
            parts = raw.split("--expect", 1)
            prompt_raw = parts[0].strip()
            expected_raw = parts[1].strip()
        elif " -e " in f" {raw} ":
            idx = raw.split().index("-e")
            tokens = raw.split()
            prompt_raw = " ".join(tokens[:idx])
            expected_raw = " ".join(tokens[idx + 1:])
        else:
            return None, None

        for q in ['"', "'"]:
            if prompt_raw.startswith(q) and prompt_raw.endswith(q):
                prompt_raw = prompt_raw[1:-1]
            if expected_raw.startswith(q) and expected_raw.endswith(q):
                expected_raw = expected_raw[1:-1]

        return prompt_raw.strip(), expected_raw.strip()

    @staticmethod
    def _classify_failure_mode(report: dict) -> dict:
        """Classify the failure into a named failure mode with confidence."""
        findings = report.get("findings", {})
        details = report.get("per_failure_details", [])
        detail = details[0] if details else {}

        modes = []

        if findings.get("knowledge_never_present", 0) > 0:
            modes.append({
                "mode": "KNOWLEDGE_GAP",
                "confidence": "high",
                "description": (
                    "The model has no knowledge of the expected answer. "
                    "The correct token never appears in the top predictions "
                    "at any layer."),
                "icon": "\U0001F4AD",
            })

        if findings.get("knowledge_present_but_lost", 0) > 0:
            peak = detail.get("peak_layer", "?")
            cross = detail.get("crossover_layer", "?")
            modes.append({
                "mode": "LATE_REGRESSION",
                "confidence": "high",
                "description": (
                    f"The model finds the correct answer at {peak} but "
                    f"then suppresses it by {cross}. The later layers "
                    f"actively override the correct prediction."),
                "icon": "\U0001F4C9",
            })

        problematic = findings.get("problematic_heads", [])
        if len(problematic) > 3:
            modes.append({
                "mode": "ATTENTION_FAILURE",
                "confidence": "medium",
                "description": (
                    f"{len(problematic)} attention heads show near-uniform "
                    f"attention patterns. The model isn't focusing on the "
                    f"relevant tokens in the prompt."),
                "icon": "\U0001F441\uFE0F",
            })

        degradation = findings.get("probing_degradation", [])
        if degradation and degradation[0].get("accuracy_drop", 0) > 0.15:
            d = degradation[0]
            modes.append({
                "mode": "CAPACITY_BOTTLENECK",
                "confidence": "medium",
                "description": (
                    f"Information is encoded at {d['from_layer']} "
                    f"({d['from_accuracy']:.0%} accuracy) but destroyed by "
                    f"{d['to_layer']} ({d['to_accuracy']:.0%}). The "
                    f"intermediate layers lack capacity to preserve it."),
                "icon": "\U0001F50D",
            })

        if detail.get("actual_prob", 0) > 0.5:
            modes.append({
                "mode": "HALLUCINATION",
                "confidence": "high" if detail["actual_prob"] > 0.8 else "medium",
                "description": (
                    f"The model confidently predicts "
                    f"'{detail.get('actual', '?')}' "
                    f"(p={detail.get('actual_prob', 0):.2f}) instead of "
                    f"'{detail.get('expected', '?')}'. This is a factual "
                    f"hallucination \u2014 the model is certain but wrong."),
                "icon": "\U0001F47B",
            })

        if not modes:
            modes.append({
                "mode": "UNCLASSIFIED",
                "confidence": "low",
                "description": "Could not classify the failure pattern.",
                "icon": "\u2753",
            })

        return {
            "primary": modes[0],
            "secondary": modes[1:],
            "all_modes": [m["mode"] for m in modes],
        }

    def _format_investigation_report(self, prompt: str, expected: str,
                                     report: dict, failure_mode: dict) -> str:
        """Format the complete investigation report as readable text."""
        lines = []
        sep = "\u2550" * 60

        lines.append(sep)
        lines.append("INVESTIGATION REPORT")
        lines.append(sep)
        lines.append(f"  Prompt:   \"{prompt}\"")
        lines.append(f"  Expected: \"{expected}\"")

        detail = {}
        details = report.get("per_failure_details", [])
        if details:
            detail = details[0]
        lines.append(
            f"  Actual:   \"{detail.get('actual', '?')}\" "
            f"(p={detail.get('actual_prob', 0):.4f})")
        lines.append(
            f"  Expected prob: {detail.get('expected_prob', 0):.6f}")
        lines.append("")

        primary = failure_mode["primary"]
        lines.append(f"{primary['icon']}  FAILURE MODE: {primary['mode']}")
        lines.append(f"   Confidence: {primary['confidence']}")
        lines.append(f"   {primary['description']}")
        for sec in failure_mode.get("secondary", []):
            lines.append(f"\n   Also detected: {sec['icon']} {sec['mode']}")
            lines.append(f"   {sec['description']}")
        lines.append("")

        lines.append(sep)
        lines.append("EVIDENCE")
        lines.append(sep)

        traj = detail.get("trajectory_summary", [])
        if traj:
            lines.append("\n  Logit Lens (per-layer prediction trajectory):")
            for t in traj:
                rank_str = (f"rank #{t['expected_rank']}"
                            if t['expected_rank'] is not None else "not in top-10")
                lines.append(
                    f"    {t['layer']:>12s} \u2192 {t['top']!r:>12s}  "
                    f"  (expected: {rank_str})")

        if detail.get("most_causal_layer"):
            lines.append(
                f"\n  Most causal layer: {detail['most_causal_layer']} "
                f"(recovery={detail.get('most_causal_recovery', 0):.2f})")

        if detail.get("num_unfocused_heads", 0) > 0:
            lines.append(
                f"\n  Unfocused attention heads: "
                f"{detail['num_unfocused_heads']}")

        lines.append("")

        recs = report.get("recommendations", [])
        lines.append(sep)
        lines.append(f"RECOMMENDATIONS ({len(recs)} total)")
        lines.append(sep)

        for i, rec in enumerate(recs, 1):
            cat_icon = {
                "fine_tuning": "\U0001F527",
                "architecture": "\U0001F3D7\uFE0F",
                "pruning": "\u2702\uFE0F",
                "data": "\U0001F4DA",
            }.get(rec.get("category", ""), "\U0001F4A1")

            lines.append(
                f"\n  {i}. {cat_icon}  {rec.get('title', '?')}  "
                f"[{rec.get('confidence', '?')} confidence, "
                f"{rec.get('impact', '?')} impact]")
            lines.append(f"     Category: {rec.get('category', '?')}")
            lines.append(f"     {rec.get('explanation', '')}")

            if rec.get("evidence"):
                lines.append("     Evidence:")
                for ev in rec["evidence"]:
                    lines.append(f"       - {ev}")

            lines.append(
                f"     Expected improvement: "
                f"{rec.get('estimated_improvement', '?')}")

            if rec.get("risks"):
                lines.append("     Risks:")
                for risk in rec["risks"]:
                    lines.append(f"       \u26A0 {risk}")

            if rec.get("graph_diff"):
                lines.append("")
                lines.append(
                    "     " + rec["graph_diff"].replace(
                        "\n", "\n     ").rstrip())

        lines.append("")
        lines.append(sep)
        lines.append("WHAT NeuralDebug CAN DO")
        lines.append(sep)

        can_do = []
        suggest_only = []
        for rec in recs:
            cat = rec.get("category", "")
            title = rec.get("title", "")
            if cat == "fine_tuning":
                can_do.append(
                    f"  \u2705 {title} \u2014 use 'finetune' command")
            elif cat == "pruning":
                can_do.append(
                    f"  \u2705 {title} \u2014 can apply head mask")
            elif rec.get("id") == "adapter-insertion":
                can_do.append(
                    f"  \u2705 {title} \u2014 can insert adapters")
            else:
                suggest_only.append(
                    f"  \U0001F4DD {title} \u2014 suggestion only "
                    f"(requires manual implementation)")

        for line in can_do:
            lines.append(line)
        for line in suggest_only:
            lines.append(line)

        return "\n".join(lines)

    # -- interpretability commands -----------------------------------------

    def cmd_logit_lens(self, args: str) -> dict:
        """Run Logit Lens: show per-layer predictions."""
        if not self.stepper or not self.stepper.is_started:
            return self._error("Session not started. Send 'start' first.")
        top_k = 5
        if args.strip():
            try:
                top_k = int(args.strip())
            except ValueError:
                pass
        result = LogitLens.run(
            self.model, self.tokenizer,
            self.stepper.ctx.input_ids, top_k=top_k)
        lines = []
        for layer in result["layers"]:
            tok = layer["top_token"]
            prob = layer["top_prob"]
            ent = layer["entropy"]
            lines.append(
                f"  {layer['layer']:>12s}  →  {tok!r:>12s}  "
                f"p={prob:.4f}  entropy={ent:.2f}")
        return {
            "status": "ok",
            "message": ("Logit Lens — per-layer prediction "
                        "(what the model would predict if it stopped here):\n"
                        + "\n".join(lines)),
            "current_location": None, "call_stack": [],
            "local_variables": result,
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_patch(self, args: str) -> dict:
        """Run Activation Patching (causal tracing)."""
        if not self.stepper or not self.stepper.is_started:
            return self._error("Session not started. Send 'start' first.")
        corrupted = args.strip().strip('"').strip("'")
        if not corrupted:
            return self._error(
                "Usage: patch <corrupted_prompt>\n"
                "Example: patch \"The capital of Germany is\"")
        clean = self.stepper.ctx.prompt_text
        result = ActivationPatching.run(
            self.model, self.tokenizer, clean, corrupted)
        lines = []
        for layer in result["layers"]:
            bar_len = int(max(0, layer["recovery"]) * 20)
            bar = "\u2588" * bar_len + "\u2591" * (20 - bar_len)
            lines.append(
                f"  {layer['layer']:>10s}  {bar}  "
                f"recovery={layer['recovery']:.4f}")
        most = result["most_causal"]
        return {
            "status": "ok",
            "message": (
                f"Activation Patching — which layer causes the model to "
                f"predict '{result['target_token']}'?\n"
                f"  clean: '{clean}' → p={result['clean_prob']:.4f}\n"
                f"  corrupted: '{corrupted}' → p={result['corrupted_prob']:.4f}\n"
                f"Per-layer recovery (1.0 = fully restores prediction):\n"
                + "\n".join(lines) + "\n"
                f"Most causal layer: {most['layer']} "
                f"(recovery={most['recovery']:.4f})"),
            "current_location": None, "call_stack": [],
            "local_variables": result,
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_attention(self, args: str) -> dict:
        """Analyze attention patterns across all heads."""
        if not self.stepper or not self.stepper.is_started:
            return self._error("Session not started. Send 'start' first.")
        args = args.strip()
        if args:
            # attention_to_token mode
            try:
                pos = int(args)
            except ValueError:
                return self._error(
                    "Usage: attention [position]\n"
                    "  No args → full head analysis\n"
                    "  With position → attention TO that token")
            result = AttentionAnalysis.attention_to_token(
                self.model, self.tokenizer,
                self.stepper.ctx.input_ids, pos)
            if "error" in result:
                return self._error(result["error"])
            lines = [f"Top heads attending to '{result['target_token']}' "
                     f"(position {pos}):"]
            for h in result["heads"]:
                bar_len = int(h["weight"] * 30)
                bar = "\u2588" * bar_len
                lines.append(
                    f"  L{h['layer']}H{h['head']:02d}  {bar}  "
                    f"w={h['weight']:.4f}")
            return {
                "status": "ok",
                "message": "\n".join(lines),
                "current_location": None, "call_stack": [],
                "local_variables": result,
                "stdout_new": "", "stderr_new": "",
            }
        else:
            # Full analysis
            result = AttentionAnalysis.analyze_heads(
                self.model, self.tokenizer,
                self.stepper.ctx.input_ids)
            lines = [f"Attention analysis ({result['total_heads']} heads):"]
            lines.append("\nMost focused heads (strongest patterns):")
            for h in result["most_focused"]:
                att = h["last_token_attends_to"]
                lines.append(
                    f"  L{h['layer']}H{h['head']:02d}  "
                    f"focus={h['focus_ratio']:.3f}  "
                    f"last→'{att['token']}' (w={att['weight']:.3f})")
            lines.append("\nLeast focused heads (most uniform):")
            for h in result["least_focused"]:
                att = h["last_token_attends_to"]
                lines.append(
                    f"  L{h['layer']}H{h['head']:02d}  "
                    f"focus={h['focus_ratio']:.3f}  "
                    f"last→'{att['token']}' (w={att['weight']:.3f})")
            return {
                "status": "ok",
                "message": "\n".join(lines),
                "current_location": None, "call_stack": [],
                "local_variables": result,
                "stdout_new": "", "stderr_new": "",
            }

    def cmd_probe(self, args: str) -> dict:
        """Run a linear probe to test what information is encoded."""
        if not self.stepper or not self.stepper.is_started:
            return self._error("Session not started. Send 'start' first.")
        task = args.strip() if args.strip() else "next_token"
        result = Probing.run(
            self.model, self.tokenizer,
            self.stepper.ctx.input_ids, task=task)
        if "error" in result:
            return self._error(result["error"])
        lines = [f"Probe task: {task}"]
        for layer in result["layers"]:
            name = layer["layer"]
            if task == "position":
                sep = layer["separability"]
                bar_len = int(max(0, min(1, (sep + 1) / 2)) * 20)
                bar = "\u2588" * bar_len + "\u2591" * (20 - bar_len)
                lines.append(
                    f"  {name:>12s}  {bar}  "
                    f"self={layer['self_similarity']:.3f}  "
                    f"cross={layer['cross_similarity']:.3f}  "
                    f"sep={sep:.4f}")
            else:
                acc = layer["accuracy"]
                bar_len = int(acc * 20)
                bar = "\u2588" * bar_len + "\u2591" * (20 - bar_len)
                lines.append(
                    f"  {name:>12s}  {bar}  "
                    f"accuracy={acc:.1%}  "
                    f"({layer['correct_count']}/{layer['total']})")
        lines.append(f"\n{result['description']}")
        return {
            "status": "ok",
            "message": "\n".join(lines),
            "current_location": None, "call_stack": [],
            "local_variables": result,
            "stdout_new": "", "stderr_new": "",
        }

    def cmd_generate(self, args: str) -> dict:
        """Run full generation: complete the forward pass + generate tokens."""
        if not self.stepper or not self.stepper.is_started:
            return self._error("Session not started. Send 'start' first.")
        max_tokens = 50
        if args.strip():
            try:
                max_tokens = int(args.strip())
            except ValueError:
                pass
        max_tokens = min(max_tokens, 200)

        # First, finish the current forward pass if not done
        if not self.stepper._forward_complete:
            self.stepper.continue_()

        # Generate tokens one at a time
        eos_id = self.tokenizer.eos_token_id
        for i in range(max_tokens):
            if not self.stepper._forward_complete:
                self.stepper.continue_()
            # Now at end of forward pass — pick the next token
            if self.stepper.ctx.logits is None:
                break
            probs = F.softmax(self.stepper.ctx.logits[0, -1], dim=-1)
            next_id = probs.argmax().item()
            if next_id == eos_id:
                break
            self.stepper.generate_next_token()
            # Run the new forward pass to completion
            if not self.stepper._forward_complete:
                self.stepper.continue_()

        prompt = self.stepper.ctx.prompt_text
        generated = self.stepper.ctx.generated_text
        full_text = prompt + generated
        return {
            "status": "ok",
            "message": (
                f"Generated {len(self.stepper.ctx.generated_tokens)} tokens:\n\n"
                f"{full_text}"),
            "current_location": None, "call_stack": [],
            "local_variables": {
                "prompt": prompt,
                "generated": generated,
                "full_text": full_text,
                "num_tokens": len(self.stepper.ctx.generated_tokens),
            },
            "stdout_new": generated, "stderr_new": "",
        }

    def cmd_diagnose(self, args: str) -> dict:
        """Run autonomous diagnosis on a test suite."""
        args = args.strip()
        if not args:
            return self._error(
                "Usage: diagnose <test_cases.json>\n"
                "JSON format: [{\"prompt\": \"...\", \"expected\": \"...\", "
                "\"category\": \"...\"}]")

        from diagnosis import DiagnosisEngine, TestCase

        try:
            with open(args) as f:
                raw = json.load(f)
        except Exception as e:
            return self._error(f"Cannot load test cases: {e}")

        test_cases = [
            TestCase(
                prompt=tc["prompt"],
                expected=tc["expected"],
                category=tc.get("category", ""),
            )
            for tc in raw
        ]

        engine = DiagnosisEngine(self.model, self.tokenizer)
        report = engine.diagnose(test_cases)

        if report.get("status") == "all_passed":
            return {
                "status": "ok",
                "message": report["message"],
                "current_location": None, "call_stack": [],
                "local_variables": report, "stdout_new": "", "stderr_new": "",
            }

        parts = [report.get("summary", "")]
        for rec in report.get("recommendations", []):
            parts.append(
                f"\n{'='*60}\n"
                f"[{rec['confidence'].upper()}] {rec['title']}\n"
                f"{rec['explanation']}\n"
                f"\nCode:\n{rec['code']}")

        return {
            "status": "ok",
            "message": "\n".join(parts),
            "current_location": None, "call_stack": [],
            "local_variables": report, "stdout_new": "", "stderr_new": "",
        }

    def cmd_finetune(self, args: str) -> dict:
        """Run LoRA fine-tuning to inject knowledge into the model.

        Accepts either:
          1. A JSON file path with structure:
             {"facts": ["..."], "verification_prompt": "...",
              "expected_token": "...", "config": {...}}
          2. Inline: finetune "fact text" --verify "prompt" --expect "token"
        """
        args = args.strip()
        if not args:
            return self._error(
                "Usage:\n"
                "  finetune <config.json>\n"
                "  finetune \"fact\" --verify \"prompt\" --expect \"token\"\n"
                "\nJSON format:\n"
                "  {\"facts\": [\"Dr. Elena Vasquez is the director of Horizon Research Labs\"],\n"
                "   \"verification_prompt\": \"Who is Dr. Elena Vasquez? Dr. Elena Vasquez is\",\n"
                "   \"expected_token\": \"the\"}")

        from finetuner import LoRAFinetuner, FinetuneConfig

        # Try JSON file first
        facts = None
        verification_prompt = None
        expected_token = None
        config_dict = {}

        if args.endswith(".json"):
            try:
                with open(args) as f:
                    spec = json.load(f)
                facts = spec.get("facts", [])
                verification_prompt = spec.get("verification_prompt", "")
                expected_token = spec.get("expected_token", "")
                config_dict = spec.get("config", {})
            except Exception as e:
                return self._error(f"Cannot load config: {e}")
        else:
            parsed = self._parse_finetune_args(args)
            if parsed is None:
                return self._error(
                    "Cannot parse arguments. Use:\n"
                    "  finetune \"fact\" --verify \"prompt\" --expect \"token\"")
            facts = parsed["facts"]
            verification_prompt = parsed["verification_prompt"]
            expected_token = parsed["expected_token"]

        if not facts or not verification_prompt or not expected_token:
            return self._error(
                "Must provide: facts, verification_prompt, and expected_token.")

        config = FinetuneConfig(**{
            k: v for k, v in config_dict.items()
            if k in FinetuneConfig.__dataclass_fields__
        })

        print(f"\nStarting LoRA fine-tuning session...", flush=True)
        print(f"  Facts: {facts}", flush=True)
        print(f"  Verify: '{verification_prompt}' -> expect '{expected_token}'",
              flush=True)

        finetuner = LoRAFinetuner(self.model, self.tokenizer,
                                  model_name=self.model_name)
        result = finetuner.run(
            facts=facts,
            verification_prompt=verification_prompt,
            expected_token=expected_token,
            config=config,
        )

        return {
            "status": "ok",
            "message": result.message,
            "current_location": None, "call_stack": [],
            "local_variables": result.to_dict(),
            "stdout_new": result.message, "stderr_new": "",
        }

    def _parse_finetune_args(self, args: str) -> Optional[dict]:
        """Parse inline finetune arguments."""
        import shlex
        try:
            parts = shlex.split(args)
        except ValueError:
            return None

        facts = []
        verification_prompt = None
        expected_token = None

        i = 0
        while i < len(parts):
            if parts[i] in ("--verify", "-v") and i + 1 < len(parts):
                verification_prompt = parts[i + 1]
                i += 2
            elif parts[i] in ("--expect", "-e") and i + 1 < len(parts):
                expected_token = parts[i + 1]
                i += 2
            else:
                facts.append(parts[i])
                i += 1

        if not facts or not verification_prompt or not expected_token:
            return None

        return {
            "facts": facts,
            "verification_prompt": verification_prompt,
            "expected_token": expected_token,
        }

    # -- Tier 2: SAE --------------------------------------------------------

    def cmd_sae(self, args: str) -> dict:
        """Sparse Autoencoder — train, decompose, or inspect features.

        Usage:
          sae train <layer> [--prompts file.txt] [--expansion 4] [--steps 200]
          sae features <layer> [position]
          sae decompose <layer> [position]
          sae dashboard <layer> <feature_id>
        """
        import shlex
        from sae import train_sae, decompose_activation, feature_dashboard

        parts = args.strip().split(None, 1)
        if not parts:
            return self._error(
                "Usage: sae train <layer> | sae features <layer> [pos] "
                "| sae decompose <layer> [pos] | sae dashboard <layer> <feat>")

        subcmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        if subcmd == "train":
            return self._sae_train(rest)
        elif subcmd in ("features", "decompose"):
            return self._sae_decompose(rest)
        elif subcmd == "dashboard":
            return self._sae_dashboard(rest)
        else:
            return self._error(
                f"Unknown SAE subcommand '{subcmd}'. "
                f"Use: train, features, decompose, dashboard")

    def _sae_train(self, args: str) -> dict:
        """Train a Sparse Autoencoder on a layer's activations."""
        from sae import train_sae

        parts = args.strip().split()
        if not parts:
            return self._error("Usage: sae train <layer> [--expansion 4] [--steps 200]")

        layer_idx = int(parts[0])
        n_blocks = len(self.model.transformer.h)
        if layer_idx < 0 or layer_idx >= n_blocks:
            return self._error(f"Layer {layer_idx} out of range [0, {n_blocks - 1}]")

        expansion = 4
        num_steps = 200
        for i, p in enumerate(parts):
            if p == "--expansion" and i + 1 < len(parts):
                expansion = int(parts[i + 1])
            elif p == "--steps" and i + 1 < len(parts):
                num_steps = int(parts[i + 1])

        # Use diverse prompts for better feature quality
        prompts = [
            "The capital of France is Paris, a beautiful city in Europe.",
            "Machine learning models can generate text that seems human.",
            "The President of the United States lives in the White House.",
            "Python is a popular programming language for data science.",
            "The theory of relativity was developed by Albert Einstein.",
            "Artificial intelligence has made significant progress recently.",
            "The stock market experienced a sharp decline last quarter.",
            "Climate change is one of the most pressing global challenges.",
            "The human brain contains approximately 86 billion neurons.",
            "Shakespeare wrote many famous plays including Hamlet and Macbeth.",
            "Deep learning architectures use multiple layers of neural networks.",
            "The speed of light in vacuum is approximately 300000 km per second.",
        ]

        # If we have a current prompt, add it
        if self.stepper and self.stepper.ctx and self.stepper.ctx.prompt_text:
            prompts.insert(0, self.stepper.ctx.prompt_text)

        print(f"Training SAE on block_{layer_idx} "
              f"(expansion={expansion}, steps={num_steps})...", flush=True)

        sae, stats = train_sae(
            self.model, self.tokenizer, prompts, layer_idx,
            expansion=expansion, num_steps=num_steps)

        self._trained_saes[layer_idx] = sae

        msg_parts = [
            f"SAE trained on block_{layer_idx}",
            f"  Architecture: {stats['input_dim']} → {stats['hidden_dim']} "
            f"({expansion}× expansion)",
            f"  Training: {num_steps} steps, "
            f"recon loss {stats['final_recon_loss']:.4f}, "
            f"sparsity loss {stats['final_sparsity_loss']:.4f}",
            f"  Features: {stats['alive_features']} alive / "
            f"{stats['dead_features']} dead (of {stats['hidden_dim']})",
            f"  Avg active per sample: {stats['avg_active_features']:.1f} "
            f"({stats['sparsity_ratio']:.1%} sparse)",
        ]

        return {
            "status": "ok",
            "message": "\n".join(msg_parts),
            "current_location": None, "call_stack": [],
            "local_variables": stats, "stdout_new": "", "stderr_new": "",
        }

    def _sae_decompose(self, args: str) -> dict:
        """Decompose current activation into SAE features."""
        from sae import decompose_activation

        parts = args.strip().split()
        if not parts:
            return self._error("Usage: sae features <layer> [position]")

        layer_idx = int(parts[0])
        position = int(parts[1]) if len(parts) > 1 else -1

        if layer_idx not in self._trained_saes:
            return self._error(
                f"No trained SAE for block_{layer_idx}. "
                f"Run 'sae train {layer_idx}' first. "
                f"Trained layers: {list(self._trained_saes.keys())}")

        if not self.stepper or not self.stepper.ctx:
            return self._error("No active prompt. Use 'start <prompt>' first.")

        input_ids = self.stepper.ctx.input_ids
        sae = self._trained_saes[layer_idx]

        result = decompose_activation(
            sae, self.model, self.tokenizer, input_ids, layer_idx, position)

        msg_parts = [
            f"SAE Decomposition — {result['layer']}, "
            f"position {result['position']} ('{result['token']}')",
            f"  Active features: {result['active_features']}/{result['total_features']} "
            f"(sparsity {result['sparsity']:.1%})",
            f"  Reconstruction: cosine={result['reconstruction_cosine']:.3f}, "
            f"MSE={result['reconstruction_mse']:.6f}",
            "",
            "  Top features:",
        ]
        for feat in result["top_features"][:10]:
            msg_parts.append(
                f"    #{feat['feature_id']:>5d}  "
                f"activation={feat['activation']:.3f}  "
                f"decoder_norm={feat['decoder_norm']:.3f}")

        return {
            "status": "ok",
            "message": "\n".join(msg_parts),
            "current_location": None, "call_stack": [],
            "local_variables": result, "stdout_new": "", "stderr_new": "",
        }

    def _sae_dashboard(self, args: str) -> dict:
        """Show dashboard for one SAE feature."""
        from sae import feature_dashboard as feat_dash

        parts = args.strip().split()
        if len(parts) < 2:
            return self._error("Usage: sae dashboard <layer> <feature_id>")

        layer_idx = int(parts[0])
        feature_idx = int(parts[1])

        if layer_idx not in self._trained_saes:
            return self._error(
                f"No trained SAE for block_{layer_idx}. "
                f"Run 'sae train {layer_idx}' first.")

        sae = self._trained_saes[layer_idx]
        prompts = [
            "The capital of France is Paris.",
            "Machine learning models can generate text.",
            "The President of the United States lives in the White House.",
            "Python is a popular programming language.",
        ]
        if self.stepper and self.stepper.ctx and self.stepper.ctx.prompt_text:
            prompts.insert(0, self.stepper.ctx.prompt_text)

        result = feat_dash(
            sae, self.model, self.tokenizer, prompts, layer_idx, feature_idx)

        msg_parts = [
            f"Feature #{feature_idx} Dashboard — {result['layer']}",
            f"  Decoder norm: {result['decoder_norm']:.4f}",
            "",
            "  When active, boosts tokens:",
        ]
        for bt in result["boosted_tokens"]:
            msg_parts.append(f"    '{bt['token']}' (p={bt['probability']:.4f})")
        msg_parts.append("")
        msg_parts.append("  Top activating tokens:")
        for ta in result["top_activations"][:8]:
            msg_parts.append(
                f"    '{ta['token']}' act={ta['activation']:.3f} "
                f"in \"{ta['prompt_snippet']}\"")

        return {
            "status": "ok",
            "message": "\n".join(msg_parts),
            "current_location": None, "call_stack": [],
            "local_variables": result, "stdout_new": "", "stderr_new": "",
        }

    # -- Tier 2: Neuron Analysis ------------------------------------------

    def cmd_neuron(self, args: str) -> dict:
        """Neuron-level analysis — dashboard, scan, or ablate.

        Usage:
          neuron <layer>.<neuron>          — full dashboard for one neuron
          neuron scan <layer>              — find most interesting neurons
          neuron scan <layer> --method causal — rank by ablation impact
          neuron ablate <layer>.<neuron>   — ablate and compare generation
        """
        from neuron_analysis import (
            neuron_dashboard, neuron_scan, neuron_ablate)

        parts = args.strip().split()
        if not parts:
            return self._error(
                "Usage: neuron <layer>.<neuron> | neuron scan <layer> "
                "| neuron ablate <layer>.<neuron>")

        if not self.stepper or not self.stepper.ctx:
            return self._error("No active prompt. Use 'start <prompt>' first.")
        input_ids = self.stepper.ctx.input_ids

        subcmd = parts[0].lower()

        if subcmd == "scan":
            if len(parts) < 2:
                return self._error("Usage: neuron scan <layer> [--method activation|variance|causal]")
            layer_idx = int(parts[1])
            method = "activation"
            top_k = 10
            for i, p in enumerate(parts):
                if p == "--method" and i + 1 < len(parts):
                    method = parts[i + 1]
                elif p == "--top" and i + 1 < len(parts):
                    top_k = int(parts[i + 1])

            result = neuron_scan(
                self.model, self.tokenizer, input_ids, layer_idx,
                top_k=top_k, method=method)

            if "error" in result:
                return self._error(result["error"])

            msg_parts = [
                f"Neuron Scan — block_{layer_idx} ({result['ffn_dim']} neurons, "
                f"method={method})",
                "",
            ]
            for n in result["top_neurons"]:
                line = f"  #{n['neuron']:>5d}  "
                if method == "variance":
                    line += f"variance={n.get('variance', 0):.4f}  "
                else:
                    line += f"max_act={n.get('max_activation', 0):.4f}  "
                line += f"mean={n['mean']:.4f}  max@'{n['max_token']}'"
                if "kl_divergence" in n:
                    line += f"  KL={n['kl_divergence']:.4f}"
                msg_parts.append(line)

            return {
                "status": "ok",
                "message": "\n".join(msg_parts),
                "current_location": None, "call_stack": [],
                "local_variables": result, "stdout_new": "", "stderr_new": "",
            }

        elif subcmd == "ablate":
            if len(parts) < 2:
                return self._error("Usage: neuron ablate <layer>.<neuron>")
            spec = parts[1]
            if "." not in spec:
                return self._error("Use format: neuron ablate <layer>.<neuron>")
            layer_idx, neuron_idx = spec.split(".", 1)
            result = neuron_ablate(
                self.model, self.tokenizer, input_ids,
                int(layer_idx), int(neuron_idx))

            if "error" in result:
                return self._error(result["error"])

            msg_parts = [
                f"Neuron Ablation — block_{result['layer']}.c_fc[{result['neuron']}]",
                f"  Prompt: \"{result['prompt'][:60]}\"",
                f"  Normal:  {result['generation_normal'][:80]}",
                f"  Ablated: {result['generation_ablated'][:80]}",
                f"  Changed: {result['changed']}",
            ]

            return {
                "status": "ok",
                "message": "\n".join(msg_parts),
                "current_location": None, "call_stack": [],
                "local_variables": result, "stdout_new": "", "stderr_new": "",
            }

        else:
            # Expect <layer>.<neuron> format
            spec = subcmd
            if "." not in spec:
                return self._error(
                    "Use format: neuron <layer>.<neuron> "
                    "(e.g., neuron 12.1024)")
            layer_idx, neuron_idx = spec.split(".", 1)
            result = neuron_dashboard(
                self.model, self.tokenizer, input_ids,
                int(layer_idx), int(neuron_idx))

            if "error" in result:
                return self._error(result["error"])

            ablation = result["ablation"]
            msg_parts = [
                f"Neuron Dashboard — block_{result['layer']}.c_fc[{result['neuron']}]",
                f"  FFN dim: {result['ffn_dim']}",
                "",
                "  Activation stats:",
                f"    mean={result['activation_stats']['mean']:.4f}  "
                f"std={result['activation_stats']['std']:.4f}  "
                f"min={result['activation_stats']['min']:.4f}  "
                f"max={result['activation_stats']['max']:.4f}",
                "",
                "  Top activating tokens:",
            ]
            for t in result["top_activating_tokens"]:
                msg_parts.append(
                    f"    pos {t['position']:>3d} '{t['token']}'  "
                    f"act={t['activation']:.4f}")
            msg_parts.extend([
                "",
                "  Ablation impact:",
                f"    Prediction: '{ablation['baseline_prediction']}' → "
                f"'{ablation['ablated_prediction']}'  "
                f"({'CHANGED' if ablation['prediction_changed'] else 'same'})",
                f"    KL divergence: {ablation['kl_divergence']:.6f}",
                f"    Top token prob: {ablation['top_token_prob_before']:.4f} → "
                f"{ablation['top_token_prob_after']:.4f} "
                f"(Δ={ablation['prob_delta']:+.4f})",
            ])

            return {
                "status": "ok",
                "message": "\n".join(msg_parts),
                "current_location": None, "call_stack": [],
                "local_variables": result, "stdout_new": "", "stderr_new": "",
            }

    # -- Tier 2: Hallucination Detector -----------------------------------

    def cmd_hallucinate(self, args: str) -> dict:
        """Detect potential hallucinations in generated text.

        Usage:
          hallucinate [prompt]                   — generate & analyze
          hallucinate --tokens 30                — control generation length
          hallucinate --check token1 token2 ...  — check specific claims
        """
        from hallucination_detector import (
            detect_hallucinations, detect_factual_conflicts)

        args = args.strip()

        # Parse arguments
        prompt = None
        max_tokens = 50
        check_tokens = []

        if args.startswith("--check"):
            rest = args[len("--check"):].strip()
            check_tokens = rest.split()
            if self.stepper and self.stepper.ctx and self.stepper.ctx.prompt:
                prompt = self.stepper.ctx.prompt
            else:
                return self._error(
                    "No active prompt. Use 'start <prompt>' first, "
                    "or provide one.")
        else:
            import shlex
            try:
                parts = shlex.split(args) if args else []
            except ValueError:
                parts = args.split()

            for i, p in enumerate(parts):
                if p == "--tokens" and i + 1 < len(parts):
                    max_tokens = int(parts[i + 1])
                    parts[i] = parts[i + 1] = ""

            prompt_text = " ".join(p for p in parts if p).strip()
            if prompt_text:
                prompt = prompt_text.strip('"').strip("'")
            elif self.stepper and self.stepper.ctx and self.stepper.ctx.prompt:
                prompt = self.stepper.ctx.prompt

        if not prompt:
            return self._error(
                "Usage: hallucinate [prompt] [--tokens N]\n"
                "   or: hallucinate --check token1 token2 ...")

        if check_tokens:
            result = detect_factual_conflicts(
                self.model, self.tokenizer, prompt, check_tokens)
            msg_parts = [
                f"Factual Conflict Check — \"{prompt[:60]}\"",
                "",
            ]
            for claim in result["claims"]:
                if "error" in claim:
                    msg_parts.append(f"  '{claim['token']}': {claim['error']}")
                else:
                    msg_parts.append(
                        f"  '{claim['token']}': {claim['grounding']} "
                        f"(rank #{claim['final_rank']}, "
                        f"p={claim['final_prob']:.4f}, "
                        f"early={claim['early_support']:.2f}, "
                        f"late={claim['late_support']:.2f})")

            return {
                "status": "ok",
                "message": "\n".join(msg_parts),
                "current_location": None, "call_stack": [],
                "local_variables": result, "stdout_new": "", "stderr_new": "",
            }

        # Default: generate and detect
        print(f"Running hallucination detection ({max_tokens} tokens)...",
              flush=True)

        result = detect_hallucinations(
            self.model, self.tokenizer, prompt, max_tokens=max_tokens)

        summary = result["summary"]
        msg_parts = [
            f"Hallucination Detection — \"{prompt[:60]}\"",
            f"  Risk level: {summary['hallucination_risk']}",
            f"  Generated {result['total_tokens']} tokens, "
            f"{result['flagged_tokens']} flagged, "
            f"{result['high_suspicion_tokens']} high-suspicion",
            f"  Avg suspicion: {result['avg_suspicion']:.3f}",
            "",
            f"  Generated text:",
            f"    {result['generated_text'][:200]}",
            "",
            f"  Annotated (⚠️=high, ?=medium):",
            f"    {result['annotated_text'][:200]}",
        ]

        if summary["flag_distribution"]:
            msg_parts.extend(["", "  Flag distribution:"])
            for flag, count in sorted(
                    summary["flag_distribution"].items(),
                    key=lambda x: x[1], reverse=True):
                msg_parts.append(f"    {flag}: {count}")

        if summary["most_suspicious"]:
            msg_parts.extend(["", "  Most suspicious tokens:"])
            for ts in summary["most_suspicious"][:5]:
                flags = ", ".join(ts["flags"]) if ts["flags"] else "none"
                msg_parts.append(
                    f"    step {ts['step']:>3d} '{ts['token']}' "
                    f"suspicion={ts['suspicion_score']:.2f} "
                    f"conf={ts['confidence']:.3f} "
                    f"agreement={ts['layer_agreement']:.2f} "
                    f"[{flags}]")

        return {
            "status": "ok",
            "message": "\n".join(msg_parts),
            "current_location": None, "call_stack": [],
            "local_variables": result, "stdout_new": "", "stderr_new": "",
        }

    # -- Tier 2: Attention Head Surgery -----------------------------------

    def cmd_surgery(self, args: str) -> dict:
        """Attention head surgery — ablate, amplify, sweep, restore.

        Usage:
          surgery ablate <layer>.<head>           — zero out head
          surgery amplify <layer>.<head> [factor]  — scale head output
          surgery sweep [start_layer-end_layer]    — rank all heads by impact
          surgery restore                          — undo modifications
          surgery status                           — show active modifications
        """
        parts = args.strip().split()
        if not parts:
            return self._error(
                "Usage: surgery ablate <L>.<H> | surgery amplify <L>.<H> [factor] "
                "| surgery sweep | surgery restore | surgery status")

        subcmd = parts[0].lower()

        if subcmd == "restore":
            result = self._head_surgeon.restore(self.model)
            return {
                "status": "ok",
                "message": result["description"],
                "current_location": None, "call_stack": [],
                "local_variables": result, "stdout_new": "", "stderr_new": "",
            }

        if subcmd == "status":
            result = self._head_surgeon.status()
            return {
                "status": "ok",
                "message": (
                    f"Surgery status: {result['active_modifications']} "
                    f"active modifications, "
                    f"{result['saved_weights']} saved weights."),
                "current_location": None, "call_stack": [],
                "local_variables": result, "stdout_new": "", "stderr_new": "",
            }

        if not self.stepper or not self.stepper.ctx:
            return self._error("No active prompt. Use 'start <prompt>' first.")
        input_ids = self.stepper.ctx.input_ids

        if subcmd == "ablate":
            if len(parts) < 2:
                return self._error("Usage: surgery ablate <layer>.<head>")
            spec = parts[1]
            if "." not in spec:
                return self._error("Use format: surgery ablate <layer>.<head>")
            layer_idx, head_idx = spec.split(".", 1)
            result = self._head_surgeon.ablate(
                self.model, self.tokenizer, input_ids,
                int(layer_idx), int(head_idx))
            if "error" in result:
                return self._error(result["error"])

            msg_parts = [
                f"Head Ablation — L{result['layer']}.H{result['head']}",
                f"  Heads: {result['n_heads']} total, "
                f"dim per head: {result['head_dim']}",
                f"  KL divergence: {result['kl_divergence']:.6f}",
                f"  Prediction: '{result['baseline_top_token']}' → "
                f"'{result['ablated_top_token']}'  "
                f"({'CHANGED' if result['prediction_changed'] else 'same'})",
                "",
                f"  Baseline generation:",
                f"    {result['baseline_generation'][:100]}",
                f"  Ablated generation:",
                f"    {result['ablated_generation'][:100]}",
            ]
            return {
                "status": "ok",
                "message": "\n".join(msg_parts),
                "current_location": None, "call_stack": [],
                "local_variables": result, "stdout_new": "", "stderr_new": "",
            }

        elif subcmd == "amplify":
            if len(parts) < 2:
                return self._error(
                    "Usage: surgery amplify <layer>.<head> [factor]")
            spec = parts[1]
            if "." not in spec:
                return self._error(
                    "Use format: surgery amplify <layer>.<head> [factor]")
            layer_idx, head_idx = spec.split(".", 1)
            factor = float(parts[2]) if len(parts) > 2 else 2.0
            result = self._head_surgeon.amplify(
                self.model, self.tokenizer, input_ids,
                int(layer_idx), int(head_idx), factor=factor)
            if "error" in result:
                return self._error(result["error"])

            msg_parts = [
                f"Head Amplification — L{result['layer']}.H{result['head']} "
                f"× {result['factor']}",
                f"  Baseline generation:",
                f"    {result['baseline_generation'][:100]}",
                f"  Amplified generation:",
                f"    {result['amplified_generation'][:100]}",
                f"  Changed: {result['generation_changed']}",
            ]
            return {
                "status": "ok",
                "message": "\n".join(msg_parts),
                "current_location": None, "call_stack": [],
                "local_variables": result, "stdout_new": "", "stderr_new": "",
            }

        elif subcmd == "sweep":
            layer_range = None
            top_k = 10
            for i, p in enumerate(parts[1:], 1):
                if "-" in p and p[0].isdigit():
                    s, e = p.split("-", 1)
                    layer_range = (int(s), int(e))
                elif p == "--top" and i + 1 < len(parts):
                    top_k = int(parts[i + 1])

            n_blocks = len(self.model.transformer.h)
            n_heads = self.model.config.n_head
            total = ((layer_range[1] - layer_range[0]) if layer_range
                     else n_blocks) * n_heads
            print(f"Running head surgery sweep ({total} heads)...",
                  flush=True)

            result = self._head_surgeon.sweep(
                self.model, self.tokenizer, input_ids,
                layer_range=layer_range, top_k=top_k)

            msg_parts = [
                f"Head Surgery Sweep — {result['total_heads_tested']} heads",
                f"  Baseline: '{result['baseline_prediction']}'",
                f"  Heads that change prediction: "
                f"{result['heads_that_change_prediction']}",
                "",
                "  Most important (highest KL when ablated):",
            ]
            for h in result["most_important"][:top_k]:
                msg_parts.append(
                    f"    L{h['layer']:>2d}.H{h['head']:>2d}  "
                    f"KL={h['kl_divergence']:.6f}  "
                    f"{'→ ' + h['ablated_top_token'] if h['prediction_changed'] else '(same)'}")

            if result["least_important"]:
                msg_parts.extend(["", "  Least important (safe to prune):"])
                for h in result["least_important"][:5]:
                    msg_parts.append(
                        f"    L{h['layer']:>2d}.H{h['head']:>2d}  "
                        f"KL={h['kl_divergence']:.6f}")

            return {
                "status": "ok",
                "message": "\n".join(msg_parts),
                "current_location": None, "call_stack": [],
                "local_variables": result, "stdout_new": "", "stderr_new": "",
            }

        return self._error(
            f"Unknown surgery subcommand '{subcmd}'. "
            f"Use: ablate, amplify, sweep, restore, status")

    def cmd_quit(self) -> dict:
        self.is_finished = True
        self.hook_manager.remove_all()
        return {
            "status": "completed",
            "message": "LLM debug session ended.",
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }

    # -- exec_analysis (Tool Forge) ----------------------------------------

    def cmd_exec_analysis(self, args: str) -> dict:
        """Execute custom analysis code against the live model.

        The code must define an ``analyze(model, tokenizer, input_ids)``
        function that returns a dict.  Execution happens in a restricted
        sandbox — only torch, numpy, math, and model access are allowed.

        Usage::

            exec_analysis <python_code>
            exec_analysis @<path_to_file.py>
            exec_analysis --timeout 120 <python_code>
        """
        if not self.model:
            return self._error("Model not loaded. Start the server first.")

        raw = args.strip()
        if not raw:
            return self._error(
                "Usage: exec_analysis <code>\n"
                "  The code must define: analyze(model, tokenizer, input_ids) -> dict\n"
                "  Example:\n"
                '    exec_analysis def analyze(model, tokenizer, input_ids):\n'
                '        return {"num_params": sum(p.numel() for p in model.parameters())}')

        # Parse optional --timeout flag
        timeout = None
        if raw.startswith("--timeout"):
            parts = raw.split(None, 2)
            if len(parts) >= 3:
                try:
                    timeout = int(parts[1])
                except ValueError:
                    return self._error(f"Invalid timeout value: '{parts[1]}'")
                raw = parts[2]
            else:
                return self._error("Usage: exec_analysis --timeout <seconds> <code>")

        # Support @file.py to load code from a file
        if raw.startswith("@"):
            import os
            fpath = raw[1:].strip()
            if not os.path.isfile(fpath):
                return self._error(f"File not found: {fpath}")
            with open(fpath, encoding="utf-8") as f:
                code = f.read()
        else:
            code = raw

        # Get input_ids if a session is active
        input_ids = None
        if self.stepper and self.stepper.is_started and self.stepper.ctx:
            input_ids = self.stepper.ctx.input_ids

        result = self._tool_forge.run(
            code=code,
            model=self.model,
            tokenizer=self.tokenizer,
            input_ids=input_ids,
            timeout=timeout,
        )

        if result["status"] == "error":
            return self._error(f"exec_analysis failed:\n{result['error']}")

        analysis_result = result["result"]

        # Build human-readable message
        lines = ["exec_analysis completed successfully."]
        if isinstance(analysis_result, dict):
            for k, v in analysis_result.items():
                val_str = str(v)
                if len(val_str) > 200:
                    val_str = val_str[:200] + "..."
                lines.append(f"  {k}: {val_str}")
        else:
            lines.append(f"  result: {str(analysis_result)[:500]}")

        return {
            "status": "ok",
            "message": "\n".join(lines),
            "current_location": None,
            "call_stack": [],
            "local_variables": analysis_result if isinstance(analysis_result, dict) else {"result": analysis_result},
            "stdout_new": "",
            "stderr_new": "",
        }

    # -- helpers -----------------------------------------------------------

    def _get_new_stdout(self) -> str:
        if self.stepper and self.stepper.ctx:
            return self.stepper.ctx.generated_text
        return ""

    def _error(self, msg: str) -> dict:
        return {
            "status": "error", "message": msg,
            "current_location": None, "call_stack": [],
            "local_variables": {}, "stdout_new": "", "stderr_new": "",
        }
