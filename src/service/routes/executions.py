"""Read-only HTTP endpoints over executions, events, and agent results."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.repository import ExecutionRepository
from src.service.deps import get_repo
from src.service.schemas import (
    AgentResultOut,
    EventOut,
    ExecutionOut,
    ListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/executions", tags=["executions"])

# Server-side limit ceilings — enforced regardless of client input.
EXECUTIONS_LIMIT_DEFAULT = 50
EXECUTIONS_LIMIT_MAX = 200
EVENTS_LIMIT_DEFAULT = 200
EVENTS_LIMIT_MAX = 1000


def _to_execution_out(execution) -> ExecutionOut:  # type: ignore[no-untyped-def]
    return ExecutionOut(
        id=execution.id,
        ticket_id=execution.ticket_id,
        project=execution.project,
        kind=execution.kind,
        status=execution.status,
        phase=execution.phase,
        started_at=execution.started_at,
        ended_at=execution.ended_at,
        cost_cents=execution.cost_cents,
        error=execution.error,
        metadata=execution.metadata,
    )


@router.get("", response_model=ListResponse[ExecutionOut])
def list_executions(
    project: Optional[str] = Query(default=None),
    ticket_id: Optional[str] = Query(default=None),
    status: Optional[ExecutionStatus] = Query(default=None),
    kind: Optional[ExecutionKind] = Query(default=None),
    limit: int = Query(default=EXECUTIONS_LIMIT_DEFAULT, ge=1),
    before: Optional[datetime] = Query(default=None),
    repo: ExecutionRepository = Depends(get_repo),
) -> ListResponse[ExecutionOut]:
    """List executions, most-recent first.

    ``limit`` is clamped to :data:`EXECUTIONS_LIMIT_MAX` server-side.
    ``before`` accepts ISO-8601 and filters to executions strictly older than
    the given timestamp. The returned ``next_cursor`` is the ISO ``started_at``
    of the oldest row when the page is full, otherwise ``None``.
    """
    clamped = min(limit, EXECUTIONS_LIMIT_MAX)
    before_iso = before.isoformat() if before is not None else None

    rows = repo.list(
        project=project,
        ticket_id=ticket_id,
        status=status,
        kind=kind,
        before=before_iso,
        limit=clamped,
    )
    items = [_to_execution_out(r) for r in rows]
    next_cursor: Optional[str] = None
    if len(items) == clamped and items:
        next_cursor = items[-1].started_at.isoformat()
    return ListResponse[ExecutionOut](items=items, next_cursor=next_cursor)


@router.get("/{execution_id}", response_model=ExecutionOut)
def get_execution(
    execution_id: str,
    repo: ExecutionRepository = Depends(get_repo),
) -> ExecutionOut:
    execution = repo.get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return _to_execution_out(execution)


@router.get("/{execution_id}/events", response_model=ListResponse[EventOut])
def list_events(
    execution_id: str,
    since_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=EVENTS_LIMIT_DEFAULT, ge=1),
    repo: ExecutionRepository = Depends(get_repo),
) -> ListResponse[EventOut]:
    if repo.get(execution_id) is None:
        raise HTTPException(status_code=404, detail="execution not found")

    clamped = min(limit, EVENTS_LIMIT_MAX)
    rows: List[EventOut] = [
        EventOut(
            seq=r["seq"],
            ts=datetime.fromisoformat(r["ts"]),
            agent=r["agent"],
            type=r["type"],
            payload=r["payload"],
        )
        for r in repo.iter_events(execution_id, since_seq=since_seq, limit=clamped)
    ]
    next_cursor: Optional[str] = None
    if len(rows) == clamped and rows:
        next_cursor = str(rows[-1].seq)
    return ListResponse[EventOut](items=rows, next_cursor=next_cursor)


@router.get(
    "/{execution_id}/agent-results", response_model=ListResponse[AgentResultOut]
)
def list_agent_results(
    execution_id: str,
    repo: ExecutionRepository = Depends(get_repo),
) -> ListResponse[AgentResultOut]:
    if repo.get(execution_id) is None:
        raise HTTPException(status_code=404, detail="execution not found")

    raw = repo.list_agent_results(execution_id)
    items = [
        AgentResultOut(
            agent=r["agent"],
            result=r["result"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )
        for r in raw
    ]
    return ListResponse[AgentResultOut](items=items, next_cursor=None)
