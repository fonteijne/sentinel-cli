"""FastAPI factory for the Sentinel Command Center (plan 05).

Plan 05 owns the final factory composition. The shape is:

* ``/health`` — unauthenticated, for container health probes and future
  docker-compose healthchecks.
* Read router — bearer auth, no rate limit. A dashboard polling GET
  endpoints regularly is expected to exceed 30/minute; read is cheap.
* Write router — bearer auth + per-token concurrent/minute rate limit +
  audit log on every call. This is where state changes (start, cancel,
  retry) and where a leaked token would hurt most.
* Stream (WS) router — bearer auth via a WebSocket-specific dep (different
  raise path; HTTPException does nothing mid-handshake).

Single-process by design: uvicorn receives the app *instance*, not a factory
string. Supervisor state and SQLite connections are per-process; a multi-
worker deploy would corrupt both and is deliberately out of scope.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Depends, FastAPI
from starlette.middleware.cors import CORSMiddleware

from src.config_loader import get_config
from src.service.auth import (
    audit_write,
    load_or_create_token,
    require_token,
    require_token_and_write_slot,
    require_token_ws,
)
from src.service.deps import command_center_lifespan, get_db_conn
from src.service.rate_limit import TokenRateLimiter
from src.service.routes import commands, executions, stream

logger = logging.getLogger(__name__)


def _validate_cors(origins: list[str]) -> None:
    """Startup-time validation of the CORS allowlist.

    The combo ``allow_credentials=True`` + ``allow_origins=["*"]`` is a silent
    browser footgun — browsers reject it and the user sees opaque CORS errors.
    Fail loudly at startup instead.
    """

    if "*" in origins:
        raise RuntimeError(
            "service.cors_origins=['*'] is incompatible with "
            "allow_credentials=True; browsers silently reject. Use explicit "
            "origins."
        )


def _docs_enabled(cfg) -> bool:  # type: ignore[no-untyped-def]
    """Decide whether to expose ``/docs`` + ``/redoc`` + ``/openapi.json``.

    Precedence: env ``SENTINEL_ENABLE_DOCS`` (truthy string) wins, then
    ``service.enable_docs`` from config, then ``False``. Prod compose sets the
    env to ``false`` by default so the schema does not leak; dev compose sets
    it to ``true`` so ``http://localhost:8787/docs`` just works.
    """

    raw = os.environ.get("SENTINEL_ENABLE_DOCS")
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(cfg.get("service.enable_docs", False))


def create_app() -> FastAPI:
    """Build a fully-composed Command Center app with auth + rate limit + CORS.

    ``command_center_lifespan`` owns ``ensure_initialized()`` and Supervisor
    lifecycle. We attach ``service_token`` and ``rate_limiter`` to
    ``app.state`` *before* lifespan runs — the auth deps read them from the
    same place during request handling.
    """

    cfg = get_config()

    # Gate /docs, /redoc AND /openapi.json together. Swagger UI fetches
    # /openapi.json, so leaving that endpoint open while hiding /docs still
    # leaks the full schema. Setting these constructor kwargs to None removes
    # the endpoints entirely (404, not 401) because /docs is app-level and
    # the router-level bearer dep from plan 05 doesn't apply to it.
    docs_on = _docs_enabled(cfg)
    app = FastAPI(
        title="Sentinel Command Center API",
        version="0.1",
        lifespan=command_center_lifespan,
        docs_url="/docs" if docs_on else None,
        redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
    )
    app.state.service_token = load_or_create_token()
    app.state.rate_limiter = TokenRateLimiter(
        max_concurrent=int(cfg.get("service.rate_limits.max_concurrent", 3)),
        max_per_minute=int(cfg.get("service.rate_limits.max_per_minute", 30)),
    )

    cors_origins = cfg.get("service.cors_origins", []) or []
    _validate_cors(cors_origins)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=[
                "authorization",
                "content-type",
                "idempotency-key",
            ],
        )

    # Unauthenticated: container health probes only.
    #
    # This is a DEEP probe: it opens a SQLite connection and runs SELECT 1.
    # If the DB is missing, locked, or otherwise unreachable, /health returns
    # 500. That's intentional for docker-compose and Kubernetes readiness
    # probes — the service isn't useful without the DB — but operators should
    # not conflate /health with a process-liveness check. A process-liveness
    # check should use a TCP port check or a future /live endpoint that
    # doesn't touch the DB.
    @app.get("/health")
    def health(conn=Depends(get_db_conn)) -> dict:  # type: ignore[no-untyped-def]
        conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "db": "ok"}

    # Read-only HTTP: bearer auth, no rate limit (polling is expected).
    http_read_protected = APIRouter(dependencies=[Depends(require_token)])
    http_read_protected.include_router(executions.router)
    app.include_router(http_read_protected)

    # Write HTTP: bearer auth + per-token rate limit + audit log.
    # The write dep is a generator that reserves a rate-limit slot on entry
    # and releases it in a ``finally`` — success and failure both release.
    http_write_protected = APIRouter(
        dependencies=[
            Depends(require_token_and_write_slot),
            Depends(audit_write),
        ]
    )
    http_write_protected.include_router(commands.router)
    app.include_router(http_write_protected)

    # WebSocket: separate dep because Starlette raises differently on a WS
    # handshake. The WS dep closes with code 1008 → handshake-level 403.
    ws_protected = APIRouter(dependencies=[Depends(require_token_ws)])
    ws_protected.include_router(stream.router)
    app.include_router(ws_protected)

    return app
