"""Phase 2C cluster extractor — postmortems → feedback_rules at probation.

Pure functions over a sqlite3 connection. The CLI command seeds a synthetic
``executions`` row and constructs the ``EventBus`` before calling
``extract_clusters`` — keeping the orchestration entrypoint testable without
either dependency (tests pass ``event_bus=None``).

Append-only spirit: this module never deletes or rewrites postmortem rows, and
its only write into ``feedback_rules`` is the UPSERT that
:func:`src.core.persistence.feedback_rules.upsert_rule` performs.
"""

from __future__ import annotations

import itertools
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Protocol

from src.core.persistence import query_postmortem_clusters, upsert_rule


class _EventBusLike(Protocol):
    """Minimal protocol the extractor needs from an event bus.

    The real ``src.core.events.bus.EventBus`` satisfies this; tests pass any
    object with a ``.publish(event)`` method (or ``None`` to skip emission).
    Stays narrow on purpose — keeps the import surface of this module small.
    """

    def publish(self, event: object) -> None: ...

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    """One accepted cluster after thresholds + whack-a-mole filters.

    ``rule_id`` is ``-1`` in dry-run mode (no UPSERT performed).
    """

    rule_id: int
    signature: str
    scope: str
    agent_target: str
    observation_count: int
    distinct_projects: int
    confidence: int
    first_postmortem_id: int
    last_postmortem_id: int


@dataclass
class ExtractionSummary:
    considered: int
    accepted: int
    rejected_pure_symptom: int
    rejected_below_thresholds: int
    rules: list[ExtractionResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Confidence curve (Appendix C.6, Phase 2C subset — clamped to [0, 95])
# ---------------------------------------------------------------------------


def compute_confidence(observation_count: int, distinct_projects: int) -> int:
    """Phase 2C confidence curve, clamped to [0, 95].

    base = 50 (cap-out default; matches insert_postmortem default at
    src/core/persistence/postmortems.py:36).
    obs_term = 10 * min(5, max(0, observation_count - 1))
    proj_term = 5 * min(3, max(0, distinct_projects - 1))
    """
    base = 50
    obs_term = 10 * min(5, max(0, observation_count - 1))
    proj_term = 5 * min(3, max(0, distinct_projects - 1))
    return max(0, min(95, base + obs_term + proj_term))


# ---------------------------------------------------------------------------
# Whack-a-mole guardrail (plan §WHACK_A_MOLE_GUARDRAIL)
# ---------------------------------------------------------------------------


_WHACK_A_MOLE_BLACKLIST = (
    "failed assertion",
    "assertion failed",
    "test failed",
    "unknown error",
    "error: unknown",
    "syntax error",
)


def is_pure_symptom(failure_signature: str) -> bool:
    """True if the signature is too generic to promote.

    Heuristic: short (< 30 chars after normalization) AND its lowercased form
    starts with one of the blacklist phrases AND contains no structural tokens
    (``::``, ``.``, digits) that would indicate a real root cause.
    """
    s = failure_signature.lower().strip()
    if len(s) >= 30:
        return False
    if not any(s.startswith(prefix) for prefix in _WHACK_A_MOLE_BLACKLIST):
        return False
    if "::" in s or "." in s or any(c.isdigit() for c in s):
        return False
    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def extract_clusters(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    min_observations: int = 3,
    min_projects: int = 2,
    dry_run: bool = False,
    event_bus: Optional[_EventBusLike] = None,
    execution_id: Optional[str] = None,
) -> ExtractionSummary:
    """Cluster recent postmortems and UPSERT probation rows in feedback_rules.

    Steps (see plan Task 10):
      1. SELECT joined postmortems × executions in the window via
         ``query_postmortem_clusters`` (already ORDER BY (stack, agent,
         signature, created_at ASC)).
      2. Group with ``itertools.groupby`` — same composite key.
      3. For each cluster: count observations and distinct (non-empty)
         project keys, derive first/last postmortem ids.
      4. Reject clusters that fail the size thresholds; reject pure-symptom
         signatures (whack-a-mole).
      5. UPSERT survivors (skip the write in dry-run; ``rule_id = -1``).
      6. Optionally publish ``FeedbackRuleExtracted`` per accepted rule.

    ``event_bus`` is typed ``object`` here so unit tests can pass any object
    with a ``.publish`` method (or ``None`` to skip emission). The real
    ``EventBus`` is imported lazily below so importing this module does not
    pull the bus + its dependencies.

    ``execution_id`` is the synthetic id stamped on each ``FeedbackRuleExtracted``
    event. When ``None`` (default — preserved for unit-test callers), the
    module generates ``"learning-extract-<UTC ISO>"`` internally. Production
    callers (the CLI) generate the id once, seed an ``executions`` row with
    that id (so the bus's FK to ``executions.id`` is satisfied), then pass the
    id verbatim — the module uses what it's given without modification.
    """
    # Lazy imports so the module is importable in unit tests without a bus.
    from src.core.events.types import FeedbackRuleExtracted  # noqa: PLC0415

    rows = query_postmortem_clusters(conn, days=days, only_active=True)

    summary = ExtractionSummary(
        considered=0,
        accepted=0,
        rejected_pure_symptom=0,
        rejected_below_thresholds=0,
    )

    def _key(r: sqlite3.Row) -> tuple[str, str, str]:
        return (r["stack_type"], r["agent"], r["failure_signature"])

    for (stack_type, agent, signature), group in itertools.groupby(rows, key=_key):
        cluster = list(group)
        summary.considered += 1

        observation_count = len(cluster)
        project_keys = {r["project_key"] for r in cluster if r["project_key"]}
        distinct_projects = len(project_keys)

        ids = [r["id"] for r in cluster]
        first_postmortem_id = min(ids)
        last_postmortem_id = max(ids)

        if observation_count < min_observations or distinct_projects < min_projects:
            summary.rejected_below_thresholds += 1
            continue

        if is_pure_symptom(signature):
            logger.warning(
                "extract_clusters: rejecting pure-symptom cluster "
                "(scope=%s agent=%s signature=%r obs=%d proj=%d)",
                stack_type,
                agent,
                signature,
                observation_count,
                distinct_projects,
            )
            summary.rejected_pure_symptom += 1
            continue

        confidence = compute_confidence(observation_count, distinct_projects)

        if dry_run:
            rule_id = -1
        else:
            rule_id = upsert_rule(
                conn,
                signature=signature,
                scope=stack_type,
                agent_target=agent,
                rule_text=signature,
                confidence=confidence,
                observation_count=observation_count,
                distinct_projects=distinct_projects,
                first_postmortem_id=first_postmortem_id,
                last_postmortem_id=last_postmortem_id,
            )
            if event_bus is not None:
                effective_execution_id = execution_id or (
                    "learning-extract-"
                    + datetime.now(timezone.utc).isoformat()
                )
                event_bus.publish(
                    FeedbackRuleExtracted(
                        execution_id=effective_execution_id,
                        rule_id=rule_id,
                        signature=signature,
                        scope=stack_type,
                        agent_target=agent,
                        observation_count=observation_count,
                        distinct_projects=distinct_projects,
                        confidence=confidence,
                    )
                )

        summary.accepted += 1
        summary.rules.append(
            ExtractionResult(
                rule_id=rule_id,
                signature=signature,
                scope=stack_type,
                agent_target=agent,
                observation_count=observation_count,
                distinct_projects=distinct_projects,
                confidence=confidence,
                first_postmortem_id=first_postmortem_id,
                last_postmortem_id=last_postmortem_id,
            )
        )

    return summary
