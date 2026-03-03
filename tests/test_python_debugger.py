"""Tests for Python debugger utilities (safe_repr, serialize_variable, is_user_frame)."""

import os

import pytest

from python_bdb import safe_repr, safe_str, serialize_variable, is_user_frame


class TestSafeRepr:
    def test_basic_types(self):
        assert safe_repr(42) == "42"
        assert safe_repr("hello") == "'hello'"
        assert safe_repr([1, 2, 3]) == "[1, 2, 3]"

    def test_truncation(self):
        long_str = "a" * 1000
        result = safe_repr(long_str, max_length=50)
        assert len(result) <= 50 + len("...<truncated>")
        assert result.endswith("...<truncated>")

    def test_no_truncation_under_limit(self):
        result = safe_repr("short", max_length=500)
        assert "truncated" not in result

    def test_repr_error(self):
        class BadRepr:
            def __repr__(self):
                raise ValueError("boom")
        result = safe_repr(BadRepr())
        assert "<repr error:" in result


class TestSafeStr:
    def test_basic(self):
        assert safe_str(42) == "42"
        assert safe_str("hello") == "hello"

    def test_truncation(self):
        result = safe_str("x" * 1000, max_length=100)
        assert result.endswith("...<truncated>")

    def test_str_error(self):
        class BadStr:
            def __str__(self):
                raise RuntimeError("bad")
        result = safe_str(BadStr())
        assert "<str error:" in result


class TestSerializeVariable:
    def test_int(self):
        result = serialize_variable("count", 42)
        assert result["type"] == "int"
        assert result["value"] == "42"
        assert result["repr"] == "42"

    def test_string(self):
        result = serialize_variable("name", "alice")
        assert result["type"] == "str"
        assert result["value"] == "alice"
        assert result["repr"] == "'alice'"

    def test_list(self):
        result = serialize_variable("items", [1, 2])
        assert result["type"] == "list"
        assert "1" in result["value"]

    def test_none(self):
        result = serialize_variable("x", None)
        assert result["type"] == "NoneType"
        assert result["value"] == "None"

    def test_dict(self):
        result = serialize_variable("d", {"a": 1})
        assert result["type"] == "dict"
        assert "a" in result["repr"]


class TestIsUserFrame:
    def test_target_file_match(self, tmp_path):
        target = str(tmp_path / "main.py")
        assert is_user_frame(target, target, str(tmp_path))

    def test_target_dir_match(self, tmp_path):
        target_file = str(tmp_path / "main.py")
        other_file = str(tmp_path / "helper.py")
        assert is_user_frame(other_file, target_file, str(tmp_path))

    def test_skip_bdb(self):
        assert not is_user_frame("bdb.py", "/some/main.py", "/some")

    def test_skip_threading(self):
        assert not is_user_frame("threading.py", "/some/main.py", "/some")

    def test_skip_python_debug_session(self):
        assert not is_user_frame(
            "python_debug_session.py", "/some/main.py", "/some"
        )

    def test_empty_filename(self):
        assert not is_user_frame("", "/some/main.py", "/some")
