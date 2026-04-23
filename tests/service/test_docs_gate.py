"""Docs/OpenAPI gate tests (plan 06 Task 3).

`/docs` is app-level in FastAPI, so the router-level bearer dep from plan 05
doesn't apply. The only gate is constructor-time: pass ``docs_url=None``
(etc.) to hide the endpoint entirely. All three of ``/docs``, ``/redoc``,
``/openapi.json`` must flip together — Swagger UI fetches the openapi doc,
so a half-gated setup still leaks the schema.

These tests assert the truth table:

| ``SENTINEL_ENABLE_DOCS`` | ``/docs`` | ``/redoc`` | ``/openapi.json`` |
|---|---|---|---|
| unset (config default False) | 404 | 404 | 404 |
| ``"false"`` | 404 | 404 | 404 |
| ``"true"`` | 200 | 200 | 200 |
| ``"1"``  | 200 | 200 | 200 |

We deliberately rebuild the app per-test via ``TestClient(create_app())``
rather than reuse the ``authed_client`` fixture, because the decision is
made at FastAPI construction time — setting the env after the factory has
already run would be a no-op.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _build_client(monkeypatch, tmp_path, enable_docs_env):
    """Fresh create_app() with ``SENTINEL_ENABLE_DOCS`` pinned for this test.

    ``None`` deletes the env var (tests the config-default branch). The DB
    and logs dirs are scoped to ``tmp_path`` so parallel test workers don't
    collide on ``~/.sentinel``.
    """

    monkeypatch.setenv("SENTINEL_SERVICE_TOKEN", "docs-gate-test-token")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SENTINEL_LOGS_DIR", str(tmp_path / "logs"))
    if enable_docs_env is None:
        monkeypatch.delenv("SENTINEL_ENABLE_DOCS", raising=False)
    else:
        monkeypatch.setenv("SENTINEL_ENABLE_DOCS", enable_docs_env)

    from src.service.app import create_app

    app = create_app()
    return TestClient(app)


@pytest.mark.parametrize("env_value", [None, "false", "0", "no", "off", ""])
def test_docs_disabled_returns_404_for_all_three(env_value, monkeypatch, tmp_path):
    """Unset / falsy env → config default (False) → all three endpoints 404.

    Ensures the factory gates ``docs_url``, ``redoc_url``, and
    ``openapi_url`` as a group — a half-gated setup would still leak the
    schema via ``/openapi.json``.

    Subtle contract pinned here: env-set-to-empty-string is treated as
    falsy by ``_docs_enabled`` (the ``"".strip().lower() in (...)`` branch
    returns False), whereas env-unset falls through to the config default.
    Both paths currently agree because the committed config default is
    ``false`` — but if someone later flips ``service.enable_docs: true``
    without updating this test, the ``""`` case will diverge and force a
    deliberate choice.
    """
    with _build_client(monkeypatch, tmp_path, env_value) as c:
        assert c.get("/docs").status_code == 404
        assert c.get("/redoc").status_code == 404
        assert c.get("/openapi.json").status_code == 404


@pytest.mark.parametrize("env_value", ["true", "TRUE", "1", "yes", "on"])
def test_docs_enabled_returns_200_for_all_three(env_value, monkeypatch, tmp_path):
    """Truthy ``SENTINEL_ENABLE_DOCS`` → all three endpoints reachable.

    Parameterised over the truthy-string variants `_docs_enabled` accepts,
    so the env-parsing contract is pinned here rather than re-derived.
    """
    with _build_client(monkeypatch, tmp_path, env_value) as c:
        assert c.get("/docs").status_code == 200
        assert c.get("/redoc").status_code == 200
        openapi = c.get("/openapi.json")
        assert openapi.status_code == 200
        # Sanity: the body is actually the openapi schema, not an empty 200.
        body = openapi.json()
        assert body.get("openapi", "").startswith("3.")
        assert body["info"]["title"] == "Sentinel Command Center API"


def test_env_overrides_config_default(monkeypatch, tmp_path):
    """Env var precedence: ``SENTINEL_ENABLE_DOCS=true`` wins over
    ``service.enable_docs: false`` (the committed config default).

    Guards the precedence order documented in `_docs_enabled`: env > config
    > hard default. Regression test against someone swapping the two.
    """
    with _build_client(monkeypatch, tmp_path, "true") as c:
        assert c.get("/docs").status_code == 200


def test_health_reachable_regardless_of_docs_gate(monkeypatch, tmp_path):
    """`/health` is orthogonal to docs gating — it must work either way.

    Compose healthchecks and Traefik readiness probes depend on this; a
    regression that accidentally gated `/health` alongside `/docs` would
    mark containers unhealthy in prod.
    """
    with _build_client(monkeypatch, tmp_path, "false") as c:
        assert c.get("/health").status_code == 200
    with _build_client(monkeypatch, tmp_path, "true") as c:
        assert c.get("/health").status_code == 200
