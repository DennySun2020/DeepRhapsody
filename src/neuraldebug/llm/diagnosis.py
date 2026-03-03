"""Autonomous LLM diagnosis engine.

Runs a structured diagnostic pipeline on a test suite to find patterns
in model failures and produce actionable recommendations.

Pipeline:
  1. Failure detection — evaluate test suite, find wrong predictions
  2. Per-failure deep analysis — Logit Lens + Patching + Attention + Probing
  3. Cross-case aggregation — statistical fingerprints across failures
  4. Recommendation generation — pattern → fix mapping (via recommendations.py)

Usage:
    engine = DiagnosisEngine(model, tokenizer)
    report = engine.diagnose(test_cases)
"""

import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F

from interpretability import (
    LogitLens, ActivationPatching, AttentionAnalysis, Probing,
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TestCase:
    """A single evaluation case."""
    prompt: str
    expected: str
    category: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class FailureAnalysis:
    """Deep analysis results for a single failing test case."""
    test_case: TestCase
    actual_prediction: str
    actual_prob: float
    expected_prob: float

    prediction_trajectory: List[dict] = field(default_factory=list)
    crossover_layer: Optional[str] = None
    peak_layer: Optional[str] = None

    causal_layers: List[dict] = field(default_factory=list)
    most_causal_layer: Optional[str] = None
    most_causal_recovery: float = 0.0

    head_analysis: List[dict] = field(default_factory=list)
    unfocused_heads: List[dict] = field(default_factory=list)

    probing_results: dict = field(default_factory=dict)


@dataclass
class AggregatedFindings:
    """Statistical patterns across multiple failures."""
    total_test_cases: int = 0
    failures: int = 0
    failure_rate: float = 0.0
    failures_by_category: Dict[str, int] = field(default_factory=dict)

    causal_layer_histogram: Dict[str, int] = field(default_factory=dict)
    dominant_causal_layers: List[str] = field(default_factory=list)

    avg_crossover_depth: float = 0.0
    knowledge_present_but_lost: int = 0
    knowledge_never_present: int = 0

    problematic_heads: List[dict] = field(default_factory=list)
    probing_degradation: List[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Diagnosis engine
# ---------------------------------------------------------------------------

class DiagnosisEngine:
    """Autonomous orchestrator that chains interpretability tools."""

    def __init__(self, model, tokenizer):
        self.model = model
        self.tokenizer = tokenizer
        self.model.eval()

    def diagnose(self, test_cases: List[TestCase],
                 issue: str = "",
                 corruption_strategy: str = "shuffle") -> dict:
        """Run the full diagnostic pipeline.

        Returns a dict with summary, findings, recommendations, and details.
        """
        # Phase 1: find failures
        all_results, failures = self._evaluate_test_suite(test_cases)

        if not failures:
            return {
                "status": "all_passed",
                "total": len(test_cases),
                "failures": 0,
                "message": "All test cases passed. No diagnosis needed.",
            }

        # Phase 2: deep analysis per failure
        analyses = []
        for fail in failures:
            analysis = self._analyze_single_failure(fail, corruption_strategy)
            analyses.append(analysis)

        # Phase 3: aggregate patterns
        findings = self._aggregate_findings(test_cases, analyses)

        # Phase 4: recommendations
        from recommendations import RecommendationEngine
        recs = RecommendationEngine.recommend(findings, analyses)

        return {
            "status": "diagnosis_complete",
            "issue": issue,
            "summary": self._generate_summary(findings),
            "total_cases": len(test_cases),
            "failures": len(failures),
            "failure_rate": round(len(failures) / len(test_cases), 4),
            "findings": _dataclass_to_dict(findings),
            "recommendations": recs,
            "per_failure_details": [
                self._serialize_analysis(a) for a in analyses
            ],
        }

    # ----- Phase 1 -------------------------------------------------------

    @torch.no_grad()
    def _evaluate_test_suite(
        self, test_cases: List[TestCase],
    ) -> Tuple[List[dict], List[dict]]:
        all_results: List[dict] = []
        failures: List[dict] = []

        for tc in test_cases:
            input_ids = self.tokenizer.encode(tc.prompt, return_tensors="pt")
            logits = self._forward_logits(input_ids)
            probs = F.softmax(logits, dim=-1)

            expected_ids = self.tokenizer.encode(
                tc.expected, add_special_tokens=False)
            if not expected_ids:
                continue
            expected_id = expected_ids[0]

            top_id = probs.argmax().item()
            top_token = self.tokenizer.decode([top_id]).strip()
            expected_prob = probs[expected_id].item()
            top_prob = probs[top_id].item()

            result = {
                "test_case": tc,
                "predicted": top_token,
                "predicted_id": top_id,
                "predicted_prob": round(top_prob, 6),
                "expected": tc.expected,
                "expected_id": expected_id,
                "expected_prob": round(expected_prob, 6),
                "passed": top_id == expected_id,
            }
            all_results.append(result)
            if not result["passed"]:
                failures.append(result)

        return all_results, failures

    # ----- Phase 2 -------------------------------------------------------

    @torch.no_grad()
    def _analyze_single_failure(
        self, failure: dict, corruption_strategy: str,
    ) -> FailureAnalysis:
        tc = failure["test_case"]
        input_ids = self.tokenizer.encode(tc.prompt, return_tensors="pt")
        expected_id = failure["expected_id"]

        analysis = FailureAnalysis(
            test_case=tc,
            actual_prediction=failure["predicted"],
            actual_prob=failure["predicted_prob"],
            expected_prob=failure["expected_prob"],
        )

        # --- Logit Lens ---
        lens = LogitLens.run(self.model, self.tokenizer, input_ids, top_k=10)
        for layer_data in lens["layers"]:
            expected_rank = None
            expected_prob_here = 0.0
            for i, pred in enumerate(layer_data["predictions"]):
                if pred["token_id"] == expected_id:
                    expected_rank = i
                    expected_prob_here = pred["probability"]
                    break
            analysis.prediction_trajectory.append({
                "layer": layer_data["layer"],
                "top_prediction": layer_data["top_token"],
                "top_prob": layer_data["top_prob"],
                "entropy": layer_data["entropy"],
                "expected_rank": expected_rank,
                "expected_prob": expected_prob_here,
            })
        self._find_trajectory_events(analysis)

        # --- Activation Patching ---
        corrupted = self._make_corrupted_prompt(
            tc.prompt, corruption_strategy)
        if corrupted and corrupted != tc.prompt:
            try:
                patch = ActivationPatching.run(
                    self.model, self.tokenizer, tc.prompt, corrupted, top_k=5)
                analysis.causal_layers = patch["layers"]
                most = patch["most_causal"]
                analysis.most_causal_layer = most["layer"]
                analysis.most_causal_recovery = most["recovery"]
            except Exception:
                pass  # patching can fail on short prompts

        # --- Attention Analysis ---
        try:
            attn = AttentionAnalysis.analyze_heads(
                self.model, self.tokenizer, input_ids)
            analysis.head_analysis = attn["heads"]
            analysis.unfocused_heads = [
                h for h in attn["heads"] if h["focus_ratio"] < 0.15
            ]
        except Exception:
            pass

        # --- Probing ---
        for task in ["next_token", "token_identity", "position"]:
            try:
                probe = Probing.run(
                    self.model, self.tokenizer, input_ids, task=task)
                if "error" not in probe:
                    analysis.probing_results[task] = probe["layers"]
            except Exception:
                pass

        return analysis

    def _find_trajectory_events(self, analysis: FailureAnalysis):
        best_rank = float("inf")
        best_layer = None
        was_correct = False

        for entry in analysis.prediction_trajectory:
            rank = entry["expected_rank"]
            if rank is not None:
                if rank < best_rank:
                    best_rank = rank
                    best_layer = entry["layer"]
                if rank == 0 and not was_correct:
                    was_correct = True
                elif rank != 0 and was_correct:
                    analysis.crossover_layer = entry["layer"]
                    was_correct = False

        analysis.peak_layer = best_layer

    def _make_corrupted_prompt(self, prompt: str, strategy: str) -> Optional[str]:
        words = prompt.split()
        if len(words) < 3:
            return None

        function_words = {
            "the", "a", "an", "is", "of", "in", "to", "and", "or",
            "for", "with", "on", "at", "by", "was", "are", "were",
        }

        if strategy == "shuffle":
            content_indices = [
                i for i, w in enumerate(words)
                if w.lower().strip(",.?!") not in function_words
            ]
            if len(content_indices) < 2:
                return self._make_corrupted_prompt(prompt, "mask")
            shuffled = words[:]
            content_words = [shuffled[i] for i in content_indices]
            random.shuffle(content_words)
            for i, idx in enumerate(content_indices):
                shuffled[idx] = content_words[i]
            result = " ".join(shuffled)
            return result if result != prompt else None

        if strategy == "mask":
            longest_idx = max(range(len(words)), key=lambda i: len(words[i]))
            masked = words[:]
            masked[longest_idx] = "something"
            return " ".join(masked)

        return None

    # ----- Phase 3 -------------------------------------------------------

    def _aggregate_findings(
        self, all_cases: List[TestCase], analyses: List[FailureAnalysis],
    ) -> AggregatedFindings:
        findings = AggregatedFindings()
        findings.total_test_cases = len(all_cases)
        findings.failures = len(analyses)
        findings.failure_rate = round(len(analyses) / max(len(all_cases), 1), 4)

        for a in analyses:
            cat = a.test_case.category or "uncategorized"
            findings.failures_by_category[cat] = (
                findings.failures_by_category.get(cat, 0) + 1
            )

        # Causal layer histogram
        for a in analyses:
            if a.most_causal_layer:
                findings.causal_layer_histogram[a.most_causal_layer] = (
                    findings.causal_layer_histogram.get(a.most_causal_layer, 0) + 1
                )
        sorted_layers = sorted(
            findings.causal_layer_histogram.items(),
            key=lambda x: x[1], reverse=True,
        )
        findings.dominant_causal_layers = [l[0] for l in sorted_layers[:3]]

        # Trajectory patterns
        crossover_depths: List[int] = []
        for a in analyses:
            if a.crossover_layer:
                idx = _layer_index(a.crossover_layer)
                if idx is not None:
                    crossover_depths.append(idx)

            has_peak = a.peak_layer is not None
            any_high_rank = any(
                t["expected_rank"] is not None and t["expected_rank"] < 5
                for t in a.prediction_trajectory
            )
            if has_peak and a.crossover_layer:
                findings.knowledge_present_but_lost += 1
            elif not any_high_rank:
                findings.knowledge_never_present += 1

        if crossover_depths:
            findings.avg_crossover_depth = round(
                sum(crossover_depths) / len(crossover_depths), 2)

        # Problematic heads
        head_stats = defaultdict(lambda: {"count": 0, "total_focus": 0.0})
        for a in analyses:
            for h in a.unfocused_heads:
                key = (h["layer"], h["head"])
                head_stats[key]["count"] += 1
                head_stats[key]["total_focus"] += h["focus_ratio"]

        n = len(analyses)
        for (layer, head), stats in head_stats.items():
            rate = stats["count"] / n
            if rate >= 0.4:
                findings.problematic_heads.append({
                    "layer": layer,
                    "head": head,
                    "failure_appearance_rate": round(rate, 3),
                    "avg_focus_ratio": round(stats["total_focus"] / stats["count"], 4),
                })
        findings.problematic_heads.sort(
            key=lambda h: h["failure_appearance_rate"], reverse=True)

        # Probing degradation
        if analyses and analyses[0].probing_results.get("next_token"):
            layer_accs: Dict[str, List[float]] = defaultdict(list)
            for a in analyses:
                for ld in a.probing_results.get("next_token", []):
                    layer_accs[ld["layer"]].append(ld["accuracy"])

            avg_accs = {
                layer: round(sum(accs) / len(accs), 4)
                for layer, accs in layer_accs.items()
            }
            layer_names = list(avg_accs.keys())
            for i in range(len(layer_names) - 1):
                for j in range(i + 1, min(i + 4, len(layer_names))):
                    drop = avg_accs[layer_names[i]] - avg_accs[layer_names[j]]
                    if drop > 0.10:
                        findings.probing_degradation.append({
                            "from_layer": layer_names[i],
                            "to_layer": layer_names[j],
                            "from_accuracy": avg_accs[layer_names[i]],
                            "to_accuracy": avg_accs[layer_names[j]],
                            "accuracy_drop": round(drop, 4),
                            "task": "next_token",
                        })
            findings.probing_degradation.sort(
                key=lambda d: d["accuracy_drop"], reverse=True)

        return findings

    # ----- Utilities -----------------------------------------------------

    def _forward_logits(self, input_ids: torch.Tensor) -> torch.Tensor:
        """Manual forward pass returning logits for the last token.

        Uses direct block traversal instead of model() to avoid hangs
        with certain PyTorch/transformers versions on Windows.
        """
        t = self.model.transformer
        tok_emb = t.wte(input_ids)
        seq_len = input_ids.shape[1]
        pos_ids = torch.arange(seq_len, device=input_ids.device).unsqueeze(0)
        pos_emb = t.wpe(pos_ids)
        hidden = t.drop(tok_emb + pos_emb)
        for block in t.h:
            hidden = block(hidden)[0]
        hidden = t.ln_f(hidden)
        logits = self.model.lm_head(hidden)
        return logits[0, -1]

    def _generate_summary(self, findings: AggregatedFindings) -> str:
        lines = []
        lines.append(
            f"{findings.failures}/{findings.total_test_cases} test cases "
            f"failed ({findings.failure_rate:.0%} failure rate).")

        if findings.dominant_causal_layers:
            lines.append(
                f"Most causally responsible layers: "
                f"{', '.join(findings.dominant_causal_layers)}.")

        if findings.knowledge_present_but_lost > 0:
            pct = findings.knowledge_present_but_lost / findings.failures
            lines.append(
                f"In {pct:.0%} of failures the correct answer WAS present "
                f"in intermediate layers but was lost — information routing "
                f"problem.")

        if findings.knowledge_never_present > 0:
            pct = findings.knowledge_never_present / findings.failures
            lines.append(
                f"In {pct:.0%} of failures the correct answer was NEVER "
                f"strongly predicted at any layer — knowledge gap.")

        if findings.problematic_heads:
            heads = ", ".join(
                f"L{h['layer']}H{h['head']}" for h in findings.problematic_heads[:5])
            lines.append(f"Consistently unfocused heads: {heads}.")

        if findings.probing_degradation:
            top = findings.probing_degradation[0]
            lines.append(
                f"Probing accuracy drops {top['from_accuracy']:.0%} → "
                f"{top['to_accuracy']:.0%} between {top['from_layer']} "
                f"and {top['to_layer']}.")

        return " ".join(lines)

    def _serialize_analysis(self, a: FailureAnalysis) -> dict:
        return {
            "prompt": a.test_case.prompt,
            "expected": a.test_case.expected,
            "actual": a.actual_prediction,
            "category": a.test_case.category,
            "expected_prob": round(a.expected_prob, 6),
            "actual_prob": round(a.actual_prob, 6),
            "crossover_layer": a.crossover_layer,
            "peak_layer": a.peak_layer,
            "most_causal_layer": a.most_causal_layer,
            "most_causal_recovery": round(a.most_causal_recovery, 4),
            "num_unfocused_heads": len(a.unfocused_heads),
            "trajectory_summary": [
                {"layer": t["layer"],
                 "top": t["top_prediction"],
                 "expected_rank": t["expected_rank"]}
                for t in a.prediction_trajectory
            ],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _layer_index(layer_name: str) -> Optional[int]:
    try:
        return int(layer_name.split("_")[-1])
    except (ValueError, IndexError):
        return None


def _dataclass_to_dict(obj) -> dict:
    """Recursively convert a dataclass to a JSON-safe dict."""
    result = {}
    for k, v in obj.__dict__.items():
        if isinstance(v, dict):
            result[k] = {str(kk): vv for kk, vv in v.items()}
        elif isinstance(v, list):
            result[k] = v
        else:
            result[k] = v
    return result
