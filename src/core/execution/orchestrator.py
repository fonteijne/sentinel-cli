"""Orchestrator — owns execution lifecycle, event emission, and agent wiring.

Scope note (plan 01 / foundation, plan CC Phase 1):
    The orchestrator creates the ``Execution`` row, wires :class:`EventBus` +
    ``execution_id`` into the agents, publishes lifecycle events, and exposes
    ``plan()``/``execute()``/``debrief()`` that the out-of-process worker
    (``src.core.execution.worker``) dispatches to. The CLI (``src.cli``) still
    carries its own inlined plan/execute/debrief bodies for operator UX; a
    follow-up (CLI thinning pass) will collapse them onto the same methods.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, Optional

from src.core.events import (
    AgentFinished,
    AgentStarted,
    DebriefTurn,
    EventBus,
    ExecutionCancelled,
    ExecutionCompleted,
    ExecutionFailed,
    ExecutionStarted,
    FindingPosted,
    PhaseChanged,
    RevisionRequested,
    SentinelEvent,
    TestResultRecorded,
)
from src.core.execution.models import Execution, ExecutionKind, ExecutionStatus
from src.core.execution.repository import ExecutionRepository

if TYPE_CHECKING:
    from src.config_loader import ConfigLoader
    from src.session_tracker import SessionTracker

logger = logging.getLogger(__name__)


class OrchestratorCancelled(Exception):
    """Raised internally by Orchestrator at phase boundaries when cancel_flag is set."""


@dataclass
class PlanResult:
    status: ExecutionStatus
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecuteResult:
    status: ExecutionStatus
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DebriefResult:
    status: ExecutionStatus
    details: Dict[str, Any] = field(default_factory=dict)


class Orchestrator:
    """Coordinates an agent-driven run against a persistent ``Execution`` row.

    One orchestrator instance per run is the expected usage pattern. Instances
    are cheap to build — share the ``EventBus`` and ``ExecutionRepository``
    across calls if desired.

    Subscribers registered in :meth:`__init__`:
        * ``cost.accrued`` → :meth:`ExecutionRepository.add_cost`
    """

    def __init__(
        self,
        repo: ExecutionRepository,
        bus: EventBus,
        session_tracker: Optional["SessionTracker"] = None,
        config: Optional["ConfigLoader"] = None,
        cancel_flag: Any = None,
    ) -> None:
        self.repo = repo
        self.bus = bus
        self.session_tracker = session_tracker
        self.config = config
        # Cooperative cancel signal. ``cancel_flag.is_set()`` is checked at
        # phase boundaries inside ``set_phase``.
        self.cancel_flag = cancel_flag

        # Mandatory subscriber: cost.accrued → executions.cost_cents
        self.bus.subscribe(self._cost_subscriber)

    # ---------------------------------------------------------- subscribers

    def _cost_subscriber(self, event: SentinelEvent) -> None:
        if event.type == "cost.accrued":
            cents = getattr(event, "cents", 0)
            if cents:
                try:
                    self.repo.add_cost(event.execution_id, cents)
                except Exception:
                    logger.exception(
                        "failed to increment cost for execution %s", event.execution_id
                    )

    # ------------------------------------------------------------ lifecycle

    def begin(
        self,
        *,
        ticket_id: str,
        project: str,
        kind: ExecutionKind,
        options: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        idempotency_token_prefix: Optional[str] = None,
    ) -> Execution:
        """Insert the execution row and publish ``ExecutionStarted``."""
        execution = self.repo.create(
            ticket_id=ticket_id,
            project=project,
            kind=kind,
            options=options,
            idempotency_key=idempotency_key,
            idempotency_token_prefix=idempotency_token_prefix,
        )
        self.bus.publish(
            ExecutionStarted(
                execution_id=execution.id,
                kind=kind.value,
                ticket_id=ticket_id,
                project=project,
            )
        )
        return execution

    def set_phase(self, execution_id: str, phase: str) -> None:
        # Cooperative cancel check at phase boundaries. The terminal
        # ``execution.cancelled`` event is owned by the post-mortem path; we
        # raise here so callers can short-circuit without emitting a phase
        # change that the run never actually entered.
        if self.cancel_flag is not None and self.cancel_flag.is_set():
            raise OrchestratorCancelled(
                f"cancel requested before entering phase {phase!r}"
            )
        self.repo.set_phase(execution_id, phase)
        self.bus.publish(PhaseChanged(execution_id=execution_id, phase=phase))

    def record_agent_result(
        self, execution_id: str, agent: str, result: Dict[str, Any]
    ) -> None:
        self.repo.record_agent_result(execution_id, agent, result)

    def complete(self, execution: Execution) -> Execution:
        """Mark the run as ``succeeded``, publish ``ExecutionCompleted``."""
        self.repo.record_ended(execution.id, ExecutionStatus.SUCCEEDED)
        refreshed = self.repo.get(execution.id) or execution
        self.bus.publish(
            ExecutionCompleted(
                execution_id=execution.id,
                status=ExecutionStatus.SUCCEEDED.value,
                cost_cents=refreshed.cost_cents,
            )
        )
        return refreshed

    def fail(self, execution: Execution, error: str) -> Execution:
        self.repo.record_ended(execution.id, ExecutionStatus.FAILED, error=error)
        refreshed = self.repo.get(execution.id) or execution
        self.bus.publish(
            ExecutionFailed(execution_id=execution.id, error=error)
        )
        return refreshed

    def cancelled(self, execution: Execution) -> Execution:
        """Mark the run as ``cancelled`` and publish ``ExecutionCancelled``."""
        self.repo.record_ended(execution.id, ExecutionStatus.CANCELLED)
        refreshed = self.repo.get(execution.id) or execution
        self.bus.publish(ExecutionCancelled(execution_id=execution.id))
        return refreshed

    # --------------------------------------------------------------- context

    @contextmanager
    def run(
        self,
        *,
        ticket_id: str,
        project: str,
        kind: ExecutionKind,
        options: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Execution]:
        """Context manager that pairs :meth:`begin` with :meth:`complete`/:meth:`fail`.

        Usage in a CLI command::

            with orchestrator.run(ticket_id=..., project=..., kind=ExecutionKind.EXECUTE) as execution:
                # drive agents, etc.
                ...
        """
        execution = self.begin(
            ticket_id=ticket_id, project=project, kind=kind, options=options
        )
        try:
            yield execution
        except BaseException as exc:
            try:
                self.fail(execution, error=str(exc) or type(exc).__name__)
            except Exception:
                logger.exception(
                    "orchestrator could not record failure for %s", execution.id
                )
            raise
        else:
            try:
                self.complete(execution)
            except Exception:
                logger.exception(
                    "orchestrator could not record success for %s", execution.id
                )

    # ---------------------------------------------------------- agent wiring

    def agent_kwargs(self, execution: Execution) -> Dict[str, Any]:
        """Return the kwargs an agent needs to emit events for this execution."""
        return {"event_bus": self.bus, "execution_id": execution.id}

    # Convenience — CLI commands that want to hand the orchestrator a single
    # callable rather than compose begin/complete/fail manually.

    def wrap(
        self,
        *,
        ticket_id: str,
        project: str,
        kind: ExecutionKind,
        runner: Callable[[Execution], Any],
        options: Optional[Dict[str, Any]] = None,
    ) -> Execution:
        """Run ``runner(execution)`` inside a managed lifecycle."""
        with self.run(
            ticket_id=ticket_id, project=project, kind=kind, options=options
        ) as execution:
            runner(execution)
        return self.repo.get(execution.id) or execution

    # -------------------------------------------------------- agent bookends

    @contextmanager
    def _agent_run(
        self, execution_id: str, agent: Any
    ) -> Iterator[None]:
        """Bookend an agent's ``run(...)`` call with AgentStarted/Finished events."""
        agent_name = getattr(agent, "agent_name", agent.__class__.__name__)
        session_id = getattr(agent, "session_id", None)
        t0 = time.monotonic()
        self.bus.publish(
            AgentStarted(
                execution_id=execution_id,
                agent=agent_name,
                session_id=session_id,
            )
        )
        status = "failed"
        try:
            yield
            status = "succeeded"
        finally:
            self.bus.publish(
                AgentFinished(
                    execution_id=execution_id,
                    agent=agent_name,
                    session_id=getattr(agent, "session_id", None),
                )
            )
            logger.debug(
                "agent.finished execution=%s agent=%s status=%s elapsed_s=%.3f",
                execution_id, agent_name, status, time.monotonic() - t0,
            )

    # ----------------------------------------------------------------- plan

    def plan(self, execution_id: str, **options: Any) -> PlanResult:
        """Worker-path plan flow — load execution, create worktree, run PlanGeneratorAgent."""
        execution = self.repo.get(execution_id)
        if execution is None:
            raise ValueError(f"execution {execution_id} not found")

        # Lazy imports: agents pull in the Claude Agent SDK and Jira clients.
        from src.agents.plan_generator import PlanGeneratorAgent
        from src.jira_factory import get_jira_client
        from src.worktree_manager import WorktreeManager

        ticket_id = execution.ticket_id
        project = execution.project

        try:
            # Validate the ticket exists before we provision a worktree.
            jira_client = get_jira_client()
            jira_client.get_ticket(ticket_id)

            self.set_phase(execution_id, "worktree")
            worktree_mgr = WorktreeManager()
            worktree_path = worktree_mgr.create_worktree(ticket_id, project)

            self.set_phase(execution_id, "planning")
            agent = PlanGeneratorAgent()
            agent.attach_events(self.bus, execution_id)

            with self._agent_run(execution_id, agent):
                result = agent.run(
                    ticket_id=ticket_id,
                    worktree_path=worktree_path,
                    force=bool(options.get("force", False)),
                    user_prompt=options.get("prompt"),
                )

            self.record_agent_result(execution_id, agent.agent_name, result)
            self.complete(execution)
            return PlanResult(status=ExecutionStatus.SUCCEEDED, details=dict(result or {}))

        except OrchestratorCancelled:
            # Cooperative cancel: post-mortem owns the terminal row transition.
            logger.info("plan cancelled cooperatively for %s", execution_id)
            return PlanResult(status=ExecutionStatus.CANCELLED, details={})
        except Exception as exc:
            logger.exception("plan failed for %s", execution_id)
            self.fail(execution, error=str(exc) or type(exc).__name__)
            raise

    # -------------------------------------------------------------- debrief

    def debrief(self, execution_id: str, **options: Any) -> DebriefResult:
        """Worker-path debrief flow — run FunctionalDebriefAgent and emit DebriefTurn."""
        execution = self.repo.get(execution_id)
        if execution is None:
            raise ValueError(f"execution {execution_id} not found")

        from src.agents.functional_debrief import FunctionalDebriefAgent
        from src.jira_factory import get_jira_client
        from src.worktree_manager import WorktreeManager

        ticket_id = execution.ticket_id
        project = execution.project

        try:
            jira_client = get_jira_client()
            jira_client.get_ticket(ticket_id)

            worktree_path: Optional[Path] = None
            try:
                self.set_phase(execution_id, "worktree")
                worktree_mgr = WorktreeManager()
                worktree_path = worktree_mgr.create_worktree(ticket_id, project)
            except OrchestratorCancelled:
                raise
            except Exception as exc:
                logger.warning(
                    "debrief: worktree unavailable for %s (%s); continuing text-only",
                    execution_id, exc,
                )

            self.set_phase(execution_id, "debriefing")
            agent = FunctionalDebriefAgent()
            agent.attach_events(self.bus, execution_id)

            user_prompt = options.get("prompt")

            with self._agent_run(execution_id, agent):
                result = agent.run(
                    ticket_id=ticket_id,
                    project=project,
                    worktree_path=worktree_path,
                    user_prompt=user_prompt,
                )

            # Emit a single DebriefTurn for this invocation. prompt_chars is the
            # operator instruction length (0 if absent); response_chars best-effort
            # samples something descriptive from the agent's result.
            prompt_chars = len(user_prompt) if isinstance(user_prompt, str) else 0
            response_text = ""
            if isinstance(result, dict):
                debrief_data = result.get("debrief_data")
                if isinstance(debrief_data, dict):
                    response_text = str(debrief_data)
                else:
                    response_text = str(result.get("action", ""))
            iteration_index = 1
            if isinstance(result, dict):
                try:
                    iteration_index = int(result.get("iteration_count", 1))
                except (TypeError, ValueError):
                    iteration_index = 1
            self.bus.publish(
                DebriefTurn(
                    execution_id=execution_id,
                    agent=agent.agent_name,
                    turn_index=iteration_index,
                    prompt_chars=prompt_chars,
                    response_chars=len(response_text),
                )
            )

            # A debrief may signal an explicit revision request. The agent does
            # not currently set ``revise`` — this is the hook for when it does.
            if isinstance(result, dict) and result.get("revise") is True:
                self.bus.publish(
                    RevisionRequested(
                        execution_id=execution_id,
                        agent=agent.agent_name,
                        revise_of_execution_id=execution_id,
                        reason=result.get("reason"),
                    )
                )

            self.record_agent_result(execution_id, agent.agent_name, result)
            self.complete(execution)
            return DebriefResult(
                status=ExecutionStatus.SUCCEEDED, details=dict(result or {})
            )

        except OrchestratorCancelled:
            logger.info("debrief cancelled cooperatively for %s", execution_id)
            return DebriefResult(status=ExecutionStatus.CANCELLED, details={})
        except Exception as exc:
            logger.exception("debrief failed for %s", execution_id)
            self.fail(execution, error=str(exc) or type(exc).__name__)
            raise

    # --------------------------------------------------------------- execute

    def execute(self, execution_id: str, **options: Any) -> ExecuteResult:
        """Worker-path execute flow.

        Minimal HTTP-path-complete implementation: register the compose project
        BEFORE any ``compose up``, run a single developer iteration, then a
        security review, emit TestResultRecorded + FindingPosted events, and
        return. Full parity with the CLI's inline execute body is blocked on
        the CLI-thinning pass.
        """
        execution = self.repo.get(execution_id)
        if execution is None:
            raise ValueError(f"execution {execution_id} not found")

        from src.agents.drupal_developer import DrupalDeveloperAgent
        from src.agents.drupal_reviewer import DrupalReviewerAgent
        from src.agents.python_developer import PythonDeveloperAgent
        from src.agents.security_reviewer import SecurityReviewerAgent
        from src.config_loader import get_config
        from src.environment_manager import EnvironmentManager
        from src.worktree_manager import WorktreeManager

        ticket_id = execution.ticket_id
        project = execution.project
        no_env = bool(options.get("no_env", False))

        env_mgr: Optional[EnvironmentManager] = None
        env_info = None

        try:
            self.set_phase(execution_id, "worktree")
            worktree_mgr = WorktreeManager()
            worktree_path = worktree_mgr.get_worktree_path(ticket_id, project)
            if worktree_path is None:
                raise RuntimeError(
                    f"worktree not found for {ticket_id}; run 'sentinel plan' first"
                )

            # Register compose project BEFORE environment setup so reconciliation
            # can clean up even if ``env_mgr.setup`` is killed mid-run.
            compose_project_name = ticket_id.split("-")[0].lower()
            try:
                self.repo.register_compose_project(execution_id, compose_project_name)
            except Exception:
                logger.exception(
                    "register_compose_project failed for %s (continuing)", execution_id
                )

            if not no_env:
                self.set_phase(execution_id, "setup_compose")
                env_mgr = EnvironmentManager()
                try:
                    env_info = env_mgr.setup(worktree_path, ticket_id)
                except Exception as exc:
                    logger.warning(
                        "environment setup failed for %s: %s", execution_id, exc
                    )
                    env_info = None

            config = get_config()
            project_config = config.get_project_config(project)
            stack_type = project_config.get("stack_type", "") if project_config else ""

            developer: Any
            if stack_type and stack_type.startswith("drupal"):
                developer = DrupalDeveloperAgent()
            else:
                developer = PythonDeveloperAgent()
            developer.attach_events(self.bus, execution_id)
            if env_mgr is not None and env_info is not None and getattr(env_info, "active", False):
                developer.set_environment(env_mgr, ticket_id)

            security = SecurityReviewerAgent()
            security.attach_events(self.bus, execution_id)

            plan_file = worktree_path / ".agents" / "plans" / f"{ticket_id}.md"
            user_prompt = options.get("prompt")
            max_iterations = int(options.get("max_iterations", 5) or 5)

            dev_result: Dict[str, Any] = {}
            sec_result: Dict[str, Any] = {}

            # TODO(cli-thinning): Multi-iteration dev+review+drupal loop, push,
            # MR posting, and Jira notification still live in ``src.cli.execute``
            # (lines ~745-1339). Fold them in here during the CLI-thinning pass.
            for iteration in range(1, max_iterations + 1):
                self.set_phase(execution_id, f"iteration_{iteration}")

                with self._agent_run(execution_id, developer):
                    dev_result = developer.run(
                        plan_file=plan_file,
                        worktree_path=worktree_path,
                        user_prompt=user_prompt,
                    )
                self.record_agent_result(execution_id, developer.agent_name, dev_result)

                # Emit TestResultRecorded from dev_result.test_results.
                test_results = dev_result.get("test_results") or {}
                if test_results:
                    self.bus.publish(
                        TestResultRecorded(
                            execution_id=execution_id,
                            agent=developer.agent_name,
                            success=bool(test_results.get("success", False)),
                            return_code=int(test_results.get("return_code", 0) or 0),
                        )
                    )

                with self._agent_run(execution_id, security):
                    sec_result = security.run(
                        worktree_path=worktree_path, ticket_id=ticket_id
                    )
                self.record_agent_result(execution_id, security.agent_name, sec_result)

                if sec_result.get("approved"):
                    # Optional Drupal quality gate for Drupal stacks.
                    if stack_type and stack_type.startswith("drupal"):
                        drupal_reviewer = DrupalReviewerAgent()
                        drupal_reviewer.attach_events(self.bus, execution_id)
                        with self._agent_run(execution_id, drupal_reviewer):
                            drupal_result = drupal_reviewer.run(
                                worktree_path=worktree_path, ticket_id=ticket_id,
                            )
                        self.record_agent_result(
                            execution_id, drupal_reviewer.agent_name, drupal_result
                        )
                        for finding in drupal_result.get("findings", []) or []:
                            self.bus.publish(
                                FindingPosted(
                                    execution_id=execution_id,
                                    agent=drupal_reviewer.agent_name,
                                    severity=str(finding.get("severity", "unknown")),
                                    summary=str(
                                        finding.get("title")
                                        or finding.get("description", "")
                                    )[:500],
                                )
                            )
                    break
                else:
                    for finding in sec_result.get("findings", []) or []:
                        self.bus.publish(
                            FindingPosted(
                                execution_id=execution_id,
                                agent=security.agent_name,
                                severity=str(finding.get("severity", "unknown")),
                                summary=str(
                                    finding.get("description")
                                    or finding.get("title", "")
                                )[:500],
                            )
                        )
                    if iteration >= max_iterations:
                        raise RuntimeError(
                            f"security review did not approve after {max_iterations} iteration(s)"
                        )

            self.complete(execution)
            return ExecuteResult(
                status=ExecutionStatus.SUCCEEDED,
                details={"dev_result": dev_result, "sec_result": sec_result},
            )

        except OrchestratorCancelled:
            logger.info("execute cancelled cooperatively for %s", execution_id)
            return ExecuteResult(status=ExecutionStatus.CANCELLED, details={})
        except Exception as exc:
            logger.exception("execute failed for %s", execution_id)
            self.fail(execution, error=str(exc) or type(exc).__name__)
            raise
        finally:
            if env_mgr is not None and env_info is not None and getattr(env_info, "active", False):
                try:
                    env_mgr.teardown(ticket_id)
                except Exception:
                    logger.exception(
                        "execute: env teardown failed for %s", execution_id
                    )
