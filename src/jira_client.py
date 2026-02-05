"""Jira REST API client for ticket management."""

from typing import Any, Dict, List, Optional

import requests
from requests.exceptions import HTTPError

from src.config_loader import get_config


class JiraClient:
    """Client for interacting with Jira Cloud REST API."""

    def __init__(self) -> None:
        """Initialize Jira client."""
        self.config = get_config()
        jira_config = self.config.get_jira_config()

        self.base_url = jira_config["base_url"].rstrip("/")
        self.email = jira_config["email"]
        self.api_token = jira_config["api_token"]

        if not self.base_url or not self.email or not self.api_token:
            raise ValueError(
                "Jira configuration incomplete. Set JIRA_API_TOKEN, JIRA_EMAIL, "
                "and jira.base_url in config.yaml"
            )

        self.session = requests.Session()
        self.session.auth = (self.email, self.api_token)
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def get_ticket(self, ticket_id: str) -> Dict[str, Any]:
        """Fetch a Jira ticket by ID.

        Args:
            ticket_id: Ticket ID (e.g., "ACME-123")

        Returns:
            Dictionary containing ticket data with keys:
                - key: Ticket ID
                - summary: Ticket summary/title
                - description: Ticket description
                - status: Current status
                - priority: Priority
                - assignee: Assignee name (if any)
                - created: Creation timestamp
                - updated: Last update timestamp

        Raises:
            requests.HTTPError: If API request fails
        """
        url = f"{self.base_url}/rest/api/3/issue/{ticket_id}"
        params = {
            "fields": "summary,description,status,priority,assignee,created,updated,comment"
        }

        response = self.session.get(url, params=params)
        try:
            response.raise_for_status()
        except HTTPError:
            if response.status_code == 404:
                raise ValueError(f"Jira ticket '{ticket_id}' not found")
            raise

        data = response.json()

        # Extract and normalize fields
        fields = data.get("fields", {})

        return {
            "key": data.get("key", ticket_id),
            "summary": fields.get("summary", ""),
            "description": fields.get("description", ""),
            "status": fields.get("status", {}).get("name", ""),
            "priority": fields.get("priority", {}).get("name", ""),
            "assignee": fields.get("assignee", {}).get("displayName") if fields.get("assignee") else None,
            "created": fields.get("created", ""),
            "updated": fields.get("updated", ""),
            "raw": data,  # Include raw response for advanced usage
        }

    def add_comment(
        self,
        ticket_id: str,
        comment: str,
        link_text: Optional[str] = None,
        link_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a comment to a Jira ticket.

        Args:
            ticket_id: Ticket ID
            comment: Comment text (supports Jira markdown)
            link_text: Optional text to display as a clickable link
            link_url: Optional URL for the link (required if link_text is provided)

        Returns:
            Comment data from API response

        Raises:
            requests.HTTPError: If API request fails
            ValueError: If link_text is provided without link_url
        """
        if link_text and not link_url:
            raise ValueError("link_url must be provided when link_text is specified")

        url = f"{self.base_url}/rest/api/3/issue/{ticket_id}/comment"

        # Build content nodes for the paragraph
        content_nodes: List[Dict[str, Any]] = []

        # Add main comment text
        content_nodes.append({
            "type": "text",
            "text": comment
        })

        # Add link if provided
        if link_text and link_url:
            content_nodes.append({
                "type": "text",
                "text": link_text,
                "marks": [
                    {
                        "type": "link",
                        "attrs": {
                            "href": link_url
                        }
                    }
                ]
            })

        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": content_nodes
                    }
                ]
            }
        }

        response = self.session.post(url, json=payload)
        response.raise_for_status()

        result: Dict[str, Any] = response.json()
        return result

    def update_status(self, ticket_id: str, status: str) -> Dict[str, Any]:
        """Update the status of a Jira ticket.

        Args:
            ticket_id: Ticket ID
            status: Target status name (e.g., "In Progress", "Done")

        Returns:
            Response data from transition

        Raises:
            requests.HTTPError: If API request fails
            ValueError: If transition to target status is not available
        """
        # Get available transitions
        transitions_url = f"{self.base_url}/rest/api/3/issue/{ticket_id}/transitions"
        response = self.session.get(transitions_url)
        response.raise_for_status()

        transitions = response.json().get("transitions", [])

        # Find transition matching target status
        transition_id = None
        for transition in transitions:
            if transition.get("to", {}).get("name", "").lower() == status.lower():
                transition_id = transition.get("id")
                break

        if transition_id is None:
            available = [t.get("to", {}).get("name") for t in transitions]
            raise ValueError(
                f"Cannot transition to '{status}'. Available transitions: {available}"
            )

        # Execute transition
        payload = {"transition": {"id": transition_id}}
        response = self.session.post(transitions_url, json=payload)
        response.raise_for_status()

        result: Dict[str, Any] = response.json()
        return result

    def search_tickets(
        self,
        jql: str,
        max_results: int = 50,
        fields: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Search for tickets using JQL (Jira Query Language).

        Args:
            jql: JQL query string
            max_results: Maximum number of results to return
            fields: List of fields to include (None for default fields)

        Returns:
            List of ticket dictionaries

        Raises:
            requests.HTTPError: If API request fails
        """
        url = f"{self.base_url}/rest/api/3/search"

        if fields is None:
            fields = ["summary", "status", "priority", "assignee"]

        payload = {
            "jql": jql,
            "maxResults": max_results,
            "fields": fields,
        }

        response = self.session.post(url, json=payload)
        response.raise_for_status()

        data = response.json()
        issues = data.get("issues", [])

        # Normalize results
        results = []
        for issue in issues:
            fields_data = issue.get("fields", {})
            results.append({
                "key": issue.get("key", ""),
                "summary": fields_data.get("summary", ""),
                "status": fields_data.get("status", {}).get("name", ""),
                "priority": fields_data.get("priority", {}).get("name", ""),
                "assignee": fields_data.get("assignee", {}).get("displayName") if fields_data.get("assignee") else None,
                "raw": issue,
            })

        return results

    def create_ticket(
        self,
        project_key: str,
        summary: str,
        description: str,
        issue_type: str = "Task",
        priority: str = "High"
    ) -> Dict[str, Any]:
        """Create a new Jira ticket.

        Args:
            project_key: Project key (e.g., "ACME", "SENTEST")
            summary: Ticket summary/title
            description: Ticket description (plain text)
            issue_type: Issue type (default: "Task")
            priority: Priority (default: "High")

        Returns:
            Created ticket data with key, id, and self URL

        Raises:
            requests.HTTPError: If API request fails
        """
        url = f"{self.base_url}/rest/api/3/issue"

        # Convert description to Atlassian Document Format
        payload = {
            "fields": {
                "project": {
                    "key": project_key
                },
                "summary": summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": description
                                }
                            ]
                        }
                    ]
                },
                "issuetype": {
                    "name": issue_type
                },
                "priority": {
                    "name": priority
                }
            }
        }

        response = self.session.post(url, json=payload)
        response.raise_for_status()

        result: Dict[str, Any] = response.json()
        return result

    def get_ticket_comments(self, ticket_id: str) -> List[Dict[str, Any]]:
        """Get all comments for a ticket.

        Args:
            ticket_id: Ticket ID

        Returns:
            List of comment dictionaries with author, created, and body

        Raises:
            requests.HTTPError: If API request fails
        """
        url = f"{self.base_url}/rest/api/3/issue/{ticket_id}/comment"

        response = self.session.get(url)
        response.raise_for_status()

        data = response.json()
        comments = data.get("comments", [])

        # Extract relevant comment data
        results = []
        for comment in comments:
            author = comment.get("author", {}).get("displayName", "Unknown")
            created = comment.get("created", "")

            # Extract text from Atlassian Document Format
            body_content = comment.get("body", {}).get("content", [])
            text_parts = []
            for content_block in body_content:
                if content_block.get("type") == "paragraph":
                    for text_node in content_block.get("content", []):
                        if text_node.get("type") == "text":
                            text_parts.append(text_node.get("text", ""))

            results.append({
                "author": author,
                "created": created,
                "body": " ".join(text_parts),
                "raw": comment,
            })

        return results
