"""Tests for the PilotHub skill specification and hub components."""

import json
import pytest
from pathlib import Path

from src.hub.skill_spec import (
    SkillMetadata,
    SkillSpec,
    SkillTool,
    parse_skill_frontmatter,
    load_skill_spec,
    load_skill_from_dir,
)
from src.hub.registry import LocalRegistry


SAMPLE_SKILL_MD = """\
---
name: memory-debugger
description: Debug memory leaks using Valgrind and AddressSanitizer
version: 1.0.0
author: johndoe
tags: [memory, c, cpp, debugging]
---

# Memory Debugger

When debugging memory issues, first run Valgrind to detect leaks:

```bash
valgrind --leak-check=full ./program
```

Then use AddressSanitizer for more detailed analysis.
"""


class TestParseFrontmatter:
    def test_basic(self):
        fm, body = parse_skill_frontmatter(SAMPLE_SKILL_MD)
        assert fm["name"] == "memory-debugger"
        assert fm["description"] == "Debug memory leaks using Valgrind and AddressSanitizer"
        assert fm["version"] == "1.0.0"
        assert fm["author"] == "johndoe"
        assert fm["tags"] == ["memory", "c", "cpp", "debugging"]
        assert "# Memory Debugger" in body

    def test_no_frontmatter(self):
        fm, body = parse_skill_frontmatter("Just plain text")
        assert fm == {}
        assert body == "Just plain text"

    def test_boolean_values(self):
        text = "---\nname: test\nenabled: true\ndisabled: false\n---\nbody"
        fm, body = parse_skill_frontmatter(text)
        assert fm["enabled"] is True
        assert fm["disabled"] is False


class TestLoadSkillSpec:
    def test_load(self, tmp_path):
        skill_md = tmp_path / "SKILL.md"
        skill_md.write_text(SAMPLE_SKILL_MD, encoding="utf-8")

        spec = load_skill_spec(skill_md)
        assert spec is not None
        assert spec.metadata.name == "memory-debugger"
        assert "Valgrind" in spec.prompt

    def test_missing_file(self, tmp_path):
        spec = load_skill_spec(tmp_path / "nonexistent.md")
        assert spec is None

    def test_no_name_uses_dir(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: test\n---\nbody", encoding="utf-8")
        spec = load_skill_spec(skill_md)
        assert spec.metadata.name == "my-skill"


class TestSkillTool:
    @pytest.mark.asyncio
    async def test_execute(self):
        spec = SkillSpec(
            metadata=SkillMetadata(name="test-skill", description="A test"),
            prompt="Use this approach for debugging.",
        )
        tool = SkillTool(spec)
        assert tool.name == "skill_test-skill"

        result = await tool.execute({"query": "how to debug?"})
        data = json.loads(result)
        assert data["skill"] == "test-skill"
        assert "debugging" in data["prompt"]
        assert data["query"] == "how to debug?"


class TestLoadSkillFromDir:
    def test_load(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(SAMPLE_SKILL_MD, encoding="utf-8")

        tool = load_skill_from_dir(skill_dir)
        assert tool is not None
        assert tool.name == "skill_memory-debugger"

    def test_no_skill_md(self, tmp_path):
        skill_dir = tmp_path / "empty"
        skill_dir.mkdir()
        assert load_skill_from_dir(skill_dir) is None


class TestLocalRegistry:
    def test_list_skills(self, tmp_path):
        # Create two skills
        for name in ("skill-a", "skill-b"):
            d = tmp_path / name
            d.mkdir()
            (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Skill {name}\n---\nPrompt for {name}", encoding="utf-8")

        registry = LocalRegistry(str(tmp_path))
        skills = registry.list_skills()
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert "skill-a" in names
        assert "skill-b" in names

    def test_get_skill_prompt(self, tmp_path):
        d = tmp_path / "my-skill"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: my-skill\n---\nDo this thing.", encoding="utf-8")

        registry = LocalRegistry(str(tmp_path))
        assert "Do this thing" in registry.get_skill_prompt("my-skill")
        assert registry.get_skill_prompt("nonexistent") is None

    def test_empty_dir(self, tmp_path):
        registry = LocalRegistry(str(tmp_path / "nonexistent"))
        assert registry.list_skills() == []

    def test_is_installed(self, tmp_path):
        d = tmp_path / "my-skill"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: my-skill\n---\ncontent", encoding="utf-8")

        registry = LocalRegistry(str(tmp_path))
        assert registry.is_installed("my-skill")
        assert not registry.is_installed("not-installed")

    def test_get_all_prompts(self, tmp_path):
        for name in ("a", "b"):
            d = tmp_path / name
            d.mkdir()
            (d / "SKILL.md").write_text(f"---\nname: {name}\n---\nPrompt {name}", encoding="utf-8")

        registry = LocalRegistry(str(tmp_path))
        prompts = registry.get_all_prompts()
        assert len(prompts) == 2
        assert "Prompt a" in prompts["a"]

    def test_refresh(self, tmp_path):
        registry = LocalRegistry(str(tmp_path))
        assert registry.list_skills() == []

        # Add a skill after first scan
        d = tmp_path / "new-skill"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: new-skill\n---\nNew", encoding="utf-8")

        # Still cached
        assert registry.list_skills() == []

        # After refresh
        registry.refresh()
        assert len(registry.list_skills()) == 1
