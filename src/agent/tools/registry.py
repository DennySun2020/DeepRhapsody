"""Tool registry — discovers and manages tools for the agent runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..providers.base import ToolCall, ToolDefinition
from .base import Tool


class MCPToolWrapper(Tool):
    """Wraps an MCP tool definition + handler into the agent Tool interface."""

    def __init__(
        self,
        tool_def: Dict[str, Any],
        handler: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    ):
        self._def = tool_def
        self._handler = handler

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self._def["name"],
            description=self._def["description"],
            parameters=self._def.get("inputSchema", {}),
        )

    async def execute(self, arguments: Dict[str, Any]) -> str:
        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, self._handler, self._def["name"], arguments,
        )
        return json.dumps(result, indent=2)


class ToolRegistry:
    """Discovers and manages tools available to the agent."""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    @property
    def tools(self) -> List[Tool]:
        return list(self._tools.values())

    def get_definitions(self) -> List[ToolDefinition]:
        return [t.definition() for t in self._tools.values()]

    async def execute(self, tool_call: ToolCall) -> str:
        """Execute a tool call and return the string result."""
        tool = self._tools.get(tool_call.name)
        if not tool:
            return json.dumps({"status": "error", "message": f"Unknown tool: {tool_call.name}"})
        try:
            return await tool.execute(tool_call.arguments)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    def discover_debug_tools(self, scripts_dir: Optional[str] = None) -> None:
        """Auto-discover all NeuralDebug debug tools from the MCP server module.

        This imports the existing MCP server's TOOLS list and handle_tool_call
        function, wrapping each tool for use in the standalone agent.
        """
        # Resolve scripts directory
        repo_root = Path(__file__).resolve().parent.parent.parent.parent
        default_scripts = repo_root / "src" / "NeuralDebug"
        scripts_path = Path(scripts_dir) if scripts_dir else default_scripts

        # Ensure the integrations and NeuralDebug modules are importable
        integrations_path = str(repo_root / "integrations")
        src_path = str(scripts_path)
        for p in (integrations_path, src_path, str(repo_root)):
            if p not in sys.path:
                sys.path.insert(0, p)

        import os
        os.environ.setdefault("NeuralDebug_SCRIPTS", str(scripts_path))

        from mcp.server import TOOLS, handle_tool_call  # type: ignore[import-untyped]

        for tool_def in TOOLS:
            self.register(MCPToolWrapper(tool_def, handle_tool_call))

    def discover_hub_skills(self, skills_dir: str) -> None:
        """Load installed PilotHub skills as prompt-only tools."""
        skills_path = Path(skills_dir).expanduser()
        if not skills_path.is_dir():
            return

        from ..hub.skill_spec import load_skill_from_dir

        for child in sorted(skills_path.iterdir()):
            if child.is_dir():
                skill = load_skill_from_dir(child)
                if skill:
                    self.register(skill)
