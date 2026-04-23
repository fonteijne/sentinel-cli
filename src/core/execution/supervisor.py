"""Out-of-process worker supervisor for the Command Center.

A single ``Supervisor`` instance lives on the FastAPI app state (``app.state.supervisor``)
and coordinates the lifecycle of ``python -m src.core.execution.worker`` child
processes — spawning, cancelling (two-stage SIGTERM→SIGINT→SIGKILL dance),
reaping, post-mortem cleanup, and startup reconciliation of runs that outlived
a prior service instance.

Thread-safety: every method that mutates ``_workers`` is guarded by an ``RLock``
(``@_locked``). ``post_mortem`` intentionally does NOT hold the lock — it is
reentrant from ``reap`` (already locked) and reconciliation (locked), and uses
its own short-lived connection.
"""

from __future__ import annotations

import functools
import logging
import multiprocessing
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional, Tuple

from src.core.events.bus import EventBus
from src.core.events.types import (
    ExecutionCancelled,
    ExecutionCancelling,
    ExecutionFailed,
)
from src.core.execution.models import ExecutionStatus
from src.core.execution.repository import ExecutionRepository

logger = logging.getLogger(__name__)

ConnectionFactory = Callable[[], sqlite3.Connection]

# Cancel escalation timings (seconds). Exposed as module-level so tests can
# patch to zero for a fast-path check.
CANCEL_GRACE_SIGTERM_S = 20.0
CANCEL_GRACE_SIGINT_S = 10.0
HEARTBEAT_STALE_S = 30.0
REAP_INTERVAL_S = 5.0


_ENV_EXACT = {
    "PATH", "HOME", "LANG", "LC_ALL", "TZ", "USER", "LOGNAME",
    "TMPDIR", "TEMP", "TMP",
    "DOCKER_HOST", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "no_proxy",
}
_ENV_PREFIXES = (
    "SENTINEL_",
    "JIRA_", "GITLAB_",
    "ANTHROPIC_", "CLAUDE_",
    "XDG_",
    "COMPOSE_", "BUILDKIT_",
    "GIT_", "SSH_",
)


def _build_worker_env() -> Dict[str, str]:
    """Explicit allowlist — adding a var means adding it here."""
    return {
        k: v
        for k, v in os.environ.items()
        if k in _ENV_EXACT or k.startswith(_ENV_PREFIXES)
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _worker_entry(execution_id: str) -> None:
    """``multiprocessing.Process`` target — delegates to the module entry.

    Kept at module level (not a closure) so ``spawn`` can pickle it.
    """
    from src.core.execution.worker import main as worker_main

    sys.argv = ["worker", "--execution-id", execution_id]
    sys.exit(worker_main())


def _locked(method):  # type: ignore[no-untyped-def]
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        with self._lock:  # noqa: SLF001
            return method(self, *args, **kwargs)

    return wrapper


class Supervisor:
    """Owns the live-worker dict and enforces ordering across cancel/reap/reconcile."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._ctx = multiprocessing.get_context("spawn")
        self._workers: Dict[str, "multiprocessing.Process"] = {}
        # Adopted workers (PID + execution_id) that were spawned by a previous
        # process — we can't track them as Process objects; they only have a
        # PID. Separate dict, checked alongside _workers where needed.
        self._adopted: Dict[str, int] = {}
        # RLock so shutdown()->cancel() can nest without deadlock.
        self._lock = threading.RLock()
        self._connection_factory = connection_factory

    # ------------------------------------------------------------ spawn/cancel

    @_locked
    def spawn(self, execution_id: str) -> int:
        """Fork a new worker and record the ``workers`` row atomically.

        Returns the child's PID. Raises if the child couldn't be started.
        """
        if execution_id in self._workers or execution_id in self._adopted:
            raise RuntimeError(
                f"worker for execution {execution_id} already running"
            )

        env = _build_worker_env()
        proc = self._ctx.Process(
            target=_worker_entry,
            args=(execution_id,),
            daemon=False,
        )
        # multiprocessing.Process does not accept env=; spawn inherits os.environ
        # of the launching interpreter. Temporarily scrub+restore so the child
        # only sees the allowlist.
        saved = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(env)
            proc.start()
        finally:
            os.environ.clear()
            os.environ.update(saved)

        assert proc.pid is not None, "Process.start() did not assign a pid"
        self._workers[execution_id] = proc

        with self._connection_factory() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO workers("
                    "execution_id, pid, started_at, last_heartbeat_at, compose_projects"
                    ") VALUES (?, ?, ?, ?, '[]')",
                    (execution_id, proc.pid, _now_iso(), _now_iso()),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        return proc.pid

    @_locked
    def cancel(self, execution_id: str) -> None:
        """Two-stage SIGTERM → SIGINT → SIGKILL escalation.

        Runs synchronously; callers should offload to a threadpool if the
        escalation window (up to 30s) matters for request latency. The
        endpoint layer returns 202 immediately and lets reaper observe
        the terminal status asynchronously, so this method is typically
        dispatched via ``loop.run_in_executor``.
        """
        pid = self._pid_for(execution_id)
        if pid is None:
            return

        with self._connection_factory() as conn:
            repo = ExecutionRepository(conn)
            bus = EventBus(conn)
            repo.set_status(execution_id, ExecutionStatus.CANCELLING)
            try:
                bus.publish(ExecutionCancelling(execution_id=execution_id))
            except Exception:
                logger.exception("cancel: publish cancelling failed")

        self._signal(pid, signal.SIGTERM)
        if self._wait_exit(execution_id, CANCEL_GRACE_SIGTERM_S):
            return
        logger.warning("cancel: SIGTERM timed out pid=%d, sending SIGINT", pid)
        self._signal(pid, signal.SIGINT)
        if self._wait_exit(execution_id, CANCEL_GRACE_SIGINT_S):
            return
        logger.warning("cancel: SIGINT timed out pid=%d, sending SIGKILL", pid)
        self._signal(pid, signal.SIGKILL)
        self._wait_exit(execution_id, 5.0)

    def _pid_for(self, execution_id: str) -> Optional[int]:
        proc = self._workers.get(execution_id)
        if proc is not None and proc.pid is not None:
            return proc.pid
        return self._adopted.get(execution_id)

    @staticmethod
    def _signal(pid: int, sig: int) -> None:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass

    def _wait_exit(self, execution_id: str, timeout_s: float) -> bool:
        """Poll every 0.2s for the worker to exit. Returns True on exit."""
        deadline = time.monotonic() + timeout_s
        pid = self._pid_for(execution_id)
        if pid is None:
            return True
        while time.monotonic() < deadline:
            if not self._pid_alive(pid):
                return True
            time.sleep(0.2)
        return False

    # ------------------------------------------------------------------- reap

    @_locked
    def reap(self) -> int:
        """Collect exited workers, delete ``workers`` rows, run post_mortem.

        Called on a 5s cadence from the FastAPI lifespan's reaper task.
        Returns the number of workers reaped this cycle.
        """
        dead: list[str] = []
        for eid, proc in list(self._workers.items()):
            if not proc.is_alive():
                dead.append(eid)
        for eid in list(self._adopted.keys()):
            if not self._pid_alive(self._adopted[eid]):
                dead.append(eid)

        for eid in dead:
            proc = self._workers.pop(eid, None)
            if proc is not None:
                try:
                    proc.join(timeout=1.0)
                except Exception:
                    logger.exception("reap: join pid=%s failed", proc.pid)
            self._adopted.pop(eid, None)
            with self._connection_factory() as conn:
                conn.execute("DELETE FROM workers WHERE execution_id=?", (eid,))
            self.post_mortem(eid)
        return len(dead)

    # ------------------------------------------------ startup reconciliation

    @_locked
    def adopt_or_reconcile_on_startup(self) -> Tuple[int, int]:
        """Walk three sets of rows on service boot.

        Returns ``(adopted, reconciled)``. See plan 04 "Startup reconciliation".
        """
        adopted = 0
        reconciled = 0
        now = datetime.now(timezone.utc)

        with self._connection_factory() as conn:
            repo = ExecutionRepository(conn)
            in_progress = [
                row
                for row in repo.list(limit=1000)
                if row.status
                in (ExecutionStatus.RUNNING, ExecutionStatus.CANCELLING)
            ]
            queued = [
                row
                for row in repo.list(limit=1000)
                if row.status == ExecutionStatus.QUEUED
            ]
            post_mortem_rows = repo.list_post_mortem_incomplete()

        # Set A — in-progress rows
        for row in in_progress:
            with self._connection_factory() as conn:
                repo = ExecutionRepository(conn)
                worker = repo.get_worker(row.id)

            alive = bool(worker) and self._pid_alive(worker["pid"])
            fresh = bool(worker) and (
                now - worker["last_heartbeat_at"]
            ) < timedelta(seconds=HEARTBEAT_STALE_S)

            if worker and alive and fresh:
                self._adopted[row.id] = worker["pid"]
                adopted += 1
            elif worker and alive and not fresh:
                logger.warning(
                    "adopting stale-but-alive worker pid=%d execution=%s "
                    "(heartbeat age=%s)",
                    worker["pid"],
                    row.id,
                    now - worker["last_heartbeat_at"],
                )
                self._adopted[row.id] = worker["pid"]
                adopted += 1
            else:
                self.post_mortem(row.id, error="orphaned_on_restart")
                reconciled += 1

        # Set B — post-mortem-incomplete terminal rows
        for row in post_mortem_rows:
            self.post_mortem(row.id)
            reconciled += 1

        # Set C — orphaned queued rows (spawn never happened)
        for row in queued:
            with self._connection_factory() as conn:
                repo = ExecutionRepository(conn)
                if repo.get_worker(row.id) is None:
                    repo.record_ended(
                        row.id,
                        ExecutionStatus.FAILED,
                        error="spawn_interrupted",
                    )
                    bus = EventBus(conn)
                    try:
                        bus.publish(
                            ExecutionFailed(
                                execution_id=row.id,
                                error="spawn_interrupted",
                            )
                        )
                    except Exception:
                        logger.exception(
                            "reconcile: publish for queued %s failed", row.id
                        )
                    reconciled += 1

        return adopted, reconciled

    # ----------------------------------------------------------- post_mortem

    def post_mortem(self, execution_id: str, error: Optional[str] = None) -> None:
        """Terminal cleanup — publish terminal event, compose down, mark complete.

        NOT ``@_locked`` — reentrant from ``reap``/``reconcile`` which already
        hold the lock. Uses its own connection. Never touches ``self._workers``.
        Each step is independently try/except'd so a single failure never
        skips subsequent steps.
        """
        terminal_status = ExecutionStatus.FAILED
        try:
            with self._connection_factory() as conn:
                repo = ExecutionRepository(conn)
                bus = EventBus(conn)
                execution = repo.get(execution_id)
                if execution is None:
                    logger.warning(
                        "post_mortem: execution %s missing; skipping", execution_id
                    )
                    return

                # Choose terminal status from current row state.
                if execution.status == ExecutionStatus.CANCELLING:
                    terminal_status = ExecutionStatus.CANCELLED
                elif execution.status in (
                    ExecutionStatus.SUCCEEDED,
                    ExecutionStatus.FAILED,
                    ExecutionStatus.CANCELLED,
                ):
                    terminal_status = execution.status
                else:
                    terminal_status = ExecutionStatus.FAILED

                # 1. Publish the terminal event (cheapest + most important).
                try:
                    if terminal_status == ExecutionStatus.CANCELLED:
                        bus.publish(ExecutionCancelled(execution_id=execution_id))
                    elif terminal_status == ExecutionStatus.FAILED:
                        bus.publish(
                            ExecutionFailed(
                                execution_id=execution_id,
                                error=error or execution.error or "worker_exited",
                            )
                        )
                    # SUCCEEDED: orchestrator already published ExecutionCompleted.
                except Exception:
                    logger.exception(
                        "post_mortem: publish terminal failed %s", execution_id
                    )

                compose_projects = (execution.metadata or {}).get(
                    "compose_projects", []
                ) or []
        except Exception:
            logger.exception("post_mortem: initial DB read failed %s", execution_id)
            compose_projects = []

        # 2. Docker compose down for each recorded project.
        for project in compose_projects:
            try:
                subprocess.run(
                    [
                        "docker", "compose",
                        "-p", project,
                        "down", "-v", "--timeout", "5",
                    ],
                    check=False,
                    capture_output=True,
                    timeout=30,
                )
            except Exception:
                logger.exception(
                    "post_mortem: compose down %s failed", project
                )

        # 3. Worktree prune — best-effort; orchestrator's finally normally handles.
        try:
            self._prune_worktree_if_any(execution_id)
        except Exception:
            logger.exception(
                "post_mortem: worktree prune failed %s", execution_id
            )

        # 4. Row state transition.
        try:
            with self._connection_factory() as conn:
                repo = ExecutionRepository(conn)
                execution = repo.get(execution_id)
                if execution is not None and execution.ended_at is None:
                    repo.record_ended(
                        execution_id, terminal_status, error=error
                    )
        except Exception:
            logger.exception(
                "post_mortem: record_ended failed %s", execution_id
            )

        # 5. Mark post-mortem complete so reconciliation doesn't loop.
        try:
            with self._connection_factory() as conn:
                repo = ExecutionRepository(conn)
                repo.mark_metadata(execution_id, post_mortem_complete=True)
        except Exception:
            logger.exception(
                "post_mortem: mark_metadata failed %s", execution_id
            )

    @staticmethod
    def _prune_worktree_if_any(execution_id: str) -> None:
        """Best-effort worktree cleanup hook. Overridable for tests.

        Left as a stub in plan 04 — the orchestrator's ``finally`` block owns
        the primary worktree lifecycle. This runs only when the worker died
        before reaching the orchestrator's cleanup.
        """
        return None

    # ----------------------------------------------------------- shutdown

    @_locked
    def shutdown(self) -> None:
        """Lifespan teardown — cancel all in-flight workers."""
        for eid in list(self._workers) + list(self._adopted):
            try:
                self.cancel(eid)
            except Exception:
                logger.exception("shutdown: cancel %s failed", eid)
        self._workers.clear()
        self._adopted.clear()

    # ----------------------------------------------------------- helpers

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Another user owns the pid — it is alive, just not ours.
            return True


async def periodic_reap(
    supervisor: Supervisor, interval_s: float = REAP_INTERVAL_S
) -> None:
    """Async helper run from the FastAPI lifespan task group."""
    import asyncio

    loop = asyncio.get_running_loop()
    while True:
        try:
            await loop.run_in_executor(None, supervisor.reap)
        except Exception:
            logger.exception("periodic_reap cycle failed")
        await asyncio.sleep(interval_s)
