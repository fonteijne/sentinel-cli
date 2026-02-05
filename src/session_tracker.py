"""Session tracker for Sentinel Agent SDK sessions."""

import json
import logging
from pathlib import Path
from typing import Dict, Optional, Set

logger = logging.getLogger(__name__)


class SessionTracker:
    """Tracks Claude Agent SDK sessions created by Sentinel.

    Sessions are associated with projects to enable per-project cleanup.
    """

    def __init__(self) -> None:
        """Initialize session tracker."""
        self.sessions_file = Path.home() / ".sentinel" / "sessions.json"
        self.sessions_file.parent.mkdir(parents=True, exist_ok=True)

    def _load_sessions(self) -> Dict[str, str]:
        """Load tracked sessions from file.

        Returns:
            Dict mapping session_id -> project_key
        """
        if not self.sessions_file.exists():
            return {}

        try:
            with open(self.sessions_file, "r") as f:
                data = json.load(f)

                # Handle legacy format: {"session_ids": [...]}
                if "session_ids" in data and "sessions" not in data:
                    legacy_sessions = data.get("session_ids", [])
                    # Migrate to new format with "unknown" project
                    migrated = {sid: "unknown" for sid in legacy_sessions}
                    logger.info(
                        f"Migrated {len(migrated)} session(s) from legacy format"
                    )
                    self._save_sessions(migrated)
                    return migrated

                # New format: {"sessions": {"session_id": "project_key", ...}}
                return data.get("sessions", {})
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load sessions file: {e}")
            return {}

    def _save_sessions(self, sessions: Dict[str, str]) -> None:
        """Save tracked sessions to file.

        Args:
            sessions: Dict mapping session_id -> project_key
        """
        try:
            with open(self.sessions_file, "w") as f:
                json.dump({"sessions": sessions}, f, indent=2)
        except OSError as e:
            logger.error(f"Failed to save sessions file: {e}")

    def track_session(self, session_id: str, project: Optional[str] = None) -> None:
        """Add a session ID to tracking.

        Args:
            session_id: Session ID to track
            project: Project key (e.g., "ACME"). Defaults to "unknown" if not provided.
        """
        sessions = self._load_sessions()
        project_key = project.upper() if project else "unknown"
        sessions[session_id] = project_key
        self._save_sessions(sessions)
        logger.debug(f"Tracking session: {session_id} (project: {project_key})")

    def untrack_session(self, session_id: str) -> None:
        """Remove a session ID from tracking.

        Args:
            session_id: Session ID to untrack
        """
        sessions = self._load_sessions()
        if session_id in sessions:
            del sessions[session_id]
            self._save_sessions(sessions)
            logger.debug(f"Untracked session: {session_id}")

    def get_tracked_sessions(self, project: Optional[str] = None) -> Set[str]:
        """Get tracked session IDs, optionally filtered by project.

        Args:
            project: If provided, only return sessions for this project.
                     If None, returns all sessions.

        Returns:
            Set of tracked session IDs
        """
        sessions = self._load_sessions()

        if project is None:
            return set(sessions.keys())

        # Filter by project (case-insensitive)
        project_upper = project.upper()
        return {
            sid for sid, proj in sessions.items() if proj.upper() == project_upper
        }

    def get_session_project(self, session_id: str) -> Optional[str]:
        """Get the project associated with a session.

        Args:
            session_id: Session ID to look up

        Returns:
            Project key or None if session not found
        """
        sessions = self._load_sessions()
        return sessions.get(session_id)

    def clear_all(self, project: Optional[str] = None) -> int:
        """Clear tracked sessions, optionally filtered by project.

        Args:
            project: If provided, only clear sessions for this project.
                     If None, clears all sessions.

        Returns:
            Number of sessions cleared
        """
        sessions = self._load_sessions()

        if project is None:
            count = len(sessions)
            self._save_sessions({})
            logger.info(f"Cleared {count} tracked session(s)")
            return count

        # Filter out sessions for the specified project
        project_upper = project.upper()
        remaining = {
            sid: proj
            for sid, proj in sessions.items()
            if proj.upper() != project_upper
        }
        count = len(sessions) - len(remaining)
        self._save_sessions(remaining)
        logger.info(f"Cleared {count} tracked session(s) for project {project_upper}")
        return count
