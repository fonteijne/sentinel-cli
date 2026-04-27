"""Tests for the shared post-execute side-effect runner.

These cover the contract that both CLI and the Command Center worker rely
on:

* Push success ⇒ MR ready, decision log, and Jira notification all attempt.
* Push failure ⇒ skip MR/Jira steps and surface the error.
* Per-step failures are best-effort (do not bring down later steps).
* Drupal findings comment is posted only when supplied.
* The revision flow posts the revision log instead of the decision log
  and does NOT mark the MR ready (it's already ready in revise context).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.execution import post_execute as pe


@pytest.fixture
def fake_clients(monkeypatch, tmp_path):
    """Fake gitlab + jira + config so the runner does no IO."""
    gitlab = MagicMock()
    gitlab.list_merge_requests.return_value = [
        {"iid": 42, "web_url": "https://gitlab.example/foo/-/merge_requests/42"}
    ]
    jira = MagicMock()
    config = MagicMock()
    config.get_project_config.return_value = {
        "git_url": "https://gitlab.example.com/acme/backend.git"
    }
    return gitlab, jira, config


def _stub_push(success=True, error=None, diverged=False, branch="feature/foo"):
    def _push(worktree_path, *, force):
        return {
            "pushed": success,
            "branch": branch,
            "error": error,
            "rejected_diverged": diverged,
        }
    return _push


def test_run_post_execute_happy_path(monkeypatch, fake_clients, tmp_path):
    gitlab, jira, config = fake_clients
    monkeypatch.setattr(pe, "push_branch", _stub_push(success=True))

    outcome = pe.run_post_execute_side_effects(
        ticket_id="PROJ-1",
        project="proj",
        worktree_path=tmp_path,
        iteration=2,
        dev_result={"tasks_completed": 3, "tasks_failed": 0,
                    "test_results": {"success": True}},
        sec_result={"approved": True, "findings": []},
        drupal_findings=None,
        drupal_attempts=None,
        force_push=False,
        gitlab_factory=lambda: gitlab,
        jira_factory=lambda: jira,
        config_factory=lambda: config,
        branch_name_factory=lambda t: "feature/PROJ-1",
    )

    assert outcome.pushed is True
    assert outcome.mr_iid == 42
    assert outcome.mr_marked_ready is True
    assert outcome.decision_log_posted is True
    assert outcome.jira_notified is True
    assert "git.pushed" in outcome.artifacts
    assert "gitlab.mr_ready" in outcome.artifacts
    assert "gitlab.decision_log_posted" in outcome.artifacts
    assert "jira.completion_comment_posted" in outcome.artifacts

    # decision log mentions ticket + iterations
    body = gitlab.add_merge_request_comment.call_args.kwargs["body"]
    assert "PROJ-1" in body and "**Iterations:** 2" in body

    # Jira comment includes link.
    jira.add_comment.assert_called_once()
    kwargs = jira.add_comment.call_args
    assert kwargs.args[0] == "PROJ-1"
    assert kwargs.kwargs["link_url"] == \
        "https://gitlab.example/foo/-/merge_requests/42"


def test_run_post_execute_push_failure_skips_mr_and_jira(
    monkeypatch, fake_clients, tmp_path
):
    gitlab, jira, config = fake_clients
    monkeypatch.setattr(
        pe,
        "push_branch",
        _stub_push(success=False, error="non-fast-forward", diverged=True),
    )

    outcome = pe.run_post_execute_side_effects(
        ticket_id="PROJ-1",
        project="proj",
        worktree_path=tmp_path,
        iteration=1,
        dev_result={"tasks_completed": 1, "tasks_failed": 0},
        sec_result={"approved": True, "findings": []},
        drupal_findings=None,
        drupal_attempts=None,
        force_push=False,
        gitlab_factory=lambda: gitlab,
        jira_factory=lambda: jira,
        config_factory=lambda: config,
        branch_name_factory=lambda t: "feature/PROJ-1",
    )

    assert outcome.pushed is False
    assert outcome.push_error == "non-fast-forward"
    assert outcome.extra["push_rejected_diverged"] is True
    assert "git.push_failed" in outcome.artifacts

    gitlab.list_merge_requests.assert_not_called()
    gitlab.mark_as_ready.assert_not_called()
    gitlab.add_merge_request_comment.assert_not_called()
    jira.add_comment.assert_not_called()


def test_run_post_execute_jira_failure_does_not_mask_decision_log(
    monkeypatch, fake_clients, tmp_path
):
    gitlab, jira, config = fake_clients
    monkeypatch.setattr(pe, "push_branch", _stub_push(success=True))
    jira.add_comment.side_effect = RuntimeError("jira down")

    outcome = pe.run_post_execute_side_effects(
        ticket_id="PROJ-1",
        project="proj",
        worktree_path=tmp_path,
        iteration=1,
        dev_result={"tasks_completed": 1, "tasks_failed": 0},
        sec_result={"approved": True, "findings": []},
        drupal_findings=None,
        drupal_attempts=None,
        force_push=False,
        gitlab_factory=lambda: gitlab,
        jira_factory=lambda: jira,
        config_factory=lambda: config,
        branch_name_factory=lambda t: "feature/PROJ-1",
    )

    assert outcome.pushed
    assert outcome.decision_log_posted
    assert outcome.jira_notified is False
    assert "gitlab.decision_log_posted" in outcome.artifacts
    assert "jira.completion_comment_posted" not in outcome.artifacts


def test_run_post_execute_with_drupal_findings(
    monkeypatch, fake_clients, tmp_path
):
    gitlab, jira, config = fake_clients
    monkeypatch.setattr(pe, "push_branch", _stub_push(success=True))

    drupal_findings = {
        "review_data": {"verdict": "REQUEST_CHANGES"},
        "findings": [
            {"id": "F-1", "severity": "MAJOR", "title": "Missing DI",
             "file": "module.php", "line": 12},
            {"id": "F-2", "severity": "BLOCKER", "title": "Cache leak",
             "file": "cache.php"},
        ],
    }

    outcome = pe.run_post_execute_side_effects(
        ticket_id="PROJ-1",
        project="proj",
        worktree_path=tmp_path,
        iteration=2,
        dev_result={"tasks_completed": 1, "tasks_failed": 0},
        sec_result={"approved": True, "findings": []},
        drupal_findings=drupal_findings,
        drupal_attempts=5,
        force_push=False,
        gitlab_factory=lambda: gitlab,
        jira_factory=lambda: jira,
        config_factory=lambda: config,
        branch_name_factory=lambda t: "feature/PROJ-1",
    )

    assert outcome.drupal_findings_posted is True
    assert "gitlab.drupal_findings_posted" in outcome.artifacts
    # add_merge_request_comment called twice: decision log + drupal findings
    assert gitlab.add_merge_request_comment.call_count == 2
    drupal_body = gitlab.add_merge_request_comment.call_args_list[1].kwargs["body"]
    assert "Drupal Review" in drupal_body
    assert "F-1" in drupal_body and "F-2" in drupal_body


def test_run_post_execute_revise_uses_revision_log_and_skips_mark_ready(
    monkeypatch, fake_clients, tmp_path
):
    gitlab, jira, config = fake_clients
    monkeypatch.setattr(pe, "push_branch", _stub_push(success=True))

    revision_result = {
        "feedback_count": 3,
        "tasks_completed": 2,
        "tasks_failed": 0,
        "questions_answered": 1,
        "questions_failed": 0,
        "acknowledged": 0,
        "test_results": {"success": True},
        "config_validation": {"success": True},
        "mr_url": "https://gitlab.example/foo/-/merge_requests/42",
    }

    outcome = pe.run_post_execute_side_effects(
        ticket_id="PROJ-1",
        project="proj",
        worktree_path=tmp_path,
        iteration=1,
        dev_result=revision_result,
        sec_result={},
        drupal_findings=None,
        drupal_attempts=None,
        force_push=False,
        revision_result=revision_result,
        gitlab_factory=lambda: gitlab,
        jira_factory=lambda: jira,
        config_factory=lambda: config,
        branch_name_factory=lambda t: "feature/PROJ-1",
    )

    assert outcome.pushed
    assert outcome.mr_marked_ready is False  # revise flow does not mark ready
    gitlab.mark_as_ready.assert_not_called()
    assert outcome.decision_log_posted
    body = gitlab.add_merge_request_comment.call_args.kwargs["body"]
    assert "Revision Complete" in body and "**Discussions analyzed:** 3" in body
    # No Jira completion comment on the revise flow.
    jira.add_comment.assert_not_called()


def test_format_decision_log_truncates_long_finding_lists():
    body = pe.format_decision_log(
        ticket_id="PROJ-1",
        iteration=1,
        dev_result={"tasks_completed": 1, "tasks_failed": 0},
        sec_result={
            "findings": [
                {"severity": "MINOR", "category": "x",
                 "description": str(i) * 200} for i in range(8)
            ]
        },
    )
    assert "and 3 more" in body


def test_format_drupal_findings_groups_by_severity():
    body = pe.format_drupal_findings_comment(
        ticket_id="PROJ-1",
        attempts=3,
        drupal_result={
            "review_data": {"verdict": "REQUEST_CHANGES"},
            "findings": [
                {"id": "A", "severity": "BLOCKER", "title": "x",
                 "file": "a.php", "line": 1},
                {"id": "B", "severity": "MAJOR", "title": "y", "file": "b.php"},
                {"id": "C", "severity": "MINOR", "title": "z", "file": "c.php"},
            ],
        },
    )
    # Blocker grouping appears before major appears before minor.
    blocker = body.index("BLOCKER")
    major = body.index("MAJOR")
    minor = body.index("MINOR")
    assert blocker < major < minor
