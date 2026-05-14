"""Postmortems helper tests: round-trip, NOT NULL provenance, FK linkage."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterator

import pytest

from src.core.persistence import apply_migrations, insert_postmortem


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """In-memory DB with migrations applied + a parent execution row.

    Postmortems FK to ``executions(id)``, so every test needs at least one
    parent row.
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


def test_insert_postmortem_round_trip(conn: sqlite3.Connection) -> None:
    rowid = insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="phpunit::failed_assertion::foo",
        context_excerpt="assertSame failed",
        fix_summary=None,
        provenance="auto",
        confidence=42,
    )
    assert rowid > 0

    row = conn.execute(
        "SELECT * FROM postmortems WHERE id = ?", (rowid,)
    ).fetchone()

    assert row["execution_id"] == "exec-1"
    assert row["stack_type"] == "drupal"
    assert row["agent"] == "drupal_developer"
    assert row["failure_signature"] == "phpunit::failed_assertion::foo"
    assert row["context_excerpt"] == "assertSame failed"
    assert row["fix_summary"] is None
    assert row["provenance"] == "auto"
    assert row["confidence"] == 42
    assert row["superseded_by"] is None
    assert row["created_at"]  # ISO string set by helper


def test_provenance_not_null_rejected(conn: sqlite3.Connection) -> None:
    """The helper itself rejects provenance=None — design invariant.

    We don't rely on the schema's NOT NULL alone because the SQLite error
    is opaque; ValueError at the call site is louder.
    """
    with pytest.raises(ValueError):
        insert_postmortem(
            conn,
            execution_id="exec-1",
            stack_type="drupal",
            agent="drupal_developer",
            failure_signature="x",
            provenance=None,  # type: ignore[arg-type]
        )

    # Also reject unrecognized provenance values.
    with pytest.raises(ValueError):
        insert_postmortem(
            conn,
            execution_id="exec-1",
            stack_type="drupal",
            agent="drupal_developer",
            failure_signature="x",
            provenance="machine-edited",
        )


def test_superseded_by_fk_roundtrip(conn: sqlite3.Connection) -> None:
    """A postmortem can point at another via superseded_by; bad FK is rejected."""
    a_id = insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="python",
        agent="python_developer",
        failure_signature="sig-A",
    )
    b_id = insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="python",
        agent="python_developer",
        failure_signature="sig-B",
    )
    # Wire B → A through the schema directly (no helper for UPDATE — append-only
    # API at the helper level). Phase 2 will add the proper revocation flow.
    conn.execute("UPDATE postmortems SET superseded_by = ? WHERE id = ?", (a_id, b_id))
    conn.commit()

    b_row = conn.execute(
        "SELECT superseded_by FROM postmortems WHERE id = ?", (b_id,)
    ).fetchone()
    assert b_row["superseded_by"] == a_id

    # Pointing at a non-existent row is rejected by the FK constraint.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE postmortems SET superseded_by = ? WHERE id = ?", (99999, b_id)
        )
        conn.commit()


def test_repeated_insert_same_signature_does_not_dedup(
    conn: sqlite3.Connection,
) -> None:
    """The persistence layer never dedups; that's Phase 2 extraction-job work."""
    first = insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="same-signature",
    )
    second = insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="same-signature",
    )

    assert first != second

    count = conn.execute(
        "SELECT COUNT(*) AS n FROM postmortems WHERE failure_signature = ?",
        ("same-signature",),
    ).fetchone()["n"]
    assert count == 2
