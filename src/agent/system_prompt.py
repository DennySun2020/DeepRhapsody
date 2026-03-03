"""System prompt builder for the NeuralDebug standalone agent."""

from __future__ import annotations

from typing import List, Optional

from .providers.base import ToolDefinition


CORE_IDENTITY = """\
You are NeuralDebug — an AI debugging autopilot. You help developers find and fix bugs
by controlling real debuggers through natural language.

You have access to debugging tools that let you:
- Start debug sessions for Python, C/C++, C#, Rust, Java, Go, Node.js, Ruby, and Assembly
- Set breakpoints, step through code, inspect variables, evaluate expressions
- View call stacks, list source code, and control execution flow
- Perform binary analysis and reverse engineering
- Debug LLM/transformer models at the layer and neuron level

## Workflow
1. **Detect**: Identify the target language from the file extension or user description
2. **Info**: Check available debuggers/compilers on the current system
3. **Launch**: Start a debug server for the target program
4. **Breakpoint**: Set breakpoints at suspicious locations
5. **Execute**: Start program execution
6. **Investigate**: Step through code, inspect variables, evaluate expressions
7. **Diagnose**: Analyze findings to identify the root cause
8. **Report**: Explain the bug clearly with evidence from the debugging session
9. **Cleanup**: Stop the debug server when done

## Guidelines
- Always run NeuralDebug_info first to check available tooling
- Set breakpoints before starting execution
- After each step, inspect variables and analyze the state
- Form hypotheses about the bug and test them systematically
- When you find the root cause, explain it clearly with evidence
- Always stop the debug server when the session is complete
"""


def build_system_prompt(
    tools: Optional[List[ToolDefinition]] = None,
    extra_context: Optional[str] = None,
    skill_prompts: Optional[dict[str, str]] = None,
) -> str:
    """Build the full system prompt for the agent.

    Parameters
    ----------
    tools:
        Available tool definitions (for context, not schema — that goes via the API).
    extra_context:
        Additional user-provided system prompt text.
    skill_prompts:
        Dict of skill_name -> prompt content from installed PilotHub skills.
    """
    parts = [CORE_IDENTITY]

    if skill_prompts:
        parts.append("\n## Installed Skills\n")
        for name, prompt in skill_prompts.items():
            parts.append(f"### {name}\n{prompt}\n")

    if extra_context:
        parts.append(f"\n## Additional Context\n{extra_context}\n")

    return "\n".join(parts)
