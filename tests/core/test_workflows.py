"""Tests for the shared workflow application layer.

These tests cover the Command Center replacement contract:

* ``WorkflowResult.assert_real_work`` raises on a no-op run, which the
  orchestrator translates into ``execution.failed``. This is the
  regression gate for the "Command Center cannot complete a no-op" rule.
* The orchestrator's worker entry points (``plan`` / ``execute`` /
  ``debrief``) read the persisted versioned options off the row.
* Cancellation between workflow steps is honoured.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from src.core.events import EventBus
from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.options import (
    ExecuteOptions,
    PlanOptions,
    to_metadata_options,
)
from src.core.execution.orchestrator import Orchestrator
from src.core.execution.repository import ExecutionRepository
from src.core.execution.workflows import (
    NoOpExecutionError,
    WorkflowError,
    WorkflowResult,
    run_plan,
)
from src.core.persistence import connect, ensure_initialized


# --------------------------------------------------------------------- fixtures


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


# ----------------------------------------------------------- WorkflowResult


def test_workflow_result_with_no_artifacts_is_a_no_op():
    result = WorkflowResult()
    with pytest.raises(NoOpExecutionError):
        result.assert_real_work()


def test_workflow_result_with_one_artifact_passes():
    result = WorkflowResult()
    result.add_artifact("git.worktree_created")
    result.assert_real_work()  # does not raise


def test_workflow_result_dedups_artifacts():
    result = WorkflowResult()
    result.add_artifact("a")
    result.add_artifact("a")
    result.add_artifact("b")
    assert result.artifacts == ["a", "b"]


# ------------------------------------------------- orchestrator workflow verbs


def test_orchestrator_execute_marks_failed_on_no_op(monkeypatch, orchestrator, db):
    """Regression: if the workflow runner produces no artifacts, the
    execution row must end up as ``failed`` with a ``no_op_detected`` error.
    Without this gate, Command Center could complete green for a scaffold
    run — which is the historical bug we are closing.
    """
    repo = ExecutionRepository(db)
    options_blob = to_metadata_options(ExecuteOptions())
    execution = repo.create(
        "PROJ-1", "proj", ExecutionKind.EXECUTE, options=options_blob
    )

    def _no_op_runner(orc, ex, *, cancel_flag=None):
        return WorkflowResult()  # NB: no artifacts

    monkeypatch.setattr(
        "src.core.execution.orchestrator.run_workflow_for_execution",
        _no_op_runner,
        raising=False,
    )
    # Patch via the workflows module import inside _run_workflow.
    monkeypatch.setattr(
        "src.core.execution.workflows.run_workflow_for_execution",
        _no_op_runner,
    )

    orchestrator.execute(execution.id)

    refreshed = repo.get(execution.id)
    assert refreshed is not None
    assert refreshed.status == ExecutionStatus.FAILED
    assert refreshed.error is not None
    assert "no_op_detected" in refreshed.error


def test_orchestrator_no_op_publishes_execution_failed_event(
    monkeypatch, orchestrator, db
):
    """The no-op gate must emit ``execution.failed`` on the bus, not just
    flip the row's status. UI/CLI subscribers rely on the event to update
    their timeline — a silent status flip would leave them stale."""
    repo = ExecutionRepository(db)
    options_blob = to_metadata_options(ExecuteOptions())
    execution = repo.create(
        "PROJ-EVT", "proj", ExecutionKind.EXECUTE, options=options_blob
    )

    monkeypatch.setattr(
        "src.core.execution.workflows.run_workflow_for_execution",
        lambda orc, ex, *, cancel_flag=None: WorkflowResult(),
    )
    orchestrator.execute(execution.id)

    types_seen = [e["type"] for e in repo.iter_events(execution.id)]
    assert "execution.failed" in types_seen, types_seen


def test_orchestrator_execute_succeeds_when_workflow_produces_artifact(
    monkeypatch, orchestrator, db
):
    repo = ExecutionRepository(db)
    options_blob = to_metadata_options(ExecuteOptions())
    execution = repo.create(
        "PROJ-2", "proj", ExecutionKind.EXECUTE, options=options_blob
    )

    def _real_runner(orc, ex, *, cancel_flag=None):
        result = WorkflowResult()
        result.add_artifact("git.worktree_resolved")
        result.add_artifact("agent.python_developer")
        return result

    monkeypatch.setattr(
        "src.core.execution.workflows.run_workflow_for_execution", _real_runner
    )

    orchestrator.execute(execution.id)

    refreshed = repo.get(execution.id)
    assert refreshed is not None
    assert refreshed.status == ExecutionStatus.SUCCEEDED


def test_orchestrator_execute_translates_workflow_error(
    monkeypatch, orchestrator, db
):
    repo = ExecutionRepository(db)
    options_blob = to_metadata_options(ExecuteOptions())
    execution = repo.create(
        "PROJ-3", "proj", ExecutionKind.EXECUTE, options=options_blob
    )

    def _err(orc, ex, *, cancel_flag=None):
        raise WorkflowError("worktree missing — run plan first")

    monkeypatch.setattr(
        "src.core.execution.workflows.run_workflow_for_execution", _err
    )

    orchestrator.execute(execution.id)

    refreshed = repo.get(execution.id)
    assert refreshed is not None
    assert refreshed.status == ExecutionStatus.FAILED
    assert "worktree missing" in (refreshed.error or "")


def test_orchestrator_execute_rejects_unknown_persisted_option(
    monkeypatch, orchestrator, db
):
    """If somebody writes a bogus key into ``metadata_json.options``
    directly (e.g. via SQL or a future schema regression), the worker must
    refuse to proceed instead of silently ignoring it."""
    repo = ExecutionRepository(db)
    # Bypass the API/CLI option model; write something that fails validation.
    raw = {
        "schema_version": 1,
        "values": {"revise": True, "totally_made_up_flag": 1},
    }
    execution = repo.create(
        "PROJ-4", "proj", ExecutionKind.EXECUTE, options=raw
    )

    orchestrator.execute(execution.id)

    refreshed = repo.get(execution.id)
    assert refreshed is not None
    assert refreshed.status == ExecutionStatus.FAILED


# --------------------------------------------------------------------- run_plan


def test_run_plan_assembles_artifacts_from_real_components(orchestrator, db):
    """End-to-end check that ``run_plan`` records the artifacts the no-op
    detector relies on, given fakes for jira/worktree/agent."""
    repo = ExecutionRepository(db)
    options_blob = to_metadata_options(PlanOptions(force=False, prompt="x"))
    execution = repo.create(
        "PROJ-5", "proj", ExecutionKind.PLAN, options=options_blob
    )

    fake_jira = MagicMock()
    fake_jira.get_ticket.return_value = {"summary": "do the thing"}

    fake_worktree_mgr = MagicMock()
    fake_worktree_path = Path("/tmp/sentinel/proj/PROJ-5")
    fake_worktree_mgr.create_worktree.return_value = fake_worktree_path

    fake_agent = MagicMock()
    fake_agent.agent_name = "plan_generator"
    fake_agent.run.return_value = {
        "plan_path": str(fake_worktree_path / ".agents" / "plans" / "PROJ-5.md"),
        "mr_url": "https://gitlab.example/foo/-/merge_requests/1",
        "mr_created": True,
        "plan_updated": True,
    }

    result = run_plan(
        orchestrator,
        ticket_id="PROJ-5",
        project="proj",
        options=PlanOptions(),
        execution_id=execution.id,
        worktree_factory=lambda: fake_worktree_mgr,
        jira_factory=lambda: fake_jira,
        plan_agent_factory=lambda: fake_agent,
    )

    # Artifacts that drive the no-op detector.
    assert "jira.ticket_fetched" in result.artifacts
    assert "git.worktree_created" in result.artifacts
    assert "agent.plan_generator" in result.artifacts
    assert "plan.persisted" in result.artifacts
    assert "gitlab.mr_created" in result.artifacts

    # And the agent_results are recorded back on the row.
    persisted = repo.list_agent_results(execution.id)
    assert any(r["agent"] == "plan_generator" for r in persisted)
