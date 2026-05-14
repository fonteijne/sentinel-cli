"""Phase 1 event catalogue.

Pydantic v2 models. Decision is settled (plan §Notes "Why pydantic v2 for events"):
pydantic v2, NOT dataclass — JSON serialization for ``payload_json`` is free, and
matches the d75d276 reference shape.

Each event has:
  - ``execution_id`` — FK to ``executions(id)``; carries the per-execution scope
    that ``EventBus.publish`` uses to compute the per-execution monotonic ``seq``.
  - ``ts`` — ISO-8601 UTC. Defaults to empty string; the bus fills it on publish
    so callers don't have to thread a clock everywhere. We deliberately do NOT
    use ``Field(default_factory=...)`` because then unit tests can't easily
    construct an event with ``ts=""`` to verify the bus fills it.
  - ``type`` — string discriminator (``Literal[...]`` per subclass). The bus
    serializes via ``model_dump_json()`` so the discriminator survives the round
    trip and the event type can be reconstructed by readers.

No imports from ``src.agents.*``: events are foundation; agents depend on events,
not vice versa.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class BaseEvent(BaseModel):
    """Common fields for every Phase 1 event.

    Subclasses MUST override ``type`` with their own ``Literal[...]`` value.
    """

    execution_id: str
    ts: str = ""  # filled by EventBus.publish if empty
    type: str


class TestResultRecorded(BaseEvent):
    # ``__test__ = False`` tells pytest's auto-collector this is a domain class,
    # not a test class. Without it, the ``Test`` prefix triggers a warning.
    __test__ = False

    type: Literal["TestResultRecorded"] = "TestResultRecorded"
    passed: bool
    attempt: int
    structured_errors_count: int
    agent: str | None = None


class StaticCheckRecorded(BaseEvent):
    type: Literal["StaticCheckRecorded"] = "StaticCheckRecorded"
    checker: str
    passed: bool
    structured_errors_count: int
    agent: str | None = None


class DeveloperCappedOut(BaseEvent):
    type: Literal["DeveloperCappedOut"] = "DeveloperCappedOut"
    agent: str
    attempts: int
    # Raw dicts at this boundary, not StructuredError TypedDict — events cross
    # process boundaries via JSON, so the typed shape stays in the agent layer.
    last_structured_errors: list[dict]


class PostmortemRecorded(BaseEvent):
    type: Literal["PostmortemRecorded"] = "PostmortemRecorded"
    postmortem_id: int
    failure_signature: str


class ReviewerHandoffTriggered(BaseEvent):
    type: Literal["ReviewerHandoffTriggered"] = "ReviewerHandoffTriggered"
    reviewer_agent: str
    finding_class: str
    blocker_count: int
    next_actor: str = "planner"


class PromptBudgetExceeded(BaseEvent):
    """Emitted when the pitfalls renderer drops bullets to stay under the cap.

    Phase 2A: the loader logs a warning rather than publishing this — the
    event class exists for Phase 2B/C callers (overlay PR proposer, planner
    hooks) to publish with the renderer's ``dropped_ids`` return value.
    """

    type: Literal["PromptBudgetExceeded"] = "PromptBudgetExceeded"
    section: str
    dropped_postmortem_ids: list[int]
    dropped_chars: int
    agent: str | None = None


class FeedbackRuleExtracted(BaseEvent):
    """Emitted by `sentinel learning extract` for each cluster the extractor lands.

    Phase 2C: `execution_id` is the synthetic `learning-extract-<UTC ISO>` row
    that the CLI command seeds before calling extract_clusters() so the bus FK
    to executions(id) is satisfied.
    """

    type: Literal["FeedbackRuleExtracted"] = "FeedbackRuleExtracted"
    rule_id: int
    signature: str
    scope: str
    agent_target: str
    observation_count: int
    distinct_projects: int
    confidence: int


class FeedbackRulePromoted(BaseEvent):
    """Emitted by `sentinel learning propose` per rule once a draft MR lands.

    `mr_url` MUST be a real URL — dry-run paths do NOT publish this event.
    `execution_id` is the synthetic `learning-propose-<UTC ISO>` row.
    """

    type: Literal["FeedbackRulePromoted"] = "FeedbackRulePromoted"
    rule_id: int
    scope: str
    mr_url: str
    branch_name: str


class FeedbackRuleRevoked(BaseEvent):
    """Emitted by `sentinel learning revoke` (D4 append-only revocation path).

    `execution_id` is the synthetic `learning-revoke-<UTC ISO>` row.
    """

    type: Literal["FeedbackRuleRevoked"] = "FeedbackRuleRevoked"
    rule_id: int
    revoked_by: str
    reason: str


class OutcomeRecorded(BaseEvent):
    """Emitted by ``OutcomeSyncService`` when an execution is tagged.

    One event per (execution_id, outcome) tag. The ``execution_id`` is the
    real run; for offline ``sentinel outcomes sync`` the bus also has a
    synthetic ``outcomes-sync-<UTC ISO>`` execution row created by the CLI
    so non-tagged-execution events (errors, summaries) have an FK target.

    Informational only: the outcome write itself happens via SQL UPDATE in
    the sync service, mirroring how postmortems are inserted then
    ``PostmortemRecorded`` is published as a notification.
    """

    type: Literal["OutcomeRecorded"] = "OutcomeRecorded"
    mr_iid: int
    project: str
    outcome: Literal["success", "rolled_back", "regressed"]
    merged_at: Optional[str] = None
    reverted_by_mr_iid: Optional[int] = None
    regressed_pipeline_id: Optional[int] = None
    evidence_summary: str
