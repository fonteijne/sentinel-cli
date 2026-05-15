"""Integration tests for ``sentinel reset`` volume cleanup.

These tests exercise ``_teardown_containers`` end-to-end through the
Click CLI, verifying that the per-ticket Docker volumes (``sentinel-projects-<slug>``
and ``<project>_db-data``) are removed regardless of whether a compose
file is present, and that the path is idempotent when nothing exists.

Pattern source: tests/test_cli_postmortems.py (CliRunner fixture) and
tests/test_compose_runner.py (subprocess mock).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.cli import cli
from src.compose_runner import ComposeResult


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _stub_worktree_mgr(
    *,
    worktree_path: Path | None,
    worktree_removed: bool = True,
    branch_deleted: bool = True,
) -> MagicMock:
    """Build a MagicMock standing in for a ``WorktreeManager`` instance."""
    mgr = MagicMock()
    mgr.get_worktree_path.return_value = worktree_path
    mgr.reset_ticket.return_value = {
        "worktree_removed": worktree_removed,
        "branch_deleted": branch_deleted,
    }
    return mgr


def test_reset_ticket_removes_volumes(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Compose file present → compose down ran, both volumes removed and reported."""
    # Worktree with a generated compose file (so the compose-down branch fires).
    worktree = tmp_path / "DHLEXC-311"
    worktree.mkdir()
    (worktree / "docker-compose.sentinel.yml").write_text("services: {}\n")

    mgr = _stub_worktree_mgr(worktree_path=worktree)
    monkeypatch.setattr("src.cli.WorktreeManager", lambda: mgr)

    compose_runner = MagicMock()
    compose_runner.down.return_value = ComposeResult(success=True)
    monkeypatch.setattr(
        "src.compose_runner.ComposeRunner", lambda **_: compose_runner
    )

    with patch("src.environment_manager.subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        result = runner.invoke(
            cli, ["reset", "DHLEXC-311", "--yes"], catch_exceptions=False
        )

    assert result.exit_code == 0, result.output
    assert "Containers stopped and removed" in result.output
    assert "Removed volume sentinel-projects-dhlexc-311" in result.output
    assert "Removed volume sentinel-dhlexc-311_db-data" in result.output
    # The confirmation block should now mention volumes too.
    assert "Docker volumes for DHLEXC-311" in result.output


def test_reset_ticket_idempotent_when_volumes_absent(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Re-running reset on a ticket whose volumes are gone → no traceback, info line."""
    worktree = tmp_path / "DHLEXC-311"
    worktree.mkdir()
    (worktree / "docker-compose.sentinel.yml").write_text("services: {}\n")

    mgr = _stub_worktree_mgr(worktree_path=worktree)
    monkeypatch.setattr("src.cli.WorktreeManager", lambda: mgr)

    compose_runner = MagicMock()
    compose_runner.down.return_value = ComposeResult(success=True)
    monkeypatch.setattr(
        "src.compose_runner.ComposeRunner", lambda **_: compose_runner
    )

    with patch("src.environment_manager.subprocess.run") as mock_run:
        mock_run.side_effect = [
            MagicMock(
                returncode=1,
                stdout="",
                stderr="Error: No such volume: sentinel-projects-dhlexc-311",
            ),
            MagicMock(
                returncode=1,
                stdout="",
                stderr="Error: No such volume: sentinel-dhlexc-311_db-data",
            ),
        ]
        result = runner.invoke(
            cli, ["reset", "DHLEXC-311", "--yes"], catch_exceptions=False
        )

    assert result.exit_code == 0, result.output
    assert "No volumes to remove" in result.output
    assert "Traceback" not in result.output
    assert "❌ Error" not in result.output


def test_reset_ticket_no_worktree_no_volumes(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No worktree (ticket never executed) → fallback orphan cleanup + idempotent volume call."""
    mgr = _stub_worktree_mgr(
        worktree_path=None, worktree_removed=False, branch_deleted=False
    )
    monkeypatch.setattr("src.cli.WorktreeManager", lambda: mgr)

    compose_runner = MagicMock()
    compose_runner.cleanup_orphans.return_value = ComposeResult(success=True)
    monkeypatch.setattr(
        "src.compose_runner.ComposeRunner", lambda **_: compose_runner
    )

    with patch("src.environment_manager.subprocess.run") as mock_run:
        # Both volumes absent.
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="Error: No such volume"),
            MagicMock(returncode=1, stdout="", stderr="Error: No such volume"),
        ]
        result = runner.invoke(
            cli, ["reset", "NEVER-RAN-999", "--yes"], catch_exceptions=False
        )

    assert result.exit_code == 0, result.output
    assert "No active containers found" in result.output
    assert "No volumes to remove" in result.output
    assert "Traceback" not in result.output
    # Volume cleanup *was* attempted — fallthrough is the whole point.
    assert mock_run.call_count == 2
