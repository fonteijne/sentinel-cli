"""Processes screen — dashboard over active and recent runs.

Track 4 of the interactive-TUI plan. Reachable via the App-level ``P``
binding, this screen polls ``GET /executions`` every
:data:`ProcessesScreen.POLL_INTERVAL_SEC` seconds and renders two lanes:

* **Current project** — the last 10 rows (client-side cap) per
  ``(ticket_id, kind)`` for the project picked on the home screen, across
  all statuses.
* **Other active processes** — rows with ``status=running`` on projects
  other than the current one, prefixed with their project name.

Attach (Enter) dismisses back to the home screen and delegates to
:meth:`HomeScreen.attach_existing`, which tails the selected execution.
Cancel (``c``) is only meaningful on ``running``/``queued`` rows;
terminal rows just log a hint. Refresh (``r``) forces an immediate fetch.

The poll guards against overlap with ``self._polling``: if a tick fires
while the previous request is still in flight, the new tick is skipped
rather than queued. See the plan's §"Polling cost" gotcha.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

from textual import work
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Label, ListItem, ListView

if TYPE_CHECKING:
    from src.tui.service_client import ExecutionOut, ServiceClient

logger = logging.getLogger(__name__)


# One-char ASCII status markers. Unicode dots (● ✓ ✗) render fine on most
# terminals but the existing codebase strips emoji for fd-capture
# alignment reasons; sticking to plain ASCII here keeps rows aligned
# regardless of the operator's font.
_STATUS_MARKER: dict[str, tuple[str, str]] = {
    "running": ("RUN", "yellow"),
    "cancelling": ("CAN", "yellow"),
    "queued": ("Q", "dim"),
    "succeeded": ("OK", "green"),
    "failed": ("FAIL", "red"),
    "cancelled": ("CXL", "dim"),
}

_ACTIVE_STATUSES = {"running", "queued", "cancelling"}


def _relative_age(when: datetime) -> str:
    """Short relative-age string: ``"3m ago"`` / ``"1h ago"`` / ``"2d ago"``."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - when
    secs = int(delta.total_seconds())
    if secs < 0:
        secs = 0
    if secs < 60:
        return f"{secs}s ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _status_markup(status: str) -> str:
    label, color = _STATUS_MARKER.get(status, (status.upper()[:4], "dim"))
    # Rich markup on a Label — Textual renders this inline.
    return f"[{color}]{label}[/]"


def _cost_str(cost_cents: int) -> str:
    return f"${cost_cents / 100:.2f}"


def _row_label(execution: "ExecutionOut", *, include_project: bool) -> str:
    pieces: List[str] = []
    if include_project:
        pieces.append(f"[{execution.project}]")
    pieces.append(execution.ticket_id)
    pieces.append(execution.kind)
    pieces.append(_status_markup(execution.status))
    pieces.append(_relative_age(execution.started_at))
    pieces.append(f"cost {_cost_str(execution.cost_cents)}")
    return "  ".join(pieces)


class ProcessesScreen(Screen[None]):
    """Cross-project execution dashboard with attach/cancel actions."""

    # Overridable by tests so they don't have to wait 3 seconds per tick.
    POLL_INTERVAL_SEC: float = 3.0

    BINDINGS = [
        ("enter", "attach", "Attach"),
        ("c", "cancel_selected", "Cancel"),
        ("r", "refresh_now", "Refresh"),
        ("escape", "back", "Back"),
    ]

    DEFAULT_CSS = """
    ProcessesScreen {
        overflow: hidden;
    }

    #proc-body {
        height: 1fr;
        width: 100%;
        padding: 0 1;
    }

    .proc-section-title {
        color: $text-muted;
        padding: 1 0 0 0;
    }

    .proc-hint {
        color: $text-muted;
        padding: 0 0 1 1;
    }

    #proc-current-list, #proc-other-list {
        height: auto;
        border: round $primary;
        margin-bottom: 1;
    }

    #proc-current-list:focus, #proc-other-list:focus {
        border: heavy $warning;
    }
    """

    def __init__(
        self,
        *,
        current_project: Optional[str],
        service_client,  # ServiceClient | None — typed loose to avoid import cycle
        attach_callback: Optional[Callable[["ExecutionOut"], None]] = None,
    ) -> None:
        super().__init__()
        self.current_project = current_project
        self._client = service_client
        # The home-screen method that actually fires up ``tail_execution``.
        # Injected for tests; at runtime the App fills it in with
        # ``HomeScreen.attach_existing`` before pushing the screen.
        self._attach_callback = attach_callback

        # Rendered rows by ListItem id → ExecutionOut, so action_attach /
        # action_cancel_selected can resolve the highlighted row back to the
        # domain object without re-querying the service.
        self._rows_by_id: Dict[str, "ExecutionOut"] = {}

        # Skip-ticks-on-overlap guard. Per the plan's §"Polling cost"
        # gotcha, only one in-flight poll at a time. A plain bool is
        # enough — Textual drives the worker on a single asyncio loop, no
        # cross-thread contention.
        self._polling: bool = False
        self._stop_poll: bool = False

        # Signalled by action_refresh_now to wake the polling worker early.
        self._wake_event: Optional[asyncio.Event] = None

    # ------------------------------------------------------------------ compose

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll(id="proc-body"):
            yield Label("Current project", classes="proc-section-title")
            yield Label("", id="proc-current-hint", classes="proc-hint")
            yield ListView(id="proc-current-list")
            yield Label("Other active processes", classes="proc-section-title")
            yield Label("", id="proc-other-hint", classes="proc-hint")
            yield ListView(id="proc-other-list")
        yield Footer()

    def on_mount(self) -> None:
        # Intentionally don't mutate ``app.sub_title`` — Textual's Header
        # schedules an async ``set_title`` when the subtitle changes which
        # queries a HeaderTitle child; on a screen pushed + dismissed in
        # rapid succession that scheduled task fires after the Header was
        # already unmounted and raises NoMatches in stderr. The lane
        # section labels inside the body carry the same information.
        current_hint = self.query_one("#proc-current-hint", Label)
        other_hint = self.query_one("#proc-other-hint", Label)
        if self.current_project is None:
            current_hint.update("[dim]No project selected — pick one on the home screen.[/]")
        else:
            current_hint.update(
                "[dim]Grouped by (ticket, kind); newest 10 per group.[/]"
            )
        other_hint.update(
            "[dim]Running / queued / cancelling on other projects.[/]"
        )

        self.query_one("#proc-current-list", ListView).focus()

        self._wake_event = asyncio.Event()
        self._poll_worker()

    # ------------------------------------------------------------------ poll

    @work(exclusive=True, group="processes-poll")
    async def _poll_worker(self) -> None:
        """Fetch + render loop. Skips ticks while a previous one is in flight."""
        # First fetch synchronously (well, awaited) so the user sees data
        # right away instead of a blank screen for up to POLL_INTERVAL_SEC.
        try:
            while not self._stop_poll:
                if not self._polling:
                    self._polling = True
                    try:
                        await self._fetch_and_render()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — display, don't crash
                        logger.warning("processes-poll: %s", exc)
                    finally:
                        self._polling = False
                # Sleep until the interval elapses OR action_refresh_now
                # pings _wake_event. asyncio.wait_for raises TimeoutError
                # on normal expiry — swallow it and loop.
                wake = self._wake_event
                if wake is None:
                    break
                try:
                    await asyncio.wait_for(
                        wake.wait(), timeout=self.POLL_INTERVAL_SEC
                    )
                except asyncio.TimeoutError:
                    pass
                else:
                    wake.clear()
        except asyncio.CancelledError:
            # Normal teardown path when the screen is dismissed.
            raise

    async def _fetch_and_render(self) -> None:
        client = self._client
        if client is None:
            return

        from src.tui.service_client import ServiceClientError  # local import

        # Lane 1 — current project (skip if none picked).
        current_rows: List["ExecutionOut"] = []
        if self.current_project is not None:
            try:
                items, _cursor = await client.list_executions(
                    project=self.current_project, limit=200
                )
                current_rows = self._group_and_truncate(items)
            except ServiceClientError as exc:
                logger.warning(
                    "processes-poll: current-lane fetch failed: %s", exc
                )

        # Lane 2 — running across all projects, minus current project.
        other_rows: List["ExecutionOut"] = []
        try:
            items, _cursor = await client.list_executions(
                status="running", limit=200
            )
            other_rows = [
                ex for ex in items if ex.project != self.current_project
            ]
        except ServiceClientError as exc:
            logger.warning("processes-poll: other-lane fetch failed: %s", exc)

        await self._render_lanes(current_rows, other_rows)

    @staticmethod
    def _group_and_truncate(
        items: List["ExecutionOut"],
    ) -> List["ExecutionOut"]:
        """Sort by ``started_at`` desc, then cap per ``(ticket_id, kind)``.

        Client-side truncation — see the plan's §"Last 10 per (worktree,
        command)" gotcha. The 200-row overall cap comes from the service.
        """
        ordered = sorted(items, key=lambda e: e.started_at, reverse=True)
        seen: Dict[Tuple[str, str], int] = {}
        result: List["ExecutionOut"] = []
        for ex in ordered:
            key = (ex.ticket_id, ex.kind)
            count = seen.get(key, 0)
            if count >= 10:
                continue
            seen[key] = count + 1
            result.append(ex)
        return result

    async def _render_lanes(
        self,
        current_rows: List["ExecutionOut"],
        other_rows: List["ExecutionOut"],
    ) -> None:
        self._rows_by_id.clear()

        current_list = self.query_one("#proc-current-list", ListView)
        other_list = self.query_one("#proc-other-list", ListView)

        # Textual's ListView.clear() removes children on the next message
        # pump tick; the returned awaitable resolves once the DOM
        # reflects the removal. Await it before appending replacements or
        # the next poll double-inserts widgets with the same id (Textual
        # enforces id uniqueness and logs a warning when violated).
        await current_list.clear()
        for ex in current_rows:
            item_id = f"proc-row-{ex.id}"
            self._rows_by_id[item_id] = ex
            current_list.append(
                ListItem(
                    Label(_row_label(ex, include_project=False)), id=item_id
                )
            )

        await other_list.clear()
        for ex in other_rows:
            item_id = f"proc-row-{ex.id}"
            self._rows_by_id[item_id] = ex
            other_list.append(
                ListItem(
                    Label(_row_label(ex, include_project=True)), id=item_id
                )
            )

    # ------------------------------------------------------------------ actions

    def _highlighted_execution(self) -> Optional["ExecutionOut"]:
        focused = self.focused

        def _from(lst: ListView) -> Optional["ExecutionOut"]:
            idx = lst.index
            # Fall back to the first item when nothing is explicitly
            # highlighted — matches the operator's expectation that Enter
            # on a freshly-rendered list picks the top row.
            if idx is None:
                idx = 0
            if idx < 0 or idx >= len(lst.children):
                return None
            item = lst.children[idx]
            return self._rows_by_id.get(item.id or "")

        # Prefer the focused ListView; fall back to the current-project
        # lane so Enter works even before the list has been focused.
        for list_id in ("#proc-current-list", "#proc-other-list"):
            lst = self.query_one(list_id, ListView)
            if focused is lst:
                result = _from(lst)
                if result is not None:
                    return result
        for list_id in ("#proc-current-list", "#proc-other-list"):
            lst = self.query_one(list_id, ListView)
            result = _from(lst)
            if result is not None:
                return result
        return None

    def action_attach(self) -> None:
        execution = self._highlighted_execution()
        if execution is None:
            return
        # Dismiss first so the attach callback runs against the home
        # screen that it was captured from. The callback sets up
        # tail_execution on a worker.
        callback = self._attach_callback
        self._stop_poll = True
        self.dismiss(None)
        if callback is not None:
            callback(execution)

    def action_cancel_selected(self) -> None:
        execution = self._highlighted_execution()
        if execution is None:
            return
        if execution.status not in _ACTIVE_STATUSES:
            # Nothing to cancel. We don't have a Log widget on this
            # screen, so the hint goes to the logger; a future rev can
            # surface a toast.
            logger.info(
                "cancel ignored: execution %s is %s (not active)",
                execution.id[:8],
                execution.status,
            )
            return
        self._cancel_worker(execution)

    @work(exclusive=False, group="processes-actions")
    async def _cancel_worker(self, execution: "ExecutionOut") -> None:
        client = self._client
        if client is None:
            return
        from src.tui.service_client import (
            ExecutionAlreadyTerminal,
            ServiceClientError,
        )

        # Optimistic flip — the next poll will replace this with the
        # server's canonical view.
        item_id = f"proc-row-{execution.id}"
        self._apply_optimistic_status(item_id, "cancelling")

        try:
            await client.cancel(execution.id)
        except ExecutionAlreadyTerminal as exc:
            logger.info(
                "cancel: %s already terminal (%s)", execution.id[:8], exc
            )
        except ServiceClientError as exc:
            logger.warning(
                "cancel: %s failed (%s)", execution.id[:8], exc
            )

    def _apply_optimistic_status(self, item_id: str, new_status: str) -> None:
        """Mutate the in-memory row + re-render its label for instant feedback."""
        existing = self._rows_by_id.get(item_id)
        if existing is None:
            return
        # ExecutionOut is a dataclass — mutate the status field directly.
        existing.status = new_status

        for list_id in ("#proc-current-list", "#proc-other-list"):
            lst = self.query_one(list_id, ListView)
            for child in lst.children:
                if child.id != item_id:
                    continue
                include_project = list_id == "#proc-other-list"
                new_text = _row_label(existing, include_project=include_project)
                # ListItem children: first Label carries the row text.
                for sub in child.children:
                    if isinstance(sub, Label):
                        sub.update(new_text)
                        break
                break

    def action_refresh_now(self) -> None:
        wake = self._wake_event
        if wake is not None:
            wake.set()

    def action_back(self) -> None:
        self._stop_poll = True
        self.dismiss(None)

    def on_unmount(self) -> None:
        self._stop_poll = True
        wake = self._wake_event
        if wake is not None:
            wake.set()
