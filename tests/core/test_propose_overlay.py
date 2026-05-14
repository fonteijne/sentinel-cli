"""Tests for ``src.core.learning.propose_overlay`` (Phase 2C, plan task 13).

Push-step strategy
------------------
The plan offers two options for handling the ``git push`` step in tests:
(1) tmp-path bare repo as the remote, or (2) monkeypatch the push step to a
no-op. We pick (2) — monkeypatching ``push_overlay_branch`` to a tiny fake
that runs ``git add`` and ``git commit`` (no push) — because:

  * a bare-repo fixture roughly doubles fixture complexity;
  * the proposer's contract for the persistence layer + GitLab interaction
    is independent of whether the push reaches a real remote;
  * the integration test (``tests/integration/test_phase2c_promotion.py``)
    is the right level for end-to-end remote semantics.

Tests assert: provenance trailer in committed file, ``draft=True`` in the
GitLab call, ``mark_proposed`` side effects, idempotency, branch naming,
zero-rules path, missing overlay file, and event publication.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import Mock

import pytest

from src.core.events.types import FeedbackRulePromoted
from src.core.learning import propose_overlay as propose_module
from src.core.learning.propose_overlay import (
    ProposalResult,
    propose_overlays,
)
from src.core.persistence import (
    apply_migrations,
    upsert_rule,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Iterator[Path]:
    """A real git repo with a starter ``prompts/overlays/drupal_developer.md``.

    Sets ``user.email`` / ``user.name`` because ``git commit`` refuses to run
    without them in a test container.
    """
    repo = tmp_path / "sentinel"
    repo.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    overlay_dir = repo / "prompts" / "overlays"
    overlay_dir.mkdir(parents=True)
    overlay_path = overlay_dir / "drupal_developer.md"
    overlay_path.write_text(
        "# Drupal Developer Overlay\n"
        "\n"
        "## Operating Principles\n"
        "\n"
        "- Drupal-way first.\n"
        "- Modern APIs only.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True,
    )
    yield repo


@pytest.fixture
def mock_gitlab() -> Mock:
    m = Mock(spec=["create_merge_request"])
    m.create_merge_request.return_value = {
        "web_url": "https://gl/proj/-/merge_requests/42",
        "iid": 42,
        "state": "opened",
        "title": "Draft: Auto-promote drupal pitfalls — 1 rule",
        "raw": {},
    }
    return m


@pytest.fixture
def conn_with_promotable_rules() -> Iterator[sqlite3.Connection]:
    """In-memory DB with executions, postmortems, and one probation rule
    at confidence=80, scope='drupal', agent_target='developer'."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    apply_migrations(c)
    now = datetime.now(timezone.utc).isoformat()

    for exec_id, ticket in (
        ("exec-acme-1", "ACME-101"),
        ("exec-bravo-1", "BRAVO-201"),
    ):
        c.execute(
            "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
            "VALUES (?, ?, 'developer', 'completed', ?)",
            (exec_id, ticket, now),
        )

    pm_ids: list[int] = []
    for exec_id in ("exec-acme-1", "exec-bravo-1"):
        cur = c.execute(
            """
            INSERT INTO postmortems (
                execution_id, stack_type, agent, failure_signature,
                context_excerpt, fix_summary, provenance, confidence, created_at
            ) VALUES (?, 'drupal', 'developer',
                      'phpunit::Foo::testBar::sentinel_demo',
                      'AssertionError on line 42', NULL, 'auto', 50, ?)
            """,
            (exec_id, now),
        )
        assert cur.lastrowid is not None
        pm_ids.append(int(cur.lastrowid))
    c.commit()

    upsert_rule(
        c,
        signature="phpunit::Foo::testBar::sentinel_demo",
        scope="drupal",
        agent_target="developer",
        rule_text="phpunit::Foo::testBar::sentinel_demo",
        confidence=80,
        observation_count=3,
        distinct_projects=2,
        first_postmortem_id=pm_ids[0],
        last_postmortem_id=pm_ids[-1],
    )
    try:
        yield c
    finally:
        c.close()


class _FakeEventBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish(self, event: Any) -> None:
        self.published.append(event)


def _no_push_overlay_branch(
    repo_root: Path,
    branch_name: str,
    paths: list[Path],
    commit_message: str,
) -> None:
    """Replacement for ``push_overlay_branch`` that stages and commits but
    skips the network ``git push``. Mirrors the real helper's check for an
    empty staged diff so test failures point at the right place."""
    for path in paths:
        subprocess.run(
            ["git", "add", str(path)],
            cwd=repo_root, check=True, capture_output=True,
        )
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_root, capture_output=True,
    )
    if diff.returncode == 0:
        raise RuntimeError("no staged changes — fake push helper")
    subprocess.run(
        ["git", "commit", "-m", commit_message],
        cwd=repo_root, check=True, capture_output=True,
    )


def _list_branches(repo_root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "branch", "--list"],
        cwd=repo_root, check=True, capture_output=True,
    ).stdout.decode()
    return [
        line.lstrip("* ").strip()
        for line in out.splitlines()
        if line.strip()
    ]


def _current_ref(repo_root: Path) -> str:
    """Return the current branch name, or the SHA if HEAD is detached.

    Mirrors the production ``_capture_starting_ref`` resolution order so test
    assertions can compare directly against either form.
    """
    sym = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if sym.returncode == 0:
        return sym.stdout.strip()
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    return sha.stdout.strip()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dry_run_creates_no_branch_no_mr(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
) -> None:
    results = propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
        dry_run=True,
    )
    assert len(results) == 1
    assert all(r.dry_run is True for r in results)
    assert all(r.mr_url == "(dry-run)" for r in results)

    assert mock_gitlab.create_merge_request.call_count == 0

    row = conn_with_promotable_rules.execute(
        "SELECT proposed_at, proposed_overlay_mr_url FROM feedback_rules "
        "WHERE id = ?",
        (results[0].rule_id,),
    ).fetchone()
    assert row["proposed_at"] is None
    assert row["proposed_overlay_mr_url"] is None

    branches = _list_branches(tmp_repo)
    assert all(
        not b.startswith("sentinel-learning/promote-drupal-")
        for b in branches
    ), f"dry-run left a stale branch behind: {branches}"


def test_propose_writes_provenance_trailer(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        propose_module,
        "push_overlay_branch",
        _no_push_overlay_branch,
    )
    results = propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
    )
    assert len(results) == 1
    rule_id = results[0].rule_id

    # HEAD is restored to the operator's starting ref after the call (H2);
    # inspect the promote branch directly to verify the committed overlay.
    committed = subprocess.run(
        ["git", "show", f"{results[0].branch_name}:prompts/overlays/drupal_developer.md"],
        cwd=tmp_repo, check=True, capture_output=True,
    ).stdout.decode()
    assert f"<!-- rule:{rule_id} origin:postmortem-" in committed
    assert "## Auto-promoted pitfalls" in committed
    assert "phpunit::Foo::testBar::sentinel_demo" in committed


def test_propose_calls_gitlab_with_draft_true(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        propose_module, "push_overlay_branch", _no_push_overlay_branch,
    )
    propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
    )
    assert mock_gitlab.create_merge_request.call_count == 1
    kwargs = mock_gitlab.create_merge_request.call_args.kwargs
    assert kwargs["draft"] is True
    assert kwargs["target_branch"] == "main"
    assert kwargs["project_id"] == "sentinel-team/sentinel"
    assert kwargs["source_branch"].startswith("sentinel-learning/promote-drupal-")
    # Description must reference at least one rule's signature.
    assert "phpunit::Foo::testBar::sentinel_demo" in kwargs["description"]


def test_propose_records_mr_url_and_path(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        propose_module, "push_overlay_branch", _no_push_overlay_branch,
    )
    results = propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
    )
    assert len(results) == 1
    rule_id = results[0].rule_id

    row = conn_with_promotable_rules.execute(
        "SELECT proposed_at, proposed_overlay_mr_url, proposed_overlay_path "
        "FROM feedback_rules WHERE id = ?",
        (rule_id,),
    ).fetchone()
    assert row["proposed_at"] is not None
    assert row["proposed_overlay_mr_url"] == "https://gl/proj/-/merge_requests/42"
    assert row["proposed_overlay_path"] == "prompts/overlays/drupal_developer.md"


def test_propose_idempotent_only_unproposed(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        propose_module, "push_overlay_branch", _no_push_overlay_branch,
    )
    first = propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
    )
    assert len(first) == 1

    second = propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
    )
    assert second == []
    # No second MR call (the first run consumed the only promotable rule).
    assert mock_gitlab.create_merge_request.call_count == 1


def test_propose_branch_naming(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        propose_module, "push_overlay_branch", _no_push_overlay_branch,
    )
    results = propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
    )
    assert len(results) == 1
    assert re.match(
        r"^sentinel-learning/promote-drupal-\d{8}-\d{6}$",
        results[0].branch_name,
    ), results[0].branch_name


def test_branch_name_unique_across_seconds() -> None:
    """Two ``_branch_name_for`` calls separated by ~1s must yield distinct
    names. Regression guard for H3: minute-precision suffixes collide on a
    same-minute retry after a failed real-run (the failure path in
    ``propose_overlays`` deliberately leaves the branch on disk).
    """
    first = propose_module._branch_name_for("drupal")
    time.sleep(1.05)
    second = propose_module._branch_name_for("drupal")
    assert first != second, (
        f"branch names collided across a 1s gap: {first!r} == {second!r} "
        "(_branch_name_for stamp precision regressed)"
    )
    pattern = r"^sentinel-learning/promote-drupal-\d{8}-\d{6}$"
    assert re.match(pattern, first), first
    assert re.match(pattern, second), second


def test_propose_zero_rules_no_branch_creation(
    tmp_repo: Path,
    mock_gitlab: Mock,
) -> None:
    # Build a connection with NO promotable rules.
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    apply_migrations(c)
    branches_before = _list_branches(tmp_repo)

    results = propose_overlays(
        c,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
    )
    assert results == []
    assert mock_gitlab.create_merge_request.call_count == 0
    branches_after = _list_branches(tmp_repo)
    assert branches_after == branches_before
    c.close()


def test_propose_publishes_event_when_bus_provided(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        propose_module, "push_overlay_branch", _no_push_overlay_branch,
    )
    bus = _FakeEventBus()
    results = propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
        event_bus=bus,
    )
    assert len(results) == 1
    assert len(bus.published) == 1
    event = bus.published[0]
    assert isinstance(event, FeedbackRulePromoted)
    assert event.rule_id == results[0].rule_id
    assert event.mr_url == "https://gl/proj/-/merge_requests/42"
    assert event.scope == "drupal"
    assert event.branch_name == results[0].branch_name


def test_propose_missing_overlay_file_raises(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
) -> None:
    overlay_path = tmp_repo / "prompts" / "overlays" / "drupal_developer.md"
    overlay_path.unlink()
    # Commit the deletion so we don't sit on a dirty tree (checkout -b would
    # otherwise carry the pending delete onto the new branch).
    subprocess.run(["git", "add", "-A"], cwd=tmp_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "drop overlay"],
        cwd=tmp_repo, check=True, capture_output=True,
    )
    with pytest.raises(FileNotFoundError):
        propose_overlays(
            conn_with_promotable_rules,
            gitlab_client=mock_gitlab,
            repo_root=tmp_repo,
            repo_project_path="sentinel-team/sentinel",
            scope="drupal",
            min_confidence=80,
        )


def test_proposal_result_overlay_path_is_string(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ProposalResult.overlay_path`` is the ``str`` of the rule's overlay relpath."""
    monkeypatch.setattr(
        propose_module, "push_overlay_branch", _no_push_overlay_branch,
    )
    results = propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
    )
    assert isinstance(results[0], ProposalResult)
    assert results[0].overlay_path == str(Path("prompts/overlays/drupal_developer.md"))


def test_real_run_restores_head_on_success(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real-run happy path: HEAD is restored to the operator's starting ref,
    AND the promote branch is preserved on disk (regression guard against
    accidentally also deleting it).
    """
    monkeypatch.setattr(
        propose_module, "push_overlay_branch", _no_push_overlay_branch,
    )
    starting = _current_ref(tmp_repo)

    propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
    )

    assert _current_ref(tmp_repo) == starting
    branches_joined = " ".join(_list_branches(tmp_repo))
    assert "sentinel-learning/promote-drupal-" in branches_joined, (
        f"promote branch was unexpectedly removed: {branches_joined}"
    )


def test_real_run_restores_head_on_failure(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real-run failure path (push raises): HEAD is restored to starting ref,
    and the promote branch survives on disk for operator inspection (existing
    intentional contract, now explicitly tested).
    """
    def _failing_push(
        repo_root: Path,
        branch_name: str,
        paths: list[Path],
        commit_message: str,
    ) -> None:
        raise RuntimeError("push fail")

    monkeypatch.setattr(propose_module, "push_overlay_branch", _failing_push)
    starting = _current_ref(tmp_repo)

    with pytest.raises(RuntimeError, match="push fail"):
        propose_overlays(
            conn_with_promotable_rules,
            gitlab_client=mock_gitlab,
            repo_root=tmp_repo,
            repo_project_path="sentinel-team/sentinel",
            scope="drupal",
            min_confidence=80,
        )

    assert _current_ref(tmp_repo) == starting
    assert any(
        b.startswith("sentinel-learning/promote-")
        for b in _list_branches(tmp_repo)
    ), "promote branch must be preserved on failure for operator inspection"


def test_dry_run_restores_head_when_apply_overlay_raises_midflow(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run mid-flow failure (e.g. malformed overlay): HEAD must still be
    restored. Previously the dry-run cleanup (`git checkout -` + `git branch
    -D`) only ran on the happy path; an exception inside the loop stranded
    HEAD on the promote branch.
    """
    monkeypatch.setattr(
        propose_module,
        "_apply_overlay_edit",
        Mock(side_effect=RuntimeError("synthetic")),
    )
    starting = _current_ref(tmp_repo)

    with pytest.raises(RuntimeError, match="synthetic"):
        propose_overlays(
            conn_with_promotable_rules,
            gitlab_client=mock_gitlab,
            repo_root=tmp_repo,
            repo_project_path="sentinel-team/sentinel",
            scope="drupal",
            min_confidence=80,
            dry_run=True,
        )

    assert _current_ref(tmp_repo) == starting


def test_restores_to_detached_head_when_started_detached(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator started in detached HEAD: snapshot via `git rev-parse HEAD`,
    restore via `git checkout <sha>` (which re-detaches at the same SHA).
    """
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "checkout", sha],
        cwd=tmp_repo, check=True, capture_output=True,
    )
    # Sanity check: we're now detached at `sha`.
    assert _current_ref(tmp_repo) == sha

    monkeypatch.setattr(
        propose_module, "push_overlay_branch", _no_push_overlay_branch,
    )

    propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
    )

    assert _current_ref(tmp_repo) == sha


def test_dry_run_refuses_when_overlay_is_dirty(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
) -> None:
    """H4: pre-flight refuses to run when the overlay file we would touch
    has uncommitted modifications. Operator's edits are preserved verbatim
    and the error message names the blocking file."""
    overlay_path = tmp_repo / "prompts" / "overlays" / "drupal_developer.md"
    operator_edit = (
        overlay_path.read_text(encoding="utf-8")
        + "\n## My WIP section\n- handwritten note\n"
    )
    overlay_path.write_text(operator_edit, encoding="utf-8")

    with pytest.raises(
        RuntimeError, match=r"uncommitted changes in .*drupal_developer\.md"
    ):
        propose_overlays(
            conn_with_promotable_rules,
            gitlab_client=mock_gitlab,
            repo_root=tmp_repo,
            repo_project_path="sentinel-team/sentinel",
            scope="drupal",
            min_confidence=80,
            dry_run=True,
        )

    # Operator's edits are preserved byte-for-byte.
    assert overlay_path.read_text(encoding="utf-8") == operator_edit
    # No branch was created.
    assert all(
        not b.startswith("sentinel-learning/promote-drupal-")
        for b in _list_branches(tmp_repo)
    )
    # No GitLab call.
    assert mock_gitlab.create_merge_request.call_count == 0


def test_real_run_refuses_when_overlay_is_dirty(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H4: real-run also refuses on dirty tree (same precondition)."""
    monkeypatch.setattr(
        propose_module, "push_overlay_branch", _no_push_overlay_branch,
    )
    overlay_path = tmp_repo / "prompts" / "overlays" / "drupal_developer.md"
    overlay_path.write_text(
        overlay_path.read_text(encoding="utf-8") + "\n## WIP\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match=r"uncommitted changes"):
        propose_overlays(
            conn_with_promotable_rules,
            gitlab_client=mock_gitlab,
            repo_root=tmp_repo,
            repo_project_path="sentinel-team/sentinel",
            scope="drupal",
            min_confidence=80,
        )
    assert mock_gitlab.create_merge_request.call_count == 0


def test_dry_run_leaves_overlay_file_byte_identical_on_clean_tree(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
) -> None:
    """H4 layer-2: even on a clean tree, dry-run must leave the overlay
    file byte-identical to its pre-run contents. Tests the explicit
    restore path, not just the branch-revert side-effect."""
    overlay_path = tmp_repo / "prompts" / "overlays" / "drupal_developer.md"
    before = overlay_path.read_bytes()

    results = propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
        dry_run=True,
    )
    assert len(results) == 1

    after = overlay_path.read_bytes()
    assert before == after, "dry-run mutated the overlay file"


def test_dry_run_refuses_when_overlay_is_untracked(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
) -> None:
    """H4 edge case: if the overlay file is untracked (e.g. operator
    drafted a new overlay variant but never `git add`'d it), porcelain
    output shows `?? path` and we must still refuse. Defensive.

    We exercise this by deleting the committed overlay and re-creating
    it as untracked content — porcelain reports it as ``?? prompts/...``
    but only AFTER we drop the deletion (otherwise the file appears as
    ` D` deleted and the test would conflate the two cases).
    """
    overlay_relpath = Path("prompts") / "overlays" / "drupal_developer.md"
    overlay_path = tmp_repo / overlay_relpath

    # Remove and commit the deletion so the next write is genuinely untracked.
    overlay_path.unlink()
    subprocess.run(
        ["git", "add", "-A"], cwd=tmp_repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "drop overlay"],
        cwd=tmp_repo, check=True, capture_output=True,
    )
    # Re-create as untracked. The proposer's FileNotFoundError check would
    # normally fire first if the file doesn't exist, so we DO write content
    # — the file exists in the working tree but is untracked.
    overlay_path.write_text("# operator's new draft overlay\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"uncommitted changes"):
        propose_overlays(
            conn_with_promotable_rules,
            gitlab_client=mock_gitlab,
            repo_root=tmp_repo,
            repo_project_path="sentinel-team/sentinel",
            scope="drupal",
            min_confidence=80,
            dry_run=True,
        )


# ---------------------------------------------------------------------------
# _overlay_relpath_for validation (M3 — defense in depth)
# ---------------------------------------------------------------------------


def test_overlay_relpath_for_valid_inputs() -> None:
    """No-op proof: known-good ``(scope, agent_target)`` produces the
    expected path under ``prompts/overlays/`` and does not raise."""
    from src.core.learning.propose_overlay import _overlay_relpath_for

    assert (
        str(_overlay_relpath_for("drupal", "developer"))
        == "prompts/overlays/drupal_developer.md"
    )
    # Real stack_type with a digit (drupal9/10/11 from stack_profiler.py)
    assert (
        str(_overlay_relpath_for("drupal10", "plan_generator"))
        == "prompts/overlays/drupal10_plan_generator.md"
    )


def test_overlay_relpath_for_rejects_traversal_in_agent_target() -> None:
    """``agent_target`` with path separators (e.g. ``"../etc"``) raises
    ``ValueError`` at the leaf — defense in depth even if the DB ever holds
    a malformed row."""
    from src.core.learning.propose_overlay import _overlay_relpath_for

    with pytest.raises(ValueError, match=r"invalid agent_target"):
        _overlay_relpath_for("drupal", "../etc")


def test_overlay_relpath_for_rejects_traversal_in_scope() -> None:
    """``scope`` is also DB-sourced (postmortems.stack_type) and is
    interpolated into the filename — same validation."""
    from src.core.learning.propose_overlay import _overlay_relpath_for

    with pytest.raises(ValueError, match=r"invalid scope"):
        _overlay_relpath_for("../etc", "developer")


@pytest.mark.parametrize(
    "scope,agent_target",
    [
        ("", "developer"),                      # empty scope
        ("drupal", ""),                         # empty agent_target
        ("Drupal", "developer"),                # uppercase rejected
        ("drupal", "Developer"),                # uppercase rejected
        ("9drupal", "developer"),               # leading digit rejected
        ("drupal", "9developer"),               # leading digit rejected
        ("drupal", "developer/extra"),          # forward slash
        ("drupal", "developer\\extra"),         # backslash
        ("drupal", "developer.md"),             # dot
        ("drupal", "developer extra"),          # space
        ("drupal", "developer\x00etc"),         # NUL byte
    ],
)
def test_overlay_relpath_for_rejects_malformed_inputs(
    scope: str, agent_target: str,
) -> None:
    from src.core.learning.propose_overlay import _overlay_relpath_for

    with pytest.raises(ValueError):
        _overlay_relpath_for(scope, agent_target)
