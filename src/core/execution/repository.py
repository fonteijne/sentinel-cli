"""SQLite-backed repository for executions, events, and agent results."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, TypedDict

from src.core.execution.models import Execution, ExecutionKind, ExecutionStatus

logger = logging.getLogger(__name__)


class WorkerRow(TypedDict):
    """Shape returned by :meth:`ExecutionRepository.get_worker`."""

    execution_id: str
    pid: int
    started_at: datetime
    last_heartbeat_at: datetime
    compose_projects: List[str]


class EventRow(TypedDict):
    """Shape returned by :meth:`ExecutionRepository.iter_events`.

    ``payload`` is the already-parsed dict; consumers never see raw JSON.
    """

    seq: int
    ts: str
    agent: Optional[str]
    type: str
    payload: Dict[str, Any]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _row_to_execution(row: sqlite3.Row) -> Execution:
    metadata_raw = row["metadata_json"] or "{}"
    try:
        metadata = json.loads(metadata_raw)
    except json.JSONDecodeError:
        logger.warning("metadata_json for execution %s was invalid; defaulting to {}", row["id"])
        metadata = {}
    started = _parse_dt(row["started_at"])
    assert started is not None  # NOT NULL column
    return Execution(
        id=row["id"],
        ticket_id=row["ticket_id"],
        project=row["project"],
        kind=ExecutionKind(row["kind"]),
        status=ExecutionStatus(row["status"]),
        phase=row["phase"],
        started_at=started,
        ended_at=_parse_dt(row["ended_at"]),
        cost_cents=row["cost_cents"],
        error=row["error"],
        idempotency_token_prefix=row["idempotency_token_prefix"],
        idempotency_key=row["idempotency_key"],
        metadata=metadata,
    )


class ExecutionRepository:
    """CRUD + lifecycle transitions over ``executions``, ``events``, ``agent_results``.

    The caller owns the connection. Never share a connection across threads.
    Writers wrap multi-statement work in ``BEGIN IMMEDIATE``/``COMMIT``; readers
    rely on SQLite's WAL snapshot isolation and skip explicit transactions.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ---------------------------------------------------------------- create

    def create(
        self,
        ticket_id: str,
        project: str,
        kind: ExecutionKind,
        *,
        options: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        idempotency_token_prefix: Optional[str] = None,
    ) -> Execution:
        """Insert a new ``queued``-state execution and return the model.

        The worker transitions the row to ``running`` on startup (plan 04).
        """
        execution_id = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc)
        metadata: Dict[str, Any] = {}
        if options:
            metadata["options"] = options

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "INSERT INTO executions("
                "id, ticket_id, project, kind, status, started_at, "
                "idempotency_token_prefix, idempotency_key, metadata_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    execution_id,
                    ticket_id,
                    project,
                    kind.value,
                    ExecutionStatus.QUEUED.value,
                    started_at.isoformat(),
                    idempotency_token_prefix,
                    idempotency_key,
                    json.dumps(metadata),
                ),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

        return Execution(
            id=execution_id,
            ticket_id=ticket_id,
            project=project,
            kind=kind,
            status=ExecutionStatus.QUEUED,
            started_at=started_at,
            idempotency_token_prefix=idempotency_token_prefix,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )

    # ------------------------------------------------------------------ reads

    def get(self, execution_id: str) -> Optional[Execution]:
        row = self._conn.execute(
            "SELECT * FROM executions WHERE id = ?", (execution_id,)
        ).fetchone()
        return _row_to_execution(row) if row else None

    def find_by_idempotency(
        self, token_prefix: str, key: str
    ) -> Optional[Execution]:
        """Look up by ``(idempotency_token_prefix, idempotency_key)``.

        Returns the existing row regardless of terminal status — a POST with
        a previously-used key does NOT re-run a failed execution; callers
        use an explicit retry endpoint for that.
        """
        row = self._conn.execute(
            "SELECT * FROM executions "
            "WHERE idempotency_token_prefix = ? AND idempotency_key = ?",
            (token_prefix, key),
        ).fetchone()
        return _row_to_execution(row) if row else None

    def list(
        self,
        *,
        project: Optional[str] = None,
        ticket_id: Optional[str] = None,
        status: Optional[ExecutionStatus] = None,
        kind: Optional[ExecutionKind] = None,
        before: Optional[str] = None,
        limit: int = 50,
    ) -> List[Execution]:
        clauses: List[str] = []
        params: List[Any] = []
        if project is not None:
            clauses.append("project = ?")
            params.append(project)
        if ticket_id is not None:
            clauses.append("ticket_id = ?")
            params.append(ticket_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        if before is not None:
            clauses.append("started_at < ?")
            params.append(before)

        sql = "SELECT * FROM executions"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_execution(r) for r in rows]

    # --------------------------------------------------------- state updates

    def set_status(
        self,
        execution_id: str,
        status: ExecutionStatus,
        error: Optional[str] = None,
    ) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "UPDATE executions SET status = ?, error = COALESCE(?, error) WHERE id = ?",
                (status.value, error, execution_id),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def set_phase(self, execution_id: str, phase: Optional[str]) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "UPDATE executions SET phase = ? WHERE id = ?",
                (phase, execution_id),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def add_cost(self, execution_id: str, cents: int) -> None:
        """Atomic increment of ``cost_cents`` on the execution row."""
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "UPDATE executions SET cost_cents = cost_cents + ? WHERE id = ?",
                (int(cents), execution_id),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def record_ended(
        self,
        execution_id: str,
        status: ExecutionStatus,
        error: Optional[str] = None,
    ) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "UPDATE executions "
                "SET status = ?, error = COALESCE(?, error), ended_at = ? "
                "WHERE id = ?",
                (status.value, error, _now_iso(), execution_id),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def mark_metadata(self, execution_id: str, **kv: Any) -> None:
        """Shallow-merge ``kv`` into ``metadata_json``.

        Used for keys like ``retry_of``, ``compose_projects``,
        ``post_mortem_complete``. Non-JSON-encodable values raise at the call
        site (json.dumps will complain).
        """
        current = self._conn.execute(
            "SELECT metadata_json FROM executions WHERE id = ?", (execution_id,)
        ).fetchone()
        if current is None:
            raise LookupError(f"execution {execution_id} not found")
        metadata = json.loads(current[0] or "{}")
        metadata.update(kv)

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "UPDATE executions SET metadata_json = ? WHERE id = ?",
                (json.dumps(metadata), execution_id),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    # ------------------------------------------------------ agent results

    def record_agent_result(
        self,
        execution_id: str,
        agent: str,
        result: Dict[str, Any],
    ) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "INSERT INTO agent_results(execution_id, agent, result_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (execution_id, agent, json.dumps(result, default=str), _now_iso()),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def list_agent_results(self, execution_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT agent, result_json, created_at FROM agent_results "
            "WHERE execution_id = ? ORDER BY id",
            (execution_id,),
        ).fetchall()
        results: List[Dict[str, Any]] = []
        for r in rows:
            try:
                result = json.loads(r["result_json"])
            except json.JSONDecodeError:
                logger.warning("agent_results row had invalid JSON; skipping")
                continue
            results.append(
                {"agent": r["agent"], "result": result, "created_at": r["created_at"]}
            )
        return results

    # ------------------------------------------------------------ events

    def iter_events(
        self,
        execution_id: str,
        since_seq: int = 0,
        limit: int = 500,
    ) -> Iterator[EventRow]:
        rows = self._conn.execute(
            "SELECT seq, ts, agent, type, payload_json FROM events "
            "WHERE execution_id = ? AND seq > ? ORDER BY seq LIMIT ?",
            (execution_id, since_seq, limit),
        ).fetchall()
        for r in rows:
            try:
                payload = json.loads(r["payload_json"])
            except json.JSONDecodeError:
                logger.warning(
                    "events row with invalid JSON (execution=%s seq=%s); skipping",
                    execution_id,
                    r["seq"],
                )
                continue
            yield EventRow(
                seq=r["seq"],
                ts=r["ts"],
                agent=r["agent"],
                type=r["type"],
                payload=payload,
            )

    def latest_event_seq(self, execution_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM events WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------- workers

    def get_worker(self, execution_id: str) -> Optional[WorkerRow]:
        row = self._conn.execute(
            "SELECT execution_id, pid, started_at, last_heartbeat_at, compose_projects "
            "FROM workers WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            projects = json.loads(row["compose_projects"] or "[]")
        except json.JSONDecodeError:
            projects = []
        return WorkerRow(
            execution_id=row["execution_id"],
            pid=int(row["pid"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            last_heartbeat_at=datetime.fromisoformat(row["last_heartbeat_at"]),
            compose_projects=projects,
        )

    def set_worker_heartbeat(self, execution_id: str) -> None:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "UPDATE workers SET last_heartbeat_at = ? WHERE execution_id = ?",
                (_now_iso(), execution_id),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def list_post_mortem_incomplete(self) -> List[Execution]:
        """Return terminal rows missing ``metadata_json.post_mortem_complete = 1``.

        Used by startup reconciliation to catch the case where the supervisor
        itself was killed mid-cleanup — such rows are terminal (``failed`` /
        ``cancelled``) but never ran compose-down, so we re-sweep them.
        """
        rows = self._conn.execute(
            "SELECT * FROM executions "
            "WHERE status IN ('failed','cancelled') "
            "AND (json_extract(metadata_json,'$.post_mortem_complete') IS NOT 1)"
        ).fetchall()
        return [_row_to_execution(r) for r in rows]

    def register_compose_project(
        self, execution_id: str, project_name: str
    ) -> None:
        """Record ``project_name`` in BOTH ``workers.compose_projects`` AND
        ``executions.metadata_json.compose_projects[]`` in a single transaction.

        ``workers`` is source of truth during the run; ``metadata_json`` is
        the archival copy used by post-mortem/reconciliation after the worker
        row is gone.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "UPDATE workers SET compose_projects = "
                "json_insert(compose_projects, '$[#]', ?) "
                "WHERE execution_id = ?",
                (project_name, execution_id),
            )
            # Initialize metadata_json.compose_projects to [] if absent
            # (json_insert is a no-op when the path exists).
            self._conn.execute(
                "UPDATE executions SET metadata_json = "
                "json_insert(metadata_json, '$.compose_projects', json('[]')) "
                "WHERE id = ?",
                (execution_id,),
            )
            self._conn.execute(
                "UPDATE executions SET metadata_json = "
                "json_insert(metadata_json, '$.compose_projects[#]', ?) "
                "WHERE id = ?",
                (project_name, execution_id),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
