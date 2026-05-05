"""CLI ``sentinel execute --remote`` payload round-trip.

Closes the historical regression where the CLI's remote path forwarded only
``--revise`` to ``POST /executions``, silently dropping ``--force``,
``--no-env``, ``--max-iterations`` and ``--prompt``. These tests pin that
``src.cli._remote_execute`` POSTs every CLI option ``ExecuteOptions`` knows
about, and that the resulting dict validates against ``ExecuteOptions`` so a
future flag rename cannot silently break the round-trip.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.cli import _remote_execute
from src.core.execution.options import ExecuteOptions


@pytest.fixture
def fake_post():
    """Patch ``requests.post`` and capture the payload."""
    captured = {}

    fake_resp = MagicMock()
    fake_resp.ok = True
    fake_resp.status_code = 202
    fake_resp.json.return_value = {"id": "exec-1", "status": "queued"}
    fake_resp.headers = {}

    def _post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers or {}
        return fake_resp

    with patch("requests.post", side_effect=_post):
        yield captured


def test_remote_execute_forwards_full_flag_set(fake_post, monkeypatch):
    """All ExecuteOptions fields supported by the CLI must reach the wire."""
    monkeypatch.delenv("SENTINEL_SERVICE_TOKEN", raising=False)
    _remote_execute(
        ticket_id="PROJ-1",
        project="proj",
        options={
            "revise": True,
            "force": True,
            "no_env": True,
            "max_iterations": 9,
            "prompt": "be thorough",
        },
        follow=False,
        idempotency_key="test-key-1",
    )

    body = fake_post["json"]
    assert body["ticket_id"] == "PROJ-1"
    assert body["project"] == "proj"
    assert body["kind"] == "execute"
    assert body["options"] == {
        "revise": True,
        "force": True,
        "no_env": True,
        "max_iterations": 9,
        "prompt": "be thorough",
    }
    # Idempotency-Key is forwarded as a header, not the body.
    assert fake_post["headers"].get("Idempotency-Key") == "test-key-1"


def test_remote_execute_drops_none_values_only(fake_post, monkeypatch):
    """``None`` is the CLI's sentinel for "flag not given"; falsy bools and
    zero-iteration counts MUST still be forwarded so the server sees the
    operator's actual choice."""
    monkeypatch.delenv("SENTINEL_SERVICE_TOKEN", raising=False)
    _remote_execute(
        ticket_id="PROJ-2",
        project="proj",
        options={
            "revise": False,
            "force": False,
            "no_env": False,
            "max_iterations": 1,
            "prompt": None,  # absent — must not be forwarded
        },
        follow=False,
        idempotency_key=None,
    )

    body = fake_post["json"]
    # ``prompt`` was None — dropped.
    assert "prompt" not in body["options"]
    # All booleans/numbers preserved, including the falsy ones.
    assert body["options"]["revise"] is False
    assert body["options"]["force"] is False
    assert body["options"]["no_env"] is False
    assert body["options"]["max_iterations"] == 1


def test_remote_execute_payload_round_trips_to_execute_options(monkeypatch):
    """Direct check: the dict the CLI sends must be a valid ExecuteOptions."""
    monkeypatch.delenv("SENTINEL_SERVICE_TOKEN", raising=False)
    captured = {}

    fake_resp = MagicMock()
    fake_resp.ok = True
    fake_resp.status_code = 202
    fake_resp.json.return_value = {"id": "x", "status": "queued"}
    fake_resp.headers = {}

    def _post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return fake_resp

    with patch("requests.post", side_effect=_post):
        _remote_execute(
            ticket_id="PROJ-4",
            project="proj",
            options={
                "revise": True,
                "force": False,
                "no_env": False,
                "max_iterations": 2,
                "prompt": None,
            },
            follow=False,
            idempotency_key=None,
        )

    options_dict = captured["json"]["options"]
    # ``extra="forbid"`` on ExecuteOptions catches any new key the CLI added
    # without wiring it through the canonical model.
    parsed = ExecuteOptions.model_validate(options_dict)
    assert parsed.revise is True
    assert parsed.max_iterations == 2
