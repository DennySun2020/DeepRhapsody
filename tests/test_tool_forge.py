"""Tests for the Tool Forge (exec_analysis sandbox)."""

import pytest
import torch

# -- import from src/NeuralDebug/llm/ ------------------------------------
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "NeuralDebug", "llm"))

from tool_forge import (
    validate_code, ValidationError, ToolForge,
    ALLOWED_MODULES, ALLOWED_BUILTINS, BLOCKED_NAMES,
    _sanitize_result,
)


# -----------------------------------------------------------------------
# AST Validation
# -----------------------------------------------------------------------

class TestValidateCode:
    """Test the AST whitelist validator."""

    def test_valid_simple_function(self):
        """Simple analyze function should pass."""
        code = '''
def analyze(model, tokenizer, input_ids):
    return {"params": sum(p.numel() for p in model.parameters())}
'''
        tree = validate_code(code)
        assert tree is not None

    def test_valid_torch_import(self):
        """Importing torch and submodules should pass."""
        code = '''
import torch
import torch.nn.functional as F

def analyze(model, tokenizer, input_ids):
    with torch.no_grad():
        out = model(input_ids)
    return {"logits_shape": list(out.logits.shape)}
'''
        tree = validate_code(code)
        assert tree is not None

    def test_valid_numpy_import(self):
        """Importing numpy should pass."""
        code = '''
import numpy as np

def analyze(model, tokenizer, input_ids):
    return {"pi": float(np.pi)}
'''
        tree = validate_code(code)
        assert tree is not None

    def test_valid_math_import(self):
        """Importing math should pass."""
        code = '''
import math

def analyze(model, tokenizer, input_ids):
    return {"log2": math.log2(1024)}
'''
        tree = validate_code(code)
        assert tree is not None

    def test_blocked_os_import(self):
        """Importing os should be rejected."""
        code = '''
import os
def analyze(model, tokenizer, input_ids):
    return {"cwd": os.getcwd()}
'''
        with pytest.raises(ValidationError, match="not allowed"):
            validate_code(code)

    def test_blocked_subprocess_import(self):
        """Importing subprocess should be rejected."""
        code = '''
import subprocess
def analyze(model, tokenizer, input_ids):
    return {}
'''
        with pytest.raises(ValidationError, match="not allowed"):
            validate_code(code)

    def test_blocked_from_os_import(self):
        """from os import ... should be rejected."""
        code = '''
from os.path import join
def analyze(model, tokenizer, input_ids):
    return {}
'''
        with pytest.raises(ValidationError, match="not allowed"):
            validate_code(code)

    def test_blocked_requests_import(self):
        """Importing requests should be rejected."""
        code = '''
import requests
def analyze(model, tokenizer, input_ids):
    return {}
'''
        with pytest.raises(ValidationError, match="not allowed"):
            validate_code(code)

    def test_blocked_open_call(self):
        """Using open() should be rejected."""
        code = '''
def analyze(model, tokenizer, input_ids):
    with open("/etc/passwd") as f:
        return {"data": f.read()}
'''
        with pytest.raises(ValidationError, match="blocked"):
            validate_code(code)

    def test_blocked_eval_call(self):
        """Using eval() should be rejected."""
        code = '''
def analyze(model, tokenizer, input_ids):
    return eval("{'a': 1}")
'''
        with pytest.raises(ValidationError, match="blocked"):
            validate_code(code)

    def test_blocked_exec_call(self):
        """Using exec() should be rejected."""
        code = '''
def analyze(model, tokenizer, input_ids):
    exec("x = 1")
    return {}
'''
        with pytest.raises(ValidationError, match="blocked"):
            validate_code(code)

    def test_blocked_dunder_import(self):
        """Using __import__() should be rejected."""
        code = '''
def analyze(model, tokenizer, input_ids):
    os = __import__("os")
    return {}
'''
        with pytest.raises(ValidationError, match="blocked"):
            validate_code(code)

    def test_blocked_weight_mutation(self):
        """In-place weight mutation should be rejected."""
        code = '''
def analyze(model, tokenizer, input_ids):
    for p in model.parameters():
        p.requires_grad_(True)
    return {}
'''
        with pytest.raises(ValidationError, match="read-only"):
            validate_code(code)

    def test_blocked_zero_mutation(self):
        """zero_() weight mutation should be rejected."""
        code = '''
def analyze(model, tokenizer, input_ids):
    for p in model.parameters():
        p.zero_()
    return {}
'''
        with pytest.raises(ValidationError, match="read-only"):
            validate_code(code)

    def test_blocked_data_access(self):
        """Direct .data access should be rejected."""
        code = '''
def analyze(model, tokenizer, input_ids):
    w = list(model.parameters())[0].data
    return {}
'''
        with pytest.raises(ValidationError, match="read-only"):
            validate_code(code)

    def test_syntax_error(self):
        """Invalid Python syntax should be rejected."""
        code = '''
def analyze(model tokenizer input_ids):
    return {}
'''
        with pytest.raises(ValidationError, match="Syntax error"):
            validate_code(code)

    def test_multiple_errors_reported(self):
        """Multiple violations should all be reported."""
        code = '''
import os
import subprocess
def analyze(model, tokenizer, input_ids):
    return {}
'''
        with pytest.raises(ValidationError) as exc_info:
            validate_code(code)
        # Should mention both blocked imports
        assert "os" in str(exc_info.value)
        assert "subprocess" in str(exc_info.value)

    def test_allowed_builtins_in_code(self):
        """Code using allowed builtins should pass."""
        code = '''
def analyze(model, tokenizer, input_ids):
    params = list(model.parameters())
    total = sum(p.numel() for p in params)
    return {"total": total, "count": len(params), "max_size": max(p.numel() for p in params)}
'''
        tree = validate_code(code)
        assert tree is not None

    def test_functools_allowed(self):
        """functools should be importable."""
        code = '''
import functools
def analyze(model, tokenizer, input_ids):
    return {}
'''
        tree = validate_code(code)
        assert tree is not None

    def test_collections_allowed(self):
        """collections should be importable."""
        code = '''
from collections import defaultdict
def analyze(model, tokenizer, input_ids):
    d = defaultdict(int)
    return dict(d)
'''
        tree = validate_code(code)
        assert tree is not None

    def test_blocked_socket(self):
        """socket access should be blocked."""
        code = '''
import socket
def analyze(model, tokenizer, input_ids):
    return {}
'''
        with pytest.raises(ValidationError, match="not allowed"):
            validate_code(code)

    def test_blocked_pickle(self):
        """pickle should be blocked."""
        code = '''
import pickle
def analyze(model, tokenizer, input_ids):
    return {}
'''
        with pytest.raises(ValidationError, match="not allowed"):
            validate_code(code)


# -----------------------------------------------------------------------
# Sanitize Result
# -----------------------------------------------------------------------

class TestSanitizeResult:
    """Test result serialization."""

    def test_primitive_passthrough(self):
        assert _sanitize_result(42) == 42
        assert _sanitize_result(3.14) == 3.14
        assert _sanitize_result("hello") == "hello"
        assert _sanitize_result(True) is True
        assert _sanitize_result(None) is None

    def test_dict_passthrough(self):
        d = {"a": 1, "b": "two"}
        assert _sanitize_result(d) == d

    def test_nested_dict(self):
        d = {"outer": {"inner": [1, 2, 3]}}
        assert _sanitize_result(d) == d

    def test_small_tensor(self):
        t = torch.tensor([1.0, 2.0, 3.0])
        result = _sanitize_result(t)
        assert result == [1.0, 2.0, 3.0]

    def test_large_tensor_summary(self):
        t = torch.randn(200)
        result = _sanitize_result(t)
        assert isinstance(result, dict)
        assert result["type"] == "tensor"
        assert result["shape"] == [200]
        assert "min" in result
        assert "max" in result
        assert "mean" in result

    def test_list_of_tensors(self):
        ts = [torch.tensor([1.0]), torch.tensor([2.0])]
        result = _sanitize_result(ts)
        assert result == [[1.0], [2.0]]

    def test_depth_limit(self):
        """Deeply nested structures should be stringified."""
        d = {"a": 1}
        for _ in range(15):
            d = {"nested": d}
        result = _sanitize_result(d)
        # At some depth it should fall back to str()
        assert isinstance(result, dict)

    def test_unknown_type_stringified(self):
        """Unknown types should be converted to string."""

        class Custom:
            def __repr__(self):
                return "Custom()"

        result = _sanitize_result(Custom())
        assert result == "Custom()"


# -----------------------------------------------------------------------
# ToolForge.run()
# -----------------------------------------------------------------------

class TestToolForgeRun:
    """Test the full sandbox execution pipeline."""

    @pytest.fixture
    def forge(self):
        return ToolForge(default_timeout=10)

    @pytest.fixture
    def dummy_model(self):
        """A minimal nn.Module for testing."""
        model = torch.nn.Linear(4, 2)
        model.eval()
        return model

    @pytest.fixture
    def dummy_tokenizer(self):
        """A mock tokenizer with just enough interface."""

        class FakeTokenizer:
            def encode(self, text):
                return [1, 2, 3]

            def decode(self, ids):
                return "fake"

        return FakeTokenizer()

    def test_simple_analysis(self, forge, dummy_model, dummy_tokenizer):
        """Basic analyze function should execute and return results."""
        code = '''
def analyze(model, tokenizer, input_ids):
    n = sum(p.numel() for p in model.parameters())
    return {"num_params": n}
'''
        result = forge.run(code, dummy_model, dummy_tokenizer)
        assert result["status"] == "ok"
        assert "num_params" in result["result"]
        assert result["result"]["num_params"] == 4 * 2 + 2  # weight + bias

    def test_missing_analyze_function(self, forge, dummy_model, dummy_tokenizer):
        """Code without analyze() should produce an error."""
        code = '''
def my_function(model, tokenizer, input_ids):
    return {}
'''
        result = forge.run(code, dummy_model, dummy_tokenizer)
        assert result["status"] == "error"
        assert "analyze" in result["error"]

    def test_runtime_error(self, forge, dummy_model, dummy_tokenizer):
        """Runtime exceptions should be caught and reported."""
        code = '''
def analyze(model, tokenizer, input_ids):
    return 1 / 0
'''
        result = forge.run(code, dummy_model, dummy_tokenizer)
        assert result["status"] == "error"
        assert "ZeroDivisionError" in result["error"]

    def test_validation_error(self, forge, dummy_model, dummy_tokenizer):
        """Code with blocked imports should fail at validation."""
        code = '''
import os
def analyze(model, tokenizer, input_ids):
    return {}
'''
        result = forge.run(code, dummy_model, dummy_tokenizer)
        assert result["status"] == "error"
        assert "not allowed" in result["error"]

    def test_syntax_error(self, forge, dummy_model, dummy_tokenizer):
        """Syntax errors should fail at validation."""
        code = '''
def analyze(model tokenizer input_ids:
    return {}
'''
        result = forge.run(code, dummy_model, dummy_tokenizer)
        assert result["status"] == "error"
        assert "Syntax error" in result["error"]

    def test_with_input_ids(self, forge, dummy_model, dummy_tokenizer):
        """input_ids should be passed through to analyze()."""
        code = '''
def analyze(model, tokenizer, input_ids):
    return {"shape": list(input_ids.shape), "values": input_ids.tolist()}
'''
        ids = torch.tensor([[10, 20, 30]])
        result = forge.run(code, dummy_model, dummy_tokenizer, input_ids=ids)
        assert result["status"] == "ok"
        assert result["result"]["shape"] == [1, 3]
        assert result["result"]["values"] == [[10, 20, 30]]

    def test_none_input_ids(self, forge, dummy_model, dummy_tokenizer):
        """analyze() should handle input_ids=None gracefully."""
        code = '''
def analyze(model, tokenizer, input_ids):
    return {"has_ids": input_ids is not None}
'''
        result = forge.run(code, dummy_model, dummy_tokenizer, input_ids=None)
        assert result["status"] == "ok"
        assert result["result"]["has_ids"] is False

    def test_torch_operations(self, forge, dummy_model, dummy_tokenizer):
        """Code using torch operations should work."""
        code = '''
import torch

def analyze(model, tokenizer, input_ids):
    x = torch.randn(1, 4)
    with torch.no_grad():
        out = model(x)
    return {"output_shape": list(out.shape)}
'''
        result = forge.run(code, dummy_model, dummy_tokenizer)
        assert result["status"] == "ok"
        assert result["result"]["output_shape"] == [1, 2]

    def test_tensor_result_sanitized(self, forge, dummy_model, dummy_tokenizer):
        """Tensor results should be auto-converted to lists."""
        code = '''
import torch

def analyze(model, tokenizer, input_ids):
    return {"weights": torch.tensor([1.0, 2.0, 3.0])}
'''
        result = forge.run(code, dummy_model, dummy_tokenizer)
        assert result["status"] == "ok"
        assert result["result"]["weights"] == [1.0, 2.0, 3.0]

    def test_timeout(self, forge, dummy_model, dummy_tokenizer):
        """Code that runs too long should be terminated."""
        code = '''
import time

def analyze(model, tokenizer, input_ids):
    while True:
        pass
    return {}
'''
        # time is not in ALLOWED_MODULES, so this will fail at validation
        result = forge.run(code, dummy_model, dummy_tokenizer, timeout=2)
        assert result["status"] == "error"

    def test_timeout_via_loop(self, forge, dummy_model, dummy_tokenizer):
        """Pure computation timeout (no imports needed)."""
        code = '''
def analyze(model, tokenizer, input_ids):
    x = 0
    while True:
        x += 1
    return {"x": x}
'''
        result = forge.run(code, dummy_model, dummy_tokenizer, timeout=2)
        assert result["status"] == "error"
        assert "timed out" in result["error"]

    def test_non_dict_result(self, forge, dummy_model, dummy_tokenizer):
        """analyze() returning a non-dict should still work."""
        code = '''
def analyze(model, tokenizer, input_ids):
    return 42
'''
        result = forge.run(code, dummy_model, dummy_tokenizer)
        assert result["status"] == "ok"
        assert result["result"] == 42

    def test_string_result(self, forge, dummy_model, dummy_tokenizer):
        """analyze() returning a string should work."""
        code = '''
def analyze(model, tokenizer, input_ids):
    return "hello world"
'''
        result = forge.run(code, dummy_model, dummy_tokenizer)
        assert result["status"] == "ok"
        assert result["result"] == "hello world"

    def test_hook_pattern(self, forge, dummy_model, dummy_tokenizer):
        """The canonical hook pattern should work."""
        code = '''
import torch

def analyze(model, tokenizer, input_ids):
    activations = []
    def hook_fn(module, input, output):
        activations.append(output.detach().clone())
    handle = model.register_forward_hook(hook_fn)
    x = torch.randn(1, 4)
    with torch.no_grad():
        model(x)
    handle.remove()
    return {"num_activations": len(activations), "shape": list(activations[0].shape)}
'''
        result = forge.run(code, dummy_model, dummy_tokenizer)
        assert result["status"] == "ok"
        assert result["result"]["num_activations"] == 1
        assert result["result"]["shape"] == [1, 2]


# -----------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------

class TestConstants:
    """Verify the safety constants are sensible."""

    def test_blocked_names_no_overlap_with_builtins(self):
        """BLOCKED_NAMES should not appear in ALLOWED_BUILTINS."""
        overlap = BLOCKED_NAMES & ALLOWED_BUILTINS
        assert overlap == set(), f"Overlap: {overlap}"

    def test_common_dangerous_modules_blocked(self):
        """Key dangerous modules should not be in ALLOWED_MODULES."""
        dangerous = {"os", "sys", "subprocess", "shutil", "socket", "http"}
        for mod in dangerous:
            assert mod not in ALLOWED_MODULES

    def test_torch_is_allowed(self):
        assert "torch" in ALLOWED_MODULES
        assert "torch.nn" in ALLOWED_MODULES
        assert "torch.nn.functional" in ALLOWED_MODULES

    def test_len_in_builtins(self):
        """Common builtins should be present."""
        for name in ("len", "range", "list", "dict", "int", "float", "str",
                      "print", "enumerate", "zip", "map", "filter", "sum",
                      "min", "max", "sorted", "isinstance", "type"):
            assert name in ALLOWED_BUILTINS, f"{name} missing from ALLOWED_BUILTINS"
