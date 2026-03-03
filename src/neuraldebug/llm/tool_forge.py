"""Tool Forge — sandbox for dynamic LLM interpretability analysis.

Allows the AI agent to generate and execute custom PyTorch hook-based
analysis code against the live model at runtime, without needing every
tool pre-implemented.

Security model:
  - AST whitelist: only ``torch``, ``numpy``, ``math``, ``collections``,
    ``json``, and model-access builtins are allowed.
  - No filesystem, network, subprocess, or ``eval``/``exec`` calls.
  - Execution timeout (default 60 s).
  - Read-only by default (hooks observe but don't mutate weights).

Usage from ``LLMDebugger``::

    from tool_forge import ToolForge

    forge = ToolForge()
    result = forge.run(code_str, model, tokenizer, input_ids)
"""

from __future__ import annotations

import ast
import signal
import sys
import threading
import traceback
from typing import Any, Dict, Optional

import torch

# ---------------------------------------------------------------------------
# AST Validator
# ---------------------------------------------------------------------------

# Modules the analysis code is allowed to import
ALLOWED_MODULES = frozenset({
    "torch", "torch.nn", "torch.nn.functional",
    "numpy", "np",
    "math",
    "collections", "collections.abc",
    "json",
    "functools",
    "dataclasses",
})

# Built-in functions the code may call
ALLOWED_BUILTINS = frozenset({
    "abs", "all", "any", "bool", "chr", "dict", "dir", "divmod",
    "enumerate", "filter", "float", "format", "frozenset", "getattr",
    "hasattr", "hash", "id", "int", "isinstance", "issubclass", "iter",
    "len", "list", "map", "max", "min", "next", "ord", "pow", "print",
    "range", "repr", "reversed", "round", "set", "slice", "sorted",
    "str", "sum", "tuple", "type", "vars", "zip",
})

# Names that must never appear in the code
BLOCKED_NAMES = frozenset({
    "open", "exec", "eval", "compile", "__import__", "importlib",
    "subprocess", "os", "sys", "shutil", "pathlib", "glob",
    "socket", "http", "urllib", "requests",
    "pickle", "shelve", "ctypes",
    "builtins", "__builtins__",
})


class ValidationError(Exception):
    """Raised when user-supplied code fails the safety check."""


class _ASTValidator(ast.NodeVisitor):
    """Walk the AST and reject anything outside the whitelist."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    # -- imports -----------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if alias.name not in ALLOWED_MODULES and root not in ("torch", "numpy", "math", "collections", "functools"):
                self.errors.append(
                    f"line {node.lineno}: import '{alias.name}' is not allowed. "
                    f"Allowed: {', '.join(sorted(ALLOWED_MODULES))}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        root = mod.split(".")[0]
        if mod not in ALLOWED_MODULES and root not in ("torch", "numpy", "math", "collections", "functools"):
            self.errors.append(
                f"line {node.lineno}: 'from {mod} import ...' is not allowed. "
                f"Allowed: {', '.join(sorted(ALLOWED_MODULES))}")
        self.generic_visit(node)

    # -- blocked names -----------------------------------------------------

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BLOCKED_NAMES:
            self.errors.append(
                f"line {node.lineno}: use of '{node.id}' is blocked for safety.")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in BLOCKED_NAMES:
            self.errors.append(
                f"line {node.lineno}: access to '.{node.attr}' is blocked.")
        # Block model.parameters().requires_grad_(True) — weight mutation
        if node.attr in ("requires_grad_", "copy_", "zero_", "fill_",
                         "uniform_", "normal_", "data"):
            self.errors.append(
                f"line {node.lineno}: in-place weight mutation via '.{node.attr}' "
                f"is blocked. exec_analysis is read-only.")
        self.generic_visit(node)

    # -- no nested exec/eval strings ---------------------------------------

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in ("exec", "eval", "compile", "__import__"):
            self.errors.append(
                f"line {node.lineno}: calling '{node.func.id}()' is blocked.")
        self.generic_visit(node)


def validate_code(code: str) -> ast.Module:
    """Parse and validate user-supplied analysis code.

    Returns the parsed AST on success.
    Raises :class:`ValidationError` with details on failure.
    """
    try:
        tree = ast.parse(code, filename="<exec_analysis>", mode="exec")
    except SyntaxError as exc:
        raise ValidationError(f"Syntax error: {exc}") from exc

    validator = _ASTValidator()
    validator.visit(tree)
    if validator.errors:
        raise ValidationError(
            "Code failed safety validation:\n" +
            "\n".join(f"  • {e}" for e in validator.errors))
    return tree


# ---------------------------------------------------------------------------
# Sandbox Executor
# ---------------------------------------------------------------------------

class _TimeoutError(Exception):
    """Raised when analysis code exceeds the time limit."""


def _timeout_handler(signum, frame):
    raise _TimeoutError("Analysis timed out")


class ToolForge:
    """Execute validated analysis code against a live model.

    Parameters
    ----------
    default_timeout : int
        Maximum execution time in seconds (default 60).
    """

    def __init__(self, default_timeout: int = 60) -> None:
        self.default_timeout = default_timeout

    def run(
        self,
        code: str,
        model: torch.nn.Module,
        tokenizer: Any,
        input_ids: Optional[torch.Tensor] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Validate and execute analysis code in a restricted scope.

        Parameters
        ----------
        code : str
            Python source to execute.  Must define an ``analyze()`` function
            that accepts ``(model, tokenizer, input_ids)`` and returns a dict.
        model : torch.nn.Module
            The loaded model (read-only access).
        tokenizer
            The HuggingFace tokenizer.
        input_ids : torch.Tensor, optional
            Current prompt tokens ``[1, seq_len]``.
        timeout : int, optional
            Override the default timeout.

        Returns
        -------
        dict
            ``{"status": "ok", "result": <return value of analyze()>}``
            or ``{"status": "error", "error": "<message>"}``
        """
        # 1. Validate
        try:
            tree = validate_code(code)
        except ValidationError as exc:
            return {"status": "error", "error": str(exc)}

        # 2. Build restricted scope
        import numpy as np
        import math
        import torch.nn.functional as F

        scope: Dict[str, Any] = {
            # Injected context
            "model": model,
            "tokenizer": tokenizer,
            "input_ids": input_ids,
            # Allowed libraries
            "torch": torch,
            "F": F,
            "np": np,
            "numpy": np,
            "math": math,
            # Safe builtins
            "__builtins__": {name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
                            for name in ALLOWED_BUILTINS
                            if (isinstance(__builtins__, dict) and name in __builtins__) or
                               (not isinstance(__builtins__, dict) and hasattr(__builtins__, name))},
        }
        # Add __import__ that only allows whitelisted modules
        scope["__builtins__"]["__import__"] = _restricted_import

        # 3. Compile
        compiled = compile(tree, "<exec_analysis>", "exec")

        # 4. Execute with timeout
        effective_timeout = timeout or self.default_timeout
        result_container: Dict[str, Any] = {}
        error_container: Dict[str, Any] = {}

        def _execute():
            try:
                exec(compiled, scope)
                # The code must define analyze()
                analyze_fn = scope.get("analyze")
                if analyze_fn is None:
                    error_container["error"] = (
                        "Code must define an 'analyze(model, tokenizer, input_ids)' "
                        "function that returns a dict.")
                    return
                ret = analyze_fn(model, tokenizer, input_ids)
                result_container["result"] = ret
            except _TimeoutError:
                error_container["error"] = (
                    f"Analysis timed out after {effective_timeout}s. "
                    "Simplify your code or increase the timeout.")
            except Exception:
                error_container["error"] = (
                    f"Runtime error:\n{traceback.format_exc()}")

        # Use threading-based timeout (works on all platforms including Windows)
        thread = threading.Thread(target=_execute, daemon=True)
        thread.start()
        thread.join(timeout=effective_timeout)

        if thread.is_alive():
            # Thread is still running — we can't forcibly kill it,
            # but we report the timeout. The daemon thread will be
            # cleaned up when the process exits.
            return {
                "status": "error",
                "error": (
                    f"Analysis timed out after {effective_timeout}s. "
                    "Simplify your code or increase the timeout."),
            }

        if error_container:
            return {"status": "error", "error": error_container["error"]}

        raw_result = result_container.get("result", {})
        return {
            "status": "ok",
            "result": _sanitize_result(raw_result),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    """Import hook that only allows whitelisted modules."""
    root = name.split(".")[0]
    if name not in ALLOWED_MODULES and root not in ("torch", "numpy", "math", "collections", "functools"):
        raise ImportError(
            f"Import of '{name}' is not allowed in exec_analysis. "
            f"Allowed modules: {', '.join(sorted(ALLOWED_MODULES))}")
    return __builtins__["__import__"](name, globals, locals, fromlist, level) if isinstance(__builtins__, dict) \
        else getattr(__builtins__, "__import__")(name, globals, locals, fromlist, level)


def _sanitize_result(obj: Any, depth: int = 0) -> Any:
    """Convert analysis results to JSON-serializable types.

    Recursively handles torch tensors, numpy arrays, and nested dicts/lists.
    Limits recursion depth to prevent infinite loops.
    """
    if depth > 10:
        return str(obj)

    if isinstance(obj, dict):
        return {str(k): _sanitize_result(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_result(v, depth + 1) for v in obj]
    if isinstance(obj, torch.Tensor):
        if obj.numel() <= 100:
            return obj.detach().cpu().tolist()
        # Large tensors: return summary
        return {
            "type": "tensor",
            "shape": list(obj.shape),
            "dtype": str(obj.dtype),
            "min": obj.min().item(),
            "max": obj.max().item(),
            "mean": obj.float().mean().item(),
        }
    try:
        import numpy as np
        if isinstance(obj, np.ndarray):
            if obj.size <= 100:
                return obj.tolist()
            return {
                "type": "ndarray",
                "shape": list(obj.shape),
                "dtype": str(obj.dtype),
                "min": float(obj.min()),
                "max": float(obj.max()),
                "mean": float(obj.mean()),
            }
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
    except ImportError:
        pass
    if isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    return str(obj)
