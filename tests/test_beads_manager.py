"""Unit tests for BeadsManager."""

import subprocess
from unittest.mock import Mock, patch

import pytest

from src.beads_manager import BeadsManager


@pytest.fixture
def beads_manager():
    """Create a BeadsManager instance with mocked bd check."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0)
        manager = BeadsManager()
        return manager


class TestBeadsManagerInit:
    """Test BeadsManager initialization."""

    def test_init_success(self):
        """Test successful initialization."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)
            manager = BeadsManager()

            # Verify bd --version was called
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "bd"
            assert args[1] == "--version"

    def test_init_bd_not_found(self):
        """Test initialization when bd is not installed."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="beads CLI .bd. not found"):
                BeadsManager()

    def test_init_bd_error(self):
        """Test initialization when bd check fails."""
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "bd")
        ):
            with pytest.raises(RuntimeError, match="beads CLI .bd. not found"):
                BeadsManager()


class TestInitProject:
    """Test init_project method."""

    @patch("subprocess.run")
    def test_init_project_already_initialized(self, mock_run, beads_manager):
        """Test when beads is already initialized."""
        # Mock bd stats returning success (already initialized)
        mock_run.return_value = Mock(returncode=0)

        beads_manager.init_project("ACME-123", "/tmp/test")

        # Should only call bd stats once
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "bd"
        assert args[1] == "stats"

    @patch("subprocess.run")
    def test_init_project_new_initialization(self, mock_run, beads_manager):
        """Test initializing a new beads project."""
        # First call fails (not initialized), second succeeds
        mock_run.side_effect = [
            Mock(returncode=1),  # bd stats check fails
            Mock(returncode=0),  # bd stats to initialize
        ]

        beads_manager.init_project("ACME-123", "/tmp/test")

        # Should call bd stats twice
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    def test_init_project_with_working_dir(self, mock_run, beads_manager):
        """Test that working directory is passed correctly."""
        mock_run.return_value = Mock(returncode=0)

        beads_manager.init_project("ACME-123", "/custom/path")

        call_args = mock_run.call_args
        assert call_args.kwargs["cwd"] == "/custom/path"


class TestCreateTask:
    """Test create_task method."""

    @patch("subprocess.run")
    def test_create_task_success(self, mock_run, beads_manager):
        """Test creating a task successfully."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="✓ Created issue: sentinel-abc: Test Task",
            stderr="",
        )

        result = beads_manager.create_task("Test Task")

        assert result == "sentinel-abc"
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "bd"
        assert args[1] == "create"
        assert "--title=Test Task" in args

    @patch("subprocess.run")
    def test_create_task_with_all_params(self, mock_run, beads_manager):
        """Test creating a task with all parameters."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="✓ Created issue: sentinel-xyz: Bug Fix",
            stderr="",
        )

        result = beads_manager.create_task(
            title="Bug Fix",
            task_type="bug",
            priority=0,
            description="Critical bug",
            working_dir="/tmp/test",
        )

        assert result == "sentinel-xyz"
        args = mock_run.call_args[0][0]
        assert "--title=Bug Fix" in args
        assert "--type=bug" in args
        assert "--priority=0" in args
        assert "--description=Critical bug" in args
        assert mock_run.call_args.kwargs["cwd"] == "/tmp/test"

    @patch("subprocess.run")
    def test_create_task_invalid_output(self, mock_run, beads_manager):
        """Test handling unexpected output format."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="Unexpected output format",
            stderr="",
        )

        with pytest.raises(RuntimeError, match="Failed to extract task ID"):
            beads_manager.create_task("Test Task")

    @patch("subprocess.run")
    def test_create_task_command_failure(self, mock_run, beads_manager):
        """Test handling command failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "bd create")

        with pytest.raises(subprocess.CalledProcessError):
            beads_manager.create_task("Test Task")


class TestUpdateTask:
    """Test update_task method."""

    @patch("subprocess.run")
    def test_update_task_status(self, mock_run, beads_manager):
        """Test updating task status."""
        mock_run.return_value = Mock(returncode=0)

        beads_manager.update_task("sentinel-abc", status="in_progress")

        args = mock_run.call_args[0][0]
        assert args[0] == "bd"
        assert args[1] == "update"
        assert args[2] == "sentinel-abc"
        assert "--status=in_progress" in args

    @patch("subprocess.run")
    def test_update_task_all_fields(self, mock_run, beads_manager):
        """Test updating all task fields."""
        mock_run.return_value = Mock(returncode=0)

        beads_manager.update_task(
            task_id="sentinel-abc",
            status="closed",
            title="New Title",
            description="New Description",
            notes="Additional notes",
            working_dir="/tmp/test",
        )

        args = mock_run.call_args[0][0]
        assert "--status=closed" in args
        assert "--title=New Title" in args
        assert "--description=New Description" in args
        assert "--notes=Additional notes" in args
        assert mock_run.call_args.kwargs["cwd"] == "/tmp/test"

    @patch("subprocess.run")
    def test_update_task_partial_fields(self, mock_run, beads_manager):
        """Test updating only some fields."""
        mock_run.return_value = Mock(returncode=0)

        beads_manager.update_task(
            task_id="sentinel-abc",
            title="New Title",
        )

        args = mock_run.call_args[0][0]
        assert "--title=New Title" in args
        # Other fields should not be present
        assert not any("--status=" in arg for arg in args)
        assert not any("--description=" in arg for arg in args)


class TestCloseTask:
    """Test close_task method."""

    @patch("subprocess.run")
    def test_close_task_success(self, mock_run, beads_manager):
        """Test closing a task successfully."""
        mock_run.return_value = Mock(returncode=0)

        beads_manager.close_task("sentinel-abc")

        args = mock_run.call_args[0][0]
        assert args[0] == "bd"
        assert args[1] == "close"
        assert args[2] == "sentinel-abc"

    @patch("subprocess.run")
    def test_close_task_with_reason(self, mock_run, beads_manager):
        """Test closing a task with reason."""
        mock_run.return_value = Mock(returncode=0)

        beads_manager.close_task(
            task_id="sentinel-abc",
            reason="Completed successfully",
            working_dir="/tmp/test",
        )

        args = mock_run.call_args[0][0]
        assert "--reason=Completed successfully" in args
        assert mock_run.call_args.kwargs["cwd"] == "/tmp/test"


class TestGetTask:
    """Test get_task method."""

    @patch("subprocess.run")
    def test_get_task_success(self, mock_run, beads_manager):
        """Test getting task details successfully."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="○ sentinel-abc · Test Task   [● P1 · OPEN]\n\nDescription here",
            stderr="",
        )

        result = beads_manager.get_task("sentinel-abc")

        assert result["id"] == "sentinel-abc"
        assert result["title"] == "Test Task"
        assert result["status"] == "open"

    @patch("subprocess.run")
    def test_get_task_in_progress(self, mock_run, beads_manager):
        """Test getting task with in_progress status."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="○ sentinel-abc · Test Task   [● P1 · IN_PROGRESS]\n",
            stderr="",
        )

        result = beads_manager.get_task("sentinel-abc")

        assert result["status"] == "in_progress"

    @patch("subprocess.run")
    def test_get_task_closed(self, mock_run, beads_manager):
        """Test getting closed task."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="○ sentinel-abc · Test Task   [● P1 · CLOSED]\n",
            stderr="",
        )

        result = beads_manager.get_task("sentinel-abc")

        assert result["status"] == "closed"

    @patch("subprocess.run")
    def test_get_task_with_working_dir(self, mock_run, beads_manager):
        """Test getting task with custom working directory."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="○ sentinel-abc · Test   [● P1 · OPEN]\n",
            stderr="",
        )

        beads_manager.get_task("sentinel-abc", working_dir="/tmp/test")

        assert mock_run.call_args.kwargs["cwd"] == "/tmp/test"

    @patch("subprocess.run")
    def test_get_task_includes_raw_output(self, mock_run, beads_manager):
        """Test that raw output is included."""
        output = "○ sentinel-abc · Test   [● P1 · OPEN]\n"
        mock_run.return_value = Mock(returncode=0, stdout=output, stderr="")

        result = beads_manager.get_task("sentinel-abc")

        assert result["raw_output"] == output


class TestListTasks:
    """Test list_tasks method."""

    @patch("subprocess.run")
    def test_list_tasks_success(self, mock_run, beads_manager):
        """Test listing tasks successfully."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout=(
                "📋 Tasks:\n"
                "○ sentinel-abc [● P1] [task] - First Task\n"
                "○ sentinel-def [● P2] [bug] - Second Task\n"
            ),
            stderr="",
        )

        result = beads_manager.list_tasks()

        assert len(result) == 2
        assert result[0]["id"] == "sentinel-abc"
        assert result[1]["id"] == "sentinel-def"

    @patch("subprocess.run")
    def test_list_tasks_with_status_filter(self, mock_run, beads_manager):
        """Test listing tasks with status filter."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="○ sentinel-abc [● P1] [task] - Task\n",
            stderr="",
        )

        beads_manager.list_tasks(status="open")

        args = mock_run.call_args[0][0]
        assert "--status=open" in args

    @patch("subprocess.run")
    def test_list_tasks_empty(self, mock_run, beads_manager):
        """Test listing tasks when none exist."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="📋 Tasks:\n",
            stderr="",
        )

        result = beads_manager.list_tasks()

        assert result == []

    @patch("subprocess.run")
    def test_list_tasks_includes_raw(self, mock_run, beads_manager):
        """Test that raw line is included in results."""
        task_line = "○ sentinel-abc [● P1] [task] - Task\n"
        mock_run.return_value = Mock(
            returncode=0,
            stdout=f"📋 Tasks:\n{task_line}",
            stderr="",
        )

        result = beads_manager.list_tasks()

        assert result[0]["raw"] == task_line.strip()


class TestGetReadyTasks:
    """Test get_ready_tasks method."""

    @patch("subprocess.run")
    def test_get_ready_tasks_success(self, mock_run, beads_manager):
        """Test getting ready tasks successfully."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout=(
                "Ready to work:\n"
                "sentinel-abc: First Task\n"
                "sentinel-def: Second Task\n"
            ),
            stderr="",
        )

        result = beads_manager.get_ready_tasks()

        assert len(result) == 2
        assert result[0]["id"] == "sentinel-abc"
        assert result[1]["id"] == "sentinel-def"

    @patch("subprocess.run")
    def test_get_ready_tasks_empty(self, mock_run, beads_manager):
        """Test getting ready tasks when none exist."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="No tasks ready\n",
            stderr="",
        )

        result = beads_manager.get_ready_tasks()

        assert result == []

    @patch("subprocess.run")
    def test_get_ready_tasks_with_working_dir(self, mock_run, beads_manager):
        """Test getting ready tasks with custom working directory."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="sentinel-abc: Task\n",
            stderr="",
        )

        beads_manager.get_ready_tasks(working_dir="/tmp/test")

        assert mock_run.call_args.kwargs["cwd"] == "/tmp/test"


class TestSync:
    """Test sync method."""

    @patch("subprocess.run")
    def test_sync_success(self, mock_run, beads_manager):
        """Test syncing successfully."""
        mock_run.return_value = Mock(returncode=0)

        beads_manager.sync()

        args = mock_run.call_args[0][0]
        assert args[0] == "bd"
        assert args[1] == "sync"

    @patch("subprocess.run")
    def test_sync_with_working_dir(self, mock_run, beads_manager):
        """Test syncing with custom working directory."""
        mock_run.return_value = Mock(returncode=0)

        beads_manager.sync(working_dir="/tmp/test")

        assert mock_run.call_args.kwargs["cwd"] == "/tmp/test"

    @patch("subprocess.run")
    def test_sync_failure(self, mock_run, beads_manager):
        """Test handling sync failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "bd sync")

        with pytest.raises(subprocess.CalledProcessError):
            beads_manager.sync()


class TestGetStats:
    """Test get_stats method."""

    @patch("subprocess.run")
    def test_get_stats_success(self, mock_run, beads_manager):
        """Test getting stats successfully."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout=(
                "Project Statistics:\n"
                "Total Issues: 10\n"
                "Open: 5\n"
                "Closed: 5\n"
                "Ready to Work: 3\n"
            ),
            stderr="",
        )

        result = beads_manager.get_stats()

        assert result["total"] == 10
        assert result["open"] == 5
        assert result["closed"] == 5
        assert result["ready"] == 3

    @patch("subprocess.run")
    def test_get_stats_partial_data(self, mock_run, beads_manager):
        """Test getting stats with partial data."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout=(
                "Project Statistics:\n"
                "Total Issues: 5\n"
            ),
            stderr="",
        )

        result = beads_manager.get_stats()

        assert result["total"] == 5
        # Missing fields should not be present
        assert "open" not in result
        assert "closed" not in result

    @patch("subprocess.run")
    def test_get_stats_includes_raw_output(self, mock_run, beads_manager):
        """Test that raw output is included."""
        output = "Project Statistics:\nTotal Issues: 10\n"
        mock_run.return_value = Mock(returncode=0, stdout=output, stderr="")

        result = beads_manager.get_stats()

        assert result["raw_output"] == output

    @patch("subprocess.run")
    def test_get_stats_with_working_dir(self, mock_run, beads_manager):
        """Test getting stats with custom working directory."""
        mock_run.return_value = Mock(
            returncode=0,
            stdout="Total Issues: 0\n",
            stderr="",
        )

        beads_manager.get_stats(working_dir="/tmp/test")

        assert mock_run.call_args.kwargs["cwd"] == "/tmp/test"

    @patch("subprocess.run")
    def test_get_stats_failure(self, mock_run, beads_manager):
        """Test handling stats command failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "bd stats")

        with pytest.raises(subprocess.CalledProcessError):
            beads_manager.get_stats()
