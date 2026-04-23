"""Command Center write endpoints — start, cancel, retry an execution.

The router is deliberately un-authenticated in isolation so tests can exercise
it without the plan 05 token dance; plan 05's ``create_app()`` wraps the
router in a bearer-auth dependency (and per-token rate limits).

Start is async: the endpoint returns 202 immediately after queueing; real
progress is observed via plan 02's GET events or plan 03's WebSocket stream.
Cancel signals SIGTERM to the worker; the escalation to SIGINT/SIGKILL is
kicked off in a threadpool so we don't block the ASGI event loop for up to
30s. Retry creates a *new* execution linked back to the original via
``metadata_json.retry_of``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from pydantic import BaseModel, ConfigDict, Field

from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.repository import ExecutionRepository
from src.core.execution.supervisor import Supervisor
from src.service.deps import get_repo, get_supervisor
from src.service.schemas import ExecutionOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/executions", tags=["commands"])


# ---------------------------------------------------------------- schemas


class ExecutionOptions(BaseModel):
    """Explicit enumerated options — never a free-form dict.

    ``extra="forbid"`` is load-bearing: the body flows into ``metadata_json``
    and is later read by the worker → orchestrator → agent prompts → ``Bash``
    tool calls. Any free-form dict is a prompt-injection vector.
    """

    model_config = ConfigDict(extra="forbid")

    revise: bool = False
    max_turns: Optional[int] = Field(default=None, ge=1, le=200)
    follow_up_ticket: Optional[str] = Field(
        default=None, pattern=r"^[A-Z][A-Z0-9]+-\d+$"
    )


class StartExecutionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(pattern=r"^[A-Z][A-Z0-9]+-\d+$")
    # docker compose project name rules: start with [a-z0-9], then [a-z0-9_-]
    project: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    kind: ExecutionKind
    options: ExecutionOptions = Field(default_factory=ExecutionOptions)


# ---------------------------------------------------------------- helpers


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


def _token_prefix(request: Request) -> Optional[str]:
    """Read the prefix stashed by plan 05's auth dep. ``None`` in isolated tests."""
    return getattr(request.state, "token_prefix", None)


# ---------------------------------------------------------------- endpoints


@router.post("", status_code=202, response_model=ExecutionOut)
def start(
    body: StartExecutionBody,
    request: Request,
    repo: Annotated[ExecutionRepository, Depends(get_repo)],
    supervisor: Annotated[Supervisor, Depends(get_supervisor)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
) -> ExecutionOut:
    token_prefix = _token_prefix(request)
    if idempotency_key and token_prefix is not None:
        existing = repo.find_by_idempotency(token_prefix, idempotency_key)
        if existing is not None:
            return _to_execution_out(existing)

    execution = repo.create(
        ticket_id=body.ticket_id,
        project=body.project,
        kind=body.kind,
        options=body.options.model_dump(),
        idempotency_token_prefix=token_prefix,
        idempotency_key=idempotency_key,
    )
    try:
        supervisor.spawn(execution.id)
    except Exception as exc:
        logger.exception("spawn failed for execution %s", execution.id)
        repo.record_ended(
            execution.id,
            ExecutionStatus.FAILED,
            error=f"spawn_failed: {exc}",
        )
        raise HTTPException(status_code=500, detail=f"spawn failed: {exc}")

    refreshed = repo.get(execution.id) or execution
    return _to_execution_out(refreshed)


@router.post("/{execution_id}/cancel", status_code=202, response_model=ExecutionOut)
async def cancel(
    execution_id: str,
    repo: Annotated[ExecutionRepository, Depends(get_repo)],
    supervisor: Annotated[Supervisor, Depends(get_supervisor)],
) -> ExecutionOut:
    execution = repo.get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="execution not found")
    if execution.status not in (
        ExecutionStatus.QUEUED,
        ExecutionStatus.RUNNING,
        ExecutionStatus.CANCELLING,
    ):
        raise HTTPException(
            status_code=409,
            detail=f"execution is {execution.status.value}; cannot cancel",
        )

    loop = asyncio.get_running_loop()
    # Fire-and-forget — the escalation may take up to 30s; respond 202 now.
    loop.run_in_executor(None, supervisor.cancel, execution_id)

    refreshed = repo.get(execution_id) or execution
    return _to_execution_out(refreshed)


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
