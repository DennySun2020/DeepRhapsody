"""Tests for language_registry.py — auto-discovery of debug backends."""

import os
import sys

import pytest

import language_registry as lr


class TestLanguageRegistry:
    def test_register_basic(self):
        reg = lr.LanguageRegistry()
        meta = {
            "name": "python",
            "aliases": ["py"],
            "extensions": [".py"],
            "default_port": 5678,
        }
        reg.register("python_debug_session.py", meta)
        assert "python" in reg.lang_scripts
        assert "py" in reg.lang_scripts
        assert reg.ext_to_lang[".py"] == "python"
        assert reg.default_ports["python"] == 5678
        assert reg.default_ports["py"] == 5678

    def test_register_no_aliases(self):
        reg = lr.LanguageRegistry()
        meta = {"name": "rust", "extensions": [".rs"], "default_port": 5680}
        reg.register("rust_debug_session.py", meta)
        assert "rust" in reg.lang_scripts
        assert reg.ext_to_lang[".rs"] == "rust"

    def test_register_multiple_extensions(self):
        reg = lr.LanguageRegistry()
        meta = {
            "name": "cpp",
            "aliases": ["c", "c++"],
            "extensions": [".c", ".cpp", ".cc", ".h", ".hpp"],
            "default_port": 5678,
        }
        reg.register("cpp_debug_session.py", meta)
        for ext in [".c", ".cpp", ".cc", ".h", ".hpp"]:
            assert reg.ext_to_lang[ext] == "cpp"

    def test_languages_dict_has_script(self):
        reg = lr.LanguageRegistry()
        meta = {"name": "go", "aliases": [], "extensions": [".go"], "default_port": 5682}
        reg.register("go_debug_session.py", meta)
        assert reg.languages["go"]["script"] == "go_debug_session.py"
        assert reg.languages["go"]["default_port"] == 5682


class TestDiscover:
    def test_discover_finds_languages(self):
        """discover() on the actual src directory should find multiple languages."""
        src_dir = os.path.join(
            os.path.dirname(__file__), os.pardir, "src", "NeuralDebug"
        )
        registry = lr.discover(os.path.abspath(src_dir))
        # At minimum we expect python and cpp
        assert "python" in registry.lang_scripts or "Python" in registry.languages
        found_names = set(registry.languages.keys())
        assert len(found_names) >= 2, f"Expected ≥2 languages, got: {found_names}"

    def test_discover_empty_dir(self, tmp_path):
        """discover() on an empty dir returns an empty registry."""
        registry = lr.discover(str(tmp_path))
        assert len(registry.languages) == 0

    def test_discover_ignores_non_session_scripts(self, tmp_path):
        """Files that don't match *_debug_session.py are ignored."""
        (tmp_path / "helper.py").write_text("x = 1\n")
        (tmp_path / "utils_debug.py").write_text("x = 1\n")
        registry = lr.discover(str(tmp_path))
        assert len(registry.languages) == 0


class TestLoadMeta:
    def test_load_meta_valid(self, tmp_path):
        script = tmp_path / "fake_debug_session.py"
        script.write_text(
            'LANGUAGE_META = {\n'
            '    "name": "fake",\n'
            '    "aliases": ["fk"],\n'
            '    "extensions": [".fk"],\n'
            '    "default_port": 9999,\n'
            '}\n'
        )
        meta = lr._load_meta_from_script(script)
        assert meta is not None
        assert meta["name"] == "fake"
        assert meta["default_port"] == 9999

    def test_load_meta_no_meta_var(self, tmp_path):
        script = tmp_path / "novar_debug_session.py"
        script.write_text("x = 42\n")
        meta = lr._load_meta_from_script(script)
        assert meta is None

    def test_load_meta_syntax_error(self, tmp_path):
        script = tmp_path / "bad_debug_session.py"
        script.write_text("def broken(\n")
        meta = lr._load_meta_from_script(script)
        assert meta is None


class TestGetRegistry:
    def test_singleton_returns_same_instance(self):
        # Reset the global singleton
        lr._registry = None
        r1 = lr.get_registry()
        r2 = lr.get_registry()
        assert r1 is r2
        lr._registry = None  # clean up
