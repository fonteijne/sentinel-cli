"""Service discovery file contract for the Command Center.

Track 1 (interactive-TUI): `sentinel i` and `sentinel serve` share a tiny
on-disk rendezvous so the TUI can auto-attach to a running service or spawn
one transparently. This module owns the file format, the atomic-write
discipline, the single-instance FLOCK, and a ``pid_alive`` liveness probe.

Layout:

* **Discovery file** — ``$XDG_STATE_HOME/sentinel/service.json`` (fallback
  ``~/.local/state/sentinel/service.json``), mode ``0o600``. Contains
  ``{pid, port, token, started_at, version}`` as JSON.
* **Lock file** — sibling ``service.lock``. FLOCK-based, never unlinked on
  release (unlinking under an active fd would silently let a fresh inode
  replace ours, making concurrent lockers both "succeed").

Atomic write strategy differs intentionally from ``auth.load_or_create_token``:

* The token file uses ``os.link`` (create-or-lose) because the winning token
  must never be overwritten — two services would disagree on which token is
  valid.
* The discovery file uses ``os.replace`` (overwrite) because a new service
  booting SHOULD clobber a stale discovery record. Single-instance
  correctness is enforced by ``discovery_lock`` (FLOCK), not by filesystem
  link semantics. Please do not "fix" this to ``os.link`` — you would break
  legitimate respawns after ``SIGKILL`` + leftover file.

This module is deliberately standalone: no FastAPI, no config_loader, no
auth imports. Both the service process and the TUI preamble import it and
neither should pull in heavy service state to do so.
"""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import secrets
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

_DISCOVERY_FILENAME = "service.json"
_LOCK_FILENAME = "service.lock"
_LOCK_POLL_INTERVAL_S = 0.05


@dataclass(frozen=True)
class ServiceDiscovery:
    """Parsed contents of the service discovery file."""

    pid: int
    port: int
    token: str
    started_at: str  # ISO-8601 UTC, e.g. "2026-05-01T14:32:11Z"
    version: str


def _state_dir() -> Path:
    """Resolve the parent directory for the discovery + lock files.

    Honours ``$XDG_STATE_HOME`` when set and non-empty; otherwise falls back
    to ``~/.local/state``. Always appends ``sentinel/``.
    """

    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        base = Path(xdg)
    else:
        base = Path.home() / ".local" / "state"
    return base / "sentinel"


def discovery_path() -> Path:
    """Absolute path to the discovery JSON file."""

    return _state_dir() / _DISCOVERY_FILENAME


def lock_path() -> Path:
    """Absolute path to the FLOCK sidecar for single-instance guard."""

    return _state_dir() / _LOCK_FILENAME


def _package_version() -> str:
    """Resolve the installed ``sentinel`` version, or ``"0"`` if unknown.

    ``importlib.metadata`` can raise ``PackageNotFoundError`` when the
    package isn't installed as a distribution (e.g. running from a plain
    source checkout). We return ``"0"`` rather than bubbling up — the
    discovery file is a best-effort hint, not a security control.
    """

    try:
        return importlib_metadata.version("sentinel")
    except importlib_metadata.PackageNotFoundError:
        return "0"
    except Exception:  # pragma: no cover — defensive fallback
        return "0"


def _utc_now_iso() -> str:
    """Current UTC time as ``YYYY-MM-DDTHH:MM:SSZ`` (no microseconds)."""

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def write_discovery(
    *,
    port: int,
    token: str,
    pid: int | None = None,
) -> ServiceDiscovery:
    """Atomically write the discovery file. Returns the written record.

    The caller is expected to hold ``discovery_lock`` when writing to
    enforce single-instance semantics, but this function does not take the
    lock itself — the lock is orthogonal to the atomic-write discipline.

    Write strategy: create ``service.json.tmp.<pid>.<hex>`` with mode
    ``0o600``, fsync the fd, then ``os.replace`` onto the final path.
    ``os.replace`` is atomic on POSIX and DOES overwrite an existing
    target, which is what we want: a newly-booted service supersedes any
    stale record from a dead predecessor.
    """

    state_dir = _state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)

    record = ServiceDiscovery(
        pid=int(pid if pid is not None else os.getpid()),
        port=int(port),
        token=token,
        started_at=_utc_now_iso(),
        version=_package_version(),
    )
    payload = json.dumps(asdict(record), sort_keys=True).encode("ascii")

    final = discovery_path()
    tmp = final.with_suffix(
        f".json.tmp.{os.getpid()}.{secrets.token_hex(4)}"
    )

    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)

    try:
        os.replace(tmp, final)
    except Exception:
        # Best-effort cleanup of the tmp file if replace bombed.
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise

    logger.info(
        "wrote service discovery file at %s (pid=%d port=%d)",
        final,
        record.pid,
        record.port,
    )
    return record


def remove_discovery() -> None:
    """Best-effort removal of the discovery file. Never raises on missing."""

    try:
        os.unlink(discovery_path())
    except FileNotFoundError:
        return
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("remove_discovery ignored error: %s", exc)


def read_discovery() -> ServiceDiscovery | None:
    """Return the parsed discovery record, or ``None`` if unusable.

    Tolerates: missing file, truncated/corrupt JSON, missing required keys,
    wrong value types. Every failure path returns ``None`` and logs at
    ``debug`` level — callers treat "no file" and "bad file" identically.
    """

    path = discovery_path()
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.debug("read_discovery: OS error reading %s: %s", path, exc)
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.debug("read_discovery: invalid JSON at %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        logger.debug("read_discovery: top-level not an object at %s", path)
        return None

    try:
        pid = data["pid"]
        port = data["port"]
        token = data["token"]
        started_at = data["started_at"]
        version = data["version"]
    except KeyError as exc:
        logger.debug("read_discovery: missing key %s in %s", exc, path)
        return None

    if not isinstance(pid, int) or not isinstance(port, int):
        logger.debug("read_discovery: pid/port not int in %s", path)
        return None
    if not isinstance(token, str) or not isinstance(started_at, str):
        logger.debug("read_discovery: token/started_at not str in %s", path)
        return None
    if not isinstance(version, str):
        logger.debug("read_discovery: version not str in %s", path)
        return None

    return ServiceDiscovery(
        pid=pid,
        port=port,
        token=token,
        started_at=started_at,
        version=version,
    )


def pid_alive(pid: int) -> bool:
    """Return True if ``pid`` identifies a live process on this host.

    ``os.kill(pid, 0)`` sends no signal but performs the permission/existence
    check that signal delivery would. Mapping:

    * ``ProcessLookupError`` (ESRCH) → process does not exist → False.
    * ``PermissionError`` (EPERM) → process exists but we may not signal it
      (different uid, e.g. root-owned leftover) → True. Good enough for a
      liveness hint; we're not about to ``kill -9`` it.
    * Any other exception → False (defensive; callers treat it as stale).
    """

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        # Extremely rare: EINVAL on a bogus signal, etc. Treat as stale.
        logger.debug("pid_alive: unexpected OSError for pid=%d: %s", pid, exc)
        return False
    return True


@contextmanager
def discovery_lock(*, timeout_s: float = 5.0) -> Iterator[int]:
    """Acquire the single-instance FLOCK; yield the held fd.

    Uses ``fcntl.flock(LOCK_EX | LOCK_NB)`` in a polling loop (~50ms) until
    it acquires or the deadline expires. The fd is yielded so tests can
    assert the handle type; callers rarely need it.

    Release discipline:

    * ``flock(LOCK_UN)`` then ``os.close(fd)``. The lock file itself is
      NEVER unlinked. A concurrent locker already holding its own fd on the
      same inode would silently "succeed" on a new inode if we unlinked —
      breaking mutual exclusion. Leaving a zero-byte ``service.lock`` on
      disk is fine and expected.

    Raises ``TimeoutError`` if the lock can't be acquired within
    ``timeout_s`` seconds; the error message includes the lock path to help
    operators figure out which process is holding it.
    """

    state_dir = _state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    path = lock_path()

    # O_CREAT | O_RDWR so we create the file if missing and keep it for
    # future lockers. Mode 0o600 for the same reason as the discovery file:
    # no other user needs to see or contend on this lock.
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    deadline = time.monotonic() + max(0.0, timeout_s)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                # Held by someone else; fall through to the wait/poll.
                pass
            except OSError as exc:
                # EWOULDBLOCK is sometimes reported as OSError on exotic
                # filesystems. Treat it like BlockingIOError.
                if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    raise
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"could not acquire {path} within {timeout_s}s"
                )
            time.sleep(_LOCK_POLL_INTERVAL_S)

        yield fd
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError as exc:  # pragma: no cover — defensive
                logger.debug("discovery_lock: LOCK_UN failed: %s", exc)
        try:
            os.close(fd)
        except OSError as exc:  # pragma: no cover — defensive
            logger.debug("discovery_lock: close failed: %s", exc)
