"""Interpretability commands — logit_lens, patch, probe, attention."""

from .base import Command


class LogitLensCommand(Command):
    name = "logit_lens"
    aliases = ["lens"]
    description = "Per-layer prediction trajectory (Logit Lens)"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_logit_lens(args)


class PatchCommand(Command):
    name = "patch"
    aliases = ["causal_trace"]
    description = "Activation Patching — causal tracing across layers"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_patch(args)


class AttentionCommand(Command):
    name = "attention"
    aliases = ["attn", "heads"]
    description = "Attention head analysis — rank heads by focus"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_attention(args)


class ProbeCommand(Command):
    name = "probe"
    aliases = ["probing"]
    description = "Probing — test what info is encoded at each layer"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_probe(args)


def register_interpretability_commands(registry) -> None:
    """Register interpretability commands with *registry*."""
    for cls in [LogitLensCommand, PatchCommand, AttentionCommand,
                ProbeCommand]:
        registry.register(cls())
