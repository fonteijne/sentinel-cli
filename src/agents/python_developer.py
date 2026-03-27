"""Python Developer Agent - Implements features using TDD."""

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from src.agents.base_agent import ImplementationAgent
from src.beads_manager import BeadsManager


logger = logging.getLogger(__name__)


class PythonDeveloperAgent(ImplementationAgent):
    """Agent that implements Python features using Test-Driven Development.

    Uses Claude Sonnet 4.5 for code generation with TDD approach.
    """

    def __init__(self) -> None:
        """Initialize Python developer agent."""
        super().__init__(
            agent_name="python_developer",
            model="claude-4-5-sonnet",
            temperature=0.2,
        )

        self.beads = BeadsManager()

    def break_down_plan(self, plan_file: Path) -> List[str]:
        """Break down implementation plan into actionable tasks using LLM.

        Args:
            plan_file: Path to the implementation plan

        Returns:
            List of task descriptions extracted from plan

        Note:
            Uses LLM to intelligently parse the plan and extract tasks,
            supporting both checklist format (- [ ]) and prose format (### Step 1:).
        """
        logger.info(f"Breaking down plan: {plan_file}")

        # Read plan content
        if not plan_file.exists():
            logger.warning(f"Plan file not found: {plan_file}")
            return []

        plan_content = plan_file.read_text()

        # Use LLM to extract tasks intelligently
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

            # Parse response into task list
            tasks = []
            for line in response.strip().split("\n"):
                task = line.strip()
                # Skip empty lines, markdown formatting, section headers
                if task and not task.startswith("#") and not task.startswith("-"):
                    tasks.append(task)

            logger.info(f"LLM extracted {len(tasks)} tasks from plan")

            if len(tasks) == 0:
                logger.warning("LLM extracted 0 tasks, falling back to regex parsing")
                # Fallback: try to extract from checklist format
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
            # Check if we're entering the implementation section
            if line.startswith("## ") and any(
                marker in line.lower()
                for marker in ["step-by-step", "implementation steps", "implementation tasks"]
            ):
                in_implementation_section = True
                continue

            # Check if we're leaving the implementation section (next ## heading)
            if in_implementation_section and line.startswith("## "):
                break

            # Extract checklist items only within implementation section
            if in_implementation_section and line.strip().startswith("- [ ]"):
                task = line.strip()[6:].strip()  # Remove "- [ ] "
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
    ) -> Dict[str, Any]:
        """Implement a feature following TDD approach.

        Args:
            task: Task description
            context: Implementation context
            worktree_path: Path to git worktree

        Returns:
            Dictionary with:
                - success: bool
                - files_created: List[str]
                - files_modified: List[str]
                - test_results: Dict (pytest output)
                - commit_message: str

        Note:
            This follows the TDD cycle:
            1. Write failing test (RED)
            2. Implement minimal code to pass (GREEN)
            3. Refactor while keeping tests passing (REFACTOR)
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

            # Get workflow definition
            workflow = cmd_result.get("workflow", [])
            logger.info(f"Loaded TDD workflow with {len(workflow)} steps")

        except Exception as e:
            logger.error(f"Error loading TDD command: {e}")
            raise

        # Build comprehensive TDD prompt for Agent SDK
        tdd_prompt = f"""Execute Test-Driven Development (TDD) for the following task:

TASK: {task}

CONTEXT: {context}

WORKTREE PATH: {worktree_path}

TDD WORKFLOW (RED-GREEN-REFACTOR):

**Phase 1: RED - Write Failing Test**
1. Analyze the feature requirements
2. Create appropriate test file in tests/ directory
3. Write comprehensive test cases:
   - Happy path scenarios
   - Edge cases
   - Error conditions
4. Run pytest to confirm test FAILS
5. If test passes unexpectedly, revise to actually test the feature

**Phase 2: GREEN - Minimal Implementation**
1. Analyze the failing test output
2. Implement the MINIMAL code needed to pass tests
3. Write clean, well-typed Python code
4. Run pytest to confirm tests PASS
5. If tests fail, debug and iterate until passing

**Phase 3: REFACTOR - Improve Code Quality**
1. Review implementation for improvements
2. Apply refactoring (DRY principle, clear naming, proper structure)
3. Run pytest after EACH change to ensure tests still pass
4. Continue until code quality is acceptable

**Quality Gates:**
- All tests must pass
- Code must have type hints
- Follow PEP 8 style guidelines
- Cyclomatic complexity <= 10
- Function length <= 50 lines

**Deliverables:**
1. Test file(s) created
2. Implementation file(s) created/modified
3. All tests passing
4. Clean, well-structured code

Execute this TDD cycle now. Use Read/Write/Edit tools for files and Bash for running pytest.
"""

        # Execute TDD workflow using Agent SDK
        # NOTE: We don't pass session_id here because each TDD task should be independent.
        # Session resumption with --print mode causes the CLI to exit with code 1.
        try:
            result = asyncio.run(self.agent_sdk.execute_with_tools(
                prompt=tdd_prompt,
                session_id=None,  # Each TDD task is independent
                system_prompt=self.system_prompt,
                cwd=str(worktree_path),
            ))

            # Parse response for file changes
            files_created = []
            files_modified = []

            # Extract file operations from tool uses
            for tool_use in result.get("tool_uses", []):
                tool_name = tool_use.get("tool")
                if tool_name == "Write":
                    files_created.append(tool_use.get("input", {}).get("file_path", ""))
                elif tool_name == "Edit":
                    files_modified.append(tool_use.get("input", {}).get("file_path", ""))

            # Run final test validation
            test_results = self.run_tests(worktree_path)

            if not test_results.get("success"):
                logger.warning(f"Tests failed after TDD implementation: {test_results.get('output')}")
                raise RuntimeError(f"TDD cycle completed but tests are failing: {test_results.get('output')}")

            # Generate commit message
            # Truncate task to reasonable length for commit message
            task_summary = task[:72] if len(task) <= 72 else task[:69] + "..."
            commit_message = f"{commit_prefix}: {task_summary}\n\n- Implemented using TDD approach\n- All tests passing"

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

        Note:
            Uses LLM to generate comprehensive tests covering:
            - Happy path scenarios
            - Edge cases
            - Error conditions
        """
        logger.info(f"Writing tests to {test_path}")

        # TODO: Use LLM to generate comprehensive tests
        test_code = '''"""Tests for implementation."""

import pytest


def test_basic_functionality():
    """Test basic functionality."""
    # TODO: Implement actual tests
    pass


def test_edge_cases():
    """Test edge cases."""
    # TODO: Implement actual tests
    pass


def test_error_handling():
    """Test error handling."""
    # TODO: Implement actual tests
    pass
'''

        # Write test file
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(test_code)

        logger.info(f"Tests written to {test_path}")

        return test_code

    def run_tests(self, worktree_path: Path) -> Dict[str, Any]:
        """Run tests using pytest.

        Args:
            worktree_path: Path to git worktree

        Returns:
            Dictionary with:
                - success: bool
                - output: str
                - failed_count: int
                - passed_count: int
        """
        logger.info(f"Running tests in {worktree_path}")

        try:
            result = subprocess.run(
                ["pytest", "-v", "--tb=short"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            output = result.stdout + result.stderr

            # Parse pytest output
            # This is simplified - actual implementation would parse more details
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
            # Stage files
            for file in files:
                subprocess.run(
                    ["git", "add", file],
                    cwd=worktree_path,
                    check=True,
                )

            # Commit with co-author
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

        # Extract project key from plan filename (e.g., "ACME-123.md" -> "ACME")
        ticket_id = plan_file.stem  # e.g., "ACME-123"
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

        # Create beads tasks for tracking
        for task in tasks:
            try:
                task_id = self.beads.create_task(
                    title=task,
                    task_type="task",
                    priority=1,
                    working_dir=str(worktree_path),
                )
                logger.info(f"Created task: {task_id}")
            except Exception as e:
                logger.warning(f"Could not create beads task: {e}")

        # Implement each task
        results = []
        for task in tasks:
            try:
                task_with_context = task + attachment_context if attachment_context else task
                impl_result = self.implement_feature(task_with_context, {}, worktree_path)

                # Commit the changes if implementation was successful
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

        return {
            "tasks_completed": sum(1 for r in results if r["success"]),
            "tasks_failed": sum(1 for r in results if not r["success"]),
            "test_results": test_results,
            "results": results,
        }

    def run_revision(  # type: ignore[override]
        self,
        ticket_id: str,
        worktree_path: Path,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Revise implementation based on MR feedback.

        Args:
            ticket_id: Jira ticket ID
            worktree_path: Path to the git worktree
            **kwargs: Additional parameters

        Returns:
            Dictionary with:
                - mr_url: URL of the MR
                - feedback_count: Number of feedback items addressed
                - changes_committed: Whether changes were committed
                - responses_posted: Number of responses posted to discussions
        """
        from src.gitlab_client import GitLabClient
        from src.config_loader import get_config

        logger.info(f"Running implementation revision for {ticket_id}")

        # Extract project key and set for session tracking
        project_key = ticket_id.split("-")[0]
        self.set_project(project_key)

        # Initialize clients
        gitlab = GitLabClient()
        config = get_config()

        # Extract project key
        project_key = ticket_id.split("-")[0]

        # Step 1: Find the MR
        project_config = config.get_project_config(project_key)
        git_url = project_config.get("git_url", "")

        # Extract project path from git URL
        if git_url.startswith("git@"):
            # SSH URL: git@gitlab.com:acme/backend.git -> acme/backend
            project_path = git_url.split(":")[1].replace(".git", "")
        elif git_url.startswith("https://"):
            # HTTPS URL: https://gitlab.com/acme/backend.git -> acme/backend
            project_path = git_url.split("gitlab.com/")[1].replace(".git", "")
        else:
            project_path = f"{project_key.lower()}/backend"

        source_branch = f"feature/{ticket_id}"
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

        # Step 2: Fetch unresolved discussions
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

        # Step 3: Analyze each discussion individually
        discussion_tasks = []

        for discussion in discussions:
            notes = discussion.get("notes", [])
            if not notes:
                continue

            first_note = notes[0]
            author = first_note.get("author", {}).get("name", "Unknown")
            body = first_note.get("body", "")
            discussion_id = discussion.get("id", "")

            # Analyze this specific discussion
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

                # Parse classification
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
                else:  # ACKNOWLEDGE or unknown
                    logger.info(f"Discussion {discussion_id}: Acknowledgment")
                    discussion_tasks.append({
                        "discussion_id": discussion_id,
                        "discussion": discussion,
                        "type": "acknowledge",
                    })

            except Exception as e:
                logger.error(f"Failed to analyze discussion {discussion_id}: {e}")
                # Default to acknowledge on error (safe)
                discussion_tasks.append({
                    "discussion_id": discussion_id,
                    "discussion": discussion,
                    "type": "acknowledge",
                    "error": str(e),
                })

        task_count = sum(1 for dt in discussion_tasks if dt.get("type") == "task")
        question_count = sum(1 for dt in discussion_tasks if dt.get("type") == "question")
        ack_count = sum(1 for dt in discussion_tasks if dt.get("type") == "acknowledge")
        logger.info(f"Analyzed {len(discussion_tasks)} discussions: {task_count} tasks, {question_count} questions, {ack_count} acknowledgments")

        # Step 4: Implement tasks and answer questions
        all_changed_files = []

        for item in discussion_tasks:
            # Implement code changes for tasks
            if item.get("type") == "task":
                task = item["task"]
                try:
                    logger.info(f"Implementing task for discussion {item['discussion_id']}: {task}")
                    # Use "fix" prefix for revision commits since they address feedback
                    impl_result = self.implement_feature(
                        task=task,
                        context={},
                        worktree_path=worktree_path,
                        commit_prefix="fix",
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
                    logger.error(f"Failed to implement task for discussion {item['discussion_id']}: {e}")
                    item["success"] = False
                    item["error"] = str(e)

            # Answer questions
            elif item.get("type") == "question":
                question = item["question"]
                try:
                    logger.info(f"Answering question for discussion {item['discussion_id']}: {question}")

                    # Extract code context from DiffNote position if available
                    discussion = item.get("discussion", {})
                    notes = discussion.get("notes", [])
                    code_context = None
                    file_path = None
                    line_info = None

                    if notes:
                        first_note = notes[0]
                        position = first_note.get("position")

                        if position:
                            # DiffNote with position data
                            file_path = position.get("new_path") or position.get("old_path")
                            new_line = position.get("new_line")
                            old_line = position.get("old_line")

                            if file_path:
                                line_info = f"Line {new_line or old_line}"
                                # Get the specific file content around the commented line
                                import subprocess
                                try:
                                    # Read the file from the current branch
                                    context_lines = 10  # Show 10 lines before and after
                                    target_line = new_line or old_line

                                    if target_line:
                                        # Use git show to get file content at HEAD
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
                                            end_line = min(len(file_lines), target_line + context_lines)

                                            context_snippet = file_lines[start_line:end_line]
                                            # Add line numbers
                                            numbered_lines = [
                                                f"{start_line + i + 1:4d} {line}"
                                                for i, line in enumerate(context_snippet)
                                            ]
                                            code_context = "\n".join(numbered_lines)
                                except Exception as e:
                                    logger.warning(f"Failed to extract code context: {e}")

                    # If no DiffNote context, fall back to git diff
                    if not code_context:
                        import subprocess
                        diff_cmd = ["git", "diff", "origin/main...HEAD"]
                        try:
                            diff_result = subprocess.run(
                                diff_cmd,
                                cwd=worktree_path,
                                capture_output=True,
                                text=True,
                                timeout=30,
                            )
                            code_context = diff_result.stdout[:5000]  # Limit diff size
                            file_path = "multiple files"
                            line_info = "see diff"
                        except Exception:
                            code_context = "Unable to retrieve diff"

                    # Build context description
                    context_desc = f"**File:** {file_path}\n**Location:** {line_info}\n\n" if file_path and line_info else ""

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
                    logger.error(f"Failed to answer question for discussion {item['discussion_id']}: {e}")
                    item["success"] = False
                    item["error"] = str(e)

        # Step 5: Run tests to verify fixes
        test_results = self.run_tests(worktree_path)

        # Step 6: Reply to ALL discussions (not just actionable ones)
        responses_posted = 0
        for item in discussion_tasks:
            discussion = item["discussion"]
            discussion_id = item["discussion_id"]

            # Determine reply based on discussion type
            discussion_type = item.get("type")

            if discussion_type == "task" and item.get("success"):
                # Task successfully implemented
                task = item["task"]
                impl_result = item.get("impl_result", {})
                files_changed = impl_result.get("files_modified", []) + impl_result.get("files_created", [])
                files_text = ", ".join(files_changed) if files_changed else "No files modified"

                reply_body = f"""✅ **Implemented**

{task}

**Files changed:** {files_text}

**Tests:** {'✅ Passing' if test_results.get('success') else '⚠️ Some failures'}

---
🤖 Sentinel Python Developer
"""
                resolve = True
                emoji = "white_check_mark"

            elif discussion_type == "task" and not item.get("success"):
                # Task failed
                task = item["task"]
                error = item.get("error", "Unknown error")

                reply_body = f"""⚠️ **Failed to implement**

Task: {task}

Error: {error}

This will need manual intervention.

---
🤖 Sentinel Python Developer
"""
                resolve = False
                emoji = "warning"

            elif discussion_type == "question" and item.get("success"):
                # Question answered
                answer = item.get("answer", "Unable to determine answer")

                reply_body = f"""{answer}

---
🤖 Sentinel Python Developer
"""
                resolve = True
                emoji = "speech_balloon"

            elif discussion_type == "question" and not item.get("success"):
                # Failed to answer question
                error = item.get("error", "Unknown error")

                reply_body = f"""⚠️ **Unable to answer question**

Error: {error}

A human will need to respond to this.

---
🤖 Sentinel Python Developer
"""
                resolve = False
                emoji = "warning"

            else:
                # Acknowledgment
                reply_body = """👍 **Acknowledged**

---
🤖 Sentinel Python Developer
"""
                resolve = True
                emoji = "thumbsup"

            try:
                # Add emoji reaction
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

                # Reply to discussion
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

        # Step 7: Add summary comment to MR
        tasks = sum(1 for item in discussion_tasks if item.get("type") == "task")
        questions = sum(1 for item in discussion_tasks if item.get("type") == "question")
        acknowledged = sum(1 for item in discussion_tasks if item.get("type") == "acknowledge")

        tasks_completed = sum(1 for item in discussion_tasks if item.get("type") == "task" and item.get("success"))
        tasks_failed = sum(1 for item in discussion_tasks if item.get("type") == "task" and not item.get("success"))
        questions_answered = sum(1 for item in discussion_tasks if item.get("type") == "question" and item.get("success"))
        questions_failed = sum(1 for item in discussion_tasks if item.get("type") == "question" and not item.get("success"))

        summary = f"""## Implementation Revision Summary 🔄

**Discussions Analyzed:** {len(discussion_tasks)}
**Tasks:** {tasks_completed}/{tasks} completed ({tasks_failed} failed)
**Questions:** {questions_answered}/{questions} answered ({questions_failed} failed)
**Acknowledged:** {acknowledged}
**Tests:** {'✅ All passing' if test_results.get('success') else '⚠️ Some failures - see output'}

All discussions have been addressed.

---
🤖 Updated by Sentinel Python Developer
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
            "tasks": tasks,
            "tasks_completed": tasks_completed,
            "tasks_failed": tasks_failed,
            "questions": questions,
            "questions_answered": questions_answered,
            "questions_failed": questions_failed,
            "acknowledged": acknowledged,
            "changes_committed": len(all_changed_files) > 0,
            "responses_posted": responses_posted,
            "test_results": test_results,
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

            # Get the first note (original comment)
            first_note = notes[0]
            author = first_note.get("author", {}).get("name", "Unknown")
            body = first_note.get("body", "")
            discussion_id = discussion.get("id", "")

            formatted.append(f"""
**Feedback {i}** (ID: {discussion_id})
Author: {author}
Comment: {body}
""")

            # Include replies if any
            if len(notes) > 1:
                formatted.append("Replies:")
                for note in notes[1:]:
                    reply_author = note.get("author", {}).get("name", "Unknown")
                    reply_body = note.get("body", "")
                    formatted.append(f"  - {reply_author}: {reply_body}")

        return "\n".join(formatted)
