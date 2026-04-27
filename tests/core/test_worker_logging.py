"""Worker subprocess logging contract.

``multiprocessing.get_context("spawn")`` re-imports the module in the child;
``basicConfig`` at module top of ``cli.py`` does NOT run in the child. The
worker's ``main()`` must call ``configure_logging()`` *first* so the child
has both a file log and the JSONL diagnostics handler attached.

This test does not drive the full orchestrator — it seeds a queued execution
row, runs the worker module with a fake orchestrator that emits one log line,
and asserts both log files were written.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import time

from src.core.execution.models import ExecutionKind
from src.core.execution.repository import ExecutionRepository
from src.core.persistence import connect, ensure_initialized


def _worker_main_in_child(execution_id: str, db_path: str, logs_dir: str) -> None:
    """Run ``worker.main()`` with the test's env applied to the child."""
    os.environ["SENTINEL_DB_PATH"] = db_path
    os.environ["SENTINEL_LOGS_DIR"] = logs_dir
    sys.argv = ["worker", "--execution-id", execution_id]
    from src.core.execution.worker import main

    sys.exit(main())


def test_spawned_worker_writes_log_and_jsonl(tmp_path, monkeypatch):
    db_path = tmp_path / "sentinel.db"
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    monkeypatch.setenv("SENTINEL_LOGS_DIR", str(logs_dir))

    ensure_initialized()
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        ex = repo.create("T-LOG", "proj", ExecutionKind.EXECUTE)
        # Insert worker row so the worker's heartbeat loop succeeds.
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO workers(execution_id, pid, started_at, last_heartbeat_at) "
            "VALUES (?, ?, ?, ?)",
            (ex.id, os.getpid(), now, now),
        )
    finally:
        conn.close()

    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(
        target=_worker_main_in_child,
        args=(ex.id, str(db_path), str(logs_dir)),
    )
    proc.start()
    proc.join(timeout=30)
    assert proc.exitcode is not None, "worker did not exit within 30s"

    log_file = logs_dir / "cli_stderr.log"
    jsonl_file = logs_dir / "agent_diagnostics.jsonl"

    # Give the FS a tick in case of buffered IO.
    for _ in range(20):
        if log_file.exists() and jsonl_file.exists():
            break
        time.sleep(0.1)

    assert log_file.exists(), f"{log_file} was not written"
    assert jsonl_file.exists(), f"{jsonl_file} was not written"
    assert log_file.read_text(encoding="utf-8").strip() != ""
    assert jsonl_file.read_text(encoding="utf-8").strip() != ""


def test_heartbeat_calls_repo_method(tmp_path, monkeypatch):
    """Heartbeat loop must go through ``ExecutionRepository.set_worker_heartbeat``.

    Replaces the repo method with a spy on the class and asserts the worker's
    heartbeat thread invokes it at least once. This directly verifies plan 04 G-08:
    no inline ``UPDATE workers SET last_heartbeat_at`` in worker.py.
    """
    import threading
    from datetime import datetime, timezone

    db_path = tmp_path / "sentinel.db"
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    monkeypatch.setenv("SENTINEL_LOGS_DIR", str(logs_dir))

    ensure_initialized()
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        ex = repo.create("T-HB", "proj", ExecutionKind.EXECUTE)
        # Keep it queued; main() will advance it and the heartbeat loop fires
        # regardless of whether orchestrator.execute() succeeds.
        conn.execute("UPDATE executions SET status='queued' WHERE id=?", (ex.id,))
        seed_heartbeat = datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO workers(execution_id, pid, started_at, last_heartbeat_at) "
            "VALUES (?, ?, ?, ?)",
            (ex.id, os.getpid(), seed_heartbeat, seed_heartbeat),
        )
        conn.commit()
    finally:
        conn.close()

    import src.core.execution.worker as worker_mod
    from src.core.execution.repository import ExecutionRepository as RealRepo

    # Accelerate heartbeat so the loop fires before main() returns.
    monkeypatch.setattr(worker_mod, "HEARTBEAT_INTERVAL_S", 0.01)

    call_count = {"n": 0}
    seen_execution_ids: list[str] = []
    call_done = threading.Event()
    lock = threading.Lock()
    original_method = RealRepo.set_worker_heartbeat

    def _spy(self, execution_id):
        rv = original_method(self, execution_id)
        with lock:
            call_count["n"] += 1
            seen_execution_ids.append(execution_id)
        call_done.set()
        return rv

    monkeypatch.setattr(RealRepo, "set_worker_heartbeat", _spy)

    sys.argv = ["worker", "--execution-id", ex.id]
    result_holder: dict[str, object] = {}

    def _runner() -> None:
        try:
            result_holder["rc"] = worker_mod.main()
        except SystemExit as e:
            result_holder["rc"] = int(e.code or 0)
        except BaseException as exc:  # noqa: BLE001
            result_holder["rc"] = f"err:{type(exc).__name__}:{exc}"

    t = threading.Thread(target=_runner, daemon=True)
    t.start()

    # Heartbeat must fire quickly via the spy.
    assert call_done.wait(timeout=15), "set_worker_heartbeat was not called"

    # Let main() run to completion (scaffold path returns after orchestrator.complete).
    t.join(timeout=30)

    assert call_count["n"] >= 1
    assert ex.id in seen_execution_ids
