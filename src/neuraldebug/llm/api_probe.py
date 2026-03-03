"""API-based LLM reasoning probe — debug hosted models via their APIs.

When you can't inspect model weights directly (GPT-4, Claude, Gemini),
this module debugs reasoning through black-box techniques:

- **Logprob analysis** — token confidence, entropy, alternatives
- **Prompt perturbation** — swap/remove parts, compare outputs
- **Chain-of-thought extraction** — force step-by-step, compare with direct
- **Consistency testing** — same question N times, measure agreement
- **Counterfactual probing** — test causal factors in the prompt
- **Calibration check** — stated confidence vs actual accuracy

Reuses the existing ``src/agent/providers/`` for API calls.
"""

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class ProbeResult:
    """Result from a single probe technique."""
    technique: str
    summary: str
    details: dict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


class APIProbe:
    """Black-box reasoning probe for API-hosted LLMs.

    Args:
        call_fn: An async-compatible function with signature::

                     call_fn(prompt: str, **kwargs) -> dict

                 Must return at minimum ``{"text": "...", "logprobs": [...]}``.
                 The ``logprobs`` key is optional (not all APIs support it).

        model_name: Display name for the model (e.g. ``"gpt-4o"``).
    """

    def __init__(self, call_fn: Callable, model_name: str = "unknown"):
        self._call = call_fn
        self.model_name = model_name

    # ------------------------------------------------------------------
    # 1. Logprob Analysis
    # ------------------------------------------------------------------

    def analyze_logprobs(self, prompt: str,
                         max_tokens: int = 50) -> ProbeResult:
        """Examine per-token confidence and alternatives.

        Requires the API to return logprobs (OpenAI, Ollama).
        """
        resp = self._call(prompt, max_tokens=max_tokens, logprobs=True,
                          top_logprobs=5)
        text = resp.get("text", "")
        logprobs = resp.get("logprobs", [])

        if not logprobs:
            return ProbeResult(
                technique="logprob_analysis",
                summary="API did not return logprobs — technique unavailable",
                warnings=["logprobs not supported by this provider"],
            )

        tokens = []
        total_entropy = 0.0
        low_confidence = []

        for i, lp in enumerate(logprobs):
            token = lp.get("token", "?")
            prob = math.exp(lp.get("logprob", 0.0))

            # Compute entropy from top alternatives
            alts = lp.get("top_logprobs", [])
            entropy = 0.0
            if alts:
                probs = [math.exp(a.get("logprob", -10)) for a in alts]
                total_p = sum(probs)
                for p in probs:
                    if p > 0 and total_p > 0:
                        pp = p / total_p
                        entropy -= pp * math.log2(pp)

            total_entropy += entropy
            token_info = {
                "token": token,
                "prob": round(prob, 4),
                "entropy": round(entropy, 3),
                "alternatives": [
                    {"token": a.get("token", "?"),
                     "prob": round(math.exp(a.get("logprob", -10)), 4)}
                    for a in alts[:3]
                ],
            }
            tokens.append(token_info)

            if prob < 0.3 and entropy > 2.0:
                low_confidence.append(
                    f"Token {i} '{token}': prob={prob:.3f}, "
                    f"entropy={entropy:.2f}")

        avg_entropy = total_entropy / max(len(logprobs), 1)
        summary = (
            f"Generated {len(tokens)} tokens. "
            f"Avg entropy: {avg_entropy:.2f}. "
            f"{len(low_confidence)} low-confidence tokens."
        )

        return ProbeResult(
            technique="logprob_analysis",
            summary=summary,
            details={
                "tokens": tokens,
                "avg_entropy": round(avg_entropy, 3),
                "low_confidence_tokens": low_confidence,
                "generated_text": text,
            },
        )

    # ------------------------------------------------------------------
    # 2. Prompt Perturbation
    # ------------------------------------------------------------------

    def perturb(self, prompt: str, perturbations: List[Dict[str, str]],
                max_tokens: int = 50) -> ProbeResult:
        """Compare outputs when parts of the prompt are changed.

        Args:
            prompt: Original prompt.
            perturbations: List of ``{"name": ..., "prompt": ...}`` dicts
                with modified versions of the prompt.
        """
        baseline = self._call(prompt, max_tokens=max_tokens)
        baseline_text = baseline.get("text", "")

        results = [{"name": "baseline", "prompt": prompt,
                     "output": baseline_text}]
        changes = []

        for p in perturbations:
            resp = self._call(p["prompt"], max_tokens=max_tokens)
            output = resp.get("text", "")
            changed = output.strip() != baseline_text.strip()
            results.append({
                "name": p["name"],
                "prompt": p["prompt"],
                "output": output,
                "changed": changed,
            })
            if changed:
                changes.append(p["name"])

        summary = (
            f"Tested {len(perturbations)} perturbations. "
            f"{len(changes)} changed the output: "
            + (", ".join(changes) if changes else "(none)")
        )

        return ProbeResult(
            technique="prompt_perturbation",
            summary=summary,
            details={"results": results, "changed": changes},
        )

    # ------------------------------------------------------------------
    # 3. Chain-of-Thought Extraction
    # ------------------------------------------------------------------

    def extract_cot(self, prompt: str,
                    max_tokens: int = 300) -> ProbeResult:
        """Force chain-of-thought reasoning and compare with direct answer.

        Sends the prompt twice:
        1. Direct answer (short max_tokens)
        2. With "Let's think step by step" prefix (longer max_tokens)
        """
        direct = self._call(prompt, max_tokens=50)
        direct_text = direct.get("text", "").strip()

        cot_prompt = prompt.rstrip() + "\nLet's think step by step."
        cot = self._call(cot_prompt, max_tokens=max_tokens)
        cot_text = cot.get("text", "").strip()

        # Check if final answer differs
        direct_short = direct_text[:100].lower()
        cot_short = cot_text[-200:].lower() if len(cot_text) > 200 else cot_text.lower()
        answers_agree = direct_short[:50] in cot_short or cot_short[:50] in direct_short

        summary = (
            f"Direct: \"{direct_text[:80]}{'…' if len(direct_text) > 80 else ''}\"\n"
            f"CoT conclusion agrees: {'yes' if answers_agree else 'NO — potential reasoning error'}"
        )

        return ProbeResult(
            technique="chain_of_thought",
            summary=summary,
            details={
                "direct_answer": direct_text,
                "cot_reasoning": cot_text,
                "answers_agree": answers_agree,
            },
            warnings=[] if answers_agree else [
                "Direct answer and CoT conclusion differ — model may be "
                "confabulating in one mode"],
        )

    # ------------------------------------------------------------------
    # 4. Consistency Testing
    # ------------------------------------------------------------------

    def test_consistency(self, prompt: str, n: int = 5,
                         temperature: float = 0.7,
                         max_tokens: int = 50) -> ProbeResult:
        """Ask the same question N times, measure answer agreement.

        High agreement = factual recall; low agreement = uncertain/confabulating.
        """
        answers = []
        for _ in range(n):
            resp = self._call(prompt, max_tokens=max_tokens,
                              temperature=temperature)
            answers.append(resp.get("text", "").strip())

        # Simple agreement: normalize and count unique
        normalized = [a.lower().strip().rstrip(".") for a in answers]
        counter = Counter(normalized)
        most_common, mc_count = counter.most_common(1)[0]
        agreement_ratio = mc_count / n
        unique_count = len(counter)

        summary = (
            f"Asked {n} times (temp={temperature}). "
            f"{unique_count} unique answers. "
            f"Agreement: {agreement_ratio:.0%} "
            f"({mc_count}/{n} gave \"{most_common[:60]}\")"
        )

        return ProbeResult(
            technique="consistency_testing",
            summary=summary,
            details={
                "answers": answers,
                "agreement_ratio": round(agreement_ratio, 3),
                "unique_answers": unique_count,
                "most_common": most_common,
                "distribution": dict(counter),
            },
            warnings=[] if agreement_ratio >= 0.6 else [
                f"Low agreement ({agreement_ratio:.0%}) — model is uncertain "
                f"or confabulating"],
        )

    # ------------------------------------------------------------------
    # 5. Counterfactual Probing
    # ------------------------------------------------------------------

    def probe_counterfactual(self, prompt: str,
                             counterfactuals: List[Dict[str, str]],
                             max_tokens: int = 50) -> ProbeResult:
        """Test causal factors by replacing key entities.

        Similar to prompt perturbation but specifically targets named
        entities, dates, numbers — the "factual slots" in a prompt.

        Args:
            prompt: Original prompt.
            counterfactuals: List of ``{"entity": ..., "replacement": ...,
                "prompt": ...}`` dicts.
        """
        baseline = self._call(prompt, max_tokens=max_tokens)
        baseline_text = baseline.get("text", "")

        results = [{"type": "baseline", "output": baseline_text}]
        sensitive_to = []

        for cf in counterfactuals:
            resp = self._call(cf["prompt"], max_tokens=max_tokens)
            output = resp.get("text", "")
            changed = output.strip() != baseline_text.strip()
            results.append({
                "entity": cf.get("entity", "?"),
                "replacement": cf.get("replacement", "?"),
                "output": output,
                "changed": changed,
            })
            if changed:
                sensitive_to.append(cf.get("entity", "?"))

        summary = (
            f"Tested {len(counterfactuals)} counterfactuals. "
            f"Output sensitive to: "
            + (", ".join(sensitive_to) if sensitive_to else "(nothing — "
               "model may be ignoring the key entity)")
        )

        return ProbeResult(
            technique="counterfactual_probing",
            summary=summary,
            details={
                "results": results,
                "sensitive_to": sensitive_to,
            },
            warnings=[] if sensitive_to else [
                "Model output didn't change for any counterfactual — "
                "it may not be using the key entity in reasoning"],
        )

    # ------------------------------------------------------------------
    # 6. Calibration Check
    # ------------------------------------------------------------------

    def check_calibration(self, qa_pairs: List[Dict[str, str]],
                          max_tokens: int = 100) -> ProbeResult:
        """Test whether the model's stated confidence matches accuracy.

        Args:
            qa_pairs: List of ``{"question": ..., "answer": ...}`` dicts
                with known correct answers.
        """
        results = []
        correct_count = 0
        confident_correct = 0
        confident_wrong = 0

        for qa in qa_pairs:
            calibration_prompt = (
                f"{qa['question']}\n"
                f"Give your answer, then rate your confidence "
                f"(LOW / MEDIUM / HIGH)."
            )
            resp = self._call(calibration_prompt, max_tokens=max_tokens)
            output = resp.get("text", "").strip()

            # Check correctness (simple substring match)
            is_correct = qa["answer"].lower() in output.lower()
            if is_correct:
                correct_count += 1

            # Parse confidence
            confidence = "unknown"
            output_upper = output.upper()
            if "HIGH" in output_upper:
                confidence = "high"
            elif "MEDIUM" in output_upper:
                confidence = "medium"
            elif "LOW" in output_upper:
                confidence = "low"

            if confidence == "high" and is_correct:
                confident_correct += 1
            elif confidence == "high" and not is_correct:
                confident_wrong += 1

            results.append({
                "question": qa["question"],
                "expected": qa["answer"],
                "output": output[:200],
                "correct": is_correct,
                "stated_confidence": confidence,
            })

        accuracy = correct_count / max(len(qa_pairs), 1)
        summary = (
            f"Accuracy: {correct_count}/{len(qa_pairs)} ({accuracy:.0%}). "
            f"High-confidence correct: {confident_correct}. "
            f"High-confidence WRONG: {confident_wrong}."
        )

        return ProbeResult(
            technique="calibration_check",
            summary=summary,
            details={
                "results": results,
                "accuracy": round(accuracy, 3),
                "confident_correct": confident_correct,
                "confident_wrong": confident_wrong,
            },
            warnings=[] if confident_wrong == 0 else [
                f"{confident_wrong} high-confidence wrong answers — "
                f"model is poorly calibrated"],
        )

    # ------------------------------------------------------------------
    # Run all probes
    # ------------------------------------------------------------------

    def full_probe(self, prompt: str,
                   perturbations: Optional[List[Dict[str, str]]] = None,
                   counterfactuals: Optional[List[Dict[str, str]]] = None,
                   qa_pairs: Optional[List[Dict[str, str]]] = None,
                   consistency_n: int = 5,
                   max_tokens: int = 50) -> Dict[str, ProbeResult]:
        """Run all available probe techniques on a prompt.

        Returns a dict of technique name → ProbeResult.
        """
        results = {}

        results["logprob_analysis"] = self.analyze_logprobs(
            prompt, max_tokens=max_tokens)

        results["chain_of_thought"] = self.extract_cot(
            prompt, max_tokens=300)

        results["consistency"] = self.test_consistency(
            prompt, n=consistency_n, max_tokens=max_tokens)

        if perturbations:
            results["perturbation"] = self.perturb(
                prompt, perturbations, max_tokens=max_tokens)

        if counterfactuals:
            results["counterfactual"] = self.probe_counterfactual(
                prompt, counterfactuals, max_tokens=max_tokens)

        if qa_pairs:
            results["calibration"] = self.check_calibration(
                qa_pairs, max_tokens=max_tokens)

        return results
