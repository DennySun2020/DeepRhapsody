"""Configuration system for the NeuralDebug standalone agent.

Loads settings from (in order of precedence):
1. CLI flags
2. Environment variables
3. Config file (~/.NeuralDebug/config.yaml or config.json)
4. Built-in defaults
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


_CONFIG_DIR = Path("~/.NeuralDebug").expanduser()
_CONFIG_YAML = _CONFIG_DIR / "config.yaml"
_CONFIG_JSON = _CONFIG_DIR / "config.json"


def _env_substitute(value: str) -> str:
    """Replace ${VAR} placeholders with environment variable values."""
    def _replace(m: re.Match) -> str:
        return os.environ.get(m.group(1), "")
    return re.sub(r"\$\{(\w+)\}", _replace, value)


def _load_yaml_simple(text: str) -> Dict[str, Any]:
    """Lightweight YAML-subset parser for flat config files.

    Handles key: value pairs, env var substitution, and simple types.
    For full YAML support, install PyYAML.
    """
    try:
        import yaml  # type: ignore[import-untyped]
        return yaml.safe_load(text) or {}
    except ImportError:
        pass

    result: Dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        value = _env_substitute(value)

        # Type coercion
        if value.lower() in ("true", "yes"):
            result[key] = True
        elif value.lower() in ("false", "no"):
            result[key] = False
        elif value.isdigit():
            result[key] = int(value)
        else:
            try:
                result[key] = float(value)
            except ValueError:
                result[key] = value.strip("'\"")

    return result


@dataclass
class AgentConfig:
    """Configuration for the NeuralDebug standalone agent."""

    # LLM provider
    provider: str = "copilot"
    model: str = ""
    api_key: str = ""
    base_url: str = ""

    # Agent behavior
    max_turns: int = 50
    temperature: float = 0.0
    max_tokens: Optional[int] = None
    system_prompt: Optional[str] = None

    # Hub settings
    hub_url: str = "https://pilothub.dev/api/v1"
    skills_dir: str = "~/.NeuralDebug/skills"

    # Debug scripts location
    scripts_dir: str = ""

    @classmethod
    def load(cls, cli_overrides: Optional[Dict[str, Any]] = None) -> "AgentConfig":
        """Load config with full precedence chain."""
        config = cls()

        # 1. Load from file
        config._apply_file()

        # 2. Apply environment variables
        config._apply_env()

        # 3. Apply CLI overrides
        if cli_overrides:
            config._apply_dict(cli_overrides)

        # 4. Resolve defaults based on provider
        config._resolve_defaults()

        return config

    def _apply_file(self) -> None:
        """Load settings from config file."""
        if _CONFIG_YAML.is_file():
            text = _CONFIG_YAML.read_text(encoding="utf-8")
            data = _load_yaml_simple(text)
            self._apply_dict(data)
        elif _CONFIG_JSON.is_file():
            text = _CONFIG_JSON.read_text(encoding="utf-8")
            data = json.loads(text)
            self._apply_dict(data)

    def _apply_env(self) -> None:
        """Apply environment variable overrides."""
        env_map = {
            "NeuralDebug_PROVIDER": "provider",
            "NeuralDebug_MODEL": "model",
            "NeuralDebug_API_KEY": "api_key",
            "NeuralDebug_BASE_URL": "base_url",
            "NeuralDebug_MAX_TURNS": "max_turns",
            "NeuralDebug_TEMPERATURE": "temperature",
            "NeuralDebug_SCRIPTS": "scripts_dir",
            "PILOTHUB_URL": "hub_url",
            "NeuralDebug_SKILLS_DIR": "skills_dir",
        }
        for env_var, attr in env_map.items():
            value = os.environ.get(env_var)
            if value is not None:
                current = getattr(self, attr)
                if isinstance(current, int):
                    setattr(self, attr, int(value))
                elif isinstance(current, float):
                    setattr(self, attr, float(value))
                else:
                    setattr(self, attr, value)

        # Also check provider-specific API key env vars
        if not self.api_key:
            provider_env = {
                "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "google": "GOOGLE_API_KEY",
                "openrouter": "OPENROUTER_API_KEY",
            }
            env_var = provider_env.get(self.provider)
            if env_var:
                self.api_key = os.environ.get(env_var, "")

    def _apply_dict(self, data: Dict[str, Any]) -> None:
        """Apply a dict of settings."""
        for key, value in data.items():
            if hasattr(self, key) and value is not None and value != "":
                setattr(self, key, value)

    def _resolve_defaults(self) -> None:
        """Fill in default model if not set."""
        if not self.model:
            defaults = {
                "copilot": "gpt-4o",
                "openai": "gpt-4o",
                "anthropic": "claude-sonnet-4-20250514",
                "google": "gemini-2.5-flash",
                "ollama": "llama3.1",
                "openrouter": "anthropic/claude-sonnet-4",
            }
            self.model = defaults.get(self.provider, "gpt-4o")

    def create_provider(self):
        """Create an LLMProvider instance from this config."""
        from .providers.openai_compat import OpenAIProvider
        from .providers.anthropic import AnthropicProvider
        from .providers.google import GoogleProvider
        from .providers.ollama import OllamaProvider
        from .providers.openrouter import OpenRouterProvider
        from .providers.copilot import CopilotProvider

        provider_map = {
            "copilot": lambda: CopilotProvider(
                model=self.model,
            ),
            "openai": lambda: OpenAIProvider(
                api_key=self.api_key or None,
                base_url=self.base_url or None,
                model=self.model,
            ),
            "anthropic": lambda: AnthropicProvider(
                api_key=self.api_key or None,
                base_url=self.base_url or None,
                model=self.model,
            ),
            "google": lambda: GoogleProvider(
                api_key=self.api_key or None,
                model=self.model,
            ),
            "ollama": lambda: OllamaProvider(
                base_url=self.base_url or None,
                model=self.model,
            ),
            "openrouter": lambda: OpenRouterProvider(
                api_key=self.api_key or None,
                model=self.model,
            ),
        }

        factory = provider_map.get(self.provider)
        if not factory:
            # Fall back to OpenAI-compatible with custom base_url
            return OpenAIProvider(
                api_key=self.api_key or None,
                base_url=self.base_url or None,
                model=self.model,
            )

        return factory()

    def save(self, path: Optional[Path] = None) -> Path:
        """Save current config to file."""
        target = path or _CONFIG_YAML
        target.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            f"provider: {self.provider}",
            f"model: {self.model}",
        ]
        if self.api_key:
            lines.append(f"api_key: {self.api_key}")
        if self.base_url:
            lines.append(f"base_url: {self.base_url}")
        lines.extend([
            f"max_turns: {self.max_turns}",
            f"temperature: {self.temperature}",
        ])

        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return target
