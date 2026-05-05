"""Tests for ``run_execute`` covering the gaps closed in this PR.

The original ``test_workflows.py`` covers ``run_plan`` and the no-op detector.
This file focuses on the bits we just lifted out of ``src.cli``:

* push / MR-ready / decision log / Jira completion notification.
* Drupal reviewer + self-fix loop for ``stack_type=drupal``.
* Push failure surfaces as a workflow failure instead of green.
* The revise flow re-uses the post-execute runner (revision log, no
  mark-as-ready).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from src.core.events import EventBus
from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.options import (
    ExecuteOptions,
    to_metadata_options,
)
from src.core.execution.orchestrator import Orchestrator
from src.core.execution.repository import ExecutionRepository
from src.core.execution.workflows import (
    NoOpExecutionError,
    WorkflowError,
    WorkflowResult,
    run_execute,
    run_revise,
)
from src.core.persistence import connect, ensure_initialized


# ----------------------------------------------------------------- fixtures


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    ensure_initialized()
    conn = connect()
    yield conn
    conn.close()


@pytest.fixture
def orchestrator(db):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    return Orchestrator(repo, bus)


@pytest.fixture
def planned_worktree(tmp_path):
    """A worktree directory that already contains plan files for every PROJ-* used."""
    worktree = tmp_path / "worktrees" / "PROJ" / "wt"
    plan_dir = worktree / ".agents" / "plans"
    plan_dir.mkdir(parents=True)
    for i in range(1, 10):
        (plan_dir / f"PROJ-{i}.md").write_text("# Plan\n\n- step 1\n")
    return worktree


def _config_factory(stack_type: str = ""):
    cfg = MagicMock()
    cfg.get_project_config.return_value = {
        "stack_type": stack_type,
        "git_url": "https://gitlab.example.com/acme/backend.git",
    }
    return lambda: cfg


def _worktree_factory(path: Path):
    mgr = MagicMock()
    mgr.get_worktree_path.return_value = path
    return lambda: mgr


def _env_factory(active: bool = False):
    mgr = MagicMock()
    mgr.setup.return_value = SimpleNamespace(
        active=active, services=[], tooling={}
    )
    mgr.teardown.return_value = True
    return lambda: mgr


def _developer_factory(
    *, dev_results: List[Dict[str, Any]] | None = None,
    revision_results: List[Dict[str, Any]] | None = None,
):
    """Return a factory + the underlying mock so tests can inspect calls."""
    dev_results = dev_results or [{
        "tasks_completed": 1, "tasks_failed": 0, "test_results": {"success": True}
    }]
    revision_results = revision_results or [{
        "feedback_count": 0, "tasks_completed": 0, "tasks_failed": 0
    }]
    dev = MagicMock()
    dev.agent_name = "python_developer"
    dev.run.side_effect = list(dev_results)
    dev.run_revision.side_effect = list(revision_results)

    def factory(stack_type=""):
        return dev
    return factory, dev


def _reviewer_factory(*, results: List[Dict[str, Any]] | None = None):
    results = results or [{"approved": True, "findings": []}]
    rev = MagicMock()
    rev.agent_name = "security_reviewer"
    rev.run.side_effect = list(results)
    return lambda: rev, rev


def _drupal_factory(*, results: List[Dict[str, Any]] | None = None):
    results = results or [{"approved": True, "findings": [], "feedback": []}]
    queue = list(results)
    instances: List[MagicMock] = []

    def factory():
        rev = MagicMock()
        rev.agent_name = "drupal_reviewer"
        rev.run.return_value = queue.pop(0)
        instances.append(rev)
        return rev

    return factory, instances


def _post_runner(events: List[str]):
    """Simulate run_post_execute_side_effects without IO."""
    from src.core.execution.post_execute import PostExecuteOutcome

    def runner(**kwargs):
        events.append(("post", kwargs))
        outcome = PostExecuteOutcome()
        outcome.pushed = True
        outcome.add("git.pushed")
        outcome.mr_iid = 1
        outcome.mr_web_url = "https://gitlab.example/foo/-/merge_requests/1"
        outcome.mr_marked_ready = kwargs.get("revision_result") is None
        if outcome.mr_marked_ready:
            outcome.add("gitlab.mr_ready")
        outcome.decision_log_posted = True
        outcome.add("gitlab.decision_log_posted")
        if kwargs.get("drupal_findings"):
            outcome.drupal_findings_posted = True
            outcome.add("gitlab.drupal_findings_posted")
        if kwargs.get("revision_result") is None:
            outcome.jira_notified = True
            outcome.add("jira.completion_comment_posted")
        return outcome

    return runner


# ----------------------------------------------------- run_execute happy path


def test_run_execute_happy_path_python(orchestrator, db, planned_worktree):
    repo = ExecutionRepository(db)
    options = ExecuteOptions(max_iterations=2)
    options_blob = to_metadata_options(options)
    execution = repo.create(
        "PROJ-1", "PROJ", ExecutionKind.EXECUTE, options=options_blob
    )

    dev_factory, dev = _developer_factory()
    rev_factory, _rev = _reviewer_factory()
    events: List[Any] = []

    result = run_execute(
        orchestrator,
        ticket_id="PROJ-1",
        project="PROJ",
        options=options,
        execution_id=execution.id,
        worktree_factory=_worktree_factory(planned_worktree),
        env_manager_factory=_env_factory(active=False),
        developer_factory=dev_factory,
        reviewer_factory=rev_factory,
        post_execute_runner=_post_runner(events),
    )

    # Real artifacts means assert_real_work passes.
    result.assert_real_work()
    assert "git.worktree_resolved" in result.artifacts
    assert "agent.python_developer" in result.artifacts
    assert "agent.security_reviewer" in result.artifacts
    assert "git.pushed" in result.artifacts
    assert "gitlab.mr_ready" in result.artifacts
    assert "gitlab.decision_log_posted" in result.artifacts
    assert "jira.completion_comment_posted" in result.artifacts
    assert result.extra["mr_url"]
    # Shared post-runner was called once.
    assert sum(1 for e in events if e[0] == "post") == 1


def test_run_execute_translates_push_failure_to_workflow_error(
    orchestrator, db, planned_worktree
):
    repo = ExecutionRepository(db)
    options = ExecuteOptions(max_iterations=1)
    options_blob = to_metadata_options(options)
    execution = repo.create(
        "PROJ-2", "PROJ", ExecutionKind.EXECUTE, options=options_blob
    )

    dev_factory, _dev = _developer_factory()
    rev_factory, _rev = _reviewer_factory()

    # Custom runner that simulates a push failure.
    from src.core.execution.post_execute import PostExecuteOutcome

    def push_fail_runner(**kwargs):
        out = PostExecuteOutcome()
        out.pushed = False
        out.push_error = "non-fast-forward"
        out.add("git.push_failed")
        return out

    with pytest.raises(WorkflowError) as exc_info:
        run_execute(
            orchestrator,
            ticket_id="PROJ-2",
            project="PROJ",
            options=options,
            execution_id=execution.id,
            worktree_factory=_worktree_factory(planned_worktree),
            env_manager_factory=_env_factory(active=False),
            developer_factory=dev_factory,
            reviewer_factory=rev_factory,
            post_execute_runner=push_fail_runner,
        )

    assert "git push failed" in str(exc_info.value)
    assert "non-fast-forward" in str(exc_info.value)


def test_run_execute_drupal_self_fix_loop_recovers(
    orchestrator, db, planned_worktree, monkeypatch
):
    """Drupal reviewer rejects on attempt 1, approves on attempt 2 after
    the developer self-fix run_revision call."""
    monkeypatch.setattr(
        "src.config_loader.get_config",
        _config_factory(stack_type="drupal-9")(),
    )
    repo = ExecutionRepository(db)
    options = ExecuteOptions(max_iterations=3)
    options_blob = to_metadata_options(options)
    execution = repo.create(
        "PROJ-3", "PROJ", ExecutionKind.EXECUTE, options=options_blob
    )

    dev_factory, dev = _developer_factory(
        revision_results=[{"tasks_completed": 1, "tasks_failed": 0}],
    )
    rev_factory, _rev = _reviewer_factory()
    drupal_factory, drupal_instances = _drupal_factory(
        results=[
            {"approved": False, "findings": [{"id": "x", "severity": "MAJOR",
             "title": "missing DI", "file": "a.php"}],
             "feedback": ["fix DI"], "review_data": {}},
            {"approved": True, "findings": [], "feedback": [],
             "review_data": {}},
        ]
    )
    events: List[Any] = []
    result = run_execute(
        orchestrator,
        ticket_id="PROJ-3",
        project="PROJ",
        options=options,
        execution_id=execution.id,
        worktree_factory=_worktree_factory(planned_worktree),
        env_manager_factory=_env_factory(active=False),
        developer_factory=dev_factory,
        reviewer_factory=rev_factory,
        drupal_reviewer_factory=drupal_factory,
        post_execute_runner=_post_runner(events),
        ticket_context_fetcher=lambda t: "summary…",
    )

    assert "agent.drupal_reviewer" in result.artifacts
    assert "drupal.self_fix_attempted" in result.artifacts
    # Two drupal reviewer instances were created (one per attempt).
    assert len(drupal_instances) == 2
    # Developer.run_revision was called once for the self-fix.
    assert dev.run_revision.call_count == 1
    fix_call_kwargs = dev.run_revision.call_args.kwargs
    assert "Fix the following Drupal review findings" in fix_call_kwargs["user_prompt"]


def test_run_execute_drupal_unresolved_findings_get_posted(
    orchestrator, db, planned_worktree, monkeypatch
):
    monkeypatch.setattr(
        "src.config_loader.get_config",
        _config_factory(stack_type="drupal-10")(),
    )
    repo = ExecutionRepository(db)
    options = ExecuteOptions(max_iterations=1)  # only one drupal attempt
    options_blob = to_metadata_options(options)
    execution = repo.create(
        "PROJ-4", "PROJ", ExecutionKind.EXECUTE, options=options_blob
    )

    dev_factory, _dev = _developer_factory()
    rev_factory, _rev = _reviewer_factory()
    drupal_factory, drupal_instances = _drupal_factory(
        results=[
            {"approved": False, "findings": [
                {"id": "x", "severity": "BLOCKER", "title": "leak",
                 "file": "a.php"}
            ], "feedback": ["fix leak"], "review_data": {}},
        ]
    )
    captured: List[Any] = []
    result = run_execute(
        orchestrator,
        ticket_id="PROJ-4",
        project="PROJ",
        options=options,
        execution_id=execution.id,
        worktree_factory=_worktree_factory(planned_worktree),
        env_manager_factory=_env_factory(active=False),
        developer_factory=dev_factory,
        reviewer_factory=rev_factory,
        drupal_reviewer_factory=drupal_factory,
        post_execute_runner=_post_runner(captured),
        ticket_context_fetcher=lambda t: "ctx",
    )

    assert "drupal.findings_unresolved" in result.artifacts
    assert "gitlab.drupal_findings_posted" in result.artifacts
    # The post runner saw the drupal_findings.
    post_kwargs = next(e[1] for e in captured if e[0] == "post")
    assert post_kwargs["drupal_findings"] is not None


# ------------------------------------------------------------------ revise


def test_run_revise_no_feedback_short_circuits(
    orchestrator, db, planned_worktree
):
    repo = ExecutionRepository(db)
    options = ExecuteOptions(revise=True, max_iterations=2)
    options_blob = to_metadata_options(options)
    execution = repo.create(
        "PROJ-5", "PROJ", ExecutionKind.EXECUTE, options=options_blob
    )

    dev_factory, dev = _developer_factory(
        revision_results=[{"feedback_count": 0,
                           "tasks_completed": 0, "tasks_failed": 0}],
    )

    captured: List[Any] = []
    result = run_revise(
        orchestrator,
        ticket_id="PROJ-5",
        project="PROJ",
        options=options,
        execution_id=execution.id,
        worktree_factory=_worktree_factory(planned_worktree),
        env_manager_factory=_env_factory(active=False),
        developer_factory=dev_factory,
        post_execute_runner=_post_runner(captured),
    )

    assert "revise.no_feedback" in result.artifacts
    # Post runner not invoked when there's nothing to revise.
    assert captured == []


def test_run_revise_calls_post_runner_with_revision_result(
    orchestrator, db, planned_worktree
):
    repo = ExecutionRepository(db)
    options = ExecuteOptions(revise=True, max_iterations=2)
    options_blob = to_metadata_options(options)
    execution = repo.create(
        "PROJ-6", "PROJ", ExecutionKind.EXECUTE, options=options_blob
    )

    revision_result = {
        "feedback_count": 2,
        "tasks_completed": 2,
        "tasks_failed": 0,
        "test_results": {"success": True},
        "config_validation": {"success": True},
        "responses_posted": 1,
        "mr_url": "https://gitlab.example/foo/-/merge_requests/2",
    }
    dev_factory, dev = _developer_factory(
        revision_results=[revision_result]
    )

    captured: List[Any] = []
    result = run_revise(
        orchestrator,
        ticket_id="PROJ-6",
        project="PROJ",
        options=options,
        execution_id=execution.id,
        worktree_factory=_worktree_factory(planned_worktree),
        env_manager_factory=_env_factory(active=False),
        developer_factory=dev_factory,
        post_execute_runner=_post_runner(captured),
    )

    assert "agent.python_developer" in result.artifacts
    post_kwargs = next(e[1] for e in captured if e[0] == "post")
    assert post_kwargs["revision_result"] is revision_result
    # The runner emits the post-execute artifacts; mr_ready is suppressed
    # in revise mode by ``_post_runner`` here.
    assert "gitlab.mr_ready" not in result.artifacts
    assert "gitlab.decision_log_posted" in result.artifacts


def test_run_revise_drupal_self_fix_records_developer_result(
    orchestrator, db, planned_worktree, monkeypatch
):
    """Parity with run_execute: the drupal self-fix developer call inside
    run_revise must record its result on the execution row and add the
    ``drupal.self_fix_attempted`` artifact, so a remote viewer can see the
    extra developer turn took place."""
    monkeypatch.setattr(
        "src.config_loader.get_config",
        _config_factory(stack_type="drupal-10")(),
    )
    repo = ExecutionRepository(db)
    options = ExecuteOptions(revise=True, max_iterations=2)
    options_blob = to_metadata_options(options)
    execution = repo.create(
        "PROJ-7", "PROJ", ExecutionKind.EXECUTE, options=options_blob
    )

    # Initial revision has feedback (so we don't short-circuit), then the
    # drupal-fix revision is the second call.
    initial_revision = {
        "feedback_count": 2, "tasks_completed": 1, "tasks_failed": 0,
    }
    drupal_fix = {
        "feedback_count": 0, "tasks_completed": 1, "tasks_failed": 0,
    }
    dev_factory, dev = _developer_factory(
        revision_results=[initial_revision, drupal_fix],
    )
    drupal_factory, _ = _drupal_factory(
        results=[
            {"approved": False, "findings": [
                {"id": "x", "severity": "MAJOR", "title": "fix me",
                 "file": "a.php"}
            ], "feedback": ["fix me"], "review_data": {}},
            {"approved": True, "findings": [], "feedback": [],
             "review_data": {}},
        ]
    )

    captured: List[Any] = []
    result = run_revise(
        orchestrator,
        ticket_id="PROJ-7",
        project="PROJ",
        options=options,
        execution_id=execution.id,
        worktree_factory=_worktree_factory(planned_worktree),
        env_manager_factory=_env_factory(active=False),
        developer_factory=dev_factory,
        drupal_reviewer_factory=drupal_factory,
        post_execute_runner=_post_runner(captured),
        ticket_context_fetcher=lambda t: "ctx",
    )

    # Self-fix marker present.
    assert "drupal.self_fix_attempted" in result.artifacts
    # Two run_revision calls: initial + the drupal self-fix.
    assert dev.run_revision.call_count == 2
    # The drupal-fix developer result is stored on the row.
    persisted = repo.list_agent_results(execution.id)
    fix_records = [r for r in persisted if r["agent"] == dev.agent_name]
    assert len(fix_records) >= 2  # initial revision + at least one fix
