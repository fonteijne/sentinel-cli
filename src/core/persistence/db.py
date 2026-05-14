"""SQLite connection + migration runner for Sentinel.

Design constraints (see docs/agent-learning-from-feedback-2026-05-03.md and
plan §Patterns SQLITE_MIGRATION_PATTERN):

  - WAL journal mode is required so readers don't block writers (the event bus
    persists then publishes inside a single connection while the CLI may read).
  - Foreign keys are enforced per-connection (SQLite default is OFF).
  - Migrations are applied per-statement inside an explicit BEGIN IMMEDIATE /
    COMMIT. ``executescript()`` is forbidden because it auto-commits, which
    would silently break atomicity if a later statement fails.
  - Migrations are forward-only and idempotent: each file's basename (without
    extension) is recorded in ``schema_migrations`` and skipped on re-run.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_DEFAULT_DB_PATH = "~/.sentinel/sentinel.db"


def _resolve_path(path: Optional[str]) -> Path:
    """Resolve the DB path. Precedence: explicit arg > env > default."""
    raw = path or os.getenv("SENTINEL_DB_PATH") or _DEFAULT_DB_PATH
    if raw == ":memory:":
        return Path(":memory:")
    return Path(raw).expanduser().resolve()


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """Open a SQLite connection with the standard pragmas applied.

    Honors ``SENTINEL_DB_PATH`` env var. Defaults to ``~/.sentinel/sentinel.db``.
    Creates the parent directory if it does not exist. If the resolved path
    points at an existing non-file (e.g. directory), raises ValueError.
    """
    resolved = _resolve_path(path)

    if str(resolved) == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        if resolved.exists() and not resolved.is_file():
            raise ValueError(
                f"SENTINEL_DB_PATH resolves to {resolved} which is not a regular file"
            )
        resolved.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(resolved))

    conn.row_factory = sqlite3.Row
    # WAL is a no-op on :memory: but harmless; cursor.execute returns the resulting mode.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _list_migration_files() -> List[Path]:
    if not _MIGRATIONS_DIR.exists():
        return []
    files = [p for p in _MIGRATIONS_DIR.glob("*.sql") if p.is_file()]
    # Numeric ordering by leading digits so 003 sorts after 002 even if 010 exists.
    def _key(p: Path) -> tuple:
        m = re.match(r"^(\d+)", p.stem)
        return (int(m.group(1)) if m else 10**9, p.stem)
    return sorted(files, key=_key)


def _strip_line_comments(sql: str) -> str:
    """Remove ``-- ...`` line comments. Preserves all other content verbatim.

    SQL string literals in migration files are single-line and do not contain
    ``--`` sequences, so a simple line-by-line strip is safe. Block comments
    (``/* */``) are not used in this project's migrations; if introduced, this
    function needs to grow.
    """
    cleaned_lines: List[str] = []
    for line in sql.splitlines():
        idx = line.find("--")
        if idx == -1:
            cleaned_lines.append(line)
        else:
            cleaned_lines.append(line[:idx])
    return "\n".join(cleaned_lines)


def _split_statements(sql: str) -> List[str]:
    """Split SQL on ';' boundaries after stripping line comments.

    Stripping comments first is important: a ``;`` inside a ``--`` comment
    must not cause a spurious split (this happens in human-written migration
    headers).

    SQLite migrations in this project do not use stored procedures, triggers
    with embedded ';' inside string literals, or block comments — if any
    arrive, this splitter needs to grow.
    """
    cleaned = _strip_line_comments(sql)
    statements: List[str] = []
    for chunk in cleaned.split(";"):
        stripped = chunk.strip()
        if not stripped:
            continue
        statements.append(stripped)
    return statements


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply all pending migration files in numeric order.

    Each migration is identified by the file stem (e.g. ``003_postmortems``).
    Already-applied versions are skipped. Each migration runs in its own
    explicit transaction (``BEGIN IMMEDIATE`` / ``COMMIT``), with per-statement
    ``execute()`` calls — never ``executescript()``.
    """
    _ensure_schema_migrations_table(conn)

    applied = {
        row["version"]
        for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
    }

    for path in _list_migration_files():
        version = path.stem
        if version in applied:
            continue

        sql = path.read_text(encoding="utf-8")
        statements = _split_statements(sql)

        # Explicit BEGIN IMMEDIATE — see plan §Patterns and the d75d276
        # commit note. Do NOT use executescript().
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
