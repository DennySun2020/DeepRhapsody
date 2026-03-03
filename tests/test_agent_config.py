"""Tests for the agent configuration system."""

import json
import os
import pytest
from pathlib import Path

from src.agent.config import AgentConfig, _load_yaml_simple, _env_substitute


class TestEnvSubstitute:
    def test_simple(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret123")
        assert _env_substitute("key=${MY_KEY}") == "key=secret123"

    def test_missing_var(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        assert _env_substitute("key=${MISSING_VAR}") == "key="

    def test_no_vars(self):
        assert _env_substitute("plain text") == "plain text"


class TestYamlSimpleParser:
    def test_basic_types(self):
        text = """
provider: openai
model: gpt-4o
max_turns: 50
temperature: 0.5
verbose: true
disabled: false
"""
        result = _load_yaml_simple(text)
        assert result["provider"] == "openai"
        assert result["model"] == "gpt-4o"
        assert result["max_turns"] == 50
        assert result["temperature"] == 0.5
        assert result["verbose"] is True
        assert result["disabled"] is False

    def test_comments_and_blanks(self):
        text = """
# This is a comment
provider: openai

model: gpt-4o
"""
        result = _load_yaml_simple(text)
        assert result["provider"] == "openai"
        assert result["model"] == "gpt-4o"


class TestAgentConfig:
    def test_defaults(self):
        config = AgentConfig()
        assert config.provider == "copilot"
        assert config.max_turns == 50
        assert config.temperature == 0.0

    def test_resolve_defaults(self):
        config = AgentConfig(provider="anthropic")
        config._resolve_defaults()
        assert "claude" in config.model

    def test_apply_dict(self):
        config = AgentConfig()
        config._apply_dict({"provider": "google", "model": "gemini-2.5-flash", "max_turns": 100})
        assert config.provider == "google"
        assert config.model == "gemini-2.5-flash"
        assert config.max_turns == 100

    def test_apply_env(self, monkeypatch):
        monkeypatch.setenv("NeuralDebug_PROVIDER", "anthropic")
        monkeypatch.setenv("NeuralDebug_MODEL", "claude-opus-4")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        config = AgentConfig()
        config._apply_env()
        assert config.provider == "anthropic"
        assert config.model == "claude-opus-4"
        assert config.api_key == "sk-test-key"

    def test_create_provider_openai(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test")
        config = AgentConfig(provider="openai", model="gpt-4o", api_key="test")
        provider = config.create_provider()
        assert provider.name == "OpenAI"

    def test_create_provider_anthropic(self):
        config = AgentConfig(provider="anthropic", model="claude-sonnet-4-20250514", api_key="test")
        provider = config.create_provider()
        assert provider.name == "Anthropic"

    def test_create_provider_google(self):
        config = AgentConfig(provider="google", model="gemini-2.5-flash", api_key="test")
        provider = config.create_provider()
        assert provider.name == "Google"

    def test_create_provider_ollama(self):
        config = AgentConfig(provider="ollama", model="llama3.1")
        provider = config.create_provider()
        assert provider.name == "Ollama"

    def test_create_provider_openrouter(self):
        config = AgentConfig(provider="openrouter", model="anthropic/claude-sonnet-4", api_key="test")
        provider = config.create_provider()
        assert provider.name == "OpenRouter"

    def test_create_provider_unknown_fallback(self):
        config = AgentConfig(provider="custom-provider", model="custom-model", api_key="test", base_url="https://custom.api/v1")
        provider = config.create_provider()
        assert provider.name == "OpenAI"  # Falls back to OpenAI-compatible

    def test_save_and_load(self, tmp_path):
        config = AgentConfig(provider="google", model="gemini-2.5-flash", api_key="test-key")
        saved = config.save(tmp_path / "config.yaml")
        assert saved.is_file()
        text = saved.read_text()
        assert "google" in text
        assert "gemini" in text

    def test_load_precedence(self, tmp_path, monkeypatch):
        """CLI overrides > env vars > file > defaults."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("provider: google\nmodel: gemini-2.5-flash\n")
        monkeypatch.setenv("NeuralDebug_MODEL", "gemini-2.5-pro")

        config = AgentConfig()
        # Simulate file load
        config._apply_dict({"provider": "google", "model": "gemini-2.5-flash"})
        config._apply_env()
        config._apply_dict({"model": "custom-override"})  # CLI
        assert config.model == "custom-override"
