"""Execution-side subscriber wiring for Sentinel learning system.

Re-exports the public entry-point so callers (CLI, tests) don't import
submodules directly.
"""

from src.core.execution.post_execute import (
    TicketContext,
    register_post_execute_subscribers,
)

__all__ = ["TicketContext", "register_post_execute_subscribers"]
