"""Tests for src.core.persistence.db — migrations, pragmas, path validation."""

from __future__ import annotations

import pytest

from src.core.persistence import connect, ensure_initialized


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Per-test SQLite DB rooted in tmp_path. Closes the connection on teardown."""
    db_path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    ensure_initialized()
    conn = connect()
    yield conn
    conn.close()


def _table_names(conn) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {row[0] for row in rows}


def test_migration_creates_expected_tables(db):
    names = _table_names(db)
    for expected in ("executions", "events", "agent_results", "schema_migrations"):
        assert expected in names, f"expected table {expected} missing; got {names}"


def test_migration_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))

    ensure_initialized()
    ensure_initialized()

    conn = connect()
    try:
        rows = conn.execute(
            "SELECT version FROM schema_migrations WHERE version = 1"
        ).fetchall()
        assert len(rows) == 1, f"expected exactly 1 v1 row, got {len(rows)}"
    finally:
        conn.close()


def test_wal_mode_enabled(db):
    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"


def test_foreign_keys_enabled(db):
    fk = db.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_sentinel_db_path_rejects_non_regular_file(monkeypatch):
    monkeypatch.setenv("SENTINEL_DB_PATH", "/dev/null")
    with pytest.raises(RuntimeError):
        connect()
