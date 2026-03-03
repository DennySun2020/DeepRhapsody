"""Tests for MCP server utilities (_detect_language, _get_script, TOOLS, _resolve_lang_and_port)."""

import sys
import os
from pathlib import Path

import pytest

# Add integration paths so we can import the MCP module
_INTEGRATIONS = os.path.join(os.path.dirname(__file__), os.pardir, "integrations")
sys.path.insert(0, os.path.abspath(os.path.join(_INTEGRATIONS, "mcp")))

from server import (
    _detect_language,
    _get_script,
    _resolve_lang_and_port,
    TOOLS,
    SCRIPTS_DIR,
    EXT_TO_LANG,
    DEFAULT_PORTS,
    LANG_SCRIPTS,
    _active_sessions,
    handle_tool_call,
)


# ---------------------------------------------------------------------------
# _detect_language
# ---------------------------------------------------------------------------

class TestDetectLanguage:
    def test_python(self):
        assert _detect_language("main.py") == "python"

    def test_c(self):
        assert _detect_language("server.c") == "cpp"

    def test_cpp(self):
        assert _detect_language("app.cpp") == "cpp"

    def test_csharp(self):
        assert _detect_language("App.cs") == "csharp"

    def test_rust(self):
        assert _detect_language("main.rs") == "rust"

    def test_java(self):
        assert _detect_language("Main.java") == "java"

    def test_go(self):
        assert _detect_language("main.go") == "go"

    def test_javascript(self):
        assert _detect_language("index.js") == "nodejs"

    def test_typescript(self):
        assert _detect_language("app.ts") == "nodejs"

    def test_ruby(self):
        assert _detect_language("script.rb") == "ruby"

    def test_unknown_defaults_to_cpp(self):
        assert _detect_language("data.txt") == "cpp"

    def test_exe_extension(self):
        assert _detect_language("myapp.exe") in ("cpp", "asm", "re")

    def test_full_path(self):
        assert _detect_language("/home/user/project/test.py") == "python"


# ---------------------------------------------------------------------------
# _get_script
# ---------------------------------------------------------------------------

class TestGetScript:
    def test_python_script(self):
        script = _get_script("python")
        assert script.name == "python_debug_session.py"
        assert script.parent == SCRIPTS_DIR

    def test_cpp_script(self):
        script = _get_script("cpp")
        assert script.name == "cpp_debug_session.py"

    def test_unknown_falls_back_to_cpp(self):
        script = _get_script("unknown_lang")
        assert script.name == "cpp_debug_session.py"


# ---------------------------------------------------------------------------
# _resolve_lang_and_port
# ---------------------------------------------------------------------------

class TestResolveLangAndPort:
    def setup_method(self):
        _active_sessions.clear()

    def teardown_method(self):
        _active_sessions.clear()

    def test_explicit_language_and_port(self):
        lang, port = _resolve_lang_and_port({"language": "python", "port": 9999})
        assert lang == "python"
        assert port == 9999

    def test_detect_from_target(self):
        lang, port = _resolve_lang_and_port({"target": "main.py"})
        assert lang == "python"
        assert port == DEFAULT_PORTS.get("python", 5678)

    def test_active_session_lookup(self):
        _active_sessions[7777] = "go"
        lang, port = _resolve_lang_and_port({"port": 7777})
        assert lang == "go"
        assert port == 7777

    def test_default_cpp(self):
        lang, port = _resolve_lang_and_port({})
        assert lang == "cpp"
        assert port == DEFAULT_PORTS.get("cpp", 5678)


# ---------------------------------------------------------------------------
# TOOLS structure validation
# ---------------------------------------------------------------------------

class TestToolsSchema:
    REQUIRED_FIELDS = {"name", "description", "inputSchema"}

    def test_tools_list_not_empty(self):
        assert len(TOOLS) > 0

    def test_all_tools_have_required_fields(self):
        for tool in TOOLS:
            missing = self.REQUIRED_FIELDS - set(tool.keys())
            assert not missing, f"Tool {tool.get('name', '?')} missing: {missing}"

    def test_input_schema_is_object(self):
        for tool in TOOLS:
            schema = tool["inputSchema"]
            assert schema.get("type") == "object"
            assert "properties" in schema

    def test_tool_names_unique(self):
        names = [t["name"] for t in TOOLS]
        assert len(names) == len(set(names)), "Duplicate tool names"

    def test_expected_tools_present(self):
        names = {t["name"] for t in TOOLS}
        expected = {
            "NeuralDebug_info",
            "NeuralDebug_start_server",
            "NeuralDebug_status",
            "NeuralDebug_step",
            "NeuralDebug_continue",
            "NeuralDebug_inspect",
            "NeuralDebug_evaluate",
            "NeuralDebug_backtrace",
            "NeuralDebug_stop",
        }
        for e in expected:
            assert e in names, f"Missing tool: {e}"


# ---------------------------------------------------------------------------
# handle_tool_call — unknown tool
# ---------------------------------------------------------------------------

class TestHandleToolCall:
    def test_unknown_tool(self):
        result = handle_tool_call("nonexistent_tool", {})
        assert result["status"] == "error"
        assert "Unknown tool" in result["message"]
