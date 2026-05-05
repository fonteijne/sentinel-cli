"""Pydantic models for Command Center executions.

Mirrors the `executions` table in the Command Center SQLite schema.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class ExecutionStatus(str, Enum):
    """Lifecycle states for an execution row.

    The terminal states are SUCCEEDED / FAILED / CANCELLED. CANCELLING is a
    transitional state used while a running execution is being stopped.
    """

    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionKind(str, Enum):
    """Kind of work the execution represents."""

    PLAN = "plan"
    EXECUTE = "execute"
    DEBRIEF = "debrief"


class Execution(BaseModel):
    """An execution row in the Command Center database.

    Field layout mirrors the `executions` table columns exactly. The
    ``metadata`` dict is stored as ``metadata_json`` (JSON-encoded) in the
    database; the mapping is handled by the repository layer.
    """

    model_config = ConfigDict(use_enum_values=False)

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
    idempotency_token_prefix: Optional[str] = None
    idempotency_key: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
