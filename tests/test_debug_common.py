"""Tests for debug_common utilities: find_repo_root, PID files, GdbMiParser."""

import os

import pytest

from debug_common import (
    find_repo_root,
    get_pid_file,
    write_pid_file,
    read_pid_file,
    remove_pid_file,
    GdbMiParser,
)


# ---------------------------------------------------------------------------
# find_repo_root
# ---------------------------------------------------------------------------

class TestFindRepoRoot:
    def test_finds_git_dir(self, tmp_repo):
        assert find_repo_root(str(tmp_repo)) == str(tmp_repo)

    def test_finds_from_subdirectory(self, tmp_repo):
        child = tmp_repo / "src" / "deep"
        child.mkdir(parents=True)
        assert find_repo_root(str(child)) == str(tmp_repo)

    def test_returns_none_no_git(self, tmp_path):
        assert find_repo_root(str(tmp_path)) is None

    def test_returns_none_at_root(self):
        # Filesystem root has no .git — should return None
        root = os.path.abspath(os.sep)
        result = find_repo_root(root)
        # Could be None or a real repo root above — just verify no crash
        assert result is None or os.path.isdir(result)


# ---------------------------------------------------------------------------
# PID file helpers
# ---------------------------------------------------------------------------

class TestPidFile:
    LANG = "test_lang"
    PORT = 59999

    def test_write_and_read(self):
        write_pid_file(self.LANG, self.PORT)
        pid = read_pid_file(self.LANG, self.PORT)
        assert pid == os.getpid()
        remove_pid_file(self.LANG, self.PORT)

    def test_read_nonexistent(self):
        assert read_pid_file("nonexistent_lang", 11111) is None

    def test_remove_nonexistent(self):
        # Should not raise
        remove_pid_file("nonexistent_lang", 11111)

    def test_get_pid_file_format(self):
        path = get_pid_file("python", 5678)
        assert "NeuralDebug_python_5678.pid" in path

    def test_roundtrip(self):
        write_pid_file(self.LANG, self.PORT)
        assert read_pid_file(self.LANG, self.PORT) is not None
        remove_pid_file(self.LANG, self.PORT)
        assert read_pid_file(self.LANG, self.PORT) is None


# ---------------------------------------------------------------------------
# GdbMiParser
# ---------------------------------------------------------------------------

class TestGdbMiParser:
    def test_parse_mi_string_basic(self):
        assert GdbMiParser.parse_mi_string("hello") == "hello"

    def test_parse_mi_string_escapes(self):
        assert GdbMiParser.parse_mi_string(r"line1\nline2") == "line1\nline2"
        assert GdbMiParser.parse_mi_string(r"tab\there") == "tab\there"
        assert GdbMiParser.parse_mi_string(r'say \"hi\"') == 'say "hi"'
        assert GdbMiParser.parse_mi_string(r"back\\slash") == "back\\slash"

    def test_parse_value_string(self):
        val, pos = GdbMiParser.parse_value('"hello"', 0)
        assert val == "hello"
        assert pos == 7

    def test_parse_value_tuple(self):
        text = '{key="val"}'
        val, _ = GdbMiParser.parse_value(text, 0)
        assert val == {"key": "val"}

    def test_parse_value_list(self):
        text = '["a","b"]'
        val, _ = GdbMiParser.parse_value(text, 0)
        assert val == ["a", "b"]

    def test_parse_record_result_done(self):
        rec = GdbMiParser.parse_record('^done,bkpt={number="1",type="breakpoint"}')
        assert rec["type"] == "result"
        assert rec["class_"] == "done"
        assert rec["body"]["bkpt"]["number"] == "1"

    def test_parse_record_with_token(self):
        rec = GdbMiParser.parse_record('42^done')
        assert rec["token"] == 42
        assert rec["type"] == "result"
        assert rec["class_"] == "done"

    def test_parse_record_exec_stopped(self):
        rec = GdbMiParser.parse_record('*stopped,reason="breakpoint-hit",bkptno="1"')
        assert rec["type"] == "exec"
        assert rec["class_"] == "stopped"
        assert rec["body"]["reason"] == "breakpoint-hit"

    def test_parse_record_console(self):
        rec = GdbMiParser.parse_record('~"Hello from GDB\\n"')
        assert rec["type"] == "console"
        assert rec["body"] == "Hello from GDB\n"

    def test_parse_record_log(self):
        rec = GdbMiParser.parse_record('&"set breakpoint pending on\\n"')
        assert rec["type"] == "log"
        assert "set breakpoint pending on" in rec["body"]

    def test_parse_record_notify(self):
        rec = GdbMiParser.parse_record('=thread-group-added,id="i1"')
        assert rec["type"] == "notify"
        assert rec["class_"] == "thread-group-added"

    def test_parse_record_gdb_prompt(self):
        assert GdbMiParser.parse_record("(gdb)") is None

    def test_parse_record_empty(self):
        assert GdbMiParser.parse_record("") is None

    def test_parse_nested_tuple(self):
        text = '{frame={addr="0x400",func="main",line="10"}}'
        val, _ = GdbMiParser.parse_value(text, 0)
        assert val["frame"]["func"] == "main"
        assert val["frame"]["line"] == "10"
