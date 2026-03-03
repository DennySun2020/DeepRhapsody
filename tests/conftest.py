"""Shared pytest fixtures for NeuralDebug tests."""

import os
import sys

import pytest

# Add source root so tests can import debug_common, language_registry, etc.
# The debug session scripts use bare imports (e.g. "from debug_common import …").
SRC_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "src", "NeuralDebug")
sys.path.insert(0, os.path.abspath(SRC_DIR))

DEBUGGERS_DIR = os.path.join(SRC_DIR, "debuggers")
sys.path.insert(0, os.path.abspath(DEBUGGERS_DIR))


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a minimal fake Git repo with a .git directory."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    return tmp_path


@pytest.fixture
def tmp_cmake_repo(tmp_repo):
    """A fake repo with a CMakeLists.txt."""
    (tmp_repo / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)\n")
    return tmp_repo


@pytest.fixture
def tmp_makefile_repo(tmp_repo):
    """A fake repo with a Makefile."""
    (tmp_repo / "Makefile").write_text("all:\n\tgcc -o main main.c\n")
    return tmp_repo
