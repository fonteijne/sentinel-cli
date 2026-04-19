"""Base Developer Agent — shared orchestration for stack-specific developer agents."""

import asyncio
import logging
import subprocess
from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from src.environment_manager import EnvironmentManager

from src.agents.base_agent import ImplementationAgent
from src.prompt_loader import load_agent_prompt
from src.worktree_manager import get_branch_name


logger = logging.getLogger(__name__)


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
    def _get_test_command(self) -> List[str]:
        """Return the test runner CLI command for this stack.

        Returns:
            Command list, e.g. ["pytest", "-v", "--tb=short"]
        """

    @abstractmethod
    def _get_test_stub(self) -> str:
        """Return minimal test file content for this stack.

        Returns:
            Test stub source code string
        """

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
    ) -> Dict[str, Any]:
        """Implement a feature following TDD approach.

        Args:
            task: Task description
            context: Implementation context
            worktree_path: Path to git worktree
            commit_prefix: Git commit prefix (feat, fix, etc.)

        Returns:
            Dictionary with success, files_created, files_modified,
            test_results, commit_message, agent_response
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

            test_results = self.run_tests(worktree_path)

            if not test_results.get("success"):
                logger.warning(f"Tests failed after TDD implementation: {test_results.get('output')}")
                raise RuntimeError(
                    f"TDD cycle completed but tests are failing: {test_results.get('output')}"
                )

            task_summary = task[:72] if len(task) <= 72 else task[:69] + "..."
            test_output = test_results.get("output", "")
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

    def run_tests(self, worktree_path: Path) -> Dict[str, Any]:
        """Run tests using the stack's test framework.

        If a container environment is attached (via set_environment),
        tests run inside the container. Otherwise, tests run on the host.

        Args:
            worktree_path: Path to git worktree

        Returns:
            Dictionary with success, output, return_code
        """
        logger.info(f"Running tests in {worktree_path}")

        test_cmd = self._get_test_command()

        if self._env_manager and self._env_ticket_id:
            return self._run_tests_in_container(test_cmd)

        return self._run_tests_on_host(test_cmd, worktree_path)

    def _ensure_composer_deps(self) -> None:
        """Ensure composer dependencies and scaffold files are installed.

        Always runs ``composer install`` to guarantee all dependencies,
        scaffold files (e.g. default.settings.php), and binaries are present.
        """
        logger.info("Running composer install in container")
        result = self._env_manager.exec(
            ticket_id=self._env_ticket_id,
            service="appserver",
            command=["composer", "install", "--no-interaction", "--no-progress"],
            workdir="/app",
        )
        if result.returncode != 0:
            logger.warning(f"composer install failed: {result.stderr}")

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
            Dictionary with success, output, return_code
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
                    "success": True,
                    "output": "No phpunit configuration found — skipping test execution",
                    "return_code": 0,
                }

            result = self._env_manager.exec(
                ticket_id=self._env_ticket_id,
                service="appserver",
                command=resolved_cmd,
                workdir="/app",
            )

            output = result.stdout + result.stderr
            success = result.returncode == 0

            return {
                "success": success,
                "output": output,
                "return_code": result.returncode,
            }

        except Exception as e:
            logger.error(f"Error running tests in container: {e}")
            return {
                "success": False,
                "output": str(e),
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
            Dictionary with success, output, return_code
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
            success = result.returncode == 0

            return {
                "success": success,
                "output": output,
                "return_code": result.returncode,
            }

        except subprocess.TimeoutExpired:
            logger.error("Tests timed out after 5 minutes")
            return {
                "success": False,
                "output": "Tests timed out",
                "return_code": -1,
            }
        except Exception as e:
            logger.error(f"Error running tests: {e}")
            return {
                "success": False,
                "output": str(e),
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
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run the complete implementation workflow.

        Args:
            plan_file: Path to implementation plan
            worktree_path: Path to git worktree
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

        # Break down plan into tasks
        tasks = self.break_down_plan(plan_file)

        # Implement each task
        results = []
        for task in tasks:
            try:
                task_with_context = task + attachment_context if attachment_context else task
                impl_result = self.implement_feature(task_with_context, {}, worktree_path, user_prompt=user_prompt)

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
                results.append({"task": task, "success": False, "error": str(e)})

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

**Tests:** {'✅ Passing' if test_results.get('success') else '⚠️ Some failures'}

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
**Tests:** {'✅ All passing' if test_results.get('success') else '⚠️ Some failures - see output'}
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
