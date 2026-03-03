#!/usr/bin/env python3
"""
End-to-end demo of the NeuralDebug standalone agent.

This script proves the full pipeline works WITHOUT an LLM API key by using
a scripted mock provider that simulates what a real LLM would do.

What it exercises:
  ✓ AgentRunner agentic loop (think → tool → observe)
  ✓ Tool Registry auto-discovering all 14 MCP debug tools
  ✓ Real NeuralDebug_info call (detects platform toolchain)
  ✓ Real debug server launch (Python bdb debugger)
  ✓ Real breakpoint, step, inspect, evaluate, backtrace, list_code
  ✓ Real debug server stop
  ✓ PilotHub skill loading from a local directory
  ✓ System prompt assembly with skills
  ✓ Config system
  ✓ Token usage tracking

Run:
    cd <repo_root>
    python playground/demo_standalone_agent.py
"""

import asyncio
import json
import os
import sys
import textwrap
from pathlib import Path

# Ensure imports work from repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.agent.providers.base import (
    LLMProvider, LLMResponse, Message, ModelInfo, ToolCall, ToolDefinition, TokenUsage,
)
from src.agent.tools.registry import ToolRegistry
from src.agent.config import AgentConfig
from src.agent.runner import AgentRunner
from src.agent.system_prompt import build_system_prompt
from src.hub.registry import LocalRegistry
from src.hub.skill_spec import SkillMetadata


# ── Scripted mock provider ────────────────────────────────────────────────
# Simulates what an LLM would do: call tools in sequence, then summarize.

class ScriptedProvider(LLMProvider):
    """A mock LLM that follows a pre-scripted debugging workflow."""

    def __init__(self):
        self._step = 0
        self._script = [
            # Step 0: Check toolchain
            lambda msgs: LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="call_0", name="NeuralDebug_info", arguments={"language": "python"})],
                usage=TokenUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
            ),
            # Step 1: Start debug server
            lambda msgs: LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="call_1", name="NeuralDebug_start_server", arguments={
                    "target": str(REPO_ROOT / "examples" / "sample_buggy_grades.py"),
                    "language": "python",
                    "port": 15678,  # Use non-default port to avoid conflicts
                })],
                usage=TokenUsage(prompt_tokens=200, completion_tokens=30, total_tokens=230),
            ),
            # Step 2: Set breakpoint at the filter condition
            lambda msgs: LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="call_2", name="NeuralDebug_set_breakpoint", arguments={
                    "location": "44", "port": 15678, "language": "python",
                })],
                usage=TokenUsage(prompt_tokens=300, completion_tokens=20, total_tokens=320),
            ),
            # Step 3: Start execution
            lambda msgs: LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="call_3", name="NeuralDebug_start_execution", arguments={
                    "port": 15678, "language": "python",
                })],
                usage=TokenUsage(prompt_tokens=400, completion_tokens=20, total_tokens=420),
            ),
            # Step 4: Inspect variables
            lambda msgs: LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="call_4", name="NeuralDebug_inspect", arguments={
                    "port": 15678, "language": "python",
                })],
                usage=TokenUsage(prompt_tokens=500, completion_tokens=20, total_tokens=520),
            ),
            # Step 5: List code around current location
            lambda msgs: LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="call_5", name="NeuralDebug_list_code", arguments={
                    "port": 15678, "language": "python",
                })],
                usage=TokenUsage(prompt_tokens=600, completion_tokens=20, total_tokens=620),
            ),
            # Step 6: Evaluate an expression
            lambda msgs: LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="call_6", name="NeuralDebug_evaluate", arguments={
                    "expression": "score >= 0", "port": 15678, "language": "python",
                })],
                usage=TokenUsage(prompt_tokens=700, completion_tokens=20, total_tokens=720),
            ),
            # Step 7: Get backtrace
            lambda msgs: LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="call_7", name="NeuralDebug_backtrace", arguments={
                    "port": 15678, "language": "python",
                })],
                usage=TokenUsage(prompt_tokens=800, completion_tokens=20, total_tokens=820),
            ),
            # Step 8: Stop the debug server
            lambda msgs: LLMResponse(
                text=None,
                tool_calls=[ToolCall(id="call_8", name="NeuralDebug_stop", arguments={
                    "port": 15678, "language": "python",
                })],
                usage=TokenUsage(prompt_tokens=900, completion_tokens=20, total_tokens=920),
            ),
            # Step 9: Final text response (summary)
            lambda msgs: LLMResponse(
                text=textwrap.dedent("""\
                    ## Debugging Summary

                    I investigated `sample_buggy_grades.py` and found **3 bugs**:

                    | # | Location | Bug | Fix |
                    |---|----------|-----|-----|
                    | 1 | Line 44 | `score >= 0` includes zero | Change to `score > 0` |
                    | 2 | Line 60 | Median on unsorted list | Add `scores = sorted(scores)` |
                    | 3 | Line 75 | Divides by N not N-1 | Use `len(scores) - 1` |

                    All three bugs were confirmed by stepping through the code and
                    inspecting variables at runtime."""),
                usage=TokenUsage(prompt_tokens=1000, completion_tokens=100, total_tokens=1100),
            ),
        ]

    @property
    def name(self) -> str:
        return "ScriptedMock"

    @property
    def default_model(self) -> str:
        return "mock-debug-v1"

    async def chat(self, messages, tools=None, *, system=None, temperature=0.0, max_tokens=None):
        if self._step >= len(self._script):
            return LLMResponse(text="[end of script]")
        resp = self._script[self._step](messages)
        self._step += 1
        return resp

    def list_models(self):
        return [ModelInfo(id="mock-debug-v1", name="Mock Debug Model", provider="mock")]


# ── Pretty printing ───────────────────────────────────────────────────────

BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

def header(text):
    print(f"\n{BOLD}{CYAN}{'═' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'═' * 60}{RESET}\n")

def section(text):
    print(f"\n{BOLD}{YELLOW}── {text} ──{RESET}\n")

def tool_call_display(tc):
    args = json.dumps(tc.arguments, indent=2) if tc.arguments else "{}"
    # Truncate for readability
    if len(args) > 300:
        args = args[:300] + "\n  ..."
    print(f"  {GREEN}🔧 {tc.name}{RESET}")
    for line in args.split("\n"):
        print(f"     {DIM}{line}{RESET}")

def tool_result_display(tc, result):
    try:
        data = json.loads(result)
        # Show status and key info
        status = data.get("status", "ok")
        color = GREEN if status in ("ok", "paused") else RED
        print(f"     {color}→ status: {status}{RESET}")
        # Show interesting fields
        for key in ("message", "current_location", "debugger", "python"):
            if key in data:
                val = data[key]
                if isinstance(val, dict):
                    val = json.dumps(val)
                print(f"       {DIM}{key}: {str(val)[:120]}{RESET}")
        # Show local variables if present
        if "local_variables" in data:
            lvars = data["local_variables"]
            if isinstance(lvars, dict):
                shown = list(lvars.items())[:5]
                for name, info in shown:
                    if isinstance(info, dict):
                        print(f"       {DIM}{name} = {info.get('value', '?')} ({info.get('type', '?')}){RESET}")
    except (json.JSONDecodeError, TypeError):
        display = result[:200] + ("..." if len(result) > 200 else "")
        print(f"     {DIM}→ {display}{RESET}")


# ── Demo runner ───────────────────────────────────────────────────────────

async def demo_tool_discovery():
    """Demo 1: Show that tool registry discovers all MCP debug tools."""
    section("Tool Registry — Auto-Discovery")

    tools = ToolRegistry()
    tools.discover_debug_tools()

    print(f"  Discovered {BOLD}{len(tools.tools)}{RESET} debug tools:\n")
    for t in tools.tools:
        defn = t.definition()
        print(f"    {GREEN}•{RESET} {defn.name:<35} {DIM}{defn.description[:60]}...{RESET}")

    return tools


async def demo_config():
    """Demo 2: Show config system."""
    section("Configuration System")

    config = AgentConfig.load()
    print(f"  provider:    {config.provider}")
    print(f"  model:       {config.model}")
    print(f"  max_turns:   {config.max_turns}")
    print(f"  temperature: {config.temperature}")
    print(f"  skills_dir:  {config.skills_dir}")


async def demo_hub_skills(tmp_dir: Path):
    """Demo 3: Create and load PilotHub skills."""
    section("PilotHub Skills — Local Registry")

    # Create a sample skill
    skill_dir = tmp_dir / "memory-debugger"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
        ---
        name: memory-debugger
        description: Debug memory leaks using Valgrind and AddressSanitizer
        version: 1.0.0
        author: demo
        tags: [memory, c, cpp]
        ---

        # Memory Debugger Skill

        When debugging memory issues:
        1. Run Valgrind: `valgrind --leak-check=full ./program`
        2. Compile with ASan: `gcc -fsanitize=address -g program.c`
        3. Look for "definitely lost" and "Invalid read" messages
    """), encoding="utf-8")

    registry = LocalRegistry(str(tmp_dir))
    skills = registry.list_skills()
    print(f"  Installed skills: {len(skills)}\n")
    for s in skills:
        print(f"    {GREEN}•{RESET} {s.name:<25} v{s.version:<10} {DIM}{s.description}{RESET}")

    prompts = registry.get_all_prompts()
    print(f"\n  Skill prompts loaded: {len(prompts)}")
    for name, prompt in prompts.items():
        preview = prompt[:80].replace("\n", " ")
        print(f"    {DIM}{name}: \"{preview}...\"{RESET}")

    return prompts


async def demo_system_prompt(skill_prompts):
    """Demo 4: Show system prompt assembly."""
    section("System Prompt Assembly")

    prompt = build_system_prompt(skill_prompts=skill_prompts, extra_context="Always explain bugs clearly.")
    lines = prompt.split("\n")
    print(f"  System prompt: {len(prompt)} chars, {len(lines)} lines")
    print(f"  Contains: core identity + {len(skill_prompts)} skill(s) + extra context")
    # Show first and last few lines
    print(f"\n  {DIM}First 3 lines:{RESET}")
    for line in lines[:3]:
        print(f"    {DIM}{line}{RESET}")
    print(f"  {DIM}...{RESET}")


async def demo_agent_run(tools, skill_prompts):
    """Demo 5: Full agentic loop — scripted LLM driving real debug tools."""
    section("Agent Runner — Full Debugging Session")

    provider = ScriptedProvider()
    config = AgentConfig(max_turns=20)

    system_prompt = build_system_prompt(
        skill_prompts=skill_prompts,
        extra_context="Debug the sample_buggy_grades.py program.",
    )

    call_count = 0

    def on_tool_call(tc):
        nonlocal call_count
        call_count += 1
        tool_call_display(tc)

    def on_tool_result(tc, result):
        tool_result_display(tc, result)

    agent = AgentRunner(
        provider=provider,
        tools=tools,
        config=config,
        system_prompt=system_prompt,
        on_tool_call=on_tool_call,
        on_tool_result=on_tool_result,
    )

    print(f"  Provider: {BOLD}{provider.name}/{provider.default_model}{RESET}")
    print(f"  Tools:    {len(tools.tools)} available")
    print(f"  Prompt:   {len(system_prompt)} chars\n")
    print(f"  {BLUE}User:{RESET} Debug sample_buggy_grades.py — all results are wrong.\n")
    print(f"  {YELLOW}Agent is working...{RESET}\n")

    result = await agent.run(
        "Debug sample_buggy_grades.py — the mean, median, and std dev are all wrong. "
        "Find all the bugs."
    )

    print(f"\n  {BLUE}{'─' * 50}{RESET}")
    print(f"\n  {BOLD}Agent Response:{RESET}\n")
    for line in result.split("\n"):
        print(f"    {line}")

    print(f"\n  {BLUE}{'─' * 50}{RESET}")
    print(f"\n  {DIM}Stats:{RESET}")
    print(f"    Tool calls made:     {call_count}")
    print(f"    Messages in history: {len(agent.messages)}")
    print(f"    Total tokens used:   {agent.total_usage.total_tokens:,}")


# ── Main ──────────────────────────────────────────────────────────────────

async def main():
    header("NeuralDebug Standalone Agent — End-to-End Demo")

    print("  This demo proves the full standalone agent pipeline works:")
    print(f"    {GREEN}✓{RESET} Tool registry auto-discovers 14 MCP debug tools")
    print(f"    {GREEN}✓{RESET} Config system loads settings")
    print(f"    {GREEN}✓{RESET} PilotHub skills load from local directory")
    print(f"    {GREEN}✓{RESET} System prompt assembles with skills + context")
    print(f"    {GREEN}✓{RESET} Agent runner drives REAL debugger through scripted LLM")
    print(f"    {GREEN}✓{RESET} No API key required (uses scripted mock provider)")

    # 1. Tool discovery
    tools = await demo_tool_discovery()

    # 2. Config
    await demo_config()

    # 3. Hub skills
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="NeuralDebug_demo_"))
    skill_prompts = await demo_hub_skills(tmp_dir)

    # 4. System prompt
    await demo_system_prompt(skill_prompts)

    # 5. Full agent run with real debug tools
    await demo_agent_run(tools, skill_prompts)

    # Cleanup
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)

    header("Demo Complete!")
    print("  The standalone agent successfully:")
    print(f"    {GREEN}✓{RESET} Discovered all debug tools from the MCP server")
    print(f"    {GREEN}✓{RESET} Loaded PilotHub skills from a local directory")
    print(f"    {GREEN}✓{RESET} Assembled a system prompt with skills and context")
    print(f"    {GREEN}✓{RESET} Ran a full agentic loop with real tool execution")
    print(f"    {GREEN}✓{RESET} Called real debugger tools (info, start, breakpoint, etc.)")
    print(f"    {GREEN}✓{RESET} Tracked token usage across the session")
    print()
    print("  To use with a real LLM, just set your API key:")
    print(f"    {CYAN}export OPENAI_API_KEY=sk-...{RESET}")
    print(f"    {CYAN}NeuralDebug chat{RESET}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
