"""Persistence-layer tests: connection pragmas + migration idempotency."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.persistence import apply_migrations, connect


def _all_table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def test_apply_migrations_is_idempotent() -> None:
    """Applying migrations twice must not double-record or error."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    apply_migrations(conn)
    first = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]

    apply_migrations(conn)
    second = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]

    assert first == second
    assert first >= 2  # at least 001_init and 003_postmortems


def test_pragmas_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """connect() must enable WAL journal_mode and foreign_keys=ON.

    Uses a temp-file path because WAL is silently downgraded on :memory: DBs.
    """
    db_path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))

    conn = connect()

    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

    assert journal_mode.lower() == "wal"
    assert foreign_keys == 1


def test_executions_events_agent_results_tables_exist() -> None:
    """After migrations, the Phase 1 foundation tables must exist."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    apply_migrations(conn)

    tables = _all_table_names(conn)
    for required in (
        "executions",
        "events",
        "agent_results",
        "schema_migrations",
        "postmortems",
    ):
        assert required in tables, f"missing table: {required}"
