"""Abstract base classes and data types for LLM providers."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """A single tool invocation requested by the model."""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class TokenUsage:
    """Token usage statistics for a single LLM call."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""
    text: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    usage: Optional[TokenUsage] = None
    stop_reason: Optional[str] = None
    raw: Any = None  # provider-specific raw response

    def to_assistant_message(self) -> "Message":
        """Convert this response into a Message suitable for the conversation history."""
        content: Any = self.text or ""
        msg = Message(role="assistant", content=content)
        if self.tool_calls:
            msg.tool_calls = self.tool_calls
        return msg


@dataclass
class Message:
    """A single message in the conversation."""
    role: str  # "system", "user", "assistant", "tool"
    content: Any = ""
    tool_calls: Optional[List[ToolCall]] = None
    # For tool-result messages
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


@dataclass
class ToolDefinition:
    """Schema definition for a tool the LLM can call."""
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelInfo:
    """Metadata about an available model."""
    id: str
    name: str
    provider: str
    context_window: Optional[int] = None
    supports_tools: bool = True
    supports_vision: bool = False


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class LLMProvider(abc.ABC):
    """Abstract base class for LLM API providers.

    Each concrete provider translates the unified NeuralDebug message/tool
    format into the provider-specific wire format and back.
    """

    @abc.abstractmethod
    async def chat(
        self,
        messages: Sequence[Message],
        tools: Optional[Sequence[ToolDefinition]] = None,
        *,
        system: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """Send messages to the LLM and return its response.

        Parameters
        ----------
        messages:
            Conversation history (user / assistant / tool messages).
        tools:
            Tool definitions the model may invoke.
        system:
            System prompt (prepended or handled per provider convention).
        temperature:
            Sampling temperature.
        max_tokens:
            Maximum tokens in the response.
        """
        ...

    @abc.abstractmethod
    def list_models(self) -> List[ModelInfo]:
        """Return the models available from this provider."""
        ...

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. 'OpenAI', 'Anthropic')."""
        ...

    @property
    @abc.abstractmethod
    def default_model(self) -> str:
        """The recommended default model id for this provider."""
        ...
