"""Event layer for Sentinel learning system.

Re-exports the bus and the Phase 1 event catalogue. Subscribers and producers
should import from here rather than the submodules so the public surface stays
explicit.
"""

from src.core.events.bus import EventBus
from src.core.events.types import (
    BaseEvent,
    DeveloperCappedOut,
    FeedbackRuleExtracted,
    FeedbackRulePromoted,
    FeedbackRuleRevoked,
    OutcomeRecorded,
    PostmortemRecorded,
    PromptBudgetExceeded,
    ReviewerHandoffTriggered,
    StaticCheckRecorded,
    TestResultRecorded,
)

__all__ = [
    "BaseEvent",
    "DeveloperCappedOut",
    "EventBus",
    "FeedbackRuleExtracted",
    "FeedbackRulePromoted",
    "FeedbackRuleRevoked",
    "OutcomeRecorded",
    "PostmortemRecorded",
    "PromptBudgetExceeded",
    "ReviewerHandoffTriggered",
    "StaticCheckRecorded",
    "TestResultRecorded",
]
