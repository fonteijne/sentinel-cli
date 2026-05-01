"""Home screen: project picker, action list, and output log."""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import (
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
)

from src.tui.actions import ACTIONS, ActionDef
from src.tui.screens.ticket import TicketPromptScreen
from src.tui.widgets.run_output import capture_stdout_to_log

logger = logging.getLogger(__name__)


class HomeScreen(Screen[None]):
    """Single-screen launcher: project Select + action ListView + output log."""

    # `q` is bound at App level so it works regardless of which widget has
    # focus (and so Input widgets in modals can type 'q' without quitting).
    BINDINGS = [
        ("r", "focus_actions", "Actions"),
        ("c", "clear_log", "Clear log"),
    ]

    DEFAULT_CSS = """
    #top-bar {
        height: 3;
        padding: 0 1;
    }

    #project-select {
        width: 60;
    }

    #actions {
        width: 50;
        border: round $accent;
        padding: 0 1;
    }

    #output-log {
        border: round $primary;
        padding: 0 1;
    }

    #body {
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._projects: List[str] = []
        self._current_project: Optional[str] = None
        self._running_label: Optional[str] = None  # None ⇒ idle

    # ------------------------------------------------------------------ compose

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="top-bar"):
            yield Label("Project:")
            yield Select[str](
                options=self._project_options(),
                id="project-select",
                allow_blank=True,
                prompt="— none —",
            )
        with Horizontal(id="body"):
            yield ListView(
                *[
                    ListItem(Label(a.label), id=f"action-{a.key}")
                    for a in ACTIONS
                ],
                id="actions",
            )
            yield RichLog(
                id="output-log",
                highlight=True,
                markup=False,
                wrap=True,
                max_lines=5000,
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#actions", ListView).focus()
        self._load_projects()

    # ------------------------------------------------------------------ data

    def _project_options(self) -> List[Tuple[str, str]]:
        from src.config_loader import get_config

        try:
            cfg = get_config()
            projects = cfg.get_all_projects() if hasattr(cfg, "get_all_projects") else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("tui: could not load projects: %s", exc)
            projects = {}
        return [(key, key) for key in sorted(projects.keys())]

    def _load_projects(self) -> None:
        options = self._project_options()
        self._projects = [k for k, _ in options]
        sel = self.query_one("#project-select", Select)
        sel.set_options(options)
        log = self.query_one("#output-log", RichLog)
        if not options:
            log.write(
                "[warn] no projects configured — run `sentinel projects add` "
                "or edit config/config.yaml before using plan/execute/debrief/status."
            )
        else:
            log.write(f"Loaded {len(options)} project(s): {', '.join(self._projects)}")

    # ------------------------------------------------------------------ actions

    def action_focus_actions(self) -> None:
        self.query_one("#actions", ListView).focus()

    def action_clear_log(self) -> None:
        self.query_one("#output-log", RichLog).clear()

    # --------------------------------------------------------------- handlers

    @on(Select.Changed, "#project-select")
    def _on_project_changed(self, event: Select.Changed) -> None:
        value = event.value
        self._current_project = None if value == Select.BLANK else str(value)

    @on(ListView.Selected, "#actions")
    def _on_action_selected(self, event: ListView.Selected) -> None:
        if self._running_label is not None:
            self._log(
                f"[busy] '{self._running_label}' is still running — "
                f"wait for it to finish (or Ctrl-Q to quit)"
            )
            return

        item_id = event.item.id or ""
        key = item_id.removeprefix("action-")
        action = next((a for a in ACTIONS if a.key == key), None)
        if action is None:
            self._log(f"[tui] unknown action: {key}")
            return

        if action.needs_project and self._current_project is None:
            self._log(f"[{action.key}] pick a project first (Project dropdown above)")
            return

        if action.needs_ticket:
            self._prompt_ticket_then_run(action)
        else:
            self._dispatch(action, ticket_id=None)

    def _prompt_ticket_then_run(self, action: ActionDef) -> None:
        def _after_prompt(ticket: Optional[str]) -> None:
            if ticket is None:
                self._log(f"[{action.key}] cancelled")
                return
            self._dispatch(action, ticket_id=ticket)

        self.app.push_screen(TicketPromptScreen(action.label), _after_prompt)

    # --------------------------------------------------------------- dispatch

    def _dispatch(self, action: ActionDef, ticket_id: Optional[str]) -> None:
        self._running_label = action.label
        self._log(
            f"\n>>> {action.label}"
            + (f"  ticket={ticket_id}" if ticket_id else "")
            + (f"  project={self._current_project}" if self._current_project else "")
            + "  (running…)"
        )
        self._run_worker(action, ticket_id)

    @work(thread=True, exclusive=True, group="actions")
    def _run_worker(self, action: ActionDef, ticket_id: Optional[str]) -> None:
        log = self.query_one("#output-log", RichLog)
        app = self.app
        success = False
        try:
            with capture_stdout_to_log(app, log):
                success = action.runner(ticket_id=ticket_id, project=self._current_project)
        except Exception as exc:  # noqa: BLE001
            app.call_from_thread(
                log.write, f"[tui] {action.key} raised: {type(exc).__name__}: {exc}"
            )
        finally:
            suffix = "[ok]" if success else "[failed]"
            app.call_from_thread(log.write, f"<<< {action.label} {suffix}")
            app.call_from_thread(self._mark_idle)

    def _mark_idle(self) -> None:
        self._running_label = None

    # --------------------------------------------------------------- utilities

    def _log(self, line: str) -> None:
        self.query_one("#output-log", RichLog).write(line)
