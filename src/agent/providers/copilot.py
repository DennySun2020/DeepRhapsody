"""GitHub Copilot LLM provider.

Uses the GitHub Models API (models.inference.ai.azure.com) which is
available to GitHub Copilot subscribers. Authentication is handled
automatically via the GitHub CLI (`gh auth token`).

No API key configuration needed — uses your existing GitHub login.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List, Optional, Sequence

from .base import (
    LLMProvider,
    LLMResponse,
    Message,
    ModelInfo,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from .openai_compat import (
    _build_openai_messages,
    _build_openai_tools,
    _parse_response,
)

_GITHUB_MODELS_URL = "https://models.inference.ai.azure.com"

# Models available via GitHub Models that support tool use
_COPILOT_MODELS = [
    ModelInfo(id="gpt-4o", name="GPT-4o", provider="copilot", context_window=128000),
    ModelInfo(id="gpt-4o-mini", name="GPT-4o Mini", provider="copilot", context_window=128000),
    ModelInfo(id="o3-mini", name="o3-mini", provider="copilot", context_window=200000),
]


def _get_github_token() -> str:
    """Obtain a GitHub token via the gh CLI or environment variable."""
    # 1. Check env var first
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token

    # 2. Try gh CLI
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return ""


class CopilotProvider(LLMProvider):
    """Provider using GitHub Copilot via GitHub Models API.

    Authenticates automatically with `gh auth token` — no API key needed.
    """

    def __init__(self, model: Optional[str] = None):
        self._model = model or "gpt-4o"
        self._token = _get_github_token()

    @property
    def name(self) -> str:
        return "GitHub Copilot"

    @property
    def default_model(self) -> str:
        return self._model

    async def chat(
        self,
        messages: Sequence[Message],
        tools: Optional[Sequence[ToolDefinition]] = None,
        *,
        system: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        import httpx

        if not self._token:
            self._token = _get_github_token()
        if not self._token:
            raise RuntimeError(
                "No GitHub token found. Run 'gh auth login' to authenticate."
            )

        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": _build_openai_messages(messages, system),
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = _build_openai_tools(tools)

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{_GITHUB_MODELS_URL}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            return _parse_response(resp.json())

    def list_models(self) -> List[ModelInfo]:
        return list(_COPILOT_MODELS)
