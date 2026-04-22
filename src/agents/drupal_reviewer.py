"""Drupal Reviewer Agent - LLM-based Drupal code review with VETO power."""

import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents.base_agent import ReviewAgent


logger = logging.getLogger(__name__)

DRUPAL_EXTENSIONS = {
    ".php", ".module", ".inc", ".install", ".theme", ".profile",
    ".engine", ".test",
    ".yml", ".yaml",
    ".twig",
    ".js", ".css", ".scss",
    ".info", ".libraries",
}

SKIP_DIRS = {".venv", "venv", "node_modules", "vendor", "__pycache__", ".git"}


class DrupalReviewerAgent(ReviewAgent):
    """Agent that reviews Drupal merge requests against 11 review dimensions.

    Uses an LLM to evaluate code changes for correctness, DI compliance,
    cache metadata, security, config management, performance, testing,
    coding standards, accessibility, documentation, and Drupal idiomatic
    correctness. Has VETO power — can block progress on BLOCKER/MAJOR findings.
    """

    def __init__(self) -> None:
        """Initialize Drupal reviewer agent."""
        super().__init__(
            agent_name="drupal_reviewer",
            model="claude-4-5-sonnet",
            temperature=0.1,
            veto_power=True,
        )
        self._load_stack_overlay()
        self._inject_environment_context()

    def _load_stack_overlay(self) -> None:
        """Append Drupal reviewer overlay to system prompt."""
        overlays_dir = Path(__file__).parent.parent.parent / "prompts" / "overlays"
        overlay_path = overlays_dir / "drupal_reviewer.md"
        if overlay_path.exists():
            try:
                content = overlay_path.read_text()
                self.system_prompt += "\n\n" + content
                logger.info(f"Loaded Drupal reviewer overlay ({len(content)} chars)")
            except OSError as e:
                logger.warning(f"Failed to read Drupal reviewer overlay: {e}")

    def _inject_environment_context(self) -> None:
        """Replace {{ key }} placeholders in system prompt with config values."""
        env = self.config.get("agents.drupal_reviewer.environment", {})
        if not env or not isinstance(env, dict):
            return
        def replace_placeholder(match: re.Match) -> str:
            key = match.group(1).strip()
            return str(env.get(key, "Not specified"))
        self.system_prompt = re.sub(
            r"\{\{\s*(\w+)\s*\}\}", replace_placeholder, self.system_prompt
        )

    def _get_changed_files(
        self, worktree_path: Path, default_branch: str = "main"
    ) -> Optional[List[Path]]:
        """Get list of files changed on the feature branch.

        Uses git merge-base to find the branch point and git diff to get
        only files that were added, copied, modified, or renamed.

        Args:
            worktree_path: Path to the git worktree
            default_branch: Default branch name to diff against

        Returns:
            List of changed file paths, or None if git operations fail
        """
        try:
            merge_base = None
            for ref in [f"origin/{default_branch}", default_branch]:
                result = subprocess.run(
                    ["git", "merge-base", "HEAD", ref],
                    cwd=worktree_path,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    merge_base = result.stdout.strip()
                    break

            if not merge_base:
                logger.warning(
                    f"Could not find merge-base with {default_branch}, "
                    "falling back to full scan"
                )
                return None

            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=ACMR",
                 f"{merge_base}..HEAD"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                logger.warning(f"git diff failed: {result.stderr}")
                return None

            files = []
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                file_path = worktree_path / line
                if file_path.exists() and file_path.suffix in DRUPAL_EXTENSIONS:
                    files.append(file_path)

            logger.info(
                f"Git diff found {len(files)} changed Drupal files "
                f"(base: {merge_base[:8]}..HEAD)"
            )
            return files

        except FileNotFoundError:
            logger.warning("git not found, falling back to full scan")
            return None
        except Exception as e:
            logger.warning(f"Error getting changed files: {e}")
            return None

    def _get_diff_content(
        self, worktree_path: Path, default_branch: str = "main"
    ) -> str:
        """Get the unified diff content for the feature branch.

        Args:
            worktree_path: Path to the git worktree
            default_branch: Default branch name to diff against

        Returns:
            Unified diff string, or empty string on failure
        """
        try:
            merge_base = None
            for ref in [f"origin/{default_branch}", default_branch]:
                result = subprocess.run(
                    ["git", "merge-base", "HEAD", ref],
                    cwd=worktree_path,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    merge_base = result.stdout.strip()
                    break

            if not merge_base:
                return ""

            result = subprocess.run(
                ["git", "diff", f"{merge_base}..HEAD"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )

            if result.returncode == 0:
                return result.stdout
            return ""

        except Exception as e:
            logger.warning(f"Error getting diff content: {e}")
            return ""

    def _read_changed_file_contents(
        self, files: List[Path], max_total_chars: int = 200_000
    ) -> str:
        """Read contents of changed files for LLM review.

        Args:
            files: List of file paths to read
            max_total_chars: Maximum total characters to include

        Returns:
            Concatenated file contents with path headers
        """
        parts = []
        total = 0

        for file_path in files:
            if any(skip in file_path.parts for skip in SKIP_DIRS):
                continue
            try:
                content = file_path.read_text()
                header = f"\n--- {file_path} ---\n"
                if total + len(header) + len(content) > max_total_chars:
                    parts.append(f"\n--- {file_path} --- (truncated, file too large)\n")
                    break
                parts.append(header + content)
                total += len(header) + len(content)
            except Exception as e:
                logger.warning(f"Could not read {file_path}: {e}")

        return "".join(parts)

    def _build_review_prompt(
        self,
        diff_content: str,
        file_contents: str,
        ticket_description: str = "",
    ) -> str:
        """Build the review prompt for the LLM.

        Args:
            diff_content: Unified diff of the changes
            file_contents: Full contents of changed files
            ticket_description: Optional ticket/MR description

        Returns:
            Complete review prompt string
        """
        prompt = "Review this Drupal merge request.\n\n"

        if ticket_description:
            prompt += f"## MR Description\n{ticket_description}\n\n"

        prompt += f"## Diff\n```diff\n{diff_content}\n```\n\n"

        if file_contents:
            prompt += f"## Changed File Contents\n{file_contents}\n\n"

        prompt += (
            "Follow your review workflow exactly. Evaluate all 11 review dimensions. "
            "Produce the full output format including the Handover JSON in Section 8. "
            "The JSON must be valid and parseable."
        )

        return prompt

    def _parse_review_response(self, response: str) -> Dict[str, Any]:
        """Parse the LLM review response to extract structured data.

        Extracts the handover JSON from Section 8, falling back to
        regex-based extraction if JSON parsing fails.

        Args:
            response: Raw LLM response string

        Returns:
            Parsed review data dictionary
        """
        json_match = re.search(
            r"```json\s*\n(.*?)\n\s*```", response, re.DOTALL
        )

        if json_match:
            try:
                handover = json.loads(json_match.group(1))
                return handover
            except json.JSONDecodeError:
                logger.warning("Failed to parse handover JSON, using fallback")

        return self._fallback_parse(response)

    def _fallback_parse(self, response: str) -> Dict[str, Any]:
        """Extract review data from response text when JSON parsing fails.

        Args:
            response: Raw LLM response string

        Returns:
            Best-effort parsed review data
        """
        verdict = "COMMENT_ONLY"
        verdict_match = re.search(
            r"(?:verdict|##\s*1\.\s*Verdict)\s*[:\n]*\s*`?(APPROVE|REQUEST_CHANGES|COMMENT_ONLY)`?",
            response,
            re.IGNORECASE,
        )
        if verdict_match:
            verdict = verdict_match.group(1).upper()

        blocker_count = len(re.findall(r"\bBLOCKER\b", response))
        major_count = len(re.findall(r"\bMAJOR\b", response))
        minor_count = len(re.findall(r"\bMINOR\b", response))
        nit_count = len(re.findall(r"\bNIT\b", response))
        question_count = len(re.findall(r"\bQUESTION\b", response))
        praise_count = len(re.findall(r"\bPRAISE\b", response))

        findings = []
        finding_pattern = re.compile(
            r"###\s*\[(BLOCKER|MAJOR|MINOR|NIT|QUESTION|PRAISE)\]\s*(.*?)(?=\n###|\n##|\Z)",
            re.DOTALL,
        )
        for match in finding_pattern.finditer(response):
            severity = match.group(1)
            body = match.group(2).strip()
            title_line = body.split("\n")[0].strip()

            file_match = re.search(r"\*\*File:\*\*\s*`?([^`\n]+)`?", body)
            file_path = file_match.group(1).strip() if file_match else ""

            line_num = 0
            if ":" in file_path:
                parts = file_path.rsplit(":", 1)
                if parts[1].isdigit():
                    file_path = parts[0]
                    line_num = int(parts[1])

            findings.append({
                "id": f"F-{len(findings) + 1:03d}",
                "severity": severity,
                "file": file_path,
                "line": line_num,
                "title": title_line,
                "blocking": severity in ("BLOCKER", "MAJOR"),
            })

        return {
            "verdict": verdict,
            "reviewer": "DrupalSentinel",
            "target_agent": "drupal_developer",
            "metrics": {
                "blockers": blocker_count,
                "majors": major_count,
                "minors": minor_count,
                "nits": nit_count,
                "questions": question_count,
                "praise": praise_count,
            },
            "findings": findings,
            "raw_response": response,
        }

    def provide_feedback(self, findings: List[Dict[str, Any]]) -> List[str]:
        """Provide actionable feedback grouped by severity.

        Args:
            findings: List of finding dictionaries

        Returns:
            List of feedback strings
        """
        feedback = []

        blockers = [f for f in findings if f.get("severity") == "BLOCKER"]
        majors = [f for f in findings if f.get("severity") == "MAJOR"]
        minors = [f for f in findings if f.get("severity") == "MINOR"]

        if blockers:
            feedback.append(
                f"BLOCKER: {len(blockers)} blocking issues must be fixed:"
            )
            for finding in blockers:
                loc = finding.get("file", "unknown")
                if finding.get("line"):
                    loc += f":{finding['line']}"
                feedback.append(
                    f"  - [{finding.get('id', '?')}] {finding.get('title', '')} "
                    f"({loc})"
                )

        if majors:
            feedback.append(
                f"MAJOR: {len(majors)} major issues should be fixed:"
            )
            for finding in majors:
                loc = finding.get("file", "unknown")
                if finding.get("line"):
                    loc += f":{finding['line']}"
                feedback.append(
                    f"  - [{finding.get('id', '?')}] {finding.get('title', '')} "
                    f"({loc})"
                )

        if minors:
            feedback.append(
                f"MINOR: {len(minors)} minor suggestions (non-blocking)"
            )

        return feedback

    def approve_or_veto(self, review_data: Dict[str, Any]) -> bool:
        """Decide whether to approve or veto based on review data.

        Any BLOCKER or MAJOR finding triggers a veto.
        COMMENT_ONLY verdict is treated as non-blocking (returns True).

        Args:
            review_data: Parsed review data from _parse_review_response

        Returns:
            True if approved, False if vetoed
        """
        verdict = review_data.get("verdict", "COMMENT_ONLY")

        if verdict == "COMMENT_ONLY":
            logger.info("Drupal review COMMENT_ONLY — non-blocking pass")
            return True

        metrics = review_data.get("metrics", {})
        blocker_count = metrics.get("blockers", 0)
        major_count = metrics.get("majors", 0)

        if blocker_count > 0:
            logger.warning(f"VETO: {blocker_count} BLOCKER findings")
            return False

        if major_count > 0:
            logger.warning(f"VETO: {major_count} MAJOR findings")
            return False

        logger.info("Drupal review APPROVED: No blocking issues found")
        return True

    def run(  # type: ignore[override]
        self,
        worktree_path: Path,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run the complete Drupal review workflow.

        Args:
            worktree_path: Path to git worktree
            **kwargs: Additional parameters (ticket_id, ticket_description, etc.)

        Returns:
            Dictionary with approved, findings, feedback, veto, review_data
        """
        logger.info(f"Running Drupal review for: {worktree_path}")

        ticket_id = kwargs.get("ticket_id", "")
        ticket_description = kwargs.get("ticket_description", "")
        default_branch = "main"

        if ticket_id and "-" in ticket_id:
            project_key = ticket_id.split("-")[0]
            self.set_project(project_key)
            try:
                project_config = self.config.get_project_config(project_key)
                if project_config:
                    default_branch = project_config.get(
                        "default_branch", "main"
                    )
            except Exception:
                pass

        changed_files = self._get_changed_files(worktree_path, default_branch)
        if changed_files is not None and not changed_files:
            logger.info("No changed Drupal files — skipping review")
            return {
                "approved": True,
                "findings": [],
                "feedback": ["No Drupal files changed — review skipped."],
                "veto": False,
                "review_data": {"verdict": "APPROVE", "metrics": {}},
            }

        diff_content = self._get_diff_content(worktree_path, default_branch)
        file_contents = ""
        if changed_files:
            file_contents = self._read_changed_file_contents(changed_files)

        prompt = self._build_review_prompt(
            diff_content, file_contents, ticket_description
        )

        try:
            llm_response = asyncio.run(
                self.agent_sdk.execute_with_tools(
                    prompt=prompt,
                    system_prompt=self.system_prompt,
                    cwd=str(worktree_path),
                )
            )
            response_text = llm_response.get("content", "")
        except Exception as e:
            logger.error(f"LLM review failed: {e}")
            return {
                "approved": True,
                "findings": [],
                "feedback": [f"Drupal review failed: {e}"],
                "veto": False,
                "review_data": {"verdict": "COMMENT_ONLY", "error": str(e)},
            }

        review_data = self._parse_review_response(response_text)
        findings = review_data.get("findings", [])
        feedback = self.provide_feedback(findings)
        approved = self.approve_or_veto(review_data)

        return {
            "approved": approved,
            "findings": findings,
            "feedback": feedback,
            "veto": not approved,
            "review_data": review_data,
        }
