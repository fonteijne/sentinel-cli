"""Unit tests for WorktreeManager."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.config_loader import ConfigLoader
from src.worktree_manager import WorktreeManager


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_config(temp_workspace):
    """Create a mock configuration."""
    config = MagicMock(spec=ConfigLoader)
    config.workspace_root = temp_workspace
    config.get_project_config.return_value = {
        "git_url": "https://github.com/test/repo.git",
        "default_branch": "main",
    }
    return config


@pytest.fixture
def worktree_manager(mock_config):
    """Create a WorktreeManager instance with mocked config."""
    with patch("src.worktree_manager.get_config", return_value=mock_config):
        manager = WorktreeManager()
        return manager


class TestWorktreeManagerInit:
    """Test WorktreeManager initialization."""

    def test_init(self, mock_config):
        """Test basic initialization."""
        with patch("src.worktree_manager.get_config", return_value=mock_config):
            manager = WorktreeManager()
            assert manager.config == mock_config
            assert manager.workspace_root == mock_config.workspace_root


class TestEnsureBareClone:
    """Test ensure_bare_clone method."""

    def test_ensure_bare_clone_missing_project(self, worktree_manager):
        """Test with missing project configuration."""
        worktree_manager.config.get_project_config.return_value = None

        with pytest.raises(ValueError, match="No configuration found for project"):
            worktree_manager.ensure_bare_clone("MISSING")

    def test_ensure_bare_clone_missing_git_url(self, worktree_manager):
        """Test with missing git_url in project config."""
        worktree_manager.config.get_project_config.return_value = {
            "default_branch": "main"
        }

        with pytest.raises(ValueError, match="No git_url configured for project"):
            worktree_manager.ensure_bare_clone("ACME")

    @patch("subprocess.run")
    def test_ensure_bare_clone_creates_new(
        self, mock_run, worktree_manager, temp_workspace
    ):
        """Test creating a new bare clone."""
        mock_run.return_value = Mock(returncode=0)

        result = worktree_manager.ensure_bare_clone("ACME")

        assert result == temp_workspace / "acme"
        # Should be called twice: once for git clone, once for bd init
        assert mock_run.call_count == 2
        # First call should be git clone
        args = mock_run.call_args_list[0][0][0]
        assert args[0] == "git"
        assert args[1] == "clone"
        assert args[2] == "--bare"
        # Second call should be bd init
        args = mock_run.call_args_list[1][0][0]
        assert args == ["bd", "init"]

    @patch("subprocess.run")
    def test_ensure_bare_clone_existing_valid(
        self, mock_run, worktree_manager, temp_workspace
    ):
        """Test with existing valid bare clone."""
        # Create bare clone directory with config file
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)
        (bare_dir / "config").touch()

        # Mock git config check to return "true"
        mock_run.return_value = Mock(
            returncode=0, stdout="true\n", stderr=""
        )

        result = worktree_manager.ensure_bare_clone("ACME")

        assert result == bare_dir
        # Should call git config and git fetch
        assert mock_run.call_count == 2

    @patch("subprocess.run")
    @patch("shutil.rmtree")
    def test_ensure_bare_clone_existing_corrupted(
        self, mock_rmtree, mock_run, worktree_manager, temp_workspace
    ):
        """Test with existing corrupted bare clone."""
        # Create bare clone directory with config file
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)
        (bare_dir / "config").touch()

        # Mock git config check to fail, then git clone, then bd init
        mock_run.side_effect = [
            subprocess.CalledProcessError(1, "git config"),
            Mock(returncode=0),  # git clone
            Mock(returncode=0),  # bd init
        ]

        result = worktree_manager.ensure_bare_clone("ACME")

        assert result == bare_dir
        mock_rmtree.assert_called_once_with(bare_dir)

    @patch("subprocess.run")
    def test_ensure_bare_clone_git_failure(self, mock_run, worktree_manager):
        """Test handling of git clone failure."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git clone")

        with pytest.raises(subprocess.CalledProcessError):
            worktree_manager.ensure_bare_clone("ACME")


class TestCreateWorktree:
    """Test create_worktree method."""

    @patch("subprocess.run")
    def test_create_worktree_new(
        self, mock_run, worktree_manager, temp_workspace
    ):
        """Test creating a new worktree."""
        # Mock ensure_bare_clone
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)

        with patch.object(
            worktree_manager, "ensure_bare_clone", return_value=bare_dir
        ):
            # Mock git worktree add, git rev-parse (branch doesn't exist), git checkout -b
            mock_run.side_effect = [
                Mock(returncode=0),  # git worktree add
                Mock(returncode=1),  # git rev-parse (branch doesn't exist)
                Mock(returncode=0),  # git checkout -b
            ]

            result = worktree_manager.create_worktree("ACME-123", "ACME")

            assert result == bare_dir / "ACME-123"
            # Should call git worktree add, git rev-parse, and git checkout -b
            assert mock_run.call_count == 3

    @patch("subprocess.run")
    def test_create_worktree_existing_valid(
        self, mock_run, worktree_manager, temp_workspace
    ):
        """Test with existing valid worktree."""
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)
        worktree_dir = bare_dir / "ACME-123"
        worktree_dir.mkdir(parents=True)

        with patch.object(
            worktree_manager, "ensure_bare_clone", return_value=bare_dir
        ):
            # Mock git status to succeed
            mock_run.return_value = Mock(returncode=0)

            result = worktree_manager.create_worktree("ACME-123", "ACME")

            assert result == worktree_dir
            # Should only call git status
            mock_run.assert_called_once()

    @patch("subprocess.run")
    @patch("shutil.rmtree")
    def test_create_worktree_existing_invalid(
        self, mock_rmtree, mock_run, worktree_manager, temp_workspace
    ):
        """Test with existing invalid worktree."""
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)
        worktree_dir = bare_dir / "ACME-123"
        worktree_dir.mkdir(parents=True)

        with patch.object(
            worktree_manager, "ensure_bare_clone", return_value=bare_dir
        ):
            # Mock git status to fail, then succeed for worktree add, rev-parse, checkout
            mock_run.side_effect = [
                subprocess.CalledProcessError(1, "git status"),
                Mock(returncode=0),  # git worktree add
                Mock(returncode=1),  # git rev-parse (branch doesn't exist)
                Mock(returncode=0),  # git checkout -b
            ]

            result = worktree_manager.create_worktree("ACME-123", "ACME")

            assert result == worktree_dir
            mock_rmtree.assert_called_once_with(worktree_dir)

    @patch("subprocess.run")
    def test_create_worktree_custom_branch(
        self, mock_run, worktree_manager, temp_workspace
    ):
        """Test worktree creation uses default branch from config."""
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)

        worktree_manager.config.get_project_config.return_value = {
            "git_url": "https://github.com/test/repo.git",
            "default_branch": "develop",
        }

        with patch.object(
            worktree_manager, "ensure_bare_clone", return_value=bare_dir
        ):
            # Mock git worktree add, git rev-parse, git checkout -b
            mock_run.side_effect = [
                Mock(returncode=0),  # git worktree add
                Mock(returncode=1),  # git rev-parse (branch doesn't exist)
                Mock(returncode=0),  # git checkout -b
            ]

            worktree_manager.create_worktree("ACME-123", "ACME")

            # Find the git worktree add call
            worktree_call = None
            for call_args in mock_run.call_args_list:
                if call_args[0][0][0] == "git" and "worktree" in call_args[0][0]:
                    worktree_call = call_args[0][0]
                    break

            assert worktree_call is not None
            assert "develop" in worktree_call

    @patch("subprocess.run")
    def test_create_worktree_existing_branch(
        self, mock_run, worktree_manager, temp_workspace
    ):
        """Test worktree creation with existing feature branch."""
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)

        with patch.object(
            worktree_manager, "ensure_bare_clone", return_value=bare_dir
        ):
            # Mock git worktree add to succeed
            # Mock git rev-parse to succeed (branch exists)
            # Mock git checkout to succeed
            mock_run.side_effect = [
                Mock(returncode=0),  # git worktree add
                Mock(returncode=0),  # git rev-parse --verify (branch exists)
                Mock(returncode=0),  # git checkout
            ]

            worktree_manager.create_worktree("ACME-123", "ACME")

            # Verify git checkout was called without -b flag
            checkout_call = mock_run.call_args_list[2][0][0]
            assert "git" in checkout_call
            assert "checkout" in checkout_call
            assert "sentinel/feature/ACME-123" in checkout_call
            assert "-b" not in checkout_call

    @patch("subprocess.run")
    def test_create_worktree_new_branch(
        self, mock_run, worktree_manager, temp_workspace
    ):
        """Test worktree creation with new feature branch."""
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)

        with patch.object(
            worktree_manager, "ensure_bare_clone", return_value=bare_dir
        ):
            # Mock git worktree add to succeed
            # Mock git rev-parse to fail (branch doesn't exist)
            # Mock git checkout -b to succeed
            mock_run.side_effect = [
                Mock(returncode=0),  # git worktree add
                Mock(returncode=1),  # git rev-parse --verify (branch doesn't exist)
                Mock(returncode=0),  # git checkout -b
            ]

            worktree_manager.create_worktree("ACME-123", "ACME")

            # Verify git checkout -b was called
            checkout_call = mock_run.call_args_list[2][0][0]
            assert "git" in checkout_call
            assert "checkout" in checkout_call
            assert "-b" in checkout_call
            assert "sentinel/feature/ACME-123" in checkout_call


class TestCleanupWorktree:
    """Test cleanup_worktree method."""

    @patch("subprocess.run")
    def test_cleanup_worktree_existing(
        self, mock_run, worktree_manager, temp_workspace
    ):
        """Test cleaning up an existing worktree."""
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)
        worktree_dir = bare_dir / "ACME-123"
        worktree_dir.mkdir(parents=True)

        mock_run.return_value = Mock(returncode=0)

        worktree_manager.cleanup_worktree("ACME-123", "ACME")

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "git"
        assert args[1] == "worktree"
        assert args[2] == "remove"
        assert "--force" in args

    @patch("subprocess.run")
    def test_cleanup_worktree_nonexistent(
        self, mock_run, worktree_manager, temp_workspace
    ):
        """Test cleaning up a non-existent worktree."""
        worktree_manager.cleanup_worktree("ACME-999", "ACME")

        # Should not call git if worktree doesn't exist
        mock_run.assert_not_called()


class TestGetWorktreePath:
    """Test get_worktree_path method."""

    @patch("subprocess.run")
    def test_get_worktree_path_valid(
        self, mock_run, worktree_manager, temp_workspace
    ):
        """Test getting path to valid worktree."""
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)
        worktree_dir = bare_dir / "ACME-123"
        worktree_dir.mkdir(parents=True)

        mock_run.return_value = Mock(returncode=0)

        result = worktree_manager.get_worktree_path("ACME-123", "ACME")

        assert result == worktree_dir

    @patch("subprocess.run")
    def test_get_worktree_path_invalid(
        self, mock_run, worktree_manager, temp_workspace
    ):
        """Test getting path to invalid worktree."""
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)
        worktree_dir = bare_dir / "ACME-123"
        worktree_dir.mkdir(parents=True)

        mock_run.side_effect = subprocess.CalledProcessError(1, "git status")

        result = worktree_manager.get_worktree_path("ACME-123", "ACME")

        assert result is None

    def test_get_worktree_path_nonexistent(self, worktree_manager):
        """Test getting path to non-existent worktree."""
        result = worktree_manager.get_worktree_path("ACME-999", "ACME")

        assert result is None


class TestListWorktrees:
    """Test list_worktrees method."""

    @patch("subprocess.run")
    def test_list_worktrees_success(
        self, mock_run, worktree_manager, temp_workspace
    ):
        """Test listing worktrees successfully."""
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)

        mock_run.return_value = Mock(
            returncode=0,
            stdout=f"worktree {bare_dir}\n"
                   f"worktree {bare_dir / 'ACME-123'}\n"
                   f"worktree {bare_dir / 'ACME-456'}\n",
            stderr="",
        )

        result = worktree_manager.list_worktrees("ACME")

        assert "ACME-123" in result
        assert "ACME-456" in result
        # Should not include the bare repo itself
        assert "acme" not in result
        assert len(result) == 2

    @patch("subprocess.run")
    def test_list_worktrees_git_failure(
        self, mock_run, worktree_manager
    ):
        """Test listing worktrees when git command fails."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "git worktree list")

        result = worktree_manager.list_worktrees("ACME")

        assert result == []

    def test_list_worktrees_no_bare_clone(self, worktree_manager):
        """Test listing worktrees when bare clone doesn't exist."""
        result = worktree_manager.list_worktrees("NONEXISTENT")

        assert result == []

    @patch("subprocess.run")
    def test_list_worktrees_empty(
        self, mock_run, worktree_manager, temp_workspace
    ):
        """Test listing worktrees when only bare repo exists."""
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)

        mock_run.return_value = Mock(
            returncode=0,
            stdout=f"worktree {bare_dir}\n",
            stderr="",
        )

        result = worktree_manager.list_worktrees("ACME")

        assert result == []


class TestCleanupAllWorktrees:
    """Test cleanup_all_worktrees method."""

    @patch.object(WorktreeManager, "list_worktrees")
    @patch.object(WorktreeManager, "cleanup_worktree")
    def test_cleanup_all_worktrees_success(
        self, mock_cleanup, mock_list, worktree_manager, temp_workspace
    ):
        """Test cleaning up all worktrees successfully."""
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)

        # Mock list to return multiple worktrees
        mock_list.return_value = ["ACME-123", "ACME-456", "ACME-789"]

        result = worktree_manager.cleanup_all_worktrees("ACME")

        assert result == 3
        assert mock_cleanup.call_count == 3
        mock_cleanup.assert_any_call("ACME-123", "ACME")
        mock_cleanup.assert_any_call("ACME-456", "ACME")
        mock_cleanup.assert_any_call("ACME-789", "ACME")

    @patch.object(WorktreeManager, "list_worktrees")
    @patch.object(WorktreeManager, "cleanup_worktree")
    def test_cleanup_all_worktrees_partial_failure(
        self, mock_cleanup, mock_list, worktree_manager, temp_workspace
    ):
        """Test cleanup continues even if some worktrees fail to remove."""
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)

        mock_list.return_value = ["ACME-123", "ACME-456", "ACME-789"]

        # Make second cleanup fail
        mock_cleanup.side_effect = [
            None,  # Success
            subprocess.CalledProcessError(1, "git worktree remove"),  # Fail
            None,  # Success
        ]

        result = worktree_manager.cleanup_all_worktrees("ACME")

        # Should still return 2 (successful removals)
        assert result == 2
        assert mock_cleanup.call_count == 3

    def test_cleanup_all_worktrees_no_bare_clone(self, worktree_manager):
        """Test cleanup when bare clone doesn't exist."""
        result = worktree_manager.cleanup_all_worktrees("NONEXISTENT")

        assert result == 0

    @patch.object(WorktreeManager, "list_worktrees")
    def test_cleanup_all_worktrees_empty(
        self, mock_list, worktree_manager, temp_workspace
    ):
        """Test cleanup when no worktrees exist."""
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir(parents=True)

        mock_list.return_value = []

        result = worktree_manager.cleanup_all_worktrees("ACME")

        assert result == 0


class TestInitializePythonProject:
    """Test Python project initialization in worktrees."""

    def test_initialize_python_project_creates_structure(
        self, worktree_manager, temp_workspace
    ):
        """Test that Python project structure is created."""
        worktree_path = temp_workspace / "test-worktree"
        worktree_path.mkdir()

        worktree_manager.initialize_python_project(worktree_path, "test-project")

        # Check directories
        assert (worktree_path / "src").exists()
        assert (worktree_path / "tests").exists()

        # Check __init__.py files
        assert (worktree_path / "src" / "__init__.py").exists()
        assert (worktree_path / "tests" / "__init__.py").exists()

        # Check pyproject.toml
        pyproject = worktree_path / "pyproject.toml"
        assert pyproject.exists()
        content = pyproject.read_text()
        assert 'name = "test-project"' in content
        assert "pytest" in content
        assert "mypy" in content
        assert "ruff" in content

        # Check README.md
        readme = worktree_path / "README.md"
        assert readme.exists()
        assert "test-project" in readme.read_text()

    def test_initialize_python_project_idempotent(
        self, worktree_manager, temp_workspace
    ):
        """Test that initializing twice doesn't fail."""
        worktree_path = temp_workspace / "test-worktree"
        worktree_path.mkdir()

        # Initialize once
        worktree_manager.initialize_python_project(worktree_path, "test-project")

        # Initialize again - should not raise error
        worktree_manager.initialize_python_project(worktree_path, "test-project")

        # Should still have the structure
        assert (worktree_path / "pyproject.toml").exists()

    def test_initialize_python_project_with_sanitized_name(
        self, worktree_manager, temp_workspace
    ):
        """Test that project name is properly used in pyproject.toml."""
        worktree_path = temp_workspace / "test-worktree"
        worktree_path.mkdir()

        worktree_manager.initialize_python_project(worktree_path, "sentest_1")

        pyproject = worktree_path / "pyproject.toml"
        content = pyproject.read_text()
        assert 'name = "sentest_1"' in content
        assert 'description = "Generated implementation for sentest_1"' in content

    def test_initialize_python_project_creates_poetry_config(
        self, worktree_manager, temp_workspace
    ):
        """Test that pyproject.toml has proper Poetry configuration."""
        worktree_path = temp_workspace / "test-worktree"
        worktree_path.mkdir()

        worktree_manager.initialize_python_project(worktree_path, "my-app")

        pyproject = worktree_path / "pyproject.toml"
        content = pyproject.read_text()

        # Check key sections
        assert "[tool.poetry]" in content
        assert "[tool.poetry.dependencies]" in content
        assert "[tool.poetry.group.dev.dependencies]" in content
        assert "[build-system]" in content
        assert "[tool.pytest.ini_options]" in content
        assert "[tool.mypy]" in content
        assert "[tool.ruff]" in content

        # Check dependencies
        assert 'python = "^3.11"' in content
        assert 'pytest = "^7.4.0"' in content
        assert 'pytest-cov = "^4.1.0"' in content


class TestDeleteLocalBranch:
    """Tests for delete_local_branch method."""

    @patch("subprocess.run")
    def test_delete_existing_branch(self, mock_run, worktree_manager, temp_workspace):
        """Test deleting an existing branch."""
        worktree_manager.workspace_root = temp_workspace
        (temp_workspace / "acme").mkdir()

        # First call: rev-parse succeeds (branch exists)
        # Second call: branch -D succeeds
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0),
        ]

        result = worktree_manager.delete_local_branch("ACME-123", "ACME")

        assert result is True
        assert mock_run.call_count == 2
        mock_run.assert_any_call(
            ["git", "branch", "-D", "sentinel/feature/ACME-123"],
            cwd=temp_workspace / "acme",
            check=True,
        )

    @patch("subprocess.run")
    def test_delete_nonexistent_branch(self, mock_run, worktree_manager, temp_workspace):
        """Test deleting a branch that doesn't exist."""
        worktree_manager.workspace_root = temp_workspace
        (temp_workspace / "acme").mkdir()

        mock_run.return_value = MagicMock(returncode=1)  # Branch doesn't exist

        result = worktree_manager.delete_local_branch("ACME-123", "ACME")

        assert result is False
        assert mock_run.call_count == 1

    def test_delete_branch_no_bare_repo(self, worktree_manager, temp_workspace):
        """Test deleting a branch when bare repo doesn't exist."""
        worktree_manager.workspace_root = temp_workspace

        result = worktree_manager.delete_local_branch("ACME-123", "ACME")

        assert result is False


class TestResetTicket:
    """Tests for reset_ticket method."""

    @patch.object(WorktreeManager, "delete_local_branch")
    @patch.object(WorktreeManager, "cleanup_worktree")
    def test_reset_removes_worktree_and_branch(
        self, mock_cleanup, mock_delete, worktree_manager, temp_workspace
    ):
        """Test reset removes both worktree and branch."""
        worktree_manager.workspace_root = temp_workspace
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir()
        (bare_dir / "ACME-123").mkdir()

        mock_delete.return_value = True

        result = worktree_manager.reset_ticket("ACME-123", "ACME")

        assert result["worktree_removed"] is True
        assert result["branch_deleted"] is True
        mock_cleanup.assert_called_once_with("ACME-123", "ACME")
        mock_delete.assert_called_once_with("ACME-123", "ACME")

    @patch.object(WorktreeManager, "delete_local_branch")
    @patch.object(WorktreeManager, "cleanup_worktree")
    def test_reset_no_worktree(
        self, mock_cleanup, mock_delete, worktree_manager, temp_workspace
    ):
        """Test reset when worktree doesn't exist but branch does."""
        worktree_manager.workspace_root = temp_workspace
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir()
        # No worktree directory created

        mock_delete.return_value = True

        result = worktree_manager.reset_ticket("ACME-123", "ACME")

        assert result["worktree_removed"] is False
        assert result["branch_deleted"] is True
        mock_cleanup.assert_not_called()
        mock_delete.assert_called_once_with("ACME-123", "ACME")

    @patch.object(WorktreeManager, "delete_local_branch")
    @patch.object(WorktreeManager, "cleanup_worktree")
    def test_reset_no_branch(
        self, mock_cleanup, mock_delete, worktree_manager, temp_workspace
    ):
        """Test reset when branch doesn't exist but worktree does."""
        worktree_manager.workspace_root = temp_workspace
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir()
        (bare_dir / "ACME-123").mkdir()

        mock_delete.return_value = False

        result = worktree_manager.reset_ticket("ACME-123", "ACME")

        assert result["worktree_removed"] is True
        assert result["branch_deleted"] is False
        mock_cleanup.assert_called_once_with("ACME-123", "ACME")
        mock_delete.assert_called_once_with("ACME-123", "ACME")


class TestResetAll:
    """Tests for reset_all method."""

    @patch.object(WorktreeManager, "cleanup_all_worktrees")
    @patch("shutil.rmtree")
    def test_reset_all_removes_repo(
        self, mock_rmtree, mock_cleanup, worktree_manager, temp_workspace
    ):
        """Test reset_all removes worktrees and bare repo."""
        worktree_manager.workspace_root = temp_workspace
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir()

        mock_cleanup.return_value = 3

        result = worktree_manager.reset_all("ACME")

        assert result["worktrees_removed"] == 3
        assert result["repo_removed"] == 1
        mock_rmtree.assert_called_once_with(bare_dir)

    def test_reset_all_no_bare_repo(self, worktree_manager, temp_workspace):
        """Test reset_all when bare repo doesn't exist."""
        worktree_manager.workspace_root = temp_workspace

        result = worktree_manager.reset_all("NONEXISTENT")

        assert result["worktrees_removed"] == 0
        assert result["repo_removed"] == 0

    @patch.object(WorktreeManager, "cleanup_all_worktrees")
    @patch("shutil.rmtree")
    def test_reset_all_empty_repo(
        self, mock_rmtree, mock_cleanup, worktree_manager, temp_workspace
    ):
        """Test reset_all when repo has no worktrees."""
        worktree_manager.workspace_root = temp_workspace
        bare_dir = temp_workspace / "acme"
        bare_dir.mkdir()

        mock_cleanup.return_value = 0

        result = worktree_manager.reset_all("ACME")

        assert result["worktrees_removed"] == 0
        assert result["repo_removed"] == 1
        mock_rmtree.assert_called_once_with(bare_dir)
