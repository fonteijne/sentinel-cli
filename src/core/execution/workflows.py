"""Shared workflow application layer.

This module is the single implementation of Sentinel's plan / execute /
debrief workflows. Both the CLI (``src.cli``) and the Command Center worker
(``src.core.execution.worker``) call into the *same* ``run_*`` functions —
they only differ in how they parse input and how they present output.

Side effects (git push, GitLab MR comments, Jira notifications) live in
:mod:`src.core.execution.post_execute` and are invoked from here so the CLI
and the worker share one implementation. The CLI keeps its own
``click.echo`` presentation by subscribing to the event bus + reading the
``WorkflowResult`` markers.
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

    A run that finishes with an empty artifacts list (no agent invocation,
    no worktree, no MR, no Jira comment) is a no-op — and
    :meth:`assert_real_work` raises ``NoOpExecutionError`` so the worker
    marks the run failed instead of succeeded.
    """

    artifacts: List[str] = field(default_factory=list)
    agent_results: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def add_artifact(self, marker: str) -> None:
        if marker and marker not in self.artifacts:
            self.artifacts.append(marker)

    def merge_artifacts(self, markers: List[str]) -> None:
        for m in markers:
            self.add_artifact(m)

    def assert_real_work(self) -> None:
        if not self.artifacts:
            raise NoOpExecutionError(
                "workflow completed without producing any real artifact "
                "(no agent invocation, no worktree, no external action)"
            )


class NoOpExecutionError(RuntimeError):
    """Raised when a workflow finished without doing real work."""


# --------------------------------------------------------------------------- #
# Worker dispatch entry point
# --------------------------------------------------------------------------- #


def run_workflow_for_execution(
    orchestrator: "Orchestrator",
    execution: Execution,
    *,
    cancel_flag: Any = None,
) -> WorkflowResult:
    """Top-level dispatcher used by the worker.

    The CLI passes through the same dispatcher when it goes via
    ``Orchestrator.execute`` etc. This keeps the worker and CLI on the same
    code path — the only difference is the presentation layer.
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
        if options.revise:
            return run_revise(
                orchestrator,
                ticket_id=execution.ticket_id,
                project=execution.project,
                options=options,
                execution_id=execution.id,
                cancel_flag=cancel_flag,
            )
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
    worktree_factory: Optional[Callable[..., Any]] = None,
    jira_factory: Optional[Callable[[], Any]] = None,
    plan_agent_factory: Optional[Callable[[], Any]] = None,
) -> WorkflowResult:
    """Execute the ``plan`` workflow against an existing execution row."""
    result = WorkflowResult()
    bus: EventBus = orchestrator.bus

    if execution_id is None:
        raise ValueError("run_plan requires execution_id")

    if cancel_flag is not None and cancel_flag.is_set():
        raise WorkflowCancelled("plan workflow cancelled before start")

    from src.jira_factory import get_jira_client
    from src.worktree_manager import WorktreeManager
    from src.agents.plan_generator import PlanGeneratorAgent

    worktree_mgr = (worktree_factory or WorktreeManager)()
    jira_client = (jira_factory or get_jira_client)()

    ticket_data = jira_client.get_ticket(ticket_id)
    result.add_artifact("jira.ticket_fetched")
    result.extra["ticket_summary"] = ticket_data.get("summary")

    orchestrator.set_phase(execution_id, "worktree")
    worktree_path = worktree_mgr.create_worktree(ticket_id, project)
    result.add_artifact("git.worktree_created")
    result.extra["worktree_path"] = str(worktree_path)

    if cancel_flag is not None and cancel_flag.is_set():
        raise WorkflowCancelled("plan workflow cancelled after worktree")

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
    drupal_reviewer_factory: Optional[Callable[..., Any]] = None,
    post_execute_runner: Optional[Callable[..., Any]] = None,
    ticket_context_fetcher: Optional[Callable[[str], str]] = None,
) -> WorkflowResult:
    """Execute the ``execute`` workflow.

    Steps:

    1. Resolve worktree (must exist).
    2. Setup container env (unless ``no_env``).
    3. Iterate developer + security reviewer until approved or budget exhausted.
    4. For Drupal stacks, run the Drupal reviewer with its own self-fix loop;
       if its budget runs out the unresolved findings are posted to the MR
       for human review.
    5. Push branch, mark MR ready, post decision log + drupal findings,
       notify Jira — all via the shared ``run_post_execute_side_effects``
       so the CLI and Command Center cannot drift.
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
    from src.agents.drupal_reviewer import DrupalReviewerAgent
    from src.core.execution.post_execute import run_post_execute_side_effects

    worktree_mgr = (worktree_factory or WorktreeManager)()

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

    env_mgr = (env_manager_factory or EnvironmentManager)()
    env_info = None
    if not options.no_env:
        try:
            env_info = env_mgr.setup(worktree_path, ticket_id)
            if env_info and env_info.active:
                result.add_artifact("env.container_active")
        except RuntimeError as exc:
            raise WorkflowError(
                f"container setup failed: {exc} — fix the issue or pass "
                f"no_env=true to skip"
            ) from exc

    iteration = 0
    dev_result: Dict[str, Any] = {}
    sec_result: Dict[str, Any] = {}
    drupal_findings_to_post: Optional[Dict[str, Any]] = None
    stack_type = ""

    try:
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

        approved = False
        for iteration in range(1, options.max_iterations + 1):
            if cancel_flag is not None and cancel_flag.is_set():
                raise WorkflowCancelled(
                    f"execute workflow cancelled at iteration {iteration}"
                )
            orchestrator.set_phase(execution_id, f"iteration_{iteration}")

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

            # Config validation gate (e.g. Drupal config sync)
            config_result = dev_result.get("config_validation") or {}
            if config_result.get("output") and not config_result.get(
                "success", True
            ):
                if iteration < options.max_iterations:
                    # Developer will re-attempt next iteration.
                    continue
                raise WorkflowError(
                    "config validation failed after max iterations; "
                    "config dependencies are broken — manual fix required"
                )

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
                approved = True
                break

            if iteration >= options.max_iterations:
                raise WorkflowError(
                    f"max iterations ({options.max_iterations}) reached "
                    f"without security approval"
                )
            # else: developer will address feedback on next iteration.

        if not approved:
            raise WorkflowError(
                "execute workflow ended without security approval"
            )

        # Drupal reviewer + self-fix loop (only for drupal stacks).
        if stack_type and stack_type.startswith("drupal"):
            ticket_description = (
                ticket_context_fetcher(ticket_id)
                if ticket_context_fetcher is not None
                else _safe_fetch_ticket_description(ticket_id)
            )
            drupal_attempts = options.max_iterations
            for drupal_attempt in range(1, drupal_attempts + 1):
                if cancel_flag is not None and cancel_flag.is_set():
                    raise WorkflowCancelled(
                        "execute workflow cancelled during drupal review"
                    )
                orchestrator.set_phase(
                    execution_id, f"drupal_review_{drupal_attempt}"
                )
                drupal_reviewer = (
                    drupal_reviewer_factory or DrupalReviewerAgent
                )()
                drupal_reviewer.attach_events(bus, execution_id)
                drupal_result = drupal_reviewer.run(
                    worktree_path=worktree_path,
                    ticket_id=ticket_id,
                    ticket_description=ticket_description,
                )
                orchestrator.record_agent_result(
                    execution_id, drupal_reviewer.agent_name, drupal_result
                )
                result.agent_results[
                    f"{drupal_reviewer.agent_name}_attempt_{drupal_attempt}"
                ] = drupal_result
                result.add_artifact(f"agent.{drupal_reviewer.agent_name}")

                if drupal_result.get("approved"):
                    break

                if drupal_attempt >= drupal_attempts:
                    drupal_findings_to_post = drupal_result
                    result.add_artifact("drupal.findings_unresolved")
                    break

                # Self-fix: ask developer to address findings.
                fix_prompt = (
                    "Fix the following Drupal review findings:\n"
                    + "\n".join(drupal_result.get("feedback") or [])
                )
                orchestrator.set_phase(
                    execution_id, f"drupal_fix_{drupal_attempt}"
                )
                fix_result = developer.run_revision(
                    ticket_id=ticket_id,
                    worktree_path=worktree_path,
                    user_prompt=fix_prompt,
                )
                orchestrator.record_agent_result(
                    execution_id, developer.agent_name, fix_result
                )
                result.agent_results[
                    f"{developer.agent_name}_drupal_fix_{drupal_attempt}"
                ] = fix_result
                result.add_artifact("drupal.self_fix_attempted")

        # Post-execute side effects (push, MR ready, comments, jira notify).
        runner = post_execute_runner or run_post_execute_side_effects
        post = runner(
            ticket_id=ticket_id,
            project=project,
            worktree_path=Path(worktree_path),
            iteration=iteration,
            dev_result=dev_result,
            sec_result=sec_result,
            drupal_findings=drupal_findings_to_post,
            drupal_attempts=options.max_iterations
            if drupal_findings_to_post is not None
            else None,
            force_push=options.force,
        )
        result.merge_artifacts(post.artifacts)
        result.extra["iterations"] = iteration
        result.extra["dev_result"] = dev_result
        result.extra["sec_result"] = sec_result
        result.extra["mr_url"] = post.mr_web_url
        result.extra["pushed"] = post.pushed
        result.extra["push_error"] = post.push_error
        result.extra["mr_marked_ready"] = post.mr_marked_ready
        result.extra["decision_log_posted"] = post.decision_log_posted
        result.extra["drupal_findings_posted"] = post.drupal_findings_posted
        result.extra["jira_notified"] = post.jira_notified
        if drupal_findings_to_post is not None:
            result.extra["drupal_findings"] = drupal_findings_to_post

        # If push failed we surface that as a workflow failure so the run is
        # marked failed rather than green-with-a-warning.
        if not post.pushed:
            raise WorkflowError(
                f"git push failed — the work is committed locally but not on "
                f"origin: {post.push_error or 'unknown error'}"
            )
    finally:
        if env_info is not None and env_info.active:
            try:
                env_mgr.teardown(ticket_id)
            except Exception:
                logger.exception("env teardown failed for %s", ticket_id)

    return result


# --------------------------------------------------------------------------- #
# Revise workflow (CLI --revise)
# --------------------------------------------------------------------------- #


def run_revise(
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
    drupal_reviewer_factory: Optional[Callable[..., Any]] = None,
    post_execute_runner: Optional[Callable[..., Any]] = None,
    ticket_context_fetcher: Optional[Callable[[str], str]] = None,
) -> WorkflowResult:
    """Run the revise flow shared between CLI ``--revise`` and Command Center."""
    result = WorkflowResult()
    bus: EventBus = orchestrator.bus
    if execution_id is None:
        raise ValueError("run_revise requires execution_id")
    if cancel_flag is not None and cancel_flag.is_set():
        raise WorkflowCancelled("revise workflow cancelled before start")

    from src.config_loader import get_config
    from src.environment_manager import EnvironmentManager
    from src.worktree_manager import WorktreeManager
    from src.agents.python_developer import PythonDeveloperAgent
    from src.agents.drupal_developer import DrupalDeveloperAgent
    from src.agents.drupal_reviewer import DrupalReviewerAgent
    from src.core.execution.post_execute import run_post_execute_side_effects

    worktree_mgr = (worktree_factory or WorktreeManager)()
    worktree_path = worktree_mgr.get_worktree_path(ticket_id, project)
    if worktree_path is None:
        raise WorkflowError(
            f"worktree not found for {ticket_id} — run 'sentinel plan' first"
        )
    result.add_artifact("git.worktree_resolved")

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

    env_mgr = (env_manager_factory or EnvironmentManager)()
    env_info = None
    if not options.no_env:
        try:
            env_info = env_mgr.setup(worktree_path, ticket_id)
            if env_info and env_info.active:
                developer.set_environment(env_mgr, ticket_id)
                result.add_artifact("env.container_active")
        except RuntimeError as exc:
            logger.warning(
                "revise: container setup failed (%s); tests will run on host",
                exc,
            )

    drupal_findings_to_post: Optional[Dict[str, Any]] = None
    try:
        orchestrator.set_phase(execution_id, "implementing_revision")
        revision_result = developer.run_revision(
            ticket_id=ticket_id,
            worktree_path=worktree_path,
            user_prompt=options.prompt,
        )
        orchestrator.record_agent_result(
            execution_id, developer.agent_name, revision_result
        )
        result.agent_results[developer.agent_name] = revision_result
        result.add_artifact(f"agent.{developer.agent_name}")

        if revision_result.get("feedback_count", 0) == 0:
            result.extra["nothing_to_revise"] = True
            result.add_artifact("revise.no_feedback")
            return result

        config_result = revision_result.get("config_validation", {}) or {}
        if config_result.get("output") and not config_result.get(
            "success", True
        ):
            raise WorkflowError(
                "config validation failed after revision; not pushing"
            )

        # Drupal reviewer + self-fix on revise.
        if stack_type and stack_type.startswith("drupal"):
            ticket_description = (
                ticket_context_fetcher(ticket_id)
                if ticket_context_fetcher is not None
                else _safe_fetch_ticket_description(ticket_id)
            )
            for drupal_attempt in range(1, options.max_iterations + 1):
                if cancel_flag is not None and cancel_flag.is_set():
                    raise WorkflowCancelled(
                        "revise workflow cancelled during drupal review"
                    )
                orchestrator.set_phase(
                    execution_id, f"drupal_review_{drupal_attempt}"
                )
                drupal_reviewer = (
                    drupal_reviewer_factory or DrupalReviewerAgent
                )()
                drupal_reviewer.attach_events(bus, execution_id)
                drupal_result = drupal_reviewer.run(
                    worktree_path=worktree_path,
                    ticket_id=ticket_id,
                    ticket_description=ticket_description,
                )
                orchestrator.record_agent_result(
                    execution_id, drupal_reviewer.agent_name, drupal_result
                )
                result.agent_results[
                    f"{drupal_reviewer.agent_name}_attempt_{drupal_attempt}"
                ] = drupal_result
                result.add_artifact(f"agent.{drupal_reviewer.agent_name}")

                if drupal_result.get("approved"):
                    break

                if drupal_attempt >= options.max_iterations:
                    drupal_findings_to_post = drupal_result
                    result.add_artifact("drupal.findings_unresolved")
                    break

                fix_prompt = (
                    "Fix the following Drupal review findings:\n"
                    + "\n".join(drupal_result.get("feedback") or [])
                )
                orchestrator.set_phase(
                    execution_id, f"drupal_fix_{drupal_attempt}"
                )
                developer.run_revision(
                    ticket_id=ticket_id,
                    worktree_path=worktree_path,
                    user_prompt=fix_prompt,
                )

        # Post side effects — push + revision log comment.
        runner = post_execute_runner or run_post_execute_side_effects
        post = runner(
            ticket_id=ticket_id,
            project=project,
            worktree_path=Path(worktree_path),
            iteration=1,
            dev_result=revision_result,
            sec_result={},
            drupal_findings=drupal_findings_to_post,
            drupal_attempts=options.max_iterations
            if drupal_findings_to_post is not None
            else None,
            force_push=options.force,
            revision_result=revision_result,
        )
        result.merge_artifacts(post.artifacts)
        result.extra["mr_url"] = (
            post.mr_web_url or revision_result.get("mr_url")
        )
        result.extra["pushed"] = post.pushed
        result.extra["push_error"] = post.push_error
        result.extra["decision_log_posted"] = post.decision_log_posted
        result.extra["drupal_findings_posted"] = post.drupal_findings_posted

        if not post.pushed:
            raise WorkflowError(
                f"git push failed during revise — work is local only: "
                f"{post.push_error or 'unknown error'}"
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
    jira_factory: Optional[Callable[[], Any]] = None,
) -> WorkflowResult:
    """Execute the ``debrief`` workflow."""
    result = WorkflowResult()
    bus: EventBus = orchestrator.bus
    if execution_id is None:
        raise ValueError("run_debrief requires execution_id")

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
        logger.warning(
            "debrief: worktree unavailable, continuing text-only: %s", exc
        )
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

    # Follow-up ticket creation. The debrief agent surfaces follow-up needs
    # via ``agent_result["follow_up"]`` (description text) and/or the
    # caller can specify ``options.follow_up_ticket`` to link to an existing
    # ticket. We support both.
    follow_up_payload = agent_result.get("follow_up") or {}
    if options.follow_up_ticket or follow_up_payload.get("description"):
        try:
            jira = (jira_factory or _default_jira_factory)()
            link_to_existing = options.follow_up_ticket
            if link_to_existing:
                # Link the parent ticket to the supplied follow-up via a comment.
                jira.add_comment(
                    ticket_id,
                    (
                        f"Sentinel debrief: linking follow-up ticket "
                        f"{link_to_existing}."
                    ),
                )
                result.extra["follow_up_linked_ticket"] = link_to_existing
                result.add_artifact("debrief.follow_up_linked")
            else:
                # Create a new follow-up ticket using the agent's payload.
                summary = follow_up_payload.get(
                    "summary", f"Follow-up from debrief on {ticket_id}"
                )
                description = follow_up_payload.get("description", "")
                created = jira.create_ticket(
                    project_key=project,
                    summary=summary,
                    description=description,
                    issue_type=follow_up_payload.get("issue_type", "Task"),
                    priority=follow_up_payload.get("priority", "Medium"),
                )
                created_key = created.get("key") or created.get("id") or ""
                result.extra["follow_up_created_ticket"] = created_key
                result.add_artifact("debrief.follow_up_created")
                # Link by commenting on the original ticket.
                if created_key:
                    try:
                        jira.add_comment(
                            ticket_id,
                            (
                                f"Sentinel debrief: created follow-up ticket "
                                f"{created_key}."
                            ),
                        )
                    except Exception as exc:
                        logger.warning(
                            "debrief: failed to comment with follow-up link: %s",
                            exc,
                        )
        except Exception as exc:
            logger.warning("debrief: follow-up handling failed: %s", exc)
            result.extra["follow_up_error"] = str(exc)
            # Don't fail the whole workflow — debrief content is the primary
            # deliverable. We *do* record a marker so the run isn't classified
            # as a no-op even when worktree was skipped.
            result.add_artifact("debrief.follow_up_failed")

    return result


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _default_jira_factory() -> Any:
    from src.jira_factory import get_jira_client

    return get_jira_client()


def _safe_fetch_ticket_description(ticket_id: str) -> str:
    """Return ticket summary+description+comments; empty string on failure."""
    try:
        from src.jira_factory import get_jira_client
        from src.ticket_context import TicketContextBuilder

        jira_client = get_jira_client()
        return TicketContextBuilder(jira_client, ticket_id).format_ticket_context()
    except Exception as exc:
        logger.warning(
            "failed to fetch ticket context for reviewer: %s", exc
        )
        return ""


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class WorkflowError(RuntimeError):
    """Recoverable workflow failure — translated to ``execution.failed``."""


class WorkflowCancelled(RuntimeError):
    """Cancellation honoured between steps."""
