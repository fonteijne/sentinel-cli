"""Schema tests for the 004_feedback_rules migration.

Validates table + indexes exist, idempotent re-apply, the partial unique
index dedup behavior on live rows, and FK enforcement to ``postmortems``
and the self-FK on ``superseded_by``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Iterator

import pytest

from src.core.persistence import apply_migrations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """In-memory DB with migrations applied + a parent execution + postmortem.

    The execution + postmortem rows let FK-bearing tests insert
    ``feedback_rules`` rows that point at real ids without each test having
    to repeat the seeding ceremony.
    """
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    apply_migrations(c)
    now = datetime.now(timezone.utc).isoformat()
    c.execute(
        "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("exec-1", "TEST-1", "developer", "completed", now),
    )
    # Two parent postmortems so first/last FK tests can point at real rows.
    c.execute(
        """
        INSERT INTO postmortems (
            execution_id, stack_type, agent, failure_signature,
            context_excerpt, fix_summary, provenance, confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("exec-1", "drupal", "drupal_developer", "sig-1", None, None, "auto", 50, now),
    )
    c.execute(
        """
        INSERT INTO postmortems (
            execution_id, stack_type, agent, failure_signature,
            context_excerpt, fix_summary, provenance, confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("exec-1", "drupal", "drupal_developer", "sig-2", None, None, "auto", 50, now),
    )
    c.commit()
    try:
        yield c
    finally:
        c.close()


def _insert_rule(
    c: sqlite3.Connection,
    *,
    signature: str = "sig-X",
    scope: str = "drupal",
    agent_target: str = "developer",
    rule_text: str = "rule",
    status: str = "probation",
    confidence: int = 80,
    observation_count: int = 3,
    distinct_projects: int = 2,
    first_postmortem_id: int = 1,
    last_postmortem_id: int = 1,
) -> int:
    """Raw INSERT bypassing the helper — tests need direct schema access."""
    now = datetime.now(timezone.utc).isoformat()
    cur = c.execute(
        """
        INSERT INTO feedback_rules (
            signature, scope, agent_target, rule_text, status, confidence,
            observation_count, distinct_projects,
            first_postmortem_id, last_postmortem_id,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            signature, scope, agent_target, rule_text, status, confidence,
            observation_count, distinct_projects,
            first_postmortem_id, last_postmortem_id,
            now, now,
        ),
    )
    c.commit()
    assert cur.lastrowid is not None
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_migration_creates_table_and_indexes(conn: sqlite3.Connection) -> None:
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "feedback_rules" in tables, f"feedback_rules table missing; got {tables}"

    indexes = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "idx_feedback_rules_dedup" in indexes
    assert "idx_feedback_rules_status" in indexes

    cols = {
        r["name"]: r
        for r in conn.execute("PRAGMA table_info(feedback_rules)").fetchall()
    }
    expected = {
        "id", "signature", "scope", "agent_target", "rule_text",
        "status", "confidence", "observation_count", "distinct_projects",
        "first_postmortem_id", "last_postmortem_id",
        "proposed_overlay_path", "proposed_overlay_mr_url", "proposed_at",
        "promoted_to_overlay_sha", "promoted_by", "promoted_at",
        "superseded_by", "revoked_by", "revoked_at", "revocation_reason",
        "created_at", "updated_at",
    }
    assert expected.issubset(set(cols.keys())), (
        f"missing columns: {expected - set(cols.keys())}"
    )

    # NOT NULL columns: id, signature, scope, agent_target, rule_text, status,
    # confidence, observation_count, distinct_projects, created_at, updated_at.
    for not_null_col in (
        "signature", "scope", "agent_target", "rule_text", "status",
        "confidence", "observation_count", "distinct_projects",
        "created_at", "updated_at",
    ):
        assert cols[not_null_col]["notnull"] == 1, (
            f"{not_null_col} should be NOT NULL"
        )

    # Nullable columns include the lifecycle metadata.
    for nullable_col in (
        "proposed_overlay_path", "proposed_overlay_mr_url", "proposed_at",
        "promoted_to_overlay_sha", "promoted_by", "promoted_at",
        "superseded_by", "revoked_by", "revoked_at", "revocation_reason",
        "first_postmortem_id", "last_postmortem_id",
    ):
        assert cols[nullable_col]["notnull"] == 0, (
            f"{nullable_col} should be nullable"
        )


def test_migration_idempotent(conn: sqlite3.Connection) -> None:
    # Migrations were already applied in the fixture; apply again must no-op.
    apply_migrations(conn)
    apply_migrations(conn)

    rows = conn.execute(
        "SELECT version FROM schema_migrations WHERE version = '004_feedback_rules'"
    ).fetchall()
    # Recorded exactly once even after multiple apply_migrations calls.
    assert len(rows) == 1


def test_partial_unique_blocks_duplicate_live_rule(conn: sqlite3.Connection) -> None:
    _insert_rule(
        conn,
        signature="dup-sig",
        scope="drupal",
        agent_target="developer",
        status="probation",
    )
    with pytest.raises(sqlite3.IntegrityError):
        _insert_rule(
            conn,
            signature="dup-sig",
            scope="drupal",
            agent_target="developer",
            status="probation",
        )


def test_partial_unique_allows_superseded_collision(conn: sqlite3.Connection) -> None:
    a_id = _insert_rule(
        conn,
        signature="reborn-sig",
        scope="drupal",
        agent_target="developer",
        status="probation",
    )
    # Flip A out of the live set.
    conn.execute(
        "UPDATE feedback_rules SET status = 'superseded' WHERE id = ?", (a_id,)
    )
    conn.commit()

    # Now a fresh probation row with the same (scope, agent_target, signature)
    # MUST be allowed because the partial unique index excludes superseded rows.
    b_id = _insert_rule(
        conn,
        signature="reborn-sig",
        scope="drupal",
        agent_target="developer",
        status="probation",
    )
    assert b_id != a_id


def test_fk_to_postmortems(conn: sqlite3.Connection) -> None:
    # Bogus first_postmortem_id → IntegrityError under PRAGMA foreign_keys=ON.
    with pytest.raises(sqlite3.IntegrityError):
        _insert_rule(
            conn,
            signature="fk-bad-pm",
            first_postmortem_id=99999,
            last_postmortem_id=1,
        )

    # Bogus last_postmortem_id likewise.
    with pytest.raises(sqlite3.IntegrityError):
        _insert_rule(
            conn,
            signature="fk-bad-pm-last",
            first_postmortem_id=1,
            last_postmortem_id=99999,
        )

    # Self-FK on superseded_by: pointing at a non-existent rule id is rejected.
    seed_id = _insert_rule(conn, signature="self-fk-seed")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE feedback_rules SET superseded_by = ? WHERE id = ?",
            (99999, seed_id),
        )
        conn.commit()
