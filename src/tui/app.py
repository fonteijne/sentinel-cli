"""Textual App for the Sentinel interactive launcher.

Entry point ``run()`` is invoked by the ``sentinel interactive`` Click command.
The app composes a single :class:`~src.tui.screens.home.HomeScreen` and
delegates all workflow invocation to wrappers in :mod:`src.tui.actions` which
call the existing Click command callbacks.
"""

from __future__ import annotations

import sys
from typing import Optional

import click
from textual.app import App

from src.tui.bootstrap import ServiceHandle, ensure_service
from src.tui.screens.home import HomeScreen


class SentinelApp(App[None]):
    """Sentinel interactive launcher."""

    TITLE = "Sentinel"
    SUB_TITLE = "interactive launcher"

    # Bindings live on the App (not the home Screen) so the quit keys work
    # regardless of which widget has focus — Screen-level `("q", "quit")`
    # resolves `action_quit` on the Screen first, which doesn't define it,
    # and the bubble to the App can be intercepted by focused widgets.
    BINDINGS = [
        ("q", "tui_quit", "Quit"),
        ("ctrl+q", "tui_quit", "Quit"),
        ("ctrl+c", "tui_quit", "Quit"),
    ]

    # Attached by ``run()`` after ``ensure_service`` succeeds. Track 3 reads
    # this to configure the HTTP client; screens today ignore it.
    service: Optional[ServiceHandle] = None

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())

    def action_tui_quit(self) -> None:
        """Unconditional exit, bypassing any focused-widget interception."""
        self.exit()


def run() -> None:
    """Run the Sentinel TUI. Blocks until the user quits.

    Mouse input is disabled: the launcher is keyboard-first, and some
    terminal/docker-exec combinations deliver X10-protocol mouse reports
    whose bytes (≥ 0x80 once the click is past column ~95) aren't valid
    UTF-8, which crashes Textual's input-decoder thread. Keyboard-only
    sidesteps the protocol mismatch entirely.

    Before mounting the UI we ensure the Command Center service is up and
    discoverable (see :mod:`src.tui.bootstrap`). A spawn failure here exits
    with code 3 — matching the ``_remote_execute`` convention in cli.py for
    "service unreachable" — rather than launching a TUI that can't talk to
    anything.
    """
    try:
        handle = ensure_service()
    except (RuntimeError, TimeoutError) as exc:
        click.echo(
            f"Failed to start Sentinel service: {exc}\n"
            "Try `sentinel serve` manually to see the error.",
            err=True,
        )
        sys.exit(3)

    app = SentinelApp()
    app.service = handle
    app.run(mouse=False)
