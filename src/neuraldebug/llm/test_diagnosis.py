#!/usr/bin/env python3
"""Test script for the autonomous LLM diagnosis pipeline."""

import json
import os
import sys
import time
from pathlib import Path

# Setup paths
_this_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_this_dir))
sys.path.insert(0, str(_this_dir.parent))  # for debug_common

os.environ["TRANSFORMERS_NO_TF"] = "1"
os.environ["USE_TORCH"] = "1"

from diagnosis import DiagnosisEngine, TestCase


def main():
    print("=" * 60)
    print("NeuralDebug Autonomous LLM Diagnosis — Test Run")
    print("=" * 60)

    # Load test cases
    test_file = _this_dir / "test_cases.json"
    with open(test_file) as f:
        raw = json.load(f)
    test_cases = [
        TestCase(prompt=tc["prompt"], expected=tc["expected"],
                 category=tc.get("category", ""))
        for tc in raw
    ]
    print(f"\nLoaded {len(test_cases)} test cases from {test_file.name}")

    # Load model
    print("\nLoading model 'distilgpt2'...")
    t0 = time.time()
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained("distilgpt2")
    model = AutoModelForCausalLM.from_pretrained(
        "distilgpt2", attn_implementation="eager")
    model.eval()
    print(f"Model loaded in {time.time() - t0:.1f}s "
          f"({sum(p.numel() for p in model.parameters()):,} params, "
          f"{len(model.transformer.h)} blocks)")

    # Run diagnosis
    print("\n" + "=" * 60)
    print("Running autonomous diagnosis...")
    print("=" * 60)
    t0 = time.time()
    engine = DiagnosisEngine(model, tokenizer)
    report = engine.diagnose(test_cases, issue="Testing factual recall")
    elapsed = time.time() - t0

    # Print results
    print(f"\nDiagnosis completed in {elapsed:.1f}s")
    print(f"Status: {report['status']}")
    print(f"Failures: {report['failures']}/{report['total_cases']} "
          f"({report['failure_rate']:.0%})")

    print("\n" + "-" * 60)
    print("SUMMARY")
    print("-" * 60)
    print(report["summary"])

    findings = report.get("findings", {})
    if findings.get("dominant_causal_layers"):
        print(f"\nDominant causal layers: "
              f"{findings['dominant_causal_layers']}")
    if findings.get("causal_layer_histogram"):
        print(f"Causal histogram: {findings['causal_layer_histogram']}")
    if findings.get("problematic_heads"):
        print(f"Problematic heads ({len(findings['problematic_heads'])}):")
        for h in findings["problematic_heads"][:5]:
            print(f"  L{h['layer']}H{h['head']} — "
                  f"appears in {h['failure_appearance_rate']:.0%} of failures, "
                  f"avg focus={h['avg_focus_ratio']:.3f}")
    if findings.get("probing_degradation"):
        print(f"Probing degradation ({len(findings['probing_degradation'])} drops):")
        for d in findings["probing_degradation"][:3]:
            print(f"  {d['from_layer']} ({d['from_accuracy']:.0%}) → "
                  f"{d['to_layer']} ({d['to_accuracy']:.0%}), "
                  f"drop={d['accuracy_drop']:.0%}")

    print(f"\nKnowledge present but lost: "
          f"{findings.get('knowledge_present_but_lost', 0)}")
    print(f"Knowledge never present: "
          f"{findings.get('knowledge_never_present', 0)}")

    print("\n" + "-" * 60)
    print(f"RECOMMENDATIONS ({len(report.get('recommendations', []))})")
    print("-" * 60)
    for i, rec in enumerate(report.get("recommendations", []), 1):
        print(f"\n[{i}] [{rec['confidence'].upper()} / {rec['impact'].upper()}] "
              f"{rec['title']}")
        print(f"    Category: {rec['category']}")
        print(f"    {rec['explanation']}")
        print(f"    Evidence:")
        for e in rec.get("evidence", []):
            print(f"      - {e}")
        print(f"    Estimated improvement: {rec['estimated_improvement']}")
        if rec.get("code"):
            code_lines = rec["code"].strip().split("\n")
            print(f"    Code ({len(code_lines)} lines):")
            for line in code_lines[:8]:
                print(f"      {line}")
            if len(code_lines) > 8:
                print(f"      ... ({len(code_lines) - 8} more lines)")

    print("\n" + "-" * 60)
    print(f"PER-FAILURE DETAILS ({len(report.get('per_failure_details', []))})")
    print("-" * 60)
    for detail in report.get("per_failure_details", []):
        print(f"\n  Prompt: \"{detail['prompt']}\"")
        print(f"  Expected: {detail['expected']}, Got: {detail['actual']}")
        print(f"  Expected prob: {detail['expected_prob']:.4f}, "
              f"Actual prob: {detail['actual_prob']:.4f}")
        print(f"  Peak layer: {detail['peak_layer']}, "
              f"Crossover: {detail['crossover_layer']}")
        print(f"  Most causal: {detail['most_causal_layer']} "
              f"(recovery={detail['most_causal_recovery']:.3f})")
        # Show trajectory
        traj = detail.get("trajectory_summary", [])
        if traj:
            key_layers = [t for t in traj if t["expected_rank"] is not None
                         and t["expected_rank"] < 5]
            if key_layers:
                print(f"  Expected in top-5 at: "
                      + ", ".join(f"{t['layer']}(rank={t['expected_rank']})"
                                 for t in key_layers[:5]))

    # Save full report
    report_path = _this_dir / "diagnosis_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n{'=' * 60}")
    print(f"Full report saved to: {report_path}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
