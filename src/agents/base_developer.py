"""Base Developer Agent — shared orchestration for stack-specific developer agents."""

import asyncio
import fnmatch
import logging
import os
import subprocess
from abc import abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from src.core.events import EventBus
    from src.environment_manager import EnvironmentManager

from src.agents._structured_errors import (
    StructuredError,
    parse_drush_config_validation,
)
from src.agents.base_agent import ImplementationAgent
from src.core.events import (
    BaseEvent,
    DeveloperCappedOut,
    StaticCheckRecorded,
    TestResultRecorded,
)
from src.prompt_loader import load_agent_prompt
from src.worktree_manager import get_branch_name


logger = logging.getLogger(__name__)


# D1: single global cap for the verifier-retry loop. Per-stack overrides are
# explicitly out of scope for Phase 1 — revisit only with telemetry from
# ≥20% of executions capping out AND postmortems showing a 4th attempt
# would have passed on a meaningful fraction.
MAX_ATTEMPTS: int = 3


def _verifier_loop_enabled() -> bool:
    """Phase 1 feature flag — set DEV_VERIFIER_LOOP=1 to enable Loop A."""
    return os.getenv("DEV_VERIFIER_LOOP", "0") == "1"


class DeveloperCappedOutException(Exception):
    """Raised when the verifier-retry loop hits MAX_ATTEMPTS without converging.

    Carries the last batch of structured errors so callers (e.g. the iteration
    loop) can accumulate them into a ``RegressionContext`` for the next
    iteration's developer prompts.
    """

    def __init__(
        self,
        message: str,
        structured_errors: Optional[List[StructuredError]] = None,
    ) -> None:
        super().__init__(message)
        self.structured_errors: List[StructuredError] = list(structured_errors or [])


class DeveloperTaskFailedException(Exception):
    """Raised by the single-shot path when tests fail post-implementation.

    Carries the structured errors parsed from the failing test run so the
    caller can fold them into the cross-iteration regression context.
    """

    def __init__(
        self,
        message: str,
        structured_errors: Optional[List[StructuredError]] = None,
    ) -> None:
        super().__init__(message)
        self.structured_errors: List[StructuredError] = list(structured_errors or [])


@dataclass
class RegressionContext:
    """Test failures that survived the prior iteration of an execution.

    Injected into every task prompt in the next iteration as additional
    acceptance criteria. Ephemeral — never persisted, never crosses
    execution boundaries.
    """

    iteration_n: int
    errors: List[StructuredError] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.errors


def _dedupe_structured_errors(
    errors: List[StructuredError],
) -> List[StructuredError]:
    """Collapse duplicate structured errors by (file, line, rule, message).

    The same test failing in three task runs within an iteration shouldn't
    appear three times in the next iteration's prompt. Order of first
    appearance is preserved.
    """
    seen: set[tuple[str, int, str, str]] = set()
    out: List[StructuredError] = []
    for err in errors:
        key = (
            str(err.get("file") or ""),
            int(err.get("line") or 0),
            str(err.get("rule") or ""),
            str(err.get("message") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(err)
    return out


def _render_regression_section(ctx: Optional[RegressionContext]) -> str:
    """Render a regression context as a markdown block ready to prepend to
    a developer task prompt. ``None`` or empty context returns ``''``.
    """
    if ctx is None or ctx.is_empty():
        return ""
    lines = [
        "## Prior Iteration Regressions",
        "",
        (
            f"The previous iteration ({ctx.iteration_n}) left "
            f"{len(ctx.errors)} test(s) failing. Treat fixing them as "
            f"additional acceptance criteria for your task — your work "
            f"isn't done until your task passes **and** these are green:"
        ),
        "",
    ]
    for err in ctx.errors:
        file_part = err.get("file") or "<unknown>"
        line_part = err.get("line") or 0
        rule_part = err.get("rule") or "unknown"
        msg_part = (err.get("message") or "").strip()
        lines.append(
            f"- `{file_part}:{line_part}` [{rule_part}] {msg_part}"
        )
    return "\n".join(lines) + "\n"


class BaseDeveloperAgent(ImplementationAgent):
    """Base class for stack-specific developer agents.

    Provides shared orchestration (plan parsing, TDD loop, commit, MR revision).
    Subclasses implement stack-specific methods:
      - _build_tdd_prompt()  — TDD prompt for the target stack
      - _get_test_command()  — test runner CLI command
      - _get_test_stub()     — minimal test file content
    """

    def __init__(
        self,
        agent_name: str,
        model: str = "claude-4-5-sonnet",
        temperature: float = 0.2,
    ) -> None:
        super().__init__(agent_name=agent_name, model=model, temperature=temperature)

        # Ensure the stack-agnostic developer prompt is loaded as base.
        # agent_name may be "python_developer" or "drupal_developer" which don't
        # have their own prompt files — fall back to prompts/developer.md.
        if not self.system_prompt:
            try:
                self.system_prompt = load_agent_prompt("developer")
                logger.info(f"Loaded fallback developer prompt ({len(self.system_prompt)} chars)")
            except FileNotFoundError:
                logger.warning("Developer system prompt not found at prompts/developer.md")

        # Container environment for test execution (set via set_environment)
        self._env_manager: Optional["EnvironmentManager"] = None
        self._env_ticket_id: Optional[str] = None

        # Event bus for Loop A telemetry (set via set_event_bus). When unset,
        # _emit() is a no-op so unit tests don't need persistence infra.
        self._event_bus: Optional["EventBus"] = None
        self._execution_id: Optional[str] = None

    def set_event_bus(self, bus: "EventBus", execution_id: str) -> None:
        """Attach an event bus + execution scope for Loop A telemetry.

        When a bus is attached, ``_emit()`` will publish events; otherwise
        emits are no-ops. Called by the CLI at execute-time.

        Args:
            bus: EventBus instance bound to a SQLite connection
            execution_id: Unique id for this run (FK to executions table)
        """
        self._event_bus = bus
        self._execution_id = execution_id

    def _emit(self, event: BaseEvent) -> None:
        """Publish an event if a bus is attached, no-op otherwise.

        This makes the loop testable without spinning up persistence: unit
        tests that don't call ``set_event_bus`` get a silent emitter.
        """
        if self._event_bus is None:
            return
        try:
            self._event_bus.publish(event)
        except Exception as e:  # pragma: no cover - defensive
            # Bus is supposed to swallow subscriber exceptions itself; this
            # catch is a belt-and-braces guard so a bus bug never breaks
            # the agent loop.
            logger.error("Event publish failed: %s", e, exc_info=True)

    def set_environment(
        self,
        env_manager: "EnvironmentManager",
        ticket_id: str,
    ) -> None:
        """Attach a container environment for test execution.

        When set, run_tests() will execute inside the container
        instead of on the host via subprocess.

        Args:
            env_manager: Active EnvironmentManager instance
            ticket_id: Ticket ID for this environment
        """
        self._env_manager = env_manager
        self._env_ticket_id = ticket_id
        logger.info(f"Container environment attached for {ticket_id}")

    # ------------------------------------------------------------------
    # Abstract methods — subclasses MUST implement
    # ------------------------------------------------------------------

    @abstractmethod
    def _build_tdd_prompt(
        self, task: str, context: Dict[str, Any], worktree_path: Path
    ) -> str:
        """Build the TDD prompt for this stack.

        Args:
            task: Task description
            context: Implementation context
            worktree_path: Path to git worktree

        Returns:
            Full TDD prompt string for the Agent SDK
        """

    @abstractmethod
    def _get_test_command(self, paths: Optional[List[str]] = None) -> List[str]:
        """Return the test runner CLI command for this stack.

        Args:
            paths: Optional list of test files (or test dirs) to scope the
                run to. When ``None`` or empty, subclasses fall back to
                their default broad scope so implementation-only tasks
                still produce a verifier signal.

        Returns:
            Command list, e.g. ["pytest", "-v", "--tb=short"]
        """

    @abstractmethod
    def _get_test_stub(self) -> str:
        """Return minimal test file content for this stack.

        Returns:
            Test stub source code string
        """

    def _parse_test_output(
        self, raw: str, return_code: int
    ) -> List[StructuredError]:
        """Parse test runner output into structured errors.

        Default returns ``[]`` — subclasses override with stack-specific parsers
        (pytest text, PHPUnit JUnit XML, etc.). Defined as a regular method
        rather than ``@abstractmethod`` so existing tests/mocks that
        instantiate ``BaseDeveloperAgent`` directly still work.

        Args:
            raw: combined stdout+stderr from the test run
            return_code: process exit code

        Returns:
            list of StructuredError dicts; empty list when nothing was parsed
        """
        return []

    def run_static_checks(self, worktree_path: Path) -> Dict[str, Any]:
        """Run stack-specific static checks (lint/typecheck/etc.).

        Default returns a passing skip — subclasses override with the real
        verifiers (PHPStan + composer validate for Drupal; ruff + mypy for
        Python).

        Args:
            worktree_path: Path to git worktree

        Returns:
            Dictionary matching ``run_tests`` shape:
              - passed: bool
              - test_results: str (raw combined output)
              - structured_errors: list[StructuredError]
              - return_code: int
        """
        return {
            "passed": True,
            "test_results": "Skipped (no static checks configured)",
            "structured_errors": [],
            "return_code": 0,
        }

    # ------------------------------------------------------------------
    # Config validation
    # ------------------------------------------------------------------

    def validate_config(self, worktree_path: Path) -> Dict[str, Any]:
        """Validate project configuration after implementation.

        Override in stack-specific subclasses to add config validation
        (e.g. Drupal config sync, Django migrations check).

        Default is a no-op that returns success.

        Args:
            worktree_path: Path to git worktree

        Returns:
            Dictionary with success, output, return_code
        """
        return {"success": True, "output": "", "return_code": 0}

    # ------------------------------------------------------------------
    # Output file validation
    # ------------------------------------------------------------------

    #: File extensions that are never valid implementation output.
    _JUNK_EXTENSIONS: frozenset = frozenset({
        ".md", ".txt", ".log", ".csv", ".json.bak",
    })

    #: Allowlist of valid output file extensions for this stack.
    #: Subclasses MUST override this.  When set, only files whose extension
    #: appears in the allowlist (or whose extension is empty, e.g. Makefiles)
    #: are kept.  Files matching _JUNK_EXTENSIONS are always rejected first.
    _VALID_EXTENSIONS: frozenset = frozenset()  # empty = no allowlist filtering

    def _filter_output_files(self, files: List[str]) -> List[str]:
        """Remove junk and off-stack files from LLM output.

        Filtering is two-tiered:
        1. Blocklist — extensions in ``_JUNK_EXTENSIONS`` are always rejected.
        2. Allowlist — if ``_VALID_EXTENSIONS`` is non-empty, only files whose
           extension appears in the set are kept.  This catches cross-stack
           contamination (e.g. ``.py`` files in a Drupal project).

        Args:
            files: List of file paths produced by Write/Edit tool uses

        Returns:
            Filtered list with invalid files removed
        """
        valid = []
        for f in files:
            if not f:
                continue
            ext = Path(f).suffix.lower()
            name = Path(f).name

            # Tier 1: always-reject blocklist
            if ext in self._JUNK_EXTENSIONS:
                logger.warning("Filtering junk output file: %s", f)
                continue

            # Reject ALL_CAPS filenames like TDD_EXECUTION_SUMMARY_FINAL.txt
            if name.replace("_", "").replace("-", "").replace(".", "").isupper() and ext in {".md", ".txt", ""}:
                logger.warning("Filtering documentation-style output file: %s", f)
                continue

            # Tier 2: per-stack allowlist
            if self._VALID_EXTENSIONS and ext and ext not in self._VALID_EXTENSIONS:
                logger.warning(
                    "Filtering off-stack file (ext=%s not in allowlist): %s",
                    ext, f,
                )
                continue

            valid.append(f)

        if len(valid) < len(files):
            logger.info(
                "Filtered %d invalid files from %d total output files",
                len(files) - len(valid), len(files),
            )
        return valid

    # ------------------------------------------------------------------
    # Shared orchestration methods
    # ------------------------------------------------------------------

    def break_down_plan(self, plan_file: Path) -> List[str]:
        """Break down implementation plan into actionable tasks using LLM.

        Args:
            plan_file: Path to the implementation plan

        Returns:
            List of task descriptions extracted from plan
        """
        logger.info(f"Breaking down plan: {plan_file}")

        if not plan_file.exists():
            logger.warning(f"Plan file not found: {plan_file}")
            return []

        plan_content = plan_file.read_text()

        extraction_prompt = f"""Extract actionable implementation tasks from this plan.

PLAN:
{plan_content}

INSTRUCTIONS:
1. Find the "Implementation Steps" or "Step-by-Step Tasks" section
2. Extract each distinct task/step as a concise action item
3. Convert prose steps (### Step 1:) into task descriptions
4. Ignore validation checklists, success criteria, or testing sections
5. Return ONLY the task descriptions, one per line
6. Do not include step numbers or formatting

EXAMPLE INPUT:
### Step 1: Add bcrypt Dependency
- **Action**: UPDATE `requirements.txt`
- **Details**: Add `bcrypt>=4.0.0`

EXAMPLE OUTPUT:
Add bcrypt dependency to requirements.txt

Return the task list now, one task per line:"""

        try:
            response = self.send_message(extraction_prompt)

            tasks = []
            for line in response.strip().split("\n"):
                task = line.strip()
                if task and not task.startswith("#") and not task.startswith("-"):
                    tasks.append(task)

            logger.info(f"LLM extracted {len(tasks)} tasks from plan")

            if len(tasks) == 0:
                logger.warning("LLM extracted 0 tasks, falling back to regex parsing")
                tasks = self._fallback_parse_tasks(plan_content)

            return tasks

        except Exception as e:
            logger.error(f"LLM task extraction failed: {e}, using fallback")
            return self._fallback_parse_tasks(plan_content)

    def _fallback_parse_tasks(self, plan_content: str) -> List[str]:
        """Fallback parser for checklist-format tasks.

        Args:
            plan_content: Plan file content

        Returns:
            List of tasks extracted from checklist items
        """
        tasks = []
        in_implementation_section = False

        for line in plan_content.split("\n"):
            if line.startswith("## ") and any(
                marker in line.lower()
                for marker in ["step-by-step", "implementation steps", "implementation tasks"]
            ):
                in_implementation_section = True
                continue

            if in_implementation_section and line.startswith("## "):
                break

            if in_implementation_section and line.strip().startswith("- [ ]"):
                task = line.strip()[6:].strip()
                if task:
                    tasks.append(task)

        logger.info(f"Fallback parser identified {len(tasks)} tasks")
        return tasks

    def implement_feature(
        self,
        task: str,
        context: Dict[str, Any],
        worktree_path: Path,
        commit_prefix: str = "feat",
        user_prompt: str | None = None,
        regressions: Optional[RegressionContext] = None,
    ) -> Dict[str, Any]:
        """Implement a feature following TDD approach.

        Behavior is governed by the ``DEV_VERIFIER_LOOP`` env var (D1):
          - default (``0``): single-shot — write code, run tests once, raise
            if tests fail. Identical to legacy behavior.
          - ``1``: Loop A — capped (``MAX_ATTEMPTS``) verifier-retry loop;
            on cap-out, emits ``DeveloperCappedOut`` and raises
            ``DeveloperCappedOutException``.

        Args:
            task: Task description
            context: Implementation context
            worktree_path: Path to git worktree
            commit_prefix: Git commit prefix (feat, fix, etc.)
            regressions: Optional structured failures from the prior
                iteration of the enclosing execution. When non-empty, a
                "## Prior Iteration Regressions" section is prepended to
                the task prompt as additional acceptance criteria.

        Returns:
            Dictionary with success, files_created, files_modified,
            test_results, commit_message, agent_response.
        """
        # Snapshot the worktree HEAD *before* the developer agent runs so
        # the post-task verifier can scope phpunit to files this task
        # actually changed. ``None`` preserves legacy broad-scope behavior.
        pretask_sha = self._capture_pretask_sha(worktree_path)

        if not _verifier_loop_enabled():
            return self._implement_feature_single_shot(
                task, context, worktree_path, commit_prefix, user_prompt,
                pretask_sha=pretask_sha,
                regressions=regressions,
            )
        return self._implement_feature_with_loop(
            task, context, worktree_path, commit_prefix, user_prompt,
            pretask_sha=pretask_sha,
            regressions=regressions,
        )

    def _implement_feature_single_shot(
        self,
        task: str,
        context: Dict[str, Any],
        worktree_path: Path,
        commit_prefix: str = "feat",
        user_prompt: str | None = None,
        pretask_sha: Optional[str] = None,
        regressions: Optional[RegressionContext] = None,
    ) -> Dict[str, Any]:
        """Legacy single-shot TDD path (preserved verbatim from pre-Phase-1).

        Used when ``DEV_VERIFIER_LOOP`` is unset or ``0``. No retries, no
        static checks, no event emission — drop-in compatible with previous
        behavior so the flag flip is a true rollback.
        """
        logger.info(f"Implementing task: {task}")

        # Load the TDD command definition
        try:
            cmd_result = self.execute_command(
                "implement-tdd",
                {
                    "feature_description": task,
                    "plan_step": task,
                }
            )

            if not cmd_result.get("success"):
                error_msg = f"TDD command validation failed: {cmd_result.get('errors')}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            workflow = cmd_result.get("workflow", [])
            logger.info(f"Loaded TDD workflow with {len(workflow)} steps")

        except Exception as e:
            logger.error(f"Error loading TDD command: {e}")
            raise

        # Build stack-specific TDD prompt
        tdd_prompt = self._build_tdd_prompt(task, context, worktree_path)
        tdd_prompt = self._prepend_regression_section(tdd_prompt, regressions)
        tdd_prompt = self._append_operator_prompt(tdd_prompt, user_prompt)

        # Execute TDD workflow using Agent SDK
        try:
            result = asyncio.run(self.agent_sdk.execute_with_tools(
                prompt=tdd_prompt,
                session_id=None,
                system_prompt=self.system_prompt,
                cwd=str(worktree_path),
            ))

            files_created = []
            files_modified = []

            for tool_use in result.get("tool_uses", []):
                tool_name = tool_use.get("tool")
                if tool_name == "Write":
                    files_created.append(tool_use.get("input", {}).get("file_path", ""))
                elif tool_name == "Edit":
                    files_modified.append(tool_use.get("input", {}).get("file_path", ""))

            # Filter out junk documentation files the LLM may have created
            files_created = self._filter_output_files(files_created)
            files_modified = self._filter_output_files(files_modified)

            test_results = self.run_tests(worktree_path, pretask_sha=pretask_sha)

            if not test_results.get("passed"):
                logger.warning(
                    "Tests failed after TDD implementation: %s",
                    test_results.get("test_results"),
                )
                raise DeveloperTaskFailedException(
                    (
                        f"TDD cycle completed but tests are failing: "
                        f"{test_results.get('test_results')}"
                    ),
                    structured_errors=list(
                        test_results.get("structured_errors") or []
                    ),
                )

            task_summary = task[:72] if len(task) <= 72 else task[:69] + "..."
            test_output = test_results.get("test_results", "")
            if "skipping" in test_output.lower():
                test_status = "Tests skipped (no test config found)"
            else:
                test_status = "All tests passing"
            commit_message = (
                f"{commit_prefix}: {task_summary}\n\n"
                f"- Implemented using TDD approach\n- {test_status}"
            )

            logger.info(f"Task implementation complete: {task}")

            return {
                "success": True,
                "files_created": [f for f in files_created if f],
                "files_modified": [f for f in files_modified if f],
                "test_results": test_results,
                "commit_message": commit_message,
                "agent_response": result.get("content", ""),
            }

        except Exception as e:
            logger.error(f"Error executing TDD workflow: {e}")
            raise

    def _implement_feature_with_loop(
        self,
        task: str,
        context: Dict[str, Any],
        worktree_path: Path,
        commit_prefix: str = "feat",
        user_prompt: str | None = None,
        pretask_sha: Optional[str] = None,
        regressions: Optional[RegressionContext] = None,
    ) -> Dict[str, Any]:
        """Loop A — capped verifier-retry loop (design §5.1).

        For up to ``MAX_ATTEMPTS`` attempts: run the developer SDK, then
        verify with ``run_tests`` + ``run_static_checks``. If both pass,
        return success. If both fail, build a refine prompt from the
        structured errors and ask the agent for a single targeted fix.

        On cap-out:
          - emit ``DeveloperCappedOut`` (last 10 errors)
          - raise ``DeveloperCappedOutException``

        Guardrail-denied iterations (``execute_with_tools`` returning a
        failed result) count as a failed attempt — the cap is not reset.
        """
        logger.info(f"Implementing task with Loop A: {task}")

        # Load the TDD command definition (same as single-shot)
        try:
            cmd_result = self.execute_command(
                "implement-tdd",
                {
                    "feature_description": task,
                    "plan_step": task,
                },
            )
            if not cmd_result.get("success"):
                error_msg = f"TDD command validation failed: {cmd_result.get('errors')}"
                logger.error(error_msg)
                raise RuntimeError(error_msg)
        except Exception as e:
            logger.error(f"Error loading TDD command: {e}")
            raise

        tdd_prompt = self._build_tdd_prompt(task, context, worktree_path)
        tdd_prompt = self._prepend_regression_section(tdd_prompt, regressions)
        tdd_prompt = self._append_operator_prompt(tdd_prompt, user_prompt)

        files_created: List[str] = []
        files_modified: List[str] = []
        last_errors: List[StructuredError] = []

        for attempt in range(1, MAX_ATTEMPTS + 1):
            logger.info("Loop A attempt %d/%d for task: %s", attempt, MAX_ATTEMPTS, task)

            prompt = (
                tdd_prompt
                if attempt == 1
                else self._build_refine_prompt(last_errors, attempt)
            )

            sdk_result = asyncio.run(self.agent_sdk.execute_with_tools(
                prompt=prompt,
                session_id=None,
                system_prompt=self.system_prompt,
                cwd=str(worktree_path),
            ))

            for tool_use in sdk_result.get("tool_uses", []):
                tool_name = tool_use.get("tool")
                if tool_name == "Write":
                    files_created.append(tool_use.get("input", {}).get("file_path", ""))
                elif tool_name == "Edit":
                    files_modified.append(tool_use.get("input", {}).get("file_path", ""))

            test_result = self.run_tests(worktree_path, pretask_sha=pretask_sha)
            static_result = self.run_static_checks(worktree_path)

            test_errors = test_result.get("structured_errors", []) or []
            static_errors = static_result.get("structured_errors", []) or []

            self._emit(
                TestResultRecorded(
                    execution_id=self._execution_id or "",
                    passed=bool(test_result.get("passed")),
                    attempt=attempt,
                    structured_errors_count=len(test_errors),
                    agent=self.agent_name,
                )
            )
            self._emit(
                StaticCheckRecorded(
                    execution_id=self._execution_id or "",
                    checker="combined",
                    passed=bool(static_result.get("passed")),
                    structured_errors_count=len(static_errors),
                    agent=self.agent_name,
                )
            )

            last_errors = list(test_errors) + list(static_errors)

            if test_result.get("passed") and static_result.get("passed"):
                # Happy path — same payload shape as single-shot.
                files_created = self._filter_output_files(files_created)
                files_modified = self._filter_output_files(files_modified)

                task_summary = task[:72] if len(task) <= 72 else task[:69] + "..."
                test_output = test_result.get("test_results", "") or ""
                if "skipping" in test_output.lower():
                    test_status = "Tests skipped (no test config found)"
                else:
                    test_status = "All tests passing"
                commit_message = (
                    f"{commit_prefix}: {task_summary}\n\n"
                    f"- Implemented using TDD approach (Loop A, attempt {attempt})\n"
                    f"- {test_status}"
                )

                logger.info(
                    "Task converged on attempt %d/%d: %s", attempt, MAX_ATTEMPTS, task
                )

                return {
                    "success": True,
                    "files_created": [f for f in files_created if f],
                    "files_modified": [f for f in files_modified if f],
                    "test_results": test_result,
                    "commit_message": commit_message,
                    "agent_response": sdk_result.get("content", ""),
                    "attempts": attempt,
                }

            logger.warning(
                "Loop A attempt %d/%d failed (%d test err, %d static err) — refining",
                attempt, MAX_ATTEMPTS, len(test_errors), len(static_errors),
            )

        # Capped out — emit and raise. Cap-side-effects (postmortem row,
        # MR draft revert, single MR comment) live in the subscriber per D7+D8.
        capped_payload = [dict(e) for e in last_errors[:10]]
        self._emit(
            DeveloperCappedOut(
                execution_id=self._execution_id or "",
                agent=self.agent_name,
                attempts=MAX_ATTEMPTS,
                last_structured_errors=capped_payload,
            )
        )
        logger.error(
            "Developer agent %s capped out after %d attempts on task: %s",
            self.agent_name, MAX_ATTEMPTS, task,
        )
        raise DeveloperCappedOutException(
            f"Capped at {MAX_ATTEMPTS} attempts for task: {task}",
            structured_errors=list(last_errors),
        )

    def _build_refine_prompt(
        self, errors: List[StructuredError], attempt: int
    ) -> str:
        """Compose the refine prompt fed back to the SDK on retry attempts.

        Design §7.3: "When the verifier fails, respond with a single targeted
        fix; do not rewrite unrelated code." Errors are included verbatim as
        a bulleted list. We deliberately do NOT replay the previous diff
        (the SDK session history already has it) and we do NOT inject
        postmortem-derived rules (Phase 2 concern).
        """
        if errors:
            bullets = []
            for err in errors:
                file_part = err.get("file") or "<unknown>"
                line_part = err.get("line") or 0
                rule_part = err.get("rule") or "unknown"
                msg_part = (err.get("message") or "").strip()
                bullets.append(
                    f"- `{file_part}:{line_part}` [{rule_part}] {msg_part}"
                )
            error_block = "\n".join(bullets)
        else:
            error_block = "- (no structured errors captured)"

        return (
            f"The verifier reported failures on attempt {attempt - 1} of "
            f"{MAX_ATTEMPTS}. This is attempt {attempt} of {MAX_ATTEMPTS}.\n\n"
            f"Structured errors:\n{error_block}\n\n"
            "Respond with a single targeted fix. Do not rewrite unrelated "
            "code, do not refactor, and do not hypothesize beyond what the "
            "errors above show. Use Edit/Write tools to apply the fix."
        )

    def _prepend_regression_section(
        self, prompt: str, regressions: Optional[RegressionContext]
    ) -> str:
        """Prepend the rendered ``## Prior Iteration Regressions`` block to
        a developer task prompt. Empty/None context is a no-op.
        """
        block = _render_regression_section(regressions)
        if not block:
            return prompt
        return f"{block}\n{prompt}"

    def write_tests(self, implementation: str, test_path: Path) -> str:
        """Write tests for an implementation.

        Args:
            implementation: Implementation code
            test_path: Path to write test file

        Returns:
            Test code
        """
        logger.info(f"Writing tests to {test_path}")

        test_code = self._get_test_stub()

        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(test_code)

        logger.info(f"Tests written to {test_path}")
        return test_code

    # ------------------------------------------------------------------
    # Changed-files-scoped verifier helpers
    # ------------------------------------------------------------------

    def _capture_pretask_sha(self, worktree_path: Path) -> Optional[str]:
        """Snapshot the worktree's HEAD before a task runs.

        Returns ``None`` on failure (e.g. fresh worktree with no commits,
        worktree is not a git dir, git is unavailable). Callers should
        treat ``None`` as "no diff base — fall back to broad scope".
        """
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("Could not capture pretask SHA: %s", e)
            return None
        if result.returncode != 0:
            return None
        sha = result.stdout.strip()
        return sha or None

    def _derive_changed_test_paths(
        self,
        worktree_path: Path,
        pretask_sha: Optional[str],
        test_glob: str = "**/tests/**/*.php",
    ) -> List[str]:
        """Return paths of test files changed since ``pretask_sha`` plus
        any test files that live in the same module dir as a changed
        implementation file (when that module's tests aren't already
        directly covered).

        An empty list means "fall back to broad scope" — caller is
        expected to feed the empty list to ``_get_test_command(paths=...)``
        which will substitute its default.
        """
        if pretask_sha is None:
            return []

        # Two reasons we don't use ``<pretask_sha>..HEAD`` here:
        #
        # 1. The per-task commit only lands AFTER tests pass — at verifier
        #    time the agent's writes are still in the worktree (modified)
        #    or merely on disk (brand-new files), so HEAD == pretask_sha
        #    and a HEAD-anchored diff is empty every time → broad fallback
        #    on every task → the very condition this scoping is meant to
        #    avoid.
        # 2. ``git diff <sha>`` compares <sha> to the working tree
        #    (including the index), which catches *modifications* to
        #    tracked files. But it does NOT catch brand-new untracked
        #    files — and brand-new test files are exactly what TDD
        #    produces. Untracked files have to be picked up via
        #    ``git ls-files --others --exclude-standard`` and unioned in.
        try:
            diff = subprocess.run(
                [
                    "git", "diff", "--name-only", "--diff-filter=AM",
                    pretask_sha, "--", test_glob,
                ],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            impl = subprocess.run(
                [
                    "git", "diff", "--name-only", "--diff-filter=AM",
                    pretask_sha,
                ],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            untracked = subprocess.run(
                [
                    "git", "ls-files", "--others", "--exclude-standard",
                ],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.SubprocessError, OSError) as e:
            logger.debug("git diff failed while deriving test paths: %s", e)
            return []

        if (
            diff.returncode != 0
            or impl.returncode != 0
            or untracked.returncode != 0
        ):
            logger.debug(
                "git diff/ls-files returned non-zero "
                "(test=%d impl=%d untracked=%d) — falling back",
                diff.returncode, impl.returncode, untracked.returncode,
            )
            return []

        # Untracked files split into test vs non-test by glob match.
        untracked_paths = [p for p in untracked.stdout.splitlines() if p]
        untracked_tests = [
            p for p in untracked_paths if fnmatch.fnmatch(p, test_glob)
        ]
        untracked_non_tests = [
            p for p in untracked_paths if not fnmatch.fnmatch(p, test_glob)
        ]

        direct = [p for p in diff.stdout.splitlines() if p] + untracked_tests
        impl_paths = (
            [p for p in impl.stdout.splitlines() if p] + untracked_non_tests
        )

        # Modules whose tests are already directly covered. We don't want
        # to also list the module's whole tests/ dir from inference —
        # the specific test file is the more useful scope.
        covered_roots: set = set()
        for p in direct:
            root = self._find_module_root(
                worktree_path, (worktree_path / p)
            )
            if root is not None:
                covered_roots.add(root)

        # Strip test files from impl_paths — they're handled above.
        non_test_impl = [
            p for p in impl_paths
            if not fnmatch.fnmatch(p, test_glob)
        ]
        inferred = self._infer_module_test_dirs(
            worktree_path, non_test_impl, exclude_roots=covered_roots
        )

        seen: set = set()
        out: List[str] = []
        for p in direct + inferred:
            if p not in seen:
                seen.add(p)
                out.append(p)
        return out

    def _infer_module_test_dirs(
        self,
        worktree_path: Path,
        changed_paths: List[str],
        exclude_roots: Optional[set] = None,
    ) -> List[str]:
        """For each changed file, walk up to the nearest Drupal module
        root (a directory containing a ``*.info.yml`` file) and return
        its ``tests/`` subdir if that subdir exists.

        Modules in ``exclude_roots`` are skipped — used by the caller
        to avoid duplicating coverage already in the direct-changes
        list.

        Returns deduped relative paths. When a path doesn't sit under a
        module root, or the module has no tests dir, it's silently
        skipped — broad scope is the safety net, not this helper.
        """
        if not changed_paths:
            return []
        excluded = exclude_roots or set()

        out: List[str] = []
        seen: set = set()
        for rel in changed_paths:
            abs_path = (worktree_path / rel).resolve()
            module_root = self._find_module_root(worktree_path, abs_path)
            if module_root is None or module_root in excluded:
                continue
            tests_dir = module_root / "tests"
            if not tests_dir.is_dir():
                continue
            try:
                rel_tests = tests_dir.relative_to(worktree_path).as_posix()
            except ValueError:
                continue
            if rel_tests not in seen:
                seen.add(rel_tests)
                out.append(rel_tests)
        return out

    @staticmethod
    def _find_module_root(
        worktree_path: Path, abs_path: Path
    ) -> Optional[Path]:
        """Walk parents of ``abs_path`` (capped at ``worktree_path``)
        until one contains a ``*.info.yml`` file. Returns that dir, or
        ``None`` if no module root is found within the worktree."""
        try:
            worktree_resolved = worktree_path.resolve()
            target = abs_path.resolve() if abs_path.exists() else abs_path
        except OSError:
            return None

        # Start from the file's parent dir (changed file itself isn't
        # the module root). If target is the worktree itself, bail.
        current = target if (target.exists() and target.is_dir()) else target.parent
        while True:
            try:
                current.relative_to(worktree_resolved)
            except ValueError:
                return None
            if current.is_dir():
                try:
                    if any(current.glob("*.info.yml")):
                        return current
                except OSError:
                    return None
            if current == worktree_resolved:
                return None
            parent = current.parent
            if parent == current:
                return None
            current = parent

    def run_tests(
        self,
        worktree_path: Path,
        pretask_sha: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run tests using the stack's test framework.

        If a container environment is attached (via set_environment),
        tests run inside the container. Otherwise, tests run on the host.

        When ``pretask_sha`` is supplied, the run is scoped to the test
        files changed since that SHA (plus tests that live alongside
        changed implementation files). When the diff yields no test
        paths, we fall back to the stack's default broad scope so
        implementation-only tasks still get a verifier signal.

        Args:
            worktree_path: Path to git worktree
            pretask_sha: Optional pre-task HEAD SHA captured at the
                start of ``implement_feature``. ``None`` preserves
                legacy broad-scope behavior.

        Returns:
            Dictionary with:
              - passed: bool — True if tests succeeded
              - test_results: str — raw stdout+stderr (renamed from "output")
              - structured_errors: list[StructuredError] — parsed errors,
                empty on pass
              - return_code: int — process exit code (-1 on exception/timeout)
        """
        logger.info(f"Running tests in {worktree_path}")

        paths = self._derive_changed_test_paths(worktree_path, pretask_sha)
        if pretask_sha is not None:
            logger.info(
                "Verifier scope: %s (pretask SHA %s)",
                f"{len(paths)} changed file(s)" if paths else "broad fallback",
                pretask_sha[:8],
            )

        test_cmd = self._get_test_command(paths=paths)

        if self._env_manager and self._env_ticket_id:
            return self._run_tests_in_container(test_cmd)

        return self._run_tests_on_host(test_cmd, worktree_path)

    def _ensure_composer_deps(self, max_attempts: int = 3):
        """Ensure composer dependencies and scaffold files are installed.

        Runs ``composer install`` up to ``max_attempts`` times. Composer plugins
        (notably ``cweagans/composer-patches``) sometimes need a second pass
        to fully activate when dependency ordering is awkward — retrying is a
        well-known workaround that costs nothing on the happy path.

        Returns the final ExecResult so callers can react to a permanent
        failure. Callers that ignore the return value retain prior behavior.
        """
        last_result = None
        for attempt in range(1, max_attempts + 1):
            logger.info(
                "Running composer install in container "
                f"(attempt {attempt}/{max_attempts})"
            )
            last_result = self._env_manager.exec(
                ticket_id=self._env_ticket_id,
                service="appserver",
                command=["composer", "install", "--no-interaction", "--no-progress"],
                workdir="/app",
            )
            if last_result.returncode == 0:
                if attempt > 1:
                    logger.info(f"composer install succeeded on attempt {attempt}")
                return last_result
            logger.warning(
                f"composer install attempt {attempt}/{max_attempts} failed "
                f"(rc={last_result.returncode}): "
                f"{(last_result.stderr or '')[:200]}"
            )
        logger.error(
            f"composer install failed after {max_attempts} attempts — "
            "callers should treat this as a fatal precondition failure"
        )
        return last_result

    def _resolve_test_cmd_for_container(self, test_cmd: List[str]) -> Optional[List[str]]:
        """Adapt the test command to what the container actually supports.

        Checks whether phpunit.xml exists and whether the requested
        ``--testsuite`` is defined in it.  Returns ``None`` when no
        phpunit config exists at all (meaning tests should be skipped
        rather than letting phpunit print its help text and exit non-zero).

        Returns:
            Adapted command list, or None if tests should be skipped.
        """
        # Check if phpunit.xml or phpunit.xml.dist exists
        check = self._env_manager.exec(
            ticket_id=self._env_ticket_id,
            service="appserver",
            command=["sh", "-c", "test -f phpunit.xml || test -f phpunit.xml.dist"],
            workdir="/app",
        )
        if check.returncode != 0:
            # No config file at all — cannot run phpunit
            logger.info("No phpunit.xml found in container — skipping test execution")
            return None

        # Config exists — check if requested testsuite is defined
        testsuite_arg = next((a for a in test_cmd if a.startswith("--testsuite=")), None)
        if testsuite_arg:
            suite_name = testsuite_arg.split("=", 1)[1]
            grep = self._env_manager.exec(
                ticket_id=self._env_ticket_id,
                service="appserver",
                command=["grep", "-q", f'name="{suite_name}"', "phpunit.xml"],
                workdir="/app",
            )
            if grep.returncode != 0:
                # Also check phpunit.xml.dist
                grep2 = self._env_manager.exec(
                    ticket_id=self._env_ticket_id,
                    service="appserver",
                    command=["grep", "-q", f'name="{suite_name}"', "phpunit.xml.dist"],
                    workdir="/app",
                )
                if grep2.returncode != 0:
                    logger.info(
                        f"Testsuite '{suite_name}' not found in phpunit config — stripping flag"
                    )
                    return [arg for arg in test_cmd if not arg.startswith("--testsuite")]

        return test_cmd

    def _run_tests_in_container(self, test_cmd: List[str]) -> Dict[str, Any]:
        """Execute tests inside the container environment.

        Args:
            test_cmd: Test command as list of strings

        Returns:
            Dictionary matching the run_tests shape (passed, test_results,
            structured_errors, return_code).
        """
        logger.info(f"Running tests in container (service=appserver)")

        try:
            # Ensure composer deps (including phpunit) exist
            self._ensure_composer_deps()

            # Adapt command to container's phpunit config
            resolved_cmd = self._resolve_test_cmd_for_container(test_cmd)

            if resolved_cmd is None:
                # No phpunit config exists — skip tests gracefully
                return {
                    "passed": True,
                    "test_results": "No phpunit configuration found — skipping test execution",
                    "structured_errors": [],
                    "return_code": 0,
                }

            result = self._env_manager.exec(
                ticket_id=self._env_ticket_id,
                service="appserver",
                command=resolved_cmd,
                workdir="/app",
            )

            output = result.stdout + result.stderr
            passed = result.returncode == 0
            structured_errors = (
                [] if passed else self._parse_test_output(output, result.returncode)
            )

            return {
                "passed": passed,
                "test_results": output,
                "structured_errors": structured_errors,
                "return_code": result.returncode,
            }

        except Exception as e:
            logger.error(f"Error running tests in container: {e}")
            return {
                "passed": False,
                "test_results": str(e),
                "structured_errors": [],
                "return_code": -1,
            }

    def _run_tests_on_host(
        self, test_cmd: List[str], worktree_path: Path
    ) -> Dict[str, Any]:
        """Execute tests on the host via subprocess.

        Args:
            test_cmd: Test command as list of strings
            worktree_path: Path to git worktree

        Returns:
            Dictionary matching the run_tests shape (passed, test_results,
            structured_errors, return_code).
        """
        try:
            result = subprocess.run(
                test_cmd,
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=300,
            )

            output = result.stdout + result.stderr
            passed = result.returncode == 0
            structured_errors = (
                [] if passed else self._parse_test_output(output, result.returncode)
            )

            return {
                "passed": passed,
                "test_results": output,
                "structured_errors": structured_errors,
                "return_code": result.returncode,
            }

        except subprocess.TimeoutExpired:
            logger.error("Tests timed out after 5 minutes")
            return {
                "passed": False,
                "test_results": "Tests timed out",
                "structured_errors": [],
                "return_code": -1,
            }
        except Exception as e:
            logger.error(f"Error running tests: {e}")
            return {
                "passed": False,
                "test_results": str(e),
                "structured_errors": [],
                "return_code": -1,
            }

    def commit_changes(
        self,
        message: str,
        files: List[str],
        worktree_path: Path,
    ) -> None:
        """Commit changes to git.

        Args:
            message: Commit message
            files: List of files to commit
            worktree_path: Path to git worktree
        """
        logger.info(f"Committing changes: {message}")

        try:
            for file in files:
                subprocess.run(
                    ["git", "add", file],
                    cwd=worktree_path,
                    check=True,
                )

            commit_msg = f"""{message}

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"""

            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=worktree_path,
                check=True,
            )

            logger.info("Changes committed successfully")

        except subprocess.CalledProcessError as e:
            logger.error(f"Git commit failed: {e}")
            raise

    def run(  # type: ignore[override]
        self,
        plan_file: Path,
        worktree_path: Path,
        user_prompt: str | None = None,
        regressions: Optional[RegressionContext] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run the complete implementation workflow.

        Args:
            plan_file: Path to implementation plan
            worktree_path: Path to git worktree
            regressions: Optional structured failures from the prior
                iteration. When non-empty, each task's developer prompt
                is prefixed with a "## Prior Iteration Regressions"
                section. The returned dict also includes
                ``regression_errors`` — the union of structured errors
                from any tasks that failed in *this* iteration, ready
                for the caller to fold into the next iteration's
                ``RegressionContext``.
            **kwargs: Additional parameters

        Returns:
            Dictionary with implementation results
        """
        logger.info(f"Running implementation for plan: {plan_file}")

        # Extract project key from plan filename
        ticket_id = plan_file.stem
        if "-" in ticket_id:
            project_key = ticket_id.split("-")[0]
            self.set_project(project_key)

        # Check for attachments context
        attach_dir = worktree_path / ".agents" / "attachments" / ticket_id
        attachment_context = ""
        if attach_dir.exists():
            files = list(attach_dir.iterdir())
            if files:
                attachment_context = (
                    "\n\nNote: This ticket has attachments available at "
                    f"`{attach_dir}`. Use the Read tool to view them if needed."
                )
                logger.info(f"Found {len(files)} attachments for {ticket_id}")

        # Baseline gate: config must be valid BEFORE we touch anything.
        # If it's broken now, nothing this run does is to blame and the
        # post-implementation developer-fix retry loop is the wrong
        # remediation — it would ask the developer to fix code they
        # never touched.
        baseline_config = self.validate_config(worktree_path)
        if (
            not baseline_config.get("success", True)
            and not baseline_config.get("environment_issue")
        ):
            parsed = parse_drush_config_validation(
                baseline_config.get("output", "") or ""
            )
            logger.error(
                "Baseline config validation failed before any task ran — "
                "aborting run. Parsed %d structured error(s).",
                len(parsed),
            )
            return {
                "tasks_completed": 0,
                "tasks_failed": 0,
                "test_results": None,
                "config_validation": baseline_config,
                "results": [],
                "aborted": "baseline_config_broken",
                "baseline_failure": parsed,
            }

        # Break down plan into tasks
        tasks = self.break_down_plan(plan_file)

        # Implement each task
        results = []
        regression_errors: List[StructuredError] = []
        for task in tasks:
            try:
                task_with_context = task + attachment_context if attachment_context else task
                impl_result = self.implement_feature(
                    task_with_context, {}, worktree_path,
                    user_prompt=user_prompt,
                    regressions=regressions,
                )

                if impl_result.get("success"):
                    all_files = (
                        impl_result.get("files_created", []) +
                        impl_result.get("files_modified", [])
                    )
                    if all_files:
                        self.commit_changes(
                            message=impl_result.get("commit_message", f"feat: {task}"),
                            files=all_files,
                            worktree_path=worktree_path,
                        )

                results.append({"task": task, "success": True, "details": impl_result})
            except Exception as e:
                logger.error(f"Failed to implement task '{task}': {e}")
                # Capture any structured errors carried on the exception so
                # the iteration loop can inject them as additional acceptance
                # criteria into the next iteration's task prompts.
                task_errors = list(getattr(e, "structured_errors", []) or [])
                regression_errors.extend(task_errors)
                results.append({
                    "task": task,
                    "success": False,
                    "error": str(e),
                    "structured_errors": task_errors,
                })

        # Run all tests
        test_results = self.run_tests(worktree_path)

        # Validate project config (e.g. Drupal config sync)
        config_validation = self.validate_config(worktree_path)

        # If config validation failed due to actual config issues (not env),
        # let the developer attempt to fix it
        max_config_retries = 2
        for config_attempt in range(max_config_retries):
            if config_validation.get("success", True):
                break
            if config_validation.get("environment_issue"):
                logger.warning("Config validation failed due to environment issue — skipping retry")
                break

            logger.warning(
                "Config validation failed (attempt %d/%d) — asking developer to fix",
                config_attempt + 1,
                max_config_retries,
            )

            config_output = config_validation.get("output", "")[:2000]
            fix_task = (
                "Fix the config validation failure. The Drupal config sync "
                "(drush site:install --config-dir=../config/sync) failed with:\n\n"
                f"{config_output}\n\n"
                "Analyze the error, create or fix the missing config files, "
                "and ensure config dependencies are satisfied."
            )

            try:
                fix_result = self.implement_feature(fix_task, {}, worktree_path, user_prompt=user_prompt)
                if fix_result.get("success"):
                    changed = (
                        fix_result.get("files_created", [])
                        + fix_result.get("files_modified", [])
                    )
                    if changed:
                        self.commit_changes(
                            message="fix: resolve config sync validation failure",
                            files=changed,
                            worktree_path=worktree_path,
                        )
            except Exception as e:
                logger.error("Config fix attempt failed: %s", e)

            config_validation = self.validate_config(worktree_path)

        return {
            "tasks_completed": sum(1 for r in results if r["success"]),
            "tasks_failed": sum(1 for r in results if not r["success"]),
            "test_results": test_results,
            "config_validation": config_validation,
            "results": results,
            "regression_errors": _dedupe_structured_errors(regression_errors),
        }

    def run_revision(  # type: ignore[override]
        self,
        ticket_id: str,
        worktree_path: Path,
        user_prompt: str | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Revise implementation based on MR feedback.

        Args:
            ticket_id: Jira ticket ID
            worktree_path: Path to the git worktree
            **kwargs: Additional parameters

        Returns:
            Dictionary with revision results
        """
        from src.gitlab_client import GitLabClient
        from src.config_loader import get_config

        logger.info(f"Running implementation revision for {ticket_id}")

        project_key = ticket_id.split("-")[0]
        self.set_project(project_key)

        gitlab = GitLabClient()
        config = get_config()

        project_config = config.get_project_config(project_key)
        git_url = project_config.get("git_url", "")

        project_path = gitlab.extract_project_path(git_url)

        source_branch = get_branch_name(ticket_id)
        mrs = gitlab.list_merge_requests(
            project_id=project_path,
            source_branch=source_branch,
        )

        if not mrs:
            raise ValueError(
                f"No MR found for branch {source_branch}. "
                f"Run 'sentinel execute {ticket_id}' first to create the initial implementation."
            )

        mr_data = mrs[0]
        mr_iid = mr_data["iid"]
        mr_url = mr_data["web_url"]

        logger.info(f"Found MR: {mr_url}")

        # Fetch unresolved discussions
        discussions = gitlab.get_merge_request_discussions(
            project_id=project_path,
            mr_iid=mr_iid,
            unresolved_only=True,
        )

        if not discussions:
            logger.info("No unresolved discussions found - nothing to revise")
            return {
                "mr_url": mr_url,
                "feedback_count": 0,
                "changes_committed": False,
                "responses_posted": 0,
                "message": "No unresolved discussions to address",
            }

        logger.info(f"Found {len(discussions)} unresolved discussions")

        # Analyze each discussion
        discussion_tasks = []

        for discussion in discussions:
            notes = discussion.get("notes", [])
            if not notes:
                continue

            first_note = notes[0]
            author = first_note.get("author", {}).get("name", "Unknown")
            body = first_note.get("body", "")
            discussion_id = discussion.get("id", "")

            analysis_prompt = f"""Analyze this code review feedback and classify it.

**Feedback:**
Author: {author}
Comment: {body}

**Instructions:**
Classify into ONE of these categories:

1. TASK: Requires code changes
   - Output format: TASK: <imperative verb task description>
   - Example: TASK: Remove PostgreSQL configuration from settings

2. QUESTION: Asking for clarification/explanation
   - Output format: QUESTION
   - Example inputs: "Why is this deleted?", "What does this do?", "How does this work?"

3. ACKNOWLEDGE: FYI/suggestion that doesn't need response
   - Output format: ACKNOWLEDGE
   - Example inputs: "Looks good", "Nice work", "Consider this in future"

**Your output (TASK:/QUESTION/ACKNOWLEDGE only):**"""

            try:
                response = self.send_message(analysis_prompt)
                classification = response.strip()

                if classification.upper().startswith("TASK:"):
                    task = classification[5:].strip()
                    logger.info(f"Discussion {discussion_id}: Task extracted: {task}")
                    discussion_tasks.append({
                        "discussion_id": discussion_id,
                        "discussion": discussion,
                        "task": task,
                        "type": "task",
                    })
                elif classification.upper() == "QUESTION":
                    logger.info(f"Discussion {discussion_id}: Question detected")
                    discussion_tasks.append({
                        "discussion_id": discussion_id,
                        "discussion": discussion,
                        "question": body,
                        "type": "question",
                    })
                else:
                    logger.info(f"Discussion {discussion_id}: Acknowledgment")
                    discussion_tasks.append({
                        "discussion_id": discussion_id,
                        "discussion": discussion,
                        "type": "acknowledge",
                    })

            except Exception as e:
                logger.error(f"Failed to analyze discussion {discussion_id}: {e}")
                discussion_tasks.append({
                    "discussion_id": discussion_id,
                    "discussion": discussion,
                    "type": "acknowledge",
                    "error": str(e),
                })

        task_count = sum(1 for dt in discussion_tasks if dt.get("type") == "task")
        question_count = sum(1 for dt in discussion_tasks if dt.get("type") == "question")
        ack_count = sum(1 for dt in discussion_tasks if dt.get("type") == "acknowledge")
        logger.info(
            f"Analyzed {len(discussion_tasks)} discussions: "
            f"{task_count} tasks, {question_count} questions, {ack_count} acknowledgments"
        )

        # Implement tasks and answer questions
        all_changed_files: List[str] = []

        for item in discussion_tasks:
            if item.get("type") == "task":
                task = item["task"]
                try:
                    logger.info(f"Implementing task for discussion {item['discussion_id']}: {task}")
                    impl_result = self.implement_feature(
                        task=task,
                        context={},
                        worktree_path=worktree_path,
                        commit_prefix="fix",
                        user_prompt=user_prompt,
                    )

                    if impl_result.get("success"):
                        changed_files = (
                            impl_result.get("files_created", []) +
                            impl_result.get("files_modified", [])
                        )
                        all_changed_files.extend(changed_files)

                        if changed_files:
                            self.commit_changes(
                                message=impl_result.get("commit_message", f"fix: {task[:72]}"),
                                files=changed_files,
                                worktree_path=worktree_path,
                            )

                    item["impl_result"] = impl_result
                    item["success"] = impl_result.get("success", False)

                except Exception as e:
                    logger.error(
                        f"Failed to implement task for discussion {item['discussion_id']}: {e}"
                    )
                    item["success"] = False
                    item["error"] = str(e)

            elif item.get("type") == "question":
                question = item["question"]
                try:
                    logger.info(
                        f"Answering question for discussion {item['discussion_id']}: {question}"
                    )

                    discussion = item.get("discussion", {})
                    notes = discussion.get("notes", [])
                    code_context = None
                    file_path = None
                    line_info = None

                    if notes:
                        first_note = notes[0]
                        position = first_note.get("position")

                        if position:
                            file_path = position.get("new_path") or position.get("old_path")
                            new_line = position.get("new_line")
                            old_line = position.get("old_line")

                            if file_path:
                                line_info = f"Line {new_line or old_line}"
                                try:
                                    context_lines = 10
                                    target_line = new_line or old_line

                                    if target_line:
                                        show_cmd = ["git", "show", f"HEAD:{file_path}"]
                                        show_result = subprocess.run(
                                            show_cmd,
                                            cwd=worktree_path,
                                            capture_output=True,
                                            text=True,
                                            timeout=10,
                                        )

                                        if show_result.returncode == 0:
                                            file_lines = show_result.stdout.splitlines()
                                            start_line = max(0, target_line - context_lines - 1)
                                            end_line = min(
                                                len(file_lines), target_line + context_lines
                                            )

                                            context_snippet = file_lines[start_line:end_line]
                                            numbered_lines = [
                                                f"{start_line + i + 1:4d} {line}"
                                                for i, line in enumerate(context_snippet)
                                            ]
                                            code_context = "\n".join(numbered_lines)
                                except Exception as e:
                                    logger.warning(f"Failed to extract code context: {e}")

                    if not code_context:
                        diff_cmd = ["git", "diff", "origin/main...HEAD"]
                        try:
                            diff_result = subprocess.run(
                                diff_cmd,
                                cwd=worktree_path,
                                capture_output=True,
                                text=True,
                                timeout=30,
                            )
                            code_context = diff_result.stdout[:5000]
                            file_path = "multiple files"
                            line_info = "see diff"
                        except Exception:
                            code_context = "Unable to retrieve diff"

                    context_desc = (
                        f"**File:** {file_path}\n**Location:** {line_info}\n\n"
                        if file_path and line_info
                        else ""
                    )

                    answer_prompt = f"""A code reviewer asked a question about the implementation. Answer it based on the code changes.

**Question:**
{question}

{context_desc}**Code context:**
```
{code_context}
```

**Instructions:**
Provide a clear, concise answer (2-3 sentences) explaining:
- What this code does
- Why it was implemented this way
- How it addresses their concern

**Your answer:**"""

                    answer = self.send_message(answer_prompt)
                    item["answer"] = answer.strip()
                    item["success"] = True
                    logger.info(f"Generated answer for discussion {item['discussion_id']}")

                except Exception as e:
                    logger.error(
                        f"Failed to answer question for discussion {item['discussion_id']}: {e}"
                    )
                    item["success"] = False
                    item["error"] = str(e)

        # Run tests to verify fixes
        test_results = self.run_tests(worktree_path)

        # Validate project config (e.g. Drupal config sync)
        config_validation = self.validate_config(worktree_path)

        # If config validation failed due to actual config issues (not env),
        # let the developer attempt to fix it
        max_config_retries = 2
        for config_attempt in range(max_config_retries):
            if config_validation.get("success", True):
                break
            if config_validation.get("environment_issue"):
                logger.warning("Config validation failed due to environment issue — skipping retry")
                break

            logger.warning(
                "Config validation failed (attempt %d/%d) — asking developer to fix",
                config_attempt + 1,
                max_config_retries,
            )

            config_output = config_validation.get("output", "")[:2000]
            fix_task = (
                "Fix the config validation failure. The Drupal config sync "
                "(drush site:install --config-dir=../config/sync) failed with:\n\n"
                f"{config_output}\n\n"
                "Analyze the error, create or fix the missing config files, "
                "and ensure config dependencies are satisfied."
            )

            try:
                fix_result = self.implement_feature(fix_task, {}, worktree_path, user_prompt=user_prompt)
                if fix_result.get("success"):
                    changed = (
                        fix_result.get("files_created", [])
                        + fix_result.get("files_modified", [])
                    )
                    if changed:
                        self.commit_changes(
                            message="fix: resolve config sync validation failure",
                            files=changed,
                            worktree_path=worktree_path,
                        )
            except Exception as e:
                logger.error("Config fix attempt failed: %s", e)

            config_validation = self.validate_config(worktree_path)

        # Reply to ALL discussions
        responses_posted = 0
        for item in discussion_tasks:
            discussion = item["discussion"]
            discussion_id = item["discussion_id"]

            discussion_type = item.get("type")

            if discussion_type == "task" and item.get("success"):
                task = item["task"]
                impl_result = item.get("impl_result", {})
                files_changed = (
                    impl_result.get("files_modified", []) + impl_result.get("files_created", [])
                )
                files_text = ", ".join(files_changed) if files_changed else "No files modified"

                reply_body = f"""✅ **Implemented**

{task}

**Files changed:** {files_text}

**Tests:** {'✅ Passing' if test_results.get('passed') else '⚠️ Some failures'}

---
🤖 Sentinel Developer
"""
                resolve = True
                emoji = "white_check_mark"

            elif discussion_type == "task" and not item.get("success"):
                task = item["task"]
                error = item.get("error", "Unknown error")

                reply_body = f"""⚠️ **Failed to implement**

Task: {task}

Error: {error}

This will need manual intervention.

---
🤖 Sentinel Developer
"""
                resolve = False
                emoji = "warning"

            elif discussion_type == "question" and item.get("success"):
                answer = item.get("answer", "Unable to determine answer")

                reply_body = f"""{answer}

---
🤖 Sentinel Developer
"""
                resolve = True
                emoji = "speech_balloon"

            elif discussion_type == "question" and not item.get("success"):
                error = item.get("error", "Unknown error")

                reply_body = f"""⚠️ **Unable to answer question**

Error: {error}

A human will need to respond to this.

---
🤖 Sentinel Developer
"""
                resolve = False
                emoji = "warning"

            else:
                reply_body = """👍 **Acknowledged**

---
🤖 Sentinel Developer
"""
                resolve = True
                emoji = "thumbsup"

            try:
                notes = discussion.get("notes", [])
                if notes:
                    latest_note = notes[-1]
                    latest_note_id = latest_note.get("id")
                    if latest_note_id:
                        try:
                            gitlab.add_emoji_reaction(
                                project_id=project_path,
                                mr_iid=mr_iid,
                                note_id=latest_note_id,
                                emoji=emoji,
                                discussion_id=discussion_id,
                            )
                        except Exception as e:
                            logger.warning(f"Failed to add reaction: {e}")

                gitlab.reply_to_discussion(
                    project_id=project_path,
                    mr_iid=mr_iid,
                    discussion_id=discussion_id,
                    body=reply_body,
                    resolve=resolve,
                )
                responses_posted += 1
                logger.info(f"Replied to discussion {discussion_id} (resolve={resolve})")

            except Exception as e:
                logger.error(f"Failed to reply to discussion {discussion_id}: {e}")

        # Add summary comment to MR
        tasks_total = sum(1 for item in discussion_tasks if item.get("type") == "task")
        questions = sum(1 for item in discussion_tasks if item.get("type") == "question")
        acknowledged = sum(1 for item in discussion_tasks if item.get("type") == "acknowledge")

        tasks_completed = sum(
            1 for item in discussion_tasks
            if item.get("type") == "task" and item.get("success")
        )
        tasks_failed = sum(
            1 for item in discussion_tasks
            if item.get("type") == "task" and not item.get("success")
        )
        questions_answered = sum(
            1 for item in discussion_tasks
            if item.get("type") == "question" and item.get("success")
        )
        questions_failed = sum(
            1 for item in discussion_tasks
            if item.get("type") == "question" and not item.get("success")
        )

        summary = f"""## Implementation Revision Summary 🔄

**Discussions Analyzed:** {len(discussion_tasks)}
**Tasks:** {tasks_completed}/{tasks_total} completed ({tasks_failed} failed)
**Questions:** {questions_answered}/{questions} answered ({questions_failed} failed)
**Acknowledged:** {acknowledged}
**Tests:** {'✅ All passing' if test_results.get('passed') else '⚠️ Some failures - see output'}
**Config:** {'✅ Valid' if config_validation.get('success') else '❌ Validation failed — config dependencies broken'}

All discussions have been addressed.

---
🤖 Updated by Sentinel Developer
"""

        try:
            gitlab.add_merge_request_comment(
                project_id=project_path,
                mr_iid=mr_iid,
                body=summary,
            )
        except Exception as e:
            logger.error(f"Failed to add summary comment: {e}")

        return {
            "mr_url": mr_url,
            "feedback_count": len(discussion_tasks),
            "tasks": tasks_total,
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
            "questions": questions,
            "questions_answered": questions_answered,
            "questions_failed": questions_failed,
            "acknowledged": acknowledged,
            "changes_committed": len(all_changed_files) > 0,
            "responses_posted": responses_posted,
            "test_results": test_results,
            "config_validation": config_validation,
        }

    def _format_mr_feedback(self, feedback: list[Dict[str, Any]]) -> str:
        """Format MR feedback for LLM consumption.

        Args:
            feedback: List of discussion dictionaries from GitLab

        Returns:
            Formatted feedback text
        """
        formatted = []

        for i, discussion in enumerate(feedback, 1):
            notes = discussion.get("notes", [])
            if not notes:
                continue

            first_note = notes[0]
            author = first_note.get("author", {}).get("name", "Unknown")
            body = first_note.get("body", "")
            discussion_id = discussion.get("id", "")

            formatted.append(f"""
**Feedback {i}** (ID: {discussion_id})
Author: {author}
Comment: {body}
""")

            if len(notes) > 1:
                formatted.append("Replies:")
                for note in notes[1:]:
                    reply_author = note.get("author", {}).get("name", "Unknown")
                    reply_body = note.get("body", "")
                    formatted.append(f"  - {reply_author}: {reply_body}")

        return "\n".join(formatted)
