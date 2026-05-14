"""Event-bus invariants — see src/core/events/bus.py docstring.

Each test asserts one named contract from the persist-then-publish design.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from src.core.events import (
    DeveloperCappedOut,
    EventBus,
    StaticCheckRecorded,
    TestResultRecorded,
)
from src.core.persistence import apply_migrations


def _conn_with_execution(execution_id: str = "exec-1") -> sqlite3.Connection:
    """Build an in-memory DB with migrations applied and one parent row."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    apply_migrations(conn)
    conn.execute(
        "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (execution_id, "TICKET-1", "execute", "running", "2026-05-08T00:00:00+00:00"),
    )
    conn.commit()
    return conn


def test_publish_persists_before_calling_subscriber() -> None:
    """The events row must exist when the subscriber fires (persist-first)."""
    conn = _conn_with_execution()
    bus = EventBus(conn)

    seen_rows: list[sqlite3.Row] = []

    def handler(event: TestResultRecorded) -> None:
        # If persist-first is broken, this SELECT would return zero rows.
        rows = conn.execute(
            "SELECT * FROM events WHERE execution_id = ? AND type = ?",
            (event.execution_id, event.type),
        ).fetchall()
        seen_rows.extend(rows)

    bus.subscribe(TestResultRecorded, handler)
    bus.publish(
        TestResultRecorded(
            execution_id="exec-1",
            passed=True,
            attempt=1,
            structured_errors_count=0,
        )
    )

    assert len(seen_rows) == 1
    assert seen_rows[0]["seq"] == 1
    assert seen_rows[0]["type"] == "TestResultRecorded"


def test_subscriber_exception_does_not_crash_publish() -> None:
    """Bad subscriber is logged and swallowed; later subscribers still run."""
    conn = _conn_with_execution()
    bus = EventBus(conn)

    calls: list[str] = []

    def bad(_event: TestResultRecorded) -> None:
        calls.append("bad")
        raise RuntimeError("boom")

    def good(_event: TestResultRecorded) -> None:
        calls.append("good")

    bus.subscribe(TestResultRecorded, bad)
    bus.subscribe(TestResultRecorded, good)

    # Must NOT raise.
    bus.publish(
        TestResultRecorded(
            execution_id="exec-1",
            passed=False,
            attempt=2,
            structured_errors_count=3,
        )
    )

    assert calls == ["bad", "good"]
    # Row was persisted before either subscriber fired.
    assert len(bus.get_events("exec-1")) == 1


def test_seq_is_monotonic_per_execution() -> None:
    """Seq counts up within an execution and is independent across executions."""
    conn = _conn_with_execution("exec-A")
    conn.execute(
        "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("exec-B", "TICKET-2", "execute", "running", "2026-05-08T00:00:00+00:00"),
    )
    conn.commit()
    bus = EventBus(conn)

    for attempt in (1, 2, 3):
        bus.publish(
            TestResultRecorded(
                execution_id="exec-A",
                passed=False,
                attempt=attempt,
                structured_errors_count=1,
            )
        )
    for checker in ("phpstan", "composer_validate"):
        bus.publish(
            StaticCheckRecorded(
                execution_id="exec-B",
                checker=checker,
                passed=True,
                structured_errors_count=0,
            )
        )

    seqs_a = [row["seq"] for row in bus.get_events("exec-A")]
    seqs_b = [row["seq"] for row in bus.get_events("exec-B")]

    assert seqs_a == [1, 2, 3]
    assert seqs_b == [1, 2]


def test_oversized_payload_truncated() -> None:
    """Payloads >64 KB are replaced with a ``_truncated`` marker."""
    conn = _conn_with_execution()
    bus = EventBus(conn)

    huge_message = "x" * 1000  # per error
    big_errors = [
        {
            "file": f"src/file_{i}.py",
            "line": i,
            "rule": "test_failed",
            "message": huge_message,
        }
        for i in range(200)  # ~200 KB serialized — comfortably above 64 KB
    ]

    bus.publish(
        DeveloperCappedOut(
            execution_id="exec-1",
            agent="python_developer",
            attempts=3,
            last_structured_errors=big_errors,
        )
    )

    rows = bus.get_events("exec-1")
    assert len(rows) == 1
    payload: dict[str, Any] = json.loads(rows[0]["payload_json"])
    assert payload.get("_truncated") is True
    assert payload["type"] == "DeveloperCappedOut"
    assert payload["execution_id"] == "exec-1"


def test_ts_filled_when_empty() -> None:
    """Empty ``ts`` on input must be filled with an ISO-8601 UTC string."""
    conn = _conn_with_execution()
    bus = EventBus(conn)

    bus.publish(
        TestResultRecorded(
            execution_id="exec-1",
            ts="",  # explicit
            passed=True,
            attempt=1,
            structured_errors_count=0,
        )
    )

    row = bus.get_events("exec-1")[0]
    assert row["ts"]
    # datetime.fromisoformat handles "+00:00" suffix in 3.11+; raises if not ISO.
    parsed = datetime.fromisoformat(row["ts"])
    assert parsed.tzinfo is not None
