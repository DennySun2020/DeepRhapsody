"""Skill specification format for PilotHub.

Skills are directories containing a SKILL.md file with YAML frontmatter
and a prompt body. This is the NeuralDebug equivalent of OpenClaw's skill format.

Example SKILL.md:
---
name: memory-debugger
description: Debug memory leaks using Valgrind and AddressSanitizer
version: 1.0.0
author: johndoe
requires:
  bins: [valgrind]
  platforms: [linux, darwin]
tags: [memory, c, cpp, debugging]
---

# Memory Debugger

When debugging memory issues, use the following approach...
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..agent.providers.base import ToolDefinition
from ..agent.tools.base import Tool


@dataclass
class SkillMetadata:
    """Parsed metadata from a SKILL.md frontmatter."""
    name: str
    description: str = ""
    version: str = "0.0.0"
    author: str = ""
    requires: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    homepage: str = ""


@dataclass
class SkillSpec:
    """A fully loaded skill: metadata + prompt content."""
    metadata: SkillMetadata
    prompt: str
    path: Optional[Path] = None


def parse_skill_frontmatter(text: str) -> tuple[Dict[str, Any], str]:
    """Parse YAML-like frontmatter from a SKILL.md file.

    Returns (frontmatter_dict, body_text).
    Uses a lightweight parser to avoid requiring PyYAML for basic skill loading.
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not match:
        return {}, text

    fm_text = match.group(1)
    body = match.group(2)

    fm: Dict[str, Any] = {}
    for line in fm_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # Handle simple lists: [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
            fm[key] = items
        # Handle nested dicts (one level)
        elif not value:
            nested: Dict[str, Any] = {}
            # peek ahead — not implemented in simple parser; store as empty
            fm[key] = nested
        # Handle booleans
        elif value.lower() in ("true", "yes"):
            fm[key] = True
        elif value.lower() in ("false", "no"):
            fm[key] = False
        else:
            fm[key] = value.strip("'\"")

    return fm, body


def load_skill_spec(path: Path) -> Optional[SkillSpec]:
    """Load a SkillSpec from a SKILL.md file."""
    if not path.is_file():
        return None

    text = path.read_text(encoding="utf-8")
    fm, body = parse_skill_frontmatter(text)

    name = fm.get("name")
    if not name:
        # Use filename/directory as fallback name
        name = path.parent.name if path.name == "SKILL.md" else path.stem

    metadata = SkillMetadata(
        name=name,
        description=fm.get("description", ""),
        version=fm.get("version", "0.0.0"),
        author=fm.get("author", ""),
        requires=fm.get("requires", {}),
        tags=fm.get("tags", []),
        homepage=fm.get("homepage", ""),
    )

    return SkillSpec(metadata=metadata, prompt=body.strip(), path=path)


class SkillTool(Tool):
    """A PilotHub skill exposed as an agent tool.

    Skills are prompt-based: when invoked, they inject their prompt
    content into the conversation context.
    """

    def __init__(self, spec: SkillSpec):
        self._spec = spec

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=f"skill_{self._spec.metadata.name}",
            description=self._spec.metadata.description or f"Activate the {self._spec.metadata.name} skill",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional context or question for the skill",
                    }
                },
            },
        )

    async def execute(self, arguments: Dict[str, Any]) -> str:
        query = arguments.get("query", "")
        result = {
            "skill": self._spec.metadata.name,
            "prompt": self._spec.prompt,
        }
        if query:
            result["query"] = query
        return json.dumps(result, indent=2)


def load_skill_from_dir(skill_dir: Path) -> Optional[SkillTool]:
    """Load a skill from a directory containing SKILL.md."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None

    spec = load_skill_spec(skill_md)
    if not spec:
        return None

    return SkillTool(spec)
