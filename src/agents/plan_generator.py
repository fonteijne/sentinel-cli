"""Plan Generator Agent - Analyzes tickets and creates implementation plans."""

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Dict

from src.agents.base_agent import PlanningAgent
from src.attachment_manager import AttachmentManager
from src.jira_factory import get_jira_client
from src.gitlab_client import GitLabClient
from src.config_loader import get_config
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

    def generate_plan(
        self,
        ticket_id: str,
        context: Dict[str, Any],
        output_path: Path,
    ) -> str:
        """Generate a detailed implementation plan.

        Args:
            ticket_id: Jira ticket ID
            context: Context from ticket analysis
            output_path: Path to write the plan file

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

        # System prompt defines the detailed format - user prompt tells agent to write the file
        plan_file_path = str(output_path)
        plan_prompt = f"""Generate a comprehensive implementation plan for ticket {ticket_id}.

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
{AttachmentManager().format_for_prompt(attachments_data) if attachments_data else ""}
## OUTPUT FILE PATH

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
                response = self.send_message(plan_prompt, cwd=worktree_cwd)
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

    def run(  # type: ignore[override]
        self,
        ticket_id: str,
        worktree_path: Path,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run the complete planning workflow.

        Args:
            ticket_id: Jira ticket ID
            worktree_path: Path to the git worktree
            **kwargs: Additional parameters

        Returns:
            Dictionary with:
                - plan_path: Path to generated plan
                - mr_url: URL of created draft MR
                - analysis: Ticket analysis results
                - plan_updated: True if plan was changed and committed
                - mr_created: True if new MR was created
        """
        logger.info(f"Running plan generation for {ticket_id}")

        # Extract project key and set for session tracking
        project_key = ticket_id.split("-")[0]
        self.set_project(project_key)

        # Step 1: Analyze ticket (pass worktree so agent can explore codebase)
        analysis = self.analyze_ticket(ticket_id, worktree_path)

        # Step 2: Generate plan
        plan_path = worktree_path / ".agents" / "plans" / f"{ticket_id}.md"
        plan_content = self.generate_plan(ticket_id, analysis, plan_path)

        # Step 3: Commit and push plan (if changed)
        plan_updated = self.commit_and_push_plan(plan_path, ticket_id, worktree_path)

        # Step 4: Create draft MR (or get existing)
        mr_url, mr_created = self.create_or_get_mr(ticket_id, plan_path, project_key)

        return {
            "plan_path": str(plan_path),
            "mr_url": mr_url,
            "analysis": analysis,
            "plan_content": plan_content,
            "plan_updated": plan_updated,
            "mr_created": mr_created,
        }

    def run_revision(  # type: ignore[override]
        self,
        ticket_id: str,
        worktree_path: Path,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Revise an existing plan based on MR feedback.

        Args:
            ticket_id: Jira ticket ID
            worktree_path: Path to the git worktree
            **kwargs: Additional parameters

        Returns:
            Dictionary with:
                - plan_path: Path to revised plan
                - mr_url: URL of the MR
                - revision_type: "incremental" or "full_rewrite"
                - feedback_count: Number of feedback items addressed
                - plan_updated: Whether plan was updated
        """
        logger.info(f"Running plan revision for {ticket_id}")

        # Extract project key and set for session tracking
        project_key = ticket_id.split("-")[0]
        self.set_project(project_key)

        # Step 1: Get existing plan
        plan_path = worktree_path / ".agents" / "plans" / f"{ticket_id}.md"
        if not plan_path.exists():
            raise FileNotFoundError(
                f"No existing plan found at {plan_path}. "
                f"Run 'sentinel plan {ticket_id}' first."
            )

        current_plan = plan_path.read_text()

        # Step 2: Find the MR
        project_config = self.config.get_project_config(project_key)
        git_url = project_config.get("git_url", "")

        # Extract project path from git URL
        project_path = self.gitlab.extract_project_path(git_url)

        source_branch = get_branch_name(ticket_id)
        mrs = self.gitlab.list_merge_requests(
            project_id=project_path,
            source_branch=source_branch,
        )

        if not mrs:
            raise ValueError(
                f"No MR found for branch {source_branch}. "
                f"Run 'sentinel plan {ticket_id}' first to create the initial plan and MR."
            )

        mr_data = mrs[0]
        mr_iid = mr_data["iid"]
        mr_url = mr_data["web_url"]

        logger.info(f"Found MR: {mr_url}")

        # Step 3: Fetch unresolved discussions
        discussions = self.gitlab.get_merge_request_discussions(
            project_id=project_path,
            mr_iid=mr_iid,
            unresolved_only=True,
        )

        if not discussions:
            logger.info("No unresolved discussions found - nothing to revise")
            return {
                "plan_path": str(plan_path),
                "mr_url": mr_url,
                "revision_type": "none",
                "feedback_count": 0,
                "plan_updated": False,
                "message": "No unresolved discussions to address",
            }

        logger.info(f"Found {len(discussions)} unresolved discussions")

        # Step 4: Revise plan based on feedback
        revision_result = self.revise_plan(
            ticket_id=ticket_id,
            current_plan=current_plan,
            feedback=discussions,
            output_path=plan_path,
        )

        # Step 5: Commit and push revised plan
        plan_updated = self.commit_and_push_plan(plan_path, ticket_id, worktree_path)

        # Step 6: Reply to discussions with explanations
        feedback_responses = revision_result.get("feedback_responses", [])
        for response in feedback_responses:
            discussion_id = response.get("discussion_id", "")
            changes_made = response.get("changes_made", "")
            section_affected = response.get("section_affected", "")

            # Find the discussion
            matching_discussion = None
            for disc in discussions:
                if disc.get("id") == discussion_id:
                    matching_discussion = disc
                    break

            if matching_discussion:
                # Get the latest note in the discussion to add reaction
                notes = matching_discussion.get("notes", [])
                if notes:
                    latest_note = notes[-1]  # Get the most recent comment
                    latest_note_id = latest_note.get("id")

                    # Add emoji reaction to acknowledge the feedback
                    if latest_note_id:
                        try:
                            self.gitlab.add_emoji_reaction(
                                project_id=project_path,
                                mr_iid=mr_iid,
                                note_id=latest_note_id,
                                emoji="eyes",  # 👀 to show we've read it
                                discussion_id=discussion_id,  # Pass discussion ID for proper endpoint
                            )
                            logger.info(f"Added reaction to note {latest_note_id}")
                        except Exception as e:
                            logger.warning(f"Failed to add reaction to note {latest_note_id}: {e}")

                # Reply to the discussion
                reply_body = f"""**Plan Updated** 🤖

{changes_made}

**Section(s) affected:** {section_affected}

**Revision type:** {revision_result.get('revision_type', 'incremental')}
"""

                try:
                    self.gitlab.reply_to_discussion(
                        project_id=project_path,
                        mr_iid=mr_iid,
                        discussion_id=discussion_id,
                        body=reply_body,
                        resolve=False,  # Don't auto-resolve, let reviewers confirm
                    )
                    logger.info(f"Replied to discussion {discussion_id}")
                except Exception as e:
                    logger.error(f"Failed to reply to discussion {discussion_id}: {e}")

        # Step 7: Add summary comment to MR
        summary = f"""## Plan Revision Summary 🔄

**Revision Type:** {revision_result.get('revision_type', 'incremental').replace('_', ' ').title()}

**Rationale:** {revision_result.get('rationale', 'N/A')}

**Feedback Addressed:** {len(feedback_responses)} discussion(s)

The plan has been updated based on team feedback. Please review the changes and let me know if further revisions are needed.

---
🤖 Updated by Sentinel Plan Generator
"""

        try:
            self.gitlab.add_merge_request_comment(
                project_id=project_path,
                mr_iid=mr_iid,
                body=summary,
            )
        except Exception as e:
            logger.error(f"Failed to add summary comment: {e}")

        return {
            "plan_path": str(plan_path),
            "mr_url": mr_url,
            "revision_type": revision_result.get("revision_type", "incremental"),
            "feedback_count": len(discussions),
            "plan_updated": plan_updated,
            "responses_posted": len(feedback_responses),
        }
