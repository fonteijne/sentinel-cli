"""API-level option-parity tests.

These cover:

* Posting an ``execute`` body with the full set of CLI flags persists each
  one against the expected typed option model (no silent drops).
* Posting an unsupported flag returns 422 (not 200/202).
* Posting against ``plan`` rejects ``execute``-only flags like ``no_env``.
* Persisted options are versioned for forward-compatibility.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from src.core.execution.options import OPTIONS_SCHEMA_VERSION

from tests.service.conftest import TEST_TOKEN


# Reuse the fake-supervisor fixture pattern from the existing commands tests.


class _FakeSupervisor:
    def __init__(self) -> None:
        self.spawn_calls: list[str] = []

    def spawn(self, execution_id: str) -> int:
        self.spawn_calls.append(execution_id)
        return 99999

    def cancel(self, execution_id: str) -> None:  # pragma: no cover
        pass


@pytest.fixture
def fake_supervisor() -> _FakeSupervisor:
    return _FakeSupervisor()


@pytest.fixture
def client(authed_env, fake_supervisor) -> Iterator[TestClient]:
    from src.service.app import create_app
    from src.service.deps import get_supervisor

    app = create_app()
    app.dependency_overrides[get_supervisor] = lambda: fake_supervisor
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        yield c


# --------------------------------------------------------------- execute kind


def test_execute_persists_all_supported_flags(client):
    resp = client.post(
        "/executions",
        json={
            "ticket_id": "PROJ-1",
            "project": "proj",
            "kind": "execute",
            "options": {
                "revise": True,
                "force": True,
                "no_env": True,
                "max_iterations": 7,
                "max_turns": 50,
                "prompt": "be thorough",
            },
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    persisted = body["metadata"]["options"]
    assert persisted["schema_version"] == OPTIONS_SCHEMA_VERSION
    assert persisted["values"]["revise"] is True
    assert persisted["values"]["force"] is True
    assert persisted["values"]["no_env"] is True
    assert persisted["values"]["max_iterations"] == 7
    assert persisted["values"]["max_turns"] == 50
    assert persisted["values"]["prompt"] == "be thorough"


def test_execute_rejects_unknown_option_with_422(client):
    resp = client.post(
        "/executions",
        json={
            "ticket_id": "PROJ-1",
            "project": "proj",
            "kind": "execute",
            "options": {"revise": True, "totally_made_up_flag": 1},
        },
    )
    assert resp.status_code == 422, resp.text
    assert "totally_made_up_flag" in resp.text


def test_execute_rejects_misnamed_flag(client):
    """Common operator typo: ``--max-iters`` instead of ``--max-iterations``.
    The API must catch this rather than silently default to 5 — the latter
    is the historical bug."""
    resp = client.post(
        "/executions",
        json={
            "ticket_id": "PROJ-1",
            "project": "proj",
            "kind": "execute",
            "options": {"max_iters": 2},
        },
    )
    assert resp.status_code == 422


# ------------------------------------------------------------------ plan kind


def test_plan_rejects_execute_only_flag(client):
    resp = client.post(
        "/executions",
        json={
            "ticket_id": "PROJ-1",
            "project": "proj",
            "kind": "plan",
            # ``no_env`` is intentionally not on PlanOptions.
            "options": {"no_env": True},
        },
    )
    assert resp.status_code == 422
    assert "no_env" in resp.text


def test_plan_persists_force_and_prompt(client):
    resp = client.post(
        "/executions",
        json={
            "ticket_id": "PROJ-1",
            "project": "proj",
            "kind": "plan",
            "options": {"force": True, "prompt": "draft v2"},
        },
    )
    assert resp.status_code == 202, resp.text
    persisted = resp.json()["metadata"]["options"]
    assert persisted["values"]["force"] is True
    assert persisted["values"]["prompt"] == "draft v2"


# --------------------------------------------------------------- debrief kind


def test_debrief_accepts_follow_up_ticket(client):
    resp = client.post(
        "/executions",
        json={
            "ticket_id": "PROJ-1",
            "project": "proj",
            "kind": "debrief",
            "options": {"follow_up_ticket": "PROJ-2"},
        },
    )
    assert resp.status_code == 202, resp.text


def test_debrief_rejects_revise(client):
    resp = client.post(
        "/executions",
        json={
            "ticket_id": "PROJ-1",
            "project": "proj",
            "kind": "debrief",
            "options": {"revise": True},
        },
    )
    assert resp.status_code == 422
