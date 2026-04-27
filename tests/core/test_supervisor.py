"""Tests for :class:`src.core.execution.supervisor.Supervisor`.

The supervisor owns live-worker tracking, cancel escalation, reaping, and
startup reconciliation. We spawn tiny `multiprocessing.get_context('spawn')`
processes rather than the real worker module so the tests stay fast and
don't touch the orchestrator or agent SDK.
"""

from __future__ import annotations

import multiprocessing
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.repository import ExecutionRepository
from src.core.execution.supervisor import Supervisor
from src.core.persistence import connect, ensure_initialized


# ----------------------------------------------------------------- fixtures


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    ensure_initialized()
    conn = connect()
    yield conn
    conn.close()


@pytest.fixture
def supervisor(db):
    return Supervisor(connection_factory=connect)


# ---------------------------- tiny target procs ---------------------------


def _sleep_target(secs: float = 60.0) -> None:
    """Stand-in worker that lives until signalled."""
    time.sleep(secs)


def _exit_zero_target() -> None:
    sys.exit(0)


def _exit_one_target() -> None:
    sys.exit(1)


def _spawn_detached(target) -> multiprocessing.Process:
    """Start a process via spawn — not tied to Supervisor._ctx."""
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=target)
    proc.start()
    return proc


# ---------------------------------------------------------------- tests


def test_reap_removes_exited_adopted_worker(db, supervisor):
    """A dead adopted PID is removed from the adopted dict by reap()."""
    repo = ExecutionRepository(db)
    ex = repo.create("T-1", "proj", ExecutionKind.EXECUTE)

    # Simulate an adopted worker via the adopted dict + a workers row
    proc = _spawn_detached(_exit_zero_target)
    proc.join(timeout=5)
    db.execute(
        "INSERT INTO workers(execution_id, pid, started_at, last_heartbeat_at) "
        "VALUES (?, ?, ?, ?)",
        (
            ex.id,
            proc.pid,
            datetime.now(timezone.utc).isoformat(),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    supervisor._adopted[ex.id] = proc.pid  # noqa: SLF001

    reaped = supervisor.reap()

    assert reaped >= 1
    assert ex.id not in supervisor._adopted  # noqa: SLF001
    # workers row deleted
    row = db.execute(
        "SELECT execution_id FROM workers WHERE execution_id=?", (ex.id,)
    ).fetchone()
    assert row is None
    refreshed = repo.get(ex.id)
    assert refreshed is not None
    assert refreshed.status == ExecutionStatus.FAILED


def test_reconcile_dead_running_row_marks_failed(db, supervisor):
    """A running row whose PID is dead → status=failed, post_mortem_complete."""
    repo = ExecutionRepository(db)
    ex = repo.create("T-RECON", "proj", ExecutionKind.EXECUTE)
    repo.set_status(ex.id, ExecutionStatus.RUNNING)
    # seed a workers row with a guaranteed-dead PID
    db.execute(
        "INSERT INTO workers(execution_id, pid, started_at, last_heartbeat_at) "
        "VALUES (?, 1, ?, ?)",
        (
            ex.id,
            datetime.now(timezone.utc).isoformat(),
            (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        ),
    )
    # kill pid 1 is impossible — but it's the init process, so os.kill(1,0)
    # succeeds (PermissionError caught as alive). Use a pid we know is dead
    # instead.
    dead_proc = _spawn_detached(_exit_zero_target)
    dead_proc.join(timeout=5)
    db.execute(
        "UPDATE workers SET pid=? WHERE execution_id=?", (dead_proc.pid, ex.id)
    )

    adopted, reconciled = supervisor.adopt_or_reconcile_on_startup()

    assert adopted == 0
    assert reconciled >= 1
    refreshed = repo.get(ex.id)
    assert refreshed is not None
    assert refreshed.status == ExecutionStatus.FAILED
    assert refreshed.metadata.get("post_mortem_complete") is True


def test_reconcile_live_fresh_worker_is_adopted(db, supervisor):
    """A running row with a live PID + fresh heartbeat is adopted."""
    repo = ExecutionRepository(db)
    ex = repo.create("T-ADOPT", "proj", ExecutionKind.EXECUTE)
    repo.set_status(ex.id, ExecutionStatus.RUNNING)

    live_proc = _spawn_detached(_sleep_target)
    try:
        db.execute(
            "INSERT INTO workers(execution_id, pid, started_at, last_heartbeat_at) "
            "VALUES (?, ?, ?, ?)",
            (
                ex.id,
                live_proc.pid,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )

        adopted, reconciled = supervisor.adopt_or_reconcile_on_startup()

        assert adopted == 1
        assert ex.id in supervisor._adopted  # noqa: SLF001
    finally:
        os.kill(live_proc.pid, signal.SIGKILL)
        live_proc.join(timeout=5)


def test_reconcile_orphaned_queued_row(db, supervisor):
    """Set C: a queued row without a workers row is marked failed=spawn_interrupted."""
    repo = ExecutionRepository(db)
    ex = repo.create("T-QORPH", "proj", ExecutionKind.EXECUTE)
    # create() defaults to QUEUED — no coercion needed.
    assert ex.status == ExecutionStatus.QUEUED

    adopted, reconciled = supervisor.adopt_or_reconcile_on_startup()

    refreshed = repo.get(ex.id)
    assert refreshed is not None
    assert refreshed.status == ExecutionStatus.FAILED
    assert refreshed.error == "spawn_interrupted"


def test_post_mortem_is_idempotent(db, supervisor):
    """Running post_mortem twice on the same row is safe."""
    repo = ExecutionRepository(db)
    ex = repo.create("T-IDEMP", "proj", ExecutionKind.EXECUTE)
    repo.set_status(ex.id, ExecutionStatus.RUNNING)

    supervisor.post_mortem(ex.id, error="first_pass")
    row_after_first = repo.get(ex.id)
    assert row_after_first.status == ExecutionStatus.FAILED
    ended_first = row_after_first.ended_at

    # Running again must not re-record ended_at or flip status.
    supervisor.post_mortem(ex.id, error="second_pass")
    row_after_second = repo.get(ex.id)
    assert row_after_second.status == ExecutionStatus.FAILED
    assert row_after_second.ended_at == ended_first
    assert row_after_second.metadata.get("post_mortem_complete") is True


def test_pid_alive_handles_nonexistent_pid():
    # Pick a pid that definitely does not exist.
    dead_proc = _spawn_detached(_exit_zero_target)
    dead_proc.join(timeout=5)
    assert Supervisor._pid_alive(dead_proc.pid) is False
    assert Supervisor._pid_alive(os.getpid()) is True


def test_env_allowlist_shape():
    from src.core.execution.supervisor import _build_worker_env

    os.environ["SENTINEL_TEST_KEY"] = "x"
    os.environ["DEFINITELY_NOT_ALLOWED_QZQZ"] = "secret"
    try:
        env = _build_worker_env()
        assert "SENTINEL_TEST_KEY" in env
        assert "DEFINITELY_NOT_ALLOWED_QZQZ" not in env
        assert "PATH" in env
    finally:
        os.environ.pop("SENTINEL_TEST_KEY", None)
        os.environ.pop("DEFINITELY_NOT_ALLOWED_QZQZ", None)


def test_spawn_env_isolation_under_concurrent_requests(db, supervisor, monkeypatch):
    """Concurrent ``Supervisor.spawn`` calls must not leak env vars between spawns.

    Seeds four allow-listed marker env vars (``SENTINEL_TEST_MARKER_<i>``) *before*
    the concurrent spawn burst. Each spawn's fake ``Process.start`` snapshots the
    value of its assigned marker from ``os.environ``. Plan 04's guarantee: the
    env-swap window is serialized under ``self._lock``, so during any one spawn's
    ``proc.start()`` the global ``os.environ`` contains the full allowlist — no
    other thread can be mid-swap (``clear()`` then ``update()``) while this one
    reads. Without serialization, a concurrent spawn's ``os.environ.clear()``
    would leave readers seeing ``<missing>`` (G-09).
    """
    from concurrent.futures import ThreadPoolExecutor

    from src.core.execution.models import ExecutionKind

    repo = ExecutionRepository(db)
    executions = [
        repo.create(f"T-CONC-{i}", "proj", ExecutionKind.EXECUTE) for i in range(4)
    ]
    for ex in executions:
        repo.set_status(ex.id, ExecutionStatus.QUEUED)

    # Seed one marker per execution. All use the ``SENTINEL_`` prefix so they
    # pass the allowlist and land in the snapshotted env.
    id_to_key = {ex.id: f"SENTINEL_TEST_MARKER_{i}" for i, ex in enumerate(executions)}
    for ex_id, key in id_to_key.items():
        os.environ[key] = ex_id

    observed: dict[str, str] = {}
    observed_lock = __import__("threading").Lock()

    class _FakeProc:
        """Mimics multiprocessing.Process just enough for Supervisor.spawn."""

        _next_pid = 900000

        def __init__(self, *args, **kwargs):
            self.pid: int | None = None
            type(self)._next_pid += 1
            self._assigned_pid = type(self)._next_pid
            # Capture the execution id passed to _worker_entry as args=(execution_id,).
            positional = kwargs.get("args", args[0] if args else ())
            self._execution_id = positional[0] if positional else None

        def start(self) -> None:
            # Dwell a hair inside the lock so concurrent spawns are forced to queue.
            time.sleep(0.005)
            my_key = id_to_key[self._execution_id]
            val = os.environ.get(my_key, "<missing>")
            # With correct serialization, every marker we seeded is still present
            # when this executes — no concurrent swap can have wiped them.
            observed_all = {
                k: os.environ.get(k, "<missing>") for k in id_to_key.values()
            }
            with observed_lock:
                observed[self._execution_id] = val
                observed[f"_all_for_{self._execution_id}"] = repr(observed_all)
            self.pid = self._assigned_pid

        def is_alive(self) -> bool:
            return False

    class _FakeCtx:
        def Process(self, *args, **kwargs):
            return _FakeProc(*args, **kwargs)

    monkeypatch.setattr(supervisor, "_ctx", _FakeCtx())

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(supervisor.spawn, ex.id) for ex in executions]
            pids = [f.result(timeout=10) for f in futures]

        # 1. Each spawn saw its own marker under its own key — no leakage.
        for ex in executions:
            assert observed.get(ex.id) == ex.id, (
                f"execution {ex.id} saw {observed.get(ex.id)!r} "
                f"(full env snapshot: {observed.get(f'_all_for_{ex.id}')})"
            )

        # 2. No spawn observed ``<missing>`` for any marker — i.e. no spawn's
        #    proc.start() overlapped another's ``os.environ.clear()`` window.
        for ex in executions:
            snapshot = observed.get(f"_all_for_{ex.id}", "")
            assert "<missing>" not in snapshot, (
                f"spawn for {ex.id} observed a mid-swap env: {snapshot}"
            )

        assert len(pids) == 4
        # 3. Every execution got a workers row (INSERT OR REPLACE inside lock).
        for ex in executions:
            row = db.execute(
                "SELECT execution_id FROM workers WHERE execution_id=?", (ex.id,)
            ).fetchone()
            assert row is not None, f"missing workers row for {ex.id}"
    finally:
        for key in id_to_key.values():
            os.environ.pop(key, None)
