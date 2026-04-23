"""HTTP tests for the Command Center write endpoints (plan 04)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.repository import ExecutionRepository
from src.core.persistence import connect, ensure_initialized
from src.service.app import create_app


class _FakeSupervisor:
    """Minimal Supervisor stand-in — records calls, never spawns a subprocess."""

    def __init__(self) -> None:
        self.spawn_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.raise_on_spawn: bool = False

    def spawn(self, execution_id: str) -> int:
        self.spawn_calls.append(execution_id)
        if self.raise_on_spawn:
            raise RuntimeError("fake spawn failed")
        return 99999

    def cancel(self, execution_id: str) -> None:
        self.cancel_calls.append(execution_id)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(path))
    monkeypatch.setenv("SENTINEL_LOGS_DIR", str(tmp_path / "logs"))
    ensure_initialized()
    return path


@pytest.fixture
def fake_supervisor():
    return _FakeSupervisor()


@pytest.fixture
def client(db_path, fake_supervisor):
    """TestClient with a fake supervisor swapped in via dependency_overrides."""
    from src.service.deps import get_supervisor

    app = create_app()
    app.dependency_overrides[get_supervisor] = lambda: fake_supervisor
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------- start endpoint


def test_start_happy_path_returns_202_and_spawns(client, fake_supervisor):
    resp = client.post(
        "/executions",
        json={
            "ticket_id": "PROJ-1",
            "project": "proj",
            "kind": "execute",
            "options": {"revise": True},
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["ticket_id"] == "PROJ-1"
    assert body["kind"] == "execute"
    assert body["metadata"]["options"]["revise"] is True
    assert len(fake_supervisor.spawn_calls) == 1
    assert fake_supervisor.spawn_calls[0] == body["id"]


def test_start_rejects_extra_fields_with_422(client):
    resp = client.post(
        "/executions",
        json={
            "ticket_id": "PROJ-1",
            "project": "proj",
            "kind": "execute",
            "options": {},
            "bogus_injection_field": "malicious",
        },
    )
    assert resp.status_code == 422


def test_start_rejects_bad_ticket_and_project(client):
    resp = client.post(
        "/executions",
        json={"ticket_id": "not-valid", "project": "proj", "kind": "execute"},
    )
    assert resp.status_code == 422

    resp = client.post(
        "/executions",
        json={"ticket_id": "PROJ-1", "project": "UPPERCASE_BAD", "kind": "execute"},
    )
    assert resp.status_code == 422


def test_start_spawn_failure_marks_row_failed_and_500s(
    client, fake_supervisor, db_path
):
    fake_supervisor.raise_on_spawn = True
    resp = client.post(
        "/executions",
        json={"ticket_id": "PROJ-1", "project": "proj", "kind": "execute"},
    )
    assert resp.status_code == 500
    # The created row should have been marked FAILED.
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        rows = repo.list()
        assert rows and rows[0].status == ExecutionStatus.FAILED
        assert "spawn_failed" in (rows[0].error or "")
    finally:
        conn.close()


# --------------------------------------------------- idempotency behavior


def test_idempotency_returns_existing_row_and_does_not_spawn_twice(
    client, fake_supervisor, db_path
):
    # Plan 05 sets request.state.token_prefix. For isolated tests we
    # simulate the same effect by directly stamping both fields on an
    # existing row, then issuing the request with the same key. Without
    # a token prefix, the handler skips the idempotency lookup.
    # Instead of faking auth, seed an idempotent row and monkey-patch
    # the request state via a custom dependency.
    from src.service.deps import get_supervisor

    app = create_app()

    def _set_prefix(request):  # type: ignore[no-untyped-def]
        request.state.token_prefix = "abcd1234"

    @app.middleware("http")
    async def _mw(request, call_next):  # type: ignore[no-untyped-def]
        _set_prefix(request)
        return await call_next(request)

    app.dependency_overrides[get_supervisor] = lambda: fake_supervisor

    with TestClient(app) as c:
        r1 = c.post(
            "/executions",
            headers={"Idempotency-Key": "key-1"},
            json={"ticket_id": "PROJ-2", "project": "proj", "kind": "execute"},
        )
        assert r1.status_code == 202, r1.text
        first_id = r1.json()["id"]
        r2 = c.post(
            "/executions",
            headers={"Idempotency-Key": "key-1"},
            json={"ticket_id": "PROJ-2", "project": "proj", "kind": "execute"},
        )
        assert r2.status_code == 202
        assert r2.json()["id"] == first_id
        assert len(fake_supervisor.spawn_calls) == 1  # spawn only once


# ---------------------------------------------------------- cancel endpoint


def test_cancel_404_for_missing(client):
    resp = client.post("/executions/does-not-exist/cancel")
    assert resp.status_code == 404


def test_cancel_409_for_terminal_row(client, db_path):
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        ex = repo.create("PROJ-3", "proj", ExecutionKind.EXECUTE)
        repo.record_ended(ex.id, ExecutionStatus.SUCCEEDED)
    finally:
        conn.close()
    resp = client.post(f"/executions/{ex.id}/cancel")
    assert resp.status_code == 409


def test_cancel_happy_path_accepts_and_schedules(
    client, fake_supervisor, db_path
):
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        ex = repo.create("PROJ-4", "proj", ExecutionKind.EXECUTE)
    finally:
        conn.close()
    resp = client.post(f"/executions/{ex.id}/cancel")
    assert resp.status_code == 202
    # Cancel runs via run_in_executor; give it a tick.
    import time as _t

    for _ in range(50):
        if fake_supervisor.cancel_calls:
            break
        _t.sleep(0.05)
    assert fake_supervisor.cancel_calls == [ex.id]


# ---------------------------------------------------------- retry endpoint


def test_retry_creates_linked_execution(client, fake_supervisor, db_path):
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        original = repo.create(
            "PROJ-5",
            "proj",
            ExecutionKind.EXECUTE,
            options={"revise": True},
        )
        repo.record_ended(original.id, ExecutionStatus.FAILED, error="flaky")
    finally:
        conn.close()

    resp = client.post(f"/executions/{original.id}/retry")
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["id"] != original.id
    assert body["ticket_id"] == "PROJ-5"
    assert body["metadata"].get("retry_of") == original.id
    assert fake_supervisor.spawn_calls == [body["id"]]


def test_retry_409_when_original_still_running(client, db_path):
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        original = repo.create("PROJ-6", "proj", ExecutionKind.EXECUTE)
    finally:
        conn.close()
    resp = client.post(f"/executions/{original.id}/retry")
    assert resp.status_code == 409
