"""Home screen: project picker, action list, and output log."""

from __future__ import annotations

import asyncio
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
from src.tui.widgets.run_output import capture_stdout_to_log, tail_execution

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
        # Lazy: constructed on first remote action. See
        # ``_get_service_client`` / ``on_unmount``.
        self._service_client = None  # type: ignore[assignment]

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
        """Worktrees on disk for the current project, deduplicated and sorted.

        ``WorktreeManager.list_worktrees`` occasionally returns the same
        ticket twice (seen after a failed plan that left a partial
        worktree registration). Dedup here so the ticket picker's
        ListView doesn't choke on duplicate child ids.
        """
        if self._current_project is None:
            return []
        try:
            from src.worktree_manager import WorktreeManager

            mgr = WorktreeManager()
            tickets = mgr.list_worktrees(self._current_project)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[tui] could not list worktrees: {exc}")
            return []
        return sorted(set(tickets))

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
        # Shift focus to the Output panel so the operator can scroll through
        # the stream while it lands. `r` or Tab returns to Actions afterwards.
        self.query_one("#output-log", Log).focus()

        if action.kind == "remote":
            # Pre-flight the token discovery before spawning a worker so the
            # error message surfaces synchronously (and we don't leave the
            # running-label stuck if the token is missing).
            client = self._get_service_client()
            if client is None:
                self._mark_idle()
                return
            if action.remote_kind is None or ticket_id is None:
                # Guard — every remote action in ACTIONS sets both. Kept so
                # ``mypy --strict`` style refactors don't tunnel a bug past.
                self._log(f"[tui] {action.key}: missing remote_kind or ticket_id")
                self._mark_idle()
                return
            self._run_remote_worker(action, ticket_id)
        else:
            self._run_local_worker(action, ticket_id)

    @work(thread=True, exclusive=True, group="actions")
    def _run_local_worker(
        self, action: ActionDef, ticket_id: Optional[str]
    ) -> None:
        """In-process runner for validate / status / drain.

        Runs on a thread worker and uses fd-level capture to relay
        stdout/stderr into the Log widget. Writes to the widget cross
        thread boundaries via ``app.call_from_thread``.
        """
        log = self.query_one("#output-log", Log)
        app = self.app
        success = False
        runner = action.runner
        if runner is None:
            app.call_from_thread(
                log.write_line,
                f"[tui] local action '{action.key}' has no runner",
            )
            app.call_from_thread(self._mark_idle)
            return
        try:
            with capture_stdout_to_log(app, log):
                success = runner(ticket_id=ticket_id, project=self._current_project)
        except Exception as exc:  # noqa: BLE001
            app.call_from_thread(
                log.write_line,
                f"[tui] {action.key} raised: {type(exc).__name__}: {exc}",
            )
        finally:
            suffix = "[ok]" if success else "[failed]"
            app.call_from_thread(log.write_line, f"<<< {action.label} {suffix}")
            app.call_from_thread(self._mark_idle)

    @work(exclusive=True, group="actions")
    async def _run_remote_worker(
        self, action: ActionDef, ticket_id: str
    ) -> None:
        """Async runner for plan / execute / debrief.

        POSTs /executions, streams the resulting WS. On
        :class:`asyncio.CancelledError` (TUI quit), propagates without
        cancelling the service-side execution — quit-safety.
        """
        log = self.query_one("#output-log", Log)
        client = self._service_client
        if client is None:
            # Re-check defensively; _dispatch already preflighted.
            log.write_line("[tui] internal error: no service client")
            self._mark_idle()
            return

        # Late import keeps the home module importable without httpx /
        # websockets deps in environments that only exercise smoke tests.
        from src.tui.service_client import (
            ExecutionAlreadyTerminal,
            ServiceClientError,
        )

        assert action.remote_kind is not None  # noqa: S101 — guarded in _dispatch
        try:
            try:
                result = await client.start(
                    project=self._current_project or "",
                    ticket_id=ticket_id,
                    kind=action.remote_kind,
                )
            except ExecutionAlreadyTerminal as exc:
                log.write_line(f"[tui] {action.key}: {exc}")
                return
            except ServiceClientError as exc:
                log.write_line(
                    f"[tui] {action.key} failed to start: {type(exc).__name__}: {exc}"
                )
                return

            execution_id = result.execution.id
            if result.attached and result.banner:
                # Render verbatim — server-formatted attach banner.
                log.write_line(result.banner)
            self._running_label = f"{action.label} · {execution_id[:8]}"

            status = await tail_execution(self.app, log, client, execution_id)
            suffix = "[ok]" if status == "succeeded" else f"[{status or 'failed'}]"
            log.write_line(f"<<< {action.label} {suffix}")
        except asyncio.CancelledError:
            # Quit path — Textual cancels the async worker on app exit. We
            # deliberately do NOT call client.cancel(): the plan's §5
            # quit-safety requires the service-side work to keep running.
            raise
        finally:
            self._mark_idle()

    def _mark_idle(self) -> None:
        self._running_label = None

    # ------------------------------------------------------------ attach_existing

    def attach_existing(self, execution) -> None:  # type: ignore[no-untyped-def]
        """Attach the Output log to an already-running execution.

        Called by :class:`~src.tui.screens.processes.ProcessesScreen` when
        the operator picks a row and hits Enter. Mirrors the tail portion
        of :meth:`_run_remote_worker` without the ``start()`` round trip —
        the execution row already exists on the service.

        Busy-guard: if another action is in flight (``_running_label`` is
        set), we log a hint and return. The operator can clear the log
        and retry once the current action finishes.
        """
        if self._running_label is not None:
            self._log(
                f"[busy] '{self._running_label}' is still running — "
                "finish it before attaching to another run"
            )
            return
        client = self._get_service_client()
        if client is None:
            return
        self._attach_existing_worker(execution)

    @work(exclusive=True, group="actions")
    async def _attach_existing_worker(self, execution) -> None:  # type: ignore[no-untyped-def]
        log = self.query_one("#output-log", Log)
        client = self._service_client
        if client is None:
            log.write_line("[tui] internal error: no service client")
            return

        kind = execution.kind
        ticket_id = execution.ticket_id
        short_id = execution.id[:8]
        self._log(
            f"\n>>> Attach · {kind}  ticket={ticket_id}  id={short_id}  (running…)"
        )
        self._log(
            f"[attach] tailing {short_id} ({kind}, {execution.status})"
        )
        self._running_label = f"Attach · {short_id}"
        log.focus()
        try:
            status = await tail_execution(
                self.app, log, client, execution.id
            )
            suffix = "[ok]" if status == "succeeded" else f"[{status or 'failed'}]"
            log.write_line(f"<<< Attach · {short_id} {suffix}")
        except asyncio.CancelledError:
            raise
        finally:
            self._mark_idle()

    # --------------------------------------------------------------- service client

    def _get_service_client(self):  # type: ignore[no-untyped-def]
        """Return the cached :class:`ServiceClient`, constructing on first use.

        Token discovery reuses the CLI helpers
        (:func:`src.cli._service_base_url`, :func:`src.cli._load_service_token`)
        so we never duplicate env-var / token-file logic. Missing token
        logs a visible error and returns ``None`` — the caller must abort.
        """
        if self._service_client is not None:
            return self._service_client

        from src.cli import _load_service_token, _service_base_url
        from src.tui.service_client import ServiceClient

        token = _load_service_token()
        if not token:
            self._log(
                "[tui] no service token found — run `sentinel serve` to "
                "bootstrap it, then retry"
            )
            return None
        base_url = _service_base_url()
        try:
            self._service_client = ServiceClient(base_url=base_url, token=token)
        except Exception as exc:  # noqa: BLE001
            self._log(
                f"[tui] could not build service client: "
                f"{type(exc).__name__}: {exc}"
            )
            return None
        return self._service_client

    def on_unmount(self) -> None:
        """Best-effort cleanup of the async HTTP client on screen teardown."""
        client = self._service_client
        if client is None:
            return
        self._service_client = None
        try:
            # Schedule ``aclose`` on the running loop; unmount is sync so we
            # can't await. ``get_running_loop`` avoids the Py3.12+ deprecation
            # on ``get_event_loop`` when no loop is current.
            import asyncio as _asyncio

            _asyncio.get_running_loop().create_task(client.aclose())
        except RuntimeError:
            # No running loop — unmount outside the app lifecycle (e.g. a
            # synchronous teardown path). Process exit will close sockets.
            pass

    # --------------------------------------------------------------- utilities

    def _log(self, line: str) -> None:
        self.query_one("#output-log", Log).write_line(line)
