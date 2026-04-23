"""Sentinel event models (pydantic v2).

Events are persisted to SQLite and replayed via a discriminated union on
``type``. The type strings are STABLE IDENTIFIERS — NEVER rename them,
as existing rows in the ``events`` table depend on them for rehydration.

Use ``AnyEventAdapter.validate_python(...)`` (or ``validate_json``) to
rehydrate an event from a stored row. Each subclass overrides ``type``
with a ``Literal[...]`` so pydantic can dispatch correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class SentinelEvent(BaseModel):
    """Common fields for every Sentinel event.

    Subclasses MUST override ``type`` with a ``Literal[...]`` — the
    discriminated union relies on that for rehydration from persisted rows.
    """

    execution_id: str
    ts: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )  # tz-aware; NEVER naive utcnow()
    agent: Optional[str] = None


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class ExecutionStarted(SentinelEvent):
    type: Literal["execution.started"] = "execution.started"
    kind: str
    ticket_id: str
    project: str


class ExecutionCompleted(SentinelEvent):
    type: Literal["execution.completed"] = "execution.completed"
    status: str
    cost_cents: int


class ExecutionFailed(SentinelEvent):
    type: Literal["execution.failed"] = "execution.failed"
    error: str


class ExecutionCancelling(SentinelEvent):
    type: Literal["execution.cancelling"] = "execution.cancelling"


class ExecutionCancelled(SentinelEvent):
    type: Literal["execution.cancelled"] = "execution.cancelled"


class PhaseChanged(SentinelEvent):
    type: Literal["phase.changed"] = "phase.changed"
    phase: str


# ---------------------------------------------------------------------------
# Agent / tool
# ---------------------------------------------------------------------------


class AgentStarted(SentinelEvent):
    type: Literal["agent.started"] = "agent.started"
    session_id: Optional[str] = None


class AgentFinished(SentinelEvent):
    type: Literal["agent.finished"] = "agent.finished"
    session_id: Optional[str] = None


class AgentMessageSent(SentinelEvent):
    type: Literal["agent.message_sent"] = "agent.message_sent"
    prompt_chars: int
    cwd: Optional[str] = None
    max_turns: Optional[int] = None


class AgentResponseReceived(SentinelEvent):
    type: Literal["agent.response_received"] = "agent.response_received"
    response_chars: int
    tool_uses_count: int
    elapsed_s: float


class ToolCalled(SentinelEvent):
    type: Literal["tool.called"] = "tool.called"
    tool: str
    args_summary: str


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class TestResultRecorded(SentinelEvent):
    type: Literal["test.result"] = "test.result"
    success: bool
    return_code: int


class FindingPosted(SentinelEvent):
    type: Literal["finding.posted"] = "finding.posted"
    severity: str
    summary: str


class CostAccrued(SentinelEvent):
    type: Literal["cost.accrued"] = "cost.accrued"
    tokens_in: int
    tokens_out: int
    cents: int


# ---------------------------------------------------------------------------
# Interactive / revision
# ---------------------------------------------------------------------------


class DebriefTurn(SentinelEvent):
    type: Literal["debrief.turn"] = "debrief.turn"
    turn_index: int
    prompt_chars: int
    response_chars: int


class RevisionRequested(SentinelEvent):
    type: Literal["revision.requested"] = "revision.requested"
    revise_of_execution_id: str
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Error-class (observational — does NOT transition ExecutionStatus)
# ---------------------------------------------------------------------------


class RateLimited(SentinelEvent):
    type: Literal["rate_limited"] = "rate_limited"
    retry_after_s: Optional[float] = None


# ---------------------------------------------------------------------------
# Terminal event types (used to decide when an execution row is "done")
# ---------------------------------------------------------------------------


TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "execution.completed",
        "execution.failed",
        "execution.cancelled",
    }
)


# ---------------------------------------------------------------------------
# Discriminated union + TypeAdapter for rehydrating from DB rows
# ---------------------------------------------------------------------------


AnyEvent = Annotated[
    Union[
        ExecutionStarted,
        ExecutionCompleted,
        ExecutionFailed,
        ExecutionCancelling,
        ExecutionCancelled,
        PhaseChanged,
        AgentStarted,
        AgentFinished,
        AgentMessageSent,
        AgentResponseReceived,
        ToolCalled,
        TestResultRecorded,
        FindingPosted,
        CostAccrued,
        DebriefTurn,
        RevisionRequested,
        RateLimited,
    ],
    Field(discriminator="type"),
]

AnyEventAdapter: TypeAdapter[AnyEvent] = TypeAdapter(AnyEvent)


__all__ = [
    # Base
    "SentinelEvent",
    # Lifecycle
    "ExecutionStarted",
    "ExecutionCompleted",
    "ExecutionFailed",
    "ExecutionCancelling",
    "ExecutionCancelled",
    "PhaseChanged",
    # Agent / tool
    "AgentStarted",
    "AgentFinished",
    "AgentMessageSent",
    "AgentResponseReceived",
    "ToolCalled",
    # Results
    "TestResultRecorded",
    "FindingPosted",
    "CostAccrued",
    # Interactive / revision
    "DebriefTurn",
    "RevisionRequested",
    # Error-class
    "RateLimited",
    # Union + constants
    "AnyEvent",
    "AnyEventAdapter",
    "TERMINAL_EVENT_TYPES",
]
