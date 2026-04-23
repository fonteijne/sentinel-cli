"""Tests for src.core.execution.repository.ExecutionRepository."""

from __future__ import annotations

import pytest

from src.core.events import EventBus, ExecutionStarted, PhaseChanged
from src.core.execution.models import ExecutionKind, ExecutionStatus
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


def test_create_inserts_running_row(db):
    repo = ExecutionRepository(db)

    ex_no_opts = repo.create("T-1", "ACME", ExecutionKind.PLAN)
    assert ex_no_opts.status == ExecutionStatus.RUNNING
    assert ex_no_opts.id
    assert ex_no_opts.metadata == {}

    ex_with_opts = repo.create(
        "T-2", "ACME", ExecutionKind.EXECUTE, options={"k": "v"}
    )
    assert ex_with_opts.status == ExecutionStatus.RUNNING
    assert ex_with_opts.metadata == {"options": {"k": "v"}}


def test_get_roundtrips_all_fields(db):
    repo = ExecutionRepository(db)
    created = repo.create("T-1", "ACME", ExecutionKind.PLAN)

    repo.set_phase(created.id, "analysing")
    repo.add_cost(created.id, 42)
    repo.mark_metadata(created.id, retry_of="prev-id")

    got = repo.get(created.id)
    assert got is not None
    assert got.id == created.id
    assert got.ticket_id == "T-1"
    assert got.project == "ACME"
    assert got.kind == ExecutionKind.PLAN
    assert got.phase == "analysing"
    assert got.cost_cents == 42
    assert got.metadata.get("retry_of") == "prev-id"


def test_lifecycle_succeeded(db):
    repo = ExecutionRepository(db)
    created = repo.create("T-1", "ACME", ExecutionKind.PLAN)

    repo.record_ended(created.id, ExecutionStatus.SUCCEEDED)
    got = repo.get(created.id)
    assert got is not None
    assert got.status == ExecutionStatus.SUCCEEDED
    assert got.ended_at is not None


def test_lifecycle_failed_captures_error(db):
    repo = ExecutionRepository(db)
    created = repo.create("T-1", "ACME", ExecutionKind.PLAN)

    repo.record_ended(created.id, ExecutionStatus.FAILED, error="boom")
    got = repo.get(created.id)
    assert got is not None
    assert got.status == ExecutionStatus.FAILED
    assert got.error == "boom"


def test_list_filters_by_project_and_status(db):
    repo = ExecutionRepository(db)
    a1 = repo.create("T-1", "ACME", ExecutionKind.PLAN)
    repo.create("T-2", "ACME", ExecutionKind.PLAN)  # stays RUNNING
    repo.create("T-3", "OTHER", ExecutionKind.PLAN)

    repo.record_ended(a1.id, ExecutionStatus.SUCCEEDED)
    # a2 stays RUNNING

    acme = repo.list(project="ACME")
    assert len(acme) == 2

    acme_ok = repo.list(project="ACME", status=ExecutionStatus.SUCCEEDED)
    assert len(acme_ok) == 1
    assert acme_ok[0].id == a1.id


def test_idempotency_find_returns_existing(db):
    repo = ExecutionRepository(db)
    created = repo.create(
        "T-1",
        "ACME",
        ExecutionKind.PLAN,
        idempotency_token_prefix="abc",
        idempotency_key="k",
    )

    found = repo.find_by_idempotency("abc", "k")
    assert found is not None
    assert found.id == created.id


def test_agent_result_json_roundtrip(db):
    repo = ExecutionRepository(db)
    created = repo.create("T-1", "ACME", ExecutionKind.PLAN)

    original = {"summary": "all good", "files": ["a.py", "b.py"], "score": 0.9}
    repo.record_agent_result(created.id, "dev", original)

    results = repo.list_agent_results(created.id)
    assert len(results) == 1
    assert results[0]["agent"] == "dev"
    assert results[0]["result"] == original


def test_iter_events_parses_payload(db):
    repo = ExecutionRepository(db)
    created = repo.create("T-1", "ACME", ExecutionKind.PLAN)

    bus = EventBus(db)
    bus.publish(
        ExecutionStarted(
            execution_id=created.id, kind="plan", ticket_id="T-1", project="ACME"
        )
    )
    bus.publish(PhaseChanged(execution_id=created.id, phase="analysing"))

    rows = list(repo.iter_events(created.id))
    assert len(rows) == 2
    # payload is a dict already, NOT raw JSON
    assert isinstance(rows[0]["payload"], dict)
    assert isinstance(rows[1]["payload"], dict)
    assert rows[0]["type"] == "execution.started"
    assert rows[1]["type"] == "phase.changed"
    assert rows[1]["payload"].get("phase") == "analysing"


def test_add_cost_is_atomic_sum(db):
    repo = ExecutionRepository(db)
    created = repo.create("T-1", "ACME", ExecutionKind.PLAN)

    repo.add_cost(created.id, 10)
    repo.add_cost(created.id, 7)

    got = repo.get(created.id)
    assert got is not None
    assert got.cost_cents == 17
