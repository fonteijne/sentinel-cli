"""Unit tests for TicketContextBuilder."""

import pytest
from unittest.mock import MagicMock, patch

from src.ticket_context import TicketContextBuilder


def _make_jira(ticket_data=None, comments=None):
    """Create a mock Jira client."""
    jira = MagicMock()
    jira.get_ticket.return_value = ticket_data or {
        "key": "TEST-1",
        "summary": "Fix login bug",
        "description": "Users cannot log in",
        "status": "Open",
        "priority": "High",
        "assignee": "dev@example.com",
        "attachments": [],
        "raw": {},
    }
    jira.get_ticket_comments.return_value = comments if comments is not None else []
    return jira


def _make_ctx(ticket_data=None, comments=None):
    """Create a TicketContextBuilder with mock Jira."""
    jira = _make_jira(ticket_data, comments)
    return TicketContextBuilder(jira, "TEST-1"), jira


class TestCaching:
    def test_ticket_data_fetched_once(self):
        ctx, jira = _make_ctx()
        _ = ctx.ticket_data
        _ = ctx.ticket_data
        _ = ctx.summary
        jira.get_ticket.assert_called_once_with("TEST-1")

    def test_comments_fetched_once(self):
        ctx, jira = _make_ctx(comments=[{"author": "a", "body": "b"}])
        _ = ctx.comments
        _ = ctx.comments
        _ = ctx.format_comments()
        jira.get_ticket_comments.assert_called_once_with("TEST-1")

    def test_lazy_no_fetch_until_accessed(self):
        ctx, jira = _make_ctx()
        jira.get_ticket.assert_not_called()
        jira.get_ticket_comments.assert_not_called()


class TestDescription:
    def test_plain_string(self):
        ctx, _ = _make_ctx({"description": "Plain text description"})
        assert ctx.description == "Plain text description"

    def test_empty_string(self):
        ctx, _ = _make_ctx({"description": ""})
        assert ctx.description == ""

    def test_missing_key(self):
        ctx, _ = _make_ctx({"summary": "S"})
        assert ctx.description == ""

    @patch("src.ticket_context.parse_adf_to_text", return_value="Parsed ADF")
    def test_adf_dict(self, mock_parse):
        adf = {"type": "doc", "content": []}
        ctx, _ = _make_ctx({"description": adf})
        assert ctx.description == "Parsed ADF"
        mock_parse.assert_called_once_with(adf)

    def test_numeric_description_converted(self):
        ctx, _ = _make_ctx({"description": 42})
        assert ctx.description == "42"


class TestTypeName:
    def test_string_issuetype(self):
        ctx, _ = _make_ctx({"issuetype": "Bug"})
        assert ctx.type_name == "Bug"

    def test_dict_issuetype(self):
        ctx, _ = _make_ctx({"issuetype": {"name": "Story"}})
        assert ctx.type_name == "Story"

    def test_missing_falls_back_to_raw(self):
        ctx, _ = _make_ctx({
            "raw": {"fields": {"issuetype": {"name": "Epic"}}}
        })
        assert ctx.type_name == "Epic"

    def test_missing_everywhere_returns_unknown(self):
        ctx, _ = _make_ctx({"summary": "S"})
        assert ctx.type_name == "Unknown"

    def test_raw_issuetype_string(self):
        ctx, _ = _make_ctx({
            "raw": {"fields": {"issuetype": "Task"}}
        })
        assert ctx.type_name == "Task"

    def test_raw_not_dict(self):
        ctx, _ = _make_ctx({"raw": "not a dict"})
        assert ctx.type_name == "Unknown"


class TestPriorityName:
    def test_string_priority(self):
        ctx, _ = _make_ctx({"priority": "Critical"})
        assert ctx.priority_name == "Critical"

    def test_dict_priority(self):
        ctx, _ = _make_ctx({"priority": {"name": "Low"}})
        assert ctx.priority_name == "Low"

    def test_missing_defaults_to_medium(self):
        ctx, _ = _make_ctx({"summary": "S"})
        assert ctx.priority_name == "Medium"


class TestSummary:
    def test_present(self):
        ctx, _ = _make_ctx({"summary": "Fix the thing"})
        assert ctx.summary == "Fix the thing"

    def test_missing(self):
        ctx, _ = _make_ctx({"description": "d"})
        assert ctx.summary == "N/A"


class TestFormatComments:
    def test_no_comments(self):
        ctx, _ = _make_ctx(comments=[])
        assert ctx.format_comments() == ""

    def test_single_comment(self):
        ctx, _ = _make_ctx(comments=[{"author": "Alice", "body": "Looks good"}])
        result = ctx.format_comments()
        assert "- [Alice]: Looks good" in result
        assert "**Existing Comments**:" in result

    def test_multiple_comments(self):
        ctx, _ = _make_ctx(comments=[
            {"author": "Alice", "body": "First"},
            {"author": "Bob", "body": "Second"},
        ])
        result = ctx.format_comments()
        assert "- [Alice]: First" in result
        assert "- [Bob]: Second" in result

    def test_custom_header(self):
        ctx, _ = _make_ctx(comments=[{"author": "A", "body": "B"}])
        result = ctx.format_comments(header="**My Header**:")
        assert "**My Header**:" in result
        assert "**Existing Comments**:" not in result


class TestFormatTicketContext:
    def test_includes_all_sections(self):
        ctx, _ = _make_ctx(
            ticket_data={
                "summary": "Test summary",
                "description": "Test desc",
            },
            comments=[{"author": "Dev", "body": "A comment"}],
        )
        result = ctx.format_ticket_context()
        assert "- **Summary**: Test summary" in result
        assert "Test desc" in result
        assert "- [Dev]: A comment" in result

    def test_no_comments_section_when_empty(self):
        ctx, _ = _make_ctx(
            ticket_data={"summary": "S", "description": "D"},
            comments=[],
        )
        result = ctx.format_ticket_context()
        assert "**Comments**" not in result


class TestFormatTicketHeader:
    def test_all_fields(self):
        ctx, _ = _make_ctx(ticket_data={
            "summary": "My ticket",
            "issuetype": "Bug",
            "priority": "High",
        })
        result = ctx.format_ticket_header()
        assert "- **ID**: TEST-1" in result
        assert "- **Summary**: My ticket" in result
        assert "- **Type**: Bug" in result
        assert "- **Priority**: High" in result
