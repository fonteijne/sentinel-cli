"""HTTP tests for the Command Center read API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.core.events import EventBus, ExecutionStarted, PhaseChanged
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
def seeded(db_path):
    """Seed a handful of executions + events + agent_results, return useful ids."""
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        bus = EventBus(conn)

        e1 = repo.create("T-1", "ACME", ExecutionKind.PLAN)
        e2 = repo.create("T-2", "ACME", ExecutionKind.EXECUTE)
        e3 = repo.create("T-3", "OTHER", ExecutionKind.PLAN)

        repo.record_ended(e1.id, ExecutionStatus.SUCCEEDED)
        # e2 stays RUNNING

        bus.publish(
            ExecutionStarted(
                execution_id=e1.id, kind="plan", ticket_id="T-1", project="ACME"
            )
        )
        bus.publish(PhaseChanged(execution_id=e1.id, phase="analysing"))
        bus.publish(PhaseChanged(execution_id=e1.id, phase="writing"))

        repo.record_agent_result(
            e1.id, "dev", {"summary": "ok", "files": ["a.py"]}
        )
    finally:
        conn.close()

    return {"e1": e1.id, "e2": e2.id, "e3": e3.id}


@pytest.fixture
def client(db_path):
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_health(client, db_path):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok"}


def test_list_executions_no_filters(client, seeded):
    r = client.get("/executions")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 3
    # Most recent first — seeded order means e3 is newest.
    assert {i["id"] for i in body["items"]} == {seeded["e1"], seeded["e2"], seeded["e3"]}


def test_list_executions_filter_by_project(client, seeded):
    r = client.get("/executions", params={"project": "ACME"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    for item in body["items"]:
        assert item["project"] == "ACME"


def test_list_executions_filter_by_status(client, seeded):
    r = client.get("/executions", params={"status": "succeeded"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "succeeded"


def test_list_executions_filter_by_ticket(client, seeded):
    r = client.get("/executions", params={"ticket_id": "T-2"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["ticket_id"] == "T-2"


def test_list_executions_limit_clamped_when_client_requests_huge(client, seeded):
    r = client.get("/executions", params={"limit": 10000})
    assert r.status_code == 200
    # Clamped server-side, but only 3 rows seeded so we see 3.
    body = r.json()
    assert len(body["items"]) == 3


def test_list_executions_invalid_before_returns_422(client, seeded):
    r = client.get("/executions", params={"before": "not-a-date"})
    assert r.status_code == 422


def test_get_execution_200(client, seeded):
    r = client.get(f"/executions/{seeded['e1']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == seeded["e1"]
    assert body["ticket_id"] == "T-1"
    assert body["project"] == "ACME"


def test_get_execution_404(client, seeded):
    r = client.get("/executions/does-not-exist")
    assert r.status_code == 404


def test_events_basic(client, seeded):
    r = client.get(f"/executions/{seeded['e1']}/events")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 3
    assert body["items"][0]["type"] == "execution.started"
    # payloads are dicts
    for item in body["items"]:
        assert isinstance(item["payload"], dict)


def test_events_since_seq_pagination(client, seeded):
    r_all = client.get(f"/executions/{seeded['e1']}/events")
    seqs = [i["seq"] for i in r_all.json()["items"]]
    assert seqs == sorted(seqs)

    r_after = client.get(
        f"/executions/{seeded['e1']}/events", params={"since_seq": seqs[0]}
    )
    assert r_after.status_code == 200
    items = r_after.json()["items"]
    assert [i["seq"] for i in items] == seqs[1:]


def test_events_limit_clamped(client, seeded):
    r = client.get(
        f"/executions/{seeded['e1']}/events", params={"limit": 10000}
    )
    assert r.status_code == 200


def test_events_404_for_unknown_execution(client, seeded):
    r = client.get("/executions/nope/events")
    assert r.status_code == 404


def test_agent_results(client, seeded):
    r = client.get(f"/executions/{seeded['e1']}/agent-results")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["agent"] == "dev"
    assert body["items"][0]["result"] == {"summary": "ok", "files": ["a.py"]}


def test_agent_results_404_for_unknown_execution(client, seeded):
    r = client.get("/executions/nope/agent-results")
    assert r.status_code == 404


def test_list_executions_next_cursor_when_page_full(client, db_path):
    """Seed > default limit to force a full page and a cursor."""
    conn = connect()
    try:
        repo = ExecutionRepository(conn)
        # 3 rows, request limit=2 → page is full → cursor present
        repo.create("T-1", "ACME", ExecutionKind.PLAN)
        repo.create("T-2", "ACME", ExecutionKind.PLAN)
        repo.create("T-3", "ACME", ExecutionKind.PLAN)
    finally:
        conn.close()

    r = client.get("/executions", params={"limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None

    # Using cursor as `before` returns the remaining row.
    r2 = client.get("/executions", params={"limit": 2, "before": body["next_cursor"]})
    assert r2.status_code == 200
    body2 = r2.json()
    assert len(body2["items"]) == 1
    assert body2["next_cursor"] is None
