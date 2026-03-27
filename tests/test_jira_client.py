"""Unit tests for JiraClient."""

from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from src.config_loader import ConfigLoader
from src.jira_client import JiraClient


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    config = MagicMock(spec=ConfigLoader)
    config.get_jira_config.return_value = {
        "base_url": "https://test.atlassian.net",
        "email": "test@example.com",
        "api_token": "test_token",
    }
    return config


@pytest.fixture
def jira_client(mock_config):
    """Create a JiraClient instance with mocked config."""
    with patch("src.jira_client.get_config", return_value=mock_config):
        client = JiraClient()
        return client


class TestJiraClientInit:
    """Test JiraClient initialization."""

    def test_init_success(self, mock_config):
        """Test successful initialization."""
        with patch("src.jira_client.get_config", return_value=mock_config):
            client = JiraClient()
            assert client.base_url == "https://test.atlassian.net"
            assert client.email == "test@example.com"
            assert client.api_token == "test_token"
            assert client.session.auth == ("test@example.com", "test_token")

    def test_init_missing_config(self):
        """Test initialization with missing configuration."""
        config = MagicMock(spec=ConfigLoader)
        config.get_jira_config.return_value = {
            "base_url": "",
            "email": "",
            "api_token": "",
        }

        with patch("src.jira_client.get_config", return_value=config):
            with pytest.raises(ValueError, match="Jira configuration incomplete"):
                JiraClient()

    def test_init_strips_trailing_slash(self):
        """Test that base URL trailing slashes are removed."""
        config = MagicMock(spec=ConfigLoader)
        config.get_jira_config.return_value = {
            "base_url": "https://test.atlassian.net/",
            "email": "test@example.com",
            "api_token": "test_token",
        }

        with patch("src.jira_client.get_config", return_value=config):
            client = JiraClient()
            assert client.base_url == "https://test.atlassian.net"


class TestGetTicket:
    """Test get_ticket method."""

    def test_get_ticket_success(self, jira_client):
        """Test successfully fetching a ticket."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "key": "ACME-123",
            "fields": {
                "summary": "Test Issue",
                "description": "Test description",
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "assignee": {"displayName": "John Doe"},
                "created": "2024-01-01T00:00:00.000Z",
                "updated": "2024-01-02T00:00:00.000Z",
            }
        }

        with patch.object(jira_client.session, "get", return_value=mock_response):
            result = jira_client.get_ticket("ACME-123")

            assert result["key"] == "ACME-123"
            assert result["summary"] == "Test Issue"
            assert result["description"] == "Test description"
            assert result["status"] == "In Progress"
            assert result["priority"] == "High"
            assert result["assignee"] == "John Doe"
            assert result["created"] == "2024-01-01T00:00:00.000Z"
            assert result["updated"] == "2024-01-02T00:00:00.000Z"
            assert result["attachments"] == []

    def test_get_ticket_with_attachments(self, jira_client):
        """Test that attachments are included in ticket data."""
        attachment_data = [
            {"id": "1", "filename": "spec.md", "size": 1024, "mimeType": "text/markdown",
             "content": "https://example.com/attachments/spec.md"},
        ]
        mock_response = Mock()
        mock_response.json.return_value = {
            "key": "ACME-123",
            "fields": {
                "summary": "Test",
                "status": {"name": "Open"},
                "attachment": attachment_data,
            }
        }

        with patch.object(jira_client.session, "get", return_value=mock_response):
            result = jira_client.get_ticket("ACME-123")
            assert result["attachments"] == attachment_data

    def test_get_ticket_no_assignee(self, jira_client):
        """Test fetching a ticket with no assignee."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "key": "ACME-123",
            "fields": {
                "summary": "Test Issue",
                "description": "Test description",
                "status": {"name": "Open"},
                "priority": {"name": "Medium"},
                "assignee": None,
                "created": "2024-01-01T00:00:00.000Z",
                "updated": "2024-01-02T00:00:00.000Z",
            }
        }

        with patch.object(jira_client.session, "get", return_value=mock_response):
            result = jira_client.get_ticket("ACME-123")

            assert result["assignee"] is None

    def test_get_ticket_api_error(self, jira_client):
        """Test handling API errors."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")

        with patch.object(jira_client.session, "get", return_value=mock_response):
            with pytest.raises(requests.HTTPError):
                jira_client.get_ticket("ACME-999")

    def test_get_ticket_includes_raw(self, jira_client):
        """Test that raw response is included."""
        mock_data = {
            "key": "ACME-123",
            "fields": {
                "summary": "Test",
                "status": {"name": "Open"},
            }
        }
        mock_response = Mock()
        mock_response.json.return_value = mock_data

        with patch.object(jira_client.session, "get", return_value=mock_response):
            result = jira_client.get_ticket("ACME-123")

            assert result["raw"] == mock_data


class TestAddComment:
    """Test add_comment method."""

    def test_add_comment_success(self, jira_client):
        """Test adding a comment successfully."""
        mock_response = Mock()
        mock_response.json.return_value = {"id": "10001"}

        with patch.object(jira_client.session, "post", return_value=mock_response) as mock_post:
            result = jira_client.add_comment("ACME-123", "Test comment")

            assert result == {"id": "10001"}
            mock_post.assert_called_once()

            # Verify payload structure
            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            assert payload["body"]["type"] == "doc"
            assert payload["body"]["content"][0]["content"][0]["text"] == "Test comment"

    def test_add_comment_api_error(self, jira_client):
        """Test handling API errors when adding comment."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")

        with patch.object(jira_client.session, "post", return_value=mock_response):
            with pytest.raises(requests.HTTPError):
                jira_client.add_comment("ACME-123", "Test")


class TestUpdateStatus:
    """Test update_status method."""

    def test_update_status_success(self, jira_client):
        """Test updating status successfully."""
        # Mock get transitions response
        transitions_response = Mock()
        transitions_response.json.return_value = {
            "transitions": [
                {"id": "11", "to": {"name": "In Progress"}},
                {"id": "21", "to": {"name": "Done"}},
            ]
        }

        # Mock post transition response
        post_response = Mock()
        post_response.json.return_value = {}

        with patch.object(
            jira_client.session, "get", return_value=transitions_response
        ):
            with patch.object(
                jira_client.session, "post", return_value=post_response
            ) as mock_post:
                result = jira_client.update_status("ACME-123", "Done")

                assert result == {}
                # Verify correct transition ID was used
                call_args = mock_post.call_args
                payload = call_args.kwargs["json"]
                assert payload["transition"]["id"] == "21"

    def test_update_status_case_insensitive(self, jira_client):
        """Test status matching is case-insensitive."""
        transitions_response = Mock()
        transitions_response.json.return_value = {
            "transitions": [
                {"id": "11", "to": {"name": "In Progress"}},
            ]
        }

        post_response = Mock()
        post_response.json.return_value = {}

        with patch.object(
            jira_client.session, "get", return_value=transitions_response
        ):
            with patch.object(
                jira_client.session, "post", return_value=post_response
            ) as mock_post:
                jira_client.update_status("ACME-123", "in progress")

                call_args = mock_post.call_args
                payload = call_args.kwargs["json"]
                assert payload["transition"]["id"] == "11"

    def test_update_status_invalid_transition(self, jira_client):
        """Test handling invalid transition."""
        transitions_response = Mock()
        transitions_response.json.return_value = {
            "transitions": [
                {"id": "11", "to": {"name": "In Progress"}},
            ]
        }

        with patch.object(
            jira_client.session, "get", return_value=transitions_response
        ):
            with pytest.raises(ValueError, match="Cannot transition to"):
                jira_client.update_status("ACME-123", "Invalid Status")


class TestSearchTickets:
    """Test search_tickets method."""

    def test_search_tickets_success(self, jira_client):
        """Test searching tickets successfully."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "issues": [
                {
                    "key": "ACME-123",
                    "fields": {
                        "summary": "First issue",
                        "status": {"name": "Open"},
                        "priority": {"name": "High"},
                        "assignee": {"displayName": "John Doe"},
                    }
                },
                {
                    "key": "ACME-124",
                    "fields": {
                        "summary": "Second issue",
                        "status": {"name": "In Progress"},
                        "priority": {"name": "Medium"},
                        "assignee": None,
                    }
                }
            ]
        }

        with patch.object(jira_client.session, "post", return_value=mock_response) as mock_post:
            result = jira_client.search_tickets("project = ACME")

            assert len(result) == 2
            assert result[0]["key"] == "ACME-123"
            assert result[0]["summary"] == "First issue"
            assert result[1]["assignee"] is None

            # Verify JQL was passed
            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            assert payload["jql"] == "project = ACME"

    def test_search_tickets_custom_fields(self, jira_client):
        """Test searching with custom fields."""
        mock_response = Mock()
        mock_response.json.return_value = {"issues": []}

        with patch.object(jira_client.session, "post", return_value=mock_response) as mock_post:
            jira_client.search_tickets(
                "project = ACME",
                fields=["summary", "description"]
            )

            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            assert payload["fields"] == ["summary", "description"]

    def test_search_tickets_max_results(self, jira_client):
        """Test search with custom max_results."""
        mock_response = Mock()
        mock_response.json.return_value = {"issues": []}

        with patch.object(jira_client.session, "post", return_value=mock_response) as mock_post:
            jira_client.search_tickets("project = ACME", max_results=100)

            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            assert payload["maxResults"] == 100

    def test_search_tickets_empty_results(self, jira_client):
        """Test search with no results."""
        mock_response = Mock()
        mock_response.json.return_value = {"issues": []}

        with patch.object(jira_client.session, "post", return_value=mock_response):
            result = jira_client.search_tickets("project = NONEXISTENT")

            assert result == []


class TestGetTicketComments:
    """Test get_ticket_comments method."""

    def test_get_ticket_comments_success(self, jira_client):
        """Test getting comments successfully."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "comments": [
                {
                    "author": {"displayName": "John Doe"},
                    "created": "2024-01-01T00:00:00.000Z",
                    "body": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": "First comment"}
                                ]
                            }
                        ]
                    }
                },
                {
                    "author": {"displayName": "Jane Smith"},
                    "created": "2024-01-02T00:00:00.000Z",
                    "body": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": "Second comment"}
                                ]
                            }
                        ]
                    }
                }
            ]
        }

        with patch.object(jira_client.session, "get", return_value=mock_response):
            result = jira_client.get_ticket_comments("ACME-123")

            assert len(result) == 2
            assert result[0]["author"] == "John Doe"
            assert result[0]["body"] == "First comment"
            assert result[1]["author"] == "Jane Smith"
            assert result[1]["body"] == "Second comment"

    def test_get_ticket_comments_unknown_author(self, jira_client):
        """Test handling comments with missing author."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "comments": [
                {
                    "author": {},
                    "created": "2024-01-01T00:00:00.000Z",
                    "body": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": "Test"}
                                ]
                            }
                        ]
                    }
                }
            ]
        }

        with patch.object(jira_client.session, "get", return_value=mock_response):
            result = jira_client.get_ticket_comments("ACME-123")

            assert result[0]["author"] == "Unknown"

    def test_get_ticket_comments_multiple_paragraphs(self, jira_client):
        """Test parsing comments with multiple paragraphs."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "comments": [
                {
                    "author": {"displayName": "John Doe"},
                    "created": "2024-01-01T00:00:00.000Z",
                    "body": {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": "First paragraph"}
                                ]
                            },
                            {
                                "type": "paragraph",
                                "content": [
                                    {"type": "text", "text": "Second paragraph"}
                                ]
                            }
                        ]
                    }
                }
            ]
        }

        with patch.object(jira_client.session, "get", return_value=mock_response):
            result = jira_client.get_ticket_comments("ACME-123")

            assert result[0]["body"] == "First paragraph Second paragraph"

    def test_get_ticket_comments_empty(self, jira_client):
        """Test getting comments when none exist."""
        mock_response = Mock()
        mock_response.json.return_value = {"comments": []}

        with patch.object(jira_client.session, "get", return_value=mock_response):
            result = jira_client.get_ticket_comments("ACME-123")

            assert result == []

    def test_get_ticket_comments_includes_raw(self, jira_client):
        """Test that raw comment data is included."""
        comment_data = {
            "author": {"displayName": "John Doe"},
            "created": "2024-01-01T00:00:00.000Z",
            "body": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Test"}]
                    }
                ]
            }
        }
        mock_response = Mock()
        mock_response.json.return_value = {"comments": [comment_data]}

        with patch.object(jira_client.session, "get", return_value=mock_response):
            result = jira_client.get_ticket_comments("ACME-123")

            assert result[0]["raw"] == comment_data
