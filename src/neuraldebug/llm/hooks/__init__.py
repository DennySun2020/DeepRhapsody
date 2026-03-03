# Hook backends for framework-agnostic LLM debugging.
from .base import HookBackend
from .pytorch import PyTorchHookBackend

# Backward-compatible aliases for the legacy debugger code.
# The original hooks.py exported HookManager (a class) and
# compute_tensor_stats (a standalone function).
HookManager = PyTorchHookBackend


class _TensorStats(dict):
    """Thin dict wrapper that also supports .to_dict() for legacy callers."""
    def to_dict(self):
        return dict(self)


def compute_tensor_stats(tensor):
    """Standalone helper matching the original hooks.compute_tensor_stats API."""
    try:
        import torch
        t = tensor.detach().float()
        return _TensorStats(
            shape=list(tensor.shape),
            dtype=str(tensor.dtype),
            mean=t.mean().item(),
            std=t.std().item() if t.numel() > 1 else 0.0,
            min=t.min().item(),
            max=t.max().item(),
        )
    except Exception:
        return _TensorStats(shape=[], dtype="unknown", mean=0, std=0, min=0, max=0)


__all__ = [
    "HookBackend", "PyTorchHookBackend",
    "HookManager", "compute_tensor_stats",
]
