"""Tests for the action registry in :mod:`src.tui.actions`.

Covers two things:

1. Registry shape — the discriminator (``kind``/``remote_kind``) is
   load-bearing for the home screen's dispatch. The three remote entries
   must have ``runner is None`` (the regression guard against reverting
   to in-process plan/execute/debrief).
2. Local runners — ``run_validate`` is the representative case. The
   other two (``run_status``, ``run_drain``) go through the same
   ``_run_cli_callback`` helper, so the SystemExit / exception
   translation is shared-fate.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.tui.actions import (
    ACTIONS,
    ActionDef,
    run_drain,
    run_status,
    run_validate,
)


# --------------------------------------------------------------------------- #
# Registry shape
# --------------------------------------------------------------------------- #


def test_registry_has_six_entries_in_expected_order() -> None:
    keys = tuple(a.key for a in ACTIONS)
    assert keys == ("plan", "execute", "debrief", "status", "drain", "validate")
    assert len(ACTIONS) == 6


def test_remote_actions_have_kind_remote_and_matching_remote_kind() -> None:
    by_key = {a.key: a for a in ACTIONS}
    for key in ("plan", "execute", "debrief"):
        a = by_key[key]
        assert isinstance(a, ActionDef)
        assert a.kind == "remote"
        assert a.remote_kind == key, f"{key}: remote_kind must equal key"


def test_local_actions_have_kind_local_and_runner_set() -> None:
    by_key = {a.key: a for a in ACTIONS}
    for key in ("status", "drain", "validate"):
        a = by_key[key]
        assert a.kind == "local"
        assert a.runner is not None, f"{key}: local runner must be set"


def test_remote_actions_have_no_local_runner() -> None:
    """Regression guard — if a future change wires a sync runner back onto
    plan/execute/debrief, the home screen would silently run them
    in-process (the original bug Track 3 exists to fix).
    """
    by_key = {a.key: a for a in ACTIONS}
    for key in ("plan", "execute", "debrief"):
        assert by_key[key].runner is None, (
            f"{key} must not carry a local runner — it is dispatched via the "
            f"service client"
        )


# --------------------------------------------------------------------------- #
# Local runner behaviour
# --------------------------------------------------------------------------- #


@pytest.fixture
def patch_validate_callback(monkeypatch: pytest.MonkeyPatch):
    """Swap the ``validate`` CLI command's callback with a caller-supplied fn.

    Yields a helper that installs a replacement callback.  The underlying
    Click command tree is untouched — only the leaf ``.callback`` is
    replaced for the duration of the test.
    """
    from src import cli as cli_module

    original = cli_module.cli.commands["validate"].callback

    def install(fn) -> None:
        cli_module.cli.commands["validate"].callback = fn

    try:
        yield install
    finally:
        cli_module.cli.commands["validate"].callback = original


def test_local_runner_success_returns_true(patch_validate_callback) -> None:
    def _ok(**_kwargs: Any) -> None:
        return None

    patch_validate_callback(_ok)
    assert run_validate(None, None) is True


def test_local_runner_system_exit_zero_returns_true(patch_validate_callback) -> None:
    def _exit_ok(**_kwargs: Any) -> None:
        raise SystemExit(0)

    patch_validate_callback(_exit_ok)
    assert run_validate(None, None) is True


def test_local_runner_system_exit_one_returns_false(patch_validate_callback) -> None:
    def _exit_err(**_kwargs: Any) -> None:
        raise SystemExit(1)

    patch_validate_callback(_exit_err)
    assert run_validate(None, None) is False


def test_local_runner_exception_returns_false(patch_validate_callback) -> None:
    def _boom(**_kwargs: Any) -> None:
        raise RuntimeError("boom")

    patch_validate_callback(_boom)
    assert run_validate(None, None) is False


def test_local_runner_signatures_are_uniform() -> None:
    """Sanity check: all three local runners accept ``(ticket_id, project)``
    positionally. The dispatcher passes both regardless of what each
    action actually uses.
    """
    # Callable check only — actual invocation is covered by the
    # ``run_validate`` suite; invoking ``run_status`` / ``run_drain`` here
    # would hit live config.
    import inspect

    for fn in (run_validate, run_status, run_drain):
        sig = inspect.signature(fn)
        params = list(sig.parameters)
        assert params[:2] == ["ticket_id", "project"], (
            f"{fn.__name__} signature drift: {params}"
        )
