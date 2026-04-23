"""HTTP tests for the Command Center write endpoints (plan 04).

Migrated in plan 05 Task 6 to use the central ``authed_client`` fixture.
A fake Supervisor is still required, but this test file needs a variant of
``authed_client`` that also overrides ``get_supervisor`` — hence the local
``authed_client_with_fake_supervisor`` fixture below. It mirrors the env
monkeypatching from ``conftest.authed_env`` (already applied via param
injection) then attaches ``app.dependency_overrides[get_supervisor]`` before
the TestClient enters its lifespan context.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.repository import ExecutionRepository
from src.core.persistence import connect

from tests.service.conftest import TEST_TOKEN


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
def fake_supervisor() -> _FakeSupervisor:
    return _FakeSupervisor()


@pytest.fixture
def authed_client_with_fake_supervisor(
    authed_env, fake_supervisor
) -> Iterator[TestClient]:
    """``authed_client`` variant that swaps in a fake supervisor.

    Mirrors the real ``authed_client`` (env already set by ``authed_env``) but
    installs ``app.dependency_overrides[get_supervisor]`` before the TestClient
    enters its lifespan — so every write-endpoint dep that resolves
    ``get_supervisor`` gets the fake. Auth header is pre-attached.
    """
    from src.service.app import create_app
    from src.service.deps import get_supervisor

    app = create_app()
    app.dependency_overrides[get_supervisor] = lambda: fake_supervisor
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        yield c


# --------------------------------------------------------- start endpoint


def test_start_happy_path_returns_202_and_spawns(
    authed_client_with_fake_supervisor, fake_supervisor
):
    client = authed_client_with_fake_supervisor
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


def test_start_rejects_extra_fields_with_422(authed_client_with_fake_supervisor):
    client = authed_client_with_fake_supervisor
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


def test_start_rejects_bad_ticket_and_project(authed_client_with_fake_supervisor):
    client = authed_client_with_fake_supervisor
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
    authed_client_with_fake_supervisor, fake_supervisor
):
    client = authed_client_with_fake_supervisor
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
    authed_client_with_fake_supervisor, fake_supervisor
):
    """With plan 05, ``require_token_and_write_slot`` stamps
    ``request.state.token_prefix`` for us — no middleware fake needed.

    Two authed POSTs with the same Idempotency-Key should resolve to the
    same execution row and spawn exactly once.
    """
    client = authed_client_with_fake_supervisor
    r1 = client.post(
        "/executions",
        headers={"Idempotency-Key": "key-1"},
        json={"ticket_id": "PROJ-2", "project": "proj", "kind": "execute"},
    )
    assert r1.status_code == 202, r1.text
    first_id = r1.json()["id"]
    r2 = client.post(
        "/executions",
        headers={"Idempotency-Key": "key-1"},
        json={"ticket_id": "PROJ-2", "project": "proj", "kind": "execute"},
    )
    assert r2.status_code == 202
    assert r2.json()["id"] == first_id
    assert len(fake_supervisor.spawn_calls) == 1  # spawn only once


# ---------------------------------------------------------- cancel endpoint


def test_cancel_404_for_missing(authed_client_with_fake_supervisor):
    client = authed_client_with_fake_supervisor
    resp = client.post("/executions/does-not-exist/cancel")
    assert resp.status_code == 404


def test_cancel_409_for_terminal_row(authed_client_with_fake_supervisor):
    client = authed_client_with_fake_supervisor
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
    authed_client_with_fake_supervisor, fake_supervisor
):
    client = authed_client_with_fake_supervisor
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


def test_retry_creates_linked_execution(
    authed_client_with_fake_supervisor, fake_supervisor
):
    client = authed_client_with_fake_supervisor
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


def test_retry_409_when_original_still_running(authed_client_with_fake_supervisor):
    client = authed_client_with_fake_supervisor
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        original = repo.create("PROJ-6", "proj", ExecutionKind.EXECUTE)
    finally:
        conn.close()
    resp = client.post(f"/executions/{original.id}/retry")
    assert resp.status_code == 409
