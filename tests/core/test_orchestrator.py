"""Tests for src.core.execution.orchestrator.Orchestrator."""

from __future__ import annotations

import threading
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from src.core.events import CostAccrued, EventBus
from src.core.execution.models import ExecutionKind, ExecutionStatus
from src.core.execution.orchestrator import (
    DebriefResult,
    ExecuteResult,
    Orchestrator,
    OrchestratorCancelled,
    PlanResult,
)
from src.core.execution.repository import ExecutionRepository
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


def _event_types(repo: ExecutionRepository, execution_id: str) -> list[str]:
    return [row["type"] for row in repo.iter_events(execution_id)]


def _seed_execution(
    repo: ExecutionRepository, kind: ExecutionKind = ExecutionKind.PLAN
) -> str:
    execution = repo.create(ticket_id="T-1", project="ACME", kind=kind)
    repo.set_status(execution.id, ExecutionStatus.RUNNING)
    return execution.id


# ----------------------------------------------------------- baseline / smoke

def test_run_happy_path_emits_started_and_completed(db):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    with orc.run(
        ticket_id="T-1", project="ACME", kind=ExecutionKind.PLAN
    ) as execution:
        assert execution.status == ExecutionStatus.QUEUED

    got = repo.get(execution.id)
    assert got is not None
    assert got.status == ExecutionStatus.SUCCEEDED

    types = _event_types(repo, execution.id)
    assert "execution.started" in types
    assert "execution.completed" in types


def test_run_failure_path_records_failed_and_reraises(db):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    with pytest.raises(RuntimeError, match="boom"):
        with orc.run(
            ticket_id="T-1", project="ACME", kind=ExecutionKind.PLAN
        ) as execution:
            raise RuntimeError("boom")

    got = repo.get(execution.id)
    assert got is not None
    assert got.status == ExecutionStatus.FAILED
    assert got.error is not None
    assert "boom" in got.error

    types = _event_types(repo, execution.id)
    assert "execution.failed" in types


def test_cost_subscriber_updates_execution(db):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    with orc.run(
        ticket_id="T-1", project="ACME", kind=ExecutionKind.PLAN
    ) as execution:
        bus.publish(
            CostAccrued(
                execution_id=execution.id, tokens_in=0, tokens_out=0, cents=13
            )
        )

    got = repo.get(execution.id)
    assert got is not None
    assert got.cost_cents == 13


def test_set_phase_publishes_phase_changed_event(db):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    with orc.run(
        ticket_id="T-1", project="ACME", kind=ExecutionKind.PLAN
    ) as execution:
        orc.set_phase(execution.id, "implementing")

    phase_rows = [
        row
        for row in repo.iter_events(execution.id)
        if row["type"] == "phase.changed"
    ]
    assert len(phase_rows) == 1
    assert phase_rows[0]["payload"].get("phase") == "implementing"


# ------------------------------------------------------------------- Task 1.1

def test_set_phase_raises_when_cancelled(db):
    """set_phase raises OrchestratorCancelled and does NOT publish phase.changed."""
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    cancel = threading.Event()
    orc = Orchestrator(repo, bus, cancel_flag=cancel)

    execution_id = _seed_execution(repo)
    cancel.set()

    with pytest.raises(OrchestratorCancelled):
        orc.set_phase(execution_id, "implementing")

    # No phase.changed event should have been published.
    types = _event_types(repo, execution_id)
    assert "phase.changed" not in types


def test_set_phase_ok_when_cancel_flag_absent(db):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)  # no cancel_flag

    execution_id = _seed_execution(repo)
    orc.set_phase(execution_id, "implementing")
    assert "phase.changed" in _event_types(repo, execution_id)


# ---------------------------------------------------- Task 1.2 — plan method

def _make_plan_agent(result: Dict[str, Any]) -> MagicMock:
    agent = MagicMock()
    agent.agent_name = "plan_generator"
    agent.session_id = None
    agent.run.return_value = result
    return agent


def test_plan_happy_path_records_result_and_completes(db, monkeypatch, tmp_path):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    execution_id = _seed_execution(repo, ExecutionKind.PLAN)

    # Jira
    mock_jira = MagicMock()
    mock_jira.get_ticket.return_value = {"summary": "test ticket"}
    monkeypatch.setattr(
        "src.jira_factory.get_jira_client", lambda: mock_jira
    )

    # WorktreeManager
    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    mock_worktree_mgr = MagicMock()
    mock_worktree_mgr.create_worktree.return_value = worktree_path
    monkeypatch.setattr(
        "src.worktree_manager.WorktreeManager", lambda: mock_worktree_mgr
    )

    agent_result = {"action": "posted", "plan_path": str(worktree_path / "plan.md")}
    plan_agent = _make_plan_agent(agent_result)
    monkeypatch.setattr(
        "src.agents.plan_generator.PlanGeneratorAgent", lambda: plan_agent
    )

    result = orc.plan(execution_id, force=False, prompt=None)

    assert isinstance(result, PlanResult)
    assert result.status == ExecutionStatus.SUCCEEDED
    assert result.details == agent_result

    # Agent was wired to the bus and invoked with expected kwargs.
    plan_agent.attach_events.assert_called_once()
    call_kwargs = plan_agent.run.call_args.kwargs
    assert call_kwargs["ticket_id"] == "T-1"
    assert call_kwargs["worktree_path"] == worktree_path
    assert call_kwargs["force"] is False
    assert call_kwargs["user_prompt"] is None

    # Row transitioned to succeeded; agent result persisted; events emitted.
    refreshed = repo.get(execution_id)
    assert refreshed is not None
    assert refreshed.status == ExecutionStatus.SUCCEEDED

    types = _event_types(repo, execution_id)
    assert "phase.changed" in types
    assert "agent.started" in types
    assert "agent.finished" in types
    assert "execution.completed" in types

    agent_results = repo.list_agent_results(execution_id)
    assert len(agent_results) == 1
    assert agent_results[0]["agent"] == "plan_generator"


def test_plan_failure_marks_failed_and_reraises(db, monkeypatch, tmp_path):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    execution_id = _seed_execution(repo, ExecutionKind.PLAN)

    monkeypatch.setattr(
        "src.jira_factory.get_jira_client", lambda: MagicMock(
            get_ticket=MagicMock(return_value={"summary": "x"})
        )
    )

    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()
    mock_worktree_mgr = MagicMock()
    mock_worktree_mgr.create_worktree.return_value = worktree_path
    monkeypatch.setattr(
        "src.worktree_manager.WorktreeManager", lambda: mock_worktree_mgr
    )

    plan_agent = MagicMock()
    plan_agent.agent_name = "plan_generator"
    plan_agent.session_id = None
    plan_agent.run.side_effect = RuntimeError("agent blew up")
    monkeypatch.setattr(
        "src.agents.plan_generator.PlanGeneratorAgent", lambda: plan_agent
    )

    with pytest.raises(RuntimeError, match="agent blew up"):
        orc.plan(execution_id)

    refreshed = repo.get(execution_id)
    assert refreshed is not None
    assert refreshed.status == ExecutionStatus.FAILED
    assert refreshed.error and "agent blew up" in refreshed.error
    types = _event_types(repo, execution_id)
    assert "execution.failed" in types


def test_plan_cancelled_returns_without_completing(db, monkeypatch, tmp_path):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    cancel = threading.Event()
    cancel.set()  # cancel before anything runs
    orc = Orchestrator(repo, bus, cancel_flag=cancel)

    execution_id = _seed_execution(repo, ExecutionKind.PLAN)

    monkeypatch.setattr(
        "src.jira_factory.get_jira_client", lambda: MagicMock(
            get_ticket=MagicMock(return_value={"summary": "x"})
        )
    )
    monkeypatch.setattr(
        "src.worktree_manager.WorktreeManager",
        lambda: MagicMock(create_worktree=MagicMock(return_value=tmp_path)),
    )
    monkeypatch.setattr(
        "src.agents.plan_generator.PlanGeneratorAgent",
        lambda: _make_plan_agent({}),
    )

    result = orc.plan(execution_id)
    assert result.status == ExecutionStatus.CANCELLED

    # Row NOT transitioned to succeeded — post-mortem owns terminal state.
    refreshed = repo.get(execution_id)
    assert refreshed is not None
    assert refreshed.status == ExecutionStatus.RUNNING


def test_plan_raises_when_execution_missing(db):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    with pytest.raises(ValueError, match="not found"):
        orc.plan("does-not-exist")


# --------------------------------------------------- Task 1.3 — debrief method

def test_debrief_emits_debrief_turn_event(db, monkeypatch, tmp_path):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    execution_id = _seed_execution(repo, ExecutionKind.DEBRIEF)

    monkeypatch.setattr(
        "src.jira_factory.get_jira_client", lambda: MagicMock(
            get_ticket=MagicMock(return_value={"summary": "x"})
        )
    )
    monkeypatch.setattr(
        "src.worktree_manager.WorktreeManager",
        lambda: MagicMock(create_worktree=MagicMock(return_value=tmp_path)),
    )

    agent = MagicMock()
    agent.agent_name = "functional_debrief"
    agent.session_id = None
    agent.run.return_value = {
        "action": "posted",
        "iteration_count": 1,
        "debrief_data": {"summary": "hi", "questions": []},
    }
    monkeypatch.setattr(
        "src.agents.functional_debrief.FunctionalDebriefAgent", lambda: agent
    )

    result = orc.debrief(execution_id, prompt="please focus on X")

    assert isinstance(result, DebriefResult)
    assert result.status == ExecutionStatus.SUCCEEDED

    types = _event_types(repo, execution_id)
    assert "debrief.turn" in types
    assert "agent.started" in types
    assert "agent.finished" in types
    assert "execution.completed" in types

    # DebriefTurn payload sanity: prompt_chars reflects the operator instruction.
    turn_rows = [r for r in repo.iter_events(execution_id) if r["type"] == "debrief.turn"]
    assert turn_rows[0]["payload"]["prompt_chars"] == len("please focus on X")
    assert turn_rows[0]["payload"]["turn_index"] == 1


def test_debrief_emits_revision_requested_when_signalled(db, monkeypatch, tmp_path):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    execution_id = _seed_execution(repo, ExecutionKind.DEBRIEF)

    monkeypatch.setattr(
        "src.jira_factory.get_jira_client",
        lambda: MagicMock(get_ticket=MagicMock(return_value={"summary": "x"})),
    )
    monkeypatch.setattr(
        "src.worktree_manager.WorktreeManager",
        lambda: MagicMock(create_worktree=MagicMock(return_value=tmp_path)),
    )

    agent = MagicMock()
    agent.agent_name = "functional_debrief"
    agent.session_id = None
    agent.run.return_value = {
        "action": "revise",
        "iteration_count": 2,
        "revise": True,
        "reason": "client changed scope",
    }
    monkeypatch.setattr(
        "src.agents.functional_debrief.FunctionalDebriefAgent", lambda: agent
    )

    orc.debrief(execution_id)

    types = _event_types(repo, execution_id)
    assert "revision.requested" in types


# -------------------------------------------------- Task 1.4 — execute method

class _FakeEnvInfo:
    def __init__(self, active: bool = False):
        self.active = active
        self.services = []
        self.tooling = {}


def _seed_worker_row(conn, execution_id: str) -> None:
    """register_compose_project writes to workers. Seed a minimal row."""
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO workers("
        "execution_id, pid, started_at, last_heartbeat_at, compose_projects"
        ") VALUES (?, ?, ?, ?, '[]')",
        (execution_id, 12345, ts, ts),
    )
    conn.commit()


def test_execute_registers_compose_project_before_up(db, monkeypatch, tmp_path):
    """compose project must be registered BEFORE env_mgr.setup runs."""
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    execution_id = _seed_execution(repo, ExecutionKind.EXECUTE)
    _seed_worker_row(db, execution_id)

    worktree_path = tmp_path / "worktree"
    (worktree_path / ".agents" / "plans").mkdir(parents=True)
    (worktree_path / ".agents" / "plans" / "T-1.md").write_text("# plan")

    monkeypatch.setattr(
        "src.worktree_manager.WorktreeManager",
        lambda: MagicMock(get_worktree_path=MagicMock(return_value=worktree_path)),
    )

    call_order: list[str] = []

    def _fake_register(self, exec_id, project_name):
        call_order.append(f"register:{project_name}")
        # Call the real method so workers row is updated
        type(self).register_compose_project.__wrapped__  # type: ignore[attr-defined]

    # Patch the repository to record the call order.
    orig_register = repo.register_compose_project

    def _record_register(exec_id, project_name):
        call_order.append(f"register:{project_name}")
        orig_register(exec_id, project_name)

    monkeypatch.setattr(repo, "register_compose_project", _record_register)

    class _EnvMgr:
        def setup(self, wtp, tid):
            call_order.append("env.setup")
            return _FakeEnvInfo(active=False)

        def teardown(self, tid):
            return True

    monkeypatch.setattr(
        "src.environment_manager.EnvironmentManager", _EnvMgr
    )

    # Config: empty stack_type → PythonDeveloperAgent
    class _Config:
        def get_project_config(self, p):
            return {"stack_type": ""}

    monkeypatch.setattr("src.config_loader.get_config", lambda: _Config())

    # Developer + security agents
    developer = MagicMock()
    developer.agent_name = "python_developer"
    developer.session_id = None
    developer.run.return_value = {
        "tasks_completed": 1,
        "tasks_failed": 0,
        "test_results": {"success": True, "return_code": 0},
    }
    monkeypatch.setattr(
        "src.agents.python_developer.PythonDeveloperAgent", lambda: developer
    )

    security = MagicMock()
    security.agent_name = "security_reviewer"
    security.session_id = None
    security.run.return_value = {"approved": True, "findings": []}
    monkeypatch.setattr(
        "src.agents.security_reviewer.SecurityReviewerAgent", lambda: security
    )

    # DrupalDeveloperAgent is imported but should not be invoked with stack_type="".
    monkeypatch.setattr(
        "src.agents.drupal_developer.DrupalDeveloperAgent", lambda: MagicMock()
    )
    monkeypatch.setattr(
        "src.agents.drupal_reviewer.DrupalReviewerAgent", lambda: MagicMock()
    )

    result = orc.execute(execution_id, max_iterations=1, no_env=False)

    assert isinstance(result, ExecuteResult)
    assert result.status == ExecutionStatus.SUCCEEDED

    # register_compose_project must precede env.setup
    register_idx = next(
        i for i, c in enumerate(call_order) if c.startswith("register:")
    )
    env_idx = call_order.index("env.setup")
    assert register_idx < env_idx, call_order

    # Compose project name is the ticket prefix lowercased.
    assert call_order[register_idx] == "register:t"

    # Event coverage
    types = _event_types(repo, execution_id)
    assert "phase.changed" in types
    assert "test.result" in types
    assert "execution.completed" in types

    # metadata.compose_projects was updated
    refreshed = repo.get(execution_id)
    assert refreshed is not None
    assert "t" in refreshed.metadata.get("compose_projects", [])


def test_execute_emits_finding_posted_on_security_rejection(db, monkeypatch, tmp_path):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)

    execution_id = _seed_execution(repo, ExecutionKind.EXECUTE)
    _seed_worker_row(db, execution_id)

    worktree_path = tmp_path / "worktree"
    worktree_path.mkdir()

    monkeypatch.setattr(
        "src.worktree_manager.WorktreeManager",
        lambda: MagicMock(get_worktree_path=MagicMock(return_value=worktree_path)),
    )

    class _EnvMgr:
        def setup(self, *a, **kw):
            return _FakeEnvInfo(active=False)

        def teardown(self, *a, **kw):
            return True

    monkeypatch.setattr(
        "src.environment_manager.EnvironmentManager", _EnvMgr
    )

    class _Config:
        def get_project_config(self, p):
            return {"stack_type": ""}

    monkeypatch.setattr("src.config_loader.get_config", lambda: _Config())

    developer = MagicMock()
    developer.agent_name = "python_developer"
    developer.session_id = None
    developer.run.return_value = {
        "tasks_completed": 1,
        "tasks_failed": 0,
        "test_results": {"success": False, "return_code": 1},
    }
    monkeypatch.setattr(
        "src.agents.python_developer.PythonDeveloperAgent", lambda: developer
    )

    security = MagicMock()
    security.agent_name = "security_reviewer"
    security.session_id = None
    security.run.return_value = {
        "approved": False,
        "findings": [
            {"severity": "high", "description": "sql injection smell"},
        ],
    }
    monkeypatch.setattr(
        "src.agents.security_reviewer.SecurityReviewerAgent", lambda: security
    )
    monkeypatch.setattr(
        "src.agents.drupal_developer.DrupalDeveloperAgent", lambda: MagicMock()
    )
    monkeypatch.setattr(
        "src.agents.drupal_reviewer.DrupalReviewerAgent", lambda: MagicMock()
    )

    with pytest.raises(RuntimeError, match="security review"):
        orc.execute(execution_id, max_iterations=1, no_env=True)

    types = _event_types(repo, execution_id)
    assert "finding.posted" in types
    assert "execution.failed" in types


# ------------------------------------------------- Task 1.5 — agent bookends

def test_agent_run_context_emits_started_and_finished(db):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)
    execution_id = _seed_execution(repo)

    agent = MagicMock()
    agent.agent_name = "plan_generator"
    agent.session_id = "session-abc"

    with orc._agent_run(execution_id, agent):
        pass

    types = _event_types(repo, execution_id)
    assert "agent.started" in types
    assert "agent.finished" in types
    started_rows = [
        r for r in repo.iter_events(execution_id) if r["type"] == "agent.started"
    ]
    assert started_rows[0]["payload"]["agent"] == "plan_generator"
    assert started_rows[0]["payload"]["session_id"] == "session-abc"


def test_agent_run_context_still_emits_finished_on_exception(db):
    repo = ExecutionRepository(db)
    bus = EventBus(db)
    orc = Orchestrator(repo, bus)
    execution_id = _seed_execution(repo)

    agent = MagicMock()
    agent.agent_name = "plan_generator"
    agent.session_id = None

    with pytest.raises(ValueError):
        with orc._agent_run(execution_id, agent):
            raise ValueError("boom")

    types = _event_types(repo, execution_id)
    assert "agent.started" in types
    assert "agent.finished" in types
