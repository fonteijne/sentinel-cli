"""Shared ticket context builder for Jira data fetching and formatting.

Fetches ticket data and comments once, caches the results, and provides
consistent parsing and formatting for LLM prompts. Used by plan_generator
and functional_debrief agents.
"""

from typing import Any, Dict

from src.jira_factory import JiraClientType
from src.utils.adf_parser import parse_adf_to_text


class TicketContextBuilder:
    """Fetches, caches, and formats Jira ticket data for LLM prompts.

    Instantiate once per run() call to ensure fresh data while avoiding
    redundant API calls within the same execution.
    """

    def __init__(self, jira: JiraClientType, ticket_id: str) -> None:
        self.jira = jira
        self.ticket_id = ticket_id
        self._ticket_data: Dict[str, Any] | None = None
        self._comments: list[Dict[str, Any]] | None = None

    @property
    def ticket_data(self) -> Dict[str, Any]:
        if self._ticket_data is None:
            self._ticket_data = self.jira.get_ticket(self.ticket_id)
        return self._ticket_data

    @property
    def comments(self) -> list[Dict[str, Any]]:
        if self._comments is None:
            self._comments = self.jira.get_ticket_comments(self.ticket_id)
        return self._comments

    @property
    def summary(self) -> str:
        return self.ticket_data.get("summary", "N/A")

    @property
    def description(self) -> str:
        raw = self.ticket_data.get("description", "")
        if isinstance(raw, dict):
            return parse_adf_to_text(raw)
        return str(raw)

    @property
    def type_name(self) -> str:
        val = self.ticket_data.get("issuetype")
        if val is None:
            raw = self.ticket_data.get("raw", {})
            fields = raw.get("fields", {}) if isinstance(raw, dict) else {}
            it = fields.get("issuetype", {})
            return it.get("name", "Unknown") if isinstance(it, dict) else str(it or "Unknown")
        return val.get("name", "Unknown") if isinstance(val, dict) else str(val)

    @property
    def priority_name(self) -> str:
        val = self.ticket_data.get("priority", "Medium")
        return val.get("name", "Medium") if isinstance(val, dict) else str(val)

    def format_comments(self, header: str = "**Existing Comments**:") -> str:
        if not self.comments:
            return ""
        lines = [f"- [{c['author']}]: {c['body']}" for c in self.comments]
        return f"\n{header}\n" + "\n".join(lines) + "\n"

    def format_ticket_context(self) -> str:
        return (
            f"- **Summary**: {self.summary}\n"
            f"- **Description**:\n{self.description}\n"
            f"{self.format_comments('**Comments:**')}"
        )

    def format_ticket_header(self) -> str:
        return (
            f"- **ID**: {self.ticket_id}\n"
            f"- **Summary**: {self.summary}\n"
            f"- **Type**: {self.type_name}\n"
            f"- **Priority**: {self.priority_name}"
        )
