"""GitLab API client for merge request management."""

from typing import Any, Dict, List, Optional

import requests

from src.config_loader import get_config


class GitLabClient:
    """Client for interacting with GitLab API."""

    def __init__(self) -> None:
        """Initialize GitLab client."""
        self.config = get_config()
        gitlab_config = self.config.get_gitlab_config()

        self.base_url = gitlab_config["base_url"].rstrip("/")
        self.api_token = gitlab_config["api_token"]

        if not self.base_url or not self.api_token:
            raise ValueError(
                "GitLab configuration incomplete. Set GITLAB_API_TOKEN "
                "and gitlab.base_url in config.yaml"
            )

        self.session = requests.Session()
        self.session.headers.update({
            "PRIVATE-TOKEN": self.api_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def create_merge_request(
        self,
        project_id: str,
        title: str,
        source_branch: str,
        target_branch: str,
        description: str = "",
        draft: bool = True,
    ) -> Dict[str, Any]:
        """Create a merge request in GitLab.

        Args:
            project_id: GitLab project ID or path (e.g., "acme/backend")
            title: MR title
            source_branch: Source branch name
            target_branch: Target branch name (e.g., "main")
            description: MR description (supports markdown)
            draft: Whether to create as draft MR (default: True)

        Returns:
            Merge request data with keys:
                - iid: MR internal ID
                - web_url: MR URL
                - state: MR state
                - title: MR title

        Raises:
            requests.HTTPError: If API request fails
        """
        # Encode project ID for URL
        project_path = project_id.replace("/", "%2F")
        url = f"{self.base_url}/api/v4/projects/{project_path}/merge_requests"

        # Add draft prefix to title if needed
        mr_title = f"Draft: {title}" if draft and not title.startswith("Draft:") else title

        payload = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": mr_title,
            "description": description,
        }

        response = self.session.post(url, json=payload)
        response.raise_for_status()

        data = response.json()

        return {
            "iid": data.get("iid"),
            "web_url": data.get("web_url"),
            "state": data.get("state"),
            "title": data.get("title"),
            "raw": data,
        }

    def update_merge_request(
        self,
        project_id: str,
        mr_iid: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        state_event: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update a merge request.

        Args:
            project_id: GitLab project ID or path
            mr_iid: Merge request internal ID
            title: New title (optional)
            description: New description (optional)
            state_event: State event - "close" or "reopen" (optional)

        Returns:
            Updated merge request data

        Raises:
            requests.HTTPError: If API request fails
        """
        project_path = project_id.replace("/", "%2F")
        url = f"{self.base_url}/api/v4/projects/{project_path}/merge_requests/{mr_iid}"

        payload = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if state_event is not None:
            payload["state_event"] = state_event

        response = self.session.put(url, json=payload)
        response.raise_for_status()

        result: Dict[str, Any] = response.json()
        return result

    def get_merge_request(self, project_id: str, mr_iid: int) -> Dict[str, Any]:
        """Get merge request details.

        Args:
            project_id: GitLab project ID or path
            mr_iid: Merge request internal ID

        Returns:
            Merge request data

        Raises:
            requests.HTTPError: If API request fails
        """
        project_path = project_id.replace("/", "%2F")
        url = f"{self.base_url}/api/v4/projects/{project_path}/merge_requests/{mr_iid}"

        response = self.session.get(url)
        response.raise_for_status()

        result: Dict[str, Any] = response.json()
        return result

    def add_merge_request_comment(
        self,
        project_id: str,
        mr_iid: int,
        body: str
    ) -> Dict[str, Any]:
        """Add a comment to a merge request.

        Args:
            project_id: GitLab project ID or path
            mr_iid: Merge request internal ID
            body: Comment text (supports markdown)

        Returns:
            Comment data

        Raises:
            requests.HTTPError: If API request fails
        """
        project_path = project_id.replace("/", "%2F")
        url = f"{self.base_url}/api/v4/projects/{project_path}/merge_requests/{mr_iid}/notes"

        payload = {"body": body}

        response = self.session.post(url, json=payload)
        response.raise_for_status()

        result: Dict[str, Any] = response.json()
        return result

    def list_merge_requests(
        self,
        project_id: str,
        state: str = "opened",
        source_branch: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List merge requests for a project.

        Args:
            project_id: GitLab project ID or path
            state: MR state - "opened", "closed", "merged", or "all"
            source_branch: Filter by source branch (optional)

        Returns:
            List of merge request dictionaries

        Raises:
            requests.HTTPError: If API request fails
        """
        project_path = project_id.replace("/", "%2F")
        url = f"{self.base_url}/api/v4/projects/{project_path}/merge_requests"

        params = {"state": state}
        if source_branch:
            params["source_branch"] = source_branch

        response = self.session.get(url, params=params)
        response.raise_for_status()

        result: List[Dict[str, Any]] = response.json()
        return result

    def get_project_id(self, project_path: str) -> int:
        """Get numeric project ID from project path.

        Args:
            project_path: Project path (e.g., "acme/backend")

        Returns:
            Numeric project ID

        Raises:
            requests.HTTPError: If API request fails
        """
        encoded_path = project_path.replace("/", "%2F")
        url = f"{self.base_url}/api/v4/projects/{encoded_path}"

        response = self.session.get(url)
        response.raise_for_status()

        data = response.json()
        project_id: int = data.get("id", 0)
        return project_id

    def mark_as_ready(self, project_id: str, mr_iid: int) -> Dict[str, Any]:
        """Mark a draft MR as ready for review.

        Args:
            project_id: GitLab project ID or path
            mr_iid: Merge request internal ID

        Returns:
            Updated merge request data

        Raises:
            requests.HTTPError: If API request fails
        """
        # Get current MR
        mr_data = self.get_merge_request(project_id, mr_iid)
        current_title = mr_data.get("title", "")

        # Remove draft prefix
        new_title = current_title.replace("Draft: ", "").replace("draft: ", "")

        return self.update_merge_request(project_id, mr_iid, title=new_title)

    def get_merge_request_discussions(
        self,
        project_id: str,
        mr_iid: int,
        unresolved_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Get discussions/comments from a merge request.

        This method fetches from BOTH /discussions and /notes endpoints because:
        - /discussions returns threaded discussions
        - /notes returns DiffNotes (code comments) that may not appear in /discussions

        Args:
            project_id: GitLab project ID or path
            mr_iid: Merge request internal ID
            unresolved_only: Only return unresolved discussions (default: False)

        Returns:
            List of discussion dictionaries with keys:
                - id: Discussion ID
                - notes: List of notes in the discussion
                - resolved: Whether discussion is resolved

        Raises:
            requests.HTTPError: If API request fails
        """
        import logging
        logger = logging.getLogger(__name__)

        project_path = project_id.replace("/", "%2F")

        # Fetch from /discussions endpoint (threaded discussions)
        discussions_url = f"{self.base_url}/api/v4/projects/{project_path}/merge_requests/{mr_iid}/discussions"
        discussions_response = self.session.get(discussions_url)
        discussions_response.raise_for_status()
        discussions = discussions_response.json()

        # Fetch from /notes endpoint (includes DiffNotes)
        notes_url = f"{self.base_url}/api/v4/projects/{project_path}/merge_requests/{mr_iid}/notes"
        notes_response = self.session.get(notes_url)
        notes_response.raise_for_status()
        notes = notes_response.json()

        # Convert standalone notes to discussion format
        # Collect note IDs that are already in discussions to avoid duplicates
        existing_note_ids = set()
        for d in discussions:
            for note in d.get("notes", []):
                existing_note_ids.add(note.get("id"))

        # Add notes that aren't already in discussions
        for note in notes:
            note_id = note.get("id")
            if note_id not in existing_note_ids:
                # Check if this note has a discussion_id (DiffNotes always do)
                # Use the real discussion_id if available for proper threading
                note_discussion_id = note.get("discussion_id")
                if note_discussion_id:
                    # Use real discussion ID for DiffNotes and other discussion notes
                    discussion_id = note_discussion_id
                else:
                    # Fallback to synthetic ID for true standalone notes
                    discussion_id = f"note_{note_id}"

                # Wrap note in discussion format
                discussions.append({
                    "id": discussion_id,
                    "individual_note": not note_discussion_id,  # Only true for standalone notes without discussion_id
                    "notes": [note],
                })

        logger.debug(f"Total items: {len(discussions)} ({len(discussions) - len(notes) + len(existing_note_ids)} discussions + {len(notes) - len(existing_note_ids)} standalone notes)")

        # Filter for unresolved discussions if requested
        if unresolved_only:
            unresolved = []
            for d in discussions:
                discussion_id = d.get("id", "unknown")
                notes_list = d.get("notes", [])

                if notes_list:
                    first_note = notes_list[0]
                    resolvable = first_note.get("resolvable", False)
                    resolved = first_note.get("resolved", False)

                    # Include if resolvable AND unresolved
                    if resolvable and not resolved:
                        unresolved.append(d)
                        logger.debug(f"Discussion {discussion_id}: ✓ Included (resolvable={resolvable}, resolved={resolved})")
                    else:
                        logger.debug(f"Discussion {discussion_id}: ✗ Filtered (resolvable={resolvable}, resolved={resolved})")

            logger.info(f"Filtered {len(discussions)} items → {len(unresolved)} unresolved")
            discussions = unresolved

        result: List[Dict[str, Any]] = discussions
        return result

    def reply_to_discussion(
        self,
        project_id: str,
        mr_iid: int,
        discussion_id: str,
        body: str,
        resolve: bool = False,
    ) -> Dict[str, Any]:
        """Reply to a discussion in a merge request.

        Handles both real discussions and synthetic discussion IDs (note_12345)
        created from standalone notes.

        For DiffNote discussions (code comments), this properly threads the reply
        within the discussion using the GitLab discussions API.

        Args:
            project_id: GitLab project ID or path
            mr_iid: Merge request internal ID
            discussion_id: Discussion ID or synthetic ID (note_12345)
            body: Reply text (supports markdown)
            resolve: Whether to resolve the discussion (default: False)

        Returns:
            Note/comment data

        Raises:
            requests.HTTPError: If API request fails
        """
        import logging
        logger = logging.getLogger(__name__)

        project_path = project_id.replace("/", "%2F")

        # Check if this is a synthetic discussion ID from a standalone note
        if discussion_id.startswith("note_"):
            # Extract the note ID from synthetic discussion ID
            note_id = discussion_id.replace("note_", "")
            logger.debug(f"Replying to standalone note {note_id}")

            # For standalone notes, update the note directly using PUT
            url = (
                f"{self.base_url}/api/v4/projects/{project_path}/"
                f"merge_requests/{mr_iid}/notes/{note_id}"
            )

            # PUT to update the note's resolved status
            payload: Dict[str, Any] = {}
            if resolve:
                payload["resolved"] = True

            if payload:
                response = self.session.put(url, json=payload)
                response.raise_for_status()

            # POST a reply as a new note
            notes_url = (
                f"{self.base_url}/api/v4/projects/{project_path}/"
                f"merge_requests/{mr_iid}/notes"
            )
            reply_payload = {
                "body": body,
                "in_reply_to_id": int(note_id),  # Reply to the original note
            }

            logger.debug("Posting reply to standalone note via /notes endpoint")
            response = self.session.post(notes_url, json=reply_payload)
            response.raise_for_status()

        else:
            # Real discussion ID - use the discussions endpoint
            # This works for all discussion types including DiffNotes
            url = (
                f"{self.base_url}/api/v4/projects/{project_path}/"
                f"merge_requests/{mr_iid}/discussions/{discussion_id}/notes"
            )

            payload = {"body": body}
            if resolve:
                payload["resolved"] = True

            logger.debug(f"Posting reply to discussion {discussion_id} via /discussions endpoint")
            logger.debug(f"URL: {url}")
            logger.debug(f"Payload: {payload}")

            response = self.session.post(url, json=payload)
            response.raise_for_status()

        result: Dict[str, Any] = response.json()
        logger.debug(f"Reply posted successfully, response: {result.get('id')}")
        return result

    def add_emoji_reaction(
        self,
        project_id: str,
        mr_iid: int,
        note_id: int,
        emoji: str = "eyes",
        discussion_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add an emoji reaction to a note/comment.

        Args:
            project_id: GitLab project ID or path
            mr_iid: Merge request internal ID
            note_id: Note ID to react to
            emoji: Emoji name (e.g., "thumbsup", "eyes", "rocket")
            discussion_id: Discussion ID (required for discussion notes)

        Returns:
            Award emoji data

        Raises:
            requests.HTTPError: If API request fails
        """
        project_path = project_id.replace("/", "%2F")

        # For discussion notes, use the appropriate endpoint
        if discussion_id and not discussion_id.startswith("note_"):
            # Real discussion ID - use discussion-specific endpoint
            url = (
                f"{self.base_url}/api/v4/projects/{project_path}/"
                f"merge_requests/{mr_iid}/discussions/{discussion_id}/"
                f"notes/{note_id}/award_emoji"
            )
        else:
            # Synthetic discussion ID or no discussion - use notes endpoint
            url = (
                f"{self.base_url}/api/v4/projects/{project_path}/"
                f"merge_requests/{mr_iid}/notes/{note_id}/award_emoji"
            )

        payload = {"name": emoji}

        response = self.session.post(url, json=payload)
        response.raise_for_status()

        result: Dict[str, Any] = response.json()
        return result
