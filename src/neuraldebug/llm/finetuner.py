"""LoRA fine-tuning engine for knowledge injection.

When the diagnosis pipeline detects a knowledge gap (the model has never
seen a fact), this module can run a lightweight LoRA fine-tuning session
to teach the model new knowledge — then verify the fix worked.

Pipeline:
  1. Generate training examples from user-supplied fact(s)
  2. Attach LoRA adapters to the base model
  3. Train for a small number of steps (50-200)
  4. Verify the model now produces the expected output
  5. Save the full merged model to disk for persistence

Usage:
    finetuner = LoRAFinetuner(model, tokenizer)
    result = finetuner.run(
        facts=["Dr. Elena Vasquez is the director of Horizon Research Labs"],
        verification_prompt="Who is Dr. Elena Vasquez? Dr. Elena Vasquez is",
        expected_token="the",
    )
"""

import copy
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

_FINETUNED_DIR = "NeuralDebug-finetuned"


def get_finetuned_model_dir(model_name: str) -> Path:
    """Return the directory where fine-tuned weights are saved.

    Uses ``~/.cache/huggingface/hub/NeuralDebug-finetuned/<model_name>/``
    to keep fine-tuned models alongside the HuggingFace cache.
    """
    cache_root = Path(os.environ.get(
        "HF_HOME",
        os.environ.get("HUGGINGFACE_HUB_CACHE",
                        Path.home() / ".cache" / "huggingface" / "hub"),
    ))
    return cache_root / _FINETUNED_DIR / model_name.replace("/", "--")


def has_finetuned_model(model_name: str) -> bool:
    """Check whether a persisted fine-tuned model exists on disk."""
    d = get_finetuned_model_dir(model_name)
    return (d / "config.json").is_file() and (
        (d / "model.safetensors").is_file()
        or any(d.glob("pytorch_model*.bin"))
    )


def _dir_size_mb(path: Path) -> float:
    """Total size in MB of all files in a directory."""
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1e6


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TrainingExample:
    """A single training text."""
    text: str
    source: str = "generated"


@dataclass
class FinetuneConfig:
    """Configuration for a LoRA fine-tuning session."""
    lora_r: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    target_modules: Optional[List[str]] = None
    learning_rate: float = 1e-4
    num_steps: int = 100
    batch_size: int = 1
    max_seq_len: int = 128
    warmup_steps: int = 10
    weight_decay: float = 0.01
    save_path: Optional[str] = None
    num_paraphrases: int = 8
    auto_save: bool = True  # persist full merged model to disk


@dataclass
class FinetuneResult:
    """Result of a fine-tuning session."""
    success: bool
    steps_completed: int
    final_loss: float
    training_losses: List[float]
    verification_before: Dict
    verification_after: Dict
    elapsed_seconds: float
    adapter_path: Optional[str] = None
    saved_model_path: Optional[str] = None
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "steps_completed": self.steps_completed,
            "final_loss": round(self.final_loss, 4),
            "training_losses_sample": [
                round(l, 4) for l in self.training_losses[::max(1, len(self.training_losses) // 10)]
            ],
            "verification_before": self.verification_before,
            "verification_after": self.verification_after,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "adapter_path": self.adapter_path,
            "saved_model_path": self.saved_model_path,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Training data generator
# ---------------------------------------------------------------------------

_PARAPHRASE_TEMPLATES = [
    "{fact}",
    "It is known that {fact_lower}",
    "According to public records, {fact_lower}",
    "In summary, {fact_lower}",
    "{fact} This is a well-known fact.",
    "Q: {question}\nA: {fact}",
    "The answer is that {fact_lower}",
    "{fact} {fact}",
]

_QUESTION_TEMPLATES = [
    "Who is {subject}?",
    "What do you know about {subject}?",
    "Tell me about {subject}.",
]


def generate_training_data(
    facts: List[str],
    num_paraphrases: int = 8,
) -> List[TrainingExample]:
    """Generate training examples from user-supplied facts.

    Produces paraphrased versions of each fact to improve generalisation
    and reduce overfitting to a single phrasing.
    """
    examples: List[TrainingExample] = []

    for fact in facts:
        fact = fact.strip()
        if not fact:
            continue

        fact_lower = fact[0].lower() + fact[1:] if len(fact) > 1 else fact.lower()

        # Try to extract a subject for question-style paraphrases
        subject = _extract_subject(fact)

        # Always include the raw fact
        examples.append(TrainingExample(text=fact, source="original"))

        templates_used = 0
        for template in _PARAPHRASE_TEMPLATES:
            if templates_used >= num_paraphrases:
                break
            try:
                if "{question}" in template:
                    if subject:
                        for q_template in _QUESTION_TEMPLATES:
                            if templates_used >= num_paraphrases:
                                break
                            question = q_template.format(subject=subject)
                            text = template.format(
                                fact=fact, fact_lower=fact_lower, question=question)
                            examples.append(TrainingExample(
                                text=text, source="paraphrase"))
                            templates_used += 1
                else:
                    text = template.format(fact=fact, fact_lower=fact_lower)
                    examples.append(TrainingExample(
                        text=text, source="paraphrase"))
                    templates_used += 1
            except (KeyError, IndexError):
                continue

    return examples


def _extract_subject(fact: str) -> Optional[str]:
    """Try to extract the subject from a fact like 'X is Y'."""
    for sep in [" is ", " was ", " serves as ", " works as "]:
        if sep in fact:
            return fact.split(sep, 1)[0].strip()
    return None


# ---------------------------------------------------------------------------
# Verification helper
# ---------------------------------------------------------------------------

def _verify_prediction(
    model,
    tokenizer,
    prompt: str,
    expected_token: str,
    top_k: int = 10,
) -> Dict:
    """Check what the model predicts for the next token after prompt."""
    input_ids = tokenizer.encode(prompt, return_tensors="pt")
    device = next(model.parameters()).device
    input_ids = input_ids.to(device)

    with torch.no_grad():
        out = model(input_ids)
        logits = out.logits[0, -1]

    probs = F.softmax(logits, dim=-1)

    # Check expected token
    exp_ids = tokenizer.encode(" " + expected_token)
    if not exp_ids:
        exp_ids = tokenizer.encode(expected_token)
    # Try both with and without space prefix
    best_prob = 0.0
    best_id = None
    for candidate in [expected_token, " " + expected_token]:
        cand_ids = tokenizer.encode(candidate)
        if cand_ids:
            cand_id = cand_ids[-1]
            p = probs[cand_id].item()
            if p > best_prob:
                best_prob = p
                best_id = cand_id

    # Top-k predictions
    topk_probs, topk_ids = probs.topk(top_k)
    top_predictions = []
    for p, idx in zip(topk_probs.tolist(), topk_ids.tolist()):
        top_predictions.append({
            "token": tokenizer.decode([idx]),
            "prob": round(p, 4),
        })

    expected_rank = None
    if best_id is not None:
        sorted_ids = probs.argsort(descending=True).tolist()
        if best_id in sorted_ids:
            expected_rank = sorted_ids.index(best_id) + 1

    return {
        "prompt": prompt,
        "expected_token": expected_token,
        "expected_prob": round(best_prob, 4),
        "expected_rank": expected_rank,
        "top_prediction": top_predictions[0]["token"] if top_predictions else "?",
        "top_predictions": top_predictions,
        "in_top_10": expected_rank is not None and expected_rank <= 10,
    }


# ---------------------------------------------------------------------------
# LoRA fine-tuner
# ---------------------------------------------------------------------------

class LoRAFinetuner:
    """Lightweight LoRA fine-tuning for knowledge injection."""

    def __init__(self, model, tokenizer, model_name: str = "unknown"):
        self.base_model = model
        self.tokenizer = tokenizer
        self.model_name = model_name

        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

    def run(
        self,
        facts: List[str],
        verification_prompt: str,
        expected_token: str,
        config: Optional[FinetuneConfig] = None,
    ) -> FinetuneResult:
        """Run a complete fine-tuning session.

        Args:
            facts: List of factual statements to teach the model.
            verification_prompt: Prompt to test after fine-tuning.
            expected_token: The token the model should predict.
            config: Fine-tuning configuration. Uses defaults if None.

        Returns:
            FinetuneResult with training metrics and verification.
        """
        if config is None:
            config = FinetuneConfig()

        t_start = time.time()

        # Step 1: Verify baseline (before fine-tuning)
        print("  [1/5] Verifying baseline prediction...", flush=True)
        baseline = _verify_prediction(
            self.base_model, self.tokenizer,
            verification_prompt, expected_token)

        if baseline["in_top_10"]:
            return FinetuneResult(
                success=True,
                steps_completed=0,
                final_loss=0.0,
                training_losses=[],
                verification_before=baseline,
                verification_after=baseline,
                elapsed_seconds=time.time() - t_start,
                message=(
                    f"Model already predicts '{expected_token}' at rank "
                    f"{baseline['expected_rank']} (p={baseline['expected_prob']:.3f}). "
                    f"No fine-tuning needed."),
            )

        # Step 2: Generate training data
        print("  [2/5] Generating training examples...", flush=True)
        examples = generate_training_data(
            facts, num_paraphrases=config.num_paraphrases)
        print(f"         {len(examples)} training examples generated", flush=True)

        # Step 3: Attach LoRA adapters
        print("  [3/5] Attaching LoRA adapters...", flush=True)
        lora_model = self._attach_lora(config)
        trainable = sum(p.numel() for p in lora_model.parameters() if p.requires_grad)
        total = sum(p.numel() for p in lora_model.parameters())
        print(f"         Trainable: {trainable:,} / {total:,} "
              f"({100 * trainable / total:.2f}%)", flush=True)

        # Step 4: Train
        print(f"  [4/5] Training for {config.num_steps} steps...", flush=True)
        losses = self._train(lora_model, examples, config)

        # Step 5: Verify after training
        print("  [5/5] Verifying after fine-tuning...", flush=True)
        lora_model.eval()
        after = _verify_prediction(
            lora_model, self.tokenizer,
            verification_prompt, expected_token)

        # Determine success
        improved = (
            after["expected_prob"] > baseline["expected_prob"] * 2
            or after["in_top_10"]
        )

        # Save adapter if requested
        adapter_path = None
        if config.save_path and improved:
            adapter_path = config.save_path
            print(f"         Saving adapter to {adapter_path}...", flush=True)
            lora_model.save_pretrained(adapter_path)

        # Merge LoRA weights back into base model so subsequent commands
        # (generate, diagnose, etc.) use the improved model.
        saved_model_path = None
        if improved:
            print("         Merging LoRA weights into base model...", flush=True)
            self._merge_lora(lora_model)

            # Auto-save the full merged model to disk for persistence
            if config.auto_save:
                saved_model_path = self._save_merged_model()

        elapsed = time.time() - t_start
        result = FinetuneResult(
            success=improved,
            steps_completed=len(losses),
            final_loss=losses[-1] if losses else 0.0,
            training_losses=losses,
            verification_before=baseline,
            verification_after=after,
            elapsed_seconds=elapsed,
            adapter_path=adapter_path,
            saved_model_path=str(saved_model_path) if saved_model_path else None,
            message=self._build_message(baseline, after, losses, elapsed,
                                        improved, saved_model_path),
        )
        return result

    def _attach_lora(self, config: FinetuneConfig, adapter=None):
        """Attach LoRA adapters to the model."""
        from peft import LoraConfig, get_peft_model, TaskType

        target_modules = config.target_modules
        if target_modules is None:
            if adapter is not None:
                target_modules = adapter.get_lora_target_modules()
            else:
                target_modules = ["c_attn", "c_proj", "c_fc"]

        lora_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=config.lora_r,
            lora_alpha=config.lora_alpha,
            lora_dropout=config.lora_dropout,
            target_modules=target_modules,
            bias="none",
        )

        lora_model = get_peft_model(self.base_model, lora_config)
        return lora_model

    def _train(
        self,
        lora_model,
        examples: List[TrainingExample],
        config: FinetuneConfig,
    ) -> List[float]:
        """Run the training loop."""
        lora_model.train()
        device = next(lora_model.parameters()).device

        optimizer = torch.optim.AdamW(
            [p for p in lora_model.parameters() if p.requires_grad],
            lr=config.learning_rate,
            weight_decay=config.weight_decay,
        )

        # Linear warmup scheduler
        def lr_lambda(step):
            if step < config.warmup_steps:
                return step / max(1, config.warmup_steps)
            return 1.0

        scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)

        # Tokenise all examples
        texts = [ex.text for ex in examples]
        encodings = self.tokenizer(
            texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=config.max_seq_len,
        )
        input_ids = encodings["input_ids"].to(device)
        attention_mask = encodings["attention_mask"].to(device)

        n_examples = input_ids.shape[0]
        losses: List[float] = []

        for step in range(config.num_steps):
            # Cycle through examples
            idx = step % n_examples
            batch_ids = input_ids[idx : idx + 1]
            batch_mask = attention_mask[idx : idx + 1]

            # Causal LM: labels = input_ids, shifted internally by HF
            outputs = lora_model(
                input_ids=batch_ids,
                attention_mask=batch_mask,
                labels=batch_ids,
            )
            loss = outputs.loss

            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                [p for p in lora_model.parameters() if p.requires_grad],
                max_norm=1.0,
            )
            optimizer.step()
            scheduler.step()

            loss_val = loss.item()
            losses.append(loss_val)

            if (step + 1) % 20 == 0 or step == 0:
                print(f"         step {step + 1}/{config.num_steps} "
                      f"loss={loss_val:.4f}", flush=True)

        return losses

    def _merge_lora(self, lora_model):
        """Merge LoRA weights back into the base model."""
        merged = lora_model.merge_and_unload()
        # Copy merged weights into self.base_model
        self.base_model.load_state_dict(merged.state_dict(), strict=False)
        self.base_model.eval()

    def _save_merged_model(self) -> Optional[Path]:
        """Save the full merged model + tokenizer to disk.

        Saves to ``~/.cache/huggingface/hub/NeuralDebug-finetuned/<model>/``
        so the fine-tuned weights persist across server restarts.
        """
        save_dir = get_finetuned_model_dir(self.model_name)
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            print(f"         Saving merged model to {save_dir} ...",
                  flush=True)
            self.base_model.save_pretrained(save_dir)
            self.tokenizer.save_pretrained(save_dir)
            print(f"         Model saved ({_dir_size_mb(save_dir):.0f} MB).",
                  flush=True)
            return save_dir
        except Exception as e:
            print(f"         WARNING: Could not save model: {e}", flush=True)
            return None

    def _build_message(
        self,
        baseline: Dict,
        after: Dict,
        losses: List[float],
        elapsed: float,
        improved: bool,
        saved_model_path: Optional[Path] = None,
    ) -> str:
        lines = []
        if improved:
            lines.append("Fine-tuning SUCCEEDED — knowledge injected.")
        else:
            lines.append("Fine-tuning completed but verification shows "
                         "limited improvement. Consider more training data "
                         "or higher LoRA rank.")

        lines.append(f"\nBefore: '{baseline['expected_token']}' ranked "
                      f"#{baseline['expected_rank'] or '?'} "
                      f"(p={baseline['expected_prob']:.4f}), "
                      f"top prediction: '{baseline['top_prediction']}'")
        lines.append(f"After:  '{after['expected_token']}' ranked "
                      f"#{after['expected_rank'] or '?'} "
                      f"(p={after['expected_prob']:.4f}), "
                      f"top prediction: '{after['top_prediction']}'")

        if losses:
            lines.append(f"\nTraining: {len(losses)} steps, "
                          f"loss {losses[0]:.4f} -> {losses[-1]:.4f}, "
                          f"{elapsed:.1f}s")

        if saved_model_path:
            lines.append(f"\nModel saved to: {saved_model_path}")
            lines.append("Weights will be auto-loaded on next server start.")

        return "\n".join(lines)
