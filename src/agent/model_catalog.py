"""Available models catalog for the NeuralDebug agent."""

from __future__ import annotations

from typing import Dict, List

from .providers.base import ModelInfo


# Static catalog of well-known models per provider.
# Providers can also dynamically discover models at runtime.
CATALOG: Dict[str, List[ModelInfo]] = {
    "openai": [
        ModelInfo(id="gpt-4o", name="GPT-4o", provider="openai", context_window=128000),
        ModelInfo(id="gpt-4o-mini", name="GPT-4o Mini", provider="openai", context_window=128000),
        ModelInfo(id="gpt-4-turbo", name="GPT-4 Turbo", provider="openai", context_window=128000),
        ModelInfo(id="o3-mini", name="o3-mini", provider="openai", context_window=200000),
    ],
    "anthropic": [
        ModelInfo(id="claude-sonnet-4-20250514", name="Claude Sonnet 4", provider="anthropic", context_window=200000),
        ModelInfo(id="claude-opus-4-20250514", name="Claude Opus 4", provider="anthropic", context_window=200000),
        ModelInfo(id="claude-haiku-3-5-20241022", name="Claude Haiku 3.5", provider="anthropic", context_window=200000),
    ],
    "google": [
        ModelInfo(id="gemini-2.5-flash", name="Gemini 2.5 Flash", provider="google", context_window=1048576),
        ModelInfo(id="gemini-2.5-pro", name="Gemini 2.5 Pro", provider="google", context_window=1048576),
        ModelInfo(id="gemini-2.0-flash", name="Gemini 2.0 Flash", provider="google", context_window=1048576),
    ],
    "ollama": [
        ModelInfo(id="llama3.1", name="Llama 3.1", provider="ollama"),
        ModelInfo(id="llama3.1:70b", name="Llama 3.1 70B", provider="ollama"),
        ModelInfo(id="qwen2.5-coder", name="Qwen 2.5 Coder", provider="ollama"),
        ModelInfo(id="deepseek-coder-v2", name="DeepSeek Coder V2", provider="ollama"),
    ],
    "openrouter": [
        ModelInfo(id="anthropic/claude-sonnet-4", name="Claude Sonnet 4", provider="openrouter", context_window=200000),
        ModelInfo(id="openai/gpt-4o", name="GPT-4o", provider="openrouter", context_window=128000),
        ModelInfo(id="google/gemini-2.5-flash", name="Gemini 2.5 Flash", provider="openrouter", context_window=1048576),
    ],
}


def get_models(provider: str) -> List[ModelInfo]:
    """Return known models for a provider."""
    return CATALOG.get(provider, [])


def get_all_providers() -> List[str]:
    """Return list of all known provider names."""
    return list(CATALOG.keys())
