"""End-to-end flow for plan 04 — POST → stream → cancel → reconcile.

Uses a fake Supervisor that runs the happy-path events synchronously so we
don't have to orchestrate a real subprocess in CI. The supervisor is
dependency-overridden on the TestClient; everything else — DB, event bus,
routes, WebSocket tail — is real.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from src.core.events.bus import EventBus
from src.core.events.types import (
    ExecutionCancelled,
    ExecutionCompleted,
    ExecutionStarted,
    PhaseChanged,
)
from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.repository import ExecutionRepository
from src.core.persistence import connect, ensure_initialized
from src.service.app import create_app


class _SyntheticSupervisor:
    """Supervisor stand-in that synthesizes lifecycle events on spawn/cancel."""

    def __init__(self) -> None:
        self.spawned: list[str] = []
        self.cancelled: list[str] = []

    def spawn(self, execution_id: str) -> int:
        self.spawned.append(execution_id)
        conn = connect()
        try:
            repo = ExecutionRepository(conn)
            bus = EventBus(conn)
            execution = repo.get(execution_id)
            assert execution is not None
            bus.publish(
                ExecutionStarted(
                    execution_id=execution_id,
                    kind=execution.kind.value,
                    ticket_id=execution.ticket_id,
                    project=execution.project,
                )
            )
            for phase in ("analysing", "writing", "reviewing", "committing"):
                bus.publish(PhaseChanged(execution_id=execution_id, phase=phase))
            repo.record_ended(execution_id, ExecutionStatus.SUCCEEDED)
            bus.publish(
                ExecutionCompleted(
                    execution_id=execution_id,
                    status="succeeded",
                    cost_cents=0,
                )
            )
        finally:
            conn.close()
        return 99999

    def cancel(self, execution_id: str) -> None:
        conn = connect()
        try:
            repo = ExecutionRepository(conn)
            bus = EventBus(conn)
            repo.record_ended(execution_id, ExecutionStatus.CANCELLED)
            bus.publish(ExecutionCancelled(execution_id=execution_id))
        finally:
            conn.close()
        # Mark complete AFTER the DB write so pollers see a consistent state.
        self.cancelled.append(execution_id)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(path))
    monkeypatch.setenv("SENTINEL_LOGS_DIR", str(tmp_path / "logs"))
    ensure_initialized()
    return path


@pytest.fixture
def client_with_fake_supervisor(db_path):
    from src.service.deps import get_supervisor

    app = create_app()
    fake = _SyntheticSupervisor()
    app.dependency_overrides[get_supervisor] = lambda: fake
    with TestClient(app) as c:
        yield c, fake


def test_start_completes_and_events_are_ordered(client_with_fake_supervisor):
    client, fake = client_with_fake_supervisor

    resp = client.post(
        "/executions",
        json={"ticket_id": "PROJ-1", "project": "proj", "kind": "execute"},
    )
    assert resp.status_code == 202, resp.text
    execution_id = resp.json()["id"]

    # GET the events that spawn() already wrote
    evts = client.get(f"/executions/{execution_id}/events").json()["items"]
    types = [e["type"] for e in evts]
    assert types[0] == "execution.started"
    assert "phase.changed" in types
    assert types[-1] == "execution.completed"
    # seq strictly ascending
    seqs = [e["seq"] for e in evts]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    row = client.get(f"/executions/{execution_id}").json()
    assert row["status"] == "succeeded"


def test_cancel_transitions_to_cancelled(client_with_fake_supervisor, db_path):
    client, fake = client_with_fake_supervisor

    # Seed a row we can cancel (bypass spawn so status stays running)
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        ex = repo.create("PROJ-2", "proj", ExecutionKind.EXECUTE)
    finally:
        conn.close()

    resp = client.post(f"/executions/{ex.id}/cancel")
    assert resp.status_code == 202

    # The fake supervisor runs sync via run_in_executor; give it a tick
    for _ in range(50):
        if fake.cancelled:
            break
        time.sleep(0.05)
    assert ex.id in fake.cancelled

    row = client.get(f"/executions/{ex.id}").json()
    assert row["status"] == "cancelled"

    evts = client.get(f"/executions/{ex.id}/events").json()["items"]
    types = [e["type"] for e in evts]
    assert "execution.cancelled" in types


def test_stream_tails_events_and_closes_on_terminal(
    client_with_fake_supervisor, db_path
):
    client, _ = client_with_fake_supervisor

    resp = client.post(
        "/executions",
        json={"ticket_id": "PROJ-3", "project": "proj", "kind": "plan"},
    )
    execution_id = resp.json()["id"]

    with client.websocket_connect(
        f"/executions/{execution_id}/stream?since_seq=0"
    ) as ws:
        collected: list[dict] = []
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                frame = ws.receive_json(mode="text")
            except Exception:
                break
            collected.append(frame)
            if frame.get("type") in (
                "execution.completed",
                "execution.failed",
                "execution.cancelled",
            ):
                break

    types = [f.get("type") for f in collected]
    assert "execution.started" in types
    assert "execution.completed" in types
