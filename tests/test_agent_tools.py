"""Tests for the tool registry."""

import json
import pytest

from src.agent.providers.base import ToolCall, ToolDefinition
from src.agent.tools.base import Tool
from src.agent.tools.registry import ToolRegistry, MCPToolWrapper


class DummyTool(Tool):
    """A simple test tool."""

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="dummy_tool",
            description="A dummy tool for testing",
            parameters={"type": "object", "properties": {"x": {"type": "integer"}}},
        )

    async def execute(self, arguments):
        return json.dumps({"result": arguments.get("x", 0) * 2})


class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = DummyTool()
        registry.register(tool)
        assert registry.get("dummy_tool") is tool
        assert registry.get("nonexistent") is None

    def test_get_definitions(self):
        registry = ToolRegistry()
        registry.register(DummyTool())
        defs = registry.get_definitions()
        assert len(defs) == 1
        assert defs[0].name == "dummy_tool"

    @pytest.mark.asyncio
    async def test_execute(self):
        registry = ToolRegistry()
        registry.register(DummyTool())
        tc = ToolCall(id="call_1", name="dummy_tool", arguments={"x": 5})
        result = await registry.execute(tc)
        data = json.loads(result)
        assert data["result"] == 10

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        tc = ToolCall(id="call_1", name="missing", arguments={})
        result = await registry.execute(tc)
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Unknown tool" in data["message"]

    def test_tools_property(self):
        registry = ToolRegistry()
        registry.register(DummyTool())
        assert len(registry.tools) == 1


class TestMCPToolWrapper:
    def test_definition(self):
        tool_def = {
            "name": "NeuralDebug_info",
            "description": "Get info",
            "inputSchema": {"type": "object", "properties": {}},
        }
        wrapper = MCPToolWrapper(tool_def, lambda name, args: {"status": "ok"})
        defn = wrapper.definition()
        assert defn.name == "NeuralDebug_info"
        assert defn.description == "Get info"

    @pytest.mark.asyncio
    async def test_execute(self):
        tool_def = {"name": "test", "description": "test"}

        def handler(name, args):
            return {"status": "ok", "name": name, "args": args}

        wrapper = MCPToolWrapper(tool_def, handler)
        result = await wrapper.execute({"language": "python"})
        data = json.loads(result)
        assert data["status"] == "ok"
        assert data["name"] == "test"
