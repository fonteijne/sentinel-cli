"""Phase 3A persistence helpers -- project_sync_state + executions.outcome.

Design invariants (design §8 task 14, DECISIONS.md D6, plan Task 2):

  - ``executions.outcome`` is **append-once**: a row's outcome can be set from
    NULL to one of {success, rolled_back, regressed}, but never overwritten.
    Once ground truth lands it does not change without explicit human
    intervention. Enforced via ``WHERE outcome IS NULL`` in the UPDATE -- not
    a CHECK constraint, because SQLite has no per-column update trigger and
    a row-level CHECK can't see the prior value.

  - ``project_sync_state`` is **per-installation** (D6): no ``installation_id``
    column. The DB file itself identifies the Sentinel installation. Rows are
    upserted; there is no delete helper.

  - There is no ``update_*`` for project_sync_state beyond the upsert, and no
    ``delete_*`` for either table. Mirrors the postmortems / feedback_rules
    discipline: persistence layer is append-only at its boundary.
"""

from __future__ import annotations

import sqlite3
from typing import Optional

_VALID_OUTCOMES = frozenset({"success", "rolled_back", "regressed"})


def read_sync_state(
    conn: sqlite3.Connection,
    project: str,
) -> Optional[sqlite3.Row]:
    """Return the watermark row for ``project`` or None if never synced.

    Caller uses ``last_seen_updated_at`` as the next ``updated_after`` query
    param. Returning None signals "no watermark yet" so the caller can decide
    between full backfill and a default lookback.

    Requires ``conn.row_factory == sqlite3.Row`` (set by ``connect()``).
    """
    cursor = conn.execute(
        """
        SELECT project, last_synced_at, last_seen_mr_iid, last_seen_updated_at
          FROM project_sync_state
         WHERE project = ?
        """,
        (project,),
    )
    row: Optional[sqlite3.Row] = cursor.fetchone()
    return row


def upsert_sync_state(
    conn: sqlite3.Connection,
    *,
    project: str,
    last_synced_at: str,
    last_seen_mr_iid: Optional[int],
    last_seen_updated_at: Optional[str],
) -> None:
    """Insert or update the watermark for ``project``.

    Uses ``INSERT ... ON CONFLICT(project) DO UPDATE`` (SQLite >= 3.24, well
    below the 3.40+ that ships with Python 3.11 on Linux). Keyword-only after
    ``conn`` so callers can't accidentally swap the two ISO-8601 timestamps.
    """
    conn.execute(
        """
        INSERT INTO project_sync_state (
            project, last_synced_at, last_seen_mr_iid, last_seen_updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(project) DO UPDATE SET
            last_synced_at       = excluded.last_synced_at,
            last_seen_mr_iid     = excluded.last_seen_mr_iid,
            last_seen_updated_at = excluded.last_seen_updated_at
        """,
        (project, last_synced_at, last_seen_mr_iid, last_seen_updated_at),
    )
    conn.commit()


def update_execution_outcome(
    conn: sqlite3.Connection,
    *,
    execution_id: str,
    outcome: str,
    evidence_json: str,
    recorded_at: str,
) -> int:
    """Tag an execution with a ground-truth outcome. Append-once.

    Returns ``cursor.rowcount`` -- 1 if the row was tagged, 0 if it was already
    tagged (the ``WHERE outcome IS NULL`` clause makes the second UPDATE a
    no-op rather than an overwrite). Callers use the return value to decide
    whether to publish an ``OutcomeRecorded`` event.

    ``evidence_json`` is stored verbatim so the audit trail survives even if
    upstream GitLab data later changes; the schema does not validate JSON
    shape (the service decides what to record).

    Raises ``ValueError`` if ``outcome`` is not one of the three accepted
    labels -- the SQL CHECK would catch it too, but a Python-level raise gives
    a clearer stack trace at the call site.
    """
    if outcome not in _VALID_OUTCOMES:
        raise ValueError(
            f"outcome must be one of {sorted(_VALID_OUTCOMES)}; got {outcome!r}"
        )

    cursor = conn.execute(
        """
        UPDATE executions
           SET outcome              = ?,
               outcome_evidence_json = ?,
               outcome_recorded_at   = ?
         WHERE id = ?
           AND outcome IS NULL
        """,
        (outcome, evidence_json, recorded_at, execution_id),
    )
    conn.commit()
    return cursor.rowcount


def list_executions_for_ticket_untagged(
    conn: sqlite3.Connection,
    ticket_id: str,
) -> list[sqlite3.Row]:
    """Return all executions for ``ticket_id`` that have not yet been tagged.

    Ordered by ``created_at`` ascending so the caller tags chronologically
    (older runs get the same outcome as newer runs for the same merged MR --
    the branch-name match is per ticket, not per execution).

    Empty list means "either Sentinel never ran this ticket or every prior
    run is already tagged" -- the service treats both as "skip this MR".
    """
    cursor = conn.execute(
        """
        SELECT id, ticket_id, created_at
          FROM executions
         WHERE ticket_id = ?
           AND outcome IS NULL
         ORDER BY created_at ASC
        """,
        (ticket_id,),
    )
    return cursor.fetchall()
