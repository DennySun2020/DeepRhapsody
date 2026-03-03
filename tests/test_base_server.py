"""Tests for BaseDebugServer logic (no live debugger needed)."""

import pytest

from debug_common import BaseDebugServer, _BASE_COMMANDS, DebugResponseMixin


class _FakeDebugger:
    """Minimal stand-in for a language debugger — just enough for BaseDebugServer."""

    def __init__(self, **attrs):
        for k, v in attrs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# _BASE_COMMANDS
# ---------------------------------------------------------------------------

class TestBaseCommands:
    def test_required_commands_present(self):
        for cmd in ["start", "continue", "step_in", "step_over", "step_out",
                     "set_breakpoint", "remove_breakpoint", "breakpoints",
                     "inspect", "evaluate", "list", "backtrace", "ping", "quit"]:
            assert cmd in _BASE_COMMANDS, f"{cmd!r} missing from _BASE_COMMANDS"

    def test_no_duplicates(self):
        assert len(_BASE_COMMANDS) == len(set(_BASE_COMMANDS))


# ---------------------------------------------------------------------------
# BaseDebugServer._available_commands
# ---------------------------------------------------------------------------

class TestAvailableCommands:
    def test_with_run_to_line(self):
        server = BaseDebugServer(_FakeDebugger(), port=9999)
        server.HAS_RUN_TO_LINE = True
        cmds = server._available_commands()
        assert "run_to_line" in cmds
        # run_to_line should appear before set_breakpoint
        assert cmds.index("run_to_line") < cmds.index("set_breakpoint")

    def test_without_run_to_line(self):
        server = BaseDebugServer(_FakeDebugger(), port=9999)
        server.HAS_RUN_TO_LINE = False
        cmds = server._available_commands()
        assert "run_to_line" not in cmds

    def test_returns_new_list(self):
        """_available_commands should return a fresh list each time."""
        server = BaseDebugServer(_FakeDebugger(), port=9999)
        a = server._available_commands()
        b = server._available_commands()
        assert a == b
        assert a is not b  # not the same object


# ---------------------------------------------------------------------------
# BaseDebugServer._get_target_label
# ---------------------------------------------------------------------------

class TestGetTargetLabel:
    def test_target_attr(self):
        server = BaseDebugServer(_FakeDebugger(target="/usr/bin/myapp"), port=9999)
        assert server._get_target_label() == "/usr/bin/myapp"

    def test_executable_attr(self):
        server = BaseDebugServer(_FakeDebugger(executable="a.out"), port=9999)
        assert server._get_target_label() == "a.out"

    def test_script_file_attr(self):
        server = BaseDebugServer(_FakeDebugger(script_file="main.py"), port=9999)
        assert server._get_target_label() == "main.py"

    def test_priority_order(self):
        """'target' takes precedence over 'executable'."""
        server = BaseDebugServer(
            _FakeDebugger(target="first", executable="second"), port=9999
        )
        assert server._get_target_label() == "first"

    def test_fallback_question_mark(self):
        server = BaseDebugServer(_FakeDebugger(), port=9999)
        assert server._get_target_label() == "?"

    def test_main_class(self):
        server = BaseDebugServer(
            _FakeDebugger(main_class="com.example.Main"), port=9999
        )
        assert server._get_target_label() == "com.example.Main"


# ---------------------------------------------------------------------------
# BaseDebugServer._dispatch_extra / _pre_start_dispatch
# ---------------------------------------------------------------------------

class TestDispatchHooks:
    def test_dispatch_extra_returns_none(self):
        server = BaseDebugServer(_FakeDebugger(), port=9999)
        assert server._dispatch_extra("custom", "args") is None

    def test_pre_start_dispatch_returns_none(self):
        server = BaseDebugServer(_FakeDebugger(), port=9999)
        assert server._pre_start_dispatch("info", "") is None


# ---------------------------------------------------------------------------
# BaseDebugServer.__init__
# ---------------------------------------------------------------------------

class TestBaseDebugServerInit:
    def test_init_attributes(self):
        dbg = _FakeDebugger()
        server = BaseDebugServer(dbg, port=5678)
        assert server.debugger is dbg
        assert server.port == 5678
        assert server.running is False

    def test_class_defaults(self):
        assert BaseDebugServer.LANGUAGE == "Generic"
        assert BaseDebugServer.HAS_RUN_TO_LINE is True
