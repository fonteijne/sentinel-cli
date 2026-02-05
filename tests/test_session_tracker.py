"""Unit tests for SessionTracker."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch
from src.session_tracker import SessionTracker


@pytest.fixture
def temp_sessions_file(tmp_path):
    """Create a temporary sessions file for testing."""
    sessions_dir = tmp_path / ".sentinel"
    sessions_dir.mkdir(parents=True)
    sessions_file = sessions_dir / "sessions.json"

    # Patch the sessions file path
    with patch.object(SessionTracker, "__init__", lambda self: None):
        tracker = SessionTracker()
        tracker.sessions_file = sessions_file
        yield tracker, sessions_file


class TestSessionTracker:
    """Tests for SessionTracker class."""

    def test_track_session_with_project(self, temp_sessions_file):
        """Test tracking a session with a project association."""
        tracker, sessions_file = temp_sessions_file

        tracker.track_session("session-123", project="ACME")

        # Verify file contents
        data = json.loads(sessions_file.read_text())
        assert "sessions" in data
        assert data["sessions"]["session-123"] == "ACME"

    def test_track_session_without_project(self, temp_sessions_file):
        """Test tracking a session without a project defaults to 'unknown'."""
        tracker, sessions_file = temp_sessions_file

        tracker.track_session("session-456")

        data = json.loads(sessions_file.read_text())
        assert data["sessions"]["session-456"] == "unknown"

    def test_track_multiple_sessions_different_projects(self, temp_sessions_file):
        """Test tracking sessions across multiple projects."""
        tracker, sessions_file = temp_sessions_file

        tracker.track_session("session-1", project="ACME")
        tracker.track_session("session-2", project="ACME")
        tracker.track_session("session-3", project="BETA")
        tracker.track_session("session-4", project="BETA")

        data = json.loads(sessions_file.read_text())
        assert len(data["sessions"]) == 4
        assert data["sessions"]["session-1"] == "ACME"
        assert data["sessions"]["session-2"] == "ACME"
        assert data["sessions"]["session-3"] == "BETA"
        assert data["sessions"]["session-4"] == "BETA"

    def test_get_tracked_sessions_all(self, temp_sessions_file):
        """Test getting all tracked sessions."""
        tracker, sessions_file = temp_sessions_file

        tracker.track_session("session-1", project="ACME")
        tracker.track_session("session-2", project="BETA")

        all_sessions = tracker.get_tracked_sessions()

        assert len(all_sessions) == 2
        assert "session-1" in all_sessions
        assert "session-2" in all_sessions

    def test_get_tracked_sessions_by_project(self, temp_sessions_file):
        """Test getting tracked sessions filtered by project."""
        tracker, sessions_file = temp_sessions_file

        tracker.track_session("session-1", project="ACME")
        tracker.track_session("session-2", project="ACME")
        tracker.track_session("session-3", project="BETA")

        acme_sessions = tracker.get_tracked_sessions(project="ACME")
        beta_sessions = tracker.get_tracked_sessions(project="BETA")

        assert len(acme_sessions) == 2
        assert "session-1" in acme_sessions
        assert "session-2" in acme_sessions

        assert len(beta_sessions) == 1
        assert "session-3" in beta_sessions

    def test_get_tracked_sessions_case_insensitive(self, temp_sessions_file):
        """Test that project filtering is case-insensitive."""
        tracker, sessions_file = temp_sessions_file

        tracker.track_session("session-1", project="ACME")

        # Should find session regardless of case
        sessions_lower = tracker.get_tracked_sessions(project="acme")
        sessions_upper = tracker.get_tracked_sessions(project="ACME")

        assert len(sessions_lower) == 1
        assert len(sessions_upper) == 1

    def test_untrack_session(self, temp_sessions_file):
        """Test untracking a session."""
        tracker, sessions_file = temp_sessions_file

        tracker.track_session("session-1", project="ACME")
        tracker.track_session("session-2", project="ACME")

        tracker.untrack_session("session-1")

        data = json.loads(sessions_file.read_text())
        assert "session-1" not in data["sessions"]
        assert "session-2" in data["sessions"]

    def test_clear_all_sessions(self, temp_sessions_file):
        """Test clearing all sessions."""
        tracker, sessions_file = temp_sessions_file

        tracker.track_session("session-1", project="ACME")
        tracker.track_session("session-2", project="BETA")

        count = tracker.clear_all()

        assert count == 2
        data = json.loads(sessions_file.read_text())
        assert len(data["sessions"]) == 0

    def test_clear_sessions_by_project(self, temp_sessions_file):
        """Test clearing sessions for a specific project only."""
        tracker, sessions_file = temp_sessions_file

        tracker.track_session("session-1", project="ACME")
        tracker.track_session("session-2", project="ACME")
        tracker.track_session("session-3", project="BETA")

        count = tracker.clear_all(project="ACME")

        assert count == 2
        data = json.loads(sessions_file.read_text())
        assert len(data["sessions"]) == 1
        assert "session-3" in data["sessions"]

    def test_get_session_project(self, temp_sessions_file):
        """Test getting the project for a specific session."""
        tracker, sessions_file = temp_sessions_file

        tracker.track_session("session-1", project="ACME")

        project = tracker.get_session_project("session-1")
        assert project == "ACME"

        # Non-existent session
        project = tracker.get_session_project("nonexistent")
        assert project is None

    def test_migrate_legacy_format(self, temp_sessions_file):
        """Test migration from legacy format to new format."""
        tracker, sessions_file = temp_sessions_file

        # Write legacy format
        legacy_data = {"session_ids": ["legacy-1", "legacy-2"]}
        sessions_file.write_text(json.dumps(legacy_data))

        # Load sessions - should trigger migration
        sessions = tracker.get_tracked_sessions()

        assert len(sessions) == 2
        assert "legacy-1" in sessions
        assert "legacy-2" in sessions

        # Verify file was migrated to new format
        data = json.loads(sessions_file.read_text())
        assert "sessions" in data
        assert data["sessions"]["legacy-1"] == "unknown"
        assert data["sessions"]["legacy-2"] == "unknown"

    def test_empty_file_returns_empty_sessions(self, temp_sessions_file):
        """Test that a missing or empty file returns empty sessions."""
        tracker, sessions_file = temp_sessions_file

        # File doesn't exist yet
        sessions = tracker.get_tracked_sessions()
        assert len(sessions) == 0

    def test_project_stored_uppercase(self, temp_sessions_file):
        """Test that project is stored in uppercase."""
        tracker, sessions_file = temp_sessions_file

        tracker.track_session("session-1", project="acme")

        data = json.loads(sessions_file.read_text())
        assert data["sessions"]["session-1"] == "ACME"
