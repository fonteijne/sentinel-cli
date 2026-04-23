"""FastAPI request-scoped dependency injectors.

Every request gets its own SQLite connection. See plan 02 GOTCHAs: FastAPI's
sync endpoints run on a threadpool and sqlite3 connections cannot be shared
across threads. WAL + ``check_same_thread=False`` make per-request connections
safe; the ``try/finally`` is essential to avoid leaking file handles.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Iterator

from fastapi import Depends

from src.core.execution.repository import ExecutionRepository
from src.core.persistence.db import connect

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
