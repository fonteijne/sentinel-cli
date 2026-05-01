"""Modal screen that prompts for a Jira ticket id."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label


class TicketPromptScreen(ModalScreen[Optional[str]]):
    """Ask the user for a ticket id; return the trimmed id or None on cancel."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    TicketPromptScreen {
        align: center middle;
    }

    #ticket-prompt-box {
        width: 60;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #ticket-prompt-box Label {
        margin-bottom: 1;
    }

    #ticket-prompt-box Input {
        margin-bottom: 1;
    }

    #ticket-prompt-buttons {
        height: auto;
        align-horizontal: right;
    }

    #ticket-prompt-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, action_label: str) -> None:
        super().__init__()
        self._action_label = action_label

    def compose(self) -> ComposeResult:
        with Vertical(id="ticket-prompt-box"):
            yield Label(f"{self._action_label} — ticket id:")
            yield Input(placeholder="e.g. IO-123", id="ticket-input")
            with Horizontal(id="ticket-prompt-buttons"):
                yield Button("Cancel", id="ticket-cancel", variant="default")
                yield Button("Go", id="ticket-go", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#ticket-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ticket-go":
            self._submit(self.query_one("#ticket-input", Input).value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self, raw: str) -> None:
        ticket = raw.strip()
        if not ticket:
            self.dismiss(None)
        else:
            self.dismiss(ticket)
