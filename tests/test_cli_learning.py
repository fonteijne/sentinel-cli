"""Tests for the ``sentinel learning`` CLI subcommand group.

Phase 2C Task 15 (plan ref: phase-2c-promotion-path.plan.md). Covers the five
subcommands — ``extract``, ``propose``, ``mark-merged``, ``revoke``, ``list``
— focused on:

  * dry-run paths producing zero side-effects,
  * feature-flag gating (``EXTRACTION_ENABLED`` / ``OVERLAY_PROPOSER_ENABLED``)
    with the correct exit code (2),
  * config errors from the proposer when ``sentinel.repo_project_path`` is
    unset on a non-dry-run path,
  * the audit-trail-significant ``mark-merged`` and ``revoke`` flipping status
    correctly and erroring on illegal transitions,
  * ``list`` with status / scope filters.

The propose non-dry-run write path (real subprocess git + GitLab call) is
exercised by ``tests/integration/test_phase2c_promotion.py``; here we only
test the dry-run + flag-off + config-missing surfaces, which is what the CLI
adds on top of ``tests/core/test_propose_overlay.py``.

The CLI's ``connect()`` is re-pointed at a tmp-path SQLite file via the
``SENTINEL_DB_PATH`` env var (same idiom as ``tests/test_cli_postmortems.py``).
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
    """Empty migrated DB at a tmp path the CLI resolves via SENTINEL_DB_PATH."""
    path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(path))

    conn = connect(str(path))
    try:
        apply_migrations(conn)
    finally:
        conn.close()

    yield path


def _seed_executions(db: Path, exec_specs: list[tuple[str, str]]) -> None:
    """Insert one ``executions`` row per (id, ticket_id) pair."""
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(str(db))
    try:
        for exec_id, ticket in exec_specs:
            conn.execute(
                "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
                "VALUES (?, ?, 'developer', 'completed', ?)",
                (exec_id, ticket, now),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_postmortem(
    db: Path,
    *,
    execution_id: str,
    failure_signature: str,
    stack_type: str = "drupal",
    agent: str = "drupal_developer",
    confidence: int = 60,
) -> int:
    conn = connect(str(db))
    try:
        return insert_postmortem(
            conn,
            execution_id=execution_id,
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


@pytest.fixture
def db_path_with_postmortems(db_path: Path) -> Path:
    """Three postmortems sharing a non-symptomatic signature, two projects.

    With defaults (min_observations=3, min_projects=2), this seed yields one
    accepted cluster (confidence 50 + 10*min(5, 2) + 5*min(3, 1) = 75).
    """
    _seed_executions(
        db_path,
        [
            ("exec-acme-1", "ACME-100"),
            ("exec-acme-2", "ACME-200"),
            ("exec-bravo-1", "BRAVO-300"),
        ],
    )
    sig = "phpunit::DrupalDemo::testFails"
    _seed_postmortem(db_path, execution_id="exec-acme-1", failure_signature=sig)
    _seed_postmortem(db_path, execution_id="exec-acme-2", failure_signature=sig)
    _seed_postmortem(db_path, execution_id="exec-bravo-1", failure_signature=sig)
    return db_path


def _seed_feedback_rule(
    db: Path,
    *,
    signature: str = "phpunit::DrupalDemo::testFails",
    scope: str = "drupal",
    agent_target: str = "drupal_developer",
    status: str = "probation",
    confidence: int = 80,
    proposed_at: str | None = None,
    promoted_to_overlay_sha: str | None = None,
    promoted_by: str | None = None,
    revoked_by: str | None = None,
    revocation_reason: str | None = None,
    first_postmortem_id: int | None = None,
    last_postmortem_id: int | None = None,
) -> int:
    """Insert a feedback_rules row directly. Returns the rowid."""
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(str(db))
    try:
        cursor = conn.execute(
            """
            INSERT INTO feedback_rules (
                signature, scope, agent_target, rule_text, status, confidence,
                observation_count, distinct_projects,
                first_postmortem_id, last_postmortem_id,
                proposed_at, promoted_to_overlay_sha, promoted_by,
                promoted_at, revoked_by, revoked_at, revocation_reason,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 3, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signature,
                scope,
                agent_target,
                signature,
                status,
                confidence,
                first_postmortem_id,
                last_postmortem_id,
                proposed_at,
                promoted_to_overlay_sha,
                promoted_by,
                now if promoted_by else None,
                revoked_by,
                now if revoked_by else None,
                revocation_reason,
                now,
                now,
            ),
        )
        conn.commit()
        rowid = cursor.lastrowid
        assert rowid is not None
        return int(rowid)
    finally:
        conn.close()


@pytest.fixture
def db_path_with_promotable_rule(db_path_with_postmortems: Path) -> Path:
    """A probation rule referencing the seeded postmortems, ready for propose."""
    _seed_feedback_rule(
        db_path_with_postmortems,
        status="probation",
        confidence=80,
        first_postmortem_id=1,
        last_postmortem_id=3,
    )
    return db_path_with_postmortems


# ---------------------------------------------------------------------------
# learning extract
# ---------------------------------------------------------------------------


def test_extract_dry_run_prints_clusters(
    runner: CliRunner, db_path_with_postmortems: Path
) -> None:
    result = runner.invoke(cli, ["learning", "extract", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "phpunit::DrupalDemo::testFails" in result.output
    # Dry-run must not write to feedback_rules.
    conn = connect(str(db_path_with_postmortems))
    try:
        rows = conn.execute("SELECT COUNT(*) AS c FROM feedback_rules").fetchone()
        assert rows["c"] == 0
    finally:
        conn.close()


def test_extract_flag_off_writes_blocked(
    runner: CliRunner,
    db_path_with_postmortems: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EXTRACTION_ENABLED", raising=False)

    result = runner.invoke(cli, ["learning", "extract"])

    assert result.exit_code == 2, result.output
    # Click's CliRunner merges stderr into output by default; check both fields.
    combined = (result.output or "") + (result.stderr if result.stderr_bytes else "")
    assert "EXTRACTION_ENABLED=0" in combined


def test_extract_flag_on_writes_row(
    runner: CliRunner,
    db_path_with_postmortems: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXTRACTION_ENABLED", "1")

    result = runner.invoke(cli, ["learning", "extract"])

    assert result.exit_code == 0, result.output
    conn = connect(str(db_path_with_postmortems))
    try:
        rows = conn.execute(
            "SELECT id, status, signature FROM feedback_rules"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "probation"
        assert rows[0]["signature"] == "phpunit::DrupalDemo::testFails"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# learning propose
# ---------------------------------------------------------------------------


def test_propose_zero_rules_when_extract_unrun(
    runner: CliRunner,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No rows in feedback_rules → no GitLab call, exit 0, friendly message."""
    monkeypatch.setenv("OVERLAY_PROPOSER_ENABLED", "1")
    # The proposer reaches the config check before query_promotable, so we
    # must also configure repo_project_path for this case to land on the
    # "no rules ready" branch.
    monkeypatch.setattr(
        "src.cli.get_config",
        lambda: type(
            "C",
            (),
            {
                "get_sentinel_repo_project_path": staticmethod(
                    lambda: "sentinel-team/sentinel"
                )
            },
        )(),
    )
    # Construct GitLabClient inside the command requires creds; stub it out.
    monkeypatch.setenv("GITLAB_BASE_URL", "https://gl.example.com")
    monkeypatch.setenv("GITLAB_API_TOKEN", "fake")

    result = runner.invoke(
        cli, ["learning", "propose", "--scope", "drupal", "--min-confidence", "80"]
    )

    assert result.exit_code == 0, result.output
    assert "No rules ready" in result.output


def test_propose_dry_run_no_writes(
    runner: CliRunner,
    db_path_with_promotable_rule: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run bypasses the flag-off check and the repo_project_path config check.

    Note: the dry-run still attempts to ``git checkout -b`` against the repo
    root the CLI resolves (the workspace itself). Reject before that by
    monkeypatching ``propose_overlays`` to a no-op so the CLI surface (flag
    bypass + arg threading + no-writes invariant) is what we actually test.
    """
    # Ensure flag is off — dry-run must work without it.
    monkeypatch.delenv("OVERLAY_PROPOSER_ENABLED", raising=False)
    # And without repo_project_path configured — dry-run does not require it.
    monkeypatch.setattr(
        "src.cli.get_config",
        lambda: type(
            "C",
            (),
            {"get_sentinel_repo_project_path": staticmethod(lambda: None)},
        )(),
    )

    captured: dict[str, object] = {}

    def fake_propose_overlays(conn, **kwargs):  # noqa: ANN001, ANN003
        captured.update(kwargs)
        return []

    # The CLI imports propose_overlays inside the function; patch the source.
    monkeypatch.setattr(
        "src.core.learning.propose_overlay.propose_overlays",
        fake_propose_overlays,
    )

    result = runner.invoke(
        cli,
        ["learning", "propose", "--scope", "drupal", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert captured.get("dry_run") is True
    assert captured.get("event_bus") is None
    assert captured.get("execution_id") is None

    # proposed_at on the seeded rule must still be NULL.
    conn = connect(str(db_path_with_promotable_rule))
    try:
        row = conn.execute(
            "SELECT proposed_at FROM feedback_rules WHERE id = 1"
        ).fetchone()
        assert row["proposed_at"] is None
    finally:
        conn.close()


def test_propose_flag_off_blocked(
    runner: CliRunner,
    db_path_with_promotable_rule: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OVERLAY_PROPOSER_ENABLED", raising=False)

    result = runner.invoke(
        cli, ["learning", "propose", "--scope", "drupal"]
    )

    assert result.exit_code == 2, result.output
    combined = (result.output or "") + (result.stderr if result.stderr_bytes else "")
    assert "OVERLAY_PROPOSER_ENABLED=0" in combined


def test_propose_missing_repo_project_path(
    runner: CliRunner,
    db_path_with_promotable_rule: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on, not dry-run, but no repo_project_path → exit 1 with config error."""
    monkeypatch.setenv("OVERLAY_PROPOSER_ENABLED", "1")
    monkeypatch.setattr(
        "src.cli.get_config",
        lambda: type(
            "C",
            (),
            {"get_sentinel_repo_project_path": staticmethod(lambda: None)},
        )(),
    )

    result = runner.invoke(cli, ["learning", "propose", "--scope", "drupal"])

    assert result.exit_code == 1, result.output
    assert "sentinel.repo_project_path is not configured" in result.output


# ---------------------------------------------------------------------------
# learning mark-merged
# ---------------------------------------------------------------------------


def test_mark_merged_flips_status(
    runner: CliRunner, db_path_with_promotable_rule: Path
) -> None:
    result = runner.invoke(
        cli,
        ["learning", "mark-merged", "1", "--sha", "def4567", "--by", "alice"],
    )

    assert result.exit_code == 0, result.output
    conn = connect(str(db_path_with_promotable_rule))
    try:
        row = conn.execute(
            "SELECT status, promoted_to_overlay_sha, promoted_by FROM feedback_rules "
            "WHERE id = 1"
        ).fetchone()
        assert row["status"] == "active"
        assert row["promoted_to_overlay_sha"] == "def4567"
        assert row["promoted_by"] == "alice"
    finally:
        conn.close()


def test_mark_merged_on_active_errors(
    runner: CliRunner, db_path_with_postmortems: Path
) -> None:
    """A rule already at status='active' must not be re-promoted."""
    _seed_feedback_rule(
        db_path_with_postmortems,
        status="active",
        confidence=85,
        promoted_to_overlay_sha="aaa1234",
        promoted_by="prior",
    )

    result = runner.invoke(
        cli,
        ["learning", "mark-merged", "1", "--sha", "bbb1234", "--by", "bob"],
    )

    assert result.exit_code == 1, result.output
    assert "probation" in result.output.lower() or "Error" in result.output


@pytest.mark.parametrize(
    "bad_sha",
    ["abc", "ABCDEF1", "g1b2c3d", "abcdef", "a" * 65],
)
def test_mark_merged_rejects_invalid_sha_at_cli(
    runner: CliRunner,
    db_path_with_promotable_rule: Path,
    bad_sha: str,
) -> None:
    """Click callback must reject malformed SHAs with exit 2 + clear message,
    and must NOT touch the DB (the row's status stays 'probation')."""
    result = runner.invoke(
        cli,
        ["learning", "mark-merged", "1", "--sha", bad_sha, "--by", "alice"],
    )
    assert result.exit_code == 2, result.output
    combined = (result.output or "") + (result.stderr if result.stderr_bytes else "")
    assert "--sha must be 7-40 lowercase hex" in combined

    # DB untouched.
    conn = connect(str(db_path_with_promotable_rule))
    try:
        row = conn.execute(
            "SELECT status, promoted_to_overlay_sha FROM feedback_rules WHERE id = 1"
        ).fetchone()
        assert row["status"] == "probation"
        assert row["promoted_to_overlay_sha"] is None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# learning revoke
# ---------------------------------------------------------------------------


def test_revoke_terminal(
    runner: CliRunner, db_path_with_promotable_rule: Path
) -> None:
    result = runner.invoke(
        cli,
        ["learning", "revoke", "1", "--by", "bob", "--reason", "policy"],
    )

    assert result.exit_code == 0, result.output
    conn = connect(str(db_path_with_promotable_rule))
    try:
        row = conn.execute(
            "SELECT status, revoked_by, revocation_reason FROM feedback_rules "
            "WHERE id = 1"
        ).fetchone()
        assert row["status"] == "revoked"
        assert row["revoked_by"] == "bob"
        assert row["revocation_reason"] == "policy"
    finally:
        conn.close()


def test_revoke_already_revoked_errors(
    runner: CliRunner, db_path_with_postmortems: Path
) -> None:
    _seed_feedback_rule(
        db_path_with_postmortems,
        status="revoked",
        confidence=80,
        revoked_by="prior",
        revocation_reason="prior reason",
    )

    result = runner.invoke(
        cli,
        ["learning", "revoke", "1", "--by", "bob", "--reason", "again"],
    )

    assert result.exit_code == 1, result.output


# ---------------------------------------------------------------------------
# learning list
# ---------------------------------------------------------------------------


def test_list_no_filter_prints_all(
    runner: CliRunner, db_path: Path
) -> None:
    _seed_feedback_rule(
        db_path, signature="sig.probation", status="probation", confidence=70
    )
    _seed_feedback_rule(
        db_path,
        signature="sig.active",
        status="active",
        confidence=90,
        promoted_to_overlay_sha="x000001",
        promoted_by="y",
    )
    _seed_feedback_rule(
        db_path,
        signature="sig.revoked",
        status="revoked",
        confidence=60,
        revoked_by="z",
        revocation_reason="reason",
    )

    result = runner.invoke(cli, ["learning", "list"])

    assert result.exit_code == 0, result.output
    assert "sig.probation" in result.output
    assert "sig.active" in result.output
    assert "sig.revoked" in result.output


def test_list_filters_by_status(
    runner: CliRunner, db_path: Path
) -> None:
    _seed_feedback_rule(
        db_path, signature="sig.probation", status="probation", confidence=70
    )
    _seed_feedback_rule(
        db_path,
        signature="sig.active",
        status="active",
        confidence=90,
        promoted_to_overlay_sha="x000001",
        promoted_by="y",
    )

    result = runner.invoke(cli, ["learning", "list", "--status", "active"])

    assert result.exit_code == 0, result.output
    assert "sig.active" in result.output
    assert "sig.probation" not in result.output


def test_list_filters_by_scope(
    runner: CliRunner, db_path: Path
) -> None:
    _seed_feedback_rule(
        db_path,
        signature="sig.drupal",
        scope="drupal",
        agent_target="drupal_developer",
        status="probation",
        confidence=70,
    )
    _seed_feedback_rule(
        db_path,
        signature="sig.python",
        scope="python",
        agent_target="python_developer",
        status="probation",
        confidence=70,
    )

    result = runner.invoke(cli, ["learning", "list", "--scope", "drupal"])

    assert result.exit_code == 0, result.output
    assert "sig.drupal" in result.output
    assert "sig.python" not in result.output
