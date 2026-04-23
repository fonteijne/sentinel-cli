"""Tests for src.core.events.bus.EventBus — persist-then-publish semantics."""

from __future__ import annotations

import json

import pytest

from src.core.events import EventBus, ExecutionStarted
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


def _insert_execution(conn, execution_id: str) -> None:
    """Minimal executions row so the FK on events is satisfied."""
    conn.execute(
        "INSERT INTO executions(id, ticket_id, project, kind, status, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (execution_id, "T-1", "ACME", "plan", "running", "2026-01-01T00:00:00+00:00"),
    )


def _make_started(execution_id: str, **kwargs) -> ExecutionStarted:
    defaults = dict(
        execution_id=execution_id,
        kind="plan",
        ticket_id="T-1",
        project="ACME",
    )
    defaults.update(kwargs)
    return ExecutionStarted(**defaults)


def test_publish_persists_before_subscriber_fires(db):
    _insert_execution(db, "exec-1")
    bus = EventBus(db)

    def bad_subscriber(_event):
        raise RuntimeError("subscriber boom")

    bus.subscribe(bad_subscriber)

    # Should NOT raise — subscriber exceptions are swallowed.
    bus.publish(_make_started("exec-1"))

    rows = db.execute(
        "SELECT type FROM events WHERE execution_id = ?", ("exec-1",)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "execution.started"


def test_seq_is_monotonic_per_execution(db):
    _insert_execution(db, "exec-1")
    _insert_execution(db, "exec-2")
    bus = EventBus(db)

    bus.publish(_make_started("exec-1"))
    bus.publish(_make_started("exec-1"))
    bus.publish(_make_started("exec-1"))
    bus.publish(_make_started("exec-2"))
    bus.publish(_make_started("exec-1"))
    bus.publish(_make_started("exec-1"))

    seqs_1 = [
        row[0]
        for row in db.execute(
            "SELECT seq FROM events WHERE execution_id = ? ORDER BY seq",
            ("exec-1",),
        ).fetchall()
    ]
    seqs_2 = [
        row[0]
        for row in db.execute(
            "SELECT seq FROM events WHERE execution_id = ? ORDER BY seq",
            ("exec-2",),
        ).fetchall()
    ]
    assert seqs_1 == [1, 2, 3, 4, 5]
    assert seqs_2 == [1]


def test_oversize_payload_is_truncated(db):
    _insert_execution(db, "exec-1")
    bus = EventBus(db)

    bus.publish(_make_started("exec-1", kind="x" * 70000))

    row = db.execute(
        "SELECT payload_json FROM events WHERE execution_id = ?", ("exec-1",)
    ).fetchone()
    assert row is not None
    decoded = json.loads(row[0])
    assert isinstance(decoded, dict)
    assert decoded.get("_truncated") is True


def test_unsubscribe_removes_callback(db):
    _insert_execution(db, "exec-1")
    bus = EventBus(db)

    calls: list = []
    unsub = bus.subscribe(lambda ev: calls.append(ev))
    unsub()

    bus.publish(_make_started("exec-1"))

    assert calls == []


def test_multiple_subscribers_all_fire(db):
    _insert_execution(db, "exec-1")
    bus = EventBus(db)

    calls_a: list = []
    calls_b: list = []
    bus.subscribe(lambda ev: calls_a.append(ev))
    bus.subscribe(lambda ev: calls_b.append(ev))

    bus.publish(_make_started("exec-1"))

    assert len(calls_a) == 1
    assert len(calls_b) == 1
    assert calls_a[0].execution_id == "exec-1"
    assert calls_b[0].execution_id == "exec-1"
