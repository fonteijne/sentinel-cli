"""Tests for ``query_postmortem_clusters`` (Phase 2C extractor read helper).

We INSERT postmortems with raw SQL when we need a precise ``created_at``
(the helper stamps ``datetime.now(...)`` at write time, so for window/
ordering tests we hand-build the timestamp). Parent ``executions`` rows
are inserted to satisfy the FK and to drive ``project_key`` derivation.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest

from src.core.persistence import apply_migrations, query_postmortem_clusters


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    apply_migrations(c)
    try:
        yield c
    finally:
        c.close()


def _seed_execution(
    c: sqlite3.Connection,
    *,
    exec_id: str,
    ticket_id: str,
    created_at: str | None = None,
) -> None:
    c.execute(
        "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            exec_id,
            ticket_id,
            "developer",
            "completed",
            created_at or datetime.now(timezone.utc).isoformat(),
        ),
    )


def _seed_postmortem_raw(
    c: sqlite3.Connection,
    *,
    execution_id: str,
    stack_type: str = "drupal",
    agent: str = "drupal_developer",
    failure_signature: str = "sig",
    created_at: str | None = None,
    confidence: int = 50,
) -> int:
    """Insert a postmortem with an explicit ``created_at`` (no helper)."""
    cur = c.execute(
        """
        INSERT INTO postmortems (
            execution_id, stack_type, agent, failure_signature,
            context_excerpt, fix_summary, provenance, confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            execution_id,
            stack_type,
            agent,
            failure_signature,
            None,
            None,
            "auto",
            confidence,
            created_at or datetime.now(timezone.utc).isoformat(),
        ),
    )
    c.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_window_filters_old_postmortems(conn: sqlite3.Connection) -> None:
    _seed_execution(conn, exec_id="exec-1", ticket_id="ACME-1")

    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=60)).isoformat()
    recent = (now - timedelta(days=5)).isoformat()

    _seed_postmortem_raw(
        conn,
        execution_id="exec-1",
        failure_signature="sig-old",
        created_at=old,
    )
    recent_id = _seed_postmortem_raw(
        conn,
        execution_id="exec-1",
        failure_signature="sig-new",
        created_at=recent,
    )

    rows = query_postmortem_clusters(conn, days=30)
    ids = [r["id"] for r in rows]
    assert ids == [recent_id], (
        f"only postmortems within 30 days should appear; got ids={ids}"
    )


def test_project_key_derivation(conn: sqlite3.Connection) -> None:
    _seed_execution(conn, exec_id="exec-acme", ticket_id="ACME-847")
    _seed_execution(conn, exec_id="exec-bravo", ticket_id="BRAVO-112")
    _seed_execution(conn, exec_id="exec-bare", ticket_id="whatever")

    _seed_postmortem_raw(conn, execution_id="exec-acme", failure_signature="sig-acme")
    _seed_postmortem_raw(conn, execution_id="exec-bravo", failure_signature="sig-bravo")
    _seed_postmortem_raw(conn, execution_id="exec-bare", failure_signature="sig-bare")

    rows = query_postmortem_clusters(conn, days=30)
    by_ticket = {r["ticket_id"]: r["project_key"] for r in rows}

    assert by_ticket["ACME-847"] == "ACME"
    assert by_ticket["BRAVO-112"] == "BRAVO"
    # No dash → INSTR returns 0 → SUBSTR(s, 1, -1) yields empty string.
    assert by_ticket["whatever"] == ""


def test_excludes_superseded(conn: sqlite3.Connection) -> None:
    _seed_execution(conn, exec_id="exec-1", ticket_id="ACME-1")

    surviving_id = _seed_postmortem_raw(
        conn, execution_id="exec-1", failure_signature="sig-shared"
    )
    superseded_id = _seed_postmortem_raw(
        conn, execution_id="exec-1", failure_signature="sig-shared"
    )
    conn.execute(
        "UPDATE postmortems SET superseded_by = ? WHERE id = ?",
        (surviving_id, superseded_id),
    )
    conn.commit()

    rows = query_postmortem_clusters(conn, days=30, only_active=True)
    ids = [r["id"] for r in rows]
    assert surviving_id in ids
    assert superseded_id not in ids


def test_orders_by_grouping_keys(conn: sqlite3.Connection) -> None:
    """Rows arrive sorted by (stack_type, agent, failure_signature, created_at ASC)."""
    _seed_execution(conn, exec_id="exec-1", ticket_id="ACME-1")

    now = datetime.now(timezone.utc)
    t1 = (now - timedelta(days=10)).isoformat()
    t2 = (now - timedelta(days=8)).isoformat()
    t3 = (now - timedelta(days=4)).isoformat()

    # Deliberately seed out-of-order:
    #   1. python / python_developer / sig-z (newest)
    #   2. drupal / drupal_developer / sig-a (newest of group)
    #   3. drupal / drupal_developer / sig-a (oldest of group)
    #   4. drupal / drupal_developer / sig-b
    pid_python = _seed_postmortem_raw(
        conn,
        execution_id="exec-1",
        stack_type="python",
        agent="python_developer",
        failure_signature="sig-z",
        created_at=t3,
    )
    pid_drupal_a_new = _seed_postmortem_raw(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="sig-a",
        created_at=t3,
    )
    pid_drupal_a_old = _seed_postmortem_raw(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="sig-a",
        created_at=t1,
    )
    pid_drupal_b = _seed_postmortem_raw(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="sig-b",
        created_at=t2,
    )

    rows = query_postmortem_clusters(conn, days=30)
    ordered_ids = [r["id"] for r in rows]

    # Expected order:
    #   drupal/drupal_developer/sig-a/t1 (oldest first within group),
    #   drupal/drupal_developer/sig-a/t3,
    #   drupal/drupal_developer/sig-b/t2,
    #   python/python_developer/sig-z/t3.
    expected = [pid_drupal_a_old, pid_drupal_a_new, pid_drupal_b, pid_python]
    assert ordered_ids == expected, (
        f"ordering wrong; got {ordered_ids}, expected {expected}"
    )


def test_only_active_false_returns_all(conn: sqlite3.Connection) -> None:
    _seed_execution(conn, exec_id="exec-1", ticket_id="ACME-1")

    a = _seed_postmortem_raw(conn, execution_id="exec-1", failure_signature="sig-x")
    b = _seed_postmortem_raw(conn, execution_id="exec-1", failure_signature="sig-x")
    conn.execute(
        "UPDATE postmortems SET superseded_by = ? WHERE id = ?", (a, b)
    )
    conn.commit()

    rows_active = query_postmortem_clusters(conn, days=30, only_active=True)
    assert b not in [r["id"] for r in rows_active]

    rows_all = query_postmortem_clusters(conn, days=30, only_active=False)
    ids = [r["id"] for r in rows_all]
    assert a in ids
    assert b in ids
