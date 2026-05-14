"""Postmortems helper — append-only inserts only.

Design invariants (design §6.2, Decision 4, plan Task 2):

  - ``provenance`` is NOT NULL and constrained to {'auto', 'human-edited'}.
    Phase 1 only ever inserts 'auto'; the 'human-edited' value exists for
    Phase 2's edit path. Reject None at the helper boundary so callers fail
    loudly rather than write a row that violates the schema.
  - Repeated inserts with the same ``failure_signature`` MUST NOT be
    de-duplicated here. Phase 2's extraction job decides what to merge or
    revoke (via ``superseded_by``). The persistence layer just appends.
  - There is no ``update_postmortem`` and no ``delete_postmortem``. Revocation
    is modeled by inserting a new row and pointing the old row's
    ``superseded_by`` at it (a Phase 2 path; not exercised here).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional

_VALID_PROVENANCE = frozenset({"auto", "human-edited"})


def insert_postmortem(
    conn: sqlite3.Connection,
    *,
    execution_id: str,
    stack_type: str,
    agent: str,
    failure_signature: str,
    context_excerpt: Optional[str] = None,
    fix_summary: Optional[str] = None,
    provenance: str = "auto",
    confidence: int = 50,
) -> int:
    """Insert one postmortem row and return its rowid.

    Keyword-only after ``conn`` so callers can't accidentally swap positional
    args (e.g. agent/stack_type) — postmortems live for a long time and a
    silently-mislabelled row is worse than a TypeError at call site.
    """
    if provenance is None or provenance not in _VALID_PROVENANCE:
        raise ValueError(
            f"provenance must be one of {sorted(_VALID_PROVENANCE)}; got {provenance!r}"
        )

    created_at = datetime.now(timezone.utc).isoformat()

    cursor = conn.execute(
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
            context_excerpt,
            fix_summary,
            provenance,
            confidence,
            created_at,
        ),
    )
    conn.commit()
    rowid = cursor.lastrowid
    if rowid is None:  # pragma: no cover — sqlite3 always returns an int after INSERT
        raise RuntimeError("INSERT did not return a lastrowid")
    return rowid


def query_active_postmortems(
    conn: sqlite3.Connection,
    stack_type: str,
    *,
    min_confidence: int = 70,
    limit: int = 15,
) -> list[sqlite3.Row]:
    """Return active (non-superseded) postmortems for this stack, newest first.

    Append-only persistence guarantee unchanged: this is a pure SELECT. Phase 2A
    callers must NEVER use the rows for write decisions — they're injected into
    the planner prompt only.

    Requires conn.row_factory == sqlite3.Row so callers can use string keys.
    """
    cursor = conn.execute(
        """
        SELECT id, execution_id, stack_type, agent, failure_signature,
               context_excerpt, fix_summary, confidence, created_at
        FROM postmortems
        WHERE stack_type = ?
          AND superseded_by IS NULL
          AND confidence >= ?
        ORDER BY confidence DESC, created_at DESC
        LIMIT ?
        """,
        (stack_type, min_confidence, limit),
    )
    return cursor.fetchall()


def query_postmortem_clusters(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    only_active: bool = True,
) -> list[sqlite3.Row]:
    """Return postmortems in a window, joined to executions for project_key.

    Used by the Phase 2C extractor: caller groups in Python by
    ``(stack_type, agent, failure_signature)`` rather than in SQL, which
    keeps the SQL trivial and lets the whack-a-mole filter run against
    grouped clusters before any UPSERT.

    Returned columns:
      ``id, stack_type, agent, failure_signature, context_excerpt,
      confidence, created_at, ticket_id, project_key``.

    ``project_key`` is derived as ``UPPER(SUBSTR(ticket_id, 1, INSTR(ticket_id, '-') - 1))``
    so ``ACME-847`` becomes ``"ACME"``. If ``ticket_id`` does not contain a
    dash, ``INSTR`` returns 0 and ``SUBSTR(s, 1, -1)`` yields ``""`` —
    intentional: such tickets get filtered out by the caller's
    ``distinct_projects >= 2`` guard, which is the correct behavior for
    non-Jira-style tickets in 2C.

    ``only_active=True`` (default) excludes superseded postmortems. Tests may
    pass ``only_active=False`` to read the full history.
    """
    cursor = conn.execute(
        """
        SELECT p.id, p.stack_type, p.agent, p.failure_signature,
               p.context_excerpt, p.confidence, p.created_at,
               e.ticket_id,
               UPPER(SUBSTR(e.ticket_id, 1, INSTR(e.ticket_id, '-') - 1)) AS project_key
          FROM postmortems p
          JOIN executions e ON e.id = p.execution_id
         WHERE (:only_active = 0 OR p.superseded_by IS NULL)
           AND p.created_at >= datetime('now', :window)
         ORDER BY p.stack_type, p.agent, p.failure_signature, p.created_at ASC
        """,
        {"only_active": 1 if only_active else 0, "window": f"-{int(days)} days"},
    )
    return cursor.fetchall()


def list_postmortems(
    conn: sqlite3.Connection,
    *,
    stack: Optional[str] = None,
    min_confidence: int = 0,
    limit: int = 20,
) -> list[sqlite3.Row]:
    """Return active postmortems for the CLI inspector.

    Like ``query_active_postmortems`` but with optional stack filter and a
    relaxed confidence floor — the CLI shows everything by default so
    maintainers can audit low-confidence rows too.
    """
    sql = (
        "SELECT id, execution_id, stack_type, agent, failure_signature, "
        "       context_excerpt, fix_summary, confidence, created_at "
        "FROM postmortems "
        "WHERE superseded_by IS NULL "
        "  AND confidence >= ? "
    )
    params: list = [min_confidence]
    if stack is not None:
        sql += "  AND stack_type = ? "
        params.append(stack)
    sql += "ORDER BY confidence DESC, created_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, tuple(params)).fetchall()
