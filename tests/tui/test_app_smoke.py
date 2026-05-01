"""Smoke test for the Sentinel TUI via Textual's headless test harness.

The goal is tiny: prove that the app mounts, the home screen renders, and
that the action list is populated from the :mod:`src.tui.actions` registry.
It does NOT invoke any Click callback — wiring to actions is covered by
existing CLI tests plus manual verification inside ``sentinel-dev``.
"""

from __future__ import annotations

import pytest

pytest_plugins: list[str] = []

textual = pytest.importorskip("textual", reason="textual not installed yet")


@pytest.mark.asyncio
async def test_app_mounts_and_shows_actions() -> None:
    from src.tui.actions import ACTIONS
    from src.tui.app import SentinelApp
    from textual.widgets import ListView

    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        actions_list = app.screen.query_one("#actions", ListView)
        # One ListItem per registered action.
        assert len(actions_list.children) == len(ACTIONS), (
            f"expected {len(ACTIONS)} action items, got {len(actions_list.children)}"
        )
        # The ids follow the ``action-<key>`` convention so the home screen
        # can dispatch by key.
        ids = {child.id for child in actions_list.children}
        assert ids == {f"action-{a.key}" for a in ACTIONS}


@pytest.mark.asyncio
async def test_ticket_prompt_cancels_on_escape() -> None:
    from src.tui.app import SentinelApp
    from src.tui.screens.ticket import TicketPromptScreen

    captured: list[object] = []

    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(TicketPromptScreen("Plan a ticket"), captured.append)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    assert captured == [None]


def test_strip_emoji_handles_validate_command_markers() -> None:
    """The emoji the existing CLI commands print must vanish from the
    captured output — otherwise Textual measures width=2 for each glyph,
    the operator's terminal often renders width=1 (or a replacement
    char), and the border columns drift until the top bar is offscreen.
    """
    from src.tui.widgets.run_output import _strip_emoji

    samples = [
        ("🔐 Validating API Credentials (Sentinel v0.3.6)",
         "Validating API Credentials (Sentinel v0.3.6)"),
        # keycap sequence "1" + VS16 + combining-keycap: the VS16 and the
        # combining-keycap go, the plain digit is kept — it's width-1 and
        # still communicates the step number.
        ("1️⃣  Testing Jira API...",
         "1  Testing Jira API..."),
        ("   ✅ Jira connected: Carsten de la Fonteijne",
         "Jira connected: Carsten de la Fonteijne"),
        ("📊 Sentinel Status", "Sentinel Status"),
        ("🏗️  Project: COE_JIRATESTAI", "Project: COE_JIRATESTAI"),
        ("📤 push-pending: drained=0 still_pending=0 errors=0",
         "push-pending: drained=0 still_pending=0 errors=0"),
        ("plain text, no emoji", "plain text, no emoji"),
    ]
    for raw, expected in samples:
        assert _strip_emoji(raw).strip() == expected.strip(), (
            f"raw={raw!r} → got={_strip_emoji(raw)!r}, want={expected!r}"
        )


def test_capture_stdout_to_log_monkeypatches_click_echo() -> None:
    """Regression: ``click.echo`` caches its stream at first use; plain
    ``sys.stdout`` replacement misses it. The capture context must monkey-
    patch ``click.echo`` so output from Click-decorated callbacks still
    reaches our forwarder. Without this the launcher looked frozen during
    any action — the Click command ran, but every ``click.echo`` wrote past
    our replaced stdout straight to the cached original.

    Tests the monkey-patch logic directly with a fake ``app`` + ``log`` to
    avoid the ``call_from_thread`` marshaling (which expects a background
    thread); production always runs capture from ``@work(thread=True)``.
    """
    import click
    from src.tui.widgets.run_output import capture_stdout_to_log

    # Warm Click's cache before capture, mimicking real-world use.
    click.echo("warmup", err=True)

    captured: list[str] = []

    class _FakeLog:
        def write_line(self, line: str) -> None:
            captured.append(line)

    class _FakeApp:
        def call_from_thread(self, fn, *args, **kwargs):
            # Same-thread variant: just run it.
            fn(*args, **kwargs)

    with capture_stdout_to_log(_FakeApp(), _FakeLog()):  # type: ignore[arg-type]
        click.echo("hello from click.echo")
        print("hello from print")

    joined = "\n".join(captured)
    assert "hello from click.echo" in joined, joined
    assert "hello from print" in joined, joined

    # Monkey-patch must be removed on exit.
    assert click.echo.__module__ == "click.utils" or click.echo.__name__ == "echo"
