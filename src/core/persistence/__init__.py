"""Persistence layer for Sentinel learning system.

Re-exports the small set of helpers callers should use:
    - connect: open a SQLite connection with WAL + foreign_keys=ON
    - apply_migrations: run pending migrations idempotently
    - insert_postmortem: append-only postmortem insert
    - list_postmortems: CLI-facing SELECT (broad filter, low confidence floor)
    - query_active_postmortems: planner-prompt SELECT (stack-scoped, ranked)
    - query_postmortem_clusters: Phase 2C extractor SELECT (windowed, joined to executions)
    - upsert_rule / query_promotable / list_rules / mark_proposed /
      mark_promoted / revoke_rule / mark_superseded: feedback_rules helpers
    - read_sync_state / upsert_sync_state: Phase 3A project_sync_state watermark
    - update_execution_outcome: Phase 3A append-once outcome tag on executions
    - list_executions_for_ticket_untagged: Phase 3A untagged-execution lookup
"""

from src.core.persistence.db import apply_migrations, connect
from src.core.persistence.feedback_rules import (
    list_rules,
    mark_promoted,
    mark_proposed,
    mark_superseded,
    query_promotable,
    revoke_rule,
    upsert_rule,
)
from src.core.persistence.postmortems import (
    insert_postmortem,
    list_postmortems,
    query_active_postmortems,
    query_postmortem_clusters,
)
from src.core.persistence.sync_state import (
    list_executions_for_ticket_untagged,
    read_sync_state,
    update_execution_outcome,
    upsert_sync_state,
)

__all__ = [
    "apply_migrations",
    "connect",
    "insert_postmortem",
    "list_executions_for_ticket_untagged",
    "list_postmortems",
    "list_rules",
    "mark_promoted",
    "mark_proposed",
    "mark_superseded",
    "query_active_postmortems",
    "query_postmortem_clusters",
    "query_promotable",
    "read_sync_state",
    "revoke_rule",
    "update_execution_outcome",
    "upsert_rule",
    "upsert_sync_state",
]
