"""FastAPI request-scoped dependency injectors + service lifespan.

Every request gets its own SQLite connection. See plan 02 GOTCHAs: FastAPI's
sync endpoints run on a threadpool and sqlite3 connections cannot be shared
across threads. WAL + ``check_same_thread=False`` make per-request connections
safe; the ``try/finally`` is essential to avoid leaking file handles.

``command_center_lifespan`` is the single source of truth for supervisor
lifecycle and startup reconciliation — plan 05's composed ``create_app()``
attaches it to the FastAPI instance.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager
from typing import AsyncIterator, Iterator

from fastapi import Depends, FastAPI, Request

from src.core.execution.repository import ExecutionRepository
from src.core.execution.supervisor import Supervisor, periodic_reap
from src.core.persistence.db import connect, ensure_initialized

logger = logging.getLogger(__name__)


def get_db_conn() -> Iterator[sqlite3.Connection]:
    """Yield a fresh connection per request; close on exit."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def get_repo(
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> ExecutionRepository:
    return ExecutionRepository(conn)


def get_supervisor(request: Request) -> Supervisor:
    """Return the Supervisor attached by the lifespan.

    Raises 500 if the service wasn't composed with ``command_center_lifespan``
    (e.g. the fallback ``create_app()`` in ``src.service.app`` for tests that
    don't need write endpoints).
    """
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=500,
            detail="supervisor not configured on this app",
        )
    return supervisor


@asynccontextmanager
async def command_center_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the Supervisor + periodic reaper for the FastAPI app's lifetime.

    On setup failure the ``@asynccontextmanager`` protocol does NOT call the
    teardown branch — hence the explicit except: without it, a crash in
    ``adopt_or_reconcile_on_startup`` would leave the reaper task running and
    workers unreaped.
    """
    ensure_initialized()
    supervisor = Supervisor(connection_factory=connect)
    reaper_task: asyncio.Task | None = None

    try:
        adopted, reconciled = supervisor.adopt_or_reconcile_on_startup()
        logger.info(
            "command_center_lifespan: startup adopted=%d reconciled=%d",
            adopted,
            reconciled,
        )
        app.state.supervisor = supervisor
        reaper_task = asyncio.create_task(periodic_reap(supervisor))
        app.state.reaper_task = reaper_task
    except Exception:
        if reaper_task is not None:
            reaper_task.cancel()
        supervisor.shutdown()
        raise

    try:
        yield
    finally:
        if reaper_task is not None:
            reaper_task.cancel()
            try:
                await reaper_task
            except (asyncio.CancelledError, Exception):
                pass
        supervisor.shutdown()
