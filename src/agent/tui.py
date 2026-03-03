"""Rich Terminal UI for NeuralDebug standalone agent.

Provides a polished interactive debugging experience with:
- Rich Markdown rendering for agent responses
- Syntax-highlighted code blocks in tool results
- Tool call progress panels with spinners
- Session header showing provider, model, and token usage
- prompt_toolkit input with history and multi-line support
- Slash commands (/help, /clear, /status, /reset, /tools)
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import AgentRunner
    from .providers.base import ToolCall


# ---------------------------------------------------------------------------
# Rich availability check
# ---------------------------------------------------------------------------

_RICH_AVAILABLE = False
_PROMPT_TOOLKIT_AVAILABLE = False

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text
    from rich.theme import Theme
    from rich.live import Live
    from rich.spinner import Spinner
    from rich.columns import Columns
    from rich.rule import Rule
    from rich.padding import Padding
    _RICH_AVAILABLE = True
except ImportError:
    pass

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.styles import Style as PTStyle
    _PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

NeuralDebug_THEME = {
    "info": "dim cyan",
    "warning": "bold yellow",
    "error": "bold red",
    "success": "bold green",
    "tool.name": "bold magenta",
    "tool.arg": "dim",
    "header.provider": "bold cyan",
    "header.model": "bold white",
    "header.tokens": "dim green",
    "user.prompt": "bold green",
    "slash.cmd": "bold yellow",
}


def is_tui_available() -> bool:
    """Check whether the Rich TUI can be used."""
    return _RICH_AVAILABLE


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

SLASH_COMMANDS = {
    "/help": "Show available commands",
    "/clear": "Clear conversation history and screen",
    "/reset": "Reset agent (clear history + tokens)",
    "/status": "Show session status (provider, model, tokens)",
    "/tools": "List available debugging tools",
    "/history": "Show conversation turn count",
    "/quit": "Exit NeuralDebug",
}


# ---------------------------------------------------------------------------
# NeuralDebugTUI
# ---------------------------------------------------------------------------

class NeuralDebugTUI:
    """Rich Terminal UI for the NeuralDebug standalone agent."""

    def __init__(self, agent: "AgentRunner"):
        if not _RICH_AVAILABLE:
            raise RuntimeError("Rich is required for TUI mode: pip install rich")

        self.agent = agent
        self.console = Console(theme=Theme(NeuralDebug_THEME))
        self._turn_count = 0
        self._session_start = time.time()
        self._tool_call_count = 0
        self._active_spinner: Optional[Live] = None

        # Set up prompt_toolkit session if available
        self._prompt_session: Optional[Any] = None
        if _PROMPT_TOOLKIT_AVAILABLE:
            try:
                history_dir = Path("~/.NeuralDebug").expanduser()
                history_dir.mkdir(parents=True, exist_ok=True)
                history_file = history_dir / "chat_history"
                self._prompt_session = PromptSession(
                    history=FileHistory(str(history_file)),
                    style=PTStyle.from_dict({
                        "prompt": "bold ansicyan",
                    }),
                )
            except Exception:
                # No real terminal (e.g., in tests or piped stdin)
                self._prompt_session = None

        # Wire up agent callbacks
        self.agent._on_tool_call = self._on_tool_call
        self.agent._on_tool_result = self._on_tool_result

    # ── Header / Banner ───────────────────────────────────────────────

    def print_banner(self) -> None:
        """Print the NeuralDebug welcome banner."""
        provider = self.agent.provider.name
        model = self.agent.config.model
        tool_count = len(self.agent.tools.get_definitions())

        banner_text = Text()
        banner_text.append("🐛 NeuralDebug Agent", style="bold white")
        banner_text.append("\n")
        banner_text.append(f"   Provider: ", style="dim")
        banner_text.append(f"{provider}", style="header.provider")
        banner_text.append(f"  Model: ", style="dim")
        banner_text.append(f"{model}", style="header.model")
        banner_text.append(f"  Tools: ", style="dim")
        banner_text.append(f"{tool_count}", style="bold yellow")

        self.console.print(Panel(
            banner_text,
            border_style="cyan",
            padding=(0, 1),
        ))

        self.console.print(
            "  Type your debugging request, or /help for commands. "
            "Use /quit or Ctrl+C to exit.",
            style="dim",
        )
        self.console.print()

    # ── Status bar ────────────────────────────────────────────────────

    def _format_tokens(self) -> str:
        usage = self.agent.total_usage
        if usage.total_tokens == 0:
            return "0"
        return f"{usage.total_tokens:,} ({usage.prompt_tokens:,}↑ {usage.completion_tokens:,}↓)"

    def print_status(self) -> None:
        """Print current session status."""
        elapsed = time.time() - self._session_start
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)

        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("key", style="dim")
        table.add_column("value", style="bold")
        table.add_row("Provider", self.agent.provider.name)
        table.add_row("Model", self.agent.config.model)
        table.add_row("Tokens", self._format_tokens())
        table.add_row("Turns", str(self._turn_count))
        table.add_row("Tool calls", str(self._tool_call_count))
        table.add_row("Session", f"{mins}m {secs}s")
        table.add_row("Messages", str(len(self.agent.messages)))

        self.console.print(Panel(
            table,
            title="[bold]Session Status[/bold]",
            border_style="cyan",
            padding=(0, 1),
        ))

    # ── Tool callbacks ────────────────────────────────────────────────

    def _on_tool_call(self, tc: "ToolCall") -> None:
        """Render a tool call with a styled panel."""
        self._tool_call_count += 1

        args_str = ""
        if tc.arguments:
            args_str = json.dumps(tc.arguments, indent=2)
            if len(args_str) > 400:
                args_str = args_str[:400] + "\n..."

        header = Text()
        header.append("🔧 ", style="bold")
        header.append(tc.name, style="tool.name")

        if args_str:
            content = Syntax(args_str, "json", theme="monokai", line_numbers=False)
        else:
            content = Text("(no arguments)", style="dim")

        self.console.print(Panel(
            content,
            title=header,
            title_align="left",
            border_style="magenta",
            padding=(0, 1),
            width=min(self.console.width, 100),
        ))

    def _on_tool_result(self, tc: "ToolCall", result: str) -> None:
        """Render a tool result with syntax detection."""
        display = result
        truncated = False
        if len(display) > 2000:
            display = display[:2000]
            truncated = True

        # Try to detect if result is JSON
        lexer = "text"
        try:
            json.loads(display)
            lexer = "json"
        except (json.JSONDecodeError, ValueError):
            # Check for common code patterns
            if display.strip().startswith(("{", "[")):
                lexer = "json"
            elif "def " in display or "class " in display or "import " in display:
                lexer = "python"
            elif "#include" in display or "int main" in display:
                lexer = "cpp"
            elif "0x" in display and ("rax" in display.lower() or "rsp" in display.lower()):
                lexer = "nasm"

        content = Syntax(
            display,
            lexer,
            theme="monokai",
            line_numbers=False,
            word_wrap=True,
        )

        suffix = ""
        if truncated:
            suffix = " [dim](truncated)[/dim]"

        self.console.print(Panel(
            content,
            title=f"[dim]→ result{suffix}[/dim]",
            title_align="left",
            border_style="dim",
            padding=(0, 1),
            width=min(self.console.width, 100),
        ))

    # ── Response rendering ────────────────────────────────────────────

    def print_response(self, text: str) -> None:
        """Render the agent's response as Rich Markdown."""
        if not text.strip():
            return

        self.console.print()
        md = Markdown(text, code_theme="monokai")
        self.console.print(Panel(
            md,
            title="[bold cyan]🐛 NeuralDebug[/bold cyan]",
            title_align="left",
            border_style="cyan",
            padding=(1, 2),
        ))
        self.console.print()

    # ── Slash command handling ────────────────────────────────────────

    def _handle_slash_command(self, cmd: str) -> Optional[str]:
        """Handle a slash command. Returns None to continue, 'quit' to exit."""
        parts = cmd.strip().split()
        command = parts[0].lower()

        if command == "/help":
            table = Table(title="Commands", show_header=True, border_style="cyan")
            table.add_column("Command", style="slash.cmd")
            table.add_column("Description")
            for c, desc in SLASH_COMMANDS.items():
                table.add_row(c, desc)
            self.console.print(table)
            return None

        elif command == "/clear":
            self.agent.reset()
            self._turn_count = 0
            self._tool_call_count = 0
            self._session_start = time.time()
            self.console.clear()
            self.print_banner()
            self.console.print("[success]✓ Conversation cleared[/success]")
            return None

        elif command == "/reset":
            self.agent.reset()
            self._turn_count = 0
            self._tool_call_count = 0
            self.console.print("[success]✓ Agent reset (history + tokens cleared)[/success]")
            return None

        elif command == "/status":
            self.print_status()
            return None

        elif command == "/tools":
            tools = self.agent.tools.get_definitions()
            if not tools:
                self.console.print("[warning]No tools loaded[/warning]")
                return None
            table = Table(title=f"Debugging Tools ({len(tools)})", border_style="magenta")
            table.add_column("#", style="dim", width=4)
            table.add_column("Tool", style="tool.name")
            table.add_column("Description", style="dim")
            for i, t in enumerate(tools, 1):
                desc = t.description
                if len(desc) > 80:
                    desc = desc[:77] + "..."
                table.add_row(str(i), t.name, desc)
            self.console.print(table)
            return None

        elif command == "/history":
            self.console.print(f"Turns: {self._turn_count}, Messages: {len(self.agent.messages)}")
            return None

        elif command in ("/quit", "/exit", "/q"):
            return "quit"

        else:
            self.console.print(f"[warning]Unknown command: {command}. Type /help for available commands.[/warning]")
            return None

    # ── Input ─────────────────────────────────────────────────────────

    def _get_input(self) -> Optional[str]:
        """Get user input using prompt_toolkit or fallback to built-in input()."""
        try:
            if self._prompt_session:
                return self._prompt_session.prompt(
                    HTML("<ansicyan><b>you❯ </b></ansicyan>"),
                ).strip()
            else:
                return input("you❯ ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

    # ── Main loop ─────────────────────────────────────────────────────

    async def run(self) -> int:
        """Run the interactive TUI chat loop. Returns exit code."""
        self.print_banner()

        while True:
            user_input = self._get_input()

            if user_input is None:
                self.console.print("\n[dim]Goodbye![/dim]")
                return 0

            if not user_input:
                continue

            # Slash commands
            if user_input.startswith("/"):
                result = self._handle_slash_command(user_input)
                if result == "quit":
                    self.console.print("[dim]Goodbye![/dim]")
                    return 0
                continue

            # Regular quit
            if user_input.lower() in ("quit", "exit", "q"):
                self.console.print("[dim]Goodbye![/dim]")
                return 0

            # Run agent
            self._turn_count += 1
            try:
                self.console.print()
                response = await self.agent.run(user_input)
                self.print_response(response)

                # Show token usage inline
                tokens = self._format_tokens()
                self.console.print(
                    f"  [dim]tokens: {tokens}[/dim]",
                    justify="right",
                )
            except KeyboardInterrupt:
                self.console.print("\n[warning][interrupted][/warning]")
            except Exception as e:
                self.console.print(f"\n[error]❌ Error: {e}[/error]")

        return 0


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

async def run_tui(agent: "AgentRunner") -> int:
    """Create and run the Rich TUI. Returns exit code."""
    tui = NeuralDebugTUI(agent)
    return await tui.run()
