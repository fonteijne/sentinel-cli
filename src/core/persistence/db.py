"""SQLite connection + migration runner for Command Center persistence.

No module-level singleton connection: every caller gets its own `sqlite3.Connection`
configured with WAL journaling, 30s busy timeout, and autocommit (isolation_level=None)
so writers explicitly `BEGIN IMMEDIATE`/`COMMIT` around multi-statement operations.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import stat as _stat
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_SQLITE_VERSION = (3, 38)
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_db_path() -> Path:
    """Resolve DB path with env override and regular-file validation.

    Order:
        1. `SENTINEL_DB_PATH` env var (user-controlled; validated)
        2. `~/.sentinel/sentinel.db`

    Raises:
        RuntimeError: if the resolved path exists and is not a regular file
            (e.g. `/dev/null`, block device, FIFO).
    """
    raw = os.environ.get("SENTINEL_DB_PATH")
    if raw:
        path = Path(raw).expanduser().resolve(strict=False)
    else:
        path = Path.home() / ".sentinel" / "sentinel.db"

    if path.exists():
        mode = path.stat().st_mode
        if not _stat.S_ISREG(mode):
            raise RuntimeError(
                f"SENTINEL_DB_PATH must resolve to a regular file, got {path} "
                f"(mode={mode:o})"
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect() -> sqlite3.Connection:
    """Return a new, fully-configured SQLite connection.

    Every caller gets its own connection — connections are not shared across
    threads or processes. `isolation_level=None` puts us in autocommit mode;
    writers must wrap multi-statement work in `BEGIN IMMEDIATE`/`COMMIT`.
    """
    conn = sqlite3.connect(
        str(get_db_path()),
        timeout=30,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _assert_sqlite_capabilities(conn: sqlite3.Connection) -> None:
    parts = sqlite3.sqlite_version.split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(
            f"Unable to parse sqlite3 version string: {sqlite3.sqlite_version}"
        ) from exc

    if (major, minor) < MIN_SQLITE_VERSION:
        raise RuntimeError(
            f"SQLite >= {MIN_SQLITE_VERSION[0]}.{MIN_SQLITE_VERSION[1]} required "
            f"for json_insert('$[#]') append syntax; "
            f"found {sqlite3.sqlite_version}. Upgrade Python / sqlite3."
        )

    # JSON1 compile-time feature check.
    conn.execute("SELECT json_extract('{}', '$.x')")


def run_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply any unseen migration files in sorted order.

    Returns the list of newly-applied versions (empty if everything was current).
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )

    applied = {
        row[0]
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }

    newly_applied: list[int] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        leading = "".join(ch for ch in path.stem.split("_", 1)[0] if ch.isdigit())
        if not leading:
            logger.warning("Skipping migration without leading digits: %s", path.name)
            continue
        version = int(leading)
        if version in applied:
            continue

        sql = path.read_text(encoding="utf-8")
        logger.info("Applying migration %03d (%s)", version, path.name)
        # Note: sqlite3.Connection.executescript() implicitly COMMITs any pending
        # transaction before running, so we split statements manually and run them
        # inside an explicit BEGIN IMMEDIATE / COMMIT block for atomicity.
        statements = [s.strip() for s in sql.split(";") if s.strip()]
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        newly_applied.append(version)

    return newly_applied


def ensure_initialized() -> None:
    """Idempotent: open a connection, check capabilities, run migrations, close."""
    conn = connect()
    try:
        _assert_sqlite_capabilities(conn)
        run_migrations(conn)
    finally:
        conn.close()
