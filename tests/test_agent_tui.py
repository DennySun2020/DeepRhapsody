"""Tests for the Rich Terminal UI module."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure agent path is importable
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.tui import (
    NeuralDebugTUI,
    SLASH_COMMANDS,
    is_tui_available,
    NeuralDebug_THEME,
)
from src.agent.providers.base import (
    LLMProvider,
    LLMResponse,
    Message,
    ToolCall,
    ToolDefinition,
    TokenUsage,
    ModelInfo,
)
from src.agent.config import AgentConfig
from src.agent.runner import AgentRunner
from src.agent.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _MockProvider(LLMProvider):
    """Minimal mock provider for TUI tests."""

    def __init__(self):
        self._responses = []
        self._call_count = 0

    def queue(self, resp: LLMResponse) -> None:
        self._responses.append(resp)

    async def chat(self, messages, tools=None, *, system=None, temperature=0.0, max_tokens=None):
        if self._call_count < len(self._responses):
            r = self._responses[self._call_count]
        else:
            r = LLMResponse(text="(no more responses)")
        self._call_count += 1
        return r

    def list_models(self):
        return [ModelInfo(id="mock-1", name="Mock", provider="mock")]

    @property
    def name(self):
        return "MockProvider"

    @property
    def default_model(self):
        return "mock-1"


def _make_agent(**kwargs) -> AgentRunner:
    provider = _MockProvider()
    config = AgentConfig(provider="mock", model="mock-1", **kwargs)
    tools = ToolRegistry()
    return AgentRunner(provider=provider, tools=tools, config=config, system_prompt="test")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTUIAvailability:
    def test_is_tui_available(self):
        # Rich is installed in test env
        assert is_tui_available() is True

    def test_tui_creates_successfully(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        assert tui.agent is agent
        assert tui._turn_count == 0
        assert tui._tool_call_count == 0


class TestTheme:
    def test_theme_has_expected_keys(self):
        expected = ["info", "warning", "error", "success", "tool.name", "user.prompt"]
        for key in expected:
            assert key in NeuralDebug_THEME


class TestSlashCommands:
    def test_known_commands(self):
        assert "/help" in SLASH_COMMANDS
        assert "/clear" in SLASH_COMMANDS
        assert "/reset" in SLASH_COMMANDS
        assert "/status" in SLASH_COMMANDS
        assert "/tools" in SLASH_COMMANDS
        assert "/quit" in SLASH_COMMANDS
        assert "/history" in SLASH_COMMANDS

    def test_help_returns_none(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        result = tui._handle_slash_command("/help")
        assert result is None

    def test_quit_returns_quit(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        result = tui._handle_slash_command("/quit")
        assert result == "quit"

    def test_exit_returns_quit(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        result = tui._handle_slash_command("/exit")
        assert result == "quit"

    def test_q_returns_quit(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        result = tui._handle_slash_command("/q")
        assert result == "quit"

    def test_status_returns_none(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        result = tui._handle_slash_command("/status")
        assert result is None

    def test_tools_returns_none(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        result = tui._handle_slash_command("/tools")
        assert result is None

    def test_history_returns_none(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        result = tui._handle_slash_command("/history")
        assert result is None

    def test_unknown_command_returns_none(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        result = tui._handle_slash_command("/nonexistent")
        assert result is None

    def test_clear_resets_state(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tui._turn_count = 5
        tui._tool_call_count = 10
        tui._handle_slash_command("/clear")
        assert tui._turn_count == 0
        assert tui._tool_call_count == 0

    def test_reset_clears_counters(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tui._turn_count = 3
        tui._tool_call_count = 7
        tui._handle_slash_command("/reset")
        assert tui._turn_count == 0
        assert tui._tool_call_count == 0


class TestToolCallbacks:
    def test_tool_call_increments_counter(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tc = ToolCall(id="tc-1", name="start_debug_session", arguments={"language": "python"})
        tui._on_tool_call(tc)
        assert tui._tool_call_count == 1

    def test_multiple_tool_calls(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        for i in range(5):
            tc = ToolCall(id=f"tc-{i}", name="set_breakpoint", arguments={"file": "main.py", "line": i})
            tui._on_tool_call(tc)
        assert tui._tool_call_count == 5

    def test_tool_call_with_no_args(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tc = ToolCall(id="tc-1", name="list_breakpoints", arguments={})
        tui._on_tool_call(tc)
        assert tui._tool_call_count == 1

    def test_tool_result_renders(self):
        """Tool result callback should not raise."""
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tc = ToolCall(id="tc-1", name="get_variable", arguments={"name": "x"})
        tui._on_tool_result(tc, '{"name": "x", "value": 42}')

    def test_tool_result_long_text_truncated(self):
        """Long results should be truncated without error."""
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tc = ToolCall(id="tc-1", name="get_backtrace", arguments={})
        long_result = "line " * 1000
        tui._on_tool_result(tc, long_result)


class TestResponseRendering:
    def test_print_response_markdown(self):
        """Render a markdown response without error."""
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tui.print_response("# Bug Found\n\nThe issue is in `main.py` line 42:\n```python\nx = None\nx.foo()  # AttributeError\n```")

    def test_print_response_empty(self):
        """Empty responses should be handled gracefully."""
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tui.print_response("")
        tui.print_response("   ")


class TestTokenFormatting:
    def test_zero_tokens(self):
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        assert tui._format_tokens() == "0"

    def test_nonzero_tokens(self):
        agent = _make_agent()
        agent.total_usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        tui = NeuralDebugTUI(agent)
        result = tui._format_tokens()
        assert "150" in result
        assert "100" in result
        assert "50" in result

    def test_large_tokens_comma_formatted(self):
        agent = _make_agent()
        agent.total_usage = TokenUsage(prompt_tokens=10000, completion_tokens=5000, total_tokens=15000)
        tui = NeuralDebugTUI(agent)
        result = tui._format_tokens()
        assert "15,000" in result


class TestBanner:
    def test_print_banner(self):
        """Banner should print without error."""
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tui.print_banner()

    def test_print_status(self):
        """Status should print without error."""
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tui.print_status()


class TestRunLoop:
    @pytest.mark.asyncio
    async def test_quit_on_none_input(self):
        """TUI exits cleanly when input returns None (Ctrl+C)."""
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tui._get_input = MagicMock(return_value=None)
        result = await tui.run()
        assert result == 0

    @pytest.mark.asyncio
    async def test_quit_on_quit_command(self):
        """TUI exits on 'quit' input."""
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tui._get_input = MagicMock(side_effect=["quit"])
        result = await tui.run()
        assert result == 0

    @pytest.mark.asyncio
    async def test_slash_quit(self):
        """TUI exits on /quit."""
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tui._get_input = MagicMock(side_effect=["/quit"])
        result = await tui.run()
        assert result == 0

    @pytest.mark.asyncio
    async def test_slash_help_then_quit(self):
        """Process /help then /quit."""
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tui._get_input = MagicMock(side_effect=["/help", "/quit"])
        result = await tui.run()
        assert result == 0

    @pytest.mark.asyncio
    async def test_empty_input_skipped(self):
        """Empty input should be skipped."""
        agent = _make_agent()
        tui = NeuralDebugTUI(agent)
        tui._get_input = MagicMock(side_effect=["", "", "/quit"])
        result = await tui.run()
        assert result == 0
        assert tui._turn_count == 0

    @pytest.mark.asyncio
    async def test_user_message_increments_turn(self):
        """Sending a message should increment the turn counter."""
        agent = _make_agent()
        agent.provider.queue(LLMResponse(text="Found the bug!", usage=TokenUsage()))
        tui = NeuralDebugTUI(agent)
        tui._get_input = MagicMock(side_effect=["debug main.py", "/quit"])
        result = await tui.run()
        assert result == 0
        assert tui._turn_count == 1

    @pytest.mark.asyncio
    async def test_error_handled_gracefully(self):
        """Errors during agent.run should be caught."""
        agent = _make_agent()
        agent.run = AsyncMock(side_effect=RuntimeError("API timeout"))
        tui = NeuralDebugTUI(agent)
        tui._get_input = MagicMock(side_effect=["debug main.py", "/quit"])
        result = await tui.run()
        assert result == 0
        assert tui._turn_count == 1


class TestCLIIntegration:
    """Test that cli.py correctly routes to TUI."""

    def test_chat_parser_has_plain_flag(self):
        from src.agent.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["chat", "--plain"])
        assert args.plain is True

    def test_chat_parser_plain_default_false(self):
        from src.agent.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["chat"])
        assert args.plain is False
