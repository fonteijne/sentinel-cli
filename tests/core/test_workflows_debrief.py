"""Tests for ``run_debrief`` follow-up ticket handling.

These tests cover the gap closed in this PR: a debrief that surfaces a
follow-up ticket creates / links it via Jira instead of being rejected
at runtime as previously.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.events import EventBus
from src.core.execution.models import ExecutionKind
from src.core.execution.options import (
    DebriefOptions,
    to_metadata_options,
)
from src.core.execution.orchestrator import Orchestrator
from src.core.execution.repository import ExecutionRepository
from src.core.execution.workflows import run_debrief
from src.core.persistence import connect, ensure_initialized


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


def _agent_factory(action: str, **extra):
    agent = MagicMock()
    agent.agent_name = "functional_debrief"
    agent.run.return_value = {"action": action, **extra}
    return lambda: agent, agent


def _wt_factory(path: Path, fail: bool = False):
    mgr = MagicMock()
    if fail:
        mgr.create_worktree.side_effect = RuntimeError("no codebase")
    else:
        mgr.create_worktree.return_value = path
    return lambda: mgr


def test_run_debrief_creates_follow_up_ticket(
    orchestrator, db, tmp_path
):
    """When the debrief agent surfaces a follow-up payload, run_debrief
    creates a new Jira ticket and posts a linking comment back."""
    repo = ExecutionRepository(db)
    options = DebriefOptions()
    execution = repo.create(
        "PROJ-1", "PROJ", ExecutionKind.DEBRIEF,
        options=to_metadata_options(options),
    )
    agent_factory, agent = _agent_factory(
        action="proposed_closure",
        follow_up={
            "summary": "Add audit log for X",
            "description": "client wants an audit log",
            "issue_type": "Task",
            "priority": "Medium",
        },
    )
    jira = MagicMock()
    jira.create_ticket.return_value = {"key": "PROJ-99"}

    result = run_debrief(
        orchestrator,
        ticket_id="PROJ-1",
        project="PROJ",
        options=options,
        execution_id=execution.id,
        worktree_factory=_wt_factory(tmp_path / "wt"),
        debrief_agent_factory=agent_factory,
        jira_factory=lambda: jira,
    )

    assert "debrief.follow_up_created" in result.artifacts
    assert result.extra["follow_up_created_ticket"] == "PROJ-99"
    jira.create_ticket.assert_called_once()
    create_kwargs = jira.create_ticket.call_args.kwargs
    assert create_kwargs["project_key"] == "PROJ"
    assert create_kwargs["summary"] == "Add audit log for X"
    # And a linking comment is posted on the original ticket.
    jira.add_comment.assert_called_once()
    call = jira.add_comment.call_args
    assert call.args[0] == "PROJ-1"
    assert "PROJ-99" in call.args[1]


def test_run_debrief_links_existing_follow_up_ticket(
    orchestrator, db, tmp_path
):
    """When ``options.follow_up_ticket`` is supplied, link it instead of
    creating a new one."""
    repo = ExecutionRepository(db)
    options = DebriefOptions(follow_up_ticket="PROJ-100")
    execution = repo.create(
        "PROJ-1", "PROJ", ExecutionKind.DEBRIEF,
        options=to_metadata_options(options),
    )
    agent_factory, _agent = _agent_factory(action="validated")
    jira = MagicMock()

    result = run_debrief(
        orchestrator,
        ticket_id="PROJ-1",
        project="PROJ",
        options=options,
        execution_id=execution.id,
        worktree_factory=_wt_factory(tmp_path / "wt"),
        debrief_agent_factory=agent_factory,
        jira_factory=lambda: jira,
    )

    assert "debrief.follow_up_linked" in result.artifacts
    assert result.extra["follow_up_linked_ticket"] == "PROJ-100"
    jira.create_ticket.assert_not_called()
    jira.add_comment.assert_called_once()
    call = jira.add_comment.call_args
    assert call.args[0] == "PROJ-1"
    assert "PROJ-100" in call.args[1]


def test_run_debrief_follow_up_failure_is_recorded_but_not_fatal(
    orchestrator, db, tmp_path
):
    repo = ExecutionRepository(db)
    options = DebriefOptions()
    execution = repo.create(
        "PROJ-1", "PROJ", ExecutionKind.DEBRIEF,
        options=to_metadata_options(options),
    )
    agent_factory, _agent = _agent_factory(
        action="proposed_closure",
        follow_up={"summary": "x", "description": "y"},
    )
    jira = MagicMock()
    jira.create_ticket.side_effect = RuntimeError("Jira down")

    result = run_debrief(
        orchestrator,
        ticket_id="PROJ-1",
        project="PROJ",
        options=options,
        execution_id=execution.id,
        worktree_factory=_wt_factory(tmp_path / "wt"),
        debrief_agent_factory=agent_factory,
        jira_factory=lambda: jira,
    )

    # Workflow doesn't crash — and the debrief artifact is still recorded
    # so the run is not a no-op.
    assert "agent.functional_debrief" in result.artifacts
    assert "debrief.follow_up_failed" in result.artifacts
    assert result.extra["follow_up_error"] == "Jira down"


def test_run_debrief_without_follow_up_does_not_call_jira(
    orchestrator, db, tmp_path
):
    repo = ExecutionRepository(db)
    options = DebriefOptions()
    execution = repo.create(
        "PROJ-1", "PROJ", ExecutionKind.DEBRIEF,
        options=to_metadata_options(options),
    )
    agent_factory, _agent = _agent_factory(action="awaiting_reply")
    jira = MagicMock()

    result = run_debrief(
        orchestrator,
        ticket_id="PROJ-1",
        project="PROJ",
        options=options,
        execution_id=execution.id,
        worktree_factory=_wt_factory(tmp_path / "wt"),
        debrief_agent_factory=agent_factory,
        jira_factory=lambda: jira,
    )

    assert "agent.functional_debrief" in result.artifacts
    jira.create_ticket.assert_not_called()
    jira.add_comment.assert_not_called()
