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
from pathlib import Path

import pytest

from src.core.execution.models import ExecutionKind, ExecutionStatus
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
