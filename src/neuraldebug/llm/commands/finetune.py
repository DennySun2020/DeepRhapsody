"""Fine-tuning command."""

from .base import Command


class FinetuneCommand(Command):
    name = "finetune"
    aliases = ["ft"]
    description = "LoRA fine-tuning to inject missing knowledge"
    requires_session = False

    def execute(self, debugger, args: str) -> dict:
        return debugger.cmd_finetune(args)


def register_finetune_commands(registry) -> None:
    """Register fine-tuning commands with *registry*."""
    registry.register(FinetuneCommand())
