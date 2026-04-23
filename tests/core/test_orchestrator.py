"""Tests for src.core.execution.orchestrator.Orchestrator."""

from __future__ import annotations

import pytest

from src.core.events import CostAccrued, EventBus
from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.orchestrator import Orchestrator
from src.core.execution.repository import ExecutionRepository
from src.core.persistence import connect, ensure_initialized


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Per-test SQLite DB rooted in tmp_path. Closes the connection on teardown."""
    db_path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    ensure_initialized()
    conn = connect()
    yield conn
    conn.close()


def _event_types(repo: ExecutionRepository, execution_id: str) -> list[str]:
    return [row["type"] for row in repo.iter_events(execution_id)]


def test_run_happy_path_emits_started_and_completed(db):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    with orc.run(
        ticket_id="T-1", project="ACME", kind=ExecutionKind.PLAN
    ) as execution:
        assert execution.status == ExecutionStatus.RUNNING

    got = repo.get(execution.id)
    assert got is not None
    assert got.status == ExecutionStatus.SUCCEEDED

    types = _event_types(repo, execution.id)
    assert "execution.started" in types
    assert "execution.completed" in types


def test_run_failure_path_records_failed_and_reraises(db):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    with pytest.raises(RuntimeError, match="boom"):
        with orc.run(
            ticket_id="T-1", project="ACME", kind=ExecutionKind.PLAN
        ) as execution:
            raise RuntimeError("boom")

    got = repo.get(execution.id)
    assert got is not None
    assert got.status == ExecutionStatus.FAILED
    assert got.error is not None
    assert "boom" in got.error

    types = _event_types(repo, execution.id)
    assert "execution.failed" in types


def test_cost_subscriber_updates_execution(db):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    with orc.run(
        ticket_id="T-1", project="ACME", kind=ExecutionKind.PLAN
    ) as execution:
        bus.publish(
            CostAccrued(
                execution_id=execution.id, tokens_in=0, tokens_out=0, cents=13
            )
        )

    got = repo.get(execution.id)
    assert got is not None
    assert got.cost_cents == 13


def test_set_phase_publishes_phase_changed_event(db):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    with orc.run(
        ticket_id="T-1", project="ACME", kind=ExecutionKind.PLAN
    ) as execution:
        orc.set_phase(execution.id, "implementing")

    phase_rows = [
        row
        for row in repo.iter_events(execution.id)
        if row["type"] == "phase.changed"
    ]
    assert len(phase_rows) == 1
    assert phase_rows[0]["payload"].get("phase") == "implementing"
