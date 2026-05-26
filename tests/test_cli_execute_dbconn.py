"""Regression test: ``sentinel execute`` must close its SQLite connection.

PR review issue H1 — both control-flow paths inside ``cli.execute`` (the
``--revise`` path and the normal-execute path) open a ``sqlite3.Connection``
via ``connect()`` and never close it. The fix wraps each path's body in
``try / finally: db_conn.close()``.

This module asserts the regression directly: monkeypatch ``src.cli.connect``
to capture the opened conn, invoke the CLI through Click's ``CliRunner`` with
mocked agents/managers, and assert that ``conn.execute(...)`` raises
``sqlite3.ProgrammingError`` after the CLI returns — i.e. the conn is closed.

Mirrored from ``tests/test_cli_outcomes.py`` (db_path / SENTINEL_DB_PATH
fixture style) and ``tests/test_cli_postmortems.py`` (CliRunner pattern).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import src.cli as cli_module
from src.cli import cli
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


@pytest.fixture
def quiet_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip optional env vars that would widen the test surface (bus wiring,
    outcome sync preflight). Keeps the asserted code path minimal.
    """
    for key in ("DEV_VERIFIER_LOOP", "LOOP_C_ENABLED", "OUTCOME_SYNC_ENABLED"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def captured_conns(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[sqlite3.Connection]]:
    """Wrap ``src.cli.connect`` so we capture every Connection it returns.

    The CLI calls ``connect()`` (no args) inside ``execute``; we substitute a
    wrapper that delegates to the real ``connect`` and stashes the returned
    object on a list. Tests then assert ``opened_conns[-1]`` is closed after
    the CLI returns.
    """
    opened: list[sqlite3.Connection] = []
    real_connect = cli_module.connect

    def _capturing_connect(*args, **kwargs) -> sqlite3.Connection:
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr("src.cli.connect", _capturing_connect)
    yield opened


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_closed(conn: sqlite3.Connection) -> None:
    """Assert ``conn`` is closed by triggering ``ProgrammingError`` on use.

    The Python sqlite3 stdlib raises ``ProgrammingError("Cannot operate on a
    closed database.")`` for any operation against a closed Connection. This
    is the canonical way to assert "closed" in pytest.
    """
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def _stub_developer_run_result() -> dict:
    """Successful developer.run() return shape (normal path, iteration 1)."""
    return {
        "tasks_completed": 1,
        "tasks_failed": 0,
        "config_validation": {},
        "regression_errors": [],
    }


def _make_inactive_env_info() -> MagicMock:
    info = MagicMock()
    info.active = False
    info.services = []
    info.tooling = {}
    return info


# ---------------------------------------------------------------------------
# Common patch context
# ---------------------------------------------------------------------------


def _patch_common(
    *,
    worktree_path: Path,
    stack_type: str = "",
):
    """Build a list of patch() context managers shared across all tests.

    Returns a list of context managers; tests `with`-stack them.
    """
    fake_config = MagicMock()
    fake_config.get_project_config.return_value = {
        "stack_type": stack_type,
        "git_url": "",
    }

    fake_env_mgr = MagicMock()
    fake_env_mgr.setup.return_value = _make_inactive_env_info()
    fake_env_mgr.teardown.return_value = True

    fake_worktree_mgr = MagicMock()
    fake_worktree_mgr.create_worktree.return_value = worktree_path

    return [
        patch("src.cli.get_config", return_value=fake_config),
        patch("src.cli.WorktreeManager", return_value=fake_worktree_mgr),
        patch("src.cli.EnvironmentManager", return_value=fake_env_mgr),
    ]


# ---------------------------------------------------------------------------
# 1. Normal path — happy success
# ---------------------------------------------------------------------------


def test_execute_normal_path_closes_db_conn_on_success(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path,
    quiet_env: None,
    captured_conns: list[sqlite3.Connection],
) -> None:
    """Normal execute path with a successful first iteration → conn closed.

    Developer returns approved tasks; security approves; push fails (no
    remote) but that is non-fatal — the function reaches its final ``finally``.
    """
    # Plan file at the worktree path the CLI looks for.
    plan_dir = tmp_path / ".agents" / "plans"
    plan_dir.mkdir(parents=True)
    (plan_dir / "ACME-1.md").write_text("# plan\n")

    fake_dev = MagicMock()
    fake_dev.run.return_value = _stub_developer_run_result()

    fake_security = MagicMock()
    fake_security.run.return_value = {"approved": True, "findings": []}

    with (
        patch("src.cli.PythonDeveloperAgent", return_value=fake_dev),
        patch("src.cli.DrupalDeveloperAgent", return_value=fake_dev),
        patch("src.cli.SecurityReviewerAgent", return_value=fake_security),
        patch("src.cli.get_jira_client", return_value=MagicMock()),
    ):
        for cm in _patch_common(worktree_path=tmp_path):
            cm.__enter__()
        try:
            result = runner.invoke(
                cli,
                ["execute", "ACME-1", "--no-env", "--max-iterations", "1"],
                catch_exceptions=False,
            )
        finally:
            # noqa: best-effort cleanup; patch context managers are not strictly
            # required to be exited in this test (the process is short-lived).
            pass

    assert captured_conns, (
        f"connect() never called; output={result.output!r}"
    )
    _assert_closed(captured_conns[-1])


# ---------------------------------------------------------------------------
# 2. Normal path — developer raises; outer except catches; conn still closed
# ---------------------------------------------------------------------------


def test_execute_normal_path_closes_db_conn_on_failure(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path,
    quiet_env: None,
    captured_conns: list[sqlite3.Connection],
) -> None:
    """Developer raises a non-CappedOut exception → outer except catches and
    sys.exit(1)s. The new ``finally: db_conn.close()`` runs *before* the
    outer except handler, so the conn must be closed.
    """
    plan_dir = tmp_path / ".agents" / "plans"
    plan_dir.mkdir(parents=True)
    (plan_dir / "ACME-2.md").write_text("# plan\n")

    fake_dev = MagicMock()
    fake_dev.run.side_effect = RuntimeError("boom")

    with (
        patch("src.cli.PythonDeveloperAgent", return_value=fake_dev),
        patch("src.cli.DrupalDeveloperAgent", return_value=fake_dev),
        patch("src.cli.SecurityReviewerAgent", return_value=MagicMock()),
    ):
        for cm in _patch_common(worktree_path=tmp_path):
            cm.__enter__()
        result = runner.invoke(
            cli,
            ["execute", "ACME-2", "--no-env", "--max-iterations", "1"],
        )

    assert captured_conns, (
        f"connect() never called; output={result.output!r}"
    )
    _assert_closed(captured_conns[-1])


# ---------------------------------------------------------------------------
# 3. Revise path — early return (zero unresolved discussions)
# ---------------------------------------------------------------------------


def test_execute_revise_path_closes_db_conn_on_success(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path,
    quiet_env: None,
    captured_conns: list[sqlite3.Connection],
) -> None:
    """``--revise`` with zero unresolved feedback → early ``return`` from
    inside the new try block. The new ``finally: db_conn.close()`` must
    still run on a normal return.
    """
    fake_dev = MagicMock()
    fake_dev.run_revision.return_value = {"feedback_count": 0}

    with (
        patch("src.cli.PythonDeveloperAgent", return_value=fake_dev),
        patch("src.cli.DrupalDeveloperAgent", return_value=fake_dev),
    ):
        for cm in _patch_common(worktree_path=tmp_path):
            cm.__enter__()
        result = runner.invoke(
            cli,
            ["execute", "ACME-3", "--revise", "--no-env"],
            catch_exceptions=False,
        )

    assert captured_conns, (
        f"connect() never called; output={result.output!r}"
    )
    _assert_closed(captured_conns[-1])


# ---------------------------------------------------------------------------
# 4. Revise path — DeveloperCappedOutException → sys.exit(1); conn closed
# ---------------------------------------------------------------------------


def test_execute_revise_path_closes_db_conn_on_developer_capped_out(
    runner: CliRunner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path,
    quiet_env: None,
    captured_conns: list[sqlite3.Connection],
) -> None:
    """``--revise`` + developer raises ``DeveloperCappedOutException`` →
    inner ``except`` calls ``sys.exit(1)``. Python guarantees ``finally``
    runs during ``SystemExit`` unwind, so the conn must be closed.
    """
    from src.agents.base_developer import DeveloperCappedOutException

    fake_dev = MagicMock()
    fake_dev.run_revision.side_effect = DeveloperCappedOutException("capped")

    with (
        patch("src.cli.PythonDeveloperAgent", return_value=fake_dev),
        patch("src.cli.DrupalDeveloperAgent", return_value=fake_dev),
    ):
        for cm in _patch_common(worktree_path=tmp_path):
            cm.__enter__()
        result = runner.invoke(
            cli,
            ["execute", "ACME-4", "--revise", "--no-env"],
        )

    assert captured_conns, (
        f"connect() never called; output={result.output!r}"
    )
    _assert_closed(captured_conns[-1])
