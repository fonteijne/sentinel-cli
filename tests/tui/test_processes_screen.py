"""Tests for :class:`src.tui.screens.processes.ProcessesScreen`.

Injects a fake service client (no real HTTP) and drives the screen via
Textual's ``app.run_test()`` harness. Each test shortens the polling
interval via :data:`ProcessesScreen.POLL_INTERVAL_SEC` so we don't wait
3 seconds of wall time per tick.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, List, Optional, Tuple

import pytest

pytest.importorskip("textual", reason="textual not installed yet")


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #


def _exec(**overrides: Any):
    from src.tui.service_client import ExecutionOut

    row = {
        "id": "abc12345",
        "ticket_id": "X-1",
        "project": "PRJ",
        "kind": "plan",
        "status": "running",
        "phase": None,
        "started_at": datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        "ended_at": None,
        "cost_cents": 0,
        "error": None,
        "metadata": {},
    }
    row.update(overrides)
    return ExecutionOut(**row)


class FakeClient:
    """Records list_executions / cancel calls; returns seeded responses."""

    def __init__(
        self,
        *,
        responses: Optional[
            dict[tuple[Optional[str], Optional[str]], list]
        ] = None,
    ) -> None:
        # keyed by (project, status) filter combination
        self._responses = responses or {}
        self.list_calls: List[dict] = []
        self.cancel_calls: List[str] = []

    async def list_executions(
        self,
        *,
        project: Optional[str] = None,
        ticket_id: Optional[str] = None,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        limit: Optional[int] = None,
        before: Optional[str] = None,
    ) -> Tuple[list, Optional[str]]:
        self.list_calls.append(
            {
                "project": project,
                "ticket_id": ticket_id,
                "status": status,
                "kind": kind,
                "limit": limit,
                "before": before,
            }
        )
        key = (project, status)
        return list(self._responses.get(key, [])), None

    async def cancel(self, execution_id: str) -> None:
        self.cancel_calls.append(execution_id)

    async def aclose(self) -> None:
        return None


class BlockingClient:
    """list_executions blocks on an ``asyncio.Event`` set by the test.

    Lets us verify the single-in-flight poll guard: while one call is
    parked, subsequent ticks should skip rather than pile up.
    """

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.concurrent = 0
        self.max_concurrent = 0
        self.total_calls = 0

    async def list_executions(self, **kwargs: Any) -> Tuple[list, Optional[str]]:
        self.total_calls += 1
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            await self.release.wait()
        finally:
            self.concurrent -= 1
        return [], None

    async def cancel(self, execution_id: str) -> None:
        return None

    async def aclose(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _wait_until(pred, *, pilot, tries: int = 100, delay: float = 0.01) -> None:
    for _ in range(tries):
        if pred():
            return
        await pilot.pause(delay)
    raise AssertionError("condition never held")


def _push_processes(
    app, *, client, current_project, attach_callback=None,
    poll_interval: float = 0.01,
):
    from src.tui.screens.processes import ProcessesScreen

    # Pin a short polling interval so tests pump quickly. The class
    # attribute is read per-iteration in ``_poll_worker``; individual
    # tests that need a frozen list pass ``poll_interval=60``.
    ProcessesScreen.POLL_INTERVAL_SEC = poll_interval

    screen = ProcessesScreen(
        current_project=current_project,
        service_client=client,
        attach_callback=attach_callback,
    )
    app.push_screen(screen)
    return screen


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_processes_screen_renders_current_project_lane() -> None:
    from src.tui.app import SentinelApp
    from textual.widgets import ListView

    now = datetime.now(timezone.utc)
    client = FakeClient(
        responses={
            ("PRJ", None): [
                _exec(id="row-a", ticket_id="X-1", kind="plan",
                      status="running", started_at=now - timedelta(minutes=1)),
                _exec(id="row-b", ticket_id="X-1", kind="execute",
                      status="succeeded", started_at=now - timedelta(hours=1)),
                _exec(id="row-c", ticket_id="X-2", kind="plan",
                      status="failed", started_at=now - timedelta(hours=2)),
            ],
            (None, "running"): [
                # one running row on another project → goes to lane 2
                _exec(id="row-other", project="OTHER", ticket_id="O-1",
                      kind="plan", status="running",
                      started_at=now - timedelta(minutes=5)),
                # a row on current project in the running-lane result; the
                # screen must filter it out of "other" (lane 1 owns it).
                _exec(id="row-a", project="PRJ", ticket_id="X-1",
                      kind="plan", status="running",
                      started_at=now - timedelta(minutes=1)),
            ],
        }
    )

    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        _push_processes(app, client=client, current_project="PRJ")
        # Wait for the first poll to land.
        await _wait_until(
            lambda: len(client.list_calls) >= 2, pilot=pilot
        )
        await pilot.pause()

        current_list = app.screen.query_one("#proc-current-list", ListView)
        other_list = app.screen.query_one("#proc-other-list", ListView)

        current_ids = {c.id for c in current_list.children}
        other_ids = {c.id for c in other_list.children}

        assert "proc-row-row-a" in current_ids
        assert "proc-row-row-b" in current_ids
        assert "proc-row-row-c" in current_ids
        assert "proc-row-row-other" in other_ids
        # Lane 2 must NOT include the current-project running row.
        assert "proc-row-row-a" not in other_ids


@pytest.mark.asyncio
async def test_processes_screen_single_in_flight_poll_on_slow_backend() -> None:
    from src.tui.app import SentinelApp
    from src.tui.screens.processes import ProcessesScreen

    # Very short interval; the blocking client ensures the first call
    # parks, so subsequent ticks must skip (guarded by self._polling).
    ProcessesScreen.POLL_INTERVAL_SEC = 0.005
    client = BlockingClient()

    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        _push_processes(app, client=client, current_project="PRJ")

        # Let many ticks attempt to fire; the first call is parked.
        for _ in range(30):
            await pilot.pause(0.005)

        # At most one call in flight at any time.
        assert client.max_concurrent <= 1, (
            f"expected single in-flight; saw max={client.max_concurrent}"
        )

        # Release and drain so teardown is clean.
        client.release.set()
        await pilot.pause()


@pytest.mark.asyncio
async def test_processes_screen_attach_dismisses_and_calls_attach_callback() -> None:
    from src.tui.app import SentinelApp
    from textual.widgets import ListView

    row = _exec(id="target-id", ticket_id="X-9", kind="plan", status="running")
    client = FakeClient(
        responses={("PRJ", None): [row], (None, "running"): []}
    )
    captured: list = []

    def _on_attach(execution) -> None:
        captured.append(execution)

    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        _push_processes(
            app, client=client, current_project="PRJ", attach_callback=_on_attach
        )

        def _lane_has_row() -> bool:
            from src.tui.screens.processes import ProcessesScreen

            if not isinstance(app.screen, ProcessesScreen):
                return False
            try:
                lst = app.screen.query_one("#proc-current-list", ListView)
            except Exception:
                return False
            return len(lst.children) == 1

        await _wait_until(_lane_has_row, pilot=pilot)

        # Trigger the attach action directly — avoids any key-routing
        # flakiness around focus state in the headless harness.
        proc_screen = app.screen
        # Ensure a row is highlighted and the list is focused so the
        # action's highlighted-execution lookup resolves.
        current_list = proc_screen.query_one("#proc-current-list", ListView)
        current_list.focus()
        current_list.index = 0
        await pilot.pause()
        proc_screen.action_attach()

        # Pump until the attach callback fires and the screen pops.
        await _wait_until(
            lambda: len(captured) == 1
            and app.screen.__class__.__name__ != "ProcessesScreen",
            pilot=pilot,
        )
        assert captured[0].id == "target-id"


@pytest.mark.asyncio
async def test_processes_screen_cancel_calls_client_cancel_for_running_row() -> None:
    from src.tui.app import SentinelApp
    from textual.widgets import ListView

    running_row = _exec(id="run-id", ticket_id="X-1", status="running")
    terminal_row = _exec(id="done-id", ticket_id="X-2", status="succeeded")
    client = FakeClient(
        responses={
            ("PRJ", None): [running_row, terminal_row],
            (None, "running"): [],
        }
    )

    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        _push_processes(
            app, client=client, current_project="PRJ", poll_interval=60.0
        )

        def _lane_has_two() -> bool:
            from src.tui.screens.processes import ProcessesScreen

            if not isinstance(app.screen, ProcessesScreen):
                return False
            try:
                lst = app.screen.query_one("#proc-current-list", ListView)
            except Exception:
                return False
            return len(lst.children) == 2

        await _wait_until(_lane_has_two, pilot=pilot)

        proc_screen = app.screen
        current_list = proc_screen.query_one("#proc-current-list", ListView)

        # Highlight the running row (index 0) and cancel.
        current_list.focus()
        current_list.index = 0
        await pilot.pause()
        proc_screen.action_cancel_selected()
        await _wait_until(
            lambda: client.cancel_calls == ["run-id"], pilot=pilot
        )

        # Highlight the terminal row and try again — must NOT cancel.
        current_list.index = 1
        await pilot.pause()
        proc_screen.action_cancel_selected()
        await pilot.pause()
        await pilot.pause()
        assert client.cancel_calls == ["run-id"], client.cancel_calls


@pytest.mark.asyncio
async def test_processes_screen_refresh_triggers_immediate_fetch() -> None:
    from src.tui.app import SentinelApp
    from src.tui.screens.processes import ProcessesScreen

    # Keep the timer long so only manual refresh fires extra calls.
    ProcessesScreen.POLL_INTERVAL_SEC = 10.0

    client = FakeClient(
        responses={("PRJ", None): [], (None, "running"): []}
    )

    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        _push_processes(app, client=client, current_project="PRJ")
        # Wait for the first round-trip (two calls: lane 1 + lane 2).
        await _wait_until(lambda: len(client.list_calls) >= 2, pilot=pilot)
        baseline = len(client.list_calls)

        app.screen.action_refresh_now()
        await _wait_until(
            lambda: len(client.list_calls) >= baseline + 2, pilot=pilot
        )
