"""Tests for the OpenAI-compatible provider message/response formatting."""

import json
import pytest

from src.agent.providers.base import (
    LLMResponse,
    Message,
    ToolCall,
    ToolDefinition,
)
from src.agent.providers.openai_compat import (
    _build_openai_messages,
    _build_openai_tools,
    _parse_response,
    OpenAIProvider,
)


class TestBuildOpenAIMessages:
    def test_system_prepended(self):
        msgs = [Message(role="user", content="hello")]
        result = _build_openai_messages(msgs, system="You are a debugger")
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are a debugger"
        assert result[1]["role"] == "user"

    def test_no_system(self):
        msgs = [Message(role="user", content="hello")]
        result = _build_openai_messages(msgs, system=None)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_tool_message(self):
        msgs = [Message(role="tool", content='{"ok":true}', tool_call_id="call_1")]
        result = _build_openai_messages(msgs, None)
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "call_1"

    def test_assistant_with_tool_calls(self):
        tc = ToolCall(id="call_1", name="test_fn", arguments={"x": 1})
        msgs = [Message(role="assistant", content="thinking", tool_calls=[tc])]
        result = _build_openai_messages(msgs, None)
        assert result[0]["role"] == "assistant"
        assert len(result[0]["tool_calls"]) == 1
        assert result[0]["tool_calls"][0]["function"]["name"] == "test_fn"


class TestBuildOpenAITools:
    def test_conversion(self):
        tools = [
            ToolDefinition(
                name="NeuralDebug_info",
                description="Get info",
                parameters={"type": "object", "properties": {"lang": {"type": "string"}}},
            )
        ]
        result = _build_openai_tools(tools)
        assert len(result) == 1
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "NeuralDebug_info"


class TestParseResponse:
    def test_text_response(self):
        data = {
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        resp = _parse_response(data)
        assert resp.text == "Hello"
        assert resp.tool_calls is None
        assert resp.usage.total_tokens == 15
        assert resp.stop_reason == "stop"

    def test_tool_call_response(self):
        data = {
            "choices": [{
                "message": {
                    "content": None,
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "NeuralDebug_info",
                            "arguments": '{"language":"python"}',
                        },
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        resp = _parse_response(data)
        assert resp.text is None
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "NeuralDebug_info"
        assert resp.tool_calls[0].arguments == {"language": "python"}

    def test_malformed_arguments(self):
        data = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call_1",
                        "function": {"name": "test", "arguments": "not json"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        }
        resp = _parse_response(data)
        assert resp.tool_calls[0].arguments == {"raw": "not json"}


class TestOpenAIProviderInit:
    def test_default_values(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-123")
        p = OpenAIProvider()
        assert p.name == "OpenAI"
        assert p.default_model == "gpt-4o"
        assert p._api_key == "test-key-123"

    def test_custom_values(self):
        p = OpenAIProvider(api_key="sk-test", base_url="https://custom.api/v1", model="gpt-4-turbo")
        assert p._api_key == "sk-test"
        assert "custom.api" in p._base_url
        assert p.default_model == "gpt-4-turbo"

    def test_list_models(self):
        p = OpenAIProvider(api_key="test")
        models = p.list_models()
        assert len(models) > 0
        assert any(m.id == "gpt-4o" for m in models)
