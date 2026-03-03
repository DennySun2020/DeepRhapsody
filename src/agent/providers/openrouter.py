"""OpenRouter meta-provider — routes to any model through a single API."""

from __future__ import annotations

import os
from typing import List, Optional

from .base import ModelInfo
from .openai_compat import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    """Provider for OpenRouter (openrouter.ai).

    OpenRouter exposes an OpenAI-compatible API that routes to
    hundreds of models from many providers.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        super().__init__(
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY", ""),
            base_url="https://openrouter.ai/api/v1",
            model=model or "anthropic/claude-sonnet-4",
        )

    @property
    def name(self) -> str:
        return "OpenRouter"

    def list_models(self) -> List[ModelInfo]:
        return [
            ModelInfo(id="anthropic/claude-sonnet-4", name="Claude Sonnet 4", provider="openrouter", context_window=200000),
            ModelInfo(id="openai/gpt-4o", name="GPT-4o", provider="openrouter", context_window=128000),
            ModelInfo(id="google/gemini-2.5-flash", name="Gemini 2.5 Flash", provider="openrouter", context_window=1048576),
            ModelInfo(id="meta-llama/llama-3.1-405b", name="Llama 3.1 405B", provider="openrouter"),
        ]
