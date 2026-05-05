"""Lifespan-level tests for service discovery file write/remove.

These tests cover the integration between ``command_center_lifespan`` and
``src.service.discovery``: the lifespan writes the discovery file when
``app.state.discovery_port`` is set before the ASGI startup phase, removes
it on teardown, and refuses to leave a stale file behind if startup crashes.

``XDG_STATE_HOME`` is pinned to ``tmp_path`` in every test so the real
``~/.local/state/sentinel/`` is never touched. We reuse the ``authed_env``
fixture from ``conftest.py`` for ``SENTINEL_SERVICE_TOKEN`` + ``SENTINEL_DB_PATH``.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from src.service import discovery


@pytest.fixture
def pinned_state(authed_env, monkeypatch, tmp_path):
    """Pin XDG_STATE_HOME → tmp_path; return the expected discovery path."""

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    return tmp_path / "xdg" / "sentinel" / "service.json"


def test_lifespan_writes_and_removes_discovery(pinned_state, service_token):
    """discovery_port set → file written on enter, removed on exit."""

    from src.service.app import create_app

    app = create_app()
    app.state.discovery_port = 54321

    assert not pinned_state.exists()

    with TestClient(app) as client:
        # Inside the context, the startup phase has run; discovery must exist.
        assert pinned_state.exists(), "discovery file should be written on startup"
        payload = json.loads(pinned_state.read_text())
        assert payload["port"] == 54321
        assert payload["token"] == service_token
        assert payload["pid"] > 0
        # Sanity check: request still works (basic lifespan didn't break).
        r = client.get("/health")
        assert r.status_code == 200
        # app.state.discovery is populated for introspection
        assert app.state.discovery is not None
        assert app.state.discovery.port == 54321

    # After the TestClient context exits, the lifespan teardown must have
    # removed the discovery file.
    assert not pinned_state.exists(), "discovery file should be removed on shutdown"


def test_lifespan_skips_discovery_when_port_not_set(
    pinned_state, caplog
):
    """No discovery_port → no file written, no crash, warning logged."""

    from src.service.app import create_app

    app = create_app()
    # Intentionally do NOT set app.state.discovery_port.

    with caplog.at_level(logging.WARNING, logger="src.service.deps"):
        with TestClient(app) as client:
            assert not pinned_state.exists()
            r = client.get("/health")
            assert r.status_code == 200
            assert getattr(app.state, "discovery", None) is None

    assert any(
        "discovery_port not set" in rec.message
        for rec in caplog.records
    ), f"expected 'discovery_port not set' warning, got {[r.message for r in caplog.records]}"
    # And still no file after teardown.
    assert not pinned_state.exists()


def test_lifespan_startup_failure_does_not_leave_discovery_file(
    pinned_state, monkeypatch
):
    """If write_discovery raises, TestClient.__enter__ bubbles and no file survives."""

    from src.service import deps

    def boom(*args, **kwargs):
        raise RuntimeError("simulated write_discovery failure")

    monkeypatch.setattr(deps, "write_discovery", boom)

    from src.service.app import create_app

    app = create_app()
    app.state.discovery_port = 54322

    with pytest.raises(RuntimeError, match="simulated write_discovery failure"):
        with TestClient(app):
            pass  # pragma: no cover — startup should raise before body

    # Defensive remove_discovery in the except branch guarantees no stale file
    # even though write_discovery didn't complete.
    assert not pinned_state.exists()
    # Guardrail: the dataclass path still resolves where we expected it.
    assert discovery.discovery_path() == pinned_state
