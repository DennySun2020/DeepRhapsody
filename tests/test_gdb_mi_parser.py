"""Tests for GdbMiParser — comprehensive edge cases and real GDB output."""

import pytest

from debug_common import GdbMiParser


class TestParseRecordRealOutputs:
    """Parse actual GDB/MI output lines from real debug sessions."""

    def test_breakpoint_created(self):
        line = '^done,bkpt={number="1",type="breakpoint",disp="keep",enabled="y",addr="0x00401234",func="main",file="main.c",fullname="/home/user/main.c",line="10",thread-groups=["i1"],times="0",original-location="main"}'
        rec = GdbMiParser.parse_record(line)
        assert rec["type"] == "result"
        assert rec["class_"] == "done"
        bkpt = rec["body"]["bkpt"]
        assert bkpt["func"] == "main"
        assert bkpt["line"] == "10"
        assert bkpt["file"] == "main.c"

    def test_stopped_at_breakpoint(self):
        line = '*stopped,reason="breakpoint-hit",disp="keep",bkptno="1",frame={addr="0x00401234",func="main",args=[],file="main.c",fullname="/home/user/main.c",line="10"},thread-id="1",stopped-threads="all"'
        rec = GdbMiParser.parse_record(line)
        assert rec["type"] == "exec"
        assert rec["class_"] == "stopped"
        assert rec["body"]["reason"] == "breakpoint-hit"
        frame = rec["body"]["frame"]
        assert frame["func"] == "main"

    def test_running(self):
        rec = GdbMiParser.parse_record("*running,thread-id=\"all\"")
        assert rec["type"] == "exec"
        assert rec["class_"] == "running"

    def test_error_result(self):
        rec = GdbMiParser.parse_record('^error,msg="No symbol table is loaded."')
        assert rec["type"] == "result"
        assert rec["class_"] == "error"
        assert "No symbol table" in rec["body"]["msg"]

    def test_thread_group_events(self):
        rec = GdbMiParser.parse_record('=thread-group-started,id="i1",pid="12345"')
        assert rec["type"] == "notify"
        assert rec["body"]["id"] == "i1"
        assert rec["body"]["pid"] == "12345"

    def test_target_output(self):
        rec = GdbMiParser.parse_record('@"Hello from the program\\n"')
        assert rec["type"] == "target"
        assert rec["body"] == "Hello from the program\n"


class TestParseValueEdgeCases:
    def test_empty_string(self):
        val, pos = GdbMiParser.parse_value('""', 0)
        assert val == ""
        assert pos == 2

    def test_string_with_backslash(self):
        val, _ = GdbMiParser.parse_value('"path\\\\to\\\\file"', 0)
        assert val == "path\\to\\file"

    def test_empty_tuple(self):
        val, _ = GdbMiParser.parse_value("{}", 0)
        assert val == {}

    def test_empty_list(self):
        val, _ = GdbMiParser.parse_value("[]", 0)
        assert val == []

    def test_bare_value(self):
        val, pos = GdbMiParser.parse_value("done,rest", 0)
        assert val == "done"
        assert pos == 4

    def test_beyond_end(self):
        val, pos = GdbMiParser.parse_value("", 0)
        assert val == ""

    def test_nested_lists(self):
        text = '[["a","b"],["c"]]'
        val, _ = GdbMiParser.parse_value(text, 0)
        assert isinstance(val, list)
        assert len(val) == 2


class TestParseTupleEdgeCases:
    def test_multiple_keys(self):
        text = '{a="1",b="2",c="3"}'
        val, _ = GdbMiParser.parse_tuple(text, 0)
        assert val == {"a": "1", "b": "2", "c": "3"}

    def test_nested_tuple(self):
        text = '{outer={inner="value"}}'
        val, _ = GdbMiParser.parse_tuple(text, 0)
        assert val["outer"]["inner"] == "value"

    def test_tuple_with_list_value(self):
        text = '{args=["one","two"]}'
        val, _ = GdbMiParser.parse_tuple(text, 0)
        assert val["args"] == ["one", "two"]


class TestParseListEdgeCases:
    def test_keyed_list(self):
        """GDB MI sometimes returns lists with key=value entries."""
        text = '[frame={level="0"},frame={level="1"}]'
        val, _ = GdbMiParser.parse_list(text, 0)
        assert len(val) == 2
        assert val[0]["frame"]["level"] == "0"

    def test_mixed_content(self):
        text = '["simple",key={nested="yes"}]'
        val, _ = GdbMiParser.parse_list(text, 0)
        assert len(val) == 2
        assert val[0] == "simple"


class TestParseMiStringEdgeCases:
    def test_unknown_escape(self):
        """Unknown escape sequences should pass through the character."""
        result = GdbMiParser.parse_mi_string(r"\a")
        assert result == "a"

    def test_empty_string(self):
        assert GdbMiParser.parse_mi_string("") == ""

    def test_no_escapes(self):
        assert GdbMiParser.parse_mi_string("plain text") == "plain text"

    def test_consecutive_escapes(self):
        result = GdbMiParser.parse_mi_string(r"\n\t\\")
        assert result == "\n\t\\"


class TestParseRecordEdgeCases:
    def test_whitespace_only(self):
        rec = GdbMiParser.parse_record("   ")
        # Whitespace-only may return None or an 'unknown' record — both are fine
        assert rec is None or rec.get("type") == "unknown"

    def test_unknown_indicator(self):
        rec = GdbMiParser.parse_record("!something")
        assert rec is not None
        assert rec["type"] == "unknown"

    def test_result_no_body(self):
        rec = GdbMiParser.parse_record("^done")
        assert rec["type"] == "result"
        assert rec["class_"] == "done"
        assert rec["body"] == {}

    def test_large_token(self):
        rec = GdbMiParser.parse_record("999999^done")
        assert rec["token"] == 999999
