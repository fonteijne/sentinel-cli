"""Tests for Docker Compose runner."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from src.compose_runner import ComposeRunner, ComposeResult, ServiceStatus


class TestComposeRunnerInit:
    """Test runner initialization."""

    @patch("subprocess.run")
    def test_finds_docker_compose_v2(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Docker Compose version v2.24.0")
        runner = ComposeRunner(project_name="test")
        assert runner._docker_compose_cmd == ["docker", "compose"]

    @patch("shutil.which", return_value="/usr/bin/docker-compose")
    @patch("subprocess.run")
    def test_falls_back_to_standalone(self, mock_run, mock_which):
        mock_run.side_effect = FileNotFoundError()
        runner = ComposeRunner(project_name="test")
        assert runner._docker_compose_cmd == ["docker-compose"]

    @patch("shutil.which", return_value=None)
    @patch("subprocess.run")
    def test_raises_if_no_compose(self, mock_run, mock_which):
        mock_run.side_effect = FileNotFoundError()
        with pytest.raises(RuntimeError, match="Docker Compose not found"):
            ComposeRunner(project_name="test")


class TestBuildCommand:
    """Test command building."""

    @patch("subprocess.run")
    def test_includes_project_name(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        runner = ComposeRunner(project_name="sentinel-STNL-001")

        cmd = runner._build_cmd("up", "-d")
        assert "-p" in cmd
        assert "sentinel-STNL-001" in cmd
        assert "up" in cmd
        assert "-d" in cmd

    @patch("subprocess.run")
    def test_includes_compose_file(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        runner = ComposeRunner(
            compose_file=Path("/workspace/projects/STNL-001/docker-compose.sentinel.yml"),
            project_name="test",
        )

        cmd = runner._build_cmd("up")
        assert "-f" in cmd
        assert "/workspace/projects/STNL-001/docker-compose.sentinel.yml" in cmd


class TestUp:
    """Test docker compose up."""

    @patch("subprocess.run")
    def test_up_detached(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        result = runner.up()
        assert result.success

        call_args = mock_run.call_args[0][0]
        assert "up" in call_args
        assert "-d" in call_args
        assert "--remove-orphans" in call_args

    @patch("subprocess.run")
    def test_up_with_build(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        result = runner.up(build=True)
        call_args = mock_run.call_args[0][0]
        assert "--build" in call_args

    @patch("subprocess.run")
    def test_up_failure(self, mock_run):
        # First call: docker compose version (init), second call: up (fail)
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="v2.24.0"),
            MagicMock(returncode=1, stdout="", stderr="Error: service failed to start"),
        ]
        runner = ComposeRunner(project_name="test")

        result = runner.up()
        assert not result.success
        assert "failed" in result.stderr


class TestDown:
    """Test docker compose down."""

    @patch("subprocess.run")
    def test_down_with_volumes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        result = runner.down()
        assert result.success

        call_args = mock_run.call_args[0][0]
        assert "down" in call_args
        assert "-v" in call_args
        assert "--remove-orphans" in call_args

    @patch("subprocess.run")
    def test_down_without_volumes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        result = runner.down(volumes=False)
        call_args = mock_run.call_args[0][0]
        assert "-v" not in call_args


class TestExec:
    """Test docker compose exec."""

    @patch("subprocess.run")
    def test_exec_string_command(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Install complete",
            stderr="",
        )
        runner = ComposeRunner(project_name="test")

        result = runner.exec("appserver", "composer install")
        assert result.success
        assert result.stdout == "Install complete"

        call_args = mock_run.call_args[0][0]
        assert "-T" in call_args
        assert "appserver" in call_args
        assert "sh" in call_args
        assert "-c" in call_args
        assert "composer install" in call_args

    @patch("subprocess.run")
    def test_exec_list_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        result = runner.exec("appserver", ["php", "-v"])
        call_args = mock_run.call_args[0][0]
        assert "php" in call_args
        assert "-v" in call_args

    @patch("subprocess.run")
    def test_exec_with_user(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        runner.exec("appserver", "whoami", user="www-data")
        call_args = mock_run.call_args[0][0]
        assert "-u" in call_args
        assert "www-data" in call_args

    @patch("subprocess.run")
    def test_exec_with_workdir(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        runner.exec("appserver", "ls", workdir="/app/web")
        call_args = mock_run.call_args[0][0]
        assert "-w" in call_args
        assert "/app/web" in call_args


class TestPs:
    """Test docker compose ps."""

    @patch("subprocess.run")
    def test_ps_parses_json(self, mock_run):
        json_output = "\n".join([
            json.dumps({"Service": "appserver", "State": "running", "Health": "healthy"}),
            json.dumps({"Service": "database", "State": "running", "Health": "healthy"}),
        ])
        mock_run.return_value = MagicMock(returncode=0, stdout=json_output, stderr="")
        runner = ComposeRunner(project_name="test")

        services = runner.ps()
        assert len(services) == 2
        assert services[0].name == "appserver"
        assert services[0].state == "running"
        assert services[0].health == "healthy"

    @patch("subprocess.run")
    def test_ps_handles_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        services = runner.ps()
        assert services == []

    @patch("subprocess.run")
    def test_ps_handles_failure(self, mock_run):
        # First call: docker compose version (init), second call: ps (fail)
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="v2.24.0"),
            MagicMock(returncode=1, stdout="", stderr="error"),
        ]
        runner = ComposeRunner(project_name="test")

        services = runner.ps()
        assert services == []


class TestLogs:
    """Test docker compose logs."""

    @patch("subprocess.run")
    def test_logs_all_services(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="log output", stderr="")
        runner = ComposeRunner(project_name="test")

        result = runner.logs()
        assert result.success
        call_args = mock_run.call_args[0][0]
        assert "logs" in call_args
        assert "--tail=50" in call_args

    @patch("subprocess.run")
    def test_logs_specific_service(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        runner.logs(service="database", tail=100)
        call_args = mock_run.call_args[0][0]
        assert "database" in call_args
        assert "--tail=100" in call_args


class TestWaitForHealthy:
    """Test health check polling."""

    @patch("subprocess.run")
    @patch("time.sleep")
    def test_healthy_immediately(self, mock_sleep, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        # Mock ps to return healthy services
        runner.ps = MagicMock(return_value=[
            ServiceStatus(name="appserver", state="running", health="healthy"),
            ServiceStatus(name="database", state="running", health="healthy"),
        ])

        assert runner.wait_for_healthy(timeout=10) is True

    @patch("subprocess.run")
    @patch("time.sleep")
    def test_waits_for_starting(self, mock_sleep, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        # First call: starting, second call: healthy
        runner.ps = MagicMock(side_effect=[
            [ServiceStatus(name="database", state="running", health="starting")],
            [ServiceStatus(name="database", state="running", health="healthy")],
        ])

        assert runner.wait_for_healthy(timeout=30) is True
        assert mock_sleep.call_count == 1

    @patch("subprocess.run")
    @patch("time.sleep")
    def test_fails_on_exited_service(self, mock_sleep, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        runner.ps = MagicMock(return_value=[
            ServiceStatus(name="database", state="exited", health=""),
        ])

        assert runner.wait_for_healthy(timeout=10) is False

    @patch("subprocess.run")
    @patch("time.time")
    @patch("time.sleep")
    def test_times_out(self, mock_sleep, mock_time, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        # Simulate time passing beyond timeout
        mock_time.side_effect = [0, 0, 5, 10, 15, 20, 25, 999]

        runner.ps = MagicMock(return_value=[
            ServiceStatus(name="database", state="running", health="starting"),
        ])

        assert runner.wait_for_healthy(timeout=20) is False

    @patch("subprocess.run")
    @patch("time.sleep")
    def test_services_without_healthcheck(self, mock_sleep, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        runner = ComposeRunner(project_name="test")

        # Services without healthcheck have empty health field
        runner.ps = MagicMock(return_value=[
            ServiceStatus(name="redis", state="running", health=""),
            ServiceStatus(name="appserver", state="running", health=""),
        ])

        assert runner.wait_for_healthy(timeout=10) is True


class TestTimeout:
    """Test command timeout handling."""

    @patch("subprocess.run")
    def test_command_timeout(self, mock_run):
        import subprocess as sp
        # First call: docker compose version (init), second call: up (timeout)
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="v2.24.0"),
            sp.TimeoutExpired(cmd="docker compose up", timeout=120),
        ]
        runner = ComposeRunner(project_name="test")

        result = runner.up()
        assert not result.success
        assert "timed out" in result.stderr
