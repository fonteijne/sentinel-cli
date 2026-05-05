"""Deferred-push retry mechanism for flaky VPN / unreachable GitLab.

When ``sentinel execute`` finishes and tries to push, the VPN may be down or
GitLab may be otherwise unreachable. Rather than leaving the work stranded
in a worktree, the post-execute step writes a small JSON marker file inside
the worktree's ``.git`` directory describing the pending push. A separate
``sentinel push-pending`` command (and an auto-drain at ``execute`` start)
reads those markers and retries.

The marker lives at ``<git-dir>/sentinel-push-pending.json`` — it travels
with the worktree and is never tracked in git.

Known limitation: when push is deferred, the downstream post-execute steps
(MR mark-ready, Jira notify, decision-log comment, Drupal findings) are
intentionally skipped. After a successful drain the user is told those
steps may need a manual follow-up.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

MARKER_FILENAME = "sentinel-push-pending.json"


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass
class PendingMarker:
    """In-memory representation of a marker file."""

    ticket_id: str
    project_key: str
    branch: str
    worktree_path: Path
    commit_sha: str
    first_deferred_at: str
    last_attempt_at: str
    attempts: int
    last_error: str
    last_error_kind: str  # "probe_failed" | "push_failed"
    gitlab_host: str

    def to_json_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["worktree_path"] = str(self.worktree_path)
        return data


@dataclass
class DrainReport:
    """Summary of what ``drain_pending`` did."""

    drained: List[PendingMarker] = field(default_factory=list)
    still_pending: List[PendingMarker] = field(default_factory=list)
    errors: List[Tuple[PendingMarker, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# URL helpers
# --------------------------------------------------------------------------- #


def extract_gitlab_host(git_url: str) -> Optional[str]:
    """Extract the hostname from a git URL (SSH or HTTPS)."""
    if not git_url:
        return None
    try:
        if git_url.startswith("git@"):
            # git@host:group/proj.git
            host_part = git_url.split("@", 1)[1]
            host = host_part.split(":", 1)[0]
            return host or None
        if git_url.startswith(("https://", "http://", "ssh://", "git://")):
            parsed = urlparse(git_url)
            return parsed.hostname
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------- #
# Probe
# --------------------------------------------------------------------------- #


def probe_gitlab_host(host: str, port: int = 443, timeout: float = 3.0) -> bool:
    """TCP-probe a host on ``port``. Returns True iff a connection was made."""
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.error, socket.timeout):
        return False
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Git-dir + marker file IO
# --------------------------------------------------------------------------- #


def get_git_dir(worktree_path: Path) -> Path:
    """Resolve ``.git`` directory for a worktree (handles linked worktrees)."""
    result = subprocess.run(
        ["git", "-C", str(worktree_path), "rev-parse", "--git-dir"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"failed to resolve git-dir for {worktree_path}: "
            f"{(result.stderr or '').strip()}"
        )
    raw = result.stdout.strip()
    git_dir = Path(raw)
    if not git_dir.is_absolute():
        git_dir = (worktree_path / git_dir).resolve()
    return git_dir


def marker_path(worktree_path: Path) -> Path:
    """Return the expected marker file path for ``worktree_path``."""
    return get_git_dir(worktree_path) / MARKER_FILENAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_pending_marker(worktree_path: Path) -> Optional[Dict[str, Any]]:
    """Return the parsed marker dict or ``None`` if missing / malformed."""
    try:
        path = marker_path(worktree_path)
    except RuntimeError:
        return None
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, ValueError):
        return None


def write_pending_marker(
    worktree_path: Path,
    *,
    ticket_id: str,
    project_key: str,
    branch: str,
    commit_sha: str,
    error: str,
    error_kind: str,
    gitlab_host: str,
) -> PendingMarker:
    """Create or update the marker file for ``worktree_path``.

    Preserves ``first_deferred_at`` across updates and increments ``attempts``
    on every call. Write is atomic (tmp + rename).
    """
    path = marker_path(worktree_path)
    now = _now_iso()

    existing = read_pending_marker(worktree_path) or {}
    first_deferred_at = existing.get("first_deferred_at") or now
    try:
        prior_attempts = int(existing.get("attempts", 0))
    except (TypeError, ValueError):
        prior_attempts = 0

    marker = PendingMarker(
        ticket_id=ticket_id,
        project_key=project_key,
        branch=branch,
        worktree_path=Path(worktree_path),
        commit_sha=commit_sha,
        first_deferred_at=first_deferred_at,
        last_attempt_at=now,
        attempts=prior_attempts + 1,
        last_error=error or "",
        last_error_kind=error_kind,
        gitlab_host=gitlab_host or "",
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(marker.to_json_dict(), fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return marker


def clear_pending_marker(worktree_path: Path) -> None:
    """Remove the marker file if present. Idempotent."""
    try:
        path = marker_path(worktree_path)
    except RuntimeError:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("clear_pending_marker: %s: %s", path, exc)


# --------------------------------------------------------------------------- #
# Enumeration + drain
# --------------------------------------------------------------------------- #


def _marker_from_dict(data: Dict[str, Any]) -> Optional[PendingMarker]:
    """Best-effort reconstruct a PendingMarker from a loaded dict."""
    try:
        return PendingMarker(
            ticket_id=str(data.get("ticket_id", "")),
            project_key=str(data.get("project_key", "")),
            branch=str(data.get("branch", "")),
            worktree_path=Path(data.get("worktree_path", "")),
            commit_sha=str(data.get("commit_sha", "")),
            first_deferred_at=str(data.get("first_deferred_at", "")),
            last_attempt_at=str(data.get("last_attempt_at", "")),
            attempts=int(data.get("attempts", 0)),
            last_error=str(data.get("last_error", "")),
            last_error_kind=str(data.get("last_error_kind", "")),
            gitlab_host=str(data.get("gitlab_host", "")),
        )
    except (TypeError, ValueError):
        return None


def enumerate_pending(workspace_root: Path) -> List[PendingMarker]:
    """Walk ``workspace_root`` for ``<project>/<ticket>`` worktrees with markers."""
    markers: List[PendingMarker] = []
    root = Path(workspace_root)
    if not root.exists():
        return markers

    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir():
            continue
        for ticket_dir in sorted(project_dir.iterdir()):
            if not ticket_dir.is_dir():
                continue
            data = read_pending_marker(ticket_dir)
            if data is None:
                continue
            marker = _marker_from_dict(data)
            if marker is None:
                continue
            # Ensure worktree_path reflects the on-disk location even if the
            # marker was written before a rename.
            marker.worktree_path = ticket_dir
            markers.append(marker)
    return markers


def _current_branch(worktree_path: Path) -> Optional[str]:
    res = subprocess.run(
        ["git", "-C", str(worktree_path), "rev-parse", "--abbrev-ref", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def _attempt_push(worktree_path: Path, branch: str) -> Tuple[bool, str]:
    """Run ``git push -u origin <branch>``. Returns (ok, stderr)."""
    res = subprocess.run(
        ["git", "-C", str(worktree_path), "push", "-u", "origin", branch],
        check=False,
        capture_output=True,
        text=True,
    )
    if res.returncode == 0:
        return True, ""
    return False, (res.stderr or res.stdout or "").strip()


def drain_pending(
    workspace_root: Path,
    *,
    logger: logging.Logger = logger,  # type: ignore[assignment]
    quiet: bool = False,
) -> DrainReport:
    """Probe + retry every pending marker under ``workspace_root``."""
    report = DrainReport()
    pending = enumerate_pending(workspace_root)

    for marker in pending:
        wt = marker.worktree_path
        host = marker.gitlab_host
        branch = marker.branch or _current_branch(wt) or ""

        if not host:
            err = "marker missing gitlab_host — cannot probe"
            if not quiet:
                logger.warning("push-pending[%s]: %s", marker.ticket_id, err)
            report.errors.append((marker, err))
            continue

        if not probe_gitlab_host(host):
            err = f"probe failed: {host}:443 unreachable"
            try:
                updated = write_pending_marker(
                    wt,
                    ticket_id=marker.ticket_id,
                    project_key=marker.project_key,
                    branch=branch,
                    commit_sha=marker.commit_sha,
                    error=err,
                    error_kind="probe_failed",
                    gitlab_host=host,
                )
                report.still_pending.append(updated)
            except Exception as exc:  # pragma: no cover - defensive
                report.errors.append((marker, f"marker update failed: {exc}"))
            if not quiet:
                logger.info(
                    "push-pending[%s]: %s (attempts so far: %d)",
                    marker.ticket_id, err, marker.attempts + 1,
                )
            continue

        ok, push_err = _attempt_push(wt, branch) if branch else (
            False, "no branch could be resolved for worktree",
        )
        if ok:
            clear_pending_marker(wt)
            report.drained.append(marker)
            if not quiet:
                logger.info(
                    "push-pending[%s]: pushed %s to origin",
                    marker.ticket_id, branch,
                )
        else:
            try:
                updated = write_pending_marker(
                    wt,
                    ticket_id=marker.ticket_id,
                    project_key=marker.project_key,
                    branch=branch,
                    commit_sha=marker.commit_sha,
                    error=push_err,
                    error_kind="push_failed",
                    gitlab_host=host,
                )
                report.still_pending.append(updated)
            except Exception as exc:  # pragma: no cover - defensive
                report.errors.append((marker, f"marker update failed: {exc}"))
            if not quiet:
                logger.warning(
                    "push-pending[%s]: push failed: %s",
                    marker.ticket_id, push_err,
                )

    return report
