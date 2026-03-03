"""Core agentic loop for the NeuralDebug standalone agent.

Implements a think → tool-call → observe cycle that drives debugging
sessions autonomously while keeping the user informed.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Callable, Dict, List, Optional

from .config import AgentConfig
from .providers.base import LLMProvider, LLMResponse, Message, ToolCall, TokenUsage
from .system_prompt import build_system_prompt
from .tools.registry import ToolRegistry


class AgentRunner:
    """Standalone agentic loop for NeuralDebug.

    Manages the conversation, tool execution, and iteration until the
    model produces a final text response or the turn limit is reached.
    """

    def __init__(
        self,
        provider: LLMProvider,
        tools: ToolRegistry,
        config: AgentConfig,
        *,
        system_prompt: Optional[str] = None,
        on_tool_call: Optional[Callable[[ToolCall], None]] = None,
        on_tool_result: Optional[Callable[[ToolCall, str], None]] = None,
        on_response: Optional[Callable[[str], None]] = None,
    ):
        self.provider = provider
        self.tools = tools
        self.config = config
        self.messages: List[Message] = []
        self.total_usage = TokenUsage()
        self._system_prompt = system_prompt or ""
        self._on_tool_call = on_tool_call
        self._on_tool_result = on_tool_result
        self._on_response = on_response

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def _accumulate_usage(self, usage: Optional[TokenUsage]) -> None:
        if usage:
            self.total_usage.prompt_tokens += usage.prompt_tokens
            self.total_usage.completion_tokens += usage.completion_tokens
            self.total_usage.total_tokens += usage.total_tokens

    async def run(self, user_message: str) -> str:
        """Run one conversational turn: user message → (tool loop) → response.

        Returns the final assistant text reply.
        """
        self.messages.append(Message(role="user", content=user_message))

        for turn in range(self.config.max_turns):
            response = await self.provider.chat(
                messages=self.messages,
                tools=self.tools.get_definitions() or None,
                system=self._system_prompt,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            self._accumulate_usage(response.usage)

            if response.tool_calls:
                # Add assistant message with tool calls
                self.messages.append(response.to_assistant_message())

                # Execute each tool call
                for tc in response.tool_calls:
                    if self._on_tool_call:
                        self._on_tool_call(tc)

                    result = await self.tools.execute(tc)

                    if self._on_tool_result:
                        self._on_tool_result(tc, result)

                    self.messages.append(Message(
                        role="tool",
                        content=result,
                        tool_call_id=tc.id,
                        name=tc.name,
                    ))

                continue  # Loop back for the model to process results

            # No tool calls — this is the final text response
            text = response.text or ""
            self.messages.append(Message(role="assistant", content=text))

            if self._on_response:
                self._on_response(text)

            return text

        return "[NeuralDebug] Reached maximum tool-use iterations. Please refine your request."

    def reset(self) -> None:
        """Clear conversation history and usage counters."""
        self.messages.clear()
        self.total_usage = TokenUsage()


async def create_agent(
    config: Optional[AgentConfig] = None,
    cli_overrides: Optional[Dict[str, Any]] = None,
) -> AgentRunner:
    """High-level factory: create a ready-to-use AgentRunner.

    Handles config loading, provider creation, tool discovery, and
    system prompt assembly.
    """
    cfg = config or AgentConfig.load(cli_overrides)

    # Create LLM provider
    provider = cfg.create_provider()

    # Discover tools
    tools = ToolRegistry()
    tools.discover_debug_tools(cfg.scripts_dir or None)

    # Load hub skills if available
    try:
        tools.discover_hub_skills(cfg.skills_dir)
    except Exception:
        pass  # Hub skills are optional

    # Build system prompt
    skill_prompts: Optional[Dict[str, str]] = None
    try:
        from ..hub.registry import LocalRegistry
        registry = LocalRegistry(cfg.skills_dir)
        skill_prompts = registry.get_all_prompts() or None
    except Exception:
        pass

    system_prompt = build_system_prompt(
        tools=tools.get_definitions(),
        extra_context=cfg.system_prompt,
        skill_prompts=skill_prompts,
    )

    return AgentRunner(
        provider=provider,
        tools=tools,
        config=cfg,
        system_prompt=system_prompt,
    )
