"""Tests for DrupalDeveloperAgent.run_static_checks (Phase 1, Task 6)."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from src.agents.drupal_developer import DrupalDeveloperAgent


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/test_drupal_developer.py:13-59)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config():
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
    with patch("src.agents.base_agent.AgentSDKWrapper") as mock:
        wrapper = Mock()

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {"content": "ok", "tool_uses": [], "session_id": "s"}

        wrapper.execute_with_tools = mock_execute
        wrapper.set_project = Mock()
        wrapper.agent_name = "drupal_developer"
        wrapper.model = "claude-4-5-sonnet"
        wrapper.llm_mode = "custom_proxy"
        wrapper.allowed_tools = ["Read", "Write", "Edit"]
        mock.return_value = wrapper
        yield wrapper


@pytest.fixture
def mock_prompt():
    with patch("src.agents.base_agent.load_agent_prompt") as mock:
        mock.return_value = "Developer system prompt"
        yield mock


@pytest.fixture
def temp_worktree():
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_static_checks_skips_without_container(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree
):
    """No env_manager attached → graceful skip, passed=True, no errors."""
    agent = DrupalDeveloperAgent()

    result = agent.run_static_checks(temp_worktree)

    assert result["passed"] is True
    assert result["structured_errors"] == []
    assert result["return_code"] == 0
    assert "Skipped" in result["test_results"]


def test_run_static_checks_calls_phpstan_and_composer(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree
):
    """With env attached and clean output, PHPStan + composer validate are invoked."""
    agent = DrupalDeveloperAgent()
    mock_env_mgr = Mock()
    # composer install (ensure deps), phpstan analyse, composer validate.
    ok = Mock(success=True, stdout='{"files":{}}', stderr="", returncode=0)
    composer_ok = Mock(
        success=True,
        stdout="./composer.json is valid\n",
        stderr="",
        returncode=0,
    )
    mock_env_mgr.exec.side_effect = [
        ok,            # composer install
        ok,            # phpstan analyse
        composer_ok,   # composer validate
    ]
    agent.set_environment(mock_env_mgr, "TEST-123")

    with patch.object(agent, "_ensure_composer_deps") as ensure_deps:
        ensure_deps.side_effect = (
            lambda: mock_env_mgr.exec(
                ticket_id="TEST-123",
                service="appserver",
                command=["composer", "install", "--no-interaction", "--no-progress"],
                workdir="/app",
            )
        )

        result = agent.run_static_checks(temp_worktree)

    ensure_deps.assert_called_once()

    # Pull out the actual commands invoked through env_manager.exec.
    commands = [call.kwargs["command"] for call in mock_env_mgr.exec.call_args_list]

    phpstan_invoked = any(
        cmd[0].endswith("phpstan") and "analyse" in cmd
        for cmd in commands
    )
    composer_validate_invoked = any(
        cmd[0] == "composer" and "validate" in cmd
        for cmd in commands
    )
    assert phpstan_invoked, f"phpstan not invoked: {commands}"
    assert composer_validate_invoked, f"composer validate not invoked: {commands}"

    assert result["passed"] is True
    assert result["structured_errors"] == []
    assert result["return_code"] == 0


def test_run_static_checks_aggregates_errors(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree
):
    """Failing PHPStan + failing composer → combined structured_errors, passed=False."""
    agent = DrupalDeveloperAgent()
    mock_env_mgr = Mock()

    phpstan_failing = Mock(
        success=False,
        stdout=(
            '{"totals":{"errors":2,"file_errors":2},"files":{'
            '"web/modules/custom/foo/foo.module":{"errors":2,"messages":['
            '{"message":"Variable $bar might not be defined.","line":12,'
            '"identifier":"variable.undefined"},'
            '{"message":"Call to undefined function nope().","line":34,'
            '"identifier":"function.notFound"}'
            "]}}}"
        ),
        stderr="",
        returncode=1,
    )
    composer_failing = Mock(
        success=False,
        stdout="./composer.json is invalid; the following errors were found:\n"
               " - require.foo/bar : invalid version constraint\n",
        stderr="",
        returncode=2,
    )

    # _ensure_composer_deps is patched out so it does not consume an exec call;
    # only the two static-check calls hit env_manager.exec.
    mock_env_mgr.exec.side_effect = [
        phpstan_failing,
        composer_failing,
    ]
    agent.set_environment(mock_env_mgr, "TEST-123")

    with patch.object(agent, "_ensure_composer_deps") as ensure_deps:
        result = agent.run_static_checks(temp_worktree)

    ensure_deps.assert_called_once()
    assert result["passed"] is False
    assert result["return_code"] == 1
    # 2 phpstan errors + 1 composer entry = at least 3.
    assert len(result["structured_errors"]) >= 3
    rules = [e["rule"] for e in result["structured_errors"]]
    assert "variable.undefined" in rules
    assert "function.notFound" in rules
    assert "composer_validate" in rules
