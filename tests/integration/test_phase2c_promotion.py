"""Phase 2C Task 16 — exit-criterion end-to-end promotion-path test.

Walks the full lifecycle of a learned rule through the CLI surfaces:

  1. ``sentinel learning extract`` clusters seeded postmortems and lands one
     probation row at confidence == 80.
  2. ``sentinel learning propose --dry-run`` prints clusters but writes
     nothing — ``proposed_at`` stays NULL and the GitLabClient is never
     constructed.
  3. ``sentinel learning propose`` (real) opens a draft MR via the mocked
     GitLabClient (``draft=True`` is hard-coded — D7 invariant), commits
     the overlay edit to a tmp git repo, and stamps ``proposed_at`` /
     ``proposed_overlay_mr_url`` on the row. ``git push`` is neutralized
     so the test does not reach for a real origin.
  4. ``sentinel learning mark-merged`` flips the row to 'active' and
     stamps the merge SHA + maintainer.
  5. ``sentinel learning revoke`` flips the row to 'revoked' but DOES NOT
     delete it — the audit ledger keeps the full history.

Plus event-table assertions: ``FeedbackRuleExtracted``,
``FeedbackRulePromoted``, and ``FeedbackRuleRevoked`` all land with
``execution_id LIKE 'learning-%'`` (the synthetic id the CLI seeds via
``_learning_seed_synthetic_execution`` to satisfy the bus's FK to
``executions.id``).

Plan ref: .claude/PRPs/plans/phase-2c-promotion-path.plan.md task 16.

Confidence math (must land at exactly 80 to clear the default
``--min-confidence 80`` floor):
    base 50 + 10*min(5, max(0, obs-1)) + 5*min(3, max(0, proj-1))
For obs=3, proj=3: 50 + 10*2 + 5*2 = 80. ✓
We seed 3 postmortems across 3 distinct projects (ACME, BRAVO, CHARLIE)
sharing a non-symptomatic signature so the whack-a-mole filter passes.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from src.cli import cli
from src.core.persistence import (
    apply_migrations,
    connect,
    insert_postmortem,
)


# ---------------------------------------------------------------------------
# Inline fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A tmp git repo that mirrors enough of the Sentinel repo layout.

    Contains:
      * ``pyproject.toml`` with ``name = "sentinel"`` so
        ``_resolve_sentinel_repo_root``'s validation accepts it (tests
        monkeypatch the resolver to return this path; the marker file is
        defensive in case a future version checks pyproject in the
        resolver call site).
      * ``prompts/overlays/drupal_developer.md`` copied from the live
        repo so ``_apply_overlay_edit`` and ``_render_rule_bullet`` operate
        on a realistic file shape.

    Initial commit is on ``main``. ``user.email`` / ``user.name`` are set
    locally so commits inside the test do not depend on the host's git
    config.
    """
    repo = tmp_path / "tmp-sentinel-repo"
    repo.mkdir()

    # pyproject marker — defensive, in case the resolver is ever called.
    (repo / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "sentinel"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )

    # Real overlay file copied verbatim. The proposer asserts the file exists
    # before editing, and _apply_overlay_edit operates on its existing shape
    # (creating an "## Auto-promoted pitfalls" section if absent).
    overlay_dir = repo / "prompts" / "overlays"
    overlay_dir.mkdir(parents=True)
    src_overlay = (
        Path(__file__).resolve().parents[2]
        / "prompts"
        / "overlays"
        / "drupal_developer.md"
    )
    shutil.copyfile(src_overlay, overlay_dir / "drupal_developer.md")

    # git init + config + initial commit on main.
    def _git(*args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    _git("init", "-b", "main")
    _git("config", "user.email", "phase2c-tests@sentinel.local")
    _git("config", "user.name", "Phase 2C Tests")
    _git("config", "commit.gpgsign", "false")
    _git("add", ".")
    _git("commit", "-m", "init")

    return repo


@pytest.fixture
def seeded_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """Tmp SQLite DB seeded with 3 executions + 3 postmortems sharing a sig.

    With obs=3 / proj=3, ``compute_confidence`` lands at exactly 80 — the
    default ``--min-confidence`` floor. Seeded ``stack_type='drupal'`` and
    ``agent='developer'`` so the proposer's overlay path resolves to
    ``prompts/overlays/drupal_developer.md`` (which exists in tmp_repo).
    """
    db = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db))

    conn = connect(str(db))
    try:
        apply_migrations(conn)
        now = datetime.now(timezone.utc).isoformat()
        # Three executions across three distinct projects so distinct_projects=3.
        # project_key is derived from ticket_id prefix
        # (UPPER(SUBSTR(ticket_id, 1, INSTR(ticket_id, '-') - 1))).
        for exec_id, ticket in [
            ("exec-acme-1", "ACME-847"),
            ("exec-bravo-1", "BRAVO-112"),
            ("exec-charlie-1", "CHARLIE-203"),
        ]:
            conn.execute(
                "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
                "VALUES (?, ?, 'developer', 'completed', ?)",
                (exec_id, ticket, now),
            )
        conn.commit()

        sig = "phpunit::failed_assertion::sentinel_demo"
        for exec_id in ("exec-acme-1", "exec-bravo-1", "exec-charlie-1"):
            insert_postmortem(
                conn,
                execution_id=exec_id,
                stack_type="drupal",
                agent="developer",
                failure_signature=sig,
                context_excerpt="ctx",
                provenance="auto",
                confidence=50,
            )
    finally:
        conn.close()

    yield db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_repo_resolution(
    monkeypatch: pytest.MonkeyPatch, repo: Path
) -> None:
    """Redirect the CLI's repo resolver + project-path config to tmp."""
    monkeypatch.setattr("src.cli._resolve_sentinel_repo_root", lambda: repo)
    monkeypatch.setattr(
        "src.cli.get_config",
        lambda: SimpleNamespace(
            get_sentinel_repo_project_path=lambda: "sentinel-team/sentinel"
        ),
    )


def _stub_gitlab_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replace GitLabClient with a MagicMock returning a deterministic MR.

    The CLI imports ``GitLabClient`` lazily inside the propose body
    (``from src.gitlab_client import GitLabClient``), so we patch the
    source module — that's where the lazy import resolves.
    """
    instance = MagicMock(name="GitLabClient")
    instance.create_merge_request.return_value = {
        "web_url": "https://gl.example.com/sentinel-team/sentinel/-/merge_requests/42",
        "iid": 42,
        "state": "opened",
        "title": "Auto-promote drupal pitfalls",
        "raw": {},
    }
    cls = MagicMock(name="GitLabClientClass", return_value=instance)
    monkeypatch.setattr("src.gitlab_client.GitLabClient", cls)
    return instance


def _neutralize_push(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``push_overlay_branch`` with a stub that commits but does not push.

    The real helper does ``git add`` + ``git commit`` + ``git push -u origin
    <branch>``. The push step would reach for a real origin remote that the
    tmp repo doesn't have. Our stub keeps the add+commit so the overlay
    edit is durably recorded in the tmp repo (the test reads it back to
    assert the provenance trailer is present), and skips only the push.
    """
    def _add_and_commit_no_push(
        repo_root: Path,
        branch_name: str,
        paths: list[Path],
        commit_message: str,
    ) -> None:
        for path in paths:
            subprocess.run(
                ["git", "add", str(path)],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
        diff_result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_root,
            capture_output=True,
        )
        if diff_result.returncode == 0:
            raise RuntimeError("No staged overlay changes — refusing to commit.")
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
        # NOTE: skip ``git push`` — the tmp repo has no origin remote.

    monkeypatch.setattr(
        "src.core.learning.propose_overlay.push_overlay_branch",
        _add_and_commit_no_push,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_extract_propose_promote_revoke_full_workflow(
    runner: CliRunner,
    seeded_db: Path,
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: extract → propose dry-run → propose → mark-merged → revoke."""

    # ------------------------------------------------------------------
    # Step 1 — extract.
    # ------------------------------------------------------------------
    monkeypatch.setenv("EXTRACTION_ENABLED", "1")

    result = runner.invoke(cli, ["learning", "extract"])
    assert result.exit_code == 0, result.output

    conn = connect(str(seeded_db))
    try:
        rows = conn.execute(
            "SELECT id, status, confidence, proposed_at FROM feedback_rules"
        ).fetchall()
        assert len(rows) == 1, f"expected 1 rule, got {len(rows)}: {result.output}"
        rule = rows[0]
        rule_id = int(rule["id"])
        assert rule["status"] == "probation"
        assert rule["confidence"] == 80, (
            f"obs=3 proj=3 must yield confidence=80; got {rule['confidence']}"
        )
        assert rule["proposed_at"] is None
    finally:
        conn.close()

    # ------------------------------------------------------------------
    # Step 2 — propose --dry-run. No GitLabClient construction, no
    # proposed_at write, no real-MR side-effects. The dry-run path still
    # exercises the real subprocess git checkout + revert against tmp_repo,
    # which is why we route the resolver to it here too.
    # ------------------------------------------------------------------
    _patch_repo_resolution(monkeypatch, tmp_repo)
    gitlab_mock = _stub_gitlab_client(monkeypatch)

    result = runner.invoke(
        cli,
        [
            "learning",
            "propose",
            "--scope",
            "drupal",
            "--min-confidence",
            "80",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (
        gitlab_mock.create_merge_request.call_count == 0
    ), "dry-run must not call create_merge_request"

    conn = connect(str(seeded_db))
    try:
        row = conn.execute(
            "SELECT proposed_at FROM feedback_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        assert row["proposed_at"] is None, (
            "dry-run must not stamp proposed_at"
        )
    finally:
        conn.close()

    # ------------------------------------------------------------------
    # Step 3 — propose (real). Opens the draft MR via mocked GitLab,
    # commits the overlay edit to tmp_repo (push is neutralized), and
    # stamps proposed_at + proposed_overlay_mr_url on the rule row.
    # ------------------------------------------------------------------
    monkeypatch.setenv("OVERLAY_PROPOSER_ENABLED", "1")
    _neutralize_push(monkeypatch)

    result = runner.invoke(
        cli,
        ["learning", "propose", "--scope", "drupal", "--min-confidence", "80"],
    )
    assert result.exit_code == 0, result.output

    # GitLabClient.create_merge_request was called exactly once with the
    # contract D7 / Decision 4 mandates: draft=True, target_branch='main',
    # project_id matched the configured repo_project_path.
    assert gitlab_mock.create_merge_request.call_count == 1
    call_kwargs = gitlab_mock.create_merge_request.call_args.kwargs
    assert call_kwargs["draft"] is True, "MR must always open as draft"
    assert call_kwargs["target_branch"] == "main"
    assert call_kwargs["project_id"] == "sentinel-team/sentinel"

    # The overlay file committed onto the promote branch contains the
    # rendered bullet's provenance trailer. H2 restores HEAD to the
    # operator's starting ref after the call, so we inspect the promote
    # branch directly (via `git show <branch>:<path>`) rather than reading
    # the working tree.
    promote_branch = call_kwargs["source_branch"]
    overlay_after = subprocess.run(
        [
            "git", "show",
            f"{promote_branch}:prompts/overlays/drupal_developer.md",
        ],
        cwd=tmp_repo, check=True, capture_output=True,
    ).stdout.decode()
    assert (
        f"<!-- rule:{rule_id} origin:postmortem-" in overlay_after
    ), "overlay must contain the rule provenance trailer"

    # Rule row was stamped with proposed_at + proposed_overlay_mr_url.
    conn = connect(str(seeded_db))
    try:
        row = conn.execute(
            "SELECT proposed_at, proposed_overlay_mr_url, status "
            "FROM feedback_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        assert row["proposed_at"] is not None
        assert (
            row["proposed_overlay_mr_url"]
            == "https://gl.example.com/sentinel-team/sentinel/-/merge_requests/42"
        )
        # mark_proposed deliberately does NOT flip status. That happens at
        # mark-merged below.
        assert row["status"] == "probation"
    finally:
        conn.close()

    # ------------------------------------------------------------------
    # Step 4 — mark-merged.
    # ------------------------------------------------------------------
    result = runner.invoke(
        cli,
        [
            "learning",
            "mark-merged",
            str(rule_id),
            "--sha",
            "def4567",
            "--by",
            "alice",
        ],
    )
    assert result.exit_code == 0, result.output

    conn = connect(str(seeded_db))
    try:
        row = conn.execute(
            "SELECT status, promoted_to_overlay_sha, promoted_by, promoted_at "
            "FROM feedback_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        assert row["status"] == "active"
        assert row["promoted_to_overlay_sha"] == "def4567"
        assert row["promoted_by"] == "alice"
        assert row["promoted_at"] is not None
    finally:
        conn.close()

    # ------------------------------------------------------------------
    # Step 5 — revoke. Append-only: row must still exist after.
    # ------------------------------------------------------------------
    result = runner.invoke(
        cli,
        [
            "learning",
            "revoke",
            str(rule_id),
            "--by",
            "alice",
            "--reason",
            "policy change",
        ],
    )
    assert result.exit_code == 0, result.output

    conn = connect(str(seeded_db))
    try:
        rows = conn.execute(
            "SELECT id, status, revoked_by, revocation_reason, "
            "promoted_to_overlay_sha "
            "FROM feedback_rules WHERE id = ?",
            (rule_id,),
        ).fetchall()
        # Append-only: revoke does NOT delete the row.
        assert len(rows) == 1, "revoke must not delete the row"
        row = rows[0]
        assert row["status"] == "revoked"
        assert row["revoked_by"] == "alice"
        assert row["revocation_reason"] == "policy change"
        # Audit columns from the prior promotion are preserved.
        assert row["promoted_to_overlay_sha"] == "def4567"
    finally:
        conn.close()

    # ------------------------------------------------------------------
    # Bonus — events table contains the three Phase 2C event types each
    # stamped with a synthetic 'learning-...' execution_id.
    # ------------------------------------------------------------------
    conn = connect(str(seeded_db))
    try:
        events_by_type = {
            t: conn.execute(
                "SELECT execution_id FROM events WHERE type = ?", (t,)
            ).fetchall()
            for t in (
                "FeedbackRuleExtracted",
                "FeedbackRulePromoted",
                "FeedbackRuleRevoked",
            )
        }
    finally:
        conn.close()

    for event_type, rows in events_by_type.items():
        assert len(rows) >= 1, f"expected at least one {event_type} event"
        for row in rows:
            assert row["execution_id"].startswith("learning-"), (
                f"{event_type}.execution_id must be a synthetic 'learning-...' "
                f"id; got {row['execution_id']!r}"
            )
