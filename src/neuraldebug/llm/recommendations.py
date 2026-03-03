"""Recommendation engine for LLM structural improvements.

Maps diagnostic patterns (from diagnosis.py) to specific, actionable
fixes with generated code.

Each rule is a pure function decorated with @RecommendationEngine.rule.
Rules are tried in registration order; all matching rules produce
recommendations, which are then sorted by confidence + impact.
"""

import textwrap
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Recommendation:
    """A single actionable recommendation."""
    id: str
    title: str
    confidence: str          # "high", "medium", "low"
    impact: str              # "high", "medium", "low"
    category: str            # "fine_tuning", "pruning", "architecture", "data"
    explanation: str
    evidence: List[str]
    code: str
    estimated_improvement: str
    prerequisites: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    graph_diff: str = ""


class RecommendationEngine:
    """Runs diagnostic rules and collects recommendations."""

    RULES: List = []

    @classmethod
    def recommend(cls, findings, analyses) -> List[dict]:
        recs: List[Recommendation] = []
        for rule_fn in cls.RULES:
            try:
                rec = rule_fn(findings, analyses)
            except Exception:
                continue
            if rec is not None:
                if isinstance(rec, list):
                    recs.extend(rec)
                else:
                    recs.append(rec)

        priority = {"high": 3, "medium": 2, "low": 1}
        recs.sort(key=lambda r: (
            priority.get(r.confidence, 0) + priority.get(r.impact, 0)
        ), reverse=True)

        return [r.__dict__ for r in recs]

    @classmethod
    def rule(cls, fn):
        """Decorator to register a diagnostic rule."""
        cls.RULES.append(fn)
        return fn


# =========================================================================
# DIAGNOSTIC RULES
# =========================================================================

def _layer_index(name: str) -> Optional[int]:
    try:
        return int(name.split("_")[-1])
    except (ValueError, IndexError):
        return None


# ---- Rule 1: Targeted LoRA Fine-Tuning --------------------------------

@RecommendationEngine.rule
def rule_targeted_lora(findings, analyses) -> Optional[Recommendation]:
    """Knowledge present early but lost mid-model → LoRA on causal layers."""
    if not findings.dominant_causal_layers:
        return None
    if findings.knowledge_present_but_lost < max(findings.failures * 0.25, 1):
        return None

    layer_indices = []
    for name in findings.dominant_causal_layers:
        idx = _layer_index(name)
        if idx is not None:
            layer_indices.append(idx)
    if not layer_indices:
        return None

    expanded = set()
    for idx in layer_indices:
        expanded.update([max(0, idx - 1), idx, idx + 1])
    layer_list = sorted(expanded)
    layers_str = ", ".join(str(l) for l in layer_list)

    evidence = [
        f"Causal layers: {', '.join(findings.dominant_causal_layers)}",
        f"{findings.knowledge_present_but_lost}/{findings.failures} failures "
        f"show correct answer present early but lost by output",
    ]
    if findings.probing_degradation:
        top = findings.probing_degradation[0]
        evidence.append(
            f"Probing accuracy drops {top['from_accuracy']:.0%} → "
            f"{top['to_accuracy']:.0%} between "
            f"{top['from_layer']} and {top['to_layer']}")

    code = textwrap.dedent(f"""\
        from peft import LoraConfig, get_peft_model, TaskType

        TARGET_LAYERS = {layer_list}

        config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=16,
            lora_alpha=32,
            lora_dropout=0.05,
            target_modules=["c_attn", "c_proj"],
            layers_to_transform=TARGET_LAYERS,
        )

        model = get_peft_model(model, config)
        trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
        total = sum(p.numel() for p in model.parameters())
        print(f"Trainable: {{trainable:,}} / {{total:,}} "
              f"({{100 * trainable / total:.1f}}%)")
    """)

    return Recommendation(
        id="targeted-lora",
        title=f"Targeted LoRA Fine-Tuning (layers {layers_str})",
        confidence="high",
        impact="high",
        category="fine_tuning",
        explanation=(
            f"The model encodes the correct answer in early layers but "
            f"loses it through layers {layers_str}. LoRA adapters on "
            f"just these layers can fix the routing with minimal training."),
        evidence=evidence,
        code=code,
        estimated_improvement="15-40% accuracy improvement on failing cases",
        prerequisites=["pip install peft", "Factual Q&A training dataset"],
        risks=["May slightly reduce performance on non-factual tasks."],
    )


# ---- Rule 2: Head Pruning ---------------------------------------------

@RecommendationEngine.rule
def rule_head_pruning(findings, analyses) -> Optional[Recommendation]:
    """Consistently unfocused heads → prune them."""
    if not findings.problematic_heads:
        return None

    candidates = [
        h for h in findings.problematic_heads
        if h["failure_appearance_rate"] >= 0.4
        and h["avg_focus_ratio"] < 0.20
    ]
    if not candidates:
        return None

    heads_str = ", ".join(
        f"L{h['layer']}H{h['head']}" for h in candidates[:6])

    mask_lines = []
    for h in candidates[:6]:
        mask_lines.append(
            f"    head_mask[{h['layer']}][{h['head']}] = 0  "
            f"# unfocused in {h['failure_appearance_rate']:.0%} of failures")

    code = textwrap.dedent("""\
        import torch

        num_layers = model.config.n_layer
        num_heads = model.config.n_head
        head_mask = torch.ones(num_layers, num_heads)

    """) + "\n".join(mask_lines) + textwrap.dedent("""

        # Apply during forward pass
        outputs = model(input_ids, head_mask=head_mask)
    """)

    return Recommendation(
        id="head-pruning",
        title=f"Prune Attention Heads: {heads_str}",
        confidence="medium",
        impact="medium",
        category="pruning",
        explanation=(
            f"Heads {heads_str} show near-uniform attention across most "
            f"failures — they add noise, not signal. Pruning noisy heads "
            f"often improves performance."),
        evidence=[
            f"Heads {heads_str} unfocused (focus < 0.20) in ≥40% of failures",
        ],
        code=code,
        estimated_improvement="5-15% accuracy improvement + inference speedup",
        risks=["Verify on general benchmark before committing."],
    )


# ---- Rule 3: Knowledge Gap → Training Data ----------------------------

@RecommendationEngine.rule
def rule_knowledge_gap(findings, analyses) -> Optional[Recommendation]:
    """Correct answer never appears at any layer → need more data."""
    if findings.knowledge_never_present < max(findings.failures * 0.25, 1):
        return None

    gap_cats: dict = {}
    for a in analyses:
        any_high = any(
            t["expected_rank"] is not None and t["expected_rank"] < 5
            for t in a.prediction_trajectory
        )
        if not any_high:
            cat = a.test_case.category or "general"
            gap_cats[cat] = gap_cats.get(cat, 0) + 1

    cats_str = ", ".join(
        f"{c} ({n})" for c, n in sorted(
            gap_cats.items(), key=lambda x: x[1], reverse=True)[:5])

    code = textwrap.dedent(f"""\
        # The model lacks knowledge entirely — need more training data.

        from peft import LoraConfig, get_peft_model

        config = LoraConfig(
            r=32,                       # higher rank for knowledge injection
            lora_alpha=64,
            target_modules=["c_attn", "c_proj", "c_fc"],
            # No layers_to_transform — train ALL layers
        )
        model = get_peft_model(model, config)

        # Recommended datasets for domains: {cats_str}
        # - Natural Questions, TriviaQA, MMLU, or domain-specific corpus
    """)

    return Recommendation(
        id="knowledge-gap",
        title="Add Training Data (Knowledge Gap)",
        confidence="high",
        impact="high",
        category="data",
        explanation=(
            f"In {findings.knowledge_never_present}/{findings.failures} "
            f"failures the correct answer was NEVER strongly predicted at "
            f"any layer. The model simply doesn't have this knowledge. "
            f"Affected domains: {cats_str}."),
        evidence=[
            f"{findings.knowledge_never_present} failures with no knowledge",
            f"Affected categories: {cats_str}",
        ],
        code=code,
        estimated_improvement="Depends on data quality",
        prerequisites=["Domain training corpus", "GPU compute"],
    )


# ---- Rule 4: Probing Degradation → Layer Fix --------------------------

@RecommendationEngine.rule
def rule_probing_degradation(findings, analyses) -> Optional[Recommendation]:
    """Sharp accuracy drop between layers → freeze or add skip connection."""
    if not findings.probing_degradation:
        return None

    worst = findings.probing_degradation[0]
    if worst["accuracy_drop"] < 0.15:
        return None

    from_idx = _layer_index(worst["from_layer"])
    to_idx = _layer_index(worst["to_layer"])
    if from_idx is None or to_idx is None:
        return None

    code = textwrap.dedent(f"""\
        # Information destroyed between {worst['from_layer']} and {worst['to_layer']}.

        # Option A: Freeze the destructive layers during fine-tuning
        for i in range({from_idx + 1}, {to_idx + 1}):
            for param in model.transformer.h[i].parameters():
                param.requires_grad = False

        # Option B: Add LoRA only on the layers AROUND the degradation
        from peft import LoraConfig, get_peft_model
        config = LoraConfig(
            r=16, lora_alpha=32,
            target_modules=["c_attn", "c_proj"],
            layers_to_transform=[{from_idx}, {to_idx}],
        )
        model = get_peft_model(model, config)
    """)

    return Recommendation(
        id="probing-degradation",
        title=f"Fix Information Loss: {worst['from_layer']} → {worst['to_layer']}",
        confidence="medium",
        impact="medium",
        category="architecture",
        explanation=(
            f"Probing accuracy drops from {worst['from_accuracy']:.0%} "
            f"to {worst['to_accuracy']:.0%} between {worst['from_layer']} "
            f"and {worst['to_layer']}. These layers are destroying useful "
            f"information."),
        evidence=[
            f"Accuracy drop: {worst['accuracy_drop']:.0%} over "
            f"{(to_idx or 0) - (from_idx or 0)} layers",
        ],
        code=code,
        estimated_improvement="10-20% accuracy improvement",
        risks=["Start with freezing (Option A) — it's zero-risk."],
    )


# ---- Rule 5: Late-Layer Regression ------------------------------------

@RecommendationEngine.rule
def rule_late_regression(findings, analyses) -> Optional[Recommendation]:
    """Correct in middle layers, wrong at output → early exit or retrain."""
    regression_cases = 0
    regression_layers: list = []

    for a in analyses:
        if a.peak_layer and a.crossover_layer:
            peak_idx = _layer_index(a.peak_layer)
            cross_idx = _layer_index(a.crossover_layer)
            if peak_idx is not None and cross_idx is not None and cross_idx > peak_idx:
                regression_cases += 1
                regression_layers.append(cross_idx)

    if regression_cases < max(findings.failures * 0.25, 1):
        return None
    if not regression_layers:
        return None

    avg_reg = sum(regression_layers) / len(regression_layers)
    optimal = max(0, int(avg_reg) - 2)

    code = textwrap.dedent(f"""\
        # The model predicts correctly at middle layers but regresses.

        # Option A: Early exit — use predictions from layer {optimal}
        hidden = model.transformer.wte(input_ids) + model.transformer.wpe(pos_ids)
        hidden = model.transformer.drop(hidden)
        for i in range({optimal + 1}):
            hidden = model.transformer.h[i](hidden)[0]
        hidden = model.transformer.ln_f(hidden)
        logits = model.lm_head(hidden)

        # Option B: Fine-tune only the final layers
        for i in range(0, {optimal}):
            for param in model.transformer.h[i].parameters():
                param.requires_grad = False
    """)

    return Recommendation(
        id="late-regression",
        title="Fix Late-Layer Prediction Regression",
        confidence="medium",
        impact="high",
        category="architecture",
        explanation=(
            f"In {regression_cases}/{findings.failures} failures the model "
            f"correctly predicts the answer around layer {optimal} but then "
            f"'changes its mind' in later layers. The final layers actively "
            f"suppress the correct answer."),
        evidence=[
            f"{regression_cases} failures show correct→incorrect regression",
            f"Average regression starts at layer {avg_reg:.0f}",
        ],
        code=code,
        estimated_improvement="20-50% accuracy recovery on regressing cases",
        risks=["Early exit reduces expressiveness for complex reasoning."],
    )


# =========================================================================
# ARCHITECTURAL RECOMMENDATION RULES
# =========================================================================

# ---- Rule 6: Retrieval Augmentation ------------------------------------

@RecommendationEngine.rule
def rule_retrieval_augmentation(findings, analyses) -> Optional[List[Recommendation]]:
    """Pure knowledge gap → suggest retrieval-augmented generation."""
    if findings.knowledge_never_present < max(findings.failures * 0.5, 1):
        return None

    pct = findings.knowledge_never_present / max(findings.failures, 1)

    code = textwrap.dedent("""\
        # Architecture change: Add retrieval module before transformer blocks.
        # This is a structural suggestion — requires a new model wrapper.

        class RetrievalAugmentedGPT2(torch.nn.Module):
            def __init__(self, base_model, retriever, top_k=3):
                super().__init__()
                self.base_model = base_model
                self.retriever = retriever  # e.g. FAISS index + encoder
                self.top_k = top_k
                d_model = base_model.config.n_embd
                # Cross-attention to attend over retrieved documents
                self.cross_attn = torch.nn.MultiheadAttention(
                    embed_dim=d_model, num_heads=8, batch_first=True)
                self.gate = torch.nn.Linear(d_model * 2, d_model)

            def forward(self, input_ids, **kwargs):
                # 1. Retrieve relevant documents
                query_emb = self.base_model.transformer.wte(input_ids).mean(dim=1)
                retrieved = self.retriever.search(query_emb, k=self.top_k)

                # 2. Encode retrieved docs
                doc_embs = self.retriever.encode(retrieved)  # [B, K, D]

                # 3. Run base embedding
                hidden = self.base_model.transformer.wte(input_ids)
                hidden = hidden + self.base_model.transformer.wpe(pos_ids)

                # 4. Cross-attend to retrieved knowledge
                attended, _ = self.cross_attn(hidden, doc_embs, doc_embs)
                hidden = self.gate(torch.cat([hidden, attended], dim=-1))

                # 5. Continue through transformer blocks
                for block in self.base_model.transformer.h:
                    hidden = block(hidden)[0]
                hidden = self.base_model.transformer.ln_f(hidden)
                return self.base_model.lm_head(hidden)
    """)

    graph_diff = textwrap.dedent("""\
        PROPOSED COMPUTE GRAPH MODIFICATION:

        CURRENT:
          embedding → block_0 → block_1 → ... → block_N → ln_f → lm_head

        PROPOSED (add retrieval + cross-attention after embedding):
          embedding ─┬─→ cross_attn(query=hidden, kv=retrieved_docs)
                     │        ↓
          retriever ─┘   gated_fusion
                              ↓
                         block_0 → block_1 → ... → block_N → ln_f → lm_head

        NEW NODES:
          + retriever         (FAISS/dense retrieval)     [external, ~0 params in model]
          + cross_attn        (MultiheadAttention)        [+4×d²  = +4.2M params]
          + gate              (Linear, 2d → d)            [+2×d²  = +2.1M params]
          Total new params: ~6.3M (+1.8% of base model)
    """)

    return Recommendation(
        id="retrieval-augmentation",
        title="Add Retrieval-Augmented Generation (RAG)",
        confidence="high",
        impact="high",
        category="architecture",
        explanation=(
            f"In {pct:.0%} of failures the correct answer was NEVER present "
            f"at any layer — the model simply doesn't have this knowledge in "
            f"its weights. A retrieval module can supply external facts at "
            f"inference time without retraining the full model."),
        evidence=[
            f"{findings.knowledge_never_present}/{findings.failures} failures "
            f"show complete knowledge absence",
            "Expected token never reaches top-10 at any layer",
        ],
        code=code,
        estimated_improvement="50-80% accuracy on factual queries",
        prerequisites=["Document corpus or knowledge base", "FAISS or similar index"],
        risks=[
            "Adds inference latency (~50ms per retrieval call)",
            "Requires maintaining an external knowledge index",
            "Cannot be applied: NeuralDebug can suggest but not build this automatically",
        ],
        graph_diff=graph_diff,
    )


# ---- Rule 7: Wider FFN / Mixture of Experts ---------------------------

@RecommendationEngine.rule
def rule_wider_ffn_or_moe(findings, analyses) -> Optional[Recommendation]:
    """FFN layers are the bottleneck → suggest wider FFN or MoE."""
    ffn_causal = 0
    for a in analyses:
        for cl in a.causal_layers:
            if "ffn" in cl.get("layer", "").lower() or (
                cl.get("recovery", 0) > 0.5
                and _layer_index(cl.get("layer", "")) is not None
            ):
                ffn_causal += 1
                break

    if ffn_causal < max(findings.failures * 0.3, 1):
        if not findings.probing_degradation:
            return None
        worst = findings.probing_degradation[0]
        if worst["accuracy_drop"] < 0.2:
            return None

    code = textwrap.dedent("""\
        # Option A: Wider FFN (simple, effective)
        # Replace c_fc (d→4d) with c_fc (d→8d) in bottleneck layers
        import torch.nn as nn

        for i in BOTTLENECK_LAYERS:
            block = model.transformer.h[i]
            d_model = model.config.n_embd       # 1024
            d_ff_new = d_model * 8               # 8192 (was 4096)
            block.mlp.c_fc = nn.Linear(d_model, d_ff_new)
            block.mlp.c_proj = nn.Linear(d_ff_new, d_model)
            # Re-initialize and fine-tune these layers

        # Option B: Mixture of Experts (more capacity, same compute)
        class MoEFFN(nn.Module):
            def __init__(self, d_model, d_ff, num_experts=4, top_k=2):
                super().__init__()
                self.gate = nn.Linear(d_model, num_experts)
                self.experts = nn.ModuleList([
                    nn.Sequential(
                        nn.Linear(d_model, d_ff),
                        nn.GELU(),
                        nn.Linear(d_ff, d_model),
                    ) for _ in range(num_experts)
                ])
                self.top_k = top_k

            def forward(self, x):
                gate_logits = self.gate(x)
                weights, indices = gate_logits.topk(self.top_k, dim=-1)
                weights = torch.softmax(weights, dim=-1)
                out = torch.zeros_like(x)
                for i, expert in enumerate(self.experts):
                    mask = (indices == i).any(dim=-1)
                    if mask.any():
                        out[mask] += weights[mask, (indices[mask] == i).nonzero()[:, 1]].unsqueeze(-1) * expert(x[mask])
                return out
    """)

    graph_diff = textwrap.dedent("""\
        PROPOSED COMPUTE GRAPH MODIFICATION:

        CURRENT (per block):
          ln_2 → c_fc [d→4d] → GELU → c_proj [4d→d] → + residual

        OPTION A — Wider FFN:
          ln_2 → c_fc [d→8d] → GELU → c_proj [8d→d] → + residual
          Params: +8.4M per modified block

        OPTION B — Mixture of Experts:
          ln_2 ─→ gate [d→4]
                   ↓ (top-2 routing)
               ┌─ expert_0: fc_up→GELU→fc_down
               ├─ expert_1: fc_up→GELU→fc_down
               ├─ expert_2: fc_up→GELU→fc_down
               └─ expert_3: fc_up→GELU→fc_down
                   ↓ (weighted sum)
               → + residual
          Params: +25.2M per MoE block (4× experts), but only 2× compute (top-2)
    """)

    return Recommendation(
        id="wider-ffn-or-moe",
        title="Wider FFN or Mixture of Experts",
        confidence="medium",
        impact="high",
        category="architecture",
        explanation=(
            "The FFN layers appear to be a capacity bottleneck — they can't "
            "distinguish between similar entities or encode enough factual "
            "knowledge. Widening the FFN or using Mixture of Experts adds "
            "capacity with manageable compute cost."),
        evidence=[
            f"{ffn_causal} failures show FFN layers as causally responsible",
        ] + ([
            f"Probing degradation: {findings.probing_degradation[0]['accuracy_drop']:.0%} "
            f"drop"
        ] if findings.probing_degradation else []),
        code=code,
        estimated_improvement="10-30% accuracy on knowledge-intensive tasks",
        prerequisites=["GPU for training", "Fine-tuning dataset"],
        risks=[
            "Wider FFN: increases memory and compute linearly",
            "MoE: adds routing complexity; load balancing needed",
            "Cannot be applied: NeuralDebug can suggest but not build this automatically",
        ],
        graph_diff=graph_diff,
    )


# ---- Rule 8: More Attention Heads / Different Head Dim ----------------

@RecommendationEngine.rule
def rule_attention_restructure(findings, analyses) -> Optional[Recommendation]:
    """Many unfocused heads → restructure attention."""
    if not findings.problematic_heads:
        return None

    unfocused_pct = len(findings.problematic_heads)
    # Get total heads from first analysis
    total_heads = 0
    for a in analyses:
        if a.head_analysis:
            total_heads = len(a.head_analysis)
            break
    if total_heads == 0:
        return None

    ratio = unfocused_pct / total_heads
    if ratio < 0.1:
        return None

    heads_str = ", ".join(
        f"L{h['layer']}H{h['head']}" for h in findings.problematic_heads[:8])

    code = textwrap.dedent("""\
        # Restructure attention: replace unfocused heads with grouped-query attention
        # or add more specialized heads

        import torch.nn as nn

        # Option A: Multi-Query Attention (fewer KV heads, more Q heads)
        # Reduces KV cache size, forces heads to specialize
        class MultiQueryAttention(nn.Module):
            def __init__(self, d_model, n_q_heads=32, n_kv_heads=8):
                super().__init__()
                self.n_q_heads = n_q_heads
                self.n_kv_heads = n_kv_heads
                self.head_dim = d_model // n_q_heads
                self.q_proj = nn.Linear(d_model, n_q_heads * self.head_dim)
                self.k_proj = nn.Linear(d_model, n_kv_heads * self.head_dim)
                self.v_proj = nn.Linear(d_model, n_kv_heads * self.head_dim)
                self.o_proj = nn.Linear(d_model, d_model)

        # Option B: Add local attention heads alongside global ones
        # Some heads attend only to nearby tokens (window=128)
        # while others attend globally — reduces uniform attention problem
    """)

    graph_diff = textwrap.dedent(f"""\
        PROPOSED COMPUTE GRAPH MODIFICATION:

        CURRENT (attention sub-tree):
          ln_1 → c_attn [d→3d] → split Q,K,V → {total_heads} heads × softmax(QKᵀ/√d_h) → concat → c_proj

        PROPOSED — Grouped-Query Attention:
          ln_1 → q_proj [d→32×d_h] ─────────→ 32 Q heads ─┐
               → k_proj [d→8×d_h]  → repeat ──→ 32 K heads ─┤ softmax(QKᵀ/√d_h)
               → v_proj [d→8×d_h]  → repeat ──→ 32 V heads ─┘
               → concat → o_proj [d→d]

        EFFECT:
          - Forces Q heads to specialize (32 > {total_heads} current)
          - Fewer KV heads (8) reduces redundancy
          - Params: similar total, but better utilization
          - Unfocused heads ({heads_str}) would be replaced by specialized ones
    """)

    return Recommendation(
        id="attention-restructure",
        title=f"Restructure Attention ({unfocused_pct} unfocused heads)",
        confidence="medium",
        impact="medium",
        category="architecture",
        explanation=(
            f"{unfocused_pct}/{total_heads} attention heads "
            f"({ratio:.0%}) show near-uniform attention patterns across "
            f"failures. These heads waste capacity. Restructuring to "
            f"grouped-query attention or adding local attention heads "
            f"can improve focus and factual recall."),
        evidence=[
            f"Unfocused heads: {heads_str}",
            f"{ratio:.0%} of heads show focus_ratio < 0.15 in ≥40% of failures",
        ],
        code=code,
        estimated_improvement="5-20% on attention-dependent tasks",
        prerequisites=["Full model retraining or distillation"],
        risks=[
            "Requires retraining from scratch or careful distillation",
            "Cannot be applied: NeuralDebug can suggest but not build this automatically",
        ],
        graph_diff=graph_diff,
    )


# ---- Rule 9: Adapter Layer Insertion ----------------------------------

@RecommendationEngine.rule
def rule_adapter_insertion(findings, analyses) -> Optional[Recommendation]:
    """Specific bottleneck layers → insert adapter modules."""
    if not findings.dominant_causal_layers:
        return None

    layer_indices = []
    for name in findings.dominant_causal_layers:
        idx = _layer_index(name)
        if idx is not None:
            layer_indices.append(idx)
    if not layer_indices:
        return None

    layers_str = ", ".join(str(i) for i in sorted(layer_indices))

    code = textwrap.dedent(f"""\
        # Insert bottleneck adapter modules at causal layers
        import torch.nn as nn

        class Adapter(nn.Module):
            def __init__(self, d_model, bottleneck=64):
                super().__init__()
                self.down = nn.Linear(d_model, bottleneck)
                self.act = nn.GELU()
                self.up = nn.Linear(bottleneck, d_model)
                self.scale = nn.Parameter(torch.ones(1) * 0.1)

            def forward(self, x):
                return x + self.scale * self.up(self.act(self.down(x)))

        # Insert adapters AFTER the attention and FFN outputs
        TARGET_LAYERS = {sorted(layer_indices)}
        d_model = model.config.n_embd  # 1024

        for i in TARGET_LAYERS:
            block = model.transformer.h[i]
            block.attn_adapter = Adapter(d_model, bottleneck=64)
            block.ffn_adapter = Adapter(d_model, bottleneck=64)

            # Monkey-patch the block's forward to include adapters
            original_forward = block.forward
            def make_adapter_forward(blk, orig_fwd):
                def adapter_forward(hidden_states, **kwargs):
                    out = orig_fwd(hidden_states, **kwargs)
                    h = out[0] if isinstance(out, tuple) else out
                    h = blk.attn_adapter(h)
                    h = blk.ffn_adapter(h)
                    return (h,) + out[1:] if isinstance(out, tuple) else h
                return adapter_forward
            block.forward = make_adapter_forward(block, original_forward)

        # Freeze base model, train only adapters
        for p in model.parameters():
            p.requires_grad = False
        for i in TARGET_LAYERS:
            for p in model.transformer.h[i].attn_adapter.parameters():
                p.requires_grad = True
            for p in model.transformer.h[i].ffn_adapter.parameters():
                p.requires_grad = True
    """)

    graph_diff = textwrap.dedent(f"""\
        PROPOSED COMPUTE GRAPH MODIFICATION:

        CURRENT (block {layer_indices[0]}):
          attn_output + residual → ln_2 → FFN → ffn_output + residual

        PROPOSED (add adapters at layers {layers_str}):
          attn_output + residual → [attn_adapter: down→GELU→up + skip]
                                        ↓
                                   ln_2 → FFN → ffn_output + residual
                                        ↓
                                   [ffn_adapter: down→GELU→up + skip]

        NEW NODES (per adapted block):
          + attn_adapter.down    Linear [d→64]     [+65K params]
          + attn_adapter.up      Linear [64→d]     [+65K params]
          + ffn_adapter.down     Linear [d→64]     [+65K params]
          + ffn_adapter.up       Linear [64→d]     [+65K params]
          Total per block: ~262K params
          Total for {len(layer_indices)} blocks: ~{len(layer_indices) * 262}K params (+{len(layer_indices) * 262 / 1000:.1f}M)
    """)

    return Recommendation(
        id="adapter-insertion",
        title=f"Insert Adapter Modules (layers {layers_str})",
        confidence="high",
        impact="medium",
        category="architecture",
        explanation=(
            f"Layers {layers_str} are the dominant causal bottleneck. "
            f"Inserting lightweight adapter modules (down-project → GELU → "
            f"up-project with skip connection) adds targeted capacity with "
            f"minimal parameter overhead. Unlike LoRA, adapters add new "
            f"capacity rather than modifying existing weights."),
        evidence=[
            f"Dominant causal layers: {', '.join(findings.dominant_causal_layers)}",
        ],
        code=code,
        estimated_improvement="10-25% accuracy improvement",
        prerequisites=["Fine-tuning dataset for adapter training"],
        risks=[
            "Slightly increases inference latency (~2% per adapted block)",
            "Adapters can be applied programmatically by NeuralDebug",
        ],
        graph_diff=graph_diff,
    )
