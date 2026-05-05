"""Command Center write endpoints — retry an execution.

Historical note: this module previously owned three write endpoints
(``POST /executions``, ``POST /executions/{id}/cancel``,
``POST /executions/{id}/retry``). Track 2 introduced an attach-or-start
semantics for start and an asyncified cancel that both live in
``routes.executions`` under a dedicated ``write_router``. This module was
trimmed to the retry handler only — the previous start/cancel handlers were
shadowed by their Track 2 replacements anyway, and leaving them around
leaked onto the read-bucket router (no rate limit, no audit).

Retry is unchanged: it creates a *new* execution linked back to the original
via ``metadata_json.retry_of``. The router is deliberately un-authenticated
in isolation so tests can exercise it without the plan 05 token dance;
``create_app()`` wraps this router under the write-bucket deps (per-token
rate limit + audit log).
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from src.core.execution.models import ExecutionStatus
from src.core.execution.repository import ExecutionRepository
from src.core.execution.supervisor import Supervisor
from src.service.deps import get_repo, get_supervisor
from src.service.schemas import ExecutionOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/executions", tags=["commands"])


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


@router.post("/{execution_id}/retry", status_code=202, response_model=ExecutionOut)
def retry(
    execution_id: str,
    repo: Annotated[ExecutionRepository, Depends(get_repo)],
    supervisor: Annotated[Supervisor, Depends(get_supervisor)],
) -> ExecutionOut:
    original = repo.get(execution_id)
    if original is None:
        raise HTTPException(status_code=404, detail="execution not found")
    if original.status in (
        ExecutionStatus.QUEUED,
        ExecutionStatus.RUNNING,
        ExecutionStatus.CANCELLING,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"execution is {original.status.value}; cannot retry a live run",
        )

    options = (original.metadata or {}).get("options", {}) or {}
    new_execution = repo.create(
        ticket_id=original.ticket_id,
        project=original.project,
        kind=original.kind,
        options=options,
    )
    repo.mark_metadata(new_execution.id, retry_of=original.id)

    try:
        supervisor.spawn(new_execution.id)
    except Exception as exc:
        logger.exception("retry-spawn failed for execution %s", new_execution.id)
        repo.record_ended(
            new_execution.id,
            ExecutionStatus.FAILED,
            error=f"spawn_failed: {exc}",
        )
        raise HTTPException(status_code=500, detail=f"spawn failed: {exc}")

    refreshed = repo.get(new_execution.id) or new_execution
    return _to_execution_out(refreshed)
