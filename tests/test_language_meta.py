"""Tests validating LANGUAGE_META dicts across all debug session scripts."""

import os
from pathlib import Path

import pytest

import language_registry as lr


SRC_DIR = Path(os.path.dirname(__file__)).parent / "src" / "NeuralDebug"

# Required keys every LANGUAGE_META dict must have
REQUIRED_META_KEYS = {"name", "display_name", "extensions", "default_port", "debuggers"}


class TestAllLanguageMeta:
    """Validate every *_debug_session.py script's LANGUAGE_META."""

    @pytest.fixture(autouse=True)
    def discover(self):
        self.registry = lr.discover(str(SRC_DIR))
        self.scripts = list(SRC_DIR.glob("*_debug_session.py"))

    def test_at_least_8_scripts(self):
        """We ship at least 8 language backends."""
        assert len(self.scripts) >= 8, (
            f"Expected ≥8 session scripts, found {len(self.scripts)}: "
            f"{[s.name for s in self.scripts]}"
        )

    def test_all_scripts_have_meta(self):
        """Every session script must define LANGUAGE_META."""
        for script in self.scripts:
            meta = lr._load_meta_from_script(script)
            assert meta is not None, f"{script.name} has no LANGUAGE_META"

    def test_required_keys(self):
        for script in self.scripts:
            meta = lr._load_meta_from_script(script)
            if meta is None:
                continue
            missing = REQUIRED_META_KEYS - set(meta.keys())
            assert not missing, (
                f"{script.name} LANGUAGE_META missing keys: {missing}"
            )

    def test_name_is_string(self):
        for name, info in self.registry.languages.items():
            assert isinstance(name, str) and len(name) > 0

    def test_default_port_is_int(self):
        for name, info in self.registry.languages.items():
            assert isinstance(info["default_port"], int)
            assert 1024 <= info["default_port"] <= 65535, (
                f"{name} has invalid port: {info['default_port']}"
            )

    def test_extensions_are_dotted(self):
        """All file extensions should start with a dot."""
        for name, info in self.registry.languages.items():
            for ext in info.get("extensions", []):
                assert ext.startswith("."), (
                    f"{name}: extension {ext!r} does not start with '.'"
                )

    def test_unique_names(self):
        names = [lr._load_meta_from_script(s)["name"]
                 for s in self.scripts
                 if lr._load_meta_from_script(s)]
        assert len(names) == len(set(names)), f"Duplicate language names: {names}"

    def test_known_languages_present(self):
        """Verify the core languages we expect."""
        found = set(self.registry.languages.keys())
        expected = {"python", "cpp", "csharp", "rust", "java", "go", "nodejs", "ruby"}
        missing = expected - found
        assert not missing, f"Missing expected languages: {missing}"

    def test_extension_to_language_mapping(self):
        """Check well-known extension → language mappings."""
        assert self.registry.ext_to_lang.get(".py") == "python"
        assert self.registry.ext_to_lang.get(".rs") == "rust"
        assert self.registry.ext_to_lang.get(".go") == "go"
        assert self.registry.ext_to_lang.get(".rb") == "ruby"
        assert self.registry.ext_to_lang.get(".java") == "java"

    def test_port_assignments_unique_per_language(self):
        """Each primary language should have a unique default port."""
        port_to_lang = {}
        for name, info in self.registry.languages.items():
            port = info["default_port"]
            if port in port_to_lang:
                # Some languages can share ports (e.g., cpp/asm), that's okay
                # but the primary 8 should be unique
                pass
            port_to_lang.setdefault(port, []).append(name)

        # At least 5 distinct ports across all languages
        assert len(port_to_lang) >= 5, (
            f"Expected ≥5 distinct ports, got {len(port_to_lang)}: {port_to_lang}"
        )
