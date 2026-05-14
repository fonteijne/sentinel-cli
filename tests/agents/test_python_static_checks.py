"""Tests for PythonDeveloperAgent.run_static_checks (Phase 1, Task 6)."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from src.agents.python_developer import PythonDeveloperAgent


# ---------------------------------------------------------------------------
# Fixtures (mirror tests/test_python_developer.py)
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
        wrapper.agent_name = "python_developer"
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


_RUFF_PASS = "[]"
_RUFF_FAIL = (
    '[{"code":"F401","message":"unused import","filename":"foo.py",'
    '"location":{"row":3,"column":1}}]'
)


def test_run_static_checks_invokes_ruff_and_mypy(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree
):
    """Both ruff and mypy must be invoked through subprocess.run."""
    agent = PythonDeveloperAgent()

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Mock(returncode=0, stdout=_RUFF_PASS if cmd[0] == "ruff" else "", stderr="")

    with patch("src.agents.python_developer.subprocess.run", side_effect=fake_run):
        result = agent.run_static_checks(temp_worktree)

    # First call ruff, second call mypy (order matters for the implementation
    # but not for the contract; just assert both ran).
    binaries = [c[0] for c in calls]
    assert "ruff" in binaries
    assert "mypy" in binaries
    assert result["passed"] is True
    assert result["structured_errors"] == []
    assert result["return_code"] == 0


def test_run_static_checks_parses_ruff_json(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree
):
    """Failing ruff output must surface at least one StructuredError."""
    agent = PythonDeveloperAgent()

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ruff":
            return Mock(returncode=1, stdout=_RUFF_FAIL, stderr="")
        # mypy clean
        return Mock(returncode=0, stdout="", stderr="")

    with patch("src.agents.python_developer.subprocess.run", side_effect=fake_run):
        result = agent.run_static_checks(temp_worktree)

    assert result["passed"] is False
    assert len(result["structured_errors"]) >= 1
    rules = [e["rule"] for e in result["structured_errors"]]
    assert "F401" in rules
    files = [e["file"] for e in result["structured_errors"]]
    assert "foo.py" in files


def test_run_static_checks_skips_when_tools_missing(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree
):
    """Both binaries missing → graceful skip (passed=True, no errors)."""
    agent = PythonDeveloperAgent()

    with patch(
        "src.agents.python_developer.subprocess.run",
        side_effect=FileNotFoundError("no ruff"),
    ):
        result = agent.run_static_checks(temp_worktree)

    assert result["passed"] is True
    assert result["structured_errors"] == []
    assert "Skipped" in result["test_results"]


def test_run_static_checks_handles_mypy_failure(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree
):
    """Failing mypy output produces structured errors via parse_mypy."""
    agent = PythonDeveloperAgent()

    mypy_out = "src/foo.py:42: error: Incompatible types  [assignment]\n"

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ruff":
            return Mock(returncode=0, stdout=_RUFF_PASS, stderr="")
        return Mock(returncode=1, stdout=mypy_out, stderr="")

    with patch("src.agents.python_developer.subprocess.run", side_effect=fake_run):
        result = agent.run_static_checks(temp_worktree)

    assert result["passed"] is False
    rules = [e["rule"] for e in result["structured_errors"]]
    assert "assignment" in rules
