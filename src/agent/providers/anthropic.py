"""Anthropic (Claude) LLM provider."""

from __future__ import annotations

import json
import os
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


def _build_anthropic_messages(messages: Sequence[Message]) -> List[Dict[str, Any]]:
    """Convert our Message list to Anthropic wire format."""
    out: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.role == "system":
            continue  # handled separately via system parameter
        elif msg.role == "tool":
            out.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "",
                    "content": msg.content if isinstance(msg.content, str) else json.dumps(msg.content),
                }],
            })
        elif msg.role == "assistant" and msg.tool_calls:
            blocks: List[Dict[str, Any]] = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls:
                blocks.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments,
                })
            out.append({"role": "assistant", "content": blocks})
        else:
            content = msg.content or ""
            out.append({"role": msg.role, "content": content})
    return out


def _build_anthropic_tools(tools: Sequence[ToolDefinition]) -> List[Dict[str, Any]]:
    """Convert ToolDefinition list to Anthropic tools format."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters,
        }
        for t in tools
    ]


def _parse_response(data: Dict[str, Any]) -> LLMResponse:
    """Parse an Anthropic Messages API response."""
    text_parts: List[str] = []
    tool_calls: List[ToolCall] = []

    for block in data.get("content", []):
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append(ToolCall(
                id=block.get("id", ""),
                name=block.get("name", ""),
                arguments=block.get("input", {}),
            ))

    usage_raw = data.get("usage", {})
    usage = TokenUsage(
        prompt_tokens=usage_raw.get("input_tokens", 0),
        completion_tokens=usage_raw.get("output_tokens", 0),
        total_tokens=usage_raw.get("input_tokens", 0) + usage_raw.get("output_tokens", 0),
    )

    return LLMResponse(
        text="\n".join(text_parts) if text_parts else None,
        tool_calls=tool_calls if tool_calls else None,
        usage=usage,
        stop_reason=data.get("stop_reason"),
        raw=data,
    )


class AnthropicProvider(LLMProvider):
    """Provider for Anthropic Claude models."""

    API_VERSION = "2023-06-01"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._base_url = (base_url or os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")).rstrip("/")
        self._model = model or "claude-sonnet-4-20250514"

    @property
    def name(self) -> str:
        return "Anthropic"

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

        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": _build_anthropic_messages(messages),
            "max_tokens": max_tokens or 8192,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = _build_anthropic_tools(tools)

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": self.API_VERSION,
        }

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{self._base_url}/v1/messages",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            return _parse_response(resp.json())

    def list_models(self) -> List[ModelInfo]:
        return [
            ModelInfo(id="claude-sonnet-4-20250514", name="Claude Sonnet 4", provider="anthropic", context_window=200000),
            ModelInfo(id="claude-opus-4-20250514", name="Claude Opus 4", provider="anthropic", context_window=200000),
            ModelInfo(id="claude-haiku-3-5-20241022", name="Claude Haiku 3.5", provider="anthropic", context_window=200000),
        ]
