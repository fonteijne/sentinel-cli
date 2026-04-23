"""WebSocket tests for the Command Center live event stream (plan 03)."""

from __future__ import annotations

import json
import threading
import time
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.core.events import (
    EventBus,
    ExecutionCancelled,
    ExecutionCompleted,
    ExecutionStarted,
    PhaseChanged,
)
from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.repository import ExecutionRepository
from src.core.persistence import connect, ensure_initialized
from src.service.app import create_app


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Route DB access through a per-test SQLite file."""
    path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(path))
    ensure_initialized()
    return path


@pytest.fixture
def client(db_path) -> Iterator[TestClient]:
    app = create_app()
    with TestClient(app) as c:
        yield c


def _seed_execution(
    ticket_id: str = "T-1",
    project: str = "ACME",
    kind: ExecutionKind = ExecutionKind.PLAN,
) -> str:
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        e = repo.create(ticket_id, project, kind)
        return e.id
    finally:
        conn.close()


def _publish(execution_id: str, event) -> None:
    conn = connect()
    try:
        bus = EventBus(conn)
        bus.publish(event)
    finally:
        conn.close()


def _finish(execution_id: str, status: ExecutionStatus) -> None:
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        repo.record_ended(execution_id, status)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Basic replay
# ---------------------------------------------------------------------------


def test_replay_finished_execution(client, db_path):
    """Seed 3 events ending in execution.completed; expect 3 event + end frame."""
    execution_id = _seed_execution()
    _publish(
        execution_id,
        ExecutionStarted(
            execution_id=execution_id, kind="plan", ticket_id="T-1", project="ACME"
        ),
    )
    _publish(execution_id, PhaseChanged(execution_id=execution_id, phase="analysing"))
    _publish(
        execution_id,
        ExecutionCompleted(
            execution_id=execution_id, status="succeeded", cost_cents=0
        ),
    )
    _finish(execution_id, ExecutionStatus.SUCCEEDED)

    frames: list[dict] = []
    with client.websocket_connect(f"/executions/{execution_id}/stream") as ws:
        for _ in range(4):
            frames.append(ws.receive_json())

    kinds = [f["kind"] for f in frames]
    assert kinds == ["event", "event", "event", "end"]
    assert [f["type"] for f in frames[:3]] == [
        "execution.started",
        "phase.changed",
        "execution.completed",
    ]
    assert frames[-1]["execution_status"] == "succeeded"
    # envelope shape stable: seq/ts/type/agent + payload
    for f in frames[:3]:
        assert set(f.keys()) == {"kind", "seq", "ts", "type", "agent", "payload"}
        assert isinstance(f["payload"], dict)


def test_since_seq_resumes_from_cursor(client, db_path):
    execution_id = _seed_execution()
    _publish(
        execution_id,
        ExecutionStarted(
            execution_id=execution_id, kind="plan", ticket_id="T-1", project="ACME"
        ),
    )
    _publish(execution_id, PhaseChanged(execution_id=execution_id, phase="analysing"))
    _publish(
        execution_id,
        ExecutionCompleted(
            execution_id=execution_id, status="succeeded", cost_cents=0
        ),
    )
    _finish(execution_id, ExecutionStatus.SUCCEEDED)

    frames: list[dict] = []
    with client.websocket_connect(
        f"/executions/{execution_id}/stream?since_seq=2"
    ) as ws:
        for _ in range(2):
            frames.append(ws.receive_json())

    assert frames[0]["kind"] == "event"
    assert frames[0]["seq"] == 3
    assert frames[0]["type"] == "execution.completed"
    assert frames[1] == {"kind": "end", "execution_status": "succeeded"}


def test_unknown_execution_closes_4404(client, db_path):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
            "/executions/does-not-exist/stream"
        ) as ws:
            ws.receive_json()
    assert excinfo.value.code == 4404


# ---------------------------------------------------------------------------
# Live tail
# ---------------------------------------------------------------------------


def test_live_tail_receives_new_event(client, db_path):
    """Connect to an in-flight execution; publish after connect; frame arrives."""
    execution_id = _seed_execution()
    _publish(
        execution_id,
        ExecutionStarted(
            execution_id=execution_id, kind="plan", ticket_id="T-1", project="ACME"
        ),
    )

    def _publish_after_delay() -> None:
        time.sleep(0.3)
        _publish(
            execution_id,
            PhaseChanged(execution_id=execution_id, phase="writing"),
        )

    with client.websocket_connect(f"/executions/{execution_id}/stream") as ws:
        first = ws.receive_json()
        assert first["type"] == "execution.started"
        t = threading.Thread(target=_publish_after_delay)
        t.start()
        try:
            second = ws.receive_json()
        finally:
            t.join()
        assert second["type"] == "phase.changed"


def test_live_tail_from_cross_process_writer(client, db_path):
    """Simulate a subprocess worker writing directly to the DB from a thread.

    The WS must read from the DB, not from the in-process bus — this test
    guards against accidentally re-introducing a bus subscription.
    """
    execution_id = _seed_execution()

    def _insert_from_another_connection() -> None:
        time.sleep(0.3)
        conn = connect()
        try:
            ts = "2026-04-23T00:00:00+00:00"
            payload = json.dumps(
                {
                    "type": "phase.changed",
                    "execution_id": execution_id,
                    "ts": ts,
                    "agent": None,
                    "phase": "writing",
                }
            )
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM events WHERE execution_id = ?",
                    (execution_id,),
                ).fetchone()
                seq = row[0] + 1
                conn.execute(
                    "INSERT INTO events("
                    "execution_id, seq, ts, agent, type, payload_json"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (execution_id, seq, ts, None, "phase.changed", payload),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    t = threading.Thread(target=_insert_from_another_connection)
    with client.websocket_connect(f"/executions/{execution_id}/stream") as ws:
        t.start()
        try:
            frame = ws.receive_json()
        finally:
            t.join()
        assert frame["type"] == "phase.changed"
        assert frame["payload"]["phase"] == "writing"


# ---------------------------------------------------------------------------
# Heartbeat & disconnect
# ---------------------------------------------------------------------------


def test_heartbeat_on_silence(client, db_path, monkeypatch):
    import src.service.routes.stream as stream_module

    monkeypatch.setattr(stream_module, "HEARTBEAT_INTERVAL_S", 0.3)
    execution_id = _seed_execution()

    with client.websocket_connect(f"/executions/{execution_id}/stream") as ws:
        frame = ws.receive_json()
        assert frame["kind"] == "heartbeat"
        assert "ts" in frame


def test_client_disconnect_exits_cleanly(client, db_path):
    execution_id = _seed_execution()
    with client.websocket_connect(f"/executions/{execution_id}/stream") as ws:
        pass
    # Reconnecting works — the server coroutine from the first connect
    # exited cleanly and did not leak state.
    with client.websocket_connect(f"/executions/{execution_id}/stream") as ws:
        ws.close()


# ---------------------------------------------------------------------------
# Terminal mapping
# ---------------------------------------------------------------------------


def test_terminal_mapping_cancelled(client, db_path):
    execution_id = _seed_execution()
    _publish(
        execution_id,
        ExecutionCancelled(execution_id=execution_id),
    )
    _finish(execution_id, ExecutionStatus.CANCELLED)

    frames: list[dict] = []
    with client.websocket_connect(f"/executions/{execution_id}/stream") as ws:
        for _ in range(2):
            frames.append(ws.receive_json())

    assert frames[0]["type"] == "execution.cancelled"
    assert frames[1] == {"kind": "end", "execution_status": "cancelled"}


def test_terminal_mapping_completed_uses_succeeded(client, db_path):
    """Guard against the `split('.')[-1]` regression."""
    execution_id = _seed_execution()
    _publish(
        execution_id,
        ExecutionCompleted(
            execution_id=execution_id, status="succeeded", cost_cents=0
        ),
    )
    _finish(execution_id, ExecutionStatus.SUCCEEDED)

    frames: list[dict] = []
    with client.websocket_connect(f"/executions/{execution_id}/stream") as ws:
        for _ in range(2):
            frames.append(ws.receive_json())

    assert frames[-1]["execution_status"] == "succeeded"
    assert frames[-1]["execution_status"] != "completed"


# ---------------------------------------------------------------------------
# Slow-client backpressure
# ---------------------------------------------------------------------------


def test_slow_client_backpressure(client, db_path, monkeypatch):
    """Client that never reads → server closes with 1011 after SEND_TIMEOUT_S.

    We can't easily force the starlette TestClient to block send_json so we
    monkeypatch the route's _send to raise asyncio.TimeoutError, simulating
    the wait_for timeout path.
    """
    import asyncio as _asyncio

    import src.service.routes.stream as stream_module

    monkeypatch.setattr(stream_module, "SEND_TIMEOUT_S", 0.05)

    execution_id = _seed_execution()
    _publish(
        execution_id,
        ExecutionStarted(
            execution_id=execution_id, kind="plan", ticket_id="T-1", project="ACME"
        ),
    )

    async def _timing_out_wait_for(coro, timeout):  # type: ignore[no-untyped-def]
        # Drain the coro to avoid "coroutine was never awaited" warnings.
        try:
            coro.close()
        except Exception:
            pass
        raise _asyncio.TimeoutError()

    monkeypatch.setattr(stream_module.asyncio, "wait_for", _timing_out_wait_for)

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
            f"/executions/{execution_id}/stream"
        ) as ws:
            ws.receive_json()
    assert excinfo.value.code == 1011
