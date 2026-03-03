"""Local skill registry — manages installed PilotHub skills."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .skill_spec import SkillMetadata, SkillSpec, load_skill_spec


class LocalRegistry:
    """Manages locally installed skills in the skills directory."""

    def __init__(self, skills_dir: Optional[str] = None):
        import os
        self._skills_dir = Path(
            skills_dir or os.environ.get("NeuralDebug_SKILLS_DIR", "~/.NeuralDebug/skills")
        ).expanduser()
        self._cache: Optional[Dict[str, SkillSpec]] = None

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    def _scan(self) -> Dict[str, SkillSpec]:
        """Scan the skills directory and load all valid skills."""
        specs: Dict[str, SkillSpec] = {}
        if not self._skills_dir.is_dir():
            return specs

        for child in sorted(self._skills_dir.iterdir()):
            if child.is_dir():
                spec = load_skill_spec(child / "SKILL.md")
                if spec:
                    specs[spec.metadata.name] = spec
        return specs

    def refresh(self) -> None:
        """Force rescan of the skills directory."""
        self._cache = None

    @property
    def skills(self) -> Dict[str, SkillSpec]:
        if self._cache is None:
            self._cache = self._scan()
        return self._cache

    def get(self, name: str) -> Optional[SkillSpec]:
        return self.skills.get(name)

    def list_skills(self) -> List[SkillMetadata]:
        return [spec.metadata for spec in self.skills.values()]

    def is_installed(self, name: str) -> bool:
        return name in self.skills

    def get_skill_prompt(self, name: str) -> Optional[str]:
        """Return the prompt body for a skill, or None if not installed."""
        spec = self.get(name)
        return spec.prompt if spec else None

    def get_all_prompts(self) -> Dict[str, str]:
        """Return a dict of skill_name -> prompt for all installed skills."""
        return {name: spec.prompt for name, spec in self.skills.items() if spec.prompt}
