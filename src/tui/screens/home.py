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
    Log,
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
        ("p", "focus_project", "Project"),
        ("r", "focus_actions", "Actions"),
        ("c", "clear_log", "Clear log"),
    ]

    DEFAULT_CSS = """
    HomeScreen {
        overflow: hidden;
    }

    /* dock: top pins the top-bar above the body unconditionally — no
       amount of body growth can push it off-screen. */
    #top-bar {
        dock: top;
        height: 3;
        padding: 0 1;
    }

    #project-select {
        width: 60;
    }

    /* Highlight the Select when it (or its open menu) has focus. */
    #project-select:focus-within > SelectCurrent {
        border: heavy $warning;
    }

    #body {
        height: 1fr;
        width: 100%;
        overflow: hidden;
    }

    /* Dim default borders; swap to heavy + bright $warning on focus so
       the active panel is unmistakable. border_title is set on each
       widget so the label stays inside the border. */
    #actions {
        width: 50;
        height: 100%;
        border: round $primary;
        border-title-color: $text-muted;
        padding: 0 1;
    }

    #actions:focus-within {
        border: heavy $warning;
        border-title-color: $warning;
        border-title-style: bold;
    }

    #output-log {
        width: 1fr;
        height: 100%;
        border: round $primary;
        border-title-color: $text-muted;
        padding: 0 1;
        overflow-x: hidden;
        overflow-y: auto;
    }

    #output-log:focus {
        border: heavy $warning;
        border-title-color: $warning;
        border-title-style: bold;
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
            yield Log(
                id="output-log",
                max_lines=5000,
                auto_scroll=True,
            )
        yield Footer()

    def on_mount(self) -> None:
        # Label each panel inside its own border so the active panel is
        # obvious even in a narrow terminal.
        self.query_one("#actions", ListView).border_title = "Actions"
        self.query_one("#output-log", Log).border_title = "Output"

        self._load_projects()
        # Land focus on the Project dropdown if nothing is picked yet so the
        # first Tab/Enter press does what a new user expects. If a project
        # is already selected (e.g. after returning to this screen), focus
        # the action list so they can get to work.
        if self._current_project is None:
            self.query_one("#project-select", Select).focus()
        else:
            self.query_one("#actions", ListView).focus()

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
        log = self.query_one("#output-log", Log)
        if not options:
            log.write_line(
                "[warn] no projects configured — run `sentinel projects add` "
                "or edit config/config.yaml before using plan/execute/debrief/status."
            )
        else:
            log.write_line(
                f"Loaded {len(options)} project(s): {', '.join(self._projects)}"
            )

    # ------------------------------------------------------------------ actions

    def action_focus_actions(self) -> None:
        self.query_one("#actions", ListView).focus()

    def action_focus_project(self) -> None:
        self.query_one("#project-select", Select).focus()

    def action_clear_log(self) -> None:
        self.query_one("#output-log", Log).clear()

    # --------------------------------------------------------------- handlers

    @on(Select.Changed, "#project-select")
    def _on_project_changed(self, event: Select.Changed) -> None:
        value = event.value
        self._current_project = None if value == Select.BLANK else str(value)
        # Auto-advance to the action list on a real project pick so the
        # next keystroke starts the workflow. Stay put when the operator
        # clears the selection (Select.BLANK) — they're probably about to
        # pick a different project.
        if self._current_project is not None:
            self.query_one("#actions", ListView).focus()

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

    # Plan and debrief can start on a brand-new ticket; execute requires
    # an existing worktree (the workflow `execute()` raises
    # "worktree not found for {ticket}; run 'sentinel plan' first").
    _ACTIONS_ALLOWING_NEW: tuple[str, ...] = ("plan", "debrief")

    def _prompt_ticket_then_run(self, action: ActionDef) -> None:
        prefix = self._jira_prefix_for_current_project()
        existing = self._existing_worktree_tickets()

        def _after_prompt(ticket: Optional[str]) -> None:
            if ticket is None:
                self._log(f"[{action.key}] cancelled")
                return
            resolved = self._resolve_ticket_id(ticket, prefix)
            if resolved != ticket:
                self._log(
                    f"[{action.key}] resolved '{ticket}' → '{resolved}' "
                    f"(project prefix {prefix})"
                )
            self._dispatch(action, ticket_id=resolved)

        self.app.push_screen(
            TicketPromptScreen(
                action.label,
                project_prefix=prefix,
                existing_tickets=existing,
                allow_new=action.key in self._ACTIONS_ALLOWING_NEW,
            ),
            _after_prompt,
        )

    def _existing_worktree_tickets(self) -> list[str]:
        """Worktrees on disk for the current project, sorted."""
        if self._current_project is None:
            return []
        try:
            from src.worktree_manager import WorktreeManager

            mgr = WorktreeManager()
            tickets = mgr.list_worktrees(self._current_project)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[tui] could not list worktrees: {exc}")
            return []
        return sorted(tickets)

    def _jira_prefix_for_current_project(self) -> Optional[str]:
        """Resolve the Jira project-key prefix used to complete bare tickets.

        Falls back to the local project key if `jira_project_key` isn't
        configured (ConfigLoader defaults it to the project key on
        `projects add`, so they usually match).
        """
        if self._current_project is None:
            return None
        try:
            from src.config_loader import get_config

            cfg = get_config()
            pcfg = cfg.get_project_config(self._current_project) or {}
        except Exception:  # noqa: BLE001
            return self._current_project
        return str(pcfg.get("jira_project_key") or self._current_project)

    @staticmethod
    def _resolve_ticket_id(raw: str, prefix: Optional[str]) -> str:
        """Prepend the Jira prefix when the user typed a bare number.

        - "356" + DHLEXC → "DHLEXC-356"
        - "DHLEXC-356"   → "DHLEXC-356"   (already qualified)
        - "356" + None   → "356"          (no project selected)
        - empty/whitespace → returned as-is (prompt layer already rejects this)
        """
        s = raw.strip()
        if not s or prefix is None:
            return s
        if "-" in s:
            return s
        return f"{prefix}-{s}"

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
        log = self.query_one("#output-log", Log)
        app = self.app
        success = False
        try:
            with capture_stdout_to_log(app, log):
                success = action.runner(ticket_id=ticket_id, project=self._current_project)
        except Exception as exc:  # noqa: BLE001
            app.call_from_thread(
                log.write_line,
                f"[tui] {action.key} raised: {type(exc).__name__}: {exc}",
            )
        finally:
            suffix = "[ok]" if success else "[failed]"
            app.call_from_thread(log.write_line, f"<<< {action.label} {suffix}")
            app.call_from_thread(self._mark_idle)

    def _mark_idle(self) -> None:
        self._running_label = None

    # --------------------------------------------------------------- utilities

    def _log(self, line: str) -> None:
        self.query_one("#output-log", Log).write_line(line)
