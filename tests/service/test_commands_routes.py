"""HTTP tests for the retry endpoint on the Command Center write bucket.

Track 2 removed ``POST /executions`` (start) and ``POST /executions/{id}/cancel``
from ``routes.commands`` — those now live on ``executions.write_router`` with
attach-or-start semantics and an asyncified cancel. Coverage for those
endpoints lives in ``tests/service/test_executions_write.py``.

This file is trimmed to the surviving ``POST /executions/{id}/retry`` handler.
The fake-supervisor + dependency-override fixture pattern is still needed
because retry calls ``supervisor.spawn`` on the linked execution.
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
    enters its lifespan — so retry's ``get_supervisor`` dep resolves to the
    fake. Auth header is pre-attached.
    """
    from src.service.app import create_app
    from src.service.deps import get_supervisor

    app = create_app()
    app.dependency_overrides[get_supervisor] = lambda: fake_supervisor
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        yield c


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
