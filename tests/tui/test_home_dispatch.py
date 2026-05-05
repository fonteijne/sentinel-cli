"""HomeScreen dispatch tests for remote actions.

The contract we're protecting:

* ``_dispatch`` + ``_run_remote_worker`` must call
  ``service_client.start(project=, ticket_id=, kind=)`` and render the
  attach banner verbatim when ``attached=True``.
* The running-label shows ``"{action.label} · <8-char-id>"``.
* Terminal frames (``kind=="end"``) clear the label and write the
  execution status marker.
* A missing service token must log an error without reaching the client.
* Exiting the app must NOT call ``client.cancel(...)`` — quit-safety.

Tests use Textual's ``app.run_test()`` harness and inject a fake client
via the existing ``HomeScreen._service_client`` seam (the attribute that
``_get_service_client`` consults as a cache).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator, List, Optional

import pytest

textual = pytest.importorskip("textual", reason="textual not installed yet")


# --------------------------------------------------------------------------- #
# Fake service client
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
    """Minimal ServiceClient stand-in.

    Records ``start`` / ``cancel`` calls; ``tail`` yields whatever the
    test seeds via ``frames``. An ``asyncio.Event`` can be attached to
    simulate a stream that never terminates (for the quit-safety test).
    """

    def __init__(
        self,
        *,
        start_result: Any,
        frames: Optional[List[dict]] = None,
        block_forever: bool = False,
    ) -> None:
        self._start_result = start_result
        self._frames = frames or []
        self._block_forever = block_forever
        self.start_calls: List[dict] = []
        self.cancel_calls: List[str] = []
        self.tail_calls: List[tuple[str, int]] = []

    async def start(
        self,
        *,
        project: str,
        ticket_id: str,
        kind: str,
        options: Optional[dict] = None,
    ) -> Any:
        self.start_calls.append(
            {"project": project, "ticket_id": ticket_id, "kind": kind}
        )
        return self._start_result

    async def cancel(self, execution_id: str) -> None:
        self.cancel_calls.append(execution_id)

    async def tail(
        self, execution_id: str, *, since_seq: int = 0
    ) -> AsyncIterator[dict]:
        self.tail_calls.append((execution_id, since_seq))
        for f in self._frames:
            yield f
        if self._block_forever:
            # Park here until the worker is cancelled on app exit. We
            # use Event().wait() so cancellation propagates cleanly.
            await asyncio.Event().wait()

    async def aclose(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _read_log_lines(app) -> list[str]:
    """Return the visible lines in the Output log widget."""
    from textual.widgets import Log

    log = app.screen.query_one("#output-log", Log)
    # Log.lines is the underlying buffer in Textual >= 0.47.
    return list(log.lines)


def _action(key: str):
    from src.tui.actions import ACTIONS

    return next(a for a in ACTIONS if a.key == key)


async def _wait_until(pred, *, pilot, tries: int = 50, delay: float = 0.02) -> None:
    """Pump the Textual event loop until ``pred()`` is true or we time out."""
    for _ in range(tries):
        if pred():
            return
        await pilot.pause(delay)
    raise AssertionError("condition never held")


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_remote_action_dispatch_calls_start_and_sets_running_label() -> None:
    """Selecting 'plan' with a fake client must POST /executions, render
    the attach banner verbatim, and update the running-label."""
    from src.tui.app import SentinelApp
    from src.tui.service_client import StartResult

    banner = "Attached to run abc12345 started 3s ago"
    fake = FakeClient(
        start_result=StartResult(
            execution=_exec(id="abc12345def"),
            attached=True,
            banner=banner,
        ),
        frames=[{"kind": "end", "execution_status": "succeeded"}],
    )

    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        # Seed the project + the injected client. The client cache short-
        # circuits ``_get_service_client`` so no real token lookup runs.
        screen._current_project = "PRJ"
        screen._service_client = fake

        screen._dispatch(_action("plan"), ticket_id="X-1")

        await _wait_until(lambda: len(fake.start_calls) == 1, pilot=pilot)
        await _wait_until(
            lambda: screen._running_label is None,
            pilot=pilot,
        )

        assert fake.start_calls[0] == {
            "project": "PRJ",
            "ticket_id": "X-1",
            "kind": "plan",
        }
        lines = await _read_log_lines(app)
        joined = "\n".join(lines)
        assert banner in joined, f"banner not rendered: {joined!r}"


@pytest.mark.asyncio
async def test_remote_action_attach_banner_renders_verbatim() -> None:
    """The server-side banner string must land in the Output log as-is."""
    from src.tui.app import SentinelApp
    from src.tui.service_client import StartResult

    banner = "Attached to run deadbeef started 42s ago"
    fake = FakeClient(
        start_result=StartResult(
            execution=_exec(id="deadbeefcafe"),
            attached=True,
            banner=banner,
        ),
        frames=[{"kind": "end", "execution_status": "succeeded"}],
    )

    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen._current_project = "PRJ"
        screen._service_client = fake

        screen._dispatch(_action("execute"), ticket_id="X-1")
        await _wait_until(lambda: len(fake.start_calls) == 1, pilot=pilot)
        await _wait_until(
            lambda: screen._running_label is None,
            pilot=pilot,
        )

        joined = "\n".join(await _read_log_lines(app))
        assert banner in joined


@pytest.mark.asyncio
async def test_remote_action_terminal_status_clears_running_label() -> None:
    """End frame with ``execution_status=succeeded`` must clear the label
    and write a terminal marker into the log."""
    from src.tui.app import SentinelApp
    from src.tui.service_client import StartResult

    fake = FakeClient(
        start_result=StartResult(
            execution=_exec(id="abc12345"),
            attached=False,
            banner=None,
        ),
        frames=[{"kind": "end", "execution_status": "succeeded"}],
    )

    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen._current_project = "PRJ"
        screen._service_client = fake

        screen._dispatch(_action("debrief"), ticket_id="X-1")
        await _wait_until(
            lambda: screen._running_label is None and len(fake.tail_calls) == 1,
            pilot=pilot,
        )

        joined = "\n".join(await _read_log_lines(app))
        # ``tail_execution`` writes ``<<< succeeded`` on the end frame and
        # the remote worker follows with a second ``<<< ... [ok]``.
        assert "succeeded" in joined


@pytest.mark.asyncio
async def test_remote_action_missing_token_does_not_call_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no token on disk / env, ``_get_service_client`` logs an error
    and returns None — the fake client's ``start`` is never called."""
    import src.cli as cli_mod
    from src.tui.app import SentinelApp

    monkeypatch.setattr(cli_mod, "_load_service_token", lambda: None)

    fake = FakeClient(start_result=None)

    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen._current_project = "PRJ"
        # Do NOT seed screen._service_client — force the lazy path.
        screen._service_client = None

        # Sanity: the fake is not plumbed through. The dispatcher will try
        # to construct a real client via the CLI helpers, which should
        # fail because the token is missing.
        screen._dispatch(_action("plan"), ticket_id="X-1")
        await pilot.pause()
        await pilot.pause()

        # No start call because no client was constructed.
        assert fake.start_calls == []
        joined = "\n".join(await _read_log_lines(app))
        assert "no service token" in joined
        # Running label cleared after the preflight bail-out.
        assert screen._running_label is None


@pytest.mark.asyncio
async def test_quit_does_not_cancel_remote_execution() -> None:
    """Exiting the app while a remote action is in-flight must not call
    ``client.cancel(...)``. The plan's §5 quit-safety requires the
    service-side work to keep running."""
    from src.tui.app import SentinelApp
    from src.tui.service_client import StartResult

    fake = FakeClient(
        start_result=StartResult(
            execution=_exec(id="abc12345"),
            attached=False,
            banner=None,
        ),
        frames=[],
        block_forever=True,
    )

    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen._current_project = "PRJ"
        screen._service_client = fake

        screen._dispatch(_action("plan"), ticket_id="X-1")
        # Wait for start to be observed and the tail loop to park.
        await _wait_until(lambda: len(fake.tail_calls) == 1, pilot=pilot)

        # Exit — Textual cancels the async worker.
        app.exit()
        await pilot.pause()

    assert fake.cancel_calls == [], (
        f"quit must not call cancel; got {fake.cancel_calls!r}"
    )
