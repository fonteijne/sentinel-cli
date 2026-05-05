"""Tests for src.core.execution.worker — dispatcher behaviour without scaffold.

Task 1.7 removed the scaffold fallback. If the orchestrator does not expose a
method for the persisted ``ExecutionKind``, the worker must:
  * log an error,
  * mark the execution ``failed`` via ``orchestrator.fail``,
  * return exit code 1.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.repository import ExecutionRepository
from src.core.persistence import connect, ensure_initialized


def _seed_queued(conn, kind: ExecutionKind = ExecutionKind.EXECUTE) -> str:
    import os
    from datetime import datetime, timezone

    repo = ExecutionRepository(conn)
    ex = repo.create("T-W", "proj", kind)
    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO workers(execution_id, pid, started_at, last_heartbeat_at) "
        "VALUES (?, ?, ?, ?)",
        (ex.id, os.getpid(), ts, ts),
    )
    conn.commit()
    return ex.id


def test_worker_fails_when_orchestrator_method_missing(tmp_path, monkeypatch):
    """When _resolve_method returns None, worker must NOT fall through to scaffold."""
    db_path = tmp_path / "sentinel.db"
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    monkeypatch.setenv("SENTINEL_LOGS_DIR", str(logs_dir))

    ensure_initialized()
    conn = connect()
    try:
        execution_id = _seed_queued(conn, ExecutionKind.EXECUTE)
    finally:
        conn.close()

    import src.core.execution.worker as worker_mod

    # Replace _resolve_method to simulate a missing verb.
    monkeypatch.setattr(worker_mod, "_resolve_method", lambda orc, kind: None)

    # Minimal orchestrator whose fail() marks the row failed and publishes.
    fail_calls = []

    def _build_orchestrator(repo, bus, cancel_flag):
        orc = MagicMock()
        orc.repo = repo

        def _fail(execution, error):
            fail_calls.append(error)
            repo.record_ended(execution.id, ExecutionStatus.FAILED, error=error)

        orc.fail.side_effect = _fail
        return orc

    monkeypatch.setattr(worker_mod, "_build_orchestrator", _build_orchestrator)

    sys.argv = ["worker", "--execution-id", execution_id]
    rc = worker_mod.main()
    assert rc == 1
    assert fail_calls == ["orchestrator method not found"]

    # Row transitioned to failed with the expected error.
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        row = repo.get(execution_id)
        assert row is not None
        assert row.status == ExecutionStatus.FAILED
        assert row.error == "orchestrator method not found"
    finally:
        conn.close()


def test_worker_dispatches_to_orchestrator_method(tmp_path, monkeypatch):
    """Happy path: _resolve_method returns a callable; worker invokes it with options."""
    db_path = tmp_path / "sentinel.db"
    logs_dir = tmp_path / "logs"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    monkeypatch.setenv("SENTINEL_LOGS_DIR", str(logs_dir))

    ensure_initialized()
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        # Use PLAN so we can stash options via create()
        ex = repo.create("T-OPT", "proj", ExecutionKind.PLAN, options={"force": True})
        import os
        from datetime import datetime, timezone

        ts = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO workers(execution_id, pid, started_at, last_heartbeat_at) "
            "VALUES (?, ?, ?, ?)",
            (ex.id, os.getpid(), ts, ts),
        )
        conn.commit()
        execution_id = ex.id
    finally:
        conn.close()

    import src.core.execution.worker as worker_mod

    # Simulate a succeeded PlanResult
    from src.core.execution.orchestrator import PlanResult

    captured = {}

    def _fake_plan(execution_id, **options):
        captured["execution_id"] = execution_id
        captured["options"] = options
        return PlanResult(status=ExecutionStatus.SUCCEEDED, details={"ok": True})

    def _build_orchestrator(repo, bus, cancel_flag):
        orc = MagicMock()
        orc.repo = repo
        orc.plan = _fake_plan
        return orc

    monkeypatch.setattr(worker_mod, "_build_orchestrator", _build_orchestrator)
    monkeypatch.setattr(
        worker_mod, "_resolve_method", lambda orc, kind: orc.plan
    )

    sys.argv = ["worker", "--execution-id", execution_id]
    rc = worker_mod.main()

    assert rc == 0
    assert captured["execution_id"] == execution_id
    assert captured["options"] == {"force": True}
