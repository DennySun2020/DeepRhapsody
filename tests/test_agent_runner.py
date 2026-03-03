"""Tests for the agent runner."""

import json
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
from src.agent.config import AgentConfig
from src.agent.tools.registry import ToolRegistry
from src.agent.tools.base import Tool
from src.agent.runner import AgentRunner


class MockTool(Tool):
    """A mock tool that returns a fixed result."""

    def __init__(self, name: str = "mock_tool", result: str = '{"status":"ok"}'):
        self._name = name
        self._result = result

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self._name,
            description=f"Mock tool: {self._name}",
            parameters={"type": "object", "properties": {}},
        )

    async def execute(self, arguments):
        return self._result


class MockProvider(LLMProvider):
    """A mock LLM provider that returns scripted responses."""

    def __init__(self, responses: list[LLMResponse]):
        self._responses = list(responses)
        self._call_count = 0

    @property
    def name(self) -> str:
        return "Mock"

    @property
    def default_model(self) -> str:
        return "mock-model"

    async def chat(self, messages, tools=None, *, system=None, temperature=0.0, max_tokens=None) -> LLMResponse:
        if self._call_count >= len(self._responses):
            return LLMResponse(text="[no more scripted responses]")
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp

    def list_models(self):
        return [ModelInfo(id="mock-model", name="Mock Model", provider="mock")]


class TestAgentRunner:
    @pytest.mark.asyncio
    async def test_simple_text_response(self):
        """Agent should return text when model gives a direct answer."""
        provider = MockProvider([LLMResponse(text="The bug is a null pointer.")])
        tools = ToolRegistry()
        config = AgentConfig(max_turns=10)

        agent = AgentRunner(provider, tools, config, system_prompt="You are a debugger.")
        result = await agent.run("find the bug")

        assert result == "The bug is a null pointer."
        assert len(agent.messages) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_tool_call_then_response(self):
        """Agent calls a tool, then responds with text."""
        tc = ToolCall(id="call_1", name="mock_tool", arguments={})
        provider = MockProvider([
            LLMResponse(tool_calls=[tc]),
            LLMResponse(text="Based on the tool result, the bug is in line 42."),
        ])
        tools = ToolRegistry()
        tools.register(MockTool())
        config = AgentConfig(max_turns=10)

        agent = AgentRunner(provider, tools, config)
        result = await agent.run("debug my code")

        assert "line 42" in result
        # Messages: user, assistant(tool_call), tool_result, assistant(text)
        assert len(agent.messages) == 4

    @pytest.mark.asyncio
    async def test_multiple_tool_calls(self):
        """Agent makes multiple sequential tool calls."""
        tc1 = ToolCall(id="call_1", name="mock_tool", arguments={})
        tc2 = ToolCall(id="call_2", name="mock_tool", arguments={})
        provider = MockProvider([
            LLMResponse(tool_calls=[tc1]),
            LLMResponse(tool_calls=[tc2]),
            LLMResponse(text="Done investigating."),
        ])
        tools = ToolRegistry()
        tools.register(MockTool())
        config = AgentConfig(max_turns=10)

        agent = AgentRunner(provider, tools, config)
        result = await agent.run("investigate")

        assert result == "Done investigating."

    @pytest.mark.asyncio
    async def test_max_turns_limit(self):
        """Agent should stop after max_turns."""
        tc = ToolCall(id="call_1", name="mock_tool", arguments={})
        # Provider always returns tool calls, never text
        provider = MockProvider([LLMResponse(tool_calls=[tc])] * 5)
        tools = ToolRegistry()
        tools.register(MockTool())
        config = AgentConfig(max_turns=3)

        agent = AgentRunner(provider, tools, config)
        result = await agent.run("infinite loop")

        assert "maximum" in result.lower()

    @pytest.mark.asyncio
    async def test_callbacks_called(self):
        """Verify on_tool_call and on_tool_result callbacks fire."""
        tc = ToolCall(id="call_1", name="mock_tool", arguments={"x": 1})
        provider = MockProvider([
            LLMResponse(tool_calls=[tc]),
            LLMResponse(text="Done."),
        ])
        tools = ToolRegistry()
        tools.register(MockTool())
        config = AgentConfig(max_turns=10)

        call_log = []
        result_log = []

        agent = AgentRunner(
            provider, tools, config,
            on_tool_call=lambda tc: call_log.append(tc.name),
            on_tool_result=lambda tc, r: result_log.append(tc.name),
        )
        await agent.run("test")

        assert call_log == ["mock_tool"]
        assert result_log == ["mock_tool"]

    @pytest.mark.asyncio
    async def test_reset(self):
        provider = MockProvider([LLMResponse(text="first")])
        tools = ToolRegistry()
        config = AgentConfig()
        agent = AgentRunner(provider, tools, config)

        await agent.run("hello")
        assert len(agent.messages) == 2

        agent.reset()
        assert len(agent.messages) == 0
        assert agent.total_usage.total_tokens == 0

    @pytest.mark.asyncio
    async def test_usage_accumulation(self):
        provider = MockProvider([
            LLMResponse(text="hi", usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)),
        ])
        tools = ToolRegistry()
        config = AgentConfig()
        agent = AgentRunner(provider, tools, config)

        await agent.run("hello")
        assert agent.total_usage.total_tokens == 15
