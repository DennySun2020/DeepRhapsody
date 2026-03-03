"""Advanced analysis commands — diagnose, hallucinate, sae, neuron, surgery."""

from .base import Command


class DiagnoseCommand(Command):
    name = "diagnose"
    aliases = ["diag"]
    description = "Run autonomous diagnostic pipeline on a test suite"
    requires_session = False

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_diagnose(args)


class InvestigateCommand(Command):
    name = "investigate"
    aliases = ["inv"]
    description = "Single-case investigation with full toolchain"
    requires_session = False

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_investigate(args)


class HallucinateCommand(Command):
    name = "hallucinate"
    aliases = ["detect", "hallucination"]
    description = "Generate tokens and flag potential hallucinations"
    requires_session = False

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_hallucinate(args)


class SAECommand(Command):
    name = "sae"
    aliases = []
    description = "Sparse Autoencoder — train, decompose, dashboard"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_sae(args)


class NeuronCommand(Command):
    name = "neuron"
    aliases = ["neurons"]
    description = "Neuron-level analysis — scan, dashboard, ablate"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_neuron(args)


class SurgeryCommand(Command):
    name = "surgery"
    aliases = ["head_surgery"]
    description = "Attention head surgery — ablate, amplify, sweep, restore"

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_surgery(args)


class ExecAnalysisCommand(Command):
    name = "exec_analysis"
    aliases = ["exec", "forge"]
    description = "Execute custom analysis code in sandboxed environment"
    requires_session = False

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_exec_analysis(args)


def register_advanced_commands(registry) -> None:
    """Register advanced analysis commands with *registry*."""
    for cls in [DiagnoseCommand, InvestigateCommand, HallucinateCommand,
                SAECommand, NeuronCommand, SurgeryCommand,
                ExecAnalysisCommand]:
        registry.register(cls())
