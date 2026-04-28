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


# -------------------------------------------------- explanatory error shape


def test_plan_with_ui_default_options_returns_explanatory_422(client):
    """The exact payload the dashboard used to send for a plan run before
    the dialog was made kind-aware. The response must:

    1. Be a structured object — not a raw multi-line pydantic dump as
       ``detail``-as-string.
    2. Name the rejected fields and the selected kind.
    3. List the allowed options for that kind so the operator (and the
       dashboard's error UI) can fix the call without reading source.
    """
    resp = client.post(
        "/executions",
        json={
            "ticket_id": "COE_JIRATESTAI-2352",
            "kind": "plan",
            "options": {
                "revise": False,
                "max_turns": 5,
                "follow_up_ticket": None,
            },
        },
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    detail = body["detail"]
    # Must be the structured shape, not the legacy raw-string blob.
    assert isinstance(detail, dict), detail
    assert detail["kind"] == "plan"
    assert set(detail["rejected_options"]) == {
        "revise",
        "max_turns",
        "follow_up_ticket",
    }
    # ``allowed_options`` reflects the actual PlanOptions field surface.
    assert set(detail["allowed_options"]) == {"force", "prompt"}
    # Single-sentence summary mentions the kind and at least one offender.
    assert "kind='plan'" in detail["message"]
    assert "revise" in detail["message"]


def test_invalid_option_value_keeps_helpful_error(client):
    """Direct value error (out-of-range int) must still produce a clean
    detail object — not a multi-line pydantic dump."""
    resp = client.post(
        "/executions",
        json={
            "ticket_id": "PROJ-1",
            "project": "proj",
            "kind": "execute",
            "options": {"max_iterations": 9999},
        },
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), detail
    assert detail["kind"] == "execute"
    # The bad value isn't an "extra" field — it just fails range validation.
    # We surface it via the ``errors`` list and the message.
    assert "max_iterations" in detail["message"]
    assert detail["allowed_options"]  # always populated


def test_execute_full_remote_parity_still_persists(client):
    """Regression guard: the explanatory-error refactor must not change the
    happy-path persisted shape for a fully-loaded execute payload (the
    surface the CLI's ``--remote`` path forwards).
    """
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
                "max_iterations": 4,
                "max_turns": 25,
                "prompt": "p",
            },
        },
    )
    assert resp.status_code == 202, resp.text
    values = resp.json()["metadata"]["options"]["values"]
    assert values == {
        "revise": True,
        "force": True,
        "no_env": True,
        "max_iterations": 4,
        "max_turns": 25,
        "prompt": "p",
    }
