"""Unit tests for Jira client factory."""

from unittest.mock import MagicMock, patch

import pytest

from src.config_loader import ConfigLoader
from src.jira_client import JiraClient
from src.jira_factory import get_jira_client
from src.jira_server_client import JiraServerClient


@pytest.fixture
def mock_config():
    """Create a mock configuration."""
    config = MagicMock(spec=ConfigLoader)
    config.get_jira_config.return_value = {
        "base_url": "https://jira.example.com",
        "email": "test@example.com",
        "api_token": "test_token",
    }
    return config


class TestGetJiraClient:
    """Test get_jira_client factory function."""

    def test_returns_cloud_client_by_default(self, mock_config):
        """Test that cloud client is returned when JIRA_MODE is unset."""
        mock_config.get_env.return_value = None

        with patch("src.jira_factory.get_config", return_value=mock_config):
            with patch("src.jira_client.get_config", return_value=mock_config):
                client = get_jira_client()
                assert isinstance(client, JiraClient)

    def test_returns_cloud_client_when_mode_cloud(self, mock_config):
        """Test that cloud client is returned when JIRA_MODE=cloud."""
        mock_config.get_env.return_value = "cloud"

        with patch("src.jira_factory.get_config", return_value=mock_config):
            with patch("src.jira_client.get_config", return_value=mock_config):
                client = get_jira_client()
                assert isinstance(client, JiraClient)

    def test_returns_server_client_when_mode_server(self, mock_config):
        """Test that server client is returned when JIRA_MODE=server."""
        mock_config.get_env.side_effect = lambda key, default=None: "server" if key == "JIRA_MODE" else default

        with patch("src.jira_factory.get_config", return_value=mock_config):
            with patch("src.jira_server_client.get_config", return_value=mock_config):
                client = get_jira_client()
                assert isinstance(client, JiraServerClient)

    def test_returns_server_client_when_mode_datacenter(self, mock_config):
        """Test that server client is returned when JIRA_MODE=datacenter."""
        mock_config.get_env.side_effect = lambda key, default=None: "datacenter" if key == "JIRA_MODE" else default

        with patch("src.jira_factory.get_config", return_value=mock_config):
            with patch("src.jira_server_client.get_config", return_value=mock_config):
                client = get_jira_client()
                assert isinstance(client, JiraServerClient)

    def test_mode_is_case_insensitive(self, mock_config):
        """Test that JIRA_MODE is case-insensitive."""
        mock_config.get_env.side_effect = lambda key, default=None: "SERVER" if key == "JIRA_MODE" else default

        with patch("src.jira_factory.get_config", return_value=mock_config):
            with patch("src.jira_server_client.get_config", return_value=mock_config):
                client = get_jira_client()
                assert isinstance(client, JiraServerClient)
