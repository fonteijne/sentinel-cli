"""Tests for src/core/persistence/feedback_rules.py.

Covers the seven write helpers + the append-only invariant (no
``update_rule`` / ``delete_rule`` exported). All tests run against an
in-memory SQLite connection with migrations applied + a parent execution
row + a parent postmortem row that all FKs point at.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone
from typing import Iterator

import pytest

from src.core.persistence import (
    apply_migrations,
    list_rules,
    mark_promoted,
    mark_proposed,
    mark_superseded,
    query_promotable,
    revoke_rule,
    upsert_rule,
)
from src.core.persistence import feedback_rules as feedback_rules_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """In-memory DB + executions + postmortem so first_postmortem_id FK is satisfied."""
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
    # Two postmortems for first/last FK targets.
    for sig in ("pm-sig-1", "pm-sig-2"):
        c.execute(
            """
            INSERT INTO postmortems (
                execution_id, stack_type, agent, failure_signature,
                context_excerpt, fix_summary, provenance, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("exec-1", "drupal", "drupal_developer", sig, None, None, "auto", 50, now),
        )
    c.commit()
    try:
        yield c
    finally:
        c.close()


def _make_upsert_kwargs(**overrides):
    """Sensible defaults for upsert_rule call sites."""
    base = dict(
        signature="phpstan.notfound::Foo::bar",
        scope="drupal",
        agent_target="developer",
        rule_text="phpstan.notfound::Foo::bar",
        confidence=80,
        observation_count=3,
        distinct_projects=2,
        first_postmortem_id=1,
        last_postmortem_id=1,
    )
    base.update(overrides)
    return base


def _row_for(c: sqlite3.Connection, rule_id: int) -> sqlite3.Row:
    row = c.execute(
        "SELECT * FROM feedback_rules WHERE id = ?", (rule_id,)
    ).fetchone()
    assert row is not None, f"rule id={rule_id} not found"
    return row


# ---------------------------------------------------------------------------
# upsert_rule
# ---------------------------------------------------------------------------


def test_upsert_inserts_new_row(conn: sqlite3.Connection) -> None:
    rid = upsert_rule(conn, **_make_upsert_kwargs())
    assert rid > 0

    row = _row_for(conn, rid)
    assert row["status"] == "probation"
    assert row["created_at"]
    assert row["updated_at"]
    assert row["confidence"] == 80
    assert row["observation_count"] == 3
    assert row["distinct_projects"] == 2


def test_upsert_updates_existing_row(conn: sqlite3.Connection) -> None:
    rid_1 = upsert_rule(conn, **_make_upsert_kwargs(observation_count=3, confidence=75))
    row_initial = _row_for(conn, rid_1)
    initial_created_at = row_initial["created_at"]

    # Force a measurable timestamp delta so updated_at advances.
    time.sleep(0.01)

    rid_2 = upsert_rule(
        conn,
        **_make_upsert_kwargs(
            observation_count=5,
            distinct_projects=3,
            confidence=85,
            last_postmortem_id=2,
        ),
    )
    assert rid_2 == rid_1, "second upsert should hit the same row id"

    row_after = _row_for(conn, rid_2)
    assert row_after["status"] == "probation", "status must not change on UPDATE branch"
    assert row_after["confidence"] == 85
    assert row_after["observation_count"] == 5
    assert row_after["distinct_projects"] == 3
    assert row_after["last_postmortem_id"] == 2
    assert row_after["created_at"] == initial_created_at
    assert row_after["updated_at"] >= initial_created_at

    # Only one row exists for the (scope, agent_target, signature) triple.
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM feedback_rules "
        "WHERE scope=? AND agent_target=? AND signature=?",
        ("drupal", "developer", "phpstan.notfound::Foo::bar"),
    ).fetchone()["n"]
    assert count == 1


def test_upsert_returns_canonical_id_after_update(conn: sqlite3.Connection) -> None:
    """Plan task 5: returned rowid on UPDATE must equal the actual row id, not 0
    or whatever lastrowid reports on the UPSERT update branch."""
    rid_1 = upsert_rule(conn, **_make_upsert_kwargs(signature="canonical-test"))

    # Insert ANOTHER (different signature) row in between to ensure that
    # lastrowid would otherwise drift if the helper relied on it.
    other_id = upsert_rule(
        conn,
        **_make_upsert_kwargs(signature="other-sig", scope="drupal"),
    )
    assert other_id != rid_1

    rid_again = upsert_rule(
        conn,
        **_make_upsert_kwargs(
            signature="canonical-test",
            confidence=90,
            observation_count=4,
        ),
    )
    assert rid_again == rid_1

    # And the row really does have id == rid_1.
    row = conn.execute(
        "SELECT id FROM feedback_rules "
        "WHERE scope=? AND agent_target=? AND signature=? "
        "  AND status IN ('probation', 'active')",
        ("drupal", "developer", "canonical-test"),
    ).fetchone()
    assert row["id"] == rid_1


# ---------------------------------------------------------------------------
# query_promotable
# ---------------------------------------------------------------------------


def test_query_promotable_filters_by_confidence_and_status(
    conn: sqlite3.Connection,
) -> None:
    # 1: probation conf=85 — should be returned.
    rid_hi = upsert_rule(
        conn, **_make_upsert_kwargs(signature="hi", confidence=85)
    )
    # 2: probation conf=70 — below floor 80.
    upsert_rule(conn, **_make_upsert_kwargs(signature="lo", confidence=70))
    # 3: probation conf=95, then promoted -> active. Below: not 'probation'.
    rid_active = upsert_rule(
        conn, **_make_upsert_kwargs(signature="active-95", confidence=95)
    )
    mark_promoted(conn, rule_id=rid_active, sha="abc1234", promoted_by="alice")
    # 4: probation conf=85 then revoked. Below: not 'probation'.
    rid_revoked = upsert_rule(
        conn, **_make_upsert_kwargs(signature="revoked-85", confidence=85)
    )
    revoke_rule(conn, rule_id=rid_revoked, revoked_by="bob", reason="bad")

    rows = query_promotable(conn, scope=None, min_confidence=80)
    ids = [r["id"] for r in rows]
    assert ids == [rid_hi], (
        f"only the conf=85 probation row should be promotable; got ids={ids}"
    )


def test_query_promotable_only_unproposed(conn: sqlite3.Connection) -> None:
    rid = upsert_rule(conn, **_make_upsert_kwargs(signature="prop-test", confidence=85))

    # Before mark_proposed: included.
    rows_before = query_promotable(conn, min_confidence=80, only_unproposed=True)
    assert rid in [r["id"] for r in rows_before]

    mark_proposed(
        conn,
        rule_id=rid,
        overlay_path="prompts/overlays/drupal_developer.md",
        mr_url="https://gl/proj/-/merge_requests/1",
    )

    rows_after = query_promotable(conn, min_confidence=80, only_unproposed=True)
    assert rid not in [r["id"] for r in rows_after]

    # only_unproposed=False brings it back.
    rows_force = query_promotable(conn, min_confidence=80, only_unproposed=False)
    assert rid in [r["id"] for r in rows_force]


def test_query_promotable_scope_filter(conn: sqlite3.Connection) -> None:
    rid_dru = upsert_rule(
        conn,
        **_make_upsert_kwargs(signature="scope-d", scope="drupal", confidence=85),
    )
    rid_py = upsert_rule(
        conn,
        **_make_upsert_kwargs(signature="scope-p", scope="python", confidence=85),
    )

    drupal_rows = query_promotable(conn, scope="drupal", min_confidence=80)
    drupal_ids = [r["id"] for r in drupal_rows]
    assert drupal_ids == [rid_dru]

    py_rows = query_promotable(conn, scope="python", min_confidence=80)
    py_ids = [r["id"] for r in py_rows]
    assert py_ids == [rid_py]


# ---------------------------------------------------------------------------
# mark_promoted
# ---------------------------------------------------------------------------


def test_mark_promoted_flips_status_and_records_sha(conn: sqlite3.Connection) -> None:
    rid = upsert_rule(conn, **_make_upsert_kwargs(signature="promote-test"))
    pre = _row_for(conn, rid)
    assert pre["status"] == "probation"
    assert pre["promoted_to_overlay_sha"] is None
    assert pre["promoted_by"] is None
    assert pre["promoted_at"] is None

    mark_promoted(conn, rule_id=rid, sha="def4567", promoted_by="alice")

    after = _row_for(conn, rid)
    assert after["status"] == "active"
    assert after["promoted_to_overlay_sha"] == "def4567"
    assert after["promoted_by"] == "alice"
    assert after["promoted_at"]


def test_mark_promoted_rejects_non_probation(conn: sqlite3.Connection) -> None:
    rid = upsert_rule(conn, **_make_upsert_kwargs(signature="non-prob"))
    mark_promoted(conn, rule_id=rid, sha="abcdef0", promoted_by="alice")  # status=active

    with pytest.raises(ValueError):
        mark_promoted(conn, rule_id=rid, sha="abcdef1", promoted_by="alice")

    rid_revoked = upsert_rule(
        conn, **_make_upsert_kwargs(signature="non-prob-revoked")
    )
    revoke_rule(conn, rule_id=rid_revoked, revoked_by="alice", reason="r")
    with pytest.raises(ValueError):
        mark_promoted(conn, rule_id=rid_revoked, sha="abcdef2", promoted_by="alice")


@pytest.mark.parametrize(
    "good_sha",
    [
        "a1b2c3d",                                   # 7-char short SHA (Git default)
        "abcdef0123456789abcdef0123456789abcdef01",  # 40-char full SHA-1
        "0" * 64,                                    # 64-char (future SHA-256 lower bound)
    ],
)
def test_mark_promoted_accepts_valid_sha(
    conn: sqlite3.Connection, good_sha: str
) -> None:
    rid = upsert_rule(
        conn, **_make_upsert_kwargs(signature=f"sig-{good_sha[:8]}")
    )
    mark_promoted(conn, rule_id=rid, sha=good_sha, promoted_by="alice")
    row = _row_for(conn, rid)
    assert row["promoted_to_overlay_sha"] == good_sha


@pytest.mark.parametrize(
    "bad_sha",
    [
        "",                                       # empty
        "abc",                                    # too short (3 chars)
        "abcdef",                                 # too short (6 chars, just under 7)
        "ABCDEF1",                                # uppercase
        "g1b2c3d",                                # non-hex char 'g'
        "abc1234 ",                               # trailing whitespace
        " abc1234",                               # leading whitespace
        "abc 1234",                               # internal whitespace
        "a" * 65,                                 # too long (65 chars)
        "abc1234\n",                              # trailing newline
    ],
)
def test_mark_promoted_rejects_invalid_sha(
    conn: sqlite3.Connection, bad_sha: str
) -> None:
    rid = upsert_rule(
        conn, **_make_upsert_kwargs(signature=f"sig-bad-{hash(bad_sha) & 0xff:x}")
    )
    with pytest.raises(ValueError, match="sha must be 7-64 lowercase hex"):
        mark_promoted(conn, rule_id=rid, sha=bad_sha, promoted_by="alice")
    # Critical: rejection must NOT have mutated the row.
    row = _row_for(conn, rid)
    assert row["status"] == "probation"
    assert row["promoted_to_overlay_sha"] is None


# ---------------------------------------------------------------------------
# revoke_rule
# ---------------------------------------------------------------------------


def test_revoke_rule_terminal(conn: sqlite3.Connection) -> None:
    rid = upsert_rule(conn, **_make_upsert_kwargs(signature="rev-1"))
    revoke_rule(conn, rule_id=rid, revoked_by="alice", reason="policy change")

    row = _row_for(conn, rid)
    assert row["status"] == "revoked"
    assert row["revoked_by"] == "alice"
    assert row["revocation_reason"] == "policy change"
    assert row["revoked_at"]

    # Second revoke is rejected.
    with pytest.raises(ValueError):
        revoke_rule(conn, rule_id=rid, revoked_by="alice", reason="again")


def test_revoke_rule_does_not_delete(conn: sqlite3.Connection) -> None:
    rid = upsert_rule(conn, **_make_upsert_kwargs(signature="rev-no-del"))
    revoke_rule(conn, rule_id=rid, revoked_by="alice", reason="r")

    row = conn.execute(
        "SELECT * FROM feedback_rules WHERE id = ?", (rid,)
    ).fetchone()
    assert row is not None
    assert row["id"] == rid
    assert row["status"] == "revoked"


# ---------------------------------------------------------------------------
# mark_superseded
# ---------------------------------------------------------------------------


def test_mark_superseded_chain(conn: sqlite3.Connection) -> None:
    """A is superseded by B (B has a different signature so the partial unique
    index doesn't collide while A is still 'probation')."""
    a_id = upsert_rule(
        conn,
        **_make_upsert_kwargs(signature="A-sig", confidence=80),
    )
    b_id = upsert_rule(
        conn,
        **_make_upsert_kwargs(signature="B-sig", confidence=85),
    )
    assert a_id != b_id

    mark_superseded(conn, old_rule_id=a_id, new_rule_id=b_id)

    a_row = _row_for(conn, a_id)
    assert a_row["status"] == "superseded"
    assert a_row["superseded_by"] == b_id

    b_row = _row_for(conn, b_id)
    assert b_row["status"] == "probation"  # not touched
    assert b_row["superseded_by"] is None

    # query_promotable(only_unproposed=False) returns B but not A
    # (A is no longer 'probation').
    rows = query_promotable(conn, min_confidence=80, only_unproposed=False)
    ids = [r["id"] for r in rows]
    assert b_id in ids
    assert a_id not in ids


def test_mark_superseded_rejects_missing_new(conn: sqlite3.Connection) -> None:
    a_id = upsert_rule(conn, **_make_upsert_kwargs(signature="A-only"))
    with pytest.raises(ValueError):
        mark_superseded(conn, old_rule_id=a_id, new_rule_id=99999)


def test_mark_superseded_allows_resurrection(conn: sqlite3.Connection) -> None:
    """After A is superseded, a fresh probation row with the SAME
    (scope, agent_target, signature) can be inserted — the partial unique
    index excludes superseded predecessors."""
    a_id = upsert_rule(conn, **_make_upsert_kwargs(signature="reborn"))
    b_id = upsert_rule(conn, **_make_upsert_kwargs(signature="anchor"))
    mark_superseded(conn, old_rule_id=a_id, new_rule_id=b_id)

    # Now insert a fresh probation row with the same triple as A.
    revived = upsert_rule(conn, **_make_upsert_kwargs(signature="reborn", confidence=70))
    assert revived != a_id

    row = _row_for(conn, revived)
    assert row["status"] == "probation"
    assert row["confidence"] == 70


# ---------------------------------------------------------------------------
# list_rules
# ---------------------------------------------------------------------------


def test_list_rules_filters_correctly(conn: sqlite3.Connection) -> None:
    rid_active = upsert_rule(
        conn,
        **_make_upsert_kwargs(signature="act-1", scope="drupal", confidence=90),
    )
    mark_promoted(conn, rule_id=rid_active, sha="abcdef3", promoted_by="alice")

    rid_prob_drupal = upsert_rule(
        conn,
        **_make_upsert_kwargs(signature="prob-drupal", scope="drupal", confidence=80),
    )
    rid_prob_py = upsert_rule(
        conn,
        **_make_upsert_kwargs(signature="prob-py", scope="python", confidence=80),
    )

    rid_revoked = upsert_rule(
        conn,
        **_make_upsert_kwargs(signature="revoked-1", scope="drupal", confidence=85),
    )
    revoke_rule(conn, rule_id=rid_revoked, revoked_by="alice", reason="r")

    # status='active' -> only the promoted row.
    active_rows = list_rules(conn, status="active")
    assert [r["id"] for r in active_rows] == [rid_active]

    # scope='drupal' -> drupal rows only (active + probation drupal + revoked drupal).
    drupal_rows = list_rules(conn, scope="drupal")
    drupal_ids = {r["id"] for r in drupal_rows}
    assert drupal_ids == {rid_active, rid_prob_drupal, rid_revoked}

    # No filters → everything.
    all_rows = list_rules(conn)
    all_ids = {r["id"] for r in all_rows}
    assert all_ids == {rid_active, rid_prob_drupal, rid_prob_py, rid_revoked}


# ---------------------------------------------------------------------------
# Append-only invariant: no UPDATE/DELETE helpers exported
# ---------------------------------------------------------------------------


def test_no_update_or_delete_helpers_exported() -> None:
    names = dir(feedback_rules_module)
    assert "update_rule" not in names, (
        "Append-only invariant violated: feedback_rules.update_rule must not exist"
    )
    assert "delete_rule" not in names, (
        "Append-only invariant violated: feedback_rules.delete_rule must not exist"
    )
