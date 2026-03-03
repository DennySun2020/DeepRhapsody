"""Core debug commands — start, step, inspect, evaluate, breakpoints, etc.

These commands delegate to the debugger's existing ``cmd_*`` methods.
They are registered with the :class:`CommandRegistry` so the dispatch
loop in ``LLMDebugServer._dispatch_extra`` can be replaced by a simple
registry lookup.
"""

from .base import Command
from typing import List


class StartCommand(Command):
    name = "start"
    aliases = ["s"]
    description = "Begin inference with a prompt, pausing at the first layer"
    requires_session = False

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_start(args)


class StepOverCommand(Command):
    name = "step_over"
    aliases = ["n", "next"]
    description = "Execute current layer, advance to the next"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_step_over()


class StepInCommand(Command):
    name = "step_in"
    aliases = ["si"]
    description = "Enter a block's sub-layers (attention, FFN)"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_step_in()


class StepOutCommand(Command):
    name = "step_out"
    aliases = ["so"]
    description = "Return to the parent block"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_step_out()


class ContinueCommand(Command):
    name = "continue"
    aliases = ["c"]
    description = "Run to the next breakpoint or end of forward pass"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_continue()


class SetBreakpointCommand(Command):
    name = "b"
    aliases = ["break", "breakpoint"]
    description = "Set breakpoint on a layer (e.g. block_3, block_2.attention)"
    requires_session = False

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_set_breakpoint(args)


class RemoveBreakpointCommand(Command):
    name = "remove_breakpoint"
    aliases = ["rb"]
    description = "Remove a breakpoint"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_remove_breakpoint(args)


class ListBreakpointsCommand(Command):
    name = "breakpoints"
    aliases = ["bl"]
    description = "List all active breakpoints"
    requires_session = False

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_list_breakpoints()


class InspectCommand(Command):
    name = "inspect"
    aliases = ["i"]
    description = "Show current layer state and tensor statistics"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_inspect()


class EvaluateCommand(Command):
    name = "evaluate"
    aliases = ["e", "eval"]
    description = "Evaluate a PyTorch expression on live tensors"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_evaluate(args)


class ListSourceCommand(Command):
    name = "list"
    aliases = ["l"]
    description = "Show model architecture around current position"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_list_source(args)


class BacktraceCommand(Command):
    name = "backtrace"
    aliases = ["bt"]
    description = "Show layer execution stack"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_backtrace()


class GraphCommand(Command):
    name = "graph"
    aliases = ["architecture", "arch"]
    description = "Show model architecture tree (ascii/detailed/json/mermaid)"
    requires_session = False

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_graph(args)


class GenerateCommand(Command):
    name = "generate"
    aliases = ["gen", "g"]
    description = "Run full generation (default 50 tokens)"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_generate(args)


# -- registry builder ------------------------------------------------------

def register_core_commands(registry) -> None:
    """Register all core debug commands with *registry*."""
    for cls in [
        StartCommand, StepOverCommand, StepInCommand, StepOutCommand,
        ContinueCommand, SetBreakpointCommand, RemoveBreakpointCommand,
        ListBreakpointsCommand, InspectCommand, EvaluateCommand,
        ListSourceCommand, BacktraceCommand, GraphCommand, GenerateCommand,
    ]:
        registry.register(cls())
