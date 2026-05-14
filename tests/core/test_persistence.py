"""Persistence-layer tests: connection pragmas + migration idempotency."""

from __future__ import annotations

import logging
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


def test_resolve_path_logs_resolved_path_on_first_connect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """First connect() per process must emit one INFO line with the resolved path + source."""
    from src.core.persistence import db as db_module

    monkeypatch.setattr(db_module, "_path_logged", False)
    db_path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    caplog.set_level(logging.INFO, logger="src.core.persistence.db")

    conn = connect()
    try:
        info_records = [
            r
            for r in caplog.records
            if r.name == "src.core.persistence.db" and r.levelno == logging.INFO
        ]
        assert len(info_records) == 1, [r.getMessage() for r in info_records]
        assert str(db_path.resolve()) in info_records[0].getMessage()
        assert "source=env" in info_records[0].getMessage()
    finally:
        conn.close()


def test_resolve_path_logs_only_once_per_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second connect() in the same process must NOT re-log."""
    from src.core.persistence import db as db_module

    monkeypatch.setattr(db_module, "_path_logged", False)
    db_path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    caplog.set_level(logging.INFO, logger="src.core.persistence.db")

    conn1 = connect()
    conn1.close()
    caplog.clear()

    conn2 = connect()
    try:
        db_records = [
            r for r in caplog.records if r.name == "src.core.persistence.db"
        ]
        assert db_records == [], [r.getMessage() for r in db_records]
    finally:
        conn2.close()


def test_resolve_path_warns_on_unusual_suffix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-{.db,.sqlite,.sqlite3} suffix must produce one WARNING (not block)."""
    from src.core.persistence import db as db_module

    monkeypatch.setattr(db_module, "_path_logged", False)
    db_path = tmp_path / "sentinel.txt"  # wrong extension
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    caplog.set_level(logging.INFO, logger="src.core.persistence.db")

    conn = connect()
    try:
        warning_records = [
            r
            for r in caplog.records
            if r.name == "src.core.persistence.db" and r.levelno == logging.WARNING
        ]
        assert len(warning_records) == 1
        assert ".txt" in warning_records[0].getMessage()
        # Connection still opened -- never blocking.
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


def test_resolve_path_memory_db_is_silent(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """In-memory connections must not produce any audit log."""
    from src.core.persistence import db as db_module

    monkeypatch.setattr(db_module, "_path_logged", False)
    monkeypatch.setenv("SENTINEL_DB_PATH", ":memory:")
    caplog.set_level(logging.INFO, logger="src.core.persistence.db")

    conn = connect()
    try:
        db_records = [
            r for r in caplog.records if r.name == "src.core.persistence.db"
        ]
        assert db_records == [], [r.getMessage() for r in db_records]
    finally:
        conn.close()


def test_resolve_path_raises_valueerror_on_symlink_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A symlink loop should surface as ValueError (not opaque OSError)."""
    from src.core.persistence import db as db_module

    monkeypatch.setattr(db_module, "_path_logged", False)

    loop_a = tmp_path / "a"
    loop_b = tmp_path / "b"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)

    monkeypatch.setenv("SENTINEL_DB_PATH", str(loop_a / "sentinel.db"))

    with pytest.raises(ValueError, match="SENTINEL_DB_PATH could not be resolved"):
        connect()
