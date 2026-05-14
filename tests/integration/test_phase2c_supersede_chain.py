"""Phase 2C Task 17 — supersede-chain integration test.

Pure persistence + event-table assertions. NO CLI, NO git repo, NO subprocess.
Verifies that ``mark_superseded`` on a promoted (active) rule:

  * flips OLD.status='superseded' and points OLD.superseded_by at NEW,
  * leaves OLD's other audit columns (``promoted_to_overlay_sha``,
    ``promoted_by``) untouched — supersession is not erasure,
  * leaves NEW unchanged (status='probation', superseded_by IS NULL),
  * removes OLD from ``query_promotable`` (it's no longer probation),
  * frees the partial unique index slot so a fresh probation row can land
    with the SAME ``(scope, agent_target, signature)`` as OLD,
  * does NOT publish ``FeedbackRuleRevoked`` (supersession is its own state,
    not revocation).

Plus: confirms the ``revoke_rule`` contract on a 'superseded' row. The
helper's docstring (src/core/persistence/feedback_rules.py:262) says "a
superseded row can in principle be revoked too, though that's unusual",
and only re-revoking an already-revoked row raises. We assert that
behavior — revoking a superseded row succeeds.

Plan ref: .claude/PRPs/plans/phase-2c-promotion-path.plan.md task 17.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from src.core.persistence import (
    apply_migrations,
    connect,
    insert_postmortem,
    mark_promoted,
    mark_proposed,
    mark_superseded,
    query_promotable,
    revoke_rule,
    upsert_rule,
)


# ---------------------------------------------------------------------------
# Inline fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn() -> sqlite3.Connection:
    """Migrated in-memory SQLite with one parent execution + parent postmortem.

    The parent rows exist so ``feedback_rules.first_postmortem_id /
    last_postmortem_id`` FKs are satisfiable. ``row_factory`` is set by
    ``connect()`` and ``PRAGMA foreign_keys=ON`` is on by default.
    """
    c = connect(":memory:")
    apply_migrations(c)

    now = datetime.now(timezone.utc).isoformat()
    c.execute(
        "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
        "VALUES (?, ?, 'developer', 'completed', ?)",
        ("exec-parent-1", "ACME-100", now),
    )
    c.commit()

    pm_id = insert_postmortem(
        c,
        execution_id="exec-parent-1",
        stack_type="drupal",
        agent="developer",
        failure_signature="phpunit::DrupalParent::testThing",
        context_excerpt="ctx",
        provenance="auto",
        confidence=50,
    )
    # Pin the postmortem id used by both rules' FKs.
    assert pm_id == 1
    return c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_supersede_chain_full_workflow(conn: sqlite3.Connection) -> None:
    """A: probation → proposed → active → superseded by B; B: probation."""
    # --- Insert rule A and walk it through the lifecycle to 'active'.
    rule_a_id = upsert_rule(
        conn,
        signature="sig-A",
        scope="drupal",
        agent_target="developer",
        rule_text="phpunit failure pattern A",
        confidence=80,
        observation_count=3,
        distinct_projects=2,
        first_postmortem_id=1,
        last_postmortem_id=1,
    )
    mark_proposed(
        conn,
        rule_id=rule_a_id,
        overlay_path="prompts/overlays/drupal_developer.md",
        mr_url="https://gl.example.com/sentinel-team/sentinel/-/merge_requests/42",
    )
    mark_promoted(conn, rule_id=rule_a_id, sha="aaa1234", promoted_by="alice")

    row_a = conn.execute(
        "SELECT * FROM feedback_rules WHERE id = ?", (rule_a_id,)
    ).fetchone()
    assert row_a["status"] == "active"
    assert row_a["promoted_to_overlay_sha"] == "aaa1234"
    assert row_a["promoted_by"] == "alice"
    assert row_a["proposed_overlay_mr_url"].endswith("/42")
    assert row_a["superseded_by"] is None

    # --- Insert rule B with a DIFFERENT signature so the partial unique index
    # allows it to coexist with A while A is still active.
    rule_b_id = upsert_rule(
        conn,
        signature="sig-B",
        scope="drupal",
        agent_target="developer",
        rule_text="phpunit failure pattern B (replaces A)",
        confidence=85,
        observation_count=4,
        distinct_projects=3,
        first_postmortem_id=1,
        last_postmortem_id=1,
    )
    assert rule_b_id != rule_a_id

    # --- Supersede.
    mark_superseded(conn, old_rule_id=rule_a_id, new_rule_id=rule_b_id)

    # A: status flipped, pointer set, audit columns untouched.
    row_a_after = conn.execute(
        "SELECT * FROM feedback_rules WHERE id = ?", (rule_a_id,)
    ).fetchone()
    assert row_a_after["status"] == "superseded"
    assert row_a_after["superseded_by"] == rule_b_id
    assert row_a_after["promoted_to_overlay_sha"] == "aaa1234", (
        "supersession must NOT erase A's promotion audit"
    )
    assert row_a_after["promoted_by"] == "alice"
    assert (
        row_a_after["proposed_overlay_mr_url"]
        == "https://gl.example.com/sentinel-team/sentinel/-/merge_requests/42"
    )

    # B: untouched — still probation, no successor.
    row_b_after = conn.execute(
        "SELECT * FROM feedback_rules WHERE id = ?", (rule_b_id,)
    ).fetchone()
    assert row_b_after["status"] == "probation"
    assert row_b_after["superseded_by"] is None
    assert row_b_after["promoted_at"] is None

    # query_promotable reports B (probation) but never A (now superseded).
    promotable = query_promotable(
        conn,
        scope="drupal",
        min_confidence=80,
        only_unproposed=False,
    )
    promotable_ids = {int(r["id"]) for r in promotable}
    assert rule_b_id in promotable_ids
    assert rule_a_id not in promotable_ids, (
        "superseded rules must not appear in query_promotable"
    )

    # The partial unique index now permits a fresh probation row with the
    # SAME (scope, agent_target, signature) as A — A no longer occupies the
    # slot because its status is 'superseded'.
    rule_a_replacement_id = upsert_rule(
        conn,
        signature="sig-A",  # same as the original A
        scope="drupal",
        agent_target="developer",
        rule_text="phpunit failure pattern A — fresh observation after revocation",
        confidence=70,
        observation_count=2,
        distinct_projects=2,
        first_postmortem_id=1,
        last_postmortem_id=1,
    )
    assert rule_a_replacement_id != rule_a_id
    assert rule_a_replacement_id != rule_b_id

    # No FeedbackRuleRevoked event was emitted (no bus was constructed and
    # supersession is not revocation). Assert explicitly so a future change
    # that publishes one through some other path fails this regression.
    revoked_count = conn.execute(
        "SELECT COUNT(*) AS c FROM events WHERE type = 'FeedbackRuleRevoked'"
    ).fetchone()
    assert revoked_count["c"] == 0


def test_revoke_on_superseded_row_succeeds(conn: sqlite3.Connection) -> None:
    """``revoke_rule`` accepts a 'superseded' row per its documented contract.

    From feedback_rules.py:262-263: "a superseded row can in principle be
    revoked too, though that's unusual." The helper only rejects
    re-revoking an already-revoked row — every other prior status is
    accepted.

    Behavior discovered from source: revoking a superseded row flips
    ``status`` to 'revoked' and stamps ``revoked_*`` columns. The pre-flip
    ``superseded_by`` pointer is preserved (not cleared) — the helper only
    UPDATEs the four revocation columns plus ``updated_at``.
    """
    # Set up A → B supersession.
    rule_a_id = upsert_rule(
        conn,
        signature="sig-A",
        scope="drupal",
        agent_target="developer",
        rule_text="A",
        confidence=80,
        observation_count=3,
        distinct_projects=2,
        first_postmortem_id=1,
        last_postmortem_id=1,
    )
    rule_b_id = upsert_rule(
        conn,
        signature="sig-B",
        scope="drupal",
        agent_target="developer",
        rule_text="B",
        confidence=85,
        observation_count=4,
        distinct_projects=3,
        first_postmortem_id=1,
        last_postmortem_id=1,
    )
    mark_superseded(conn, old_rule_id=rule_a_id, new_rule_id=rule_b_id)

    # Revoke the superseded row.
    revoke_rule(
        conn,
        rule_id=rule_a_id,
        revoked_by="alice",
        reason="post-supersession cleanup",
    )

    row = conn.execute(
        "SELECT status, superseded_by, revoked_by, revocation_reason "
        "FROM feedback_rules WHERE id = ?",
        (rule_a_id,),
    ).fetchone()
    assert row["status"] == "revoked", (
        "revoke_rule on a superseded row must flip status to 'revoked'"
    )
    assert row["revoked_by"] == "alice"
    assert row["revocation_reason"] == "post-supersession cleanup"
    # The supersession pointer is preserved — revoke_rule only touches the
    # four revocation columns + updated_at, not superseded_by.
    assert row["superseded_by"] == rule_b_id

    # Re-revoking the now-revoked row must raise — masking intent is the
    # one transition the helper rejects.
    with pytest.raises(ValueError, match="already revoked"):
        revoke_rule(
            conn,
            rule_id=rule_a_id,
            revoked_by="bob",
            reason="duplicate",
        )
