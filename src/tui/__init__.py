"""Sentinel interactive TUI.

Entry point: ``src.tui.app.run()`` — invoked by the ``sentinel interactive``
(aliased ``sentinel i``) Click command. The TUI is a thin launcher over the
existing Click commands; it does not re-implement any workflow.
"""

from src.tui.app import run

__all__ = ["run"]
