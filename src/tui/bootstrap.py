"""Auto-launch preamble for the Sentinel TUI.

Track 1 of the interactive-TUI plan: the user runs ``sentinel i`` and never
thinks about the Command Center service. This module is the glue that either
attaches to a running service (via the discovery file) or spawns one
transparently.

Algorithm (see ``ensure_service``):

1. Acquire the discovery FLOCK with a short deadline — if another TUI is
   mid-spawn, we propagate the TimeoutError rather than race.
2. Under the lock, read the discovery file. If it points at a live pid AND
   ``GET /health`` answers 200, we're done — release the lock, return a
   ServiceHandle with ``spawned=False``.
3. Otherwise the record is stale or absent. Unlink it (best-effort) and
   spawn ``sentinel serve --port 0`` detached via ``start_new_session=True``.
4. Still holding the lock, poll ``read_discovery`` until we see a new record
   that passes health, or the overall deadline expires.
5. Success: release lock, return ``ServiceHandle(..., spawned=True)``.
   Failure: raise ``RuntimeError`` with a user-facing hint.

Invariant worth restating: **the lock is held only for the spawn-decision
window, not for the lifetime of the spawned service.** ``sentinel serve``
does not take the lock; port-level mutual exclusion (two processes cannot
bind the same port) is what enforces single-instance correctness. The FLOCK
is strictly about racing two ``sentinel i`` invocations against each other.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from dataclasses import dataclass

import requests

from src.service.discovery import (
    ServiceDiscovery,
    discovery_lock,
    pid_alive,
    read_discovery,
    remove_discovery,
)

logger = logging.getLogger(__name__)

_POLL_INTERVAL_S = 0.1
_HEALTH_TIMEOUT_S = 2.0


@dataclass(frozen=True)
class ServiceHandle:
    """Result of ``ensure_service`` — everything the TUI needs to talk HTTP."""

    base_url: str
    token: str
    discovery: ServiceDiscovery
    spawned: bool  # True if this call spawned serve; False if we attached.


def _base_url_for(d: ServiceDiscovery) -> str:
    return f"http://127.0.0.1:{d.port}"


def _probe_health(d: ServiceDiscovery) -> bool:
    """GET /health; True iff the service answers 200 within a short deadline.

    ``/health`` is unauthenticated in ``src/service/app.py`` but we pass the
    bearer anyway — harmless today, future-proof if the endpoint is ever
    locked down. Any non-200, connection refused, or timeout returns False;
    the caller treats every failure mode as "not healthy".
    """

    url = f"{_base_url_for(d)}/health"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {d.token}"},
            timeout=_HEALTH_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.debug("health probe failed at %s: %s", url, exc)
        return False
    return resp.status_code == 200


def _spawn_serve() -> subprocess.Popen:
    """Launch ``sentinel serve --port 0`` fully detached.

    ``start_new_session=True`` runs ``setsid`` under the hood, which is enough
    on Linux to divorce the child from our controlling terminal and process
    group. No need for double-fork gymnastics. stdin/stdout/stderr go to
    /dev/null so the spawned service never touches the TUI's TTY.

    We intentionally inherit the environment. The caller's XDG_STATE_HOME,
    SENTINEL_SERVICE_TOKEN, and any other config must flow through to the
    service so both processes resolve the same discovery/token paths.
    """

    cmd = [sys.executable, "-m", "src.cli", "serve", "--port", "0"]
    logger.info("spawning service: %s", " ".join(cmd))
    return subprocess.Popen(  # noqa: S603 — argv is a literal, no shell.
        cmd,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


def ensure_service(*, timeout_s: float = 10.0) -> ServiceHandle:
    """Discover or spawn the Command Center service.

    The lock is held only during the spawn-decision window (this function),
    not for the lifetime of the spawned service. See module docstring.

    Raises:
        TimeoutError: another TUI holds the discovery lock past its deadline.
        RuntimeError: spawn completed but /health never came up in time.
    """

    deadline = time.monotonic() + max(0.0, timeout_s)

    with discovery_lock(timeout_s=5.0):
        existing = read_discovery()
        if existing is not None and pid_alive(existing.pid) and _probe_health(
            existing
        ):
            logger.info(
                "attached to existing service at port=%d (pid=%d)",
                existing.port,
                existing.pid,
            )
            return ServiceHandle(
                base_url=_base_url_for(existing),
                token=existing.token,
                discovery=existing,
                spawned=False,
            )

        # Stale or absent. Unlink so a polling reader below isn't fooled by
        # the old record while the child is still writing its own.
        if existing is not None:
            logger.info(
                "discovery stale (pid=%d alive=%s); respawning",
                existing.pid,
                pid_alive(existing.pid),
            )
        remove_discovery()

        stale_started_at = existing.started_at if existing is not None else None
        child = _spawn_serve()

        while True:
            candidate = read_discovery()
            if (
                candidate is not None
                and candidate.started_at != stale_started_at
                and pid_alive(candidate.pid)
                and _probe_health(candidate)
            ):
                logger.info(
                    "spawned new service at port=%d (pid=%d)",
                    candidate.port,
                    candidate.pid,
                )
                return ServiceHandle(
                    base_url=_base_url_for(candidate),
                    token=candidate.token,
                    discovery=candidate,
                    spawned=True,
                )

            if child.poll() is not None:
                # Child died before we could confirm it. No point in waiting
                # out the full deadline.
                logger.error(
                    "spawned serve exited early with returncode=%s",
                    child.returncode,
                )
                raise RuntimeError(
                    "sentinel serve exited before becoming healthy "
                    f"(returncode={child.returncode}); "
                    "try `sentinel serve` manually to see the error"
                )

            if time.monotonic() >= deadline:
                # Leave the child running; the operator may want to diagnose
                # it (e.g. it's blocked on DB migration). We only failed to
                # CONFIRM health within our deadline.
                logger.error(
                    "service did not become healthy within %.1fs", timeout_s
                )
                raise RuntimeError(
                    f"service did not become healthy within {timeout_s:.1f}s "
                    f"(child pid={child.pid}); "
                    "try `sentinel serve` manually to see the error"
                )

            time.sleep(_POLL_INTERVAL_S)


__all__ = ["ServiceHandle", "ensure_service"]
