"""OpenAI-compatible LLM provider.

Supports OpenAI, DeepSeek, Groq, Together, and any API that implements
the OpenAI chat completions format.
"""

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


def _build_openai_messages(
    messages: Sequence[Message],
    system: Optional[str],
) -> List[Dict[str, Any]]:
    """Convert our Message list to OpenAI wire format."""
    out: List[Dict[str, Any]] = []
    if system:
        out.append({"role": "system", "content": system})

    for msg in messages:
        if msg.role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": msg.tool_call_id or "",
                "content": msg.content if isinstance(msg.content, str) else json.dumps(msg.content),
            })
        elif msg.role == "assistant" and msg.tool_calls:
            tc_list = []
            for tc in msg.tool_calls:
                tc_list.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments,
                    },
                })
            entry: Dict[str, Any] = {"role": "assistant", "tool_calls": tc_list}
            if msg.content:
                entry["content"] = msg.content
            out.append(entry)
        else:
            out.append({"role": msg.role, "content": msg.content or ""})
    return out


def _build_openai_tools(tools: Sequence[ToolDefinition]) -> List[Dict[str, Any]]:
    """Convert ToolDefinition list to OpenAI tools format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def _parse_response(data: Dict[str, Any]) -> LLMResponse:
    """Parse an OpenAI-format chat completion response."""
    choice = data.get("choices", [{}])[0]
    message = choice.get("message", {})

    text = message.get("content")
    stop_reason = choice.get("finish_reason")

    tool_calls = None
    raw_tcs = message.get("tool_calls")
    if raw_tcs:
        tool_calls = []
        for tc in raw_tcs:
            fn = tc.get("function", {})
            args_raw = fn.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {"raw": args_raw}
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=fn.get("name", ""),
                arguments=args,
            ))

    usage = None
    raw_usage = data.get("usage")
    if raw_usage:
        usage = TokenUsage(
            prompt_tokens=raw_usage.get("prompt_tokens", 0),
            completion_tokens=raw_usage.get("completion_tokens", 0),
            total_tokens=raw_usage.get("total_tokens", 0),
        )

    return LLMResponse(
        text=text,
        tool_calls=tool_calls if tool_calls else None,
        usage=usage,
        stop_reason=stop_reason,
        raw=data,
    )


class OpenAIProvider(LLMProvider):
    """Provider for OpenAI and OpenAI-compatible APIs."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        organization: Optional[str] = None,
    ):
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self._model = model or "gpt-4o"
        self._organization = organization or os.environ.get("OPENAI_ORG_ID")

    @property
    def name(self) -> str:
        return "OpenAI"

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
            "messages": _build_openai_messages(messages, system),
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = _build_openai_tools(tools)

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        if self._organization:
            headers["OpenAI-Organization"] = self._organization

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            return _parse_response(resp.json())

    def list_models(self) -> List[ModelInfo]:
        return [
            ModelInfo(id="gpt-4o", name="GPT-4o", provider="openai", context_window=128000),
            ModelInfo(id="gpt-4o-mini", name="GPT-4o Mini", provider="openai", context_window=128000),
            ModelInfo(id="gpt-4-turbo", name="GPT-4 Turbo", provider="openai", context_window=128000),
            ModelInfo(id="o3-mini", name="o3-mini", provider="openai", context_window=200000),
        ]
