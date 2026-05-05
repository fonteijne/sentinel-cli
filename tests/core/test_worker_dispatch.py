"""Worker dispatch contract.

Pinned by the Command Center replacement acceptance criteria:

* The worker must call ``Orchestrator.plan/execute/debrief`` for the row's
  kind. There is no fallback "scaffold lifecycle" path that completes a run
  green without invoking the workflow.
* When the orchestrator's verb cannot be resolved (future regression), the
  row is recorded as ``failed`` with an explicit error — never as
  ``succeeded``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.options import ExecuteOptions, to_metadata_options
from src.core.execution.repository import ExecutionRepository
from src.core.persistence import connect, ensure_initialized


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    monkeypatch.setenv("SENTINEL_LOGS_DIR", str(tmp_path / "logs"))
    ensure_initialized()
    conn = connect()
    yield conn
    conn.close()


def _seed_worker_row(conn, execution_id: str) -> None:
    """Worker heartbeat thread requires a workers row — tests must seed it."""
    import os

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO workers(execution_id, pid, started_at, last_heartbeat_at) "
        "VALUES (?, ?, ?, ?)",
        (execution_id, os.getpid(), now, now),
    )
    conn.commit()


def test_worker_invokes_orchestrator_method_for_kind(db, monkeypatch):
    """The worker must call the orchestrator's plan/execute/debrief verb,
    not the scaffold path. We assert by recording calls on a fake orchestrator
    that the worker builds via :func:`_build_orchestrator`."""
    repo = ExecutionRepository(db)
    options_blob = to_metadata_options(ExecuteOptions())
    execution = repo.create(
        "PROJ-1", "proj", ExecutionKind.EXECUTE, options=options_blob
    )
    _seed_worker_row(db, execution.id)
    db.close()

    calls: list[Any] = []

    class FakeOrchestrator:
        def __init__(self):
            self.repo = ExecutionRepository(connect())

        def plan(self, execution_id: str, **_):  # pragma: no cover
            calls.append(("plan", execution_id))

        def execute(self, execution_id: str, **_):
            calls.append(("execute", execution_id))
            # Mark succeeded so the worker reports exit_code 0.
            self.repo.record_ended(
                execution_id, ExecutionStatus.SUCCEEDED
            )

        def debrief(self, execution_id: str, **_):  # pragma: no cover
            calls.append(("debrief", execution_id))

    monkeypatch.setattr(
        "src.core.execution.worker._build_orchestrator",
        lambda repo, bus, cancel: FakeOrchestrator(),
    )

    import sys

    monkeypatch.setattr(sys, "argv", ["worker", "--execution-id", execution.id])
    from src.core.execution.worker import main

    rc = main()
    assert rc == 0
    assert calls == [("execute", execution.id)]


def test_worker_marks_failed_when_no_orchestrator_method_for_kind(
    db, monkeypatch
):
    """If somebody adds a new ExecutionKind without wiring the verb, the
    worker must record FAILED — never silently succeed via a scaffold path.
    We simulate this by replacing _resolve_method to return None.
    """
    repo = ExecutionRepository(db)
    options_blob = to_metadata_options(ExecuteOptions())
    execution = repo.create(
        "PROJ-2", "proj", ExecutionKind.EXECUTE, options=options_blob
    )
    _seed_worker_row(db, execution.id)
    db.close()

    monkeypatch.setattr(
        "src.core.execution.worker._resolve_method",
        lambda orchestrator, kind: None,
    )

    import sys

    monkeypatch.setattr(sys, "argv", ["worker", "--execution-id", execution.id])
    from src.core.execution.worker import main

    rc = main()
    assert rc == 1

    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        refreshed = repo.get(execution.id)
        assert refreshed is not None
        assert refreshed.status == ExecutionStatus.FAILED
        assert "unsupported_execution_kind" in (refreshed.error or "")
    finally:
        conn.close()
