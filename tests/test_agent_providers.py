"""Tests for the agent provider base types and interface."""

import pytest
from src.agent.providers.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ModelInfo,
    ToolCall,
    ToolDefinition,
    TokenUsage,
)


class TestToolCall:
    def test_basic_creation(self):
        tc = ToolCall(id="call_1", name="NeuralDebug_info", arguments={"language": "python"})
        assert tc.id == "call_1"
        assert tc.name == "NeuralDebug_info"
        assert tc.arguments == {"language": "python"}


class TestLLMResponse:
    def test_text_response(self):
        resp = LLMResponse(text="Hello world")
        assert resp.text == "Hello world"
        assert resp.tool_calls is None

    def test_tool_call_response(self):
        tc = ToolCall(id="call_1", name="test_tool", arguments={})
        resp = LLMResponse(tool_calls=[tc])
        assert resp.text is None
        assert len(resp.tool_calls) == 1

    def test_to_assistant_message_text(self):
        resp = LLMResponse(text="Hello")
        msg = resp.to_assistant_message()
        assert msg.role == "assistant"
        assert msg.content == "Hello"
        assert msg.tool_calls is None

    def test_to_assistant_message_tool_calls(self):
        tc = ToolCall(id="call_1", name="test", arguments={"a": 1})
        resp = LLMResponse(text="thinking...", tool_calls=[tc])
        msg = resp.to_assistant_message()
        assert msg.role == "assistant"
        assert msg.tool_calls == [tc]


class TestMessage:
    def test_user_message(self):
        msg = Message(role="user", content="debug this")
        assert msg.role == "user"
        assert msg.content == "debug this"

    def test_tool_message(self):
        msg = Message(role="tool", content='{"status":"ok"}', tool_call_id="call_1", name="test_tool")
        assert msg.tool_call_id == "call_1"
        assert msg.name == "test_tool"


class TestToolDefinition:
    def test_basic(self):
        td = ToolDefinition(
            name="NeuralDebug_info",
            description="Get debugger info",
            parameters={"type": "object", "properties": {}},
        )
        assert td.name == "NeuralDebug_info"
        assert "object" in str(td.parameters)


class TestTokenUsage:
    def test_defaults(self):
        usage = TokenUsage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0


class TestModelInfo:
    def test_basic(self):
        m = ModelInfo(id="gpt-4o", name="GPT-4o", provider="openai", context_window=128000)
        assert m.id == "gpt-4o"
        assert m.supports_tools is True


class TestLLMProviderIsAbstract:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            LLMProvider()  # type: ignore[abstract]
