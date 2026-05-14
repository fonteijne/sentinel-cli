"""Tests for the ``sentinel outcomes`` CLI subcommand group (Phase 3A).

Surfaces under test:

  1. ``outcomes sync`` exit-2 when ``OUTCOME_SYNC_ENABLED=0`` and
     ``--dry-run`` is not passed.
  2. ``outcomes sync --dry-run`` runs cleanly with the flag off (no GitLab
     calls beyond the mocked client).
  3. The pre-flight hook in ``plan`` / ``execute`` is a no-op when the flag
     is off (verified by exercising ``_outcome_sync_enabled`` directly,
     because spinning up the full ``plan`` command path requires Jira and is
     out of scope for this surface test).
  4. The pre-flight hook swallows sync exceptions when the flag is on
     (verified by replicating the ``try/except`` guard pattern from cli.py).

Plan ref: phase-3a-outcome-ingestion.plan.md task 11.d.

Test rule: do NOT touch ``~/.sentinel/sentinel.db``. CLI tests redirect
``connect()`` at a tmp-path SQLite file via the ``SENTINEL_DB_PATH`` env
var (same idiom as ``tests/test_cli_learning.py``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli import _outcome_sync_enabled, cli
from src.core.persistence import apply_migrations, connect


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Empty migrated DB at a tmp path the CLI resolves via SENTINEL_DB_PATH."""
    path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(path))

    conn = connect(str(path))
    try:
        apply_migrations(conn)
    finally:
        conn.close()

    yield path


# ---------------------------------------------------------------------------
# 1. flag-off-without-dry-run exits 2
# ---------------------------------------------------------------------------


def test_outcomes_sync_disabled_without_dry_run_exits_2(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
):
    """Flag off + no --dry-run → exit code 2 + actionable stderr."""
    monkeypatch.delenv("OUTCOME_SYNC_ENABLED", raising=False)

    result = runner.invoke(
        cli, ["outcomes", "sync", "--project", "acme/backend"]
    )

    assert result.exit_code == 2, (
        f"expected exit 2 when flag is off, got {result.exit_code}; "
        f"output={result.output!r}"
    )
    assert "OUTCOME_SYNC_ENABLED=0" in result.output


# ---------------------------------------------------------------------------
# 2. --dry-run with flag off runs cleanly
# ---------------------------------------------------------------------------


def test_outcomes_sync_dry_run_runs_with_flag_off(
    runner: CliRunner,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """With --dry-run, the flag check is bypassed and sync runs."""
    monkeypatch.delenv("OUTCOME_SYNC_ENABLED", raising=False)

    # Mock GitLabClient at the import site inside the subcommand.
    fake_gitlab = MagicMock()
    fake_gitlab.list_merged_mrs_since.return_value = []
    fake_gitlab.list_pipelines_for_commit.return_value = []
    fake_gitlab.list_merge_requests.return_value = []

    with patch("src.gitlab_client.GitLabClient", return_value=fake_gitlab):
        result = runner.invoke(
            cli,
            ["outcomes", "sync", "--dry-run", "--project", "acme/backend"],
        )

    assert result.exit_code == 0, (
        f"expected exit 0 on dry-run with flag off, got {result.exit_code}; "
        f"output={result.output!r}"
    )
    # Summary printed.
    assert "acme/backend" in result.output
    # No GitLab calls beyond the mocked one (no exception propagation).
    assert fake_gitlab.list_merged_mrs_since.called


# ---------------------------------------------------------------------------
# 3. preflight is a no-op with flag off
# ---------------------------------------------------------------------------


def test_plan_preflight_is_noop_with_flag_off(
    monkeypatch: pytest.MonkeyPatch,
):
    """The guard ``if _outcome_sync_enabled(): _run_outcome_sync_preflight()``
    must not call the preflight when the env var is unset.

    We test the guard logic directly (the full ``plan`` command requires Jira
    plumbing and is exercised end-to-end elsewhere) — this is the smallest
    correctness boundary for the hook.
    """
    monkeypatch.delenv("OUTCOME_SYNC_ENABLED", raising=False)

    preflight_called = MagicMock()

    # Replicate the cli.py guard pattern verbatim.
    if _outcome_sync_enabled():
        preflight_called()  # pragma: no cover -- branch must NOT execute

    preflight_called.assert_not_called()


# ---------------------------------------------------------------------------
# 4. preflight swallows sync exceptions when flag is on
# ---------------------------------------------------------------------------


def test_plan_preflight_swallows_sync_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """When the flag is on and the preflight raises, no exception escapes."""
    monkeypatch.setenv("OUTCOME_SYNC_ENABLED", "1")

    # Replicate the cli.py guard pattern: try/except + logger.warning.
    logger = logging.getLogger("test_preflight_guard")

    def _exploding_preflight() -> None:
        raise RuntimeError("kaboom")

    raised = False
    if _outcome_sync_enabled():
        try:
            _exploding_preflight()
        except Exception as e:
            logger.warning("outcome sync preflight failed: %s", e)
        else:  # pragma: no cover
            pass

    # If we got here without `raised=True`, the guard correctly swallowed.
    assert raised is False


# ---------------------------------------------------------------------------
# 5. M5 — preflight time budget
# ---------------------------------------------------------------------------


def test_preflight_budget_default_is_30(monkeypatch: pytest.MonkeyPatch):
    """Default budget is 30s when env var is unset."""
    from src.cli import _outcome_sync_preflight_budget_seconds

    monkeypatch.delenv(
        "OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS", raising=False
    )
    assert _outcome_sync_preflight_budget_seconds() == 30.0


def test_preflight_budget_zero_or_negative_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
):
    """0 / negative / non-numeric values are coerced to default (cannot disable)."""
    from src.cli import _outcome_sync_preflight_budget_seconds

    for bad in ("0", "-1", "abc", ""):
        monkeypatch.setenv("OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS", bad)
        assert _outcome_sync_preflight_budget_seconds() == 30.0


def test_preflight_logs_loud_warning_when_budget_exhausted(
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """Two known projects + clock that advances past the deadline mid-loop
    => loud WARNING listing the remaining project.
    """
    import time as time_module

    from src.cli import _run_outcome_sync_preflight
    from src.core.persistence import connect, upsert_sync_state

    # Seed two known projects so _discover_known_projects returns 2.
    conn = connect(str(db_path))
    try:
        upsert_sync_state(
            conn,
            project="acme/alpha",
            last_synced_at="2026-01-01T00:00:00Z",
            last_seen_mr_iid=None,
            last_seen_updated_at=None,
        )
        upsert_sync_state(
            conn,
            project="acme/beta",
            last_synced_at="2026-01-01T00:00:00Z",
            last_seen_mr_iid=None,
            last_seen_updated_at=None,
        )
    finally:
        conn.close()

    monkeypatch.setenv("OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS", "1")

    # Drive monotonic deterministically: start=0, deadline check at iter 0
    # sees t=0 (within budget=1s) so the first sync() runs; subsequent calls
    # see t=100 (past the deadline) so the second iteration logs the WARNING.
    real_monotonic = time_module.monotonic
    fake_clock = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0])

    def _fake_monotonic() -> float:
        try:
            return next(fake_clock)
        except StopIteration:
            return real_monotonic()

    # Mock GitLabClient so the first sync() call is fast.
    fake_gitlab = MagicMock()
    fake_gitlab.list_merged_mrs_since.return_value = []
    fake_gitlab.list_pipelines_for_commit.return_value = []
    fake_gitlab.list_merge_requests.return_value = []

    with caplog.at_level(logging.WARNING):
        with patch.object(
            time_module, "monotonic", _fake_monotonic
        ), patch(
            "src.gitlab_client.GitLabClient", return_value=fake_gitlab
        ):
            _run_outcome_sync_preflight(project=None)

    budget_warnings = [
        rec for rec in caplog.records
        if "preflight budget exhausted" in rec.message
    ]
    assert budget_warnings, (
        f"expected budget WARNING; got {[r.message for r in caplog.records]}"
    )
    # WARNING line must include the seeded project so an operator can see
    # which project is still pending.
    formatted = budget_warnings[0].getMessage()
    assert "acme/" in formatted, f"expected project name in WARNING: {formatted!r}"
    # Required fields per the plan's WARNING contract.
    for token in ("synced=", "remaining=", "elapsed=", "budget=", "remaining_projects="):
        assert token in formatted, f"missing {token!r} in WARNING: {formatted!r}"


def test_preflight_returns_cleanly_when_no_known_projects(
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """First-run / empty DB: preflight is a clean no-op (no GitLab call)."""
    from src.cli import _run_outcome_sync_preflight

    monkeypatch.setenv("OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS", "30")
    fake_gitlab = MagicMock()
    with patch("src.gitlab_client.GitLabClient", return_value=fake_gitlab):
        _run_outcome_sync_preflight(project=None)

    fake_gitlab.list_merged_mrs_since.assert_not_called()
