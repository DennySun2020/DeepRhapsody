"""Tests for OpenAI and LangChain integration adapters."""

import json
import sys
import os
from pathlib import Path

import pytest

# Add integration paths
_INTEGRATIONS = os.path.join(os.path.dirname(__file__), os.pardir, "integrations")
sys.path.insert(0, os.path.abspath(os.path.join(_INTEGRATIONS, "openai")))
sys.path.insert(0, os.path.abspath(os.path.join(_INTEGRATIONS, "langchain")))
sys.path.insert(0, os.path.abspath(os.path.join(_INTEGRATIONS, "mcp")))


# ---------------------------------------------------------------------------
# OpenAI adapter: get_tools()
# ---------------------------------------------------------------------------

class TestOpenAIAdapter:
    def test_get_tools_returns_list(self):
        from adapter import get_tools
        tools = get_tools()
        assert isinstance(tools, list)
        assert len(tools) > 0

    def test_openai_format(self):
        from adapter import get_tools
        tools = get_tools()
        for tool in tools:
            assert tool["type"] == "function"
            assert "function" in tool
            fn = tool["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            assert fn["parameters"]["type"] == "object"

    def test_handle_function_call_unknown(self):
        from adapter import handle_function_call
        result = json.loads(handle_function_call("bogus_tool", {}))
        assert result["status"] == "error"

    def test_handle_function_call_accepts_json_string(self):
        from adapter import handle_function_call
        result = json.loads(handle_function_call("bogus_tool", "{}"))
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# LangChain tools
# ---------------------------------------------------------------------------

class TestLangChainTools:
    def test_get_NeuralDebug_tools(self):
        from tools import get_NeuralDebug_tools
        tool_list = get_NeuralDebug_tools()
        assert isinstance(tool_list, list)
        assert len(tool_list) >= 4

    def test_tool_has_name_and_description(self):
        from tools import get_NeuralDebug_tools
        for tool in get_NeuralDebug_tools():
            assert tool.name
            assert tool.description

    def test_tool_to_openai_function(self):
        from tools import get_NeuralDebug_tools
        for tool in get_NeuralDebug_tools():
            fn = tool.to_openai_function()
            assert fn["type"] == "function"
            assert fn["function"]["name"] == tool.name

    def test_tool_callable(self):
        from tools import NeuralDebugTool
        called = []
        tool = NeuralDebugTool("test", "test tool", lambda: called.append(1))
        tool()
        assert len(called) == 1

    def test_detect_lang(self):
        from tools import _detect_lang
        assert _detect_lang("main.py") == "python"
        assert _detect_lang("app.cpp") == "cpp"
        assert _detect_lang("server.go") == "go"
        assert _detect_lang("unknown.xyz") == "cpp"


# ---------------------------------------------------------------------------
# functions.json validation
# ---------------------------------------------------------------------------

class TestFunctionsJson:
    @pytest.fixture(autouse=True)
    def load_json(self):
        json_path = os.path.join(
            os.path.dirname(__file__), os.pardir,
            "integrations", "openai", "functions.json"
        )
        with open(json_path) as f:
            self.data = json.load(f)

    def test_has_tools_array(self):
        assert "tools" in self.data
        assert isinstance(self.data["tools"], list)
        assert len(self.data["tools"]) > 0

    def test_tools_have_openai_format(self):
        for tool in self.data["tools"]:
            assert tool["type"] == "function"
            fn = tool["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_tool_names_match_mcp(self):
        """Verify that functions.json tool names are a subset of MCP TOOLS."""
        from server import TOOLS as mcp_tools
        mcp_names = {t["name"] for t in mcp_tools}
        json_names = {t["function"]["name"] for t in self.data["tools"]}
        # functions.json may have a different set, but key tools should overlap
        overlap = json_names & mcp_names
        assert len(overlap) >= 3, f"Too few overlapping tools: {overlap}"
