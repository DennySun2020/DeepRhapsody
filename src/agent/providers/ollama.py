"""Ollama provider for local LLM models.

Ollama exposes an OpenAI-compatible API at http://localhost:11434/v1,
so this provider inherits from OpenAIProvider with local defaults.
"""

from __future__ import annotations

import os
from typing import List, Optional

from .base import ModelInfo
from .openai_compat import OpenAIProvider


class OllamaProvider(OpenAIProvider):
    """Provider for Ollama local models."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        resolved_base = base_url or os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        resolved_base = resolved_base.rstrip("/")
        # Ollama's OpenAI-compat endpoint lives at /v1
        if not resolved_base.endswith("/v1"):
            resolved_base += "/v1"

        super().__init__(
            api_key="ollama",  # Ollama doesn't require auth
            base_url=resolved_base,
            model=model or "llama3.1",
        )

    @property
    def name(self) -> str:
        return "Ollama"

    def list_models(self) -> List[ModelInfo]:
        """Attempt to discover models from the Ollama API, fall back to common defaults."""
        try:
            import httpx
            # The tags endpoint is on the base Ollama URL, not the /v1 path
            base = self._base_url.replace("/v1", "")
            resp = httpx.get(f"{base}/api/tags", timeout=5)
            resp.raise_for_status()
            models = []
            for m in resp.json().get("models", []):
                name = m.get("name", "")
                models.append(ModelInfo(
                    id=name,
                    name=name,
                    provider="ollama",
                    supports_tools=True,
                ))
            return models if models else self._default_models()
        except Exception:
            return self._default_models()

    @staticmethod
    def _default_models() -> List[ModelInfo]:
        return [
            ModelInfo(id="llama3.1", name="Llama 3.1", provider="ollama"),
            ModelInfo(id="llama3.1:70b", name="Llama 3.1 70B", provider="ollama"),
            ModelInfo(id="qwen2.5-coder", name="Qwen 2.5 Coder", provider="ollama"),
            ModelInfo(id="deepseek-coder-v2", name="DeepSeek Coder V2", provider="ollama"),
        ]
