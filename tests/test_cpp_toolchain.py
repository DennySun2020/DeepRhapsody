"""Tests for C++ toolchain utilities (detect_build_system, find_binaries, scan_repo_context)."""

import os
import stat
import sys

import pytest

from cpp_common import detect_build_system, find_binaries, scan_repo_context


class TestDetectBuildSystem:
    def test_cmake(self, tmp_cmake_repo):
        result = detect_build_system(str(tmp_cmake_repo))
        assert result is not None
        assert result["name"] == "cmake"
        assert result["marker"] == "CMakeLists.txt"
        assert "cmake" in result["default_cmd"]

    def test_makefile(self, tmp_makefile_repo):
        result = detect_build_system(str(tmp_makefile_repo))
        assert result is not None
        assert result["name"] == "make"
        assert result["marker"] == "Makefile"

    def test_meson(self, tmp_repo):
        (tmp_repo / "meson.build").write_text("project('test', 'c')\n")
        result = detect_build_system(str(tmp_repo))
        assert result is not None
        assert result["name"] == "meson"

    def test_cargo(self, tmp_repo):
        (tmp_repo / "Cargo.toml").write_text("[package]\nname = 'test'\n")
        result = detect_build_system(str(tmp_repo))
        assert result is not None
        assert result["name"] == "cargo"

    def test_no_build_system(self, tmp_repo):
        result = detect_build_system(str(tmp_repo))
        assert result is None

    def test_priority_cmake_over_make(self, tmp_repo):
        """CMake should be detected before Make when both exist."""
        (tmp_repo / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\n")
        (tmp_repo / "Makefile").write_text("all:\n\techo hi\n")
        result = detect_build_system(str(tmp_repo))
        assert result["name"] == "cmake"


class TestFindBinaries:
    def _create_exe(self, path):
        """Create a fake executable file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * 1024)
        if sys.platform != "win32":
            path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def test_find_exe_files(self, tmp_path):
        if sys.platform == "win32":
            exe = tmp_path / "build" / "main.exe"
        else:
            exe = tmp_path / "build" / "main"
        self._create_exe(exe)
        results = find_binaries([str(tmp_path / "build")])
        assert len(results) >= 1
        names = [r["name"] for r in results]
        assert any("main" in n for n in names)

    def test_empty_dir(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        results = find_binaries([str(empty)])
        assert results == []

    def test_nonexistent_dir(self, tmp_path):
        results = find_binaries([str(tmp_path / "nope")])
        assert results == []

    def test_name_hint_filtering(self, tmp_path):
        if sys.platform == "win32":
            self._create_exe(tmp_path / "build" / "myapp.exe")
            self._create_exe(tmp_path / "build" / "other.exe")
        else:
            self._create_exe(tmp_path / "build" / "myapp")
            self._create_exe(tmp_path / "build" / "other")
        results = find_binaries([str(tmp_path / "build")], name_hint="myapp")
        # myapp should be sorted first
        if results:
            assert "myapp" in results[0]["name"]


class TestScanRepoContext:
    def test_basic_scan(self, tmp_cmake_repo):
        result = scan_repo_context(str(tmp_cmake_repo))
        assert result["repo_root"] == os.path.abspath(str(tmp_cmake_repo))
        assert result["build_system"] is not None
        assert result["build_system"]["name"] == "cmake"
        assert isinstance(result["doc_files"], list)
        assert isinstance(result["source_dirs"], list)

    def test_detects_test_dirs(self, tmp_cmake_repo):
        test_dir = tmp_cmake_repo / "tests"
        test_dir.mkdir()
        (test_dir / "test_main.cpp").write_text("int main() { return 0; }\n")
        result = scan_repo_context(str(tmp_cmake_repo))
        assert result["has_tests"] is True

    def test_detects_readme(self, tmp_cmake_repo):
        (tmp_cmake_repo / "README.md").write_text("# My Project\n")
        result = scan_repo_context(str(tmp_cmake_repo))
        assert any("README" in d for d in result["doc_files"])

    def test_no_build_system(self, tmp_repo):
        result = scan_repo_context(str(tmp_repo))
        assert result["build_system"] is None
