"""Pydantic response models for the Command Center HTTP read API.

These are explicit API shapes, deliberately decoupled from the internal
repository rows so we don't leak SQL column names or change behaviour when
the schema evolves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from src.core.execution.models import ExecutionKind, ExecutionStatus

T = TypeVar("T")


class ExecutionOut(BaseModel):
    """Outbound shape of an execution row."""

    model_config = ConfigDict(use_enum_values=True)

    id: str
    ticket_id: str
    project: str
    kind: ExecutionKind
    status: ExecutionStatus
    phase: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    cost_cents: int = 0
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EventOut(BaseModel):
    """Outbound shape of an event row — payload is a dict, not raw JSON."""

    seq: int
    ts: datetime
    agent: Optional[str] = None
    type: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class AgentResultOut(BaseModel):
    """Outbound shape of an agent_results row."""

    agent: str
    result: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ListResponse(BaseModel, Generic[T]):
    """Envelope for list endpoints.

    ``next_cursor`` is opaque to clients — currently the ISO timestamp of the
    oldest returned item (usable as the ``before`` query param), or ``None``
    when there are no more pages.
    """

    items: List[T]
    next_cursor: Optional[str] = None


# ---------------------------------------------------------------------------
# Track 2 — attach-or-start / cancel request/response models
# ---------------------------------------------------------------------------


class ExecutionCreate(BaseModel):
    """Request body for ``POST /executions`` (attach-or-start).

    ``extra="forbid"`` because the body flows into ``metadata_json`` and
    eventually into agent prompts; a free-form dict is a prompt-injection
    vector. ``options`` stays open-ended (the Orchestrator ignores unknown
    keys) but the top-level envelope is locked down.
    """

    model_config = ConfigDict(extra="forbid")

    project: str
    ticket_id: str
    kind: ExecutionKind
    options: Dict[str, Any] = Field(default_factory=dict)


class ExecutionStartResponse(BaseModel):
    """Response body for ``POST /executions``.

    ``attached=True`` means the response echoes an already-running row that
    matched the ``(project, ticket_id, kind)`` triple; ``banner`` is a human-
    readable string the TUI renders verbatim. Populated only on attach.
    """

    execution: ExecutionOut
    attached: bool = False
    banner: Optional[str] = None


class ExecutionCancelResponse(BaseModel):
    """Response body for ``POST /executions/{id}/cancel``.

    ``signalled=True`` means ``supervisor.cancel`` was invoked (or, for a
    ``QUEUED`` row that hadn't yet spawned, the row was directly marked
    ``CANCELLED``). Idempotent — a second cancel on a ``CANCELLING`` row
    returns ``signalled=True`` too.
    """

    execution: ExecutionOut
    signalled: bool = True
