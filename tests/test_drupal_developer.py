"""Unit tests for DrupalDeveloperAgent."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from src.agents.drupal_developer import DrupalDeveloperAgent


@pytest.fixture
def mock_config():
    """Mock configuration loader."""
    with patch("src.agents.base_agent.get_config") as mock:
        config = Mock()
        config.get_agent_config.return_value = {
            "model": "claude-4-5-sonnet",
            "temperature": 0.2,
        }
        config.get_llm_config.return_value = {
            "mode": "custom_proxy",
            "api_key": "test-api-key",
            "base_url": "https://test.api.com/v1",
        }
        config.get.return_value = ["Read", "Grep", "Glob"]
        mock.return_value = config
        yield config


@pytest.fixture
def mock_agent_sdk():
    """Mock Agent SDK wrapper."""
    with patch("src.agents.base_agent.AgentSDKWrapper") as mock:
        wrapper = Mock()
        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {
                "content": "Test LLM response",
                "tool_uses": [],
                "session_id": "test-session-123"
            }
        wrapper.execute_with_tools = mock_execute
        wrapper.set_project = Mock()
        wrapper.agent_name = "drupal_developer"
        wrapper.model = "claude-4-5-sonnet"
        wrapper.llm_mode = "custom_proxy"
        wrapper.allowed_tools = ["Read", "Write", "Edit", "Grep", "Glob", "Bash"]
        mock.return_value = wrapper
        yield wrapper


@pytest.fixture
def mock_prompt():
    """Mock prompt loader."""
    with patch("src.agents.base_agent.load_agent_prompt") as mock:
        mock.return_value = "Developer system prompt"
        yield mock


@pytest.fixture
def mock_beads():
    """Mock BeadsManager."""
    with patch("src.agents.base_developer.BeadsManager") as mock:
        manager = Mock()
        manager.create_task.return_value = "task-123"
        mock.return_value = manager
        yield manager


@pytest.fixture
def temp_worktree():
    """Create a temporary directory for worktree."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_plan_file(temp_worktree):
    """Create a sample plan file for Drupal."""
    plan_path = temp_worktree / "plan.md"
    plan_content = """# Implementation Plan

## Overview
Implement webform submission handler.

## Step-by-Step Tasks

- [ ] Create hook_webform_submission_presave in webform_hooks.module
- [ ] Add service definition in webform_hooks.services.yml
- [ ] Write PHPUnit test for submission handler

## Validation Commands
- phpcs --standard=Drupal
- vendor/bin/phpunit
"""
    plan_path.write_text(plan_content)
    return plan_path


class TestDrupalDeveloperAgent:
    """Test suite for DrupalDeveloperAgent class."""

    def test_init(self, mock_config, mock_agent_sdk, mock_prompt, mock_beads):
        """Test agent initialization."""
        agent = DrupalDeveloperAgent()

        assert agent.agent_name == "drupal_developer"
        assert agent.model == "claude-4-5-sonnet"
        assert agent.temperature == 0.2

    def test_init_loads_overlay(self, mock_config, mock_agent_sdk, mock_prompt, mock_beads):
        """Test that overlay is appended to system prompt."""
        agent = DrupalDeveloperAgent()

        # The overlay should be appended if the file exists
        overlay_path = Path(__file__).parent.parent / "prompts" / "overlays" / "drupal_developer.md"
        if overlay_path.exists():
            assert "Drupal Developer Overlay" in agent.system_prompt

    def test_get_test_command(self, mock_config, mock_agent_sdk, mock_prompt, mock_beads):
        """Test Drupal test command returns phpunit."""
        agent = DrupalDeveloperAgent()
        cmd = agent._get_test_command()

        assert cmd == ["vendor/bin/phpunit", "--testsuite=unit", "--no-coverage"]

    def test_get_test_command_is_not_pytest(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads
    ):
        """Test Drupal test command does NOT return pytest."""
        agent = DrupalDeveloperAgent()
        cmd = agent._get_test_command()

        assert "pytest" not in cmd

    def test_get_test_stub(self, mock_config, mock_agent_sdk, mock_prompt, mock_beads):
        """Test Drupal test stub is PHP, not Python."""
        agent = DrupalDeveloperAgent()
        stub = agent._get_test_stub()

        assert "<?php" in stub
        assert "UnitTestCase" in stub
        assert "public function testBasicFunctionality" in stub
        # Must NOT contain Python
        assert "import pytest" not in stub
        assert "def test_" not in stub

    def test_build_tdd_prompt_references_phpunit(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads, temp_worktree
    ):
        """Test Drupal TDD prompt references PHPUnit and Drupal concepts."""
        agent = DrupalDeveloperAgent()
        prompt = agent._build_tdd_prompt("Implement webform handler", {}, temp_worktree)

        assert "PHPUnit" in prompt
        assert "Drupal" in prompt
        assert "drush cr" in prompt
        assert "TASK: Implement webform handler" in prompt

    def test_build_tdd_prompt_no_python_references(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads, temp_worktree
    ):
        """Test Drupal TDD prompt does NOT reference Python concepts."""
        agent = DrupalDeveloperAgent()
        prompt = agent._build_tdd_prompt("Add feature", {}, temp_worktree)

        assert "pytest" not in prompt
        assert "PEP 8" not in prompt
        assert "type hints" not in prompt.lower() or "type hint" not in prompt.lower()

    def test_build_tdd_prompt_includes_di_guidance(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads, temp_worktree
    ):
        """Test Drupal TDD prompt includes dependency injection guidance."""
        agent = DrupalDeveloperAgent()
        prompt = agent._build_tdd_prompt("Add service", {}, temp_worktree)

        assert "dependency injection" in prompt.lower()
        assert "\\Drupal::service()" in prompt

    def test_run_tests_uses_phpunit(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads, temp_worktree
    ):
        """Test that run_tests calls phpunit, not pytest."""
        agent = DrupalDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout="OK (3 tests, 5 assertions)",
                stderr="",
            )

            result = agent.run_tests(temp_worktree)

            assert result["success"] is True
            call_args = mock_run.call_args
            assert call_args[0][0] == ["vendor/bin/phpunit", "--testsuite=unit", "--no-coverage"]
            assert call_args[1]["cwd"] == temp_worktree

    def test_run_tests_failure(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads, temp_worktree
    ):
        """Test running Drupal tests with failures."""
        agent = DrupalDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=1,
                stdout="FAILURES!\nTests: 3, Assertions: 4, Failures: 1.",
                stderr="",
            )

            result = agent.run_tests(temp_worktree)

            assert result["success"] is False
            assert result["return_code"] == 1

    def test_write_tests_creates_php_file(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads, temp_worktree
    ):
        """Test that write_tests creates a PHP test file."""
        agent = DrupalDeveloperAgent()

        test_path = temp_worktree / "tests" / "src" / "Unit" / "BasicTest.php"
        test_code = agent.write_tests("implementation", test_path)

        assert test_path.exists()
        assert "<?php" in test_code
        assert "UnitTestCase" in test_code
        assert "import pytest" not in test_code

    def test_run_complete_workflow(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads,
        sample_plan_file, temp_worktree
    ):
        """Test complete run workflow for Drupal."""
        agent = DrupalDeveloperAgent()

        with patch.object(agent, "implement_feature") as mock_implement, \
             patch.object(agent, "run_tests") as mock_test:

            mock_test.return_value = {
                "success": True,
                "return_code": 0,
                "output": "OK (3 tests, 5 assertions)",
            }

            result = agent.run(
                plan_file=sample_plan_file,
                worktree_path=temp_worktree,
            )

            assert "tasks_completed" in result
            assert "tasks_failed" in result
            assert "test_results" in result

            # Should attempt to implement each task
            assert mock_implement.call_count == 3

    def test_implement_feature_with_command(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads, temp_worktree
    ):
        """Test implementing a feature loads TDD command and runs Agent SDK."""
        agent = DrupalDeveloperAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "run_tests") as mock_tests:
            mock_execute.return_value = {
                "success": True,
                "workflow": [{"name": "write_failing_test"}],
            }
            mock_tests.return_value = {"success": True, "output": "", "return_code": 0}

            result = agent.implement_feature("Add form handler", {}, temp_worktree)

            mock_execute.assert_called_once_with(
                "implement-tdd",
                {
                    "feature_description": "Add form handler",
                    "plan_step": "Add form handler",
                },
            )
            assert result["success"] is True


class TestContainerAwareTests:
    """Test container-aware test execution for Drupal projects."""

    def test_set_environment_stores_values(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads
    ):
        """Test that set_environment stores env_manager and ticket_id."""
        agent = DrupalDeveloperAgent()
        mock_env_mgr = Mock()

        agent.set_environment(mock_env_mgr, "TEST-123")

        assert agent._env_manager is mock_env_mgr
        assert agent._env_ticket_id == "TEST-123"

    def test_run_tests_uses_container_when_env_set(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads, temp_worktree
    ):
        """Test that run_tests uses container exec when environment is attached."""
        agent = DrupalDeveloperAgent()
        mock_env_mgr = Mock()
        mock_env_mgr.exec.return_value = Mock(
            success=True,
            stdout="OK (3 tests, 5 assertions)",
            stderr="",
            returncode=0,
        )

        agent.set_environment(mock_env_mgr, "TEST-123")

        with patch("src.agents.base_developer.subprocess.run") as mock_subprocess:
            result = agent.run_tests(temp_worktree)

            # Container exec should be called
            mock_env_mgr.exec.assert_called_once_with(
                ticket_id="TEST-123",
                service="appserver",
                command=["vendor/bin/phpunit", "--testsuite=unit", "--no-coverage"],
            )

            # Host subprocess should NOT be called
            mock_subprocess.assert_not_called()

            assert result["success"] is True
            assert result["return_code"] == 0

    def test_run_tests_falls_back_to_host_without_env(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads, temp_worktree
    ):
        """Test that run_tests uses subprocess when no environment is attached."""
        agent = DrupalDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_subprocess:
            mock_subprocess.return_value = Mock(
                returncode=0,
                stdout="OK (3 tests, 5 assertions)",
                stderr="",
            )

            result = agent.run_tests(temp_worktree)

            # Host subprocess should be called
            mock_subprocess.assert_called_once()
            call_args = mock_subprocess.call_args
            assert call_args[0][0] == ["vendor/bin/phpunit", "--testsuite=unit", "--no-coverage"]
            assert call_args[1]["cwd"] == temp_worktree

            assert result["success"] is True

    def test_run_tests_container_failure_returns_gracefully(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads, temp_worktree
    ):
        """Test that container exec failures are handled gracefully."""
        agent = DrupalDeveloperAgent()
        mock_env_mgr = Mock()
        mock_env_mgr.exec.side_effect = RuntimeError("No active environment for TEST-123")

        agent.set_environment(mock_env_mgr, "TEST-123")

        result = agent.run_tests(temp_worktree)

        assert result["success"] is False
        assert result["return_code"] == -1
        assert "No active environment" in result["output"]

    def test_run_tests_container_test_failure(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_beads, temp_worktree
    ):
        """Test that failing tests in container are reported correctly."""
        agent = DrupalDeveloperAgent()
        mock_env_mgr = Mock()
        mock_env_mgr.exec.return_value = Mock(
            success=False,
            stdout="FAILURES!\nTests: 3, Assertions: 4, Failures: 1.",
            stderr="",
            returncode=1,
        )

        agent.set_environment(mock_env_mgr, "TEST-123")

        result = agent.run_tests(temp_worktree)

        assert result["success"] is False
        assert result["return_code"] == 1
        assert "FAILURES!" in result["output"]
