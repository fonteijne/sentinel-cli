"""Tests for the interactive ``sentinel`` menu (no-arg invocation).

Covers ``src.cli._collect_loaded_tickets``, ``_prompt_ticket``,
``_prompt_new_ticket``, ``_prompt_action``, ``_dispatch``, and the
``invoke_without_command=True`` wiring on the ``cli()`` group.

All ``questionary`` prompts are monkeypatched to a stub returning a canned
value — the real prompt would require a TTY and would hang under pytest.
Mirrors the ``CliRunner`` idiom from ``tests/test_cli_postmortems.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from src import cli as cli_module
from src.cli import (
    _collect_loaded_tickets,
    _dispatch,
    _parse_ticket_input,
    _prompt_new_ticket,
    cli,
)


# ---------------------------------------------------------------------------
# Fixtures / stubs
# ---------------------------------------------------------------------------


# Sentinel: when the stub sees this value it raises KeyboardInterrupt from
# ``unsafe_ask()`` — used to simulate Ctrl-C. ``None`` continues to mean ESC
# (the real questionary behavior under ``unsafe_ask``).
_CTRL_C = object()


class _Stub:
    """Stand-in for a ``questionary`` prompt object.

    ``ask()`` and ``unsafe_ask()`` both return ``value``, except when
    ``value is _CTRL_C`` — then ``unsafe_ask`` raises KeyboardInterrupt and
    ``ask`` returns None (mirroring the real questionary contract where
    ``ask`` swallows Ctrl-C).
    """

    def __init__(self, value: Any) -> None:
        self._value = value

    def ask(self) -> Any:
        if self._value is _CTRL_C:
            return None
        return self._value

    def unsafe_ask(self) -> Any:
        if self._value is _CTRL_C:
            raise KeyboardInterrupt()
        return self._value


# Sentinel marker so the fixture can distinguish "patch with None" (ESC
# simulation) from "don't patch this prompt type". Plain ``None`` is a real
# expected value (questionary returns it on ESC under ``unsafe_ask``).
_UNSET = object()


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patch_questionary(monkeypatch: pytest.MonkeyPatch):
    """Install monkeypatched ``select`` / ``text`` / ``confirm``.

    A list value is consumed in order across consecutive calls — each
    ``questionary.<type>(...)`` invocation pops the next value from the
    queue. A scalar (including ``None``) is reused on every call.

    Pass ``_UNSET`` (the default) to leave a prompt type untouched.
    """

    def _make_factory(value: Any):
        if isinstance(value, list):
            queue = list(value)

            def _factory(*a: Any, **k: Any) -> _Stub:
                if not queue:
                    raise AssertionError(
                        "patch_questionary queue exhausted — too many prompts fired"
                    )
                return _Stub(queue.pop(0))

            return _factory
        return lambda *a, **k: _Stub(value)

    def _install(
        *,
        select: Any = _UNSET,
        text: Any = _UNSET,
        confirm: Any = _UNSET,
    ) -> None:
        if select is not _UNSET:
            monkeypatch.setattr(
                "src.cli.questionary.select", _make_factory(select)
            )
        if text is not _UNSET:
            monkeypatch.setattr(
                "src.cli.questionary.text", _make_factory(text)
            )
        if confirm is not _UNSET:
            monkeypatch.setattr(
                "src.cli.questionary.confirm", _make_factory(confirm)
            )

    return _install


# ---------------------------------------------------------------------------
# _collect_loaded_tickets
# ---------------------------------------------------------------------------


def _stub_config(projects: dict[str, Any]) -> Any:
    """Build a minimal ConfigLoader-like stub returning ``projects``."""

    class _C:
        def get_all_projects(self) -> dict[str, Any]:
            return dict(projects)

    return _C()


def _stub_worktree_mgr(map_: dict[str, list[str]]) -> Any:
    """Build a minimal WorktreeManager-like stub."""

    class _W:
        def list_worktrees(self, project_key: str) -> list[str]:
            return list(map_.get(project_key, []))

    return _W()


def test_collect_loaded_tickets_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({}))
    monkeypatch.setattr("src.cli.WorktreeManager", lambda: _stub_worktree_mgr({}))

    assert _collect_loaded_tickets() == []


def test_collect_loaded_tickets_sorted_multi_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projects = {"DHLEXS": {}, "ACME": {}, "JIRA": {}}
    worktrees = {
        "DHLEXS": ["DHLEXC-311", "DHLEXC-123"],
        "ACME": ["ACME-142"],
        "JIRA": ["TESTAI-1234"],
    }
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config(projects))
    monkeypatch.setattr(
        "src.cli.WorktreeManager", lambda: _stub_worktree_mgr(worktrees)
    )

    result = _collect_loaded_tickets()

    # Sorted alphabetically by (project_key, ticket_id).
    assert result == [
        ("ACME", "ACME-142"),
        ("DHLEXS", "DHLEXC-123"),
        ("DHLEXS", "DHLEXC-311"),
        ("JIRA", "TESTAI-1234"),
    ]


def test_collect_loaded_tickets_project_with_no_worktrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.cli.get_config", lambda: _stub_config({"ACME": {}})
    )
    monkeypatch.setattr(
        "src.cli.WorktreeManager", lambda: _stub_worktree_mgr({"ACME": []})
    )
    assert _collect_loaded_tickets() == []


# ---------------------------------------------------------------------------
# _prompt_new_ticket
# ---------------------------------------------------------------------------


def test_prompt_new_ticket_known_project(
    monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    monkeypatch.setattr(
        "src.cli.get_config", lambda: _stub_config({"ACME": {}})
    )
    patch_questionary(text="ACME-142")

    ctx = _make_ctx()
    result = _prompt_new_ticket(ctx)

    assert result == ("ACME", "ACME-142")


def test_prompt_new_ticket_lowercases_input(
    monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    monkeypatch.setattr(
        "src.cli.get_config", lambda: _stub_config({"ACME": {}})
    )
    patch_questionary(text="  acme-142  ")

    result = _prompt_new_ticket(_make_ctx())

    assert result == ("ACME", "ACME-142")


def test_prompt_new_ticket_invalid_input(
    monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({}))
    patch_questionary(text="garbage")

    assert _prompt_new_ticket(_make_ctx()) is None


def test_prompt_new_ticket_ctrl_c(
    monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({}))
    patch_questionary(text=None)  # questionary.ask() returns None on Ctrl-C

    assert _prompt_new_ticket(_make_ctx()) is None


def test_prompt_new_ticket_unknown_project_invokes_projects_add(
    monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    """An unknown project key triggers ``projects_add`` and re-reads config."""
    state = {"projects": {}}

    def _get_config() -> Any:
        return _stub_config(state["projects"])

    monkeypatch.setattr("src.cli.get_config", _get_config)
    patch_questionary(text="NEWPROJ-1")

    add_calls: list[bool] = []

    def fake_add() -> None:
        add_calls.append(True)
        # Simulate the user adding the project mid-prompt.
        state["projects"]["NEWPROJ"] = {}

    monkeypatch.setattr("src.cli.projects_add.callback", fake_add)

    result = _prompt_new_ticket(_make_ctx())

    assert add_calls == [True], "projects_add should be invoked exactly once"
    assert result == ("NEWPROJ", "NEWPROJ-1")


def test_prompt_new_ticket_unknown_project_user_aborts_add(
    monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    """If ``projects_add`` runs but the project still isn't there, return None."""
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({}))
    patch_questionary(text="NEWPROJ-1")
    # projects_add does nothing — simulates a user aborting the inner prompt.
    monkeypatch.setattr("src.cli.projects_add.callback", lambda: None)

    assert _prompt_new_ticket(_make_ctx()) is None


# ---------------------------------------------------------------------------
# _dispatch — wiring per action
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action,target_attr,expected_kwargs",
    [
        ("Debrief", "debrief", {"ticket_id": "ACME-1", "project": "ACME"}),
        ("Plan", "plan", {"ticket_id": "ACME-1", "project": "ACME"}),
        (
            "Execute",
            "execute",
            {"ticket_id": "ACME-1", "project": "ACME"},
        ),
    ],
)
def test_dispatch_routes_to_correct_command(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    target_attr: str,
    expected_kwargs: dict[str, Any],
) -> None:
    captured: dict[str, Any] = {}

    def fake(**kwargs: Any) -> None:
        captured.update(kwargs)

    target = getattr(cli_module, target_attr)
    monkeypatch.setattr(target, "callback", fake)

    ctx = _make_ctx()
    _dispatch(ctx, "ACME", "ACME-1", action)

    for key, value in expected_kwargs.items():
        assert captured.get(key) == value, captured


def test_dispatch_reset_confirmed(
    monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    captured: dict[str, Any] = {}

    def fake_reset(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("src.cli.reset.callback", fake_reset)
    patch_questionary(confirm=True)

    _dispatch(_make_ctx(), "ACME", "ACME-1", "Reset")

    assert captured.get("ticket_id") == "ACME-1"
    assert captured.get("project") == "ACME"
    assert captured.get("yes") is True


def test_dispatch_reset_declined(
    monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    called: list[bool] = []

    monkeypatch.setattr("src.cli.reset.callback", lambda **kw: called.append(True))
    patch_questionary(confirm=False)

    _dispatch(_make_ctx(), "ACME", "ACME-1", "Reset")

    assert called == [], "reset must NOT be invoked when confirm is declined"


# ---------------------------------------------------------------------------
# CliRunner integration — bare `sentinel` invocation
# ---------------------------------------------------------------------------


def test_bare_invocation_dispatches_plan(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    """`sentinel` (no args) → pick + new + ACME-142 + Plan → plan callback fires."""
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({"ACME": {}}))
    monkeypatch.setattr(
        "src.cli.WorktreeManager", lambda: _stub_worktree_mgr({"ACME": []})
    )
    # First select → "+ new…", then second select → "Plan".
    patch_questionary(select=["__new__", "Plan"], text="ACME-142")

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "src.cli.plan.callback",
        lambda **kw: captured.update(kw),
    )

    result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert captured.get("ticket_id") == "ACME-142"
    assert captured.get("project") == "ACME"


def test_menu_actions_does_not_include_execute_revise() -> None:
    """The menu must collapse Execute and Execute --revise into a single entry.

    Regression test for the CLI consolidation: state is auto-detected at
    runtime, so users cannot pick the wrong variant from the menu.
    """
    from src.cli import _MENU_ACTIONS

    assert "Execute --revise" not in _MENU_ACTIONS
    assert "Execute" in _MENU_ACTIONS
    assert _MENU_ACTIONS == ["Debrief", "Plan", "Execute", "Reset"]


def test_bare_invocation_reset_decline(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({"ACME": {}}))
    monkeypatch.setattr(
        "src.cli.WorktreeManager",
        lambda: _stub_worktree_mgr({"ACME": ["ACME-1"]}),
    )
    patch_questionary(
        select=[("ACME", "ACME-1"), "Reset"], confirm=False
    )

    called: list[bool] = []
    monkeypatch.setattr(
        "src.cli.reset.callback", lambda **kw: called.append(True)
    )

    result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert called == []


def test_bare_invocation_reset_confirm(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({"ACME": {}}))
    monkeypatch.setattr(
        "src.cli.WorktreeManager",
        lambda: _stub_worktree_mgr({"ACME": ["ACME-1"]}),
    )
    patch_questionary(
        select=[("ACME", "ACME-1"), "Reset"], confirm=True
    )

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "src.cli.reset.callback", lambda **kw: captured.update(kw)
    )

    result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert captured.get("ticket_id") == "ACME-1"
    assert captured.get("project") == "ACME"
    assert captured.get("yes") is True


def test_subcommand_invocation_does_not_fire_menu(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sentinel plan --help` MUST NOT trigger the menu (regression guard)."""

    def boom(*args: Any, **kw: Any) -> None:
        raise AssertionError("_run_menu fired despite a subcommand being given")

    monkeypatch.setattr("src.cli._run_menu", boom)

    # `plan --help` short-circuits Click before plan's own callback runs and
    # never invokes ``_run_menu`` because ``ctx.invoked_subcommand`` is set.
    result = runner.invoke(cli, ["plan", "--help"])

    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


def test_help_does_not_fire_menu(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sentinel --help` MUST NOT trigger the menu either."""

    def boom(*args: Any, **kw: Any) -> None:
        raise AssertionError("_run_menu fired during --help")

    monkeypatch.setattr("src.cli._run_menu", boom)

    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Commands:" in result.output


def test_esc_at_ticket_select_exits_cleanly(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    """ESC at the top-level ticket select exits with code 0 — no action invoked."""
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({"ACME": {}}))
    monkeypatch.setattr(
        "src.cli.WorktreeManager",
        lambda: _stub_worktree_mgr({"ACME": ["ACME-1"]}),
    )
    # ESC under unsafe_ask returns None at the top → exit.
    patch_questionary(select=None)

    for name in ("plan", "debrief", "execute", "reset"):
        monkeypatch.setattr(
            f"src.cli.{name}.callback",
            lambda **kw: pytest.fail(f"{name} should not be called"),
        )

    result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output


def test_ctrl_c_anywhere_exits_cleanly(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    """Ctrl-C (KeyboardInterrupt from unsafe_ask) is caught — exit code 0, no traceback."""
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({"ACME": {}}))
    monkeypatch.setattr(
        "src.cli.WorktreeManager",
        lambda: _stub_worktree_mgr({"ACME": ["ACME-1"]}),
    )
    patch_questionary(select=_CTRL_C)

    for name in ("plan", "debrief", "execute", "reset"):
        monkeypatch.setattr(
            f"src.cli.{name}.callback",
            lambda **kw: pytest.fail(f"{name} should not be called"),
        )

    result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output
    # No KeyboardInterrupt traceback in the output.
    assert "Traceback" not in result.output


# ---------------------------------------------------------------------------
# _parse_ticket_input — Bug 2: menu display form must round-trip
# ---------------------------------------------------------------------------


def test_parse_ticket_input_bare_form(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ACME-142` → ('ACME', 'ACME-142'). Project derived from prefix."""
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({}))

    assert _parse_ticket_input("ACME-142") == ("ACME", "ACME-142")


def test_parse_ticket_input_menu_display_form(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`DHLEXS_DHLEXC-384` → ('DHLEXS', 'DHLEXC-384') when DHLEXS is configured.

    Regression: previously this returned ('DHLEXS_DHLEXC', 'DHLEXS_DHLEXC-384'),
    causing the action menu label to read 'DHLEXS_DHLEXC_DHLEXS_DHLEXC-384'
    and the dispatch to pass the malformed ticket_id to JIRA.
    """
    monkeypatch.setattr(
        "src.cli.get_config", lambda: _stub_config({"DHLEXS": {}})
    )

    assert _parse_ticket_input("DHLEXS_DHLEXC-384") == ("DHLEXS", "DHLEXC-384")


def test_parse_ticket_input_menu_form_unconfigured_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the underscore prefix is NOT configured, fall back to bare-form parse.

    `FOO_BAR-1` with no project FOO → bare match against
    ``^[A-Z][A-Z0-9_]*-\\d+$`` succeeds, so the whole string is the ticket
    and ``FOO_BAR`` is the derived project key (the user must then add it).
    """
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({}))

    assert _parse_ticket_input("FOO_BAR-1") == ("FOO_BAR", "FOO_BAR-1")


def test_parse_ticket_input_lowercase_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Input is uppercased and stripped before matching."""
    monkeypatch.setattr(
        "src.cli.get_config", lambda: _stub_config({"DHLEXS": {}})
    )

    assert _parse_ticket_input("  dhlexs_dhlexc-384  ") == (
        "DHLEXS",
        "DHLEXC-384",
    )


def test_parse_ticket_input_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({}))

    assert _parse_ticket_input("garbage") is None
    assert _parse_ticket_input("") is None
    # No digits after the dash:
    assert _parse_ticket_input("ACME-") is None
    # Underscore-form with non-numeric tail:
    assert _parse_ticket_input("DHLEXS_NOTATICKET") is None


def test_prompt_new_ticket_round_trips_menu_display_form(
    monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    """Re-typing the menu's display label resolves to the right project + ticket."""
    monkeypatch.setattr(
        "src.cli.get_config", lambda: _stub_config({"DHLEXS": {}})
    )
    patch_questionary(text="DHLEXS_DHLEXC-384")

    result = _prompt_new_ticket(_make_ctx())

    assert result == ("DHLEXS", "DHLEXC-384")


# ---------------------------------------------------------------------------
# Back-navigation — Bug 1: ESC goes back one step
# ---------------------------------------------------------------------------


def test_dispatch_returns_true_for_normal_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan/Debrief/Execute all return True (= exit menu)."""
    for name in ("plan", "debrief", "execute"):
        monkeypatch.setattr(f"src.cli.{name}.callback", lambda **kw: None)

    ctx = _make_ctx()
    for action in ("Debrief", "Plan", "Execute"):
        assert _dispatch(ctx, "ACME", "ACME-1", action) is True, action


def test_dispatch_reset_esc_returns_false(
    monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    """ESC at the reset confirm returns False — caller re-prompts the action."""
    called: list[bool] = []
    monkeypatch.setattr(
        "src.cli.reset.callback", lambda **kw: called.append(True)
    )
    patch_questionary(confirm=None)

    result = _dispatch(_make_ctx(), "ACME", "ACME-1", "Reset")

    assert result is False
    assert called == [], "reset must NOT be invoked when ESC was pressed"


def test_dispatch_reset_confirmed_returns_true(
    monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    monkeypatch.setattr("src.cli.reset.callback", lambda **kw: None)
    patch_questionary(confirm=True)

    assert _dispatch(_make_ctx(), "ACME", "ACME-1", "Reset") is True


def test_dispatch_reset_declined_returns_true(
    monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    """A real `no` answer (not ESC) ends the flow — the user chose, just declined."""
    monkeypatch.setattr(
        "src.cli.reset.callback",
        lambda **kw: pytest.fail("reset must not be invoked when declined"),
    )
    patch_questionary(confirm=False)

    assert _dispatch(_make_ctx(), "ACME", "ACME-1", "Reset") is True


def test_esc_at_action_returns_to_ticket_select(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    """ESC at the action menu re-prompts the ticket select; ESC there exits."""
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({"ACME": {}}))
    monkeypatch.setattr(
        "src.cli.WorktreeManager",
        lambda: _stub_worktree_mgr({"ACME": ["ACME-1"]}),
    )
    # 1. Pick the ticket. 2. ESC at action menu. 3. ESC at ticket select → exit.
    patch_questionary(select=[("ACME", "ACME-1"), None, None])

    for name in ("plan", "debrief", "execute", "reset"):
        monkeypatch.setattr(
            f"src.cli.{name}.callback",
            lambda **kw: pytest.fail(f"{name} should not be called"),
        )

    result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output


def test_esc_at_reset_confirm_re_prompts_action(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    """ESC at the reset confirm re-shows the action menu; user then picks Plan."""
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({"ACME": {}}))
    monkeypatch.setattr(
        "src.cli.WorktreeManager",
        lambda: _stub_worktree_mgr({"ACME": ["ACME-1"]}),
    )
    # Selects: ticket → "Reset" → "Plan" (after ESC at confirm).
    # Confirms: ESC (None) at the first confirm.
    patch_questionary(
        select=[("ACME", "ACME-1"), "Reset", "Plan"],
        confirm=None,
    )

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "src.cli.plan.callback", lambda **kw: captured.update(kw)
    )
    monkeypatch.setattr(
        "src.cli.reset.callback",
        lambda **kw: pytest.fail("reset must not be invoked after ESC"),
    )

    result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert captured.get("ticket_id") == "ACME-1"
    assert captured.get("project") == "ACME"


def test_esc_in_new_ticket_flow_returns_to_ticket_select(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, patch_questionary
) -> None:
    """ESC at the `+ new` text prompt loops back to the ticket select."""
    monkeypatch.setattr("src.cli.get_config", lambda: _stub_config({"ACME": {}}))
    monkeypatch.setattr(
        "src.cli.WorktreeManager",
        lambda: _stub_worktree_mgr({"ACME": ["ACME-1"]}),
    )
    # 1. Pick "+ new". 2. ESC at text prompt. 3. ESC at ticket select → exit.
    patch_questionary(select=["__new__", None], text=None)

    for name in ("plan", "debrief", "execute", "reset"):
        monkeypatch.setattr(
            f"src.cli.{name}.callback",
            lambda **kw: pytest.fail(f"{name} should not be called"),
        )

    result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx() -> Any:
    """Build a Click context wired to the real ``cli`` group.

    ``ctx.invoke`` looks up parameter metadata on the target command, so we
    need a real :class:`click.Context` rooted at the actual group rather
    than a bare ``Mock``.
    """
    import click

    return click.Context(cli)
