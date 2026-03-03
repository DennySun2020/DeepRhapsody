"""Tests for the Anthropic provider message/response formatting."""

import json
import pytest

from src.agent.providers.base import (
    Message,
    ToolCall,
    ToolDefinition,
)
from src.agent.providers.anthropic import (
    _build_anthropic_messages,
    _build_anthropic_tools,
    _parse_response,
    AnthropicProvider,
)


class TestBuildAnthropicMessages:
    def test_system_skipped(self):
        msgs = [Message(role="system", content="ignore"), Message(role="user", content="hi")]
        result = _build_anthropic_messages(msgs)
        assert len(result) == 1
        assert result[0]["role"] == "user"

    def test_tool_result(self):
        msgs = [Message(role="tool", content='{"ok":true}', tool_call_id="tc_1")]
        result = _build_anthropic_messages(msgs)
        assert result[0]["role"] == "user"
        assert result[0]["content"][0]["type"] == "tool_result"
        assert result[0]["content"][0]["tool_use_id"] == "tc_1"

    def test_assistant_tool_use(self):
        tc = ToolCall(id="tc_1", name="debug", arguments={"x": 1})
        msgs = [Message(role="assistant", content="let me check", tool_calls=[tc])]
        result = _build_anthropic_messages(msgs)
        content = result[0]["content"]
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "tool_use"
        assert content[1]["name"] == "debug"


class TestBuildAnthropicTools:
    def test_conversion(self):
        tools = [ToolDefinition(name="test", description="A test", parameters={"type": "object"})]
        result = _build_anthropic_tools(tools)
        assert result[0]["name"] == "test"
        assert "input_schema" in result[0]


class TestParseAnthropicResponse:
    def test_text_response(self):
        data = {
            "content": [{"type": "text", "text": "Found the bug!"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 50, "output_tokens": 20},
        }
        resp = _parse_response(data)
        assert resp.text == "Found the bug!"
        assert resp.tool_calls is None
        assert resp.usage.prompt_tokens == 50
        assert resp.usage.completion_tokens == 20

    def test_tool_use_response(self):
        data = {
            "content": [
                {"type": "text", "text": "Let me check"},
                {"type": "tool_use", "id": "tu_1", "name": "NeuralDebug_info", "input": {"language": "python"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        resp = _parse_response(data)
        assert resp.text == "Let me check"
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0].name == "NeuralDebug_info"

    def test_empty_content(self):
        data = {"content": [], "usage": {"input_tokens": 0, "output_tokens": 0}}
        resp = _parse_response(data)
        assert resp.text is None
        assert resp.tool_calls is None


class TestAnthropicProviderInit:
    def test_default_values(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        p = AnthropicProvider()
        assert p.name == "Anthropic"
        assert "claude" in p.default_model
        assert p._api_key == "test-key"

    def test_list_models(self):
        p = AnthropicProvider(api_key="test")
        models = p.list_models()
        assert len(models) > 0
        assert any("claude" in m.id for m in models)
