"""HookBackend ABC — framework-agnostic hook management.

Debugger code never calls ``module.register_forward_hook()`` directly.
Instead it asks the hook backend, which wraps the framework-specific
mechanism (PyTorch hooks today, JAX transforms in the future, etc.).
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Optional


class HookHandle:
    """Opaque handle returned by hook registration.

    Call :meth:`remove` to detach the hook.
    """

    def __init__(self, raw_handle: Any = None):
        self._raw = raw_handle

    def remove(self):
        """Remove the hook from the model."""
        if self._raw is not None and hasattr(self._raw, "remove"):
            self._raw.remove()
            self._raw = None


class HookBackend(ABC):
    """Abstract hook management — one instance per debug session."""

    @abstractmethod
    def register_forward_hook(self, module: Any,
                              hook_fn: Callable) -> HookHandle:
        """Register a hook that fires **after** *module*'s forward pass.

        The *hook_fn* signature follows the PyTorch convention::

            hook_fn(module, input, output) -> Optional[output]
        """

    @abstractmethod
    def register_forward_pre_hook(self, module: Any,
                                  hook_fn: Callable) -> HookHandle:
        """Register a hook that fires **before** *module*'s forward pass."""

    @abstractmethod
    def clear_all(self) -> int:
        """Remove all hooks registered through this backend.

        Returns:
            Number of hooks removed.
        """

    @abstractmethod
    def compute_tensor_stats(self, tensor: Any) -> dict:
        """Compute summary statistics for a tensor.

        Returns:
            Dict with keys: shape, dtype, mean, std, min, max.
        """

    # -- convenience -------------------------------------------------------

    @property
    def active_hook_count(self) -> int:
        """Number of hooks currently registered."""
        return 0  # subclasses should override
