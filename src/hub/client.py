"""PilotHub API client — search, install, publish, update skills."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .skill_spec import SkillMetadata, load_skill_spec


@dataclass
class RemoteSkillInfo:
    """Summary info for a skill on PilotHub."""
    name: str
    description: str
    version: str
    author: str
    download_url: str = ""
    tags: List[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


class PilotHubClient:
    """Client for the PilotHub skill registry.

    Default registry URL can be overridden via PILOTHUB_URL env var.
    """

    def __init__(
        self,
        registry_url: Optional[str] = None,
        skills_dir: Optional[str] = None,
    ):
        import os
        self._registry_url = (
            registry_url
            or os.environ.get("PILOTHUB_URL", "https://pilothub.dev/api/v1")
        ).rstrip("/")
        self._skills_dir = Path(
            skills_dir or os.environ.get("NeuralDebug_SKILLS_DIR", "~/.NeuralDebug/skills")
        ).expanduser()

    @property
    def skills_dir(self) -> Path:
        return self._skills_dir

    async def search(self, query: str) -> List[RemoteSkillInfo]:
        """Search the PilotHub registry for skills matching *query*."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self._registry_url}/skills/search",
                    params={"q": query},
                )
                resp.raise_for_status()
                data = resp.json()
                return [
                    RemoteSkillInfo(
                        name=s.get("name", ""),
                        description=s.get("description", ""),
                        version=s.get("version", ""),
                        author=s.get("author", ""),
                        download_url=s.get("download_url", ""),
                        tags=s.get("tags", []),
                    )
                    for s in data.get("skills", [])
                ]
        except Exception:
            return []

    async def install(self, name: str, version: str = "latest") -> Optional[Path]:
        """Install a skill from PilotHub into the local skills directory."""
        import httpx
        self._skills_dir.mkdir(parents=True, exist_ok=True)

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{self._registry_url}/skills/{name}/download",
                    params={"version": version},
                )
                resp.raise_for_status()
                data = resp.json()

                skill_dir = self._skills_dir / name
                skill_dir.mkdir(parents=True, exist_ok=True)

                skill_md = skill_dir / "SKILL.md"
                skill_md.write_text(data.get("content", ""), encoding="utf-8")

                return skill_dir
        except Exception:
            return None

    async def publish(self, skill_dir: Path) -> Optional[str]:
        """Publish a local skill directory to PilotHub."""
        import httpx
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            return None

        spec = load_skill_spec(skill_md)
        if not spec:
            return None

        content = skill_md.read_text(encoding="utf-8")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self._registry_url}/skills/publish",
                    json={
                        "name": spec.metadata.name,
                        "description": spec.metadata.description,
                        "version": spec.metadata.version,
                        "author": spec.metadata.author,
                        "content": content,
                        "tags": spec.metadata.tags,
                    },
                )
                resp.raise_for_status()
                return resp.json().get("url", "published")
        except Exception:
            return None

    async def update(self, name: Optional[str] = None) -> List[str]:
        """Update installed skills. If *name* is None, update all."""
        updated: List[str] = []
        if name:
            result = await self.install(name)
            if result:
                updated.append(name)
        else:
            if self._skills_dir.is_dir():
                for child in self._skills_dir.iterdir():
                    if child.is_dir() and (child / "SKILL.md").is_file():
                        result = await self.install(child.name)
                        if result:
                            updated.append(child.name)
        return updated

    def list_installed(self) -> List[SkillMetadata]:
        """List locally installed skills."""
        skills: List[SkillMetadata] = []
        if not self._skills_dir.is_dir():
            return skills

        for child in sorted(self._skills_dir.iterdir()):
            if child.is_dir():
                spec = load_skill_spec(child / "SKILL.md")
                if spec:
                    skills.append(spec.metadata)
        return skills

    def uninstall(self, name: str) -> bool:
        """Remove a locally installed skill."""
        skill_dir = self._skills_dir / name
        if skill_dir.is_dir():
            shutil.rmtree(skill_dir)
            return True
        return False
