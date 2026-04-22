"""Functional Debrief Agent - Analyzes tickets from a functional perspective."""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from src.agents.base_agent import PlanningAgent
from src.jira_factory import get_jira_client
from src.ticket_context import TicketContextBuilder


logger = logging.getLogger(__name__)


class FunctionalDebriefAgent(PlanningAgent):
    """Agent that analyzes Jira tickets from a functional perspective.

    Generates conversational debrief comments to validate understanding
    with the ticket author before technical planning begins.

    Uses Claude Sonnet 4.5 for text comprehension and writing.
    """

    DEBRIEF_MARKER = "Sentinel Functional Debrief"

    def __init__(self) -> None:
        """Initialize functional debrief agent."""
        super().__init__(
            agent_name="functional_debrief",
            model="claude-4-5-sonnet",
            temperature=0.3,
        )

        self.jira = get_jira_client()

    def run(  # type: ignore[override]
        self,
        ticket_id: str,
        project: str | None = None,
        worktree_path: str | Path | None = None,
        user_prompt: str | None = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run the debrief workflow.

        Auto-detects debrief state and takes the appropriate action:
        - initial: Generate and post debrief
        - awaiting_reply: Inform user to wait
        - has_reply: Generate follow-up or propose closure
        - pending_confirmation: Wait for client confirmation
        - validated: Inform user debrief is complete

        Args:
            ticket_id: Jira ticket ID (e.g., "ACME-123")
            project: Project key (optional)
            worktree_path: Path to git worktree for codebase access (optional)
            **kwargs: Additional parameters

        Returns:
            Dictionary with action taken and state details
        """
        logger.info(f"Running functional debrief for {ticket_id}")
        self._cwd = str(worktree_path) if worktree_path else None

        if project:
            self.set_project(project)

        # Step 1: Fetch ticket (validates it exists)
        ctx = TicketContextBuilder(self.jira, ticket_id)
        ticket_data = ctx.ticket_data
        logger.info(f"Fetched ticket: {ticket_data.get('summary', 'N/A')}")

        # Step 2: Detect state
        state_info = self._detect_debrief_state(ticket_id)
        state = state_info["state"]
        logger.info(f"Debrief state for {ticket_id}: {state}")

        if state == "initial":
            # Generate debrief, post to Jira, save state
            debrief_data = self._generate_debrief(ticket_id, ticket_data, user_prompt=user_prompt, ctx=ctx)
            self._post_debrief_comment(ticket_id, debrief_data, comment_type="initial")
            self._save_state(ticket_id, {
                "status": "awaiting_reply",
                "ticket_id": ticket_id,
                "posted_at": datetime.now(timezone.utc).isoformat(),
                "iteration_count": 1,
                "validated_at": None,
            })
            logger.info(f"Posted initial debrief for {ticket_id}")
            return {
                "action": "posted",
                "debrief_data": debrief_data,
                "iteration_count": 1,
            }

        elif state == "awaiting_reply":
            return {
                "action": "awaiting_reply",
                "posted_at": state_info.get("posted_at"),
                "iteration_count": state_info.get("iteration_count", 1),
            }

        elif state == "has_reply":
            # Generate follow-up based on client replies
            existing_state = state_info["existing_state"]
            new_replies = state_info["client_replies"]
            conversation = state_info.get("conversation_history", [])

            followup_data = self._generate_followup(
                ticket_id, ticket_data, conversation, new_replies,
                user_prompt=user_prompt, ctx=ctx,
            )

            iteration_count = existing_state.get("iteration_count", 1) + 1

            if followup_data.get("gaps_resolved", False):
                # Propose closure
                self._post_debrief_comment(ticket_id, followup_data, comment_type="summary")
                self._save_state(ticket_id, {
                    **existing_state,
                    "status": "pending_confirmation",
                    "iteration_count": iteration_count,
                })
                logger.info(f"Proposed closure for {ticket_id} after {iteration_count} iterations")
                return {
                    "action": "proposed_closure",
                    "debrief_data": followup_data,
                    "iteration_count": iteration_count,
                }
            else:
                # Post follow-up, continue conversation
                self._post_debrief_comment(ticket_id, followup_data, comment_type="followup")
                self._save_state(ticket_id, {
                    **existing_state,
                    "status": "awaiting_reply",
                    "iteration_count": iteration_count,
                })
                logger.info(f"Posted follow-up for {ticket_id} (iteration {iteration_count})")
                return {
                    "action": "followed_up",
                    "debrief_data": followup_data,
                    "iteration_count": iteration_count,
                }

        elif state == "pending_confirmation":
            if state_info.get("client_replied"):
                # Client replied after summary — could be confirmation OR correction
                # Send to LLM to determine which
                existing_state = state_info["existing_state"]
                new_replies = state_info["client_replies"]
                conversation = state_info.get("conversation_history", [])

                followup_data = self._generate_followup(
                    ticket_id, ticket_data, conversation, new_replies,
                    user_prompt=user_prompt, ctx=ctx,
                )

                iteration_count = existing_state.get("iteration_count", 1) + 1

                if followup_data.get("gaps_resolved", False):
                    # LLM determined client confirmed — mark validated
                    self._save_state(ticket_id, {
                        **existing_state,
                        "status": "validated",
                        "validated_at": datetime.now(timezone.utc).isoformat(),
                        "iteration_count": iteration_count,
                    })
                    logger.info(f"Debrief validated for {ticket_id}")
                    return {
                        "action": "validated",
                        "iteration_count": iteration_count,
                    }
                else:
                    # Client corrected/added — reopen conversation
                    self._post_debrief_comment(ticket_id, followup_data, comment_type="followup")
                    self._save_state(ticket_id, {
                        **existing_state,
                        "status": "awaiting_reply",
                        "iteration_count": iteration_count,
                    })
                    logger.info(f"Client corrected after summary for {ticket_id}, reopening conversation (iteration {iteration_count})")
                    return {
                        "action": "followed_up",
                        "debrief_data": followup_data,
                        "iteration_count": iteration_count,
                    }
            else:
                return {
                    "action": "pending_confirmation",
                    "iteration_count": state_info.get("iteration_count", 1),
                }

        elif state == "validated":
            return {
                "action": "validated",
                "iteration_count": state_info.get("iteration_count", 1),
                "validated_at": state_info.get("validated_at"),
            }

        # Should not reach here
        logger.warning(f"Unexpected debrief state: {state}")
        return {"action": "error", "message": f"Unexpected state: {state}"}

    def _detect_debrief_state(self, ticket_id: str) -> Dict[str, Any]:
        """Detect the current debrief state for a ticket.

        Checks local state file and Jira comments to determine
        what action to take.

        Args:
            ticket_id: Jira ticket ID

        Returns:
            Dictionary with state and context information
        """
        # Load local state
        existing_state = self._load_state(ticket_id)

        if existing_state is None:
            return {"state": "initial"}

        status = existing_state.get("status")

        # Fetch Jira comments for all non-initial states
        comments = self.jira.get_ticket_comments(ticket_id)

        # Find the last debrief comment in Jira
        last_debrief_idx = -1
        for i, comment in enumerate(comments):
            if self.is_debrief_comment(comment.get("body", "")):
                last_debrief_idx = i

        if status == "validated":
            # Verify the debrief comment still exists in Jira
            if last_debrief_idx == -1:
                logger.warning(f"Debrief comment deleted from Jira for {ticket_id}, resetting to initial")
                return {"state": "initial"}
            return {
                "state": "validated",
                "existing_state": existing_state,
                "validated_at": existing_state.get("validated_at"),
                "iteration_count": existing_state.get("iteration_count", 1),
            }

        if last_debrief_idx == -1:
            # Debrief comment not found in Jira (deleted?) — reset to initial
            logger.warning(f"Debrief comment not found in Jira for {ticket_id}, resetting to initial")
            return {"state": "initial"}

        # Collect non-Sentinel comments after the last debrief comment
        post_debrief_comments = comments[last_debrief_idx + 1:]
        client_replies = [
            c for c in post_debrief_comments
            if not self.is_debrief_comment(c.get("body", ""))
            and not self._is_other_sentinel_comment(c.get("body", ""))
        ]

        if status == "pending_confirmation":
            if client_replies:
                return {
                    "state": "pending_confirmation",
                    "existing_state": existing_state,
                    "client_replied": True,
                    "client_replies": client_replies,
                    "conversation_history": comments[:last_debrief_idx + 1],
                    "iteration_count": existing_state.get("iteration_count", 1),
                }
            return {
                "state": "pending_confirmation",
                "existing_state": existing_state,
                "client_replied": False,
                "iteration_count": existing_state.get("iteration_count", 1),
            }

        # Status is "awaiting_reply"
        if client_replies:
            return {
                "state": "has_reply",
                "existing_state": existing_state,
                "client_replies": client_replies,
                "conversation_history": comments[:last_debrief_idx + 1],
                "posted_at": existing_state.get("posted_at"),
                "iteration_count": existing_state.get("iteration_count", 1),
            }

        return {
            "state": "awaiting_reply",
            "existing_state": existing_state,
            "posted_at": existing_state.get("posted_at"),
            "iteration_count": existing_state.get("iteration_count", 1),
        }

    def _generate_debrief(
        self, ticket_id: str, ticket_data: Dict[str, Any],
        user_prompt: str | None = None,
        ctx: TicketContextBuilder | None = None,
    ) -> Dict[str, Any]:
        """Generate the initial functional debrief for a ticket.

        Args:
            ticket_id: Jira ticket ID
            ticket_data: Ticket data from Jira
            ctx: Shared ticket context builder

        Returns:
            Structured debrief data (understanding, assumptions, gaps, questions, cta)
        """
        if ctx is None:
            ctx = TicketContextBuilder(self.jira, ticket_id)

        description = ctx.description
        comments_context = ctx.format_comments()
        issuetype_name = ctx.type_name
        priority_name = ctx.priority_name

        codebase_instruction = (
            "\n**CODEBASE**: You have access to the project codebase. "
            "Use Read, Grep, and Glob tools to explore relevant code BEFORE writing your response. "
            "Validate assumptions against the code. But keep your JSON output purely functional — "
            "no file paths, class names, or code references in the output.\n"
            if self._cwd else
            "\n**NOTE**: No codebase access available. Analyze from the ticket text alone.\n"
        )

        prompt = f"""Analyze this Jira ticket from a FUNCTIONAL perspective and generate a debrief.

**IMPORTANT**: Return ONLY a JSON object as your final output.{codebase_instruction}

**Mode**: Initial Debrief

---

**Ticket Details**:
- **ID**: {ticket_id}
- **Summary**: {ticket_data.get('summary', 'N/A')}
- **Type**: {issuetype_name}
- **Priority**: {priority_name}

**Description**:
{description}
{comments_context}
---

Generate a functional debrief following your system prompt instructions.
Return ONLY the JSON object as specified in the "Mode: Initial Debrief" section of your system prompt."""

        prompt = self._append_operator_prompt(prompt, user_prompt)

        response = self.send_message(prompt, cwd=self._cwd)
        logger.info(f"Raw debrief response ({len(response)} chars): {response[:300]}...")

        result = self._extract_json_from_response(response)
        if result is None:
            logger.error(f"Failed to parse debrief JSON. Response:\n{response[:2000]}")
            return self._default_debrief()

        # Validate required fields
        for field in ["understanding", "assumptions", "gaps", "questions", "cta"]:
            if field not in result:
                logger.warning(f"Missing field in debrief: {field}")
                result.setdefault(field, [] if field in ("assumptions", "gaps", "questions") else "")

        result.setdefault("gaps_resolved", False)
        return result

    def _generate_followup(
        self,
        ticket_id: str,
        ticket_data: Dict[str, Any],
        conversation_history: List[Dict[str, Any]],
        new_replies: List[Dict[str, Any]],
        user_prompt: str | None = None,
        ctx: TicketContextBuilder | None = None,
    ) -> Dict[str, Any]:
        """Generate a follow-up response based on client replies.

        Args:
            ticket_id: Jira ticket ID
            ticket_data: Ticket data from Jira
            conversation_history: All comments up to and including the last debrief
            new_replies: Client replies since the last debrief comment
            ctx: Shared ticket context builder

        Returns:
            Structured follow-up data (same format as debrief)
        """
        if ctx is None:
            ctx = TicketContextBuilder(self.jira, ticket_id)
        description = ctx.description

        # Format conversation history
        history_text = ""
        if conversation_history:
            history_lines = []
            for c in conversation_history:
                history_lines.append(f"- [{c['author']}]: {c['body']}")
            history_text = "\n".join(history_lines)

        # Format new client replies
        replies_text = "\n".join(
            f"- [{c['author']}]: {c['body']}" for c in new_replies
        )

        codebase_instruction = (
            "\n**CODEBASE**: You have access to the project codebase. "
            "Use Read, Grep, and Glob tools to explore relevant code BEFORE writing your response. "
            "Validate the client's replies against the code. But keep your JSON output purely functional — "
            "no file paths, class names, or code references in the output.\n"
            if self._cwd else
            "\n**NOTE**: No codebase access available. Analyze from the ticket text alone.\n"
        )

        prompt = f"""Analyze the client's reply and generate a follow-up for this Jira ticket debrief.

**IMPORTANT**: Return ONLY a JSON object as your final output.{codebase_instruction}

**Mode**: Follow-up

---

**Ticket Details**:
- **ID**: {ticket_id}
- **Summary**: {ticket_data.get('summary', 'N/A')}

**Original Description**:
{description}

---

**Conversation So Far**:
{history_text}

---

**New Client Replies**:
{replies_text}

---

Generate a follow-up response following your system prompt instructions.
If all gaps are resolved, set "gaps_resolved" to true and provide a complete summary in "understanding".
Return ONLY the JSON object as specified in the "Mode: Follow-up" section of your system prompt."""

        prompt = self._append_operator_prompt(prompt, user_prompt)

        response = self.send_message(prompt, cwd=self._cwd)
        logger.info(f"Raw followup response ({len(response)} chars): {response[:300]}...")

        result = self._extract_json_from_response(response)
        if result is None:
            logger.error(f"Failed to parse followup JSON. Response:\n{response[:2000]}")
            return self._default_debrief()

        # Validate required fields
        for field in ["understanding", "assumptions", "gaps", "questions", "cta"]:
            if field not in result:
                logger.warning(f"Missing field in followup: {field}")
                result.setdefault(field, [] if field in ("assumptions", "gaps", "questions") else "")

        result.setdefault("gaps_resolved", False)
        return result

    def _post_debrief_comment(
        self,
        ticket_id: str,
        debrief_data: Dict[str, Any],
        comment_type: str = "initial",
    ) -> None:
        """Post a debrief comment to Jira in wiki markup format.

        Args:
            ticket_id: Jira ticket ID
            debrief_data: Structured debrief data from LLM
            comment_type: "initial", "followup", or "summary"
        """
        # Determine header
        if comment_type == "summary":
            header = f"h2. \U0001f4ac {self.DEBRIEF_MARKER} \u2014 Summary"
        elif comment_type == "followup":
            header = f"h2. \U0001f4ac {self.DEBRIEF_MARKER} \u2014 Follow-up"
        else:
            header = f"h2. \U0001f4ac {self.DEBRIEF_MARKER}"

        lines: list[str] = [header, ""]

        # Understanding
        understanding = debrief_data.get("understanding", "")
        if understanding:
            lines.append(understanding)
            lines.append("")

        # Assumptions
        assumptions = debrief_data.get("assumptions", [])
        if assumptions:
            lines.append("h3. Assumptions")
            for a in assumptions:
                lines.append(f"* {a}")
            lines.append("")

        # Gaps
        gaps = debrief_data.get("gaps", [])
        if gaps:
            lines.append("h3. Information Gaps")
            for g in gaps:
                lines.append(f"* {g}")
            lines.append("")

        # Questions
        questions = debrief_data.get("questions", [])
        if questions:
            lines.append("h3. Questions")
            for q in questions:
                lines.append(f"# {q}")
            lines.append("")

        # CTA
        cta = debrief_data.get("cta", "")
        if cta:
            lines.append(cta)
            lines.append("")

        # Footer with re-run instruction
        if comment_type == "summary":
            lines.append(f"_Re-run:_ {{code}}sentinel plan {ticket_id}{{code}}")
        else:
            lines.append(f"_Re-run:_ {{code}}sentinel debrief {ticket_id}{{code}}")

        comment_body = "\n".join(lines)

        try:
            logger.info(f"Posting {comment_type} debrief to Jira {ticket_id} ({len(lines)} lines)")
            self.jira.add_comment(ticket_id, comment_body)
            logger.info(f"Posted {comment_type} debrief to Jira {ticket_id}")
        except Exception as e:
            logger.error(f"Failed to post debrief to Jira {ticket_id}: {e}", exc_info=True)

    def _save_state(self, ticket_id: str, state: Dict[str, Any]) -> None:
        """Save debrief state to local file.

        Args:
            ticket_id: Jira ticket ID
            state: State dictionary to persist
        """
        state_dir = Path.home() / ".sentinel" / "debriefs"
        state_dir.mkdir(parents=True, exist_ok=True)

        state_file = state_dir / f"{ticket_id}.json"
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)

        logger.info(f"Saved debrief state to {state_file}")

    def _load_state(self, ticket_id: str) -> Dict[str, Any] | None:
        """Load debrief state from local file.

        Args:
            ticket_id: Jira ticket ID

        Returns:
            State dictionary, or None if no state file exists
        """
        state_file = Path.home() / ".sentinel" / "debriefs" / f"{ticket_id}.json"

        if not state_file.exists():
            return None

        with open(state_file, "r") as f:
            return json.load(f)

    @staticmethod
    def is_debrief_comment(body: str) -> bool:
        """Check if a Jira comment is a Sentinel debrief.

        Args:
            body: Comment body text

        Returns:
            True if the comment contains the debrief marker
        """
        return FunctionalDebriefAgent.DEBRIEF_MARKER in body

    @staticmethod
    def _is_other_sentinel_comment(body: str) -> bool:
        """Check if a Jira comment is from another Sentinel agent (not debrief).

        Args:
            body: Comment body text

        Returns:
            True if the comment is from Sentinel but not a debrief
        """
        return (
            "Sentinel Confidence Report" in body
            or "Sentinel Investigation Report" in body
        )

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

        # Try 3: Find first { and match braces
        first_brace = response.find("{")
        if first_brace != -1:
            depth = 0
            for i, char in enumerate(response[first_brace:]):
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        json_str = response[first_brace:first_brace + i + 1]
                        try:
                            return json.loads(json_str)
                        except json.JSONDecodeError:
                            pass
                        break

        return None

    @staticmethod
    def _default_debrief() -> Dict[str, Any]:
        """Return a safe default debrief when LLM parsing fails."""
        return {
            "understanding": "Could not automatically generate debrief — please review the ticket manually.",
            "assumptions": [],
            "gaps": ["Automated analysis failed — manual review needed"],
            "questions": ["Could you describe the desired feature in your own words?"],
            "cta": "Please describe what you need so we can proceed.",
            "gaps_resolved": False,
        }
