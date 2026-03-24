"""Factory for creating the appropriate Jira client based on configuration."""

from typing import Union

from src.config_loader import get_config
from src.jira_client import JiraClient
from src.jira_server_client import JiraServerClient

JiraClientType = Union[JiraClient, JiraServerClient]


def get_jira_client() -> JiraClientType:
    """Create the appropriate Jira client based on configuration.

    Checks JIRA_MODE environment variable:
    - "server" or "datacenter": Returns JiraServerClient (API v2 + PAT auth)
    - "cloud" or unset: Returns JiraClient (API v3 + basic auth)

    Returns:
        JiraClient or JiraServerClient instance
    """
    config = get_config()
    jira_mode = (config.get_env("JIRA_MODE", "cloud") or "cloud").lower().strip()

    if jira_mode in ("server", "datacenter"):
        return JiraServerClient()
    return JiraClient()
