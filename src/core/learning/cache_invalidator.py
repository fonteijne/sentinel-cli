"""Subscribe a :class:`PromptLoader` to :class:`PostmortemRecorded`.

Phase 2A: conservative full-cache clear (plan §Notes). There is exactly one
stack live (drupal); per-stack invalidation is a one-line refactor when
Phase 2B exercises a second stack and the cost matters. Until then,
``PostmortemRecorded`` clears every cached ``(agent_name, stack_type)`` entry.

The subscriber is registered alongside the existing post-execute subscribers
in ``src.cli`` (Task 10) so the invalidator and the cap-out path share a
lifetime. Unit tests construct it directly.
"""

from __future__ import annotations

import logging

from src.core.events import EventBus, PostmortemRecorded
from src.core.events.types import BaseEvent
from src.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)


def register_prompt_cache_invalidator(
    bus: EventBus,
    loader: PromptLoader,
) -> None:
    """Wire ``PostmortemRecorded`` to ``loader.clear_cache``.

    Mirrors the closure-based registration pattern in
    :func:`src.core.execution.post_execute.register_post_execute_subscribers`.
    The handler defends against subscriber-internal exceptions so a misbehaving
    cache (e.g. a future loader that raises in ``clear_cache``) cannot crash
    the bus's fan-out for other subscribers.
    """

    def _handle(event: BaseEvent) -> None:
        # Defensive isinstance — the bus already filters by exact type, but
        # mirror the existing post_execute pattern for consistency.
        if not isinstance(event, PostmortemRecorded):
            return
        try:
            loader.clear_cache()
            logger.info(
                "Prompt cache cleared after postmortem #%d", event.postmortem_id
            )
        except Exception:
            logger.error("prompt cache invalidator crashed", exc_info=True)

    bus.subscribe(PostmortemRecorded, _handle)
