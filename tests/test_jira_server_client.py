"""Unit tests for JiraServerClient."""

from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from src.config_loader import ConfigLoader
from src.jira_server_client import JiraServerClient


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    config = MagicMock(spec=ConfigLoader)
    config.get_jira_config.return_value = {
        "base_url": "https://jira.mycompany.com",
        "email": "ignored@example.com",
        "api_token": "test_pat_token",
    }
    return config


@pytest.fixture
def jira_server_client(mock_config):
    """Create a JiraServerClient instance with mocked config."""
    with patch("src.jira_server_client.get_config", return_value=mock_config):
        client = JiraServerClient()
        return client


class TestJiraServerClientInit:
    """Test JiraServerClient initialization."""

    def test_init_success(self, mock_config):
        """Test successful initialization with PAT auth."""
        with patch("src.jira_server_client.get_config", return_value=mock_config):
            client = JiraServerClient()
            assert client.base_url == "https://jira.mycompany.com"
            assert client.api_token == "test_pat_token"
            assert client.session.headers["Authorization"] == "Bearer test_pat_token"

    def test_init_no_basic_auth(self, mock_config):
        """Test that basic auth is not used (PAT uses Bearer header instead)."""
        with patch("src.jira_server_client.get_config", return_value=mock_config):
            client = JiraServerClient()
            assert client.session.auth is None

    def test_init_missing_config(self):
        """Test initialization with missing configuration."""
        config = MagicMock(spec=ConfigLoader)
        config.get_jira_config.return_value = {
            "base_url": "",
            "email": "",
            "api_token": "",
        }

        with patch("src.jira_server_client.get_config", return_value=config):
            with pytest.raises(ValueError, match="Jira Server configuration incomplete"):
                JiraServerClient()

    def test_init_strips_trailing_slash(self):
        """Test that base URL trailing slashes are removed."""
        config = MagicMock(spec=ConfigLoader)
        config.get_jira_config.return_value = {
            "base_url": "https://jira.mycompany.com/",
            "email": "",
            "api_token": "test_token",
        }

        with patch("src.jira_server_client.get_config", return_value=config):
            client = JiraServerClient()
            assert client.base_url == "https://jira.mycompany.com"


class TestGetTicket:
    """Test get_ticket method."""

    def test_get_ticket_success(self, jira_server_client):
        """Test successfully fetching a ticket."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "key": "ACME-123",
            "fields": {
                "summary": "Test Issue",
                "description": "Plain text description",
                "status": {"name": "In Progress"},
                "priority": {"name": "High"},
                "assignee": {"displayName": "John Doe"},
                "created": "2024-01-01T00:00:00.000+0000",
                "updated": "2024-01-02T00:00:00.000+0000",
            }
        }

        with patch.object(jira_server_client.session, "get", return_value=mock_response) as mock_get:
            result = jira_server_client.get_ticket("ACME-123")

            assert result["key"] == "ACME-123"
            assert result["summary"] == "Test Issue"
            assert result["description"] == "Plain text description"
            assert result["status"] == "In Progress"
            assert result["priority"] == "High"
            assert result["assignee"] == "John Doe"

            # Verify API v2 endpoint
            call_url = mock_get.call_args[0][0]
            assert "/rest/api/2/" in call_url

    def test_get_ticket_no_assignee(self, jira_server_client):
        """Test fetching a ticket with no assignee."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "key": "ACME-123",
            "fields": {
                "summary": "Test Issue",
                "description": "desc",
                "status": {"name": "Open"},
                "priority": {"name": "Medium"},
                "assignee": None,
                "created": "2024-01-01T00:00:00.000+0000",
                "updated": "2024-01-02T00:00:00.000+0000",
            }
        }

        with patch.object(jira_server_client.session, "get", return_value=mock_response):
            result = jira_server_client.get_ticket("ACME-123")
            assert result["assignee"] is None

    def test_get_ticket_not_found(self, jira_server_client):
        """Test handling 404 error."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = requests.HTTPError("404 Not Found")

        with patch.object(jira_server_client.session, "get", return_value=mock_response):
            with pytest.raises(ValueError, match="not found"):
                jira_server_client.get_ticket("ACME-999")

    def test_get_ticket_includes_raw(self, jira_server_client):
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

        with patch.object(jira_server_client.session, "get", return_value=mock_response):
            result = jira_server_client.get_ticket("ACME-123")
            assert result["raw"] == mock_data


class TestAddComment:
    """Test add_comment method."""

    def test_add_comment_plain_text(self, jira_server_client):
        """Test adding a plain text comment (not ADF)."""
        mock_response = Mock()
        mock_response.json.return_value = {"id": "10001"}

        with patch.object(jira_server_client.session, "post", return_value=mock_response) as mock_post:
            result = jira_server_client.add_comment("ACME-123", "Test comment")

            assert result == {"id": "10001"}
            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            # API v2 uses plain text body, not ADF
            assert payload["body"] == "Test comment"
            assert "type" not in payload.get("body", "")

    def test_add_comment_with_link(self, jira_server_client):
        """Test adding a comment with a wiki markup link."""
        mock_response = Mock()
        mock_response.json.return_value = {"id": "10002"}

        with patch.object(jira_server_client.session, "post", return_value=mock_response) as mock_post:
            result = jira_server_client.add_comment(
                "ACME-123", "See MR: ", link_text="MR !42", link_url="https://gitlab.com/mr/42"
            )

            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            assert "[MR !42|https://gitlab.com/mr/42]" in payload["body"]

    def test_add_comment_link_text_without_url(self, jira_server_client):
        """Test that link_text without link_url raises ValueError."""
        with pytest.raises(ValueError, match="link_url must be provided"):
            jira_server_client.add_comment("ACME-123", "Test", link_text="link")

    def test_add_comment_uses_api_v2(self, jira_server_client):
        """Test that comment endpoint uses API v2."""
        mock_response = Mock()
        mock_response.json.return_value = {"id": "10001"}

        with patch.object(jira_server_client.session, "post", return_value=mock_response) as mock_post:
            jira_server_client.add_comment("ACME-123", "Test")
            call_url = mock_post.call_args[0][0]
            assert "/rest/api/2/" in call_url

    def test_add_comment_api_error(self, jira_server_client):
        """Test handling API errors when adding comment."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("403 Forbidden")

        with patch.object(jira_server_client.session, "post", return_value=mock_response):
            with pytest.raises(requests.HTTPError):
                jira_server_client.add_comment("ACME-123", "Test")


class TestUpdateStatus:
    """Test update_status method."""

    def test_update_status_success(self, jira_server_client):
        """Test updating status successfully."""
        transitions_response = Mock()
        transitions_response.json.return_value = {
            "transitions": [
                {"id": "11", "to": {"name": "In Progress"}},
                {"id": "21", "to": {"name": "Done"}},
            ]
        }

        post_response = Mock()
        post_response.content = b""

        with patch.object(
            jira_server_client.session, "get", return_value=transitions_response
        ):
            with patch.object(
                jira_server_client.session, "post", return_value=post_response
            ) as mock_post:
                result = jira_server_client.update_status("ACME-123", "Done")

                assert result == {}
                call_args = mock_post.call_args
                payload = call_args.kwargs["json"]
                assert payload["transition"]["id"] == "21"

    def test_update_status_case_insensitive(self, jira_server_client):
        """Test status matching is case-insensitive."""
        transitions_response = Mock()
        transitions_response.json.return_value = {
            "transitions": [
                {"id": "11", "to": {"name": "In Progress"}},
            ]
        }

        post_response = Mock()
        post_response.content = b""

        with patch.object(
            jira_server_client.session, "get", return_value=transitions_response
        ):
            with patch.object(
                jira_server_client.session, "post", return_value=post_response
            ) as mock_post:
                jira_server_client.update_status("ACME-123", "in progress")
                call_args = mock_post.call_args
                payload = call_args.kwargs["json"]
                assert payload["transition"]["id"] == "11"

    def test_update_status_invalid_transition(self, jira_server_client):
        """Test handling invalid transition."""
        transitions_response = Mock()
        transitions_response.json.return_value = {
            "transitions": [
                {"id": "11", "to": {"name": "In Progress"}},
            ]
        }

        with patch.object(
            jira_server_client.session, "get", return_value=transitions_response
        ):
            with pytest.raises(ValueError, match="Cannot transition to"):
                jira_server_client.update_status("ACME-123", "Invalid Status")


class TestSearchTickets:
    """Test search_tickets method."""

    def test_search_tickets_success(self, jira_server_client):
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

        with patch.object(jira_server_client.session, "post", return_value=mock_response) as mock_post:
            result = jira_server_client.search_tickets("project = ACME")

            assert len(result) == 2
            assert result[0]["key"] == "ACME-123"
            assert result[1]["assignee"] is None

            # Verify API v2 endpoint
            call_url = mock_post.call_args[0][0]
            assert "/rest/api/2/" in call_url

    def test_search_tickets_empty(self, jira_server_client):
        """Test search with no results."""
        mock_response = Mock()
        mock_response.json.return_value = {"issues": []}

        with patch.object(jira_server_client.session, "post", return_value=mock_response):
            result = jira_server_client.search_tickets("project = NONEXISTENT")
            assert result == []


class TestCreateTicket:
    """Test create_ticket method."""

    def test_create_ticket_plain_text(self, jira_server_client):
        """Test creating a ticket with plain text description (not ADF)."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "10001",
            "key": "ACME-125",
            "self": "https://jira.mycompany.com/rest/api/2/issue/10001",
        }

        with patch.object(jira_server_client.session, "post", return_value=mock_response) as mock_post:
            result = jira_server_client.create_ticket(
                "ACME", "New feature", "Implement the thing"
            )

            assert result["key"] == "ACME-125"

            call_args = mock_post.call_args
            payload = call_args.kwargs["json"]
            # API v2 uses plain text, not ADF
            assert payload["fields"]["description"] == "Implement the thing"
            assert isinstance(payload["fields"]["description"], str)

    def test_create_ticket_uses_api_v2(self, jira_server_client):
        """Test that create endpoint uses API v2."""
        mock_response = Mock()
        mock_response.json.return_value = {"id": "10001", "key": "ACME-125"}

        with patch.object(jira_server_client.session, "post", return_value=mock_response) as mock_post:
            jira_server_client.create_ticket("ACME", "Test", "Desc")
            call_url = mock_post.call_args[0][0]
            assert "/rest/api/2/" in call_url


class TestGetTicketComments:
    """Test get_ticket_comments method."""

    def test_get_comments_plain_text(self, jira_server_client):
        """Test getting comments that are plain text (not ADF)."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "comments": [
                {
                    "author": {"displayName": "John Doe"},
                    "created": "2024-01-01T00:00:00.000+0000",
                    "body": "This is a plain text comment"
                },
                {
                    "author": {"displayName": "Jane Smith"},
                    "created": "2024-01-02T00:00:00.000+0000",
                    "body": "Another comment"
                }
            ]
        }

        with patch.object(jira_server_client.session, "get", return_value=mock_response):
            result = jira_server_client.get_ticket_comments("ACME-123")

            assert len(result) == 2
            assert result[0]["author"] == "John Doe"
            assert result[0]["body"] == "This is a plain text comment"
            assert result[1]["body"] == "Another comment"

    def test_get_comments_unknown_author(self, jira_server_client):
        """Test handling comments with missing author."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "comments": [
                {
                    "author": {},
                    "created": "2024-01-01T00:00:00.000+0000",
                    "body": "Test"
                }
            ]
        }

        with patch.object(jira_server_client.session, "get", return_value=mock_response):
            result = jira_server_client.get_ticket_comments("ACME-123")
            assert result[0]["author"] == "Unknown"

    def test_get_comments_empty(self, jira_server_client):
        """Test getting comments when none exist."""
        mock_response = Mock()
        mock_response.json.return_value = {"comments": []}

        with patch.object(jira_server_client.session, "get", return_value=mock_response):
            result = jira_server_client.get_ticket_comments("ACME-123")
            assert result == []

    def test_get_comments_includes_raw(self, jira_server_client):
        """Test that raw comment data is included."""
        comment_data = {
            "author": {"displayName": "John Doe"},
            "created": "2024-01-01T00:00:00.000+0000",
            "body": "Test comment"
        }
        mock_response = Mock()
        mock_response.json.return_value = {"comments": [comment_data]}

        with patch.object(jira_server_client.session, "get", return_value=mock_response):
            result = jira_server_client.get_ticket_comments("ACME-123")
            assert result[0]["raw"] == comment_data
