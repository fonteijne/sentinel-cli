"""Textual App for the Sentinel interactive launcher.

Entry point ``run()`` is invoked by the ``sentinel interactive`` Click command.
The app composes a single :class:`~src.tui.screens.home.HomeScreen` and
delegates all workflow invocation to wrappers in :mod:`src.tui.actions` which
call the existing Click command callbacks.
"""

from __future__ import annotations

from textual.app import App

from src.tui.screens.home import HomeScreen


class SentinelApp(App[None]):
    """Sentinel interactive launcher."""

    TITLE = "Sentinel"
    SUB_TITLE = "interactive launcher"

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
    ]

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())


def run() -> None:
    """Run the Sentinel TUI. Blocks until the user quits.

    Mouse input is disabled: the launcher is keyboard-first, and some
    terminal/docker-exec combinations deliver X10-protocol mouse reports
    whose bytes (≥ 0x80 once the click is past column ~95) aren't valid
    UTF-8, which crashes Textual's input-decoder thread. Keyboard-only
    sidesteps the protocol mismatch entirely.
    """
    SentinelApp().run(mouse=False)
