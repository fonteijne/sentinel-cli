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
