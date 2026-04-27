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
from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.options import (
    DebriefOptions,
    ExecuteOptions,
    PlanOptions,
    to_metadata_options,
)
from src.core.execution.repository import ExecutionRepository
from src.core.execution.supervisor import Supervisor
from src.service.deps import get_repo, get_supervisor
from src.service.schemas import ExecutionOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/executions", tags=["commands"])


# ---------------------------------------------------------------- schemas


# Ticket-ID pattern. Prefix: uppercase letter, then letters/digits/underscores.
# Suffix after the dash: numeric issue ID. Underscores in the prefix are
# intentional — Jira instances like the ones this deploys against use keys
# such as ``KAN_KAN-1``, ``COE_JIRATESTAI-2352``, ``DHLPPXC_DHLEX-99``.
# Defined once so the Start body and the Options follow-up stay in lockstep.
_TICKET_ID_PATTERN = r"^[A-Z][A-Z0-9_]+-\d+$"

# Docker Compose project-name pattern: must start with a lowercase letter or
# digit, then allow letters/digits/underscores/hyphens up to 64 chars total.
_PROJECT_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"


def _empty_string_to_none(value):  # type: ignore[no-untyped-def]
    """Swagger UI fills optional string fields with ``""`` instead of omitting
    them, which then fails pattern validation for no useful reason. Coerce
    ``""`` → ``None`` *before* the field-level pattern runs so the field
    behaves identically whether the client sends nothing, ``null``, or an
    empty string. Applied as a ``mode="before"`` validator on every optional
    patterned string in this module.
    """
    if value == "":
        return None
    return value


# Back-compat shim: previous API tests POST `{"options": {"revise": true}}` etc.
# We now route the dict into the kind-specific WorkflowOptions class so a
# misnamed flag (e.g. `--no-env` against a plan run) returns 422 instead of
# being silently dropped. ExecutionOptions is kept as an alias of
# ExecuteOptions for one release so existing API consumers don't break.
ExecutionOptions = ExecuteOptions


_OPTIONS_BY_KIND = {
    ExecutionKind.PLAN: PlanOptions,
    ExecutionKind.EXECUTE: ExecuteOptions,
    ExecutionKind.DEBRIEF: DebriefOptions,
}


class StartExecutionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(pattern=_TICKET_ID_PATTERN)
    # Docker Compose project name. Optional — if omitted, null, or empty we
    # derive it from the ticket_id prefix (lowercased), matching the CLI's
    # ``-p`` default in src/cli.py (``project or ticket_id.split("-")[0]``).
    project: Optional[str] = Field(default=None, pattern=_PROJECT_PATTERN)
    kind: ExecutionKind
    # ``options`` is parsed lazily against the kind-specific WorkflowOptions
    # subclass in the model_validator so we get clean 422 errors with the
    # field that was unsupported, rather than a generic "extra fields"
    # message against a union type.
    options: Dict[str, Any] = Field(default_factory=dict)

    _project_empty_to_none = field_validator("project", mode="before")(
        _empty_string_to_none
    )

    @model_validator(mode="after")
    def _derive_project_and_validate_options(self) -> "StartExecutionBody":
        if self.project is None:
            # ticket_id is already pattern-validated at this point, so the
            # prefix is guaranteed [A-Z][A-Z0-9_]+ which lowercases into a
            # valid Compose project name. No re-validation needed.
            self.project = self.ticket_id.split("-", 1)[0].lower()

        # Coerce ``options`` (a free-form dict at this point) into the
        # kind-specific :class:`WorkflowOptions`. ``extra="forbid"`` means
        # an unsupported flag fails with 422 — we never silently strip it.
        cls = _OPTIONS_BY_KIND.get(self.kind)
        if cls is None:  # defensive — ExecutionKind enum is exhaustive
            raise ValueError(f"unsupported kind: {self.kind!r}")
        try:
            self.options = cls.model_validate(self.options or {}).model_dump(
                mode="json"
            )
        except Exception as exc:
            # Re-raise as ValueError so pydantic translates it into a 422 with
            # the original location info from the inner model.
            raise ValueError(f"invalid options for kind={self.kind.value}: {exc}")
        return self

    def workflow_options(self):
        """Return the parsed :class:`WorkflowOptions` instance.

        ``options`` is a plain dict in this model so the OpenAPI schema
        keeps the union explicit per ``kind``; this helper turns it back
        into the typed model right before persistence.
        """
        cls = _OPTIONS_BY_KIND[self.kind]
        return cls.model_validate(self.options or {})


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

    options_payload = to_metadata_options(body.workflow_options())
    execution = repo.create(
        ticket_id=body.ticket_id,
        project=body.project,
        kind=body.kind,
        options=options_payload,
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
