"""Tests for the ``sentinel postmortems list`` CLI subcommand.

Phase 2A Task 11 (plan ref: phase-2a-pitfalls-visible.plan.md).

These tests drive the CLI through Click's :class:`CliRunner` (matching the
idiom in ``tests/test_cli_info_attachment.py``). The CLI's ``connect()`` is
re-pointed at a tmp-path SQLite file via the ``SENTINEL_DB_PATH`` env var,
which ``src.core.persistence.db.connect`` honors (verified by reading the
helper). This is preferred over patching ``connect`` because it exercises the
real resolution path the CLI uses in production.

We seed the DB by calling :func:`apply_migrations` + :func:`insert_postmortem`
in-process before invoking the CLI; the CLI opens its OWN connection to the
same on-disk file and reads the rows back. Both ends share WAL — readers don't
block writers — so this works reliably without explicit fsync.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest
from click.testing import CliRunner

from src.cli import cli
from src.core.persistence import (
    apply_migrations,
    connect,
    insert_postmortem,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """tmp_path-backed SQLite DB the CLI will resolve via SENTINEL_DB_PATH."""
    path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(path))

    # Apply migrations + seed the parent execution row so postmortem inserts
    # don't trip the FK to executions(id).
    conn = connect(str(path))
    try:
        apply_migrations(conn)
        conn.execute(
            "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "exec-cli",
                "TEST-1",
                "execute",
                "running",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    yield path


def _seed(
    db_path: Path,
    *,
    stack_type: str = "drupal",
    agent: str = "drupal_developer",
    failure_signature: str = "phpstan.notFound undefined method",
    confidence: int = 80,
) -> int:
    """Insert one postmortem; return its rowid."""
    conn = connect(str(db_path))
    try:
        return insert_postmortem(
            conn,
            execution_id="exec-cli",
            stack_type=stack_type,
            agent=agent,
            failure_signature=failure_signature,
            context_excerpt="ctx",
            fix_summary=None,
            provenance="auto",
            confidence=confidence,
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Empty table
# ---------------------------------------------------------------------------


def test_postmortems_list_empty(runner: CliRunner, db_path: Path) -> None:
    """Empty postmortems table → "No postmortems matched." with exit code 0."""
    result = runner.invoke(cli, ["postmortems", "list"])

    assert result.exit_code == 0, result.output
    assert "No postmortems matched." in result.output


# ---------------------------------------------------------------------------
# Listing rows
# ---------------------------------------------------------------------------


def test_postmortems_list_with_rows(runner: CliRunner, db_path: Path) -> None:
    """Three rows present → CLI lists three rows including each failure_signature."""
    sigs = [
        "phpstan.notFound undefined method foo",
        "phpunit::failed_assertion::bar",
        "composer.dep::version_conflict::baz",
    ]
    for sig in sigs:
        _seed(db_path, failure_signature=sig)

    result = runner.invoke(cli, ["postmortems", "list"])

    assert result.exit_code == 0, result.output
    assert "Postmortems (3)" in result.output
    for sig in sigs:
        assert sig in result.output, f"missing signature in output: {sig}"


# ---------------------------------------------------------------------------
# --stack filter
# ---------------------------------------------------------------------------


def test_postmortems_list_with_stack_filter(
    runner: CliRunner, db_path: Path
) -> None:
    """``--stack drupal`` returns only drupal rows; python rows excluded."""
    _seed(
        db_path,
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="drupal.only.signature",
    )
    _seed(
        db_path,
        stack_type="python",
        agent="python_developer",
        failure_signature="python.only.signature",
    )

    result = runner.invoke(cli, ["postmortems", "list", "--stack", "drupal"])

    assert result.exit_code == 0, result.output
    assert "drupal.only.signature" in result.output
    assert "python.only.signature" not in result.output
    assert "Postmortems (1)" in result.output


# ---------------------------------------------------------------------------
# --limit
# ---------------------------------------------------------------------------


def test_postmortems_list_with_limit(runner: CliRunner, db_path: Path) -> None:
    """``--limit 2`` outputs only 2 entries even though 5 exist."""
    for i in range(5):
        _seed(db_path, failure_signature=f"sig.{i}")

    result = runner.invoke(cli, ["postmortems", "list", "--limit", "2"])

    assert result.exit_code == 0, result.output
    assert "Postmortems (2)" in result.output
    # Count signature lines: each postmortem renders a line containing "sig."
    signature_line_count = sum(
        1 for line in result.output.splitlines() if "sig." in line
    )
    assert signature_line_count == 2, (
        f"expected 2 signature lines, got {signature_line_count}\n{result.output}"
    )


# ---------------------------------------------------------------------------
# --min-confidence
# ---------------------------------------------------------------------------


def test_postmortems_list_with_min_confidence(
    runner: CliRunner, db_path: Path
) -> None:
    """``--min-confidence 80`` only returns rows with confidence >= 80."""
    _seed(db_path, failure_signature="sig.low", confidence=50)
    _seed(db_path, failure_signature="sig.mid", confidence=70)
    _seed(db_path, failure_signature="sig.high", confidence=90)

    result = runner.invoke(
        cli, ["postmortems", "list", "--min-confidence", "80"]
    )

    assert result.exit_code == 0, result.output
    assert "sig.high" in result.output
    assert "sig.low" not in result.output
    assert "sig.mid" not in result.output
    assert "Postmortems (1)" in result.output


# ---------------------------------------------------------------------------
# Superseded rows excluded
# ---------------------------------------------------------------------------


def test_postmortems_list_excludes_superseded(
    runner: CliRunner, db_path: Path
) -> None:
    """A postmortem with ``superseded_by`` set must be excluded from the listing."""
    old_id = _seed(db_path, failure_signature="sig.old", confidence=80)
    new_id = _seed(db_path, failure_signature="sig.new", confidence=80)

    # Mark the old row as superseded by the new one. Direct UPDATE matches the
    # escape-hatch pattern used in tests/core/test_postmortems.py — the helper
    # API is append-only by design (Decision 4).
    conn = connect(str(db_path))
    try:
        conn.execute(
            "UPDATE postmortems SET superseded_by = ? WHERE id = ?",
            (new_id, old_id),
        )
        conn.commit()
    finally:
        conn.close()

    result = runner.invoke(cli, ["postmortems", "list"])

    assert result.exit_code == 0, result.output
    assert "sig.new" in result.output
    assert "sig.old" not in result.output, (
        "superseded row leaked into listing"
    )
    assert "Postmortems (1)" in result.output
