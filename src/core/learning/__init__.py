"""Phase 2A learning subsystem.

Public surface:
    * ``render_pitfalls_section`` — bullet renderer with hard char cap (Phase 2A)
    * ``MAX_PITFALL_CHARS``        — the cap constant (≈ 2,000 tokens)
    * ``OutcomeSyncService``       — Phase 3A pull-on-demand outcome ingestion
    * ``OutcomeSyncSummary``       — per-project sync result dataclass
    * ``classify_outcome``         — pure classifier (regressed > rolled_back > success)

The ``register_prompt_cache_invalidator`` subscriber lives in
``src.core.learning.cache_invalidator`` and is imported directly there;
keeping the bus-touching seam out of the package init avoids a
``PromptLoader``→``EventBus`` import cycle when the loader doesn't need
the bus.
"""

from src.core.learning.outcome_sync import (
    OutcomeSyncService,
    OutcomeSyncSummary,
    classify_outcome,
)
from src.core.learning.pitfalls import MAX_PITFALL_CHARS, render_pitfalls_section

__all__ = [
    "MAX_PITFALL_CHARS",
    "OutcomeSyncService",
    "OutcomeSyncSummary",
    "classify_outcome",
    "render_pitfalls_section",
]
