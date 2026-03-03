"""Adapter registry — auto-detect model architecture or select manually.

Usage::

    from adapters import AdapterRegistry

    # Auto-detect (inspects model attributes)
    adapter = AdapterRegistry.auto_detect(model)

    # Manual selection
    adapter = AdapterRegistry.from_name("gpt2", model)

    # Register a custom adapter
    AdapterRegistry.register("phi", PhiAdapter)
    adapter = AdapterRegistry.from_name("phi", model)
"""

from typing import Any, Dict, List, Optional, Type

from .base import ModelAdapter


class AdapterRegistry:
    """Central registry for model adapters.

    Built-in adapters (GPT-2, Llama) are registered on import.
    Users register additional adapters for custom architectures.
    """

    _adapters: Dict[str, Type[ModelAdapter]] = {}
    _detect_fns: List[tuple] = []   # [(predicate, adapter_cls), ...]

    @classmethod
    def register(cls, name: str, adapter_cls: Type[ModelAdapter],
                 detect_fn=None):
        """Register an adapter class under *name*.

        Args:
            name: Short name (e.g. ``"gpt2"``, ``"llama"``).
            adapter_cls: A :class:`ModelAdapter` subclass.
            detect_fn: Optional callable ``(model) -> bool`` for
                auto-detection.  If provided, :meth:`auto_detect` will
                try this predicate.
        """
        cls._adapters[name] = adapter_cls
        if detect_fn is not None:
            cls._detect_fns.append((detect_fn, adapter_cls))

    @classmethod
    def auto_detect(cls, model: Any) -> ModelAdapter:
        """Inspect model structure and return the correct adapter.

        Tries registered detection functions first, then falls back to
        built-in heuristics.

        Raises:
            ValueError: If no adapter matches.
        """
        # User-registered detectors (checked first — higher priority)
        for predicate, adapter_cls in cls._detect_fns:
            try:
                if predicate(model):
                    return adapter_cls(model)
            except Exception:
                continue

        # Built-in heuristics
        # GPT-2: model.transformer.h
        if hasattr(model, "transformer") and hasattr(
                getattr(model, "transformer"), "h"):
            from .gpt2 import GPT2Adapter
            return GPT2Adapter(model)

        # Llama / Mistral / Qwen-2 / DeepSeek: model.model.layers
        if hasattr(model, "model") and hasattr(
                getattr(model, "model"), "layers"):
            from .llama import LlamaAdapter
            return LlamaAdapter(model)

        # Phi-2 / Phi-3: model.model.layers (same as Llama heuristic)
        # — already covered above.

        # GPT-NeoX / Pythia: model.gpt_neox.layers
        if hasattr(model, "gpt_neox") and hasattr(
                getattr(model, "gpt_neox"), "layers"):
            raise ValueError(
                f"Model looks like GPT-NeoX / Pythia ({type(model).__name__}) "
                f"but no GPTNeoXAdapter is registered.  Implement one and "
                f"register it with AdapterRegistry.register('gptneox', cls).")

        raise ValueError(
            f"Cannot auto-detect model architecture for "
            f"{type(model).__name__}.  "
            f"Register a custom adapter with "
            f"AdapterRegistry.register(name, MyAdapter, "
            f"detect_fn=lambda m: hasattr(m, 'my_attr')).")

    @classmethod
    def from_name(cls, name: str, model: Any) -> ModelAdapter:
        """Create an adapter by explicit name.

        Raises:
            KeyError: If *name* was never registered.
        """
        if name not in cls._adapters:
            available = ", ".join(sorted(cls._adapters)) or "(none)"
            raise KeyError(
                f"No adapter registered under '{name}'.  "
                f"Available: {available}")
        return cls._adapters[name](model)

    @classmethod
    def list_adapters(cls) -> List[str]:
        """Return names of all registered adapters."""
        return sorted(cls._adapters)

    @classmethod
    def reset(cls):
        """Clear all registrations (mainly for testing)."""
        cls._adapters.clear()
        cls._detect_fns.clear()
        _register_builtins()


def _register_builtins():
    """Register the built-in adapters on module import."""
    from .gpt2 import GPT2Adapter
    from .llama import LlamaAdapter

    AdapterRegistry.register(
        "gpt2", GPT2Adapter,
        detect_fn=lambda m: (
            hasattr(m, "transformer")
            and hasattr(m.transformer, "h")),
    )
    AdapterRegistry.register(
        "llama", LlamaAdapter,
        detect_fn=lambda m: (
            hasattr(m, "model")
            and hasattr(m.model, "layers")
            and not hasattr(m, "transformer")),
    )


_register_builtins()
