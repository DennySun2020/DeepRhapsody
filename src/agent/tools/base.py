"""Abstract base class for agent tools."""

from __future__ import annotations

import abc
from typing import Any, Dict

from ..providers.base import ToolDefinition


class Tool(abc.ABC):
    """A single callable tool available to the agent."""

    @abc.abstractmethod
    def definition(self) -> ToolDefinition:
        """Return the tool schema for the LLM."""
        ...

    @abc.abstractmethod
    async def execute(self, arguments: Dict[str, Any]) -> str:
        """Execute the tool with the given arguments and return a string result."""
        ...

    @property
    def name(self) -> str:
        return self.definition().name
