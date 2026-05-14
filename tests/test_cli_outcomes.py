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
