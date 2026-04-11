"""Confidence Evaluator Agent - Assesses plan quality with VETO power."""

import json
import logging
import re
from typing import Any, Dict

from src.agents.base_agent import ReviewAgent


logger = logging.getLogger(__name__)


class ConfidenceEvaluatorAgent(ReviewAgent):
    """Agent that evaluates implementation plan confidence.

    Uses Claude Sonnet 4.5 for consistent evaluation with VETO power.
    Scores plans against INVEST criteria and identifies gaps/assumptions.
    """

    def __init__(self) -> None:
        """Initialize confidence evaluator agent."""
        super().__init__(
            agent_name="confidence_evaluator",
            model="claude-4-5-sonnet",
            temperature=0.1,
            veto_power=True,
        )

    def evaluate(
        self,
        plan_content: str,
        ticket_data: Dict[str, Any],
        analysis: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Evaluate a plan's confidence and extract gaps/assumptions.

        Args:
            plan_content: The generated implementation plan (markdown)
            ticket_data: Raw Jira ticket data
            analysis: Ticket analysis results (requirements, risks, etc.)

        Returns:
            Dictionary with:
                - confidence_score: 0-100
                - gaps: List of missing information
                - assumptions: List of assumptions made
                - questions: List of questions for the ticket author
                - invest_evaluation: INVEST criteria scores
                - summary: One-line evaluation summary
                - scope_suggestion: Split suggestion or None
        """
        logger.info("Evaluating plan confidence")

        # Build evaluation prompt
        description = ticket_data.get("description", "")
        if isinstance(description, dict):
            # ADF format - extract text representation
            from src.utils.adf_parser import parse_adf_to_text
            description = parse_adf_to_text(description)

        requirements = analysis.get("requirements", [])
        risks = analysis.get("risks", [])
        complexity = analysis.get("estimated_complexity", "unknown")

        requirements_text = "\n".join(f"- {r}" for r in requirements) if requirements else "None extracted"
        risks_text = "\n".join(f"- {r}" for r in risks) if risks else "None identified"

        # Include comments if available (for re-entry context)
        comments = analysis.get("comments", [])
        comments_text = ""
        if comments:
            comments_text = "\n**Existing Comments/Discussion**:\n" + "\n".join(
                f"- [{c['author']}]: {c['body']}" for c in comments
            ) + "\n"

        eval_prompt = f"""Evaluate this implementation plan against its source Jira ticket.

**IMPORTANT**: Return ONLY a JSON object. Do NOT use any tools. Do NOT explore any codebase.

---

## Jira Ticket

**ID**: {ticket_data.get('key', 'N/A')}
**Summary**: {ticket_data.get('summary', 'N/A')}
**Type**: {ticket_data.get('issuetype', {}).get('name', 'Unknown') if isinstance(ticket_data.get('issuetype'), dict) else ticket_data.get('issuetype', 'Unknown')}
**Priority**: {ticket_data.get('priority', {}).get('name', 'Medium') if isinstance(ticket_data.get('priority'), dict) else ticket_data.get('priority', 'Medium')}

**Description**:
{description}
{comments_text}
---

## Analysis Results

**Requirements extracted**:
{requirements_text}

**Risks identified**:
{risks_text}

**Estimated complexity**: {complexity}

---

## Implementation Plan

{plan_content}

---

## Your Task

Evaluate the plan against the ticket using the INVEST criteria (Independent, Negotiable, Valuable, Estimatable, Small, Testable).

Return ONLY a JSON object in this exact format (no other text, no markdown):

```json
{{
  "confidence_score": <0-100 integer>,
  "gaps": ["list of missing information in the ticket"],
  "assumptions": ["list of assumptions the plan made that aren't in the ticket"],
  "questions": ["questions for the ticket author to clarify gaps — max 5"],
  "invest_evaluation": {{
    "independent": {{"score": <1-5>, "note": "explanation"}},
    "negotiable": {{"score": <1-5>, "note": "explanation"}},
    "valuable": {{"score": <1-5>, "note": "explanation"}},
    "estimatable": {{"score": <1-5>, "note": "explanation"}},
    "small": {{"score": <1-5>, "note": "explanation"}},
    "testable": {{"score": <1-5>, "note": "explanation"}}
  }},
  "summary": "One-line summary of the evaluation",
  "scope_suggestion": null or "suggestion to split the ticket"
}}
```

Scoring: base = sum(INVEST scores) / 30 * 100. Subtract 8 per gap (max -40), 5 per assumption (max -25). Floor at 0.
Match the tone of any questions to the ticket's technical level.
Return ONLY the JSON. No explanation, no markdown fences around it."""

        try:
            response = self.send_message(eval_prompt, cwd=None)
            logger.info(f"Raw evaluator response ({len(response)} chars): {response[:300]}...")
            result = self._extract_json_from_response(response)

            if result is None:
                logger.error(f"Failed to parse evaluator JSON response. Full response:\n{response[:2000]}")
                # Return a conservative default that triggers triage
                return self._default_evaluation(
                    "Failed to parse evaluation response — defaulting to triage"
                )

            # Validate required fields
            required_fields = [
                "confidence_score", "gaps", "assumptions",
                "questions", "invest_evaluation", "summary",
            ]
            for field in required_fields:
                if field not in result:
                    logger.warning(f"Missing field in evaluation: {field}")
                    result.setdefault(field, self._default_field(field))

            # Ensure confidence_score is an integer
            result["confidence_score"] = int(result.get("confidence_score", 0))

            # Ensure scope_suggestion exists
            result.setdefault("scope_suggestion", None)

            logger.info(f"Confidence score: {result['confidence_score']}/100")
            return result

        except Exception as e:
            logger.error(f"Confidence evaluation failed: {e}", exc_info=True)
            return self._default_evaluation(f"Evaluation error: {e}")

    def run(self, **kwargs: Any) -> Dict[str, Any]:
        """Run the evaluation workflow.

        Required kwargs:
            plan_content: str
            ticket_data: dict
            analysis: dict
        """
        return self.evaluate(
            plan_content=kwargs["plan_content"],
            ticket_data=kwargs["ticket_data"],
            analysis=kwargs["analysis"],
        )

    def _extract_json_from_response(self, response: str) -> Dict[str, Any] | None:
        """Extract JSON object from LLM response.

        Handles various formats:
        - Pure JSON response
        - JSON wrapped in markdown code blocks
        - JSON with surrounding text
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

    def _default_evaluation(self, reason: str) -> Dict[str, Any]:
        """Return a conservative default evaluation that triggers triage."""
        return {
            "confidence_score": 0,
            "gaps": [reason],
            "assumptions": [],
            "questions": ["Could not evaluate plan automatically — please review manually."],
            "invest_evaluation": {
                "independent": {"score": 0, "note": "Unable to evaluate"},
                "negotiable": {"score": 0, "note": "Unable to evaluate"},
                "valuable": {"score": 0, "note": "Unable to evaluate"},
                "estimatable": {"score": 0, "note": "Unable to evaluate"},
                "small": {"score": 0, "note": "Unable to evaluate"},
                "testable": {"score": 0, "note": "Unable to evaluate"},
            },
            "summary": reason[:150],
            "scope_suggestion": None,
        }

    def _default_field(self, field: str) -> Any:
        """Return a safe default for a missing evaluation field."""
        defaults: Dict[str, Any] = {
            "confidence_score": 0,
            "gaps": [],
            "assumptions": [],
            "questions": [],
            "invest_evaluation": {
                "independent": {"score": 0, "note": "Not evaluated"},
                "negotiable": {"score": 0, "note": "Not evaluated"},
                "valuable": {"score": 0, "note": "Not evaluated"},
                "estimatable": {"score": 0, "note": "Not evaluated"},
                "small": {"score": 0, "note": "Not evaluated"},
                "testable": {"score": 0, "note": "Not evaluated"},
            },
            "summary": "Incomplete evaluation",
        }
        return defaults.get(field)
