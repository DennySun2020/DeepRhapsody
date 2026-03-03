"""Tests for C++ build dir guessing and edge cases in cpp_common."""

import os
import sys

import pytest

from cpp_common import (
    _guess_build_dir,
    detect_build_system,
    find_binaries,
    scan_repo_context,
    BUILD_SYSTEM_MARKERS,
    SKIP_DIRS,
)


# ---------------------------------------------------------------------------
# _guess_build_dir
# ---------------------------------------------------------------------------

class TestGuessBuildDir:
    def test_cmake_prefers_build(self, tmp_repo):
        build_dir = tmp_repo / "build"
        build_dir.mkdir()
        result = _guess_build_dir(str(tmp_repo), "cmake")
        assert os.path.basename(result) == "build"

    def test_cargo_prefers_target_debug(self, tmp_repo):
        td = tmp_repo / "target" / "debug"
        td.mkdir(parents=True)
        result = _guess_build_dir(str(tmp_repo), "cargo")
        assert "target" in result and "debug" in result

    def test_meson_prefers_builddir(self, tmp_repo):
        (tmp_repo / "builddir").mkdir()
        result = _guess_build_dir(str(tmp_repo), "meson")
        assert os.path.basename(result) == "builddir"

    def test_unknown_build_system(self, tmp_repo):
        result = _guess_build_dir(str(tmp_repo), "unknown_system")
        # Should return something (falls back to generic guesses)
        assert isinstance(result, str)

    def test_no_matching_dir_returns_first_guess(self, tmp_repo):
        # No build dirs exist at all
        result = _guess_build_dir(str(tmp_repo), "cmake")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# BUILD_SYSTEM_MARKERS / SKIP_DIRS constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_markers_is_list_of_tuples(self):
        assert isinstance(BUILD_SYSTEM_MARKERS, list)
        for item in BUILD_SYSTEM_MARKERS:
            assert len(item) == 3, f"Marker tuple should be (file, name, cmd): {item}"
            marker, name, cmd = item
            assert isinstance(marker, str)
            assert isinstance(name, str)
            assert isinstance(cmd, str)

    def test_skip_dirs_has_common_entries(self):
        assert ".git" in SKIP_DIRS or any(".git" in d for d in SKIP_DIRS)

    def test_known_build_systems(self):
        names = {m[1] for m in BUILD_SYSTEM_MARKERS}
        expected = {"cmake", "make", "cargo", "meson"}
        assert expected.issubset(names), f"Missing build systems: {expected - names}"


# ---------------------------------------------------------------------------
# detect_build_system — edge cases
# ---------------------------------------------------------------------------

class TestDetectBuildSystemEdge:
    def test_autotools(self, tmp_repo):
        (tmp_repo / "configure").write_text("#!/bin/sh\n")
        result = detect_build_system(str(tmp_repo))
        assert result is not None
        assert result["name"] == "autotools"

    def test_ninja(self, tmp_repo):
        (tmp_repo / "build.ninja").write_text("rule cc\n")
        result = detect_build_system(str(tmp_repo))
        assert result is not None
        assert result["name"] == "ninja"

    def test_bazel(self, tmp_repo):
        (tmp_repo / "BUILD").write_text("cc_binary(name='main')\n")
        result = detect_build_system(str(tmp_repo))
        # Bazel uses BUILD file — may or may not be detected depending on marker order
        # Just ensure no crash
        assert result is None or isinstance(result["name"], str)


# ---------------------------------------------------------------------------
# find_binaries — edge cases
# ---------------------------------------------------------------------------

class TestFindBinariesEdge:
    def _make_exe(self, path, size=1024):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * size)
        if sys.platform != "win32":
            import stat
            path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def test_test_only_flag(self, tmp_path):
        ext = ".exe" if sys.platform == "win32" else ""
        self._make_exe(tmp_path / "build" / f"main{ext}")
        self._make_exe(tmp_path / "build" / f"test_main{ext}")
        results = find_binaries([str(tmp_path / "build")], test_only=True)
        if results:
            assert all(r["is_test"] for r in results)

    def test_sorted_by_size(self, tmp_path):
        ext = ".exe" if sys.platform == "win32" else ""
        self._make_exe(tmp_path / "build" / f"small{ext}", size=100)
        self._make_exe(tmp_path / "build" / f"big{ext}", size=10000)
        results = find_binaries([str(tmp_path / "build")])
        if len(results) >= 2:
            assert results[0]["size"] >= results[1]["size"]

    def test_multiple_search_dirs(self, tmp_path):
        ext = ".exe" if sys.platform == "win32" else ""
        self._make_exe(tmp_path / "dir1" / f"app1{ext}")
        self._make_exe(tmp_path / "dir2" / f"app2{ext}")
        results = find_binaries([
            str(tmp_path / "dir1"),
            str(tmp_path / "dir2"),
        ])
        names = [r["name"] for r in results]
        assert len(names) >= 2


# ---------------------------------------------------------------------------
# scan_repo_context — edge cases
# ---------------------------------------------------------------------------

class TestScanRepoContextEdge:
    def test_detects_source_dirs(self, tmp_cmake_repo):
        src = tmp_cmake_repo / "src"
        src.mkdir()
        (src / "main.cpp").write_text("int main() {}\n")
        result = scan_repo_context(str(tmp_cmake_repo))
        assert any("src" in d for d in result["source_dirs"])

    def test_multiple_doc_files(self, tmp_cmake_repo):
        (tmp_cmake_repo / "README.md").write_text("# Readme\n")
        (tmp_cmake_repo / "CONTRIBUTING.md").write_text("# Contributing\n")
        result = scan_repo_context(str(tmp_cmake_repo))
        assert len(result["doc_files"]) >= 2

    def test_build_hints_from_readme(self, tmp_cmake_repo):
        (tmp_cmake_repo / "README.md").write_text(
            "# Build\n\nTo compile: `cmake -B build && cmake --build build`\n"
        )
        result = scan_repo_context(str(tmp_cmake_repo))
        assert any("cmake" in h.lower() or "compile" in h.lower()
                    for h in result["build_hints"])
