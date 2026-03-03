#!/usr/bin/env python3
"""Auto-discovery language registry for NeuralDebug debug backends."""

import importlib.util
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional


class LanguageRegistry:
    def __init__(self):
        self.lang_scripts: Dict[str, str] = {}
        self.ext_to_lang: Dict[str, str] = {}
        self.default_ports: Dict[str, int] = {}
        self.languages: Dict[str, dict] = {}  # name -> full meta dict

    def register(self, script_filename: str, meta: dict):
        name = meta["name"]
        self.languages[name] = {**meta, "script": script_filename}
        self.lang_scripts[name] = script_filename

        for alias in meta.get("aliases", []):
            self.lang_scripts[alias] = script_filename
            self.default_ports[alias] = meta["default_port"]

        for ext in meta.get("extensions", []):
            self.ext_to_lang[ext] = name

        self.default_ports[name] = meta["default_port"]


def _load_meta_from_script(script_path: Path) -> Optional[dict]:
    try:
        spec = importlib.util.spec_from_file_location(
            f"_meta_{script_path.stem}", str(script_path),
            submodule_search_locations=[],
        )
        if spec is None or spec.loader is None:
            return None

        source = script_path.read_text(encoding="utf-8")

        import ast
        tree = ast.parse(source, filename=str(script_path))
        for node in ast.iter_child_nodes(tree):
            if (isinstance(node, ast.Assign)
                    and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "LANGUAGE_META"):
                return ast.literal_eval(node.value)
        return None
    except Exception:
        return None


def discover(scripts_dir: Optional[str] = None) -> LanguageRegistry:
    """Scan scripts_dir for debug session scripts and build a registry."""
    if scripts_dir is None:
        scripts_dir = str(Path(__file__).resolve().parent)

    registry = LanguageRegistry()
    scripts_path = Path(scripts_dir)

    for script in sorted(scripts_path.glob("*_debug_session.py")):
        meta = _load_meta_from_script(script)
        if meta and "name" in meta:
            registry.register(script.name, meta)

    return registry


_registry: Optional[LanguageRegistry] = None


def get_registry(scripts_dir: Optional[str] = None) -> LanguageRegistry:
    global _registry
    if _registry is None:
        _registry = discover(scripts_dir)
    return _registry
