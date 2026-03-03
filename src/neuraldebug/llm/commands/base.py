"""Command ABC + CommandRegistry — pluggable command dispatch.

Instead of elif chains in ``_dispatch_extra``, each command is a class
that registers itself with the :class:`CommandRegistry`.  Users can add
custom analysis commands without modifying debugger.py.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional


class Command(ABC):
    """Base class for an LLM debugger command."""

    name: str = ""
    aliases: List[str] = []
    description: str = ""
    requires_session: bool = True   # needs an active stepping session?

    @abstractmethod
    def execute(self, debugger: Any, args: str) -> dict:
        """Run the command.

        Args:
            debugger: The ``LLMDebugger`` instance.
            args: Raw argument string (everything after the command name).

        Returns:
            Standard NeuralDebug response dict.
        """


class CommandRegistry:
    """Maps command names (+ aliases) to :class:`Command` instances."""

    def __init__(self):
        self._commands: Dict[str, Command] = {}
        self._all: List[Command] = []

    def register(self, cmd: Command) -> None:
        """Register a command (and its aliases)."""
        if not cmd.name:
            raise ValueError("Command must have a non-empty 'name'")
        self._all.append(cmd)
        for key in [cmd.name] + list(cmd.aliases):
            self._commands[key] = cmd

    def get(self, name: str) -> Optional[Command]:
        """Look up a command by name or alias."""
        return self._commands.get(name)

    def dispatch(self, name: str, debugger: Any, args: str) -> Optional[dict]:
        """Dispatch a command by name.

        Returns:
            Response dict, or ``None`` if the command is not registered.
        """
        cmd = self._commands.get(name)
        if cmd is None:
            return None
        return cmd.execute(debugger, args)

    def list_commands(self) -> List[dict]:
        """Return a list of registered commands (deduplicated)."""
        seen = set()
        result = []
        for cmd in self._all:
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append({
                    "name": cmd.name,
                    "aliases": cmd.aliases,
                    "description": cmd.description,
                    "requires_session": cmd.requires_session,
                })
        return result

    def command_names(self) -> List[str]:
        """Return all registered command names and aliases."""
        return sorted(self._commands.keys())


def command(name: str, aliases: Optional[List[str]] = None,
            description: str = "", requires_session: bool = True):
    """Decorator to create a Command from a plain function.

    Usage::

        @command("logit_lens", aliases=["lens"],
                 description="Per-layer prediction trajectory")
        def cmd_logit_lens(debugger, args):
            return debugger.cmd_logit_lens(args)
    """
    _aliases = aliases or []

    def decorator(fn: Callable) -> Command:
        class _Cmd(Command):
            def execute(self, debugger, args):
                return fn(debugger, args)
        _Cmd.name = name
        _Cmd.aliases = _aliases
        _Cmd.description = description
        _Cmd.requires_session = requires_session
        return _Cmd()
    return decorator
