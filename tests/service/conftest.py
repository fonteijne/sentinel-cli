"""Central fixtures for Command Center service tests.

Plan 05 introduces bearer-token auth. Every HTTP test goes through
``authed_client`` so only one place needs to know how to mint a test token.
The fixture pins ``SENTINEL_SERVICE_TOKEN`` + ``SENTINEL_DB_PATH`` via
``monkeypatch`` so the app factory's ``load_or_create_token()`` path
deterministically picks the env-var branch (no token file is touched).

Tests that need to exercise unauthenticated behaviour build their own
``TestClient`` without the ``Authorization`` header.
"""

from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

TEST_TOKEN = "test-token-for-command-center-tests-abc"


@pytest.fixture
def service_token() -> str:
    """The token string baked into ``authed_client``.

    Exposed so individual tests can construct request headers that match
    (``Authorization: Bearer <service_token>``) without duplicating the
    literal.
    """

    return TEST_TOKEN


@pytest.fixture
def authed_env(tmp_path, monkeypatch):
    """Env-level scaffolding used by every service test.

    * ``SENTINEL_SERVICE_TOKEN`` — wins over the file branch in
      ``load_or_create_token``, so no test touches ``~/.sentinel``.
    * ``SENTINEL_DB_PATH`` — per-test SQLite file; ``ensure_initialized``
      is called by the lifespan as the TestClient opens.
    * ``SENTINEL_LOGS_DIR`` — harmless but keeps plan 04's supervisor from
      scribbling into the real logs dir.
    """

    monkeypatch.setenv("SENTINEL_SERVICE_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("SENTINEL_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("SENTINEL_LOGS_DIR", str(tmp_path / "logs"))
    return tmp_path


@pytest.fixture
def authed_client(authed_env) -> Iterator[TestClient]:
    """A ``TestClient`` with the bearer header pre-attached.

    Imported inside the fixture so ``monkeypatch.setenv`` runs before the
    factory's ``load_or_create_token`` call — otherwise the first import
    would cache config/token state from the real environment.
    """

    from src.service.app import create_app

    app = create_app()
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
        yield c


@pytest.fixture
def unauthed_client(authed_env) -> Iterator[TestClient]:
    """A ``TestClient`` without any ``Authorization`` header.

    Used by auth tests that assert 401/403 on missing or malformed creds.
    """

    from src.service.app import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
