"""Modal screen that prompts for a Jira ticket id.

Shows existing worktrees (tickets) for the active project as a quick-pick
list plus a text input for new / arbitrary ids. For actions where creating
a new ticket/worktree makes sense (``plan`` / ``debrief``) an extra
"+ New ticket" row in the list focuses the input. For ``execute`` the
list is still shown but no "+ New ticket" affordance appears — execute
requires a plan/worktree to already exist (the CLI errors cleanly if one
doesn't).
"""

from __future__ import annotations

from typing import List, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView


_NEW_TICKET_ITEM_ID = "ticket-item-new"


class TicketPromptScreen(ModalScreen[Optional[str]]):
    """Ask for a ticket id and return it (trimmed) or None on cancel."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    TicketPromptScreen {
        align: center middle;
    }

    #ticket-prompt-box {
        width: 72;
        height: auto;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }

    #ticket-prompt-box Label {
        margin-bottom: 1;
    }

    #ticket-list {
        height: auto;
        max-height: 10;
        border: round $primary;
        margin-bottom: 1;
    }

    #ticket-list > ListItem#ticket-item-new {
        color: $success;
    }

    #ticket-input {
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

    def __init__(
        self,
        action_label: str,
        project_prefix: Optional[str] = None,
        existing_tickets: Optional[List[str]] = None,
        allow_new: bool = True,
    ) -> None:
        super().__init__()
        self._action_label = action_label
        self._project_prefix = project_prefix
        self._existing_tickets = list(existing_tickets or [])
        self._allow_new = allow_new

    # ----------------------------------------------------------------- compose

    def compose(self) -> ComposeResult:
        placeholder = (
            f"e.g. 356  (prefixed with {self._project_prefix}-)"
            if self._project_prefix
            else "e.g. IO-123"
        )
        hint = (
            f"{self._action_label} — pick an existing worktree or type a ticket id"
            if self._existing_tickets
            else (
                f"{self._action_label} — ticket id "
                f"(a bare number becomes {self._project_prefix}-<n>):"
                if self._project_prefix
                else f"{self._action_label} — ticket id:"
            )
        )

        with Vertical(id="ticket-prompt-box"):
            yield Label(hint)
            if self._existing_tickets or self._allow_new:
                yield ListView(
                    *self._list_items(), id="ticket-list"
                )
            yield Input(placeholder=placeholder, id="ticket-input")
            with Horizontal(id="ticket-prompt-buttons"):
                yield Button("Cancel", id="ticket-cancel", variant="default")
                yield Button("Go", id="ticket-go", variant="primary")

    def _list_items(self) -> List[ListItem]:
        items: List[ListItem] = []
        for ticket in self._existing_tickets:
            items.append(
                ListItem(Label(ticket), id=f"ticket-item-{ticket}")
            )
        if self._allow_new:
            items.append(
                ListItem(Label("+ New ticket"), id=_NEW_TICKET_ITEM_ID)
            )
        return items

    # ------------------------------------------------------------------ mount

    def on_mount(self) -> None:
        # Focus the list if there's anything to pick from; else the input.
        try:
            lst = self.query_one("#ticket-list", ListView)
            lst.focus()
        except Exception:
            self.query_one("#ticket-input", Input).focus()

    # ---------------------------------------------------------------- events

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ticket-go":
            self._submit(self.query_one("#ticket-input", Input).value)
        else:
            self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or ""
        if item_id == _NEW_TICKET_ITEM_ID:
            # Jump to the input so the operator can type a new id.
            self.query_one("#ticket-input", Input).focus()
            return
        if item_id.startswith("ticket-item-"):
            ticket = item_id.removeprefix("ticket-item-")
            self.dismiss(ticket)

    def action_cancel(self) -> None:
        self.dismiss(None)

    # ---------------------------------------------------------------- submit

    def _submit(self, raw: str) -> None:
        ticket = raw.strip()
        if not ticket:
            self.dismiss(None)
        else:
            self.dismiss(ticket)
