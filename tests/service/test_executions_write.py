"""HTTP tests for Track 2 write endpoints: attach-or-start + cancel.

These cover the handlers on ``executions.write_router``:

  * ``POST /executions``             (``create_or_attach_execution``)
  * ``POST /executions/{id}/cancel`` (``cancel_execution``)

Both are mounted under the plan 05 write bucket (bearer auth + per-token
rate limit + audit log). Auth is reused via the central ``authed_client``
fixture from :mod:`tests.service.conftest`; a local variant swaps in a
``FakeSupervisor`` via ``app.dependency_overrides[get_supervisor]`` so we
never spawn a real worker subprocess.

The orchestrator is left as-is: it writes via the same per-request SQLite
connection the repo uses, so events land in the test DB and can be inspected
via the read endpoints if needed. The tests here are pure black-box HTTP +
direct-DB seeding for status rows that cannot be reached through the API
alone (``SUCCEEDED`` / ``FAILED`` / ``CANCELLING``).
"""

from __future__ import annotations

import re
import time
from typing import Iterator, Optional

import pytest
from fastapi.testclient import TestClient

from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.repository import ExecutionRepository
from src.core.persistence import connect

from tests.service.conftest import TEST_TOKEN


# ---------------------------------------------------------------------------
# Fakes + fixtures
# ---------------------------------------------------------------------------


class _FakeSupervisor:
    """Minimal ``Supervisor`` stand-in.

    * ``spawn_calls`` / ``cancel_calls`` — ordered log of invocations.
    * ``raise_on_spawn`` — set to an Exception to force the 503 path. Using
      ``OSError`` exercises the non-``RuntimeError`` branch the plan calls out
      explicitly (Docker socket unreachable class of failure).
    """

    def __init__(self) -> None:
        self.spawn_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.raise_on_spawn: Optional[Exception] = None

    def spawn(self, execution_id: str) -> int:
        self.spawn_calls.append(execution_id)
        if self.raise_on_spawn is not None:
            raise self.raise_on_spawn
        return 99999

    def cancel(self, execution_id: str) -> None:
        # NB: the real supervisor flips RUNNING → CANCELLING when it knows
        # the PID. For tests that seed a RUNNING row directly via the repo,
        # the supervisor has no PID mapping, so real .cancel() is a no-op on
        # unknown PIDs — mirroring that here keeps the observed row state
        # honest (see test_cancel_running_row_200_and_signals_once).
        self.cancel_calls.append(execution_id)


@pytest.fixture
def fake_supervisor() -> _FakeSupervisor:
    return _FakeSupervisor()


@pytest.fixture
def client(authed_env, fake_supervisor) -> Iterator[TestClient]:
    """``authed_client`` variant with ``get_supervisor`` overridden.

    Mirrors the pattern in ``test_commands_routes.py``. ``authed_env`` has
    already set ``SENTINEL_SERVICE_TOKEN`` / ``SENTINEL_DB_PATH`` /
    ``SENTINEL_LOGS_DIR``, so the lifespan opens a pristine per-test DB.
    """
    from src.service.app import create_app
    from src.service.deps import get_supervisor

    app = create_app()
    app.dependency_overrides[get_supervisor] = lambda: fake_supervisor
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        yield c


def _fetch_rows() -> list:
    """Pull every executions row via a short-lived connection.

    Used by tests that assert "no row was created" for rejected requests.
    Keeps it read-only — the HTTP client owns writes.
    """
    conn = connect()
    try:
        return ExecutionRepository(conn).list()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /executions — fresh start
# ---------------------------------------------------------------------------


def test_fresh_start_returns_201_and_spawns_once(client, fake_supervisor):
    """Test 1 — no active row, fresh row created, supervisor.spawn called once."""
    resp = client.post(
        "/executions",
        json={
            "project": "acme",
            "ticket_id": "PROJ-1",
            "kind": "execute",
            "options": {"revise": True},
        },
    )
    assert resp.status_code == 201, resp.text

    body = resp.json()
    # ExecutionStartResponse shape.
    assert body["attached"] is False
    assert body["banner"] is None
    assert "execution" in body

    ex = body["execution"]
    assert ex["ticket_id"] == "PROJ-1"
    assert ex["project"] == "acme"
    assert ex["kind"] == "execute"
    assert ex["status"] == "queued"

    assert fake_supervisor.spawn_calls == [ex["id"]]

    # Row exists.
    rows = _fetch_rows()
    assert [r.id for r in rows] == [ex["id"]]


# ---------------------------------------------------------------------------
# POST /executions — attach path
# ---------------------------------------------------------------------------


_BANNER_RE = re.compile(r"^Attached to run [0-9a-f]{8} started \d+[smhd] ago$")


def test_attach_to_active_run_returns_200_with_banner(client, fake_supervisor):
    """Test 2 — second POST with same (project, ticket_id, kind) attaches.

    Banner must match ``^Attached to run [0-9a-f]{8} started \\d+[smh] ago$``.
    The plan's regex listed ``[smh]``; the implementation also emits ``d`` for
    day-old runs, but day-old can't happen in a unit test, so we stick with
    the plan's alphabet plus ``d`` defensively — matches the handler source.
    """
    body = {"project": "acme", "ticket_id": "PROJ-ATTACH", "kind": "plan"}

    r1 = client.post("/executions", json=body)
    assert r1.status_code == 201, r1.text
    first_id = r1.json()["execution"]["id"]

    # Row is still QUEUED (fake supervisor never spawns a real worker that
    # would transition it to RUNNING). ``find_active`` returns newest queued-
    # or-running row, so this is a valid attach target.
    r2 = client.post("/executions", json=body)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["attached"] is True
    assert body2["execution"]["id"] == first_id
    assert body2["banner"] is not None
    assert _BANNER_RE.match(body2["banner"]), body2["banner"]

    # Exactly one spawn call across both POSTs.
    assert fake_supervisor.spawn_calls == [first_id]

    # One row, not two.
    rows = _fetch_rows()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------


def test_bad_kind_returns_422_and_no_row_created(client, fake_supervisor):
    """Test 3 — Pydantic rejects an unknown ``kind`` with 422.

    ``ExecutionKind`` is a str-Enum; Pydantic yields 422 on an invalid value.
    If that ever changes to 400 via a handler-level catch, this test will
    signal which behaviour to pin.
    """
    resp = client.post(
        "/executions",
        json={"project": "acme", "ticket_id": "PROJ-1", "kind": "not-a-real-kind"},
    )
    assert resp.status_code == 422, resp.text
    assert _fetch_rows() == []
    assert fake_supervisor.spawn_calls == []


def test_oversized_options_returns_413_and_no_row(client, fake_supervisor):
    """Test 4 — options JSON > 8 KiB → 413, no row, no spawn.

    The handler encodes ``options`` via ``json.dumps`` and compares the length
    against ``OPTIONS_MAX_BYTES`` (8 * 1024). ``"x" * 20000`` lands well above
    that ceiling regardless of JSON quoting overhead.
    """
    resp = client.post(
        "/executions",
        json={
            "project": "acme",
            "ticket_id": "PROJ-1",
            "kind": "execute",
            "options": {"blob": "x" * 20000},
        },
    )
    assert resp.status_code == 413, resp.text
    assert _fetch_rows() == []
    assert fake_supervisor.spawn_calls == []


# ---------------------------------------------------------------------------
# POST /executions/{id}/cancel
# ---------------------------------------------------------------------------


def test_cancel_running_row_202_and_signals_once(client, fake_supervisor):
    """Test 5 — seed a RUNNING row, cancel → 202, supervisor.cancel once.

    The cancel handler offloads to ``run_in_executor``, so we briefly poll
    ``cancel_calls`` before asserting.
    """
    # Seed a RUNNING row directly (no API path flips QUEUED → RUNNING in
    # unit tests because no real worker starts). The handler only cares
    # about terminal vs non-terminal, not QUEUED vs RUNNING, except for the
    # "was_queued" branch covered in the next test.
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        ex = repo.create("PROJ-RUN", "acme", ExecutionKind.EXECUTE)
        repo.set_status(ex.id, ExecutionStatus.RUNNING)
    finally:
        conn.close()

    resp = client.post(f"/executions/{ex.id}/cancel")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["signalled"] is True
    assert body["execution"]["id"] == ex.id

    # Cancel is dispatched via run_in_executor; give it a tick.
    for _ in range(50):
        if fake_supervisor.cancel_calls:
            break
        time.sleep(0.05)
    assert fake_supervisor.cancel_calls == [ex.id]


def test_cancel_queued_row_marks_cancelled_directly(client, fake_supervisor):
    """Test 6 — QUEUED rows never spawn a worker, so supervisor.cancel is a
    no-op on unknown PIDs; the handler itself flips the row to CANCELLED and
    publishes the terminal event.
    """
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        ex = repo.create("PROJ-Q", "acme", ExecutionKind.EXECUTE)
        assert ex.status == ExecutionStatus.QUEUED
    finally:
        conn.close()

    resp = client.post(f"/executions/{ex.id}/cancel")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["signalled"] is True
    # The response body reflects the post-cancel state (handler re-fetches).
    assert body["execution"]["status"] == "cancelled"

    # Wait for the run_in_executor cancel dispatch before inspecting the DB;
    # the row flip happens *after* the threadpool call returns.
    for _ in range(50):
        if fake_supervisor.cancel_calls:
            break
        time.sleep(0.05)
    assert fake_supervisor.cancel_calls == [ex.id]

    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        refreshed = repo.get(ex.id)
    finally:
        conn.close()
    assert refreshed is not None
    assert refreshed.status == ExecutionStatus.CANCELLED


@pytest.mark.parametrize(
    "terminal_status",
    [
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
    ],
)
def test_cancel_terminal_row_returns_409(
    client, fake_supervisor, terminal_status
):
    """Test 7 — a row in any terminal status yields 409; supervisor untouched."""
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        ex = repo.create("PROJ-T", "acme", ExecutionKind.EXECUTE)
        repo.record_ended(ex.id, terminal_status)
    finally:
        conn.close()

    resp = client.post(f"/executions/{ex.id}/cancel")
    assert resp.status_code == 409, resp.text
    # Detail echoes the current status so the TUI can render something useful.
    assert terminal_status.value in resp.json()["detail"]
    assert fake_supervisor.cancel_calls == []


def test_cancel_unknown_id_returns_404(client, fake_supervisor):
    """Test 8 — unknown execution id → 404, supervisor never called."""
    resp = client.post("/executions/no-such-id/cancel")
    assert resp.status_code == 404, resp.text
    assert fake_supervisor.cancel_calls == []


def test_cancel_idempotency_second_call_on_cancelling_row_also_202(
    client, fake_supervisor
):
    """Test 9 — cancel on a RUNNING row, then cancel again while the row sits
    in CANCELLING (the handler-observable mid-state between the supervisor's
    ``set_status(CANCELLING)`` and the worker actually exiting). Both return
    202; CANCELLING is not in ``_TERMINAL_STATUSES``.

    Because the fake supervisor records the call but does *not* flip the row
    to CANCELLING itself (the real supervisor does that inside .cancel()), we
    emulate the mid-cancel state by setting CANCELLING on the row before the
    second POST. That matches the observable state the TUI would see if the
    user double-tapped cancel while the real supervisor's SIGTERM grace
    window was open.
    """
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        ex = repo.create("PROJ-IDEM", "acme", ExecutionKind.EXECUTE)
        repo.set_status(ex.id, ExecutionStatus.RUNNING)
    finally:
        conn.close()

    r1 = client.post(f"/executions/{ex.id}/cancel")
    assert r1.status_code == 202

    # Drive the observable mid-cancel state: CANCELLING.
    conn = connect()
    try:
        ExecutionRepository(conn).set_status(ex.id, ExecutionStatus.CANCELLING)
    finally:
        conn.close()

    r2 = client.post(f"/executions/{ex.id}/cancel")
    assert r2.status_code == 202, r2.text
    assert r2.json()["signalled"] is True

    # Second call still dispatched cancel to the supervisor; we pin that as
    # the expected (idempotent) behaviour rather than silently skipping it.
    for _ in range(50):
        if len(fake_supervisor.cancel_calls) >= 2:
            break
        time.sleep(0.05)
    assert fake_supervisor.cancel_calls == [ex.id, ex.id]


# ---------------------------------------------------------------------------
# Spawn failure → 503
# ---------------------------------------------------------------------------


def test_spawn_failure_returns_503_and_marks_row_failed(client, fake_supervisor):
    """Test 10 — supervisor.spawn raises a non-RuntimeError → 503; the
    handler records the row as FAILED so the next attach lookup doesn't stick.
    """
    fake_supervisor.raise_on_spawn = OSError("fork failed")
    resp = client.post(
        "/executions",
        json={"project": "acme", "ticket_id": "PROJ-BOOM", "kind": "execute"},
    )
    assert resp.status_code == 503, resp.text
    assert "fork failed" in resp.json()["detail"]

    # Pinned behaviour: the handler marks the row FAILED rather than leaving
    # it for the reaper. This keeps ``find_active`` clean — the QUEUED row
    # never becomes a zombie attach target.
    rows = _fetch_rows()
    assert len(rows) == 1
    assert rows[0].status == ExecutionStatus.FAILED
    assert "spawn_failed" in (rows[0].error or "")


# ---------------------------------------------------------------------------
# Auth smoke test (optional 11th)
# ---------------------------------------------------------------------------


def test_unauthenticated_post_rejected(unauthed_client):
    """An unauthenticated POST to the write bucket returns 401/403.

    Uses the shared ``unauthed_client`` from conftest (no bearer header). We
    accept either status to stay robust against the auth layer's choice of
    code; what matters is the request never reaches the handler.
    """
    resp = unauthed_client.post(
        "/executions",
        json={"project": "acme", "ticket_id": "PROJ-1", "kind": "execute"},
    )
    assert resp.status_code in (401, 403), resp.text
