"""PyTorch hook backend — wraps torch.nn.Module hook registration."""

from typing import Any, Callable, List

import torch
import torch.nn as nn

from .base import HookBackend, HookHandle


class PyTorchHookBackend(HookBackend):
    """Hook backend using ``torch.nn.Module.register_forward_hook``."""

    def __init__(self):
        self._handles: List[HookHandle] = []

    # -- registration ------------------------------------------------------

    def register_forward_hook(self, module: nn.Module,
                              hook_fn: Callable) -> HookHandle:
        raw = module.register_forward_hook(hook_fn)
        handle = HookHandle(raw)
        self._handles.append(handle)
        return handle

    def register_forward_pre_hook(self, module: nn.Module,
                                  hook_fn: Callable) -> HookHandle:
        raw = module.register_forward_pre_hook(hook_fn)
        handle = HookHandle(raw)
        self._handles.append(handle)
        return handle

    # -- cleanup -----------------------------------------------------------

    def clear_all(self) -> int:
        count = len(self._handles)
        for h in self._handles:
            h.remove()
        self._handles.clear()
        return count

    # Backward-compatible aliases used by the legacy debugger code
    remove_all = clear_all

    def register_on_model(self, model: nn.Module) -> None:
        """Register activation-capture hooks on every sub-module.

        This is the legacy HookManager API.  The hook simply records the
        output tensor statistics so that ``inspect`` can display them.
        """
        self._activation_stats: dict = {}

        def _make_hook(name: str):
            def _hook(_mod, _inp, out):
                tensor = out[0] if isinstance(out, tuple) else out
                if isinstance(tensor, torch.Tensor):
                    self._activation_stats[name] = self.compute_tensor_stats(tensor)
            return _hook

        for name, mod in model.named_modules():
            if name:
                self.register_forward_hook(mod, _make_hook(name))

    # -- tensor stats ------------------------------------------------------

    def compute_tensor_stats(self, tensor: torch.Tensor) -> dict:
        t = tensor.detach().float()
        return {
            "shape": list(tensor.shape),
            "dtype": str(tensor.dtype),
            "mean": t.mean().item(),
            "std": t.std().item() if t.numel() > 1 else 0.0,
            "min": t.min().item(),
            "max": t.max().item(),
        }

    # -- property ----------------------------------------------------------

    @property
    def active_hook_count(self) -> int:
        return len(self._handles)
