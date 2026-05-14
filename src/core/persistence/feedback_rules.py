"""Feedback-rules helpers — append-only canonical-rule store.

Design invariants (plan §"Patterns to Mirror" / D4 / append-only invariant):

  - There is NO ``update_rule`` and NO ``delete_rule``. Tests assert these
    names are not module attributes. Mutation of an existing row is restricted
    to status transitions modeled by the named helpers below
    (``mark_proposed`` / ``mark_promoted`` / ``revoke_rule`` / ``mark_superseded``)
    plus the UPSERT branch of ``upsert_rule`` which only bumps counts and
    timestamps on a still-live row.
  - ``status`` is constrained to {'probation','active','superseded','revoked'}.
    A live rule is one whose status is in {'probation','active'} — exactly the
    predicate used by the partial unique index ``idx_feedback_rules_dedup``.
  - Revocation never deletes. ``revoke_rule`` flips status and stamps
    ``revoked_by/at/reason`` on the same row.
  - Supersession never deletes. ``mark_superseded`` points the OLD row at the
    NEW row and flips OLD.status to 'superseded' inside one transaction so a
    crash mid-call cannot leave a row pointing at a successor whose own status
    is still 'probation'.
  - All write helpers bump ``updated_at`` to a fresh UTC ISO timestamp.
  - ``mark_promoted`` validates ``sha`` against ``^[0-9a-f]{7,64}$`` before any
    DB I/O. SHA is append-only per D4 — typos caught at the leaf can never
    become a permanent ghost row.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

_VALID_STATUS = frozenset({"probation", "active", "superseded", "revoked"})
_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_rule(
    conn: sqlite3.Connection,
    *,
    signature: str,
    scope: str,
    agent_target: str,
    rule_text: str,
    confidence: int,
    observation_count: int,
    distinct_projects: int,
    first_postmortem_id: int,
    last_postmortem_id: int,
) -> int:
    """Insert a probation row or update an existing live one. Returns the rowid.

    The conflict target is the partial unique index
    ``idx_feedback_rules_dedup ON (scope, agent_target, signature)
    WHERE status IN ('probation','active')`` — superseded/revoked predecessors
    do not participate so a fresh probation row can land after a revocation.

    On the UPDATE branch we only bump counts, confidence, ``last_postmortem_id``,
    and ``updated_at``; ``status`` is NOT touched (a live row stays live; a
    promoted row stays promoted).
    """
    now = _utcnow_iso()
    cursor = conn.execute(
        """
        INSERT INTO feedback_rules (
            signature, scope, agent_target, rule_text, status, confidence,
            observation_count, distinct_projects, first_postmortem_id,
            last_postmortem_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'probation', ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope, agent_target, signature)
        WHERE status IN ('probation', 'active')
        DO UPDATE SET
            confidence         = excluded.confidence,
            observation_count  = excluded.observation_count,
            distinct_projects  = excluded.distinct_projects,
            last_postmortem_id = excluded.last_postmortem_id,
            updated_at         = excluded.updated_at
        """,
        (
            signature,
            scope,
            agent_target,
            rule_text,
            confidence,
            observation_count,
            distinct_projects,
            first_postmortem_id,
            last_postmortem_id,
            now,
            now,
        ),
    )
    conn.commit()

    # `lastrowid` is unreliable on the UPSERT update branch (sqlite3 may return
    # 0 or the previous INSERT's rowid). Recover the canonical id by SELECT.
    row = conn.execute(
        """
        SELECT id FROM feedback_rules
        WHERE scope = ? AND agent_target = ? AND signature = ?
          AND status IN ('probation', 'active')
        """,
        (scope, agent_target, signature),
    ).fetchone()
    if row is None:  # pragma: no cover — partial unique index guarantees a live row
        # Fall back to lastrowid if somehow no live row matches (shouldn't happen
        # because we just inserted/updated one). This keeps the helper honest.
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        raise RuntimeError("upsert_rule: could not recover canonical row id")
    return int(row["id"])


def query_promotable(
    conn: sqlite3.Connection,
    *,
    scope: Optional[str] = None,
    min_confidence: int = 80,
    only_unproposed: bool = True,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """Return probation rows ready for promotion, ordered by confidence DESC.

    Filters: ``status='probation'`` AND ``confidence >= min_confidence``.
    When ``only_unproposed`` (default True), excludes rows whose ``proposed_at``
    is already populated — the proposer should not re-propose a rule whose MR
    is already open. Optional ``scope`` filter for per-stack runs.
    """
    sql = (
        "SELECT * FROM feedback_rules "
        "WHERE status = 'probation' "
        "  AND confidence >= ? "
    )
    params: list = [min_confidence]
    if only_unproposed:
        sql += "  AND proposed_at IS NULL "
    if scope is not None:
        sql += "  AND scope = ? "
        params.append(scope)
    sql += "ORDER BY confidence DESC, updated_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, tuple(params)).fetchall()


def list_rules(
    conn: sqlite3.Connection,
    *,
    status: Optional[str] = None,
    scope: Optional[str] = None,
    limit: int = 50,
) -> list[sqlite3.Row]:
    """Return rules for the CLI inspector.

    No status filter ⇒ all rules (including superseded and revoked) so a
    maintainer auditing the ledger can see the full history. Optional
    ``scope`` filter.
    """
    if status is not None and status not in _VALID_STATUS:
        raise ValueError(
            f"status must be one of {sorted(_VALID_STATUS)} or None; got {status!r}"
        )

    sql = "SELECT * FROM feedback_rules WHERE 1=1 "
    params: list = []
    if status is not None:
        sql += "  AND status = ? "
        params.append(status)
    if scope is not None:
        sql += "  AND scope = ? "
        params.append(scope)
    sql += "ORDER BY confidence DESC, updated_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, tuple(params)).fetchall()


def mark_proposed(
    conn: sqlite3.Connection,
    *,
    rule_id: int,
    overlay_path: str,
    mr_url: str,
) -> None:
    """Record that a draft MR has been opened for this rule.

    Sets ``proposed_overlay_path``, ``proposed_overlay_mr_url``, ``proposed_at``,
    bumps ``updated_at``. Status is intentionally NOT changed — promotion to
    'active' only happens on ``mark_promoted`` after a maintainer merges.
    """
    now = _utcnow_iso()
    conn.execute(
        """
        UPDATE feedback_rules
           SET proposed_overlay_path   = ?,
               proposed_overlay_mr_url = ?,
               proposed_at             = ?,
               updated_at              = ?
         WHERE id = ?
        """,
        (overlay_path, mr_url, now, now, rule_id),
    )
    conn.commit()


def mark_promoted(
    conn: sqlite3.Connection,
    *,
    rule_id: int,
    sha: str,
    promoted_by: str,
) -> None:
    """Flip a probation row to 'active' after the maintainer merges the MR.

    Verify-then-update inside one BEGIN IMMEDIATE / COMMIT so a parallel
    ``revoke_rule`` cannot race with the status check. Raises ``ValueError``
    if the row's prior status was not 'probation' (an already-active or
    revoked or superseded row must not be silently re-promoted).

    Raises ``ValueError`` if ``sha`` does not match ``^[0-9a-f]{7,64}$`` (Git
    short or full SHA, lowercase hex only). Validated *before* opening the
    transaction so a malformed input never reaches DB I/O.
    """
    if not _SHA_RE.fullmatch(sha):
        raise ValueError(
            f"sha must be 7-64 lowercase hex characters; got {sha!r}"
        )
    now = _utcnow_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT status FROM feedback_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"feedback_rule id={rule_id} not found")
        if row["status"] != "probation":
            raise ValueError(
                f"mark_promoted requires status='probation'; "
                f"rule id={rule_id} is status={row['status']!r}"
            )
        conn.execute(
            """
            UPDATE feedback_rules
               SET status                  = 'active',
                   promoted_to_overlay_sha = ?,
                   promoted_by             = ?,
                   promoted_at             = ?,
                   updated_at              = ?
             WHERE id = ?
            """,
            (sha, promoted_by, now, now, rule_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def revoke_rule(
    conn: sqlite3.Connection,
    *,
    rule_id: int,
    revoked_by: str,
    reason: str,
) -> None:
    """Flip a rule's status to 'revoked'. Append-only — does NOT delete.

    Raises ``ValueError`` if the prior status was already 'revoked' (idempotent
    revocation would mask intent — force the operator to look at the existing
    row before re-asserting). All other prior statuses are accepted: a
    probation row can be revoked before promotion; an active row can be
    revoked after promotion; a superseded row can in principle be revoked
    too, though that's unusual.
    """
    now = _utcnow_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT status FROM feedback_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"feedback_rule id={rule_id} not found")
        if row["status"] == "revoked":
            raise ValueError(
                f"feedback_rule id={rule_id} is already revoked; "
                "re-revoking would mask intent"
            )
        conn.execute(
            """
            UPDATE feedback_rules
               SET status            = 'revoked',
                   revoked_by        = ?,
                   revoked_at        = ?,
                   revocation_reason = ?,
                   updated_at        = ?
             WHERE id = ?
            """,
            (revoked_by, now, reason, now, rule_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def mark_superseded(
    conn: sqlite3.Connection,
    *,
    old_rule_id: int,
    new_rule_id: int,
) -> None:
    """Point ``old_rule_id`` at ``new_rule_id`` and flip OLD.status='superseded'.

    Both UPDATEs run inside one explicit BEGIN IMMEDIATE / COMMIT so a crash
    mid-call cannot leave a row pointing at a successor whose own status is
    still 'probation' (and therefore still occupies the partial unique index
    slot, which would block the supersession). The new row is left untouched.

    Validates that ``new_rule_id`` exists before attempting the UPDATE so the
    error message is clearer than the FK violation we'd otherwise get.
    """
    now = _utcnow_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        new_row = conn.execute(
            "SELECT id FROM feedback_rules WHERE id = ?",
            (new_rule_id,),
        ).fetchone()
        if new_row is None:
            raise ValueError(
                f"mark_superseded: new_rule_id={new_rule_id} not found"
            )
        old_row = conn.execute(
            "SELECT id FROM feedback_rules WHERE id = ?",
            (old_rule_id,),
        ).fetchone()
        if old_row is None:
            raise ValueError(
                f"mark_superseded: old_rule_id={old_rule_id} not found"
            )
        conn.execute(
            """
            UPDATE feedback_rules
               SET superseded_by = ?,
                   status        = 'superseded',
                   updated_at    = ?
             WHERE id = ?
            """,
            (new_rule_id, now, old_rule_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
