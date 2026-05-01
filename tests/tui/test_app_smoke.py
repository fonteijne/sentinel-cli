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
