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


@pytest.mark.asyncio
async def test_ticket_prompt_lists_existing_worktrees_and_new_item() -> None:
    """Plan/debrief modals must list existing worktrees plus a '+ New
    ticket' row; execute must list worktrees only (no new-ticket
    affordance) because the execute workflow requires a plan first.
    """
    from src.tui.app import SentinelApp
    from src.tui.screens.ticket import TicketPromptScreen
    from textual.widgets import ListView

    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        # plan-style modal: existing tickets + New
        captured: list[object] = []
        app.push_screen(
            TicketPromptScreen(
                "Plan a ticket",
                project_prefix="DHLEXS_DHLEXC",
                existing_tickets=["DHLEXS_DHLEXC-289", "DHLEXS_DHLEXC-356"],
                allow_new=True,
            ),
            captured.append,
        )
        await pilot.pause()
        lst = app.screen.query_one("#ticket-list", ListView)
        ids = [c.id for c in lst.children]
        assert ids == [
            "ticket-item-DHLEXS_DHLEXC-289",
            "ticket-item-DHLEXS_DHLEXC-356",
            "ticket-item-new",
        ]
        await pilot.press("escape")
        await pilot.pause()
        assert captured == [None]

        # execute-style modal: no New row.
        captured2: list[object] = []
        app.push_screen(
            TicketPromptScreen(
                "Execute a plan",
                project_prefix="DHLEXS_DHLEXC",
                existing_tickets=["DHLEXS_DHLEXC-289"],
                allow_new=False,
            ),
            captured2.append,
        )
        await pilot.pause()
        lst2 = app.screen.query_one("#ticket-list", ListView)
        ids2 = [c.id for c in lst2.children]
        assert ids2 == ["ticket-item-DHLEXS_DHLEXC-289"]
        await pilot.press("escape")
        await pilot.pause()
        assert captured2 == [None]


@pytest.mark.asyncio
async def test_ticket_prompt_dismisses_with_selected_existing_ticket() -> None:
    """Picking a worktree from the list dismisses the modal with that id."""
    from src.tui.app import SentinelApp
    from src.tui.screens.ticket import TicketPromptScreen

    captured: list[object] = []
    app = SentinelApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(
            TicketPromptScreen(
                "Plan a ticket",
                existing_tickets=["DHLEXS_DHLEXC-289", "DHLEXS_DHLEXC-356"],
                allow_new=True,
            ),
            captured.append,
        )
        await pilot.pause()
        # Default focus is on the ListView; Enter on the first item.
        await pilot.press("enter")
        await pilot.pause()

    assert captured == ["DHLEXS_DHLEXC-289"]


def test_resolve_ticket_id_prefixes_bare_numbers() -> None:
    """Bare numbers must get auto-prefixed with the project's Jira key so
    the user can type just '356' when DHLEXS_DHLEXC is active.
    """
    from src.tui.screens.home import HomeScreen

    assert HomeScreen._resolve_ticket_id("356", "DHLEXS_DHLEXC") == "DHLEXS_DHLEXC-356"
    assert HomeScreen._resolve_ticket_id("  356 ", "DHLEXS_DHLEXC") == "DHLEXS_DHLEXC-356"
    # Already qualified — leave alone.
    assert HomeScreen._resolve_ticket_id("IO-42", "DHLEXS_DHLEXC") == "IO-42"
    assert HomeScreen._resolve_ticket_id("DHLEXS_DHLEXC-356", "DHLEXS_DHLEXC") == "DHLEXS_DHLEXC-356"
    # No project selected — can't prefix; return as typed.
    assert HomeScreen._resolve_ticket_id("356", None) == "356"
    # Empty stays empty (the modal rejects this before calling).
    assert HomeScreen._resolve_ticket_id("", "DHLEXS_DHLEXC") == ""


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


def test_capture_stdout_to_log_fd_level() -> None:
    """Fd-level capture must pick up writes that happen at the OS fd layer:

    * ``os.write(1, ...)`` / ``os.write(2, ...)`` — what Python's
      TextIOWrapper eventually calls under the hood.
    * Child-process output — subprocess inherits the parent's fd 1/2
      and writes past any Python-level pipe. This is the regression the
      fd capture exists to close (git / ssh were splashing across the
      TUI frame before).

    We can't assert ``print(...)`` or ``click.echo(...)`` here because
    pytest intercepts ``sys.stdout`` / ``sys.stderr`` at the Python
    level, so those writes never touch fd 1/2 in a pytest environment.
    In production the CLI commands use real file wrappers around fd 1/2,
    so the underlying fd write is what matters — and that's what we test.
    """
    import os
    import subprocess
    import sys
    import threading
    from src.tui.widgets.run_output import capture_stdout_to_log

    captured: list[str] = []
    lock = threading.Lock()

    class _FakeLog:
        def write_line(self, line: str) -> None:
            with lock:
                captured.append(line)

    class _FakeApp:
        # No _driver → the Textual-rescue step is a no-op. Good: we're
        # not running a UI in this test.
        def call_from_thread(self, fn, *args, **kwargs):  # type: ignore[no-untyped-def]
            fn(*args, **kwargs)

    with capture_stdout_to_log(_FakeApp(), _FakeLog()):  # type: ignore[arg-type]
        os.write(1, b"hello from os.write stdout\n")
        os.write(2, b"hello from os.write stderr\n")
        # Spawn a subprocess that writes to its own fd 2 — must be
        # captured because the child inherits the redirected fd 2.
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('hello from subprocess stderr\\n')",
            ],
            check=True,
        )

    joined = "\n".join(captured)
    assert "hello from os.write stdout" in joined, joined
    assert "hello from os.write stderr" in joined, joined
    assert "hello from subprocess stderr" in joined, joined
