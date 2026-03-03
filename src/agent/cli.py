"""NeuralDebug CLI — standalone agent and hub commands.

Usage:
    NeuralDebug chat               Interactive debugging session
    NeuralDebug run "prompt"       One-shot debugging task
    NeuralDebug config             Show/set configuration
    NeuralDebug models             List available models
    NeuralDebug hub search QUERY   Search PilotHub for skills
    NeuralDebug hub install NAME   Install a skill
    NeuralDebug hub list           List installed skills
    NeuralDebug hub publish DIR    Publish a skill
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional


def _print_err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _print_tool_call(tc) -> None:
    """Pretty-print a tool call to stderr."""
    args_str = json.dumps(tc.arguments, indent=2) if tc.arguments else "{}"
    # Truncate long args for display
    if len(args_str) > 200:
        args_str = args_str[:200] + "..."
    _print_err(f"\n🔧 {tc.name}({args_str})")


def _print_tool_result(tc, result: str) -> None:
    """Pretty-print a tool result to stderr."""
    # Show first 500 chars of result
    display = result[:500] + ("..." if len(result) > 500 else "")
    _print_err(f"   → {display}")


# ── chat command ──────────────────────────────────────────────────────────

async def _cmd_chat(args: argparse.Namespace) -> int:
    """Interactive chat REPL (Rich TUI when available, plain-text fallback)."""
    from .runner import create_agent

    overrides = {}
    if args.provider:
        overrides["provider"] = args.provider
    if args.model:
        overrides["model"] = args.model
    if args.api_key:
        overrides["api_key"] = args.api_key

    agent = await create_agent(cli_overrides=overrides or None)

    # Use Rich TUI if available and not explicitly disabled
    use_plain = getattr(args, "plain", False)
    if not use_plain:
        try:
            from .tui import is_tui_available, run_tui
            if is_tui_available():
                return await run_tui(agent)
        except ImportError:
            pass

    # Plain-text fallback
    provider_name = agent.provider.name
    model_name = agent.config.model
    print(f"NeuralDebug Agent — {provider_name}/{model_name}")
    print("Type your debugging request. Use 'quit' or Ctrl+C to exit.\n")

    agent._on_tool_call = _print_tool_call
    agent._on_tool_result = _print_tool_result

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            return 0

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            return 0

        try:
            response = await agent.run(user_input)
            print(f"\n{response}\n")
        except KeyboardInterrupt:
            print("\n[interrupted]")
        except Exception as e:
            _print_err(f"\n❌ Error: {e}")

    return 0


# ── run command ───────────────────────────────────────────────────────────

async def _cmd_run(args: argparse.Namespace) -> int:
    """One-shot debugging task."""
    from .runner import create_agent

    overrides = {}
    if args.provider:
        overrides["provider"] = args.provider
    if args.model:
        overrides["model"] = args.model
    if args.api_key:
        overrides["api_key"] = args.api_key

    agent = await create_agent(cli_overrides=overrides or None)

    if not args.quiet:
        agent._on_tool_call = _print_tool_call
        agent._on_tool_result = _print_tool_result

    prompt = " ".join(args.prompt)
    try:
        response = await agent.run(prompt)
        print(response)
        return 0
    except Exception as e:
        _print_err(f"Error: {e}")
        return 1


# ── config command ────────────────────────────────────────────────────────

def _cmd_config(args: argparse.Namespace) -> int:
    """Show or set configuration."""
    from .config import AgentConfig

    if args.config_action == "show":
        config = AgentConfig.load()
        print(f"provider:    {config.provider}")
        print(f"model:       {config.model}")
        print(f"api_key:     {'***' + config.api_key[-4:] if len(config.api_key) > 4 else '(not set)'}")
        print(f"base_url:    {config.base_url or '(default)'}")
        print(f"max_turns:   {config.max_turns}")
        print(f"temperature: {config.temperature}")
        print(f"skills_dir:  {config.skills_dir}")
        return 0

    elif args.config_action == "set":
        config = AgentConfig.load()
        key, _, value = args.value.partition("=")
        key = key.strip()
        value = value.strip()
        if not hasattr(config, key):
            _print_err(f"Unknown config key: {key}")
            return 1
        current = getattr(config, key)
        if isinstance(current, int):
            setattr(config, key, int(value))
        elif isinstance(current, float):
            setattr(config, key, float(value))
        else:
            setattr(config, key, value)
        path = config.save()
        print(f"Saved {key}={value} to {path}")
        return 0

    elif args.config_action == "init":
        from .config import _CONFIG_DIR, _CONFIG_YAML
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if _CONFIG_YAML.exists() and not args.force:
            _print_err(f"Config already exists: {_CONFIG_YAML}")
            _print_err("Use --force to overwrite")
            return 1
        config = AgentConfig()
        path = config.save()
        print(f"Created default config: {path}")
        return 0

    return 0


# ── models command ────────────────────────────────────────────────────────

def _cmd_models(args: argparse.Namespace) -> int:
    """List available models."""
    from .model_catalog import CATALOG, get_all_providers

    provider_filter = args.provider
    providers = [provider_filter] if provider_filter else get_all_providers()

    for provider in providers:
        models = CATALOG.get(provider, [])
        if not models:
            continue
        print(f"\n{provider}:")
        for m in models:
            ctx = f"  ({m.context_window:,} tokens)" if m.context_window else ""
            print(f"  {m.id:<40} {m.name}{ctx}")

    return 0


# ── hub commands ──────────────────────────────────────────────────────────

async def _cmd_hub(args: argparse.Namespace) -> int:
    """PilotHub skill management commands."""
    from ..hub.client import PilotHubClient

    client = PilotHubClient()

    if args.hub_action == "search":
        query = " ".join(args.query)
        results = await client.search(query)
        if not results:
            print("No skills found.")
            return 0
        for s in results:
            tags = f"  [{', '.join(s.tags)}]" if s.tags else ""
            print(f"  {s.name:<30} v{s.version:<10} {s.description}{tags}")
        return 0

    elif args.hub_action == "install":
        name = args.name
        version = args.version or "latest"
        print(f"Installing {name}@{version}...")
        result = await client.install(name, version)
        if result:
            print(f"✅ Installed to {result}")
        else:
            _print_err(f"❌ Failed to install {name}")
            return 1
        return 0

    elif args.hub_action == "list":
        skills = client.list_installed()
        if not skills:
            print("No skills installed.")
            return 0
        print("Installed skills:")
        for s in skills:
            tags = f"  [{', '.join(s.tags)}]" if s.tags else ""
            print(f"  {s.name:<30} v{s.version:<10} {s.description}{tags}")
        return 0

    elif args.hub_action == "publish":
        skill_dir = Path(args.dir).resolve()
        if not (skill_dir / "SKILL.md").is_file():
            _print_err(f"No SKILL.md found in {skill_dir}")
            return 1
        print(f"Publishing {skill_dir.name}...")
        result = await client.publish(skill_dir)
        if result:
            print(f"✅ Published: {result}")
        else:
            _print_err("❌ Failed to publish")
            return 1
        return 0

    elif args.hub_action == "uninstall":
        name = args.name
        if client.uninstall(name):
            print(f"✅ Uninstalled {name}")
        else:
            _print_err(f"Skill '{name}' not found")
            return 1
        return 0

    elif args.hub_action == "update":
        name = getattr(args, "name", None)
        print("Updating skills...")
        updated = await client.update(name)
        if updated:
            print(f"✅ Updated: {', '.join(updated)}")
        else:
            print("No skills to update.")
        return 0

    return 0


# ── argument parser ───────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="NeuralDebug",
        description="NeuralDebug — AI debugging autopilot",
    )
    subparsers = parser.add_subparsers(dest="command")

    # -- chat --
    chat_p = subparsers.add_parser("chat", help="Interactive debugging session")
    chat_p.add_argument("--provider", "-p", help="LLM provider (openai, anthropic, google, ollama, openrouter)")
    chat_p.add_argument("--model", "-m", help="Model ID")
    chat_p.add_argument("--api-key", "-k", dest="api_key", help="API key")
    chat_p.add_argument("--plain", action="store_true", help="Disable Rich TUI (plain-text mode)")

    # -- run --
    run_p = subparsers.add_parser("run", help="One-shot debugging task")
    run_p.add_argument("prompt", nargs="+", help="Debugging prompt")
    run_p.add_argument("--provider", "-p", help="LLM provider")
    run_p.add_argument("--model", "-m", help="Model ID")
    run_p.add_argument("--api-key", "-k", dest="api_key", help="API key")
    run_p.add_argument("--quiet", "-q", action="store_true", help="Suppress tool call output")

    # -- config --
    config_p = subparsers.add_parser("config", help="Show or set configuration")
    config_sub = config_p.add_subparsers(dest="config_action")
    config_sub.add_parser("show", help="Show current config")
    set_p = config_sub.add_parser("set", help="Set a config value (key=value)")
    set_p.add_argument("value", help="key=value pair")
    init_p = config_sub.add_parser("init", help="Create default config file")
    init_p.add_argument("--force", action="store_true", help="Overwrite existing config")

    # -- models --
    models_p = subparsers.add_parser("models", help="List available models")
    models_p.add_argument("--provider", "-p", help="Filter by provider")

    # -- hub --
    hub_p = subparsers.add_parser("hub", help="PilotHub skill management")
    hub_sub = hub_p.add_subparsers(dest="hub_action")

    search_p = hub_sub.add_parser("search", help="Search for skills")
    search_p.add_argument("query", nargs="+", help="Search query")

    install_p = hub_sub.add_parser("install", help="Install a skill")
    install_p.add_argument("name", help="Skill name")
    install_p.add_argument("--version", "-v", help="Specific version")

    hub_sub.add_parser("list", help="List installed skills")

    publish_p = hub_sub.add_parser("publish", help="Publish a skill")
    publish_p.add_argument("dir", help="Skill directory containing SKILL.md")

    uninstall_p = hub_sub.add_parser("uninstall", help="Uninstall a skill")
    uninstall_p.add_argument("name", help="Skill name")

    update_p = hub_sub.add_parser("update", help="Update installed skills")
    update_p.add_argument("name", nargs="?", help="Specific skill to update (default: all)")

    return parser


# ── main ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "config":
        sys.exit(_cmd_config(args))
    elif args.command == "models":
        sys.exit(_cmd_models(args))
    elif args.command == "chat":
        sys.exit(asyncio.run(_cmd_chat(args)))
    elif args.command == "run":
        sys.exit(asyncio.run(_cmd_run(args)))
    elif args.command == "hub":
        if not args.hub_action:
            parser.parse_args(["hub", "--help"])
            sys.exit(0)
        sys.exit(asyncio.run(_cmd_hub(args)))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
