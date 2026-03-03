"""Tests for the CLI argument parser and sync commands."""

import pytest
from src.agent.cli import build_parser


class TestBuildParser:
    def test_chat_command(self):
        parser = build_parser()
        args = parser.parse_args(["chat", "--provider", "anthropic", "--model", "claude-sonnet-4-20250514"])
        assert args.command == "chat"
        assert args.provider == "anthropic"
        assert args.model == "claude-sonnet-4-20250514"

    def test_run_command(self):
        parser = build_parser()
        args = parser.parse_args(["run", "find", "the", "bug", "--provider", "openai"])
        assert args.command == "run"
        assert args.prompt == ["find", "the", "bug"]
        assert args.provider == "openai"

    def test_run_quiet(self):
        parser = build_parser()
        args = parser.parse_args(["run", "test", "-q"])
        assert args.quiet is True

    def test_config_show(self):
        parser = build_parser()
        args = parser.parse_args(["config", "show"])
        assert args.command == "config"
        assert args.config_action == "show"

    def test_config_set(self):
        parser = build_parser()
        args = parser.parse_args(["config", "set", "provider=google"])
        assert args.config_action == "set"
        assert args.value == "provider=google"

    def test_config_init(self):
        parser = build_parser()
        args = parser.parse_args(["config", "init", "--force"])
        assert args.config_action == "init"
        assert args.force is True

    def test_models_command(self):
        parser = build_parser()
        args = parser.parse_args(["models", "--provider", "anthropic"])
        assert args.command == "models"
        assert args.provider == "anthropic"

    def test_hub_search(self):
        parser = build_parser()
        args = parser.parse_args(["hub", "search", "memory", "debugging"])
        assert args.command == "hub"
        assert args.hub_action == "search"
        assert args.query == ["memory", "debugging"]

    def test_hub_install(self):
        parser = build_parser()
        args = parser.parse_args(["hub", "install", "my-skill", "--version", "1.0.0"])
        assert args.command == "hub"
        assert args.hub_action == "install"
        assert args.name == "my-skill"
        assert args.version == "1.0.0"

    def test_hub_list(self):
        parser = build_parser()
        args = parser.parse_args(["hub", "list"])
        assert args.hub_action == "list"

    def test_hub_publish(self):
        parser = build_parser()
        args = parser.parse_args(["hub", "publish", "./my-skill"])
        assert args.hub_action == "publish"
        assert args.dir == "./my-skill"

    def test_hub_uninstall(self):
        parser = build_parser()
        args = parser.parse_args(["hub", "uninstall", "my-skill"])
        assert args.hub_action == "uninstall"
        assert args.name == "my-skill"

    def test_hub_update_all(self):
        parser = build_parser()
        args = parser.parse_args(["hub", "update"])
        assert args.hub_action == "update"

    def test_hub_update_specific(self):
        parser = build_parser()
        args = parser.parse_args(["hub", "update", "my-skill"])
        assert args.hub_action == "update"
        assert args.name == "my-skill"

    def test_no_command(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.command is None
