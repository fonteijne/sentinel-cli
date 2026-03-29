"""Tests for container environment lifecycle manager."""

from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import yaml

from src.compose_runner import ComposeResult, ServiceStatus
from src.environment_manager import EnvironmentManager, EnvironmentInfo, SENTINEL_COMPOSE_FILE


@pytest.fixture
def mock_config():
    """Mock config_loader.get_config."""
    with patch("src.environment_manager.get_config") as mock:
        config = MagicMock()
        config.get.return_value = {
            "runtime": "dood",
            "health_timeout": 30,
            "auto_detect": True,
            "auto_cleanup": True,
            "volume_name": "sentinel-projects",
        }
        mock.return_value = config
        yield config


@pytest.fixture
def env_mgr(mock_config):
    """Create EnvironmentManager with mocked config."""
    return EnvironmentManager()


@pytest.fixture
def lando_worktree(tmp_path):
    """Create a worktree with .lando.yml."""
    lando_config = {
        "recipe": "drupal10",
        "config": {
            "php": "8.2",
            "webroot": "web",
        },
        "services": {
            "cache": {"type": "redis"},
        },
    }
    lando_file = tmp_path / ".lando.yml"
    lando_file.write_text(yaml.dump(lando_config))
    return tmp_path


@pytest.fixture
def python_worktree(tmp_path):
    """Create a worktree without .lando.yml (Python project)."""
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = 'test'")
    return tmp_path


class TestDetectProjectType:
    """Test project type auto-detection."""

    def test_detects_lando(self, env_mgr, lando_worktree):
        assert env_mgr.detect_project_type(lando_worktree) == "lando"

    def test_detects_lando_local(self, env_mgr, tmp_path):
        (tmp_path / ".lando.local.yml").write_text("recipe: drupal10")
        assert env_mgr.detect_project_type(tmp_path) == "lando"

    def test_no_container_for_python(self, env_mgr, python_worktree):
        assert env_mgr.detect_project_type(python_worktree) is None

    def test_no_container_for_empty(self, env_mgr, tmp_path):
        assert env_mgr.detect_project_type(tmp_path) is None


class TestSetup:
    """Test environment setup."""

    @patch("src.environment_manager.ComposeRunner")
    def test_setup_lando_project(self, MockRunner, env_mgr, lando_worktree):
        # Mock compose runner
        runner = MagicMock()
        runner.up.return_value = ComposeResult(success=True)
        runner.wait_for_healthy.return_value = True
        runner.ps.return_value = [
            ServiceStatus(name="appserver", state="running", health=""),
        ]
        MockRunner.return_value = runner

        info = env_mgr.setup(lando_worktree, "STNL-001")

        assert info.active is True
        assert info.ticket_id == "STNL-001"
        assert "appserver" in info.services
        assert "database" in info.services
        assert "cache" in info.services

        # Compose file should be generated
        compose_file = lando_worktree / SENTINEL_COMPOSE_FILE
        assert compose_file.exists()

        # Verify compose file content
        with open(compose_file) as f:
            compose = yaml.safe_load(f)
        assert "services" in compose
        assert "appserver" in compose["services"]

        # Runner should have been called
        runner.up.assert_called_once()
        runner.wait_for_healthy.assert_called_once()

    def test_setup_python_project_noop(self, env_mgr, python_worktree):
        info = env_mgr.setup(python_worktree, "PYPROJ-001")

        assert info.active is False
        assert info.ticket_id == "PYPROJ-001"
        assert info.services == []

    @patch("src.environment_manager.ComposeRunner")
    def test_setup_fails_on_compose_error(self, MockRunner, env_mgr, lando_worktree):
        runner = MagicMock()
        runner.up.return_value = ComposeResult(
            success=False,
            stderr="Cannot connect to Docker daemon",
        )
        MockRunner.return_value = runner

        with pytest.raises(RuntimeError, match="Failed to start"):
            env_mgr.setup(lando_worktree, "STNL-002")

    @patch("src.environment_manager.ComposeRunner")
    def test_setup_fails_on_health_timeout(self, MockRunner, env_mgr, lando_worktree):
        runner = MagicMock()
        runner.up.return_value = ComposeResult(success=True)
        runner.wait_for_healthy.return_value = False
        runner.ps.return_value = [
            ServiceStatus(name="database", state="running", health="unhealthy"),
        ]
        runner.logs.return_value = ComposeResult(success=True, stdout="Error in MySQL")
        runner.down.return_value = ComposeResult(success=True)
        MockRunner.return_value = runner

        with pytest.raises(RuntimeError, match="failed to become healthy"):
            env_mgr.setup(lando_worktree, "STNL-003")

        # Should clean up on failure
        runner.down.assert_called_once()

    def test_setup_disabled_by_config(self, env_mgr, lando_worktree):
        # Override config to disable auto-detect
        env_mgr.config.get.return_value = {
            "auto_detect": False,
            "runtime": "dood",
        }

        info = env_mgr.setup(lando_worktree, "STNL-004")
        assert info.active is False


class TestTeardown:
    """Test environment teardown."""

    @patch("src.environment_manager.ComposeRunner")
    def test_teardown_active_environment(self, MockRunner, env_mgr, lando_worktree):
        runner = MagicMock()
        runner.up.return_value = ComposeResult(success=True)
        runner.wait_for_healthy.return_value = True
        runner.down.return_value = ComposeResult(success=True)
        MockRunner.return_value = runner

        # Setup first
        env_mgr.setup(lando_worktree, "STNL-010")

        # Teardown
        success = env_mgr.teardown("STNL-010")
        assert success is True
        runner.down.assert_called_once_with(volumes=True)

    def test_teardown_inactive_noop(self, env_mgr):
        # Teardown with no active environment
        success = env_mgr.teardown("NONEXISTENT-001")
        assert success is True

    def test_teardown_python_project_noop(self, env_mgr, python_worktree):
        env_mgr.setup(python_worktree, "PYPROJ-002")
        success = env_mgr.teardown("PYPROJ-002")
        assert success is True

    @patch("src.environment_manager.ComposeRunner")
    def test_teardown_failure_attempts_cleanup(self, MockRunner, env_mgr, lando_worktree):
        runner = MagicMock()
        runner.up.return_value = ComposeResult(success=True)
        runner.wait_for_healthy.return_value = True
        runner.down.return_value = ComposeResult(success=False, stderr="error")
        runner.cleanup_orphans.return_value = ComposeResult(success=True)
        MockRunner.return_value = runner

        env_mgr.setup(lando_worktree, "STNL-011")
        success = env_mgr.teardown("STNL-011")

        assert success is False
        runner.cleanup_orphans.assert_called_once()


class TestExec:
    """Test command execution in containers."""

    @patch("src.environment_manager.ComposeRunner")
    def test_exec_in_running_service(self, MockRunner, env_mgr, lando_worktree):
        runner = MagicMock()
        runner.up.return_value = ComposeResult(success=True)
        runner.wait_for_healthy.return_value = True
        runner.exec.return_value = ComposeResult(
            success=True,
            stdout="PHP 8.2.0",
        )
        MockRunner.return_value = runner

        env_mgr.setup(lando_worktree, "STNL-020")
        result = env_mgr.exec("STNL-020", "appserver", "php -v")

        assert result.success
        assert "PHP 8.2" in result.stdout

    def test_exec_no_active_env_raises(self, env_mgr):
        with pytest.raises(RuntimeError, match="No active environment"):
            env_mgr.exec("NONEXISTENT", "appserver", "ls")

    @patch("src.environment_manager.ComposeRunner")
    def test_exec_callback(self, MockRunner, env_mgr, lando_worktree):
        runner = MagicMock()
        runner.up.return_value = ComposeResult(success=True)
        runner.wait_for_healthy.return_value = True
        runner.exec.return_value = ComposeResult(
            success=True,
            stdout="output",
            stderr="",
            returncode=0,
        )
        MockRunner.return_value = runner

        env_mgr.setup(lando_worktree, "STNL-021")
        callback = env_mgr.get_exec_callback("STNL-021")

        stdout, stderr, rc = callback("composer install")
        assert stdout == "output"
        assert rc == 0


class TestIsRunning:
    """Test running status check."""

    @patch("src.environment_manager.ComposeRunner")
    def test_is_running_active(self, MockRunner, env_mgr, lando_worktree):
        runner = MagicMock()
        runner.up.return_value = ComposeResult(success=True)
        runner.wait_for_healthy.return_value = True
        runner.ps.return_value = [
            ServiceStatus(name="appserver", state="running"),
        ]
        MockRunner.return_value = runner

        env_mgr.setup(lando_worktree, "STNL-030")
        assert env_mgr.is_running("STNL-030") is True

    def test_is_running_no_env(self, env_mgr):
        assert env_mgr.is_running("NONEXISTENT") is False


class TestPostStartCommands:
    """Test post-start command execution."""

    @patch("src.environment_manager.ComposeRunner")
    def test_runs_post_start_commands(self, MockRunner, env_mgr, tmp_path):
        # Create lando config with build_as_root
        lando_config = {
            "recipe": "drupal10",
            "services": {
                "appserver": {
                    "build_as_root": ["apt-get update", "apt-get install -y vim"],
                },
            },
        }
        (tmp_path / ".lando.yml").write_text(yaml.dump(lando_config))

        runner = MagicMock()
        runner.up.return_value = ComposeResult(success=True)
        runner.wait_for_healthy.return_value = True
        runner.exec.return_value = ComposeResult(success=True)
        MockRunner.return_value = runner

        env_mgr.setup(tmp_path, "STNL-040")

        # Should have called exec for post-start commands
        exec_calls = [
            call for call in runner.exec.call_args_list
            if "apt-get" in str(call)
        ]
        assert len(exec_calls) >= 1


class TestGetEnvironmentInfo:
    """Test environment info retrieval."""

    def test_get_info_exists(self, env_mgr, python_worktree):
        env_mgr.setup(python_worktree, "PYPROJ-003")
        info = env_mgr.get_environment_info("PYPROJ-003")
        assert info is not None
        assert info.ticket_id == "PYPROJ-003"

    def test_get_info_not_exists(self, env_mgr):
        info = env_mgr.get_environment_info("NONEXISTENT")
        assert info is None
