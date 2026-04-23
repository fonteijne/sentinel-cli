"""Sentinel core events package.

Re-exports the event models, the discriminated-union ``TypeAdapter`` used
for rehydrating persisted rows, and the in-process :class:`EventBus`.
"""

from src.core.events.bus import EventBus
from src.core.events.types import (
    # Union + constants
    AnyEvent,
    AnyEventAdapter,
    TERMINAL_EVENT_TYPES,
    # Base
    SentinelEvent,
    # Lifecycle
    ExecutionStarted,
    ExecutionCompleted,
    ExecutionFailed,
    ExecutionCancelling,
    ExecutionCancelled,
    PhaseChanged,
    # Agent / tool
    AgentStarted,
    AgentFinished,
    AgentMessageSent,
    AgentResponseReceived,
    ToolCalled,
    # Results
    TestResultRecorded,
    FindingPosted,
    CostAccrued,
    # Interactive / revision
    DebriefTurn,
    RevisionRequested,
    # Error-class
    RateLimited,
)

__all__ = [
    "EventBus",
    "AnyEvent",
    "AnyEventAdapter",
    "TERMINAL_EVENT_TYPES",
    "SentinelEvent",
    "ExecutionStarted",
    "ExecutionCompleted",
    "ExecutionFailed",
    "ExecutionCancelling",
    "ExecutionCancelled",
    "PhaseChanged",
    "AgentStarted",
    "AgentFinished",
    "AgentMessageSent",
    "AgentResponseReceived",
    "ToolCalled",
    "TestResultRecorded",
    "FindingPosted",
    "CostAccrued",
    "DebriefTurn",
    "RevisionRequested",
    "RateLimited",
]
