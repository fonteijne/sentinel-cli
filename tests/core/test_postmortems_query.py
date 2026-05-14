"""SELECT-helper tests for postmortems: query_active_postmortems + list_postmortems.

Phase 2A Task 2 (plan ref: phase-2a-pitfalls-visible.plan.md).

Mirrors the in-memory fixture pattern from ``tests/core/test_postmortems.py`` —
a fresh ``:memory:`` DB with migrations applied and a parent execution row so
postmortems' FK to ``executions(id)`` is satisfied.

These tests exercise SELECT helpers only; Decision 4 (append-only) means we
never UPDATE or DELETE through the helper API. The one direct UPDATE here
(setting ``superseded_by``) goes through raw SQL — the same escape hatch the
existing ``test_superseded_by_fk_roundtrip`` uses.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterator

import pytest

from src.core.persistence import (
    apply_migrations,
    insert_postmortem,
    list_postmortems,
    query_active_postmortems,
)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """In-memory DB with migrations applied + a parent execution row.

    Identical to the fixture in ``tests/core/test_postmortems.py`` — kept local
    so this module is self-contained and won't drift if the shared conftest
    fixture changes shape.
    """
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    apply_migrations(c)
    c.execute(
        """
        INSERT INTO executions (id, ticket_id, kind, status, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "exec-1",
            "TEST-1",
            "developer",
            "running",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    c.commit()
    try:
        yield c
    finally:
        c.close()


# ---------------------------------------------------------------------------
# query_active_postmortems
# ---------------------------------------------------------------------------


def test_returns_only_matching_stack(conn: sqlite3.Connection) -> None:
    insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="phpunit::A",
        confidence=80,
    )
    insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="python",
        agent="python_developer",
        failure_signature="pytest::B",
        confidence=80,
    )

    rows = query_active_postmortems(conn, "drupal")
    assert len(rows) == 1
    assert rows[0]["stack_type"] == "drupal"
    assert rows[0]["failure_signature"] == "phpunit::A"


def test_returns_only_above_confidence_floor(conn: sqlite3.Connection) -> None:
    for sig, confidence in [("low", 50), ("mid", 70), ("high", 90)]:
        insert_postmortem(
            conn,
            execution_id="exec-1",
            stack_type="drupal",
            agent="drupal_developer",
            failure_signature=sig,
            confidence=confidence,
        )

    rows = query_active_postmortems(conn, "drupal", min_confidence=70)
    sigs = {r["failure_signature"] for r in rows}
    assert sigs == {"mid", "high"}


def test_excludes_superseded(conn: sqlite3.Connection) -> None:
    a_id = insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="sig-A",
        confidence=80,
    )
    b_id = insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="sig-B",
        confidence=80,
    )
    # Mark B as superseded by A. (Direct SQL — no helper, by design.)
    conn.execute(
        "UPDATE postmortems SET superseded_by = ? WHERE id = ?", (a_id, b_id)
    )
    conn.commit()

    rows = query_active_postmortems(conn, "drupal")
    sigs = {r["failure_signature"] for r in rows}
    assert sigs == {"sig-A"}
    assert "sig-B" not in sigs


def test_orders_by_confidence_then_created_at(conn: sqlite3.Connection) -> None:
    """Higher confidence first; ties broken by newer created_at first.

    Inserts use the helper which stamps ``created_at`` from ``datetime.now``,
    so we tweak ``created_at`` directly via UPDATE for the tie-break check —
    the helper API stays append-only insert-only.
    """
    a = insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="conf50",
        confidence=50,
    )
    b = insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="conf90-older",
        confidence=90,
    )
    c = insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="conf90-newer",
        confidence=90,
    )
    d = insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="conf70",
        confidence=70,
    )
    # Force deterministic created_at ordering for the conf=90 tie.
    conn.execute(
        "UPDATE postmortems SET created_at = ? WHERE id = ?",
        ("2026-01-01T00:00:00+00:00", b),
    )
    conn.execute(
        "UPDATE postmortems SET created_at = ? WHERE id = ?",
        ("2026-02-01T00:00:00+00:00", c),
    )
    conn.commit()

    rows = query_active_postmortems(conn, "drupal", min_confidence=0, limit=10)
    # Expected: conf=90 newer, conf=90 older, conf=70, conf=50.
    assert [r["failure_signature"] for r in rows] == [
        "conf90-newer",
        "conf90-older",
        "conf70",
        "conf50",
    ]
    # Sanity: a is the lowest-confidence row and lands last; d sits between.
    assert rows[-1]["id"] == a
    assert rows[2]["id"] == d


def test_respects_limit(conn: sqlite3.Connection) -> None:
    for i in range(20):
        insert_postmortem(
            conn,
            execution_id="exec-1",
            stack_type="drupal",
            agent="drupal_developer",
            failure_signature=f"sig-{i}",
            confidence=80,
        )

    rows = query_active_postmortems(conn, "drupal", limit=5)
    assert len(rows) == 5


# ---------------------------------------------------------------------------
# list_postmortems
# ---------------------------------------------------------------------------


def test_list_postmortems_no_stack_filter(conn: sqlite3.Connection) -> None:
    insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="phpunit::A",
        confidence=50,
    )
    insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="python",
        agent="python_developer",
        failure_signature="pytest::B",
        confidence=50,
    )

    rows = list_postmortems(conn)
    stacks = {r["stack_type"] for r in rows}
    assert stacks == {"drupal", "python"}


def test_list_postmortems_with_stack_filter(conn: sqlite3.Connection) -> None:
    insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="phpunit::A",
        confidence=50,
    )
    insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="python",
        agent="python_developer",
        failure_signature="pytest::B",
        confidence=50,
    )

    rows = list_postmortems(conn, stack="drupal")
    assert len(rows) == 1
    assert rows[0]["stack_type"] == "drupal"
    assert rows[0]["failure_signature"] == "phpunit::A"


def test_list_postmortems_min_confidence_filter(conn: sqlite3.Connection) -> None:
    for sig, confidence in [("c50", 50), ("c75", 75), ("c80", 80), ("c95", 95)]:
        insert_postmortem(
            conn,
            execution_id="exec-1",
            stack_type="drupal",
            agent="drupal_developer",
            failure_signature=sig,
            confidence=confidence,
        )

    rows = list_postmortems(conn, min_confidence=80)
    sigs = {r["failure_signature"] for r in rows}
    assert sigs == {"c80", "c95"}
