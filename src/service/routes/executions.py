"""HTTP endpoints over executions, events, and agent results.

Read handlers (``GET``) are the bulk of this module. Track 2 layered two
write handlers (``POST /executions`` attach-or-start and
``POST /executions/{id}/cancel``) onto the same router so the TUI can drive
the full interactive lifecycle from a single auth-bucket surface. Auth is
applied router-level by ``create_app()`` — handlers here do not re-check.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from src.core.events.bus import EventBus
from src.core.events.types import ExecutionCancelled
from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.orchestrator import Orchestrator
from src.core.execution.repository import ExecutionRepository
from src.core.execution.supervisor import Supervisor
from src.service.deps import get_db_conn, get_repo, get_supervisor
from src.service.schemas import (
    AgentResultOut,
    EventOut,
    ExecutionCancelResponse,
    ExecutionCreate,
    ExecutionOut,
    ExecutionStartResponse,
    ListResponse,
)

logger = logging.getLogger(__name__)

# Two routers in one module so ``create_app()`` can mount each under the
# correct auth bucket:
#
#   * ``router``       → read bucket (``require_token`` only).
#   * ``write_router`` → write bucket (``require_token_and_write_slot`` +
#     audit). The Track 2 attach-or-start and cancel handlers live here so
#     they inherit the per-token rate limiter and audit trail. Mounting them
#     on the read router would silently bypass both — the shadowing
#     regression this split fixes.
router = APIRouter(prefix="/executions", tags=["executions"])
write_router = APIRouter(prefix="/executions", tags=["executions"])

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


# ---------------------------------------------------------------------------
# Track 2 — attach-or-start + cancel
# ---------------------------------------------------------------------------

# Cap JSON-encoded size of ``options`` to keep malicious / accidental payloads
# from bloating ``metadata_json``. Plan 02 gotcha: this check runs BEFORE the
# DB is touched so a hostile client can't fill the row table with garbage.
OPTIONS_MAX_BYTES = 8 * 1024


def _format_relative(started_at: datetime) -> str:
    """Human-readable relative-time — ``"14s ago"`` / ``"3m ago"`` / ``"2h ago"``.

    Small and deliberate: the TUI renders the banner verbatim, so we want a
    stable short format, not locale-aware i18n.
    """
    now = datetime.now(timezone.utc)
    # Normalise naive timestamps (shouldn't happen — repo stores tz-aware —
    # but guard against a legacy row to avoid a 500).
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    delta = now - started_at
    secs = int(delta.total_seconds())
    if secs < 0:
        secs = 0
    if secs < 60:
        return f"{secs}s ago"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _attach_banner(execution) -> str:  # type: ignore[no-untyped-def]
    short_id = execution.id[:8]
    rel = _format_relative(execution.started_at)
    return f"Attached to run {short_id} started {rel}"


@write_router.post(
    "",
    response_model=ExecutionStartResponse,
    responses={
        200: {"description": "Attached to an already-running execution."},
        201: {"description": "Fresh execution started."},
        413: {"description": "Options payload exceeds 8 KB cap."},
        503: {"description": "Supervisor could not spawn the worker."},
    },
)
def create_or_attach_execution(
    body: ExecutionCreate,
    response: Response,
    repo: ExecutionRepository = Depends(get_repo),
    supervisor: Supervisor = Depends(get_supervisor),
    conn=Depends(get_db_conn),
) -> ExecutionStartResponse:
    """Attach to an active run for ``(project, ticket_id, kind)`` if one exists;
    otherwise spawn a fresh execution.

    Status codes:
        * ``200`` — attached to an existing ``QUEUED``/``RUNNING`` row.
        * ``201`` — fresh row created and worker spawned.
        * ``413`` — ``options`` JSON encoding exceeds :data:`OPTIONS_MAX_BYTES`.
        * ``503`` — ``supervisor.spawn`` raised (e.g., Docker socket unreachable).

    Concurrency note: we intentionally use a two-step check (``find_active``
    then ``orchestrator.begin``) and accept the narrow race where two
    simultaneous POSTs can both miss the attach lookup and both create rows.
    See Track 2 plan "Stale running row" gotcha — treated as a known v1 gap.
    """
    # 413 early — before touching the DB. The budget is generous; anything
    # larger is almost certainly accidental or malicious.
    try:
        encoded_len = len(json.dumps(body.options))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail=f"options is not JSON-encodable: {exc}"
        )
    if encoded_len > OPTIONS_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"options payload is {encoded_len} bytes; "
                f"cap is {OPTIONS_MAX_BYTES}."
            ),
        )

    # Attach path — return the active run, do not touch the supervisor.
    existing = repo.find_active(body.project, body.ticket_id, body.kind)
    if existing is not None:
        response.status_code = 200
        return ExecutionStartResponse(
            execution=_to_execution_out(existing),
            attached=True,
            banner=_attach_banner(existing),
        )

    # Fresh-start path. Build a request-scoped Orchestrator bound to the
    # same connection the repo is using so the ``ExecutionStarted`` event
    # lands in the same WAL snapshot as the inserted row.
    bus = EventBus(conn)
    orchestrator = Orchestrator(repo=repo, bus=bus)
    execution = orchestrator.begin(
        ticket_id=body.ticket_id,
        project=body.project,
        kind=body.kind,
        options=body.options or None,
    )

    try:
        supervisor.spawn(execution.id)
    except Exception as exc:
        logger.exception(
            "supervisor.spawn failed for fresh execution %s", execution.id
        )
        # Mark the freshly-inserted row as failed so the reaper / post-mortem
        # sees a terminal row and the TUI's next attach attempt doesn't stick
        # to this ghost. 503 signals "infra not available" to the client so it
        # can retry with backoff rather than surfacing as a bug.
        try:
            repo.record_ended(
                execution.id,
                ExecutionStatus.FAILED,
                error=f"spawn_failed: {exc}",
            )
        except Exception:
            logger.exception(
                "failed to mark execution %s FAILED after spawn error",
                execution.id,
            )
        raise HTTPException(
            status_code=503,
            detail=f"supervisor spawn failed: {exc}",
        )

    refreshed = repo.get(execution.id) or execution
    response.status_code = 201
    return ExecutionStartResponse(
        execution=_to_execution_out(refreshed),
        attached=False,
        banner=None,
    )


_TERMINAL_STATUSES = (
    ExecutionStatus.SUCCEEDED,
    ExecutionStatus.FAILED,
    ExecutionStatus.CANCELLED,
)


@write_router.post(
    "/{execution_id}/cancel",
    status_code=202,
    response_model=ExecutionCancelResponse,
    responses={
        404: {"description": "Unknown execution id."},
        409: {"description": "Execution is already in a terminal state."},
    },
)
async def cancel_execution(
    execution_id: str,
    repo: ExecutionRepository = Depends(get_repo),
    supervisor: Supervisor = Depends(get_supervisor),
    conn=Depends(get_db_conn),
) -> ExecutionCancelResponse:
    """Signal cancellation of an in-flight execution.

    Status codes:
        * ``202`` — signal delivered (or row marked ``CANCELLED`` directly for
          a ``QUEUED`` row that never spawned a worker). Idempotent: a second
          cancel on a ``CANCELLING`` row returns 202 as well.
        * ``404`` — unknown id.
        * ``409`` — already terminal; current status echoed in ``detail``.
    """
    execution = repo.get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="execution not found")
    if execution.status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"execution is {execution.status.value}; cannot cancel",
        )

    was_queued = execution.status == ExecutionStatus.QUEUED

    # supervisor.cancel blocks up to ~35s on the SIGTERM/SIGINT/SIGKILL
    # escalation; offload to the default threadpool so we don't pin the ASGI
    # event loop. For an adopted PID the supervisor flips the row to
    # CANCELLING itself. For an unknown PID (QUEUED before spawn) it is a
    # silent no-op — we handle that row transition below.
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, supervisor.cancel, execution_id)

    if was_queued:
        # Queued rows never spawned a worker; the supervisor's cancel() is a
        # no-op on unknown PIDs, so the row would remain QUEUED forever. Mark
        # it CANCELLED directly and publish the terminal event so downstream
        # tails (plan 03 stream, TUI) see the transition.
        current = repo.get(execution_id)
        if current is not None and current.status == ExecutionStatus.QUEUED:
            repo.record_ended(execution_id, ExecutionStatus.CANCELLED)
            try:
                EventBus(conn).publish(
                    ExecutionCancelled(execution_id=execution_id)
                )
            except Exception:
                logger.exception(
                    "cancel: publish cancelled for queued %s failed",
                    execution_id,
                )

    refreshed = repo.get(execution_id) or execution
    return ExecutionCancelResponse(
        execution=_to_execution_out(refreshed),
        signalled=True,
    )
