"""Try GPT-2 Medium text generation with HuggingFace Transformers.

Usage:
    pip install torch transformers
    python examples/try_gpt2_medium.py
    python examples/try_gpt2_medium.py --model C:\\path\\to\\finetuned\\gpt2-medium

"""

import argparse
import os
import sys
import traceback
import types

# Work around torch._dynamo import hang on PyTorch ≤2.5 / Windows CPU.
os.environ["TRANSFORMERS_NO_TF"] = "1"

if "torch._dynamo" not in sys.modules:
    _stub = types.ModuleType("torch._dynamo")
    _stub.is_compiling = lambda: False
    _stub.graph_break = lambda: None
    _stub.disable = lambda fn=None, *a, **kw: fn if fn else (lambda f: f)
    _stub.config = types.ModuleType("torch._dynamo.config")
    _stub.config.suppress_errors = False
    sys.modules["torch._dynamo"] = _stub
    sys.modules["torch._dynamo.config"] = _stub.config

import transformers.utils.import_utils as _iu  # noqa: E402
_iu.is_torchdynamo_compiling = lambda: False
import transformers.modeling_utils as _mu  # noqa: E402
_mu.is_torchdynamo_compiling = lambda: False

import torch  # noqa: E402
from transformers import AutoTokenizer, AutoModelForCausalLM  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="GPT-2 Medium text generation")
    parser.add_argument(
        "--model", "-m", default="gpt2-medium",
        help="Model name or local path (e.g. path to fine-tuned weights)")
    args = parser.parse_args()

    try:
        model_id = args.model
        print(f"Loading model '{model_id}' ...", flush=True)
        tokenizer = AutoTokenizer.from_pretrained(model_id)
        model = AutoModelForCausalLM.from_pretrained(
            model_id, attn_implementation="eager")
        model.eval()
        params = sum(p.numel() for p in model.parameters())
        print(f"Model loaded: {params:,} parameters, "
              f"{len(model.transformer.h)} blocks", flush=True)

        prompts = [
            "The capital of France is",
            "Who is Dr. Elena Vasquez? Dr. Elena Vasquez is",
            "The meaning of life is",
            "Who is Dr. James Whitfield? Dr. James Whitfield is",
        ]

        for prompt in prompts:
            print(f"\nPrompt: {prompt}", flush=True)
            print("Generating...", flush=True)
            input_ids = tokenizer.encode(prompt, return_tensors="pt")
            with torch.no_grad():
                gen = model.generate(
                    input_ids,
                    max_new_tokens=30,
                    do_sample=False,
                    pad_token_id=tokenizer.eos_token_id,
                )
            text = tokenizer.decode(gen[0], skip_special_tokens=True)
            print(f"Output: {text}", flush=True)

    except Exception as e:
        print(f"\nERROR: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
