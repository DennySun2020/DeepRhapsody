"""Google Gemini LLM provider."""

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


def _build_gemini_contents(
    messages: Sequence[Message],
    system: Optional[str],
) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Convert messages to Gemini contents format.

    Returns (system_instruction, contents).
    """
    system_instruction = None
    if system:
        system_instruction = {"parts": [{"text": system}]}

    contents: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.role == "system":
            continue
        elif msg.role == "tool":
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": msg.name or "",
                        "response": {
                            "result": msg.content if isinstance(msg.content, str) else json.dumps(msg.content),
                        },
                    }
                }],
            })
        elif msg.role == "assistant" and msg.tool_calls:
            parts: List[Dict[str, Any]] = []
            if msg.content:
                parts.append({"text": msg.content})
            for tc in msg.tool_calls:
                parts.append({
                    "functionCall": {
                        "name": tc.name,
                        "args": tc.arguments,
                    }
                })
            contents.append({"role": "model", "parts": parts})
        elif msg.role == "assistant":
            contents.append({"role": "model", "parts": [{"text": msg.content or ""}]})
        else:
            contents.append({"role": "user", "parts": [{"text": msg.content or ""}]})

    return system_instruction, contents


def _build_gemini_tools(tools: Sequence[ToolDefinition]) -> List[Dict[str, Any]]:
    """Convert ToolDefinition list to Gemini function declarations."""
    declarations = []
    for t in tools:
        decl: Dict[str, Any] = {
            "name": t.name,
            "description": t.description,
        }
        if t.parameters:
            decl["parameters"] = t.parameters
        declarations.append(decl)
    return [{"functionDeclarations": declarations}]


def _parse_response(data: Dict[str, Any]) -> LLMResponse:
    """Parse a Gemini generateContent response."""
    candidates = data.get("candidates", [])
    if not candidates:
        return LLMResponse(text=None, stop_reason="empty")

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])

    text_parts: List[str] = []
    tool_calls: List[ToolCall] = []

    for i, part in enumerate(parts):
        if "text" in part:
            text_parts.append(part["text"])
        elif "functionCall" in part:
            fc = part["functionCall"]
            tool_calls.append(ToolCall(
                id=f"call_{i}",
                name=fc.get("name", ""),
                arguments=fc.get("args", {}),
            ))

    usage = None
    usage_raw = data.get("usageMetadata")
    if usage_raw:
        usage = TokenUsage(
            prompt_tokens=usage_raw.get("promptTokenCount", 0),
            completion_tokens=usage_raw.get("candidatesTokenCount", 0),
            total_tokens=usage_raw.get("totalTokenCount", 0),
        )

    return LLMResponse(
        text="\n".join(text_parts) if text_parts else None,
        tool_calls=tool_calls if tool_calls else None,
        usage=usage,
        stop_reason=candidates[0].get("finishReason"),
        raw=data,
    )


class GoogleProvider(LLMProvider):
    """Provider for Google Gemini models."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._api_key = api_key or os.environ.get("GOOGLE_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")
        self._model = model or "gemini-2.5-flash"
        self._base_url = "https://generativelanguage.googleapis.com/v1beta"

    @property
    def name(self) -> str:
        return "Google"

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

        system_instruction, contents = _build_gemini_contents(messages, system)

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        if max_tokens is not None:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens
        if tools:
            payload["tools"] = _build_gemini_tools(tools)

        url = f"{self._base_url}/models/{self._model}:generateContent?key={self._api_key}"

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return _parse_response(resp.json())

    def list_models(self) -> List[ModelInfo]:
        return [
            ModelInfo(id="gemini-2.5-flash", name="Gemini 2.5 Flash", provider="google", context_window=1048576),
            ModelInfo(id="gemini-2.5-pro", name="Gemini 2.5 Pro", provider="google", context_window=1048576),
            ModelInfo(id="gemini-2.0-flash", name="Gemini 2.0 Flash", provider="google", context_window=1048576),
        ]
