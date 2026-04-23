"""FastAPI factory for the Sentinel Command Center read API.

Plan 05 replaces this with a composed factory that wraps the plan 02/03/04
routers behind bearer auth and attaches the plan 04 command lifespan. Keep
this factory dumb and stable: it's the fallback used in isolated tests.
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI

from src.core.persistence.db import ensure_initialized
from src.service.deps import get_db_conn
from src.service.routes import executions

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Build a fresh FastAPI app with read routes and /health.

    ``ensure_initialized()`` runs pending migrations. Idempotent and cheap;
    makes ``create_app()`` self-sufficient for tests without the CLI entry.
    """
    ensure_initialized()

    app = FastAPI(title="Sentinel Command Center API", version="0.1")
    app.include_router(executions.router)

    @app.get("/health")
    def health(conn=Depends(get_db_conn)) -> dict:  # type: ignore[no-untyped-def]
        conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "db": "ok"}

    return app
