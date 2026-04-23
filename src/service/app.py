"""FastAPI factory for the Sentinel Command Center.

Plan 05 replaces this with a composed factory that wraps the plan 02/03/04
routers behind bearer auth. This factory stays minimal: it wires every router
landed so far + the command-center lifespan so read/write endpoints and the
supervisor all function end-to-end in tests and dev.
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI

from src.service.deps import command_center_lifespan, get_db_conn
from src.service.routes import commands, executions, stream

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Build a fresh FastAPI app with read+write routes, /health, and lifespan.

    ``command_center_lifespan`` owns ``ensure_initialized()`` and the
    Supervisor; a second ``ensure_initialized()`` at module import would be a
    redundant round-trip and also racy with the lifespan's own startup path.
    """
    app = FastAPI(
        title="Sentinel Command Center API",
        version="0.1",
        lifespan=command_center_lifespan,
    )
    app.include_router(executions.router)
    app.include_router(stream.router)
    app.include_router(commands.router)

    @app.get("/health")
    def health(conn=Depends(get_db_conn)) -> dict:  # type: ignore[no-untyped-def]
        conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "db": "ok"}

    return app
