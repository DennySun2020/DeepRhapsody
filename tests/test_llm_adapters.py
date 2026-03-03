"""Tests for the LLM debugger adapter abstraction layer."""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Add the llm directory to path
_llm_dir = Path(__file__).resolve().parent.parent / "src" / "neuraldebug" / "llm"
sys.path.insert(0, str(_llm_dir))

from adapters.base import ModelAdapter, ModelInfo
from adapters.registry import AdapterRegistry


class TestModelInfo(unittest.TestCase):
    """Test the ModelInfo dataclass."""

    def test_basic_fields(self):
        info = ModelInfo(
            name="test-model",
            architecture="gpt2",
            num_layers=12,
            hidden_dim=768,
            num_heads=12,
            head_dim=64,
            vocab_size=50257,
            ffn_dim=3072,
        )
        self.assertEqual(info.num_layers, 12)
        self.assertEqual(info.hidden_dim, 768)
        self.assertTrue(info.is_causal)  # default
        self.assertIsNone(info.num_kv_heads)  # default

    def test_gqa_model(self):
        info = ModelInfo(
            name="llama-70b",
            architecture="llama",
            num_layers=80,
            hidden_dim=8192,
            num_heads=64,
            head_dim=128,
            vocab_size=32000,
            ffn_dim=28672,
            num_kv_heads=8,
        )
        self.assertEqual(info.num_kv_heads, 8)


class TestAdapterRegistry(unittest.TestCase):
    """Test the adapter registry auto-detection and manual selection."""

    def setUp(self):
        AdapterRegistry.reset()

    def test_list_adapters(self):
        names = AdapterRegistry.list_adapters()
        self.assertIn("gpt2", names)
        self.assertIn("llama", names)

    def test_from_name_unknown(self):
        with self.assertRaises(KeyError):
            AdapterRegistry.from_name("nonexistent", MagicMock())

    def test_register_custom(self):
        class DummyAdapter(ModelAdapter):
            def __init__(self, model):
                self.model = model
            def info(self): return ModelInfo("dummy", "dummy", 1, 64, 1, 64, 100, 64)
            def get_block(self, i): return None
            def get_attention_output_proj(self, i): return None
            def get_ffn_intermediate(self, i): return None
            def get_embedding(self): return None
            def get_final_norm(self): return None
            def get_lm_head(self): return None
            def embed(self, x): return x
            def forward_block(self, h, i): return h
            def apply_final_norm(self, h): return h
            def get_logits(self, h): return h
            def get_layer_graph(self): return {}
            def get_lora_target_modules(self): return ["q", "v"]

        AdapterRegistry.register("dummy", DummyAdapter)
        self.assertIn("dummy", AdapterRegistry.list_adapters())

        model = MagicMock()
        adapter = AdapterRegistry.from_name("dummy", model)
        self.assertIsInstance(adapter, DummyAdapter)
        self.assertEqual(adapter.info().architecture, "dummy")
        self.assertEqual(adapter.get_lora_target_modules(), ["q", "v"])

    def test_auto_detect_gpt2(self):
        model = MagicMock()
        model.transformer = MagicMock()
        model.transformer.h = [MagicMock() for _ in range(6)]
        # Make it look like GPT-2
        model.config = MagicMock()
        model.config._name_or_path = "gpt2"
        model.config.n_embd = 768
        model.config.n_head = 12
        model.config.vocab_size = 50257
        model.transformer.h[0].mlp.c_fc.weight = MagicMock()
        model.transformer.h[0].mlp.c_fc.weight.shape = [3072, 768]

        adapter = AdapterRegistry.auto_detect(model)
        self.assertEqual(adapter.info().architecture, "gpt2")
        self.assertEqual(adapter.info().num_layers, 6)

    def test_auto_detect_llama(self):
        model = MagicMock()
        # Llama: model.model.layers (no model.transformer)
        del model.transformer
        model.model = MagicMock()
        model.model.layers = [MagicMock() for _ in range(32)]
        model.config = MagicMock()
        model.config._name_or_path = "meta-llama/Llama-2-7b"
        model.config.num_hidden_layers = 32
        model.config.hidden_size = 4096
        model.config.num_attention_heads = 32
        model.config.vocab_size = 32000
        model.config.intermediate_size = 11008

        adapter = AdapterRegistry.auto_detect(model)
        self.assertEqual(adapter.info().architecture, "llama")
        self.assertEqual(adapter.info().num_layers, 32)

    def test_auto_detect_unknown_raises(self):
        model = MagicMock(spec=[])  # No attributes
        with self.assertRaises(ValueError):
            AdapterRegistry.auto_detect(model)

    def test_register_with_detect_fn(self):
        class PhiAdapter(ModelAdapter):
            def __init__(self, model):
                self.model = model
            def info(self): return ModelInfo("phi", "phi", 32, 2560, 32, 80, 51200, 10240)
            def get_block(self, i): return None
            def get_attention_output_proj(self, i): return None
            def get_ffn_intermediate(self, i): return None
            def get_embedding(self): return None
            def get_final_norm(self): return None
            def get_lm_head(self): return None
            def embed(self, x): return x
            def forward_block(self, h, i): return h
            def apply_final_norm(self, h): return h
            def get_logits(self, h): return h
            def get_layer_graph(self): return {}
            def get_lora_target_modules(self): return ["q_proj", "v_proj"]

        AdapterRegistry.register(
            "phi", PhiAdapter,
            detect_fn=lambda m: hasattr(m, "is_phi_model"))

        model = MagicMock()
        model.is_phi_model = True
        # Remove attributes that would match built-in detectors
        del model.transformer
        model.model = MagicMock(spec=[])  # no .layers

        adapter = AdapterRegistry.auto_detect(model)
        self.assertEqual(adapter.info().architecture, "phi")


class TestGPT2Adapter(unittest.TestCase):
    """Test GPT2Adapter methods."""

    def test_get_layer_graph_keys(self):
        from adapters.gpt2 import GPT2Adapter

        model = MagicMock()
        model.transformer.h = [MagicMock() for _ in range(2)]
        model.config._name_or_path = "gpt2"
        model.config.n_embd = 768
        model.config.n_head = 12
        model.config.vocab_size = 50257
        model.transformer.h[0].mlp.c_fc.weight.shape = [3072, 768]

        adapter = GPT2Adapter(model)
        graph = adapter.get_layer_graph()

        self.assertIn("embedding", graph)
        self.assertIn("block_{i}.attn.ln_qkv", graph)
        self.assertIn("block_{i}.ffn.ln_up", graph)
        self.assertIn("final_norm", graph)
        self.assertIn("lm_head", graph)

    def test_lora_targets(self):
        from adapters.gpt2 import GPT2Adapter

        model = MagicMock()
        model.transformer.h = [MagicMock()]
        model.config._name_or_path = "gpt2"
        model.config.n_embd = 768
        model.config.n_head = 12
        model.config.vocab_size = 50257
        model.transformer.h[0].mlp.c_fc.weight.shape = [3072, 768]

        adapter = GPT2Adapter(model)
        targets = adapter.get_lora_target_modules()
        self.assertIn("c_attn", targets)
        self.assertIn("c_proj", targets)


class TestLlamaAdapter(unittest.TestCase):
    """Test LlamaAdapter methods."""

    def test_lora_targets(self):
        from adapters.llama import LlamaAdapter

        model = MagicMock()
        model.model.layers = [MagicMock() for _ in range(4)]
        model.config._name_or_path = "llama-7b"
        model.config.num_hidden_layers = 4
        model.config.hidden_size = 4096
        model.config.num_attention_heads = 32
        model.config.vocab_size = 32000
        model.config.intermediate_size = 11008

        adapter = LlamaAdapter(model)
        targets = adapter.get_lora_target_modules()
        self.assertIn("q_proj", targets)
        self.assertIn("gate_proj", targets)


class TestHookBackend(unittest.TestCase):
    """Test the PyTorch hook backend."""

    def test_register_and_clear(self):
        from hooks.pytorch import PyTorchHookBackend
        import torch.nn as nn

        backend = PyTorchHookBackend()
        linear = nn.Linear(10, 10)

        handle = backend.register_forward_hook(
            linear, lambda m, i, o: None)
        self.assertEqual(backend.active_hook_count, 1)

        count = backend.clear_all()
        self.assertEqual(count, 1)
        self.assertEqual(backend.active_hook_count, 0)

    def test_compute_tensor_stats(self):
        from hooks.pytorch import PyTorchHookBackend
        import torch

        backend = PyTorchHookBackend()
        t = torch.randn(2, 3)
        stats = backend.compute_tensor_stats(t)

        self.assertEqual(stats["shape"], [2, 3])
        self.assertIn("mean", stats)
        self.assertIn("std", stats)
        self.assertIn("min", stats)
        self.assertIn("max", stats)


class TestCommandRegistry(unittest.TestCase):
    """Test the command registry."""

    def test_register_and_dispatch(self):
        from commands.base import Command, CommandRegistry

        class TestCmd(Command):
            name = "test_cmd"
            aliases = ["tc"]
            description = "A test command"

            def execute(self, debugger, args):
                return {"status": "ok", "args": args}

        registry = CommandRegistry()
        registry.register(TestCmd())

        # Dispatch by name
        result = registry.dispatch("test_cmd", None, "hello")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["args"], "hello")

        # Dispatch by alias
        result = registry.dispatch("tc", None, "world")
        self.assertEqual(result["status"], "ok")

        # Unknown command
        result = registry.dispatch("nonexistent", None, "")
        self.assertIsNone(result)

    def test_list_commands(self):
        from commands.base import CommandRegistry
        from commands.core import register_core_commands

        registry = CommandRegistry()
        register_core_commands(registry)

        cmds = registry.list_commands()
        names = [c["name"] for c in cmds]
        self.assertIn("start", names)
        self.assertIn("step_over", names)
        self.assertIn("inspect", names)
        self.assertIn("generate", names)

    def test_decorator(self):
        from commands.base import command, CommandRegistry

        @command("my_cmd", aliases=["mc"], description="My command")
        def my_func(debugger, args):
            return {"status": "ok", "data": args}

        registry = CommandRegistry()
        registry.register(my_func)

        result = registry.dispatch("mc", None, "test")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"], "test")


class TestAPIProbe(unittest.TestCase):
    """Test API probe techniques."""

    def _mock_call(self, prompt, **kwargs):
        """Simple mock LLM API call."""
        return {
            "text": "The capital of France is Paris.",
            "logprobs": [
                {"token": "The", "logprob": -0.5,
                 "top_logprobs": [
                     {"token": "The", "logprob": -0.5},
                     {"token": "A", "logprob": -2.0},
                 ]},
                {"token": " capital", "logprob": -0.3,
                 "top_logprobs": [
                     {"token": " capital", "logprob": -0.3},
                     {"token": " city", "logprob": -1.5},
                 ]},
            ],
        }

    def test_logprob_analysis(self):
        from api_probe import APIProbe
        probe = APIProbe(self._mock_call, model_name="test")
        result = probe.analyze_logprobs("What is the capital of France?")

        self.assertEqual(result.technique, "logprob_analysis")
        self.assertIn("tokens", result.details)
        self.assertIn("avg_entropy", result.details)

    def test_consistency(self):
        from api_probe import APIProbe
        call_count = [0]

        def varying_call(prompt, **kwargs):
            call_count[0] += 1
            return {"text": "Paris" if call_count[0] % 2 == 0 else "Paris."}

        probe = APIProbe(varying_call, model_name="test")
        result = probe.test_consistency("Capital of France?", n=4)

        self.assertEqual(result.technique, "consistency_testing")
        self.assertIn("agreement_ratio", result.details)
        self.assertEqual(len(result.details["answers"]), 4)

    def test_cot_extraction(self):
        from api_probe import APIProbe

        def cot_call(prompt, **kwargs):
            if "step by step" in prompt:
                return {"text": "Let me think... France → Paris. The answer is Paris."}
            return {"text": "Paris"}

        probe = APIProbe(cot_call, model_name="test")
        result = probe.extract_cot("Capital of France?")

        self.assertEqual(result.technique, "chain_of_thought")
        self.assertIn("direct_answer", result.details)
        self.assertIn("cot_reasoning", result.details)

    def test_perturbation(self):
        from api_probe import APIProbe

        def perturb_call(prompt, **kwargs):
            if "Germany" in prompt:
                return {"text": "Berlin"}
            return {"text": "Paris"}

        probe = APIProbe(perturb_call, model_name="test")
        result = probe.perturb(
            "Capital of France?",
            [{"name": "swap_country", "prompt": "Capital of Germany?"}],
        )

        self.assertEqual(result.technique, "prompt_perturbation")
        self.assertIn("swap_country", result.details["changed"])


if __name__ == "__main__":
    unittest.main()
