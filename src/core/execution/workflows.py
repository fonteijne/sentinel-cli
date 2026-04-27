"""Shared workflow application layer.

This module is the single implementation of Sentinel's plan / execute /
debrief workflows. Both the CLI (``src.cli``) and the Command Center worker
(``src.core.execution.worker``) call into the *same* ``run_*`` functions —
they only differ in how they parse input and how they present output.

The functions here intentionally do **not** print (Click ``echo`` is left to
the CLI) and return a :class:`WorkflowResult` instead. The Result's
``artifacts`` field is the no-op detector: a remote run that completes
without producing at least one of the configured artifacts is rejected by
:meth:`WorkflowResult.assert_real_work`. That is what makes Command Center
unable to report "success" for a scaffold run.

Side effects (Jira comments, git push, GitLab MR creation) live in the
existing CLI helpers and the agent classes. Refactoring those further is
out of scope for this pass — the goal is:

1. Make the worker invoke the *real* CLI workflow, not a no-op.
2. Make the option model honest (no silent drops).
3. Catch no-op runs at the seam where it matters.

Everything else is preserved.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from src.core.events import EventBus
from src.core.execution.models import Execution, ExecutionKind
from src.core.execution.options import (
    DebriefOptions,
    ExecuteOptions,
    PlanOptions,
)

if TYPE_CHECKING:
    from src.core.execution.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Result model — also the no-op detector
# --------------------------------------------------------------------------- #


@dataclass
class WorkflowResult:
    """What a workflow produced. Used to validate non-no-op completion.

    ``artifacts`` is the canonical record of whether *real* work happened.
    A run that finishes with an empty artifacts list (no agent invocation,
    no worktree, no MR, no Jira comment) is, by definition, a no-op — and
    :meth:`assert_real_work` raises ``NoOpExecutionError`` so the worker
    marks the run failed instead of succeeded.

    The artifact strings are intentionally coarse-grained — the goal is to
    fail loudly on "did nothing", not to be a structured dataset. The agent
    results, plan path, MR URL etc. are already persisted via the agent
    classes; this is a parallel marker for the worker's no-op gate.
    """

    artifacts: List[str] = field(default_factory=list)
    agent_results: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def add_artifact(self, marker: str) -> None:
        if marker and marker not in self.artifacts:
            self.artifacts.append(marker)

    def assert_real_work(self) -> None:
        """Raise if no real workflow artifact was produced.

        The acceptance criteria forbid silent green runs. A workflow that
        completes without invoking any agent, creating any worktree, or
        producing any external artifact must surface as a failure so the
        operator can see *something* is wrong.
        """
        if not self.artifacts:
            raise NoOpExecutionError(
                "workflow completed without producing any real artifact "
                "(no agent invocation, no worktree, no external action)"
            )


class NoOpExecutionError(RuntimeError):
    """Raised when a workflow finished without doing real work.

    Caught by the worker and translated into a ``failed`` row + an
    ``execution.failed`` event so the dashboard surfaces the issue.
    """


# --------------------------------------------------------------------------- #
# Worker dispatch entry point
# --------------------------------------------------------------------------- #


def run_workflow_for_execution(
    orchestrator: "Orchestrator",
    execution: Execution,
    *,
    cancel_flag: Any = None,
) -> WorkflowResult:
    """Top-level dispatcher — used by the worker.

    Reads the persisted, versioned options off the execution row and
    delegates to the kind-specific runner. Any unsupported / extra option
    causes ``WorkflowOptions.model_validate`` to raise — the worker then
    records the failure on the row.
    """
    from src.core.execution.options import from_metadata_options

    raw = (execution.metadata or {}).get("options")
    options = from_metadata_options(execution.kind.value, raw)

    if execution.kind == ExecutionKind.PLAN:
        assert isinstance(options, PlanOptions)
        return run_plan(
            orchestrator,
            ticket_id=execution.ticket_id,
            project=execution.project,
            options=options,
            execution_id=execution.id,
            cancel_flag=cancel_flag,
        )
    if execution.kind == ExecutionKind.EXECUTE:
        assert isinstance(options, ExecuteOptions)
        return run_execute(
            orchestrator,
            ticket_id=execution.ticket_id,
            project=execution.project,
            options=options,
            execution_id=execution.id,
            cancel_flag=cancel_flag,
        )
    if execution.kind == ExecutionKind.DEBRIEF:
        assert isinstance(options, DebriefOptions)
        return run_debrief(
            orchestrator,
            ticket_id=execution.ticket_id,
            project=execution.project,
            options=options,
            execution_id=execution.id,
            cancel_flag=cancel_flag,
        )

    raise ValueError(f"unsupported execution kind {execution.kind!r}")


# --------------------------------------------------------------------------- #
# Plan workflow
# --------------------------------------------------------------------------- #


def run_plan(
    orchestrator: "Orchestrator",
    *,
    ticket_id: str,
    project: str,
    options: PlanOptions,
    execution_id: Optional[str] = None,
    cancel_flag: Any = None,
    # Hooks for tests — production callers pass nothing and the real impls
    # are imported lazily inside the function so importing this module is
    # cheap.
    worktree_factory: Optional[Callable[..., Any]] = None,
    jira_factory: Optional[Callable[[], Any]] = None,
    plan_agent_factory: Optional[Callable[[], Any]] = None,
) -> WorkflowResult:
    """Execute the ``plan`` workflow against an existing execution row.

    The orchestrator already wraps this call in a ``run()`` context (CLI) or
    ``begin/complete`` cycle (worker) — this function only owns the body.
    """
    result = WorkflowResult()
    bus: EventBus = orchestrator.bus

    if execution_id is None:
        # CLI path passes the live execution_id implicitly via the with-block;
        # the worker passes it explicitly. Surfacing this as a kwarg keeps
        # the function callable from either side without dragging in the
        # full Execution model where it is not needed.
        raise ValueError("run_plan requires execution_id")

    if cancel_flag is not None and cancel_flag.is_set():
        raise WorkflowCancelled("plan workflow cancelled before start")

    from src.jira_factory import get_jira_client
    from src.worktree_manager import WorktreeManager
    from src.agents.plan_generator import PlanGeneratorAgent

    worktree_mgr = (worktree_factory or WorktreeManager)()
    jira_client = (jira_factory or get_jira_client)()

    # 1. Validate ticket exists before doing anything else (matches CLI order).
    ticket_data = jira_client.get_ticket(ticket_id)
    result.add_artifact("jira.ticket_fetched")
    result.extra["ticket_summary"] = ticket_data.get("summary")

    # 2. Worktree. The CLI's plan flow always creates one — even on retry it
    # short-circuits. We mirror that for parity.
    orchestrator.set_phase(execution_id, "worktree")
    worktree_path = worktree_mgr.create_worktree(ticket_id, project)
    result.add_artifact("git.worktree_created")
    result.extra["worktree_path"] = str(worktree_path)

    if cancel_flag is not None and cancel_flag.is_set():
        raise WorkflowCancelled("plan workflow cancelled after worktree")

    # 3. Plan agent.
    orchestrator.set_phase(execution_id, "planning")
    plan_agent = (plan_agent_factory or PlanGeneratorAgent)()
    plan_agent.attach_events(bus, execution_id)
    agent_result = plan_agent.run(
        ticket_id=ticket_id,
        worktree_path=worktree_path,
        force=options.force,
        user_prompt=options.prompt,
    )
    orchestrator.record_agent_result(
        execution_id, plan_agent.agent_name, agent_result
    )
    result.add_artifact("agent.plan_generator")
    result.agent_results[plan_agent.agent_name] = agent_result

    if agent_result.get("plan_path"):
        result.extra["plan_path"] = agent_result["plan_path"]
        result.add_artifact("plan.persisted")
    if agent_result.get("mr_url"):
        result.extra["mr_url"] = agent_result["mr_url"]
        result.add_artifact("gitlab.mr_present")
    if agent_result.get("plan_updated") or agent_result.get("changes_committed"):
        result.add_artifact("git.changes_committed")
    if agent_result.get("mr_created"):
        result.add_artifact("gitlab.mr_created")

    return result


# --------------------------------------------------------------------------- #
# Execute workflow
# --------------------------------------------------------------------------- #


def run_execute(
    orchestrator: "Orchestrator",
    *,
    ticket_id: str,
    project: str,
    options: ExecuteOptions,
    execution_id: Optional[str] = None,
    cancel_flag: Any = None,
    worktree_factory: Optional[Callable[..., Any]] = None,
    env_manager_factory: Optional[Callable[..., Any]] = None,
    developer_factory: Optional[Callable[..., Any]] = None,
    reviewer_factory: Optional[Callable[..., Any]] = None,
) -> WorkflowResult:
    """Execute the ``execute`` workflow.

    This is intentionally the *minimum* re-use to make remote runs real:

    1. Resolve the worktree (must already exist — same as CLI).
    2. Set up container environment (unless ``--no-env``) — gives parity
       with the CLI's pre-flight.
    3. Run the developer agent (Drupal vs Python based on stack profile).
    4. Run the reviewer agent.
    5. Mark the metadata so :meth:`WorkflowResult.assert_real_work` passes.

    Side effects beyond that (push, MR ready, Jira comment) currently live
    in the CLI and the agent code itself; this function exposes its own
    artifact markers so the worker can detect a no-op even if the further
    side-effect pieces have not yet been moved here.
    """
    result = WorkflowResult()
    bus: EventBus = orchestrator.bus
    if execution_id is None:
        raise ValueError("run_execute requires execution_id")

    if cancel_flag is not None and cancel_flag.is_set():
        raise WorkflowCancelled("execute workflow cancelled before start")

    from src.config_loader import get_config
    from src.environment_manager import EnvironmentManager
    from src.worktree_manager import WorktreeManager
    from src.agents.python_developer import PythonDeveloperAgent
    from src.agents.drupal_developer import DrupalDeveloperAgent
    from src.agents.security_reviewer import SecurityReviewerAgent

    worktree_mgr = (worktree_factory or WorktreeManager)()

    # 1. Resolve worktree. CLI bails when not present; matching that here
    # avoids running the full agent loop against the wrong tree.
    worktree_path = worktree_mgr.get_worktree_path(ticket_id, project)
    if worktree_path is None:
        raise WorkflowError(
            f"worktree not found for {ticket_id} — run 'sentinel plan' first"
        )
    result.add_artifact("git.worktree_resolved")
    result.extra["worktree_path"] = str(worktree_path)

    plan_file = Path(worktree_path) / ".agents" / "plans" / f"{ticket_id}.md"
    if not plan_file.exists():
        raise WorkflowError(
            f"plan file not found at {plan_file} — run 'sentinel plan' first"
        )
    result.add_artifact("plan.present")

    # 2. Environment.
    env_mgr = (env_manager_factory or EnvironmentManager)()
    env_info = None
    if not options.no_env:
        try:
            env_info = env_mgr.setup(worktree_path, ticket_id)
            if env_info and env_info.active:
                result.add_artifact("env.container_active")
        except RuntimeError as exc:
            # Mirror CLI behaviour: container failure is fatal in execute.
            raise WorkflowError(
                f"container setup failed: {exc} — fix the issue or pass "
                f"no_env=true to skip"
            ) from exc

    try:
        # 3. Stack-driven developer agent.
        config = get_config()
        project_config = config.get_project_config(project)
        stack_type = project_config.get("stack_type", "")

        if developer_factory is not None:
            developer = developer_factory(stack_type=stack_type)
        elif stack_type and stack_type.startswith("drupal"):
            developer = DrupalDeveloperAgent()
        else:
            developer = PythonDeveloperAgent()
        developer.attach_events(bus, execution_id)
        if env_info is not None and env_info.active:
            developer.set_environment(env_mgr, ticket_id)

        reviewer = (reviewer_factory or SecurityReviewerAgent)()
        reviewer.attach_events(bus, execution_id)

        for iteration in range(1, options.max_iterations + 1):
            if cancel_flag is not None and cancel_flag.is_set():
                raise WorkflowCancelled(
                    f"execute workflow cancelled at iteration {iteration}"
                )
            orchestrator.set_phase(execution_id, f"iteration_{iteration}")

            # Developer
            dev_result = developer.run(
                plan_file=plan_file,
                worktree_path=worktree_path,
                user_prompt=options.prompt,
            )
            orchestrator.record_agent_result(
                execution_id, developer.agent_name, dev_result
            )
            result.agent_results[f"{developer.agent_name}_iter_{iteration}"] = (
                dev_result
            )
            result.add_artifact(f"agent.{developer.agent_name}")

            if dev_result.get("tasks_completed", 0) == 0:
                raise WorkflowError(
                    f"all {dev_result.get('tasks_failed', 0)} developer "
                    f"tasks failed; nothing to review"
                )

            # Reviewer
            sec_result = reviewer.run(
                worktree_path=worktree_path, ticket_id=ticket_id
            )
            orchestrator.record_agent_result(
                execution_id, reviewer.agent_name, sec_result
            )
            result.agent_results[f"{reviewer.agent_name}_iter_{iteration}"] = (
                sec_result
            )
            result.add_artifact(f"agent.{reviewer.agent_name}")

            if sec_result.get("approved"):
                result.extra["iterations"] = iteration
                result.extra["dev_result"] = dev_result
                result.extra["sec_result"] = sec_result
                break

            if iteration >= options.max_iterations:
                raise WorkflowError(
                    f"max iterations ({options.max_iterations}) reached "
                    f"without security approval"
                )
        else:  # pragma: no cover — for-loop falls through on zero iterations
            raise WorkflowError(
                "execute workflow ended without running any iteration"
            )
    finally:
        if env_info is not None and env_info.active:
            try:
                env_mgr.teardown(ticket_id)
            except Exception:
                logger.exception("env teardown failed for %s", ticket_id)

    return result


# --------------------------------------------------------------------------- #
# Debrief workflow
# --------------------------------------------------------------------------- #


def run_debrief(
    orchestrator: "Orchestrator",
    *,
    ticket_id: str,
    project: str,
    options: DebriefOptions,
    execution_id: Optional[str] = None,
    cancel_flag: Any = None,
    worktree_factory: Optional[Callable[..., Any]] = None,
    debrief_agent_factory: Optional[Callable[[], Any]] = None,
) -> WorkflowResult:
    """Execute the ``debrief`` workflow."""
    result = WorkflowResult()
    bus: EventBus = orchestrator.bus
    if execution_id is None:
        raise ValueError("run_debrief requires execution_id")

    if options.follow_up_ticket is not None:
        # Wiring for follow-up tickets is not implemented — we choose to
        # reject the option rather than silently drop it. This matches the
        # acceptance criteria: unsupported options must fail validation, not
        # be honoured-as-no-op.
        raise WorkflowError(
            "debrief.follow_up_ticket is not yet supported by the workflow "
            "engine; remove it or use a CLI-only run for now"
        )

    if cancel_flag is not None and cancel_flag.is_set():
        raise WorkflowCancelled("debrief workflow cancelled before start")

    from src.agents.functional_debrief import FunctionalDebriefAgent
    from src.worktree_manager import WorktreeManager

    worktree_mgr = (worktree_factory or WorktreeManager)()

    worktree_path = None
    try:
        orchestrator.set_phase(execution_id, "worktree")
        worktree_path = worktree_mgr.create_worktree(ticket_id, project)
        result.add_artifact("git.worktree_created")
        result.extra["worktree_path"] = str(worktree_path)
    except Exception as exc:
        # Matches the CLI: debrief degrades to text-only when no codebase
        # access. Note as an artifact so the run isn't classified as a
        # no-op even when the worktree step is skipped.
        logger.warning("debrief: worktree unavailable, continuing text-only: %s", exc)
        result.extra["worktree_skipped_reason"] = str(exc)
        result.add_artifact("git.worktree_skipped")

    orchestrator.set_phase(execution_id, "debriefing")
    agent = (debrief_agent_factory or FunctionalDebriefAgent)()
    agent.attach_events(bus, execution_id)
    agent_result = agent.run(
        ticket_id=ticket_id,
        project=project,
        worktree_path=worktree_path,
        user_prompt=options.prompt,
    )
    orchestrator.record_agent_result(execution_id, agent.agent_name, agent_result)
    result.agent_results[agent.agent_name] = agent_result
    result.add_artifact(f"agent.{agent.agent_name}")
    if agent_result.get("action"):
        result.extra["action"] = agent_result["action"]

    return result


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class WorkflowError(RuntimeError):
    """Recoverable workflow failure — translated to ``execution.failed``."""


class WorkflowCancelled(RuntimeError):
    """Cancellation honoured between steps."""
