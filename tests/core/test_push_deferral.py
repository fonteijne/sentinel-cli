"""Tests for the deferred-push-retry module."""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.execution import push_deferral as pd


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _init_worktree(path: Path) -> Path:
    """Create a minimal git repo so ``get_git_dir`` works."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"], cwd=path, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "t"], cwd=path, check=True
    )
    return path


# --------------------------------------------------------------------------- #
# probe_gitlab_host
# --------------------------------------------------------------------------- #


def test_probe_gitlab_host_happy_path():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        assert pd.probe_gitlab_host("127.0.0.1", port=port, timeout=2.0) is True
    finally:
        srv.close()


def test_probe_gitlab_host_connection_refused():
    # 127.0.0.1 with almost-certainly-closed ephemeral port.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert pd.probe_gitlab_host("127.0.0.1", port=port, timeout=1.0) is False


def test_probe_gitlab_host_timeout(monkeypatch):
    def _boom(addr, timeout=None):
        raise socket.timeout("simulated")

    monkeypatch.setattr(pd.socket, "create_connection", _boom)
    assert pd.probe_gitlab_host("example.com", timeout=0.5) is False


def test_probe_gitlab_host_empty_host():
    assert pd.probe_gitlab_host("") is False


# --------------------------------------------------------------------------- #
# extract_gitlab_host
# --------------------------------------------------------------------------- #


def test_extract_gitlab_host_ssh():
    assert pd.extract_gitlab_host(
        "git@gitlab.example.com:acme/backend.git"
    ) == "gitlab.example.com"


def test_extract_gitlab_host_https():
    assert pd.extract_gitlab_host(
        "https://gitlab.example.com/acme/backend.git"
    ) == "gitlab.example.com"


def test_extract_gitlab_host_ssh_proto():
    assert pd.extract_gitlab_host(
        "ssh://git@gitlab.example.com:22/acme/backend.git"
    ) == "gitlab.example.com"


def test_extract_gitlab_host_malformed():
    assert pd.extract_gitlab_host("not a url") is None
    assert pd.extract_gitlab_host("") is None


# --------------------------------------------------------------------------- #
# Marker IO
# --------------------------------------------------------------------------- #


def test_write_read_clear_marker_roundtrip(tmp_path):
    wt = _init_worktree(tmp_path / "wt")

    marker = pd.write_pending_marker(
        wt,
        ticket_id="ACME-1",
        project_key="acme",
        branch="sentinel/feature/ACME-1",
        commit_sha="deadbeef",
        error="probe failed",
        error_kind="probe_failed",
        gitlab_host="gitlab.example.com",
    )
    assert marker.attempts == 1
    assert marker.first_deferred_at
    assert marker.first_deferred_at == marker.last_attempt_at

    data = pd.read_pending_marker(wt)
    assert data is not None
    assert data["ticket_id"] == "ACME-1"
    assert data["attempts"] == 1
    assert data["last_error_kind"] == "probe_failed"

    pd.clear_pending_marker(wt)
    assert pd.read_pending_marker(wt) is None

    # Idempotent clear.
    pd.clear_pending_marker(wt)
    assert pd.read_pending_marker(wt) is None


def test_write_marker_second_call_increments_attempts(tmp_path):
    wt = _init_worktree(tmp_path / "wt")

    first = pd.write_pending_marker(
        wt, ticket_id="T-1", project_key="t", branch="b",
        commit_sha="c", error="e1", error_kind="probe_failed",
        gitlab_host="h",
    )
    second = pd.write_pending_marker(
        wt, ticket_id="T-1", project_key="t", branch="b",
        commit_sha="c", error="e2", error_kind="push_failed",
        gitlab_host="h",
    )
    assert second.attempts == first.attempts + 1
    assert second.first_deferred_at == first.first_deferred_at
    assert second.last_error == "e2"
    assert second.last_error_kind == "push_failed"


def test_read_pending_marker_malformed_returns_none(tmp_path):
    wt = _init_worktree(tmp_path / "wt")
    path = pd.marker_path(wt)
    path.write_text("{not json")
    assert pd.read_pending_marker(wt) is None


def test_marker_written_inside_git_dir(tmp_path):
    wt = _init_worktree(tmp_path / "wt")
    pd.write_pending_marker(
        wt, ticket_id="T-1", project_key="t", branch="b",
        commit_sha="c", error="e", error_kind="probe_failed",
        gitlab_host="h",
    )
    assert (wt / ".git" / pd.MARKER_FILENAME).exists()


# --------------------------------------------------------------------------- #
# enumerate_pending
# --------------------------------------------------------------------------- #


def test_enumerate_pending_walks_worktree_tree(tmp_path):
    workspace = tmp_path / "ws"
    proj = workspace / "acme"
    wt1 = _init_worktree(proj / "ACME-1")
    wt2 = _init_worktree(proj / "ACME-2")
    # no marker on wt2 — should be skipped

    pd.write_pending_marker(
        wt1, ticket_id="ACME-1", project_key="acme",
        branch="sentinel/feature/ACME-1", commit_sha="c1",
        error="probe failed", error_kind="probe_failed",
        gitlab_host="gitlab.example.com",
    )

    found = pd.enumerate_pending(workspace)
    assert len(found) == 1
    assert found[0].ticket_id == "ACME-1"
    assert found[0].worktree_path == wt1
    # unused wt2 has no marker
    assert wt2.exists()


def test_enumerate_pending_missing_root_returns_empty(tmp_path):
    assert pd.enumerate_pending(tmp_path / "does_not_exist") == []


# --------------------------------------------------------------------------- #
# drain_pending
# --------------------------------------------------------------------------- #


def test_drain_probe_fails_bumps_attempts(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    wt = _init_worktree(workspace / "acme" / "ACME-1")
    pd.write_pending_marker(
        wt, ticket_id="ACME-1", project_key="acme",
        branch="sentinel/feature/ACME-1", commit_sha="c1",
        error="original", error_kind="probe_failed",
        gitlab_host="gitlab.example.com",
    )

    monkeypatch.setattr(pd, "probe_gitlab_host", lambda *a, **k: False)

    report = pd.drain_pending(workspace, logger=MagicMock(), quiet=True)
    assert not report.drained
    assert len(report.still_pending) == 1
    updated = report.still_pending[0]
    assert updated.attempts == 2
    assert updated.last_error_kind == "probe_failed"

    # Marker file still present.
    assert pd.read_pending_marker(wt) is not None


def test_drain_push_success_clears_marker(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    wt = _init_worktree(workspace / "acme" / "ACME-1")
    pd.write_pending_marker(
        wt, ticket_id="ACME-1", project_key="acme",
        branch="sentinel/feature/ACME-1", commit_sha="c1",
        error="x", error_kind="push_failed",
        gitlab_host="gitlab.example.com",
    )

    monkeypatch.setattr(pd, "probe_gitlab_host", lambda *a, **k: True)
    monkeypatch.setattr(pd, "_attempt_push", lambda wt, branch: (True, ""))

    report = pd.drain_pending(workspace, logger=MagicMock(), quiet=True)
    assert len(report.drained) == 1
    assert report.drained[0].ticket_id == "ACME-1"
    assert not report.still_pending
    assert pd.read_pending_marker(wt) is None


def test_drain_push_fails_updates_marker(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    wt = _init_worktree(workspace / "acme" / "ACME-1")
    pd.write_pending_marker(
        wt, ticket_id="ACME-1", project_key="acme",
        branch="sentinel/feature/ACME-1", commit_sha="c1",
        error="x", error_kind="probe_failed",
        gitlab_host="gitlab.example.com",
    )

    monkeypatch.setattr(pd, "probe_gitlab_host", lambda *a, **k: True)
    monkeypatch.setattr(
        pd, "_attempt_push", lambda wt, branch: (False, "rejected: auth")
    )

    report = pd.drain_pending(workspace, logger=MagicMock(), quiet=True)
    assert not report.drained
    assert len(report.still_pending) == 1
    updated = report.still_pending[0]
    assert updated.last_error_kind == "push_failed"
    assert "auth" in updated.last_error
    assert updated.attempts == 2


def test_drain_missing_host_recorded_as_error(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    wt = _init_worktree(workspace / "acme" / "ACME-1")
    # Write marker manually with empty host.
    path = pd.marker_path(wt)
    path.write_text(json.dumps({
        "ticket_id": "ACME-1",
        "project_key": "acme",
        "branch": "b",
        "worktree_path": str(wt),
        "commit_sha": "c",
        "first_deferred_at": "2026-01-01T00:00:00Z",
        "last_attempt_at": "2026-01-01T00:00:00Z",
        "attempts": 1,
        "last_error": "x",
        "last_error_kind": "push_failed",
        "gitlab_host": "",
    }))

    report = pd.drain_pending(workspace, logger=MagicMock(), quiet=True)
    assert not report.drained
    assert not report.still_pending
    assert len(report.errors) == 1
