"""Plan Generator Agent - Analyzes tickets and creates implementation plans."""

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from src.agents.base_agent import PlanningAgent
from src.attachment_manager import AttachmentManager
from src.jira_factory import get_jira_client
from src.gitlab_client import GitLabClient
from src.config_loader import get_config
from src.stack_profiler import generate_profile_markdown
from src.worktree_manager import get_branch_name
from src.utils.adf_parser import parse_adf_to_text


logger = logging.getLogger(__name__)


class PlanGeneratorAgent(PlanningAgent):
    """Agent that analyzes Jira tickets and generates implementation plans.

    Uses Claude Opus 4.5 for strategic planning and analysis.
    """

    def __init__(self) -> None:
        """Initialize plan generator agent."""
        super().__init__(
            agent_name="plan_generator",
            model="claude-opus-4-5",
            temperature=0.3,
        )

        self.jira = get_jira_client()
        self.gitlab = GitLabClient()
        self.config = get_config()

    def analyze_ticket(
        self, ticket_id: str, worktree_path: Path | None = None
    ) -> Dict[str, Any]:
        """Analyze a Jira ticket and extract requirements.

        Args:
            ticket_id: Jira ticket ID (e.g., "ACME-123")
            worktree_path: Optional path to worktree for codebase exploration

        Returns:
            Dictionary with analyzed requirements:
                - ticket_data: Raw ticket data from Jira
                - requirements: Extracted requirements
                - technical_approach: Suggested approach
                - risks: Identified risks
        """
        logger.info(f"Analyzing ticket: {ticket_id}")

        # Fetch ticket data
        ticket_data = self.jira.get_ticket(ticket_id)

        # Fetch existing comments for re-entry context (e.g., PO answered triage questions)
        comments = self.jira.get_ticket_comments(ticket_id)
        if comments:
            logger.info(f"Found {len(comments)} existing comment(s) on {ticket_id}")

        # Download attachments if worktree is available
        attachment_mgr = AttachmentManager()
        attachments_data = None
        attachment_context = ""
        attachments_metadata = ticket_data.get("attachments", [])

        if worktree_path and attachments_metadata:
            attachments_data = attachment_mgr.download_attachments(
                self.jira.session, attachments_metadata, ticket_id, worktree_path,
                base_url=self.jira.base_url,
            )
            attachment_context = attachment_mgr.format_for_prompt(attachments_data)
            logger.info(
                f"Attachments: {len(attachments_data.get('text_attachments', []))} text, "
                f"{len(attachments_data.get('image_attachments', []))} images, "
                f"{len(attachments_data.get('skipped', []))} skipped"
            )
        elif attachments_metadata:
            attachment_context = attachment_mgr.format_metadata_only(attachments_metadata)

        # Extract and parse description
        description_raw = ticket_data.get("description", "")
        if isinstance(description_raw, dict):
            description = parse_adf_to_text(description_raw)
        else:
            description = str(description_raw)

        # Build analysis prompt
        # Handle both dict and string formats for issuetype and priority
        issuetype = ticket_data.get('issuetype', 'Unknown')
        issuetype_name = issuetype.get('name', 'Unknown') if isinstance(issuetype, dict) else str(issuetype)

        priority = ticket_data.get('priority', 'Medium')
        priority_name = priority.get('name', 'Medium') if isinstance(priority, dict) else str(priority)

        # Build comments context for re-entry
        comments_context = ""
        if comments:
            comments_text = "\n".join(
                f"- [{c['author']}]: {c['body']}" for c in comments
            )
            comments_context = f"\n**Existing Comments/Discussion**:\n{comments_text}\n\n"

        # Self-contained prompt for ticket analysis - returns JSON only, no tool use
        analysis_prompt = f"""Analyze this Jira ticket and return ONLY a JSON object.

**IMPORTANT**: Do NOT use any tools. Do NOT explore the codebase. Just analyze the ticket text and return JSON.

**Ticket Details**:
- **ID**: {ticket_id}
- **Summary**: {ticket_data.get('summary', 'N/A')}
- **Type**: {issuetype_name}
- **Priority**: {priority_name}

**Description**:
{description}
{attachment_context}
{comments_context}
**OUTPUT FORMAT** (return ONLY this JSON, no other text):
```json
{{
  "requirements": [
    "Requirement 1 extracted from ticket",
    "Requirement 2 extracted from ticket"
  ],
  "technical_approach": "Brief description of implementation approach",
  "risks": [
    "Risk 1",
    "Risk 2"
  ],
  "estimated_complexity": "low|medium|high",
  "rationale": "Brief explanation of complexity estimate"
}}
```

Return ONLY the JSON object. No markdown code blocks, no explanatory text, just the raw JSON.
"""

        # Call LLM to analyze ticket
        # NOTE: Do NOT pass cwd here - we don't want the agent to explore the codebase.
        # Ticket analysis should be purely based on the ticket text, not codebase exploration.
        # Codebase exploration happens in generate_plan().
        try:
            response = self.send_message(analysis_prompt, cwd=None)

            # Parse JSON response
            ai_analysis = self._extract_json_from_response(response)
            if ai_analysis is None:
                logger.warning("LLM did not return valid JSON")
                raise ValueError("LLM did not return valid JSON")

            # Build analysis result
            analysis = {
                "ticket_data": ticket_data,
                "requirements": ai_analysis.get("requirements", []),
                "technical_approach": ai_analysis.get("technical_approach", "TDD implementation"),
                "risks": ai_analysis.get("risks", []),
                "estimated_complexity": ai_analysis.get("estimated_complexity", "medium"),
                "attachments_data": attachments_data,
                "comments": comments,
            }

            logger.info(f"Ticket analysis complete: {len(analysis['requirements'])} requirements")

        except json.JSONDecodeError as e:
            error_msg = (
                f"Failed to analyze ticket {ticket_id} - LLM returned invalid JSON.\n\n"
                f"This indicates the LLM did not follow the structured output format.\n\n"
                f"LLM Response:\n{response[:500]}...\n\n"
                f"Expected JSON format:\n"
                f'{{\n'
                f'  "requirements": [...],\n'
                f'  "technical_approach": "...",\n'
                f'  "risks": [...],\n'
                f'  "estimated_complexity": "low|medium|high"\n'
                f'}}\n\n'
                f"Please check the system prompt at .agents/prompts/plan_generator.md"
            )
            logger.error(error_msg)
            raise ValueError(error_msg) from e

        except Exception as e:
            error_msg = (
                f"Failed to analyze ticket {ticket_id} - Unexpected error.\n\n"
                f"Error: {str(e)}\n\n"
                f"This may indicate:\n"
                f"  - Jira API connectivity issues\n"
                f"  - Invalid ticket ID format\n"
                f"  - Missing ticket data\n\n"
                f"Please verify the ticket exists and is accessible."
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e

        return analysis

    def _extract_json_from_response(self, response: str) -> Dict[str, Any] | None:
        """Extract JSON object from LLM response.

        Handles various formats:
        - Pure JSON response
        - JSON wrapped in markdown code blocks
        - JSON with surrounding text

        Args:
            response: Raw LLM response text

        Returns:
            Parsed JSON as dict, or None if extraction fails
        """
        # Try 1: Parse entire response as JSON
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Try 2: Look for JSON in code blocks
        code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response, re.DOTALL)
        if code_block_match:
            try:
                return json.loads(code_block_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try 3: Find first { and try to find matching } with proper brace counting
        first_brace = response.find('{')
        if first_brace != -1:
            depth = 0
            for i, char in enumerate(response[first_brace:]):
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        json_str = response[first_brace:first_brace + i + 1]
                        try:
                            return json.loads(json_str)
                        except json.JSONDecodeError:
                            pass
                        break

        return None

    def _extract_requirements(self, ticket_data: Dict[str, Any]) -> list[str]:
        """Extract requirements from ticket data.

        Args:
            ticket_data: Ticket data from Jira

        Returns:
            List of requirement strings

        Note:
            This is a simplified version. In production, this would use
            the LLM to intelligently extract and structure requirements.
        """
        requirements = []

        # Extract from summary
        summary = ticket_data.get("summary", "")
        if summary:
            requirements.append(f"Main goal: {summary}")

        # Extract from description
        description_raw = ticket_data.get("description", "")
        if description_raw:
            # Parse ADF format to plain text
            if isinstance(description_raw, dict):
                description = parse_adf_to_text(description_raw)
            else:
                description = str(description_raw)

            # TODO: Parse description with LLM to extract structured requirements
            # For now, include first 200 chars as a preview
            if len(description) > 200:
                requirements.append(f"Description: {description[:200]}...")
            else:
                requirements.append(f"Description: {description}")

        return requirements

    def _load_stack_context(self, ticket_id: str, worktree_path: Path) -> str:
        """Load stack-specific context for plan generation.

        Reads project-context.md and Drupal overlay prompts if the project
        has been profiled as a Drupal stack.

        Args:
            ticket_id: Jira ticket ID (used to extract project key)
            worktree_path: Path to the git worktree root

        Returns:
            Stack context string to inject into plan prompt, or empty string
        """
        project_key = ticket_id.split("-")[0]
        project_config = self.config.get_project_config(project_key)
        stack_type = project_config.get("stack_type", "")

        if not stack_type:
            return ""

        context_parts: list[str] = []

        # Load project context from worktree
        context_path = worktree_path / ".sentinel" / "project-context.md"
        if context_path.exists():
            try:
                content = context_path.read_text()
                context_parts.append(f"\n## Project Context\n\n{content}")
                logger.info(f"Loaded project context ({len(content)} chars)")
            except OSError as e:
                logger.warning(f"Failed to read project context: {e}")

        # Load stack-specific overlay prompts from Sentinel's built-in prompts
        if stack_type.startswith("drupal"):
            overlays_dir = Path(__file__).parent.parent.parent / "prompts" / "overlays"
            for overlay_name in ["drupal_plan_generator.md", "drupal_exploration.md"]:
                overlay_path = overlays_dir / overlay_name
                if overlay_path.exists():
                    try:
                        content = overlay_path.read_text()
                        context_parts.append(f"\n{content}")
                        logger.info(f"Loaded overlay: {overlay_name} ({len(content)} chars)")
                    except OSError as e:
                        logger.warning(f"Failed to read overlay {overlay_name}: {e}")

        return "\n".join(context_parts)

    def _auto_profile_if_needed(self, worktree_path: Path, project_key: str) -> None:
        """Auto-generate project profile if none exists.

        Args:
            worktree_path: Path to the git worktree
            project_key: Project key for config metadata
        """
        context_path = worktree_path / ".sentinel" / "project-context.md"
        if context_path.exists():
            content = context_path.read_text()
            if len(content) > 100:
                return
            logger.warning(f"Existing profile looks invalid ({len(content)} chars), regenerating")

        logger.info("No project profile found, auto-profiling...")

        # Save LLM env vars — the profiler creates a separate agent that may
        # reconfigure them (e.g., switching to subscription mode), which would
        # wipe the plan_generator's custom_proxy credentials.
        saved_env = {
            k: os.environ.get(k)
            for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
        }

        markdown, stack_type = generate_profile_markdown(worktree_path, project_key)

        # Restore LLM env vars so the plan_generator can continue authenticating
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

        if not stack_type:
            logger.info("Could not detect stack type, skipping auto-profile")
            return

        context_path.parent.mkdir(parents=True, exist_ok=True)
        context_path.write_text(markdown)
        logger.info(f"Project profile generated: {stack_type} ({len(markdown)} chars)")

        # Commit the profile to the worktree branch
        try:
            subprocess.run(
                ["git", "add", ".sentinel/project-context.md"],
                cwd=worktree_path,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", f"Add Sentinel project profile ({stack_type})"],
                cwd=worktree_path,
                check=True,
                capture_output=True,
            )
            logger.info("Committed project profile to worktree branch")
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode() if e.stderr else ""
            logger.warning(f"Could not commit project profile: {stderr}")

        # Update config metadata
        try:
            self.config.update_project_metadata(
                project_key,
                stack_type=stack_type,
                profiled_at=datetime.now(timezone.utc).isoformat(),
            )
            logger.info(f"Updated project metadata: stack_type={stack_type}")
        except ValueError as e:
            logger.warning(f"Could not update project metadata: {e}")

    def generate_plan(
        self,
        ticket_id: str,
        context: Dict[str, Any],
        output_path: Path,
        worktree_path: Path | None = None,
        investigation_findings: str | None = None,
    ) -> str:
        """Generate a detailed implementation plan.

        Args:
            ticket_id: Jira ticket ID
            context: Context from ticket analysis
            output_path: Path to write the plan file
            worktree_path: Path to the git worktree root
            investigation_findings: Pre-research findings from investigate_comments()

        Returns:
            Plan content as markdown string
        """
        logger.info(f"Generating plan for {ticket_id}")

        # Reset session to avoid context contamination from previous conversations
        # (e.g., ticket analysis may have different context than plan generation)
        self.session_id = None
        self.messages.clear()

        ticket_data = context.get("ticket_data", {})
        requirements = context.get("requirements", [])
        technical_approach = context.get("technical_approach", "TDD")
        risks = context.get("risks", [])
        complexity = context.get("estimated_complexity", "medium")
        attachments_data = context.get("attachments_data")

        # Build plan generation prompt
        requirements_text = chr(10).join(f"  - {req}" for req in requirements) if requirements else "  - No specific requirements provided"
        risks_text = chr(10).join(f"  - {risk}" for risk in risks) if risks else "  - No specific risks identified"

        # Handle both dict and string formats for issuetype and priority
        issuetype = ticket_data.get('issuetype', 'Task')
        issuetype_name = issuetype.get('name', 'Task') if isinstance(issuetype, dict) else str(issuetype)

        priority = ticket_data.get('priority', 'Medium')
        priority_name = priority.get('name', 'Medium') if isinstance(priority, dict) else str(priority)

        # Load stack-specific context if available
        effective_worktree = worktree_path or output_path.parent.parent.parent
        stack_context = self._load_stack_context(ticket_id, effective_worktree)

        # Build optional sections
        attachments_section = AttachmentManager().format_for_prompt(attachments_data) if attachments_data else ""

        findings_section = ""
        if investigation_findings:
            findings_section = (
                "## Pre-Research Findings\n\n"
                "The following findings were verified by searching the codebase based on client feedback.\n"
                "Use these as VERIFIED FACTS - do not re-investigate these items.\n\n"
                f"{investigation_findings}\n\n"
            )

        # System prompt defines the detailed format - user prompt tells agent to write the file
        plan_file_path = str(output_path)
        plan_prompt = f"""Generate a comprehensive implementation plan for ticket {ticket_id}.
{stack_context}

**Ticket Context**:
- **ID**: {ticket_id}
- **Summary**: {ticket_data.get('summary', 'N/A')}
- **Type**: {issuetype_name}
- **Priority**: {priority_name}

**Requirements Analysis**:
{requirements_text}

**Technical Approach**: {technical_approach}

**Identified Risks**:
{risks_text}

**Estimated Complexity**: {complexity}
{attachments_section}
{findings_section}## OUTPUT FILE PATH

Use the Write tool to save the implementation plan to: `{plan_file_path}`

## EXECUTION INSTRUCTIONS

1. Use your tools (Read, Grep, Glob) to explore the target codebase
2. Follow the workflow phases in your system prompt
3. Use the **Write tool** to save the COMPLETE plan to the file path above

## REQUIRED SECTIONS

The plan MUST contain these sections:
- # Feature: (title)
- ## Summary
- ## User Story
- ## Step-by-Step Tasks
- ## Testing Strategy
- ## Acceptance Criteria
- ## Risks and Mitigations

## CRITICAL: STOP AFTER WRITING THE PLAN

**DO NOT implement the plan.** Your ONLY job is to:
1. Explore the codebase to understand patterns
2. Write the plan document to the file path above
3. Say "Plan written to [path]" and STOP

**You are a PLANNER, not an IMPLEMENTER.** Do NOT:
- Create source code files
- Modify existing code
- Run tests or builds
- Make any changes beyond writing the plan file

After writing the plan file, respond with ONLY: "Plan written to {plan_file_path}"
"""

        # Call LLM to generate plan - agent will write the file directly
        # Pass the worktree as cwd so the LLM can explore the target codebase
        worktree_cwd = str(output_path.parent.parent.parent)  # .agents/plans/{id}.md -> worktree root
        logger.info(f"Sending plan generation request to LLM (cwd={worktree_cwd})")

        # Ensure the output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        max_iterations = 3
        plan_content = ""

        for iteration in range(max_iterations):
            try:
                response = self.send_message(plan_prompt, cwd=worktree_cwd, max_turns=30)
            except Exception as e:
                error_msg = (
                    f"Failed to generate plan for {ticket_id} - LLM request failed.\n"
                    f"Error: {str(e)}\n\n"
                    f"This likely indicates:\n"
                    f"  - API connectivity issues\n"
                    f"  - Invalid API credentials\n"
                    f"  - Model availability problems\n\n"
                    f"Check your configuration in sentinel_config.yaml and ensure the Agent SDK is properly set up."
                )
                logger.error(error_msg)
                raise RuntimeError(error_msg) from e

            logger.debug(f"LLM response ({len(response)} chars): {response[:500]}...")

            # Check if the agent wrote the file
            if not output_path.exists():
                logger.warning(f"Iteration {iteration + 1}: Agent did not write plan file to {output_path}")
                if iteration < max_iterations - 1:
                    # Provide feedback for next iteration
                    plan_prompt = f"""The plan file was NOT created. You MUST use the Write tool to save the plan.

Write the complete implementation plan to: `{plan_file_path}`

This is iteration {iteration + 2} of {max_iterations}. Please write the file now.
"""
                    continue
                else:
                    raise RuntimeError(
                        f"Failed to generate plan for {ticket_id} - Agent did not write the plan file "
                        f"after {max_iterations} attempts.\n\n"
                        f"Last LLM Response:\n{response[:500]}..."
                    )

            # Read the plan file
            plan_content = output_path.read_text()
            logger.info(f"Plan file written: {output_path} ({len(plan_content)} chars)")

            # Validate plan has required sections
            required_sections = {
                "Feature Header": ["# feature:", "# feature"],
                "Step-by-Step Tasks": ["step-by-step tasks", "## step-by-step tasks"],
                "Testing Strategy": ["testing strategy", "## testing strategy"],
                "Risks and Mitigations": ["risks and mitigations", "## risks and mitigations"],
            }

            plan_lower = plan_content.lower()
            missing_sections = []
            for section_name, variants in required_sections.items():
                if not any(variant in plan_lower for variant in variants):
                    missing_sections.append(section_name)

            if not missing_sections:
                # Validation passed
                logger.info(f"Plan validation passed on iteration {iteration + 1}")
                break

            logger.warning(f"Iteration {iteration + 1}: Plan missing sections: {missing_sections}")

            if iteration < max_iterations - 1:
                # Provide feedback for next iteration
                plan_prompt = f"""The plan file is missing required sections: {', '.join(missing_sections)}

Please UPDATE the plan at `{plan_file_path}` to include these sections.

This is iteration {iteration + 2} of {max_iterations}.
"""
            else:
                # Final iteration failed
                error_msg = (
                    f"Failed to generate valid plan for {ticket_id} after {max_iterations} iterations.\n\n"
                    f"Missing sections: {', '.join(missing_sections)}\n\n"
                    f"Plan Preview:\n{plan_content[:500]}..."
                )
                logger.error(error_msg)
                raise ValueError(error_msg)

        logger.info(f"AI-generated plan created successfully ({len(plan_content)} characters)")

        # Agent already wrote the file, no need to write again
        logger.info(f"Plan saved to {output_path}")

        return plan_content

    def revise_plan(
        self,
        ticket_id: str,
        current_plan: str,
        feedback: list[Dict[str, Any]],
        output_path: Path,
    ) -> Dict[str, Any]:
        """Revise an existing plan based on MR feedback.

        Args:
            ticket_id: Jira ticket ID
            current_plan: Current plan content
            feedback: List of unresolved discussions from MR
            output_path: Path where revised plan should be saved

        Returns:
            Dictionary with:
                - revised_plan: Updated plan content
                - revision_summary: Summary of changes made
                - feedback_responses: Responses to each feedback item
                - revision_type: "incremental" or "full_rewrite"
        """
        logger.info(f"Revising plan for {ticket_id} based on {len(feedback)} feedback items")

        # Format feedback for LLM
        feedback_text = self._format_feedback(feedback)

        # Build revision prompt
        revision_prompt = f"""You are revising an implementation plan based on team feedback.

**Current Plan:**
{current_plan}

**Feedback from Team (Unresolved Discussions):**
{feedback_text}

**Your Task:**
1. **Analyze the feedback** - Understand what changes are being requested
2. **Decide revision approach:**
   - If feedback is minor/clarifications → Make **incremental updates** to specific sections
   - If feedback fundamentally challenges the approach → Perform **full rewrite** with new strategy
3. **Revise the plan** - Update the plan to address all feedback
4. **Document changes** - For each feedback item, explain what you changed

**Output Format:**
Return a JSON object with:
{{
    "revision_type": "incremental" or "full_rewrite",
    "rationale": "Why you chose this revision approach",
    "revised_plan": "The complete updated plan in markdown",
    "feedback_responses": [
        {{
            "discussion_id": "...",
            "feedback_summary": "What the reviewer said",
            "changes_made": "What you changed in the plan to address this",
            "section_affected": "Which section(s) of the plan were updated"
        }}
    ]
}}

Be specific about what changed and why. The team needs to understand how their feedback was incorporated.
"""

        # Call LLM
        response = self.send_message(revision_prompt)

        # Parse response
        import json
        import re

        result_dict: Dict[str, Any]
        try:
            result_dict = json.loads(response)
        except json.JSONDecodeError:
            # If LLM didn't return valid JSON, try to extract it
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                result_dict = json.loads(json_match.group())
            else:
                raise ValueError("LLM did not return valid JSON response")

        # Write revised plan to file
        revised_plan = str(result_dict["revised_plan"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(revised_plan)

        logger.info(f"Revised plan written to {output_path} ({result_dict.get('revision_type', 'incremental')} revision)")

        return result_dict

    def _format_feedback(self, feedback: list[Dict[str, Any]]) -> str:
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

    def commit_and_push_plan(
        self,
        plan_path: Path,
        ticket_id: str,
        worktree_path: Path,
    ) -> bool:
        """Commit and push the plan file to the remote repository.

        Args:
            plan_path: Path to the plan file
            ticket_id: Jira ticket ID
            worktree_path: Path to the git worktree

        Returns:
            True if changes were committed and pushed, False if no changes

        Raises:
            subprocess.CalledProcessError: If git operations fail
        """
        logger.info(f"Committing and pushing plan for {ticket_id}")

        try:
            # Add the plan file to git
            subprocess.run(
                ["git", "add", str(plan_path.relative_to(worktree_path))],
                cwd=worktree_path,
                check=True,
                capture_output=True,
            )

            # Check if there are staged changes
            diff_result = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=worktree_path,
                capture_output=True,
            )

            # Exit code 0 means no changes, 1 means changes exist
            if diff_result.returncode == 0:
                logger.info("No changes to commit - plan file unchanged")
                return False

            # Commit the plan
            commit_message = f"Add implementation plan for {ticket_id}"
            subprocess.run(
                ["git", "commit", "-m", commit_message],
                cwd=worktree_path,
                check=True,
                capture_output=True,
            )

            # Push to remote
            branch_name = get_branch_name(ticket_id)
            subprocess.run(
                ["git", "push", "-u", "origin", branch_name],
                cwd=worktree_path,
                check=True,
                capture_output=True,
            )

            logger.info(f"Plan committed and pushed to {branch_name}")
            return True

        except subprocess.CalledProcessError as e:
            logger.error(f"Git operation failed: {e.stderr.decode() if e.stderr else str(e)}")
            raise

    def create_draft_mr(
        self,
        ticket_id: str,
        plan_path: Path,
        project_key: str,
    ) -> str:
        """Create a draft merge request with the plan.

        Args:
            ticket_id: Jira ticket ID
            plan_path: Path to the plan file
            project_key: Project key (e.g., "ACME")

        Returns:
            MR URL
        """
        logger.info(f"Creating draft MR for {ticket_id}")

        # Get project config
        project_config = self.config.get_project_config(project_key)
        git_url = project_config.get("git_url", "")

        # Extract project path from git URL
        project_path = self.gitlab.extract_project_path(git_url)

        # Read plan content
        plan_content = plan_path.read_text()

        # Get ticket data for MR description
        ticket_data = self.jira.get_ticket(ticket_id)

        # Create MR description
        mr_description = f"""## Ticket
**{ticket_id}**: {ticket_data.get('summary', 'N/A')}

[View in Jira]({self.jira.base_url}/browse/{ticket_id})

## Implementation Plan
The detailed plan has been committed to the repository at `{plan_path}`.

### Plan Summary
{plan_content[:500]}...

---
🤖 This draft MR was created by Sentinel
"""

        # Create draft MR
        source_branch = get_branch_name(ticket_id)
        target_branch = project_config.get("default_branch", "main")

        mr_data = self.gitlab.create_merge_request(
            project_id=project_path,
            title=f"{ticket_id}: {ticket_data.get('summary', 'Implementation')}",
            source_branch=source_branch,
            target_branch=target_branch,
            description=mr_description,
            draft=True,
        )

        mr_url = mr_data["web_url"]

        # Add comment to Jira ticket with link
        self.jira.add_comment(
            ticket_id,
            "Draft implementation plan ready: ",
            link_text="View Merge Request",
            link_url=mr_url,
        )

        logger.info(f"Draft MR created: {mr_url}")

        result: str = str(mr_url)
        return result

    def create_or_get_mr(
        self,
        ticket_id: str,
        plan_path: Path,
        project_key: str,
    ) -> tuple[str, bool]:
        """Create a draft MR or get existing one if it already exists.

        Args:
            ticket_id: Jira ticket ID
            plan_path: Path to the plan file
            project_key: Project key (e.g., "ACME")

        Returns:
            Tuple of (MR URL, was_created)
            - MR URL: URL of the merge request
            - was_created: True if a new MR was created, False if existing
        """
        source_branch = get_branch_name(ticket_id)

        # Get project config
        project_config = self.config.get_project_config(project_key)
        git_url = project_config.get("git_url", "")

        # Extract project path from git URL
        project_path = self.gitlab.extract_project_path(git_url)

        try:
            # Try to create a new MR
            mr_url = self.create_draft_mr(ticket_id, plan_path, project_key)
            return mr_url, True

        except Exception as e:
            # If MR already exists, try to find it
            error_msg = str(e).lower()
            if "already exists" in error_msg or "conflict" in error_msg:
                logger.info(f"MR already exists for {source_branch}, fetching existing MR")

                try:
                    # Get existing MRs for this branch
                    mrs = self.gitlab.list_merge_requests(
                        project_id=project_path,
                        source_branch=source_branch,
                    )

                    if mrs:
                        mr_url = mrs[0]["web_url"]
                        logger.info(f"Found existing MR: {mr_url}")
                        return mr_url, False
                    else:
                        # MR was mentioned in error but not found - unexpected
                        logger.error(f"MR conflict reported but no MR found for {source_branch}")
                        raise

                except Exception as inner_e:
                    logger.error(f"Failed to fetch existing MR: {inner_e}")
                    raise
            else:
                # Some other error, re-raise
                raise

    def investigate_comments(
        self,
        ticket_id: str,
        new_comments: list[Dict[str, Any]],
        existing_plan: str,
        worktree_path: Path,
    ) -> str:
        """Investigate actionable claims from Jira comments by searching the codebase.

        Called when the client has replied to Sentinel's confidence report with
        new context. Uses Read/Grep/Glob to verify claims and locate referenced
        code before plan generation.

        Args:
            ticket_id: Jira ticket ID
            new_comments: New comment dicts with 'author', 'created', 'body'
            existing_plan: Current plan content (for context)
            worktree_path: Path to the git worktree (enables tool access)

        Returns:
            Investigation report as markdown
        """
        logger.info(f"Investigating {len(new_comments)} new comment(s) for {ticket_id}")

        # Fresh session to avoid context contamination
        self.session_id = None
        self.messages.clear()

        comments_text = "\n".join(
            f"### Comment by {c['author']} ({c.get('created', 'unknown')})\n{c['body']}\n"
            for c in new_comments
        )

        investigation_prompt = f"""You are investigating new client feedback for ticket {ticket_id}.

The client replied to Sentinel's confidence report with new information. Your job is to
SEARCH THE CODEBASE to verify and locate anything the client mentions, then report your findings.

## New Comments from Client

{comments_text}

## Current Plan (for context only — do NOT rewrite it)

{existing_plan}

## Your Task

1. **Extract actionable claims** from the comments above. These are statements like:
   - "There's already a [thing] in the project"
   - "Look at [file/module/pattern]"
   - "We use [library/approach] for this"
   - Answers to specific questions from the confidence report

2. **For each claim, search the codebase** using your tools:
   - Use Grep to search for keywords, function names, class names mentioned
   - Use Glob to find files matching described patterns
   - Use Read to examine relevant files you find

3. **Write your findings** in this exact format:

## Investigation Report

### Claims Investigated

#### Claim 1: [paraphrase of what client said]
- **Search performed**: [what you searched for]
- **Found**: [what you actually found, with file paths and line numbers]
- **Relevance to plan**: [how this affects the implementation approach]

[repeat for each actionable claim]

### Summary of Findings
[2-3 sentences summarizing what was found and how it should influence the plan]

### Items NOT Found
[Any claims that could not be verified in the codebase, or "None" if all verified]

## RULES
- Do NOT rewrite or generate a plan. You are ONLY investigating.
- Do NOT explore the entire codebase. Only search for things mentioned in comments.
- Keep your investigation focused: max 2-3 searches per claim.
- Include exact file paths and line numbers for everything you find.
- If a comment is just acknowledgment ("thanks", "ok") with no actionable content, note it and move on.
"""

        response = self.send_message(
            investigation_prompt,
            cwd=str(worktree_path),
            max_turns=15,
        )

        # Extract the report section if present
        if "## Investigation Report" in response:
            report = response[response.index("## Investigation Report"):]
        else:
            report = response

        logger.info(f"Investigation complete for {ticket_id}: {len(report)} chars")
        return report

    def _post_investigation_report(
        self,
        ticket_id: str,
        investigation_report: str,
    ) -> None:
        """Post investigation findings to Jira so the client sees the research.

        Args:
            ticket_id: Jira ticket ID
            investigation_report: Markdown investigation findings
        """
        # Convert markdown to Jira wiki markup
        body = investigation_report
        if body.startswith("## Investigation Report"):
            body = body[len("## Investigation Report"):].strip()

        body = re.sub(r'^#### (.+)$', r'h4. \1', body, flags=re.MULTILINE)
        body = re.sub(r'^### (.+)$', r'h3. \1', body, flags=re.MULTILINE)
        body = re.sub(r'\*\*(.+?)\*\*', r'*\1*', body)

        lines: list[str] = [
            "h2. \U0001f50d Sentinel Investigation Report",
            "",
            "_Sentinel investigated the codebase based on your feedback:_",
            "",
            body,
            "",
            f"_This research will be incorporated into the updated plan._",
        ]
        comment_body = "\n".join(lines)

        try:
            logger.info(f"Posting investigation report to Jira {ticket_id}")
            self.jira.add_comment(ticket_id, comment_body)
            logger.info(f"Posted investigation report to Jira {ticket_id}")
        except Exception as e:
            logger.error(f"Failed to post investigation report: {e}", exc_info=True)

    def _detect_plan_state(
        self,
        ticket_id: str,
        worktree_path: Path,
        project_key: str,
    ) -> Dict[str, Any]:
        """Detect the current state of a plan for a ticket.

        Checks plan file, MR, discussions, and Jira comments to determine
        what action to take.

        Args:
            ticket_id: Jira ticket ID
            worktree_path: Path to the git worktree
            project_key: Project key (e.g., "ACME")

        Returns:
            Dictionary with:
                - state: "initial", "has_feedback", "update", or "nothing_changed"
                - existing_plan: Plan content if plan file exists
                - mr_iid: MR IID if MR exists
                - mr_url: MR URL if MR exists
                - project_path: GitLab project path
                - discussions: Unresolved discussions if any
        """
        plan_path = worktree_path / ".agents" / "plans" / f"{ticket_id}.md"

        # Check if plan file exists
        if not plan_path.exists():
            return {"state": "initial"}

        existing_plan = plan_path.read_text()

        # Check if MR exists
        mr_info = self._get_mr_info(ticket_id, project_key)
        if not mr_info:
            # Plan exists but no MR — treat as fresh (previous failed run)
            return {"state": "initial", "existing_plan": existing_plan}

        project_path = mr_info["project_path"]
        mr_iid = mr_info["mr_iid"]
        mr_url = mr_info["mr_url"]

        # Check for unresolved MR discussions
        discussions = self.gitlab.get_merge_request_discussions(
            project_id=project_path,
            mr_iid=mr_iid,
            unresolved_only=True,
        )

        if discussions:
            return {
                "state": "has_feedback",
                "existing_plan": existing_plan,
                "mr_iid": mr_iid,
                "mr_url": mr_url,
                "project_path": project_path,
                "discussions": discussions,
            }

        # No discussions — check Jira for new context
        comments = self.jira.get_ticket_comments(ticket_id)

        # Find the last Sentinel-authored comment (confidence or investigation report)
        last_sentinel_idx = -1
        for i, comment in enumerate(comments):
            if self._is_sentinel_comment(comment.get("body", "")):
                last_sentinel_idx = i

        # Collect non-Sentinel comments posted after the last report
        comment_pool = comments if last_sentinel_idx == -1 else comments[last_sentinel_idx + 1:]
        new_comments = [
            c for c in comment_pool
            if not self._is_sentinel_comment(c.get("body", ""))
        ]

        if new_comments:
            return {
                "state": "update",
                "existing_plan": existing_plan,
                "mr_iid": mr_iid,
                "mr_url": mr_url,
                "project_path": project_path,
                "discussions": [],
                "new_comments": new_comments,
            }

        # Nothing changed since last run
        return {
            "state": "nothing_changed",
            "existing_plan": existing_plan,
            "mr_iid": mr_iid,
            "mr_url": mr_url,
            "project_path": project_path,
            "discussions": [],
        }

    @staticmethod
    def _is_sentinel_comment(body: str) -> bool:
        """Check if a Jira comment was posted by Sentinel."""
        return (
            "Sentinel Confidence Report" in body
            or "Sentinel Investigation Report" in body
            or "Sentinel Functional Debrief" in body
        )

    def _get_mr_info(
        self,
        ticket_id: str,
        project_key: str,
    ) -> Dict[str, Any] | None:
        """Look up an existing MR for a ticket.

        Args:
            ticket_id: Jira ticket ID
            project_key: Project key (e.g., "ACME")

        Returns:
            Dictionary with project_path, mr_iid, mr_url, or None if no MR exists.
        """
        project_config = self.config.get_project_config(project_key)
        git_url = project_config.get("git_url", "")
        project_path = self.gitlab.extract_project_path(git_url)

        source_branch = get_branch_name(ticket_id)
        mrs = self.gitlab.list_merge_requests(
            project_id=project_path,
            source_branch=source_branch,
        )

        if not mrs:
            return None

        return {
            "project_path": project_path,
            "mr_iid": mrs[0]["iid"],
            "mr_url": mrs[0]["web_url"],
        }

    def _post_confidence_report(
        self,
        ticket_id: str,
        evaluation: Dict[str, Any],
    ) -> None:
        """Post a confidence report to Jira.

        Supports both Jira Cloud (ADF) and Jira Server (wiki markup).

        Args:
            ticket_id: Jira ticket ID
            evaluation: Full evaluation result from ConfidenceEvaluatorAgent
        """
        score = evaluation.get("confidence_score", 0)
        threshold = evaluation.get("threshold", 95)
        assumptions = evaluation.get("assumptions", [])
        questions = evaluation.get("questions", [])
        gaps = evaluation.get("gaps", [])
        invest = evaluation.get("invest_evaluation", {})
        scope_suggestion = evaluation.get("scope_suggestion")
        summary_text = evaluation.get("summary", "")

        # Build wiki markup (works on both Jira Server and Cloud)
        lines: list[str] = []
        lines.append(f"h2. \U0001f916 Sentinel Confidence Report \u2014 {score}/100 (threshold: {threshold})")
        lines.append("")

        if summary_text:
            lines.append(summary_text)
            lines.append("")

        if assumptions:
            lines.append("h3. Assumptions Made")
            for a in assumptions:
                lines.append(f"* {a}")
            lines.append("")

        if gaps:
            lines.append("h3. Information Gaps")
            for g in gaps:
                lines.append(f"* {g}")
            lines.append("")

        if questions:
            lines.append("h3. Questions to Clarify")
            for i, q in enumerate(questions, 1):
                lines.append(f"# {q}")
            lines.append("")

        if invest:
            lines.append("h3. INVEST Assessment")
            for criterion in ["independent", "negotiable", "valuable", "estimatable", "small", "testable"]:
                data = invest.get(criterion, {})
                score_val = data.get("score", "?")
                note = data.get("note", "")
                lines.append(f"* *{criterion.capitalize()}*: {score_val}/5 \u2014 {note}")
            lines.append("")

        if scope_suggestion:
            lines.append("h3. Suggested Scope")
            lines.append(scope_suggestion)
            lines.append("")

        lines.append(f"_Reply here with answers, then re-run:_ {{code}}sentinel plan {ticket_id}{{code}}")

        comment_body = "\n".join(lines)

        try:
            logger.info(f"Posting confidence report to Jira {ticket_id} ({len(lines)} lines)")
            self.jira.add_comment(ticket_id, comment_body)
            logger.info(f"Posted confidence report to Jira {ticket_id}")
        except Exception as e:
            logger.error(f"Failed to post confidence report to Jira {ticket_id}: {e}", exc_info=True)

    def _reply_to_discussions(
        self,
        discussions: list[Dict[str, Any]],
        feedback_responses: list[Dict[str, Any]],
        project_path: str,
        mr_iid: int,
    ) -> None:
        """Reply to MR discussions with revision explanations.

        Args:
            discussions: Unresolved discussions from GitLab
            feedback_responses: LLM-generated responses for each feedback item
            project_path: GitLab project path
            mr_iid: Merge request IID
        """
        for response in feedback_responses:
            discussion_id = response.get("discussion_id", "")
            changes_made = response.get("changes_made", "")
            section_affected = response.get("section_affected", "")

            # Find the matching discussion
            matching_discussion = None
            for disc in discussions:
                if disc.get("id") == discussion_id:
                    matching_discussion = disc
                    break

            if matching_discussion:
                # Add emoji reaction to the latest note
                notes = matching_discussion.get("notes", [])
                if notes:
                    latest_note_id = notes[-1].get("id")
                    if latest_note_id:
                        try:
                            self.gitlab.add_emoji_reaction(
                                project_id=project_path,
                                mr_iid=mr_iid,
                                note_id=latest_note_id,
                                emoji="eyes",
                                discussion_id=discussion_id,
                            )
                        except Exception as e:
                            logger.warning(f"Failed to add reaction to note {latest_note_id}: {e}")

                # Reply to the discussion
                reply_body = f"""**Plan Updated** \U0001f916

{changes_made}

**Section(s) affected:** {section_affected}
"""

                try:
                    self.gitlab.reply_to_discussion(
                        project_id=project_path,
                        mr_iid=mr_iid,
                        discussion_id=discussion_id,
                        body=reply_body,
                        resolve=False,
                    )
                    logger.info(f"Replied to discussion {discussion_id}")
                except Exception as e:
                    logger.error(f"Failed to reply to discussion {discussion_id}: {e}")

    def run(  # type: ignore[override]
        self,
        ticket_id: str,
        worktree_path: Path,
        force: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run the unified planning workflow.

        Auto-detects plan state (initial, has_feedback, update, nothing_changed)
        and takes the appropriate action. Always creates MR, always includes
        confidence report.

        Args:
            ticket_id: Jira ticket ID
            worktree_path: Path to the git worktree
            force: Skip confidence evaluation (no report generated)
            **kwargs: Additional parameters

        Returns:
            Dictionary with:
                - action: State that was detected
                - plan_path: Path to generated plan
                - mr_url: URL of the MR
                - plan_content: Plan content
                - plan_updated: True if plan was changed and committed
                - mr_created: True if new MR was created
                - confidence_score: Evaluator's confidence score (None if --force)
                - evaluation: Full evaluation result (None if --force)
                - feedback_count: Number of discussions addressed
                - revision_type: Revision type if revised
        """
        run_start = time.monotonic()
        logger.info(f"[RUN] Starting plan generation for {ticket_id} (force={force})")

        # Extract project key and set for session tracking
        project_key = ticket_id.split("-")[0]
        self.set_project(project_key)

        # Step 0: Auto-profile project if no profile exists
        t0 = time.monotonic()
        logger.info(f"[RUN] Step 0: Auto-profile check...")
        self._auto_profile_if_needed(worktree_path, project_key)
        logger.info(f"[RUN] Step 0: Auto-profile done ({time.monotonic() - t0:.1f}s)")

        # Step 1: Detect current state
        t0 = time.monotonic()
        logger.info(f"[RUN] Step 1: Detecting plan state...")
        state_info = self._detect_plan_state(ticket_id, worktree_path, project_key)
        state = state_info["state"]
        logger.info(f"[RUN] Step 1: State = {state} ({time.monotonic() - t0:.1f}s)")

        if state == "nothing_changed":
            logger.info(f"[RUN] Nothing changed — returning early ({time.monotonic() - run_start:.1f}s total)")
            plan_path = worktree_path / ".agents" / "plans" / f"{ticket_id}.md"
            return {
                "action": "nothing_changed",
                "plan_path": str(plan_path),
                "mr_url": state_info.get("mr_url"),
                "plan_content": state_info.get("existing_plan"),
                "plan_updated": False,
                "mr_created": False,
                "confidence_score": None,
                "evaluation": None,
                "feedback_count": 0,
                "revision_type": None,
            }

        # Step 2: Generate or revise plan based on state
        plan_path = worktree_path / ".agents" / "plans" / f"{ticket_id}.md"
        revision_result = None

        if state == "has_feedback":
            t0 = time.monotonic()
            logger.info(f"[RUN] Step 2a: Revising plan based on {len(state_info['discussions'])} discussion(s)...")
            revision_result = self.revise_plan(
                ticket_id, state_info["existing_plan"],
                state_info["discussions"], plan_path,
            )
            logger.info(f"[RUN] Step 2a: Revision done ({time.monotonic() - t0:.1f}s)")
            plan_content = revision_result["revised_plan"]

            t0 = time.monotonic()
            logger.info(f"[RUN] Step 2b: Analyzing ticket (post-revision)...")
            analysis = self.analyze_ticket(ticket_id, worktree_path)
            logger.info(f"[RUN] Step 2b: Analysis done ({time.monotonic() - t0:.1f}s)")
        else:
            t0 = time.monotonic()
            logger.info(f"[RUN] Step 2a: Analyzing ticket...")
            analysis = self.analyze_ticket(ticket_id, worktree_path)
            logger.info(f"[RUN] Step 2a: Analysis done ({time.monotonic() - t0:.1f}s)")

            # Step 2a.5: Investigate client comments (update re-entry only)
            investigation_findings = None
            new_comments = state_info.get("new_comments", [])
            if state == "update" and new_comments:
                t0 = time.monotonic()
                logger.info(f"[RUN] Step 2a.5: Investigating {len(new_comments)} new comment(s)...")
                investigation_findings = self.investigate_comments(
                    ticket_id, new_comments,
                    state_info.get("existing_plan", ""), worktree_path,
                )
                self._post_investigation_report(ticket_id, investigation_findings)
                logger.info(f"[RUN] Step 2a.5: Investigation done ({time.monotonic() - t0:.1f}s)")

            t0 = time.monotonic()
            logger.info(f"[RUN] Step 2b: Generating plan...")
            plan_content = self.generate_plan(
                ticket_id, analysis, plan_path, worktree_path,
                investigation_findings=investigation_findings,
            )
            logger.info(f"[RUN] Step 2b: Plan generation done ({time.monotonic() - t0:.1f}s, {len(plan_content)} chars)")

        # Step 3: Confidence evaluation (unless --force)
        evaluation = None
        if not force:
            t0 = time.monotonic()
            logger.info(f"[RUN] Step 3: Running confidence evaluation...")
            evaluation = self._evaluate_confidence(
                plan_content, analysis, ticket_id, project_key
            )
            logger.info(f"[RUN] Step 3: Confidence = {evaluation['confidence_score']}/100 ({time.monotonic() - t0:.1f}s)")
        else:
            logger.info(f"[RUN] Step 3: Skipped (--force)")

        # Step 4: Commit and push
        t0 = time.monotonic()
        logger.info(f"[RUN] Step 4: Committing and pushing...")
        plan_updated = self.commit_and_push_plan(plan_path, ticket_id, worktree_path)
        logger.info(f"[RUN] Step 4: Commit/push done (updated={plan_updated}, {time.monotonic() - t0:.1f}s)")

        # Step 5: Create or get MR
        t0 = time.monotonic()
        logger.info(f"[RUN] Step 5: Creating/getting MR...")
        mr_url, mr_created = self.create_or_get_mr(ticket_id, plan_path, project_key)
        logger.info(f"[RUN] Step 5: MR done (created={mr_created}, {time.monotonic() - t0:.1f}s)")

        # Step 6: Post confidence report to Jira
        if evaluation:
            t0 = time.monotonic()
            logger.info(f"[RUN] Step 6: Posting confidence report to Jira...")
            self._post_confidence_report(ticket_id, evaluation)
            logger.info(f"[RUN] Step 6: Report posted ({time.monotonic() - t0:.1f}s)")

        # Step 7: If revision, reply to discussions
        if state == "has_feedback" and revision_result:
            t0 = time.monotonic()
            logger.info(f"[RUN] Step 7: Replying to discussions...")
            self._reply_to_discussions(
                state_info["discussions"],
                revision_result.get("feedback_responses", []),
                state_info["project_path"],
                state_info["mr_iid"],
            )
            logger.info(f"[RUN] Step 7: Replies done ({time.monotonic() - t0:.1f}s)")

        logger.info(f"[RUN] Complete for {ticket_id} — total {time.monotonic() - run_start:.1f}s")

        return {
            "action": state,
            "plan_path": str(plan_path),
            "mr_url": mr_url,
            "plan_content": plan_content,
            "plan_updated": plan_updated,
            "mr_created": mr_created,
            "confidence_score": evaluation["confidence_score"] if evaluation else None,
            "evaluation": evaluation,
            "feedback_count": len(state_info.get("discussions", [])),
            "revision_type": revision_result.get("revision_type") if revision_result else None,
            "investigation_findings": investigation_findings if state != "has_feedback" else None,
        }

    def run_revision(  # type: ignore[override]
        self,
        ticket_id: str,
        worktree_path: Path,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Deprecated — run() now auto-detects state and handles revisions.

        Args:
            ticket_id: Jira ticket ID
            worktree_path: Path to the git worktree
            **kwargs: Additional parameters

        Returns:
            Result from run()
        """
        logger.warning("run_revision() is deprecated — run() now auto-detects state")
        return self.run(ticket_id, worktree_path, **kwargs)

    def _evaluate_confidence(
        self,
        plan_content: str,
        analysis: Dict[str, Any],
        ticket_id: str,
        project_key: str,
    ) -> Dict[str, Any]:
        """Evaluate plan confidence using the ConfidenceEvaluatorAgent.

        Args:
            plan_content: Generated plan markdown
            analysis: Ticket analysis results
            ticket_id: Jira ticket ID
            project_key: Project key for threshold lookup

        Returns:
            Evaluation dict with 'passed' and 'threshold' added
        """
        from src.agents.confidence_evaluator import ConfidenceEvaluatorAgent

        logger.info(f"Running confidence evaluation for {ticket_id}")
        evaluator = ConfidenceEvaluatorAgent()
        evaluator.set_project(project_key)
        result = evaluator.evaluate(plan_content, analysis["ticket_data"], analysis)

        # Look up threshold (per-project override or global default)
        project_config = self.config.get_project_config(project_key)
        threshold = project_config.get(
            "confidence_threshold",
            self.config.get("confidence.default_threshold", 95),
        )

        return {
            **result,
            "passed": result["confidence_score"] >= threshold,
            "threshold": threshold,
        }

