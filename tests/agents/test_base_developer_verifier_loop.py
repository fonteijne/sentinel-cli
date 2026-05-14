"""Phase 1 Loop A tests for BaseDeveloperAgent.

Covers:
  - Task 5: run_tests() new return shape (passed, test_results, structured_errors, return_code)
  - Task 7: capped retry loop, refine-prompt feedback, cap-out emission
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from src.agents.base_developer import (
    DeveloperCappedOutException,
    MAX_ATTEMPTS,
)
from src.agents.python_developer import PythonDeveloperAgent
from src.core.events import (
    BaseEvent,
    DeveloperCappedOut,
    StaticCheckRecorded,
    TestResultRecorded,
)


# ---------------------------------------------------------------------------
# Shared fixtures (mirror tests/test_python_developer.py)
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
            return {
                "content": "ok",
                "tool_uses": [],
                "session_id": "test-session-123",
            }

        wrapper.execute_with_tools = mock_execute
        wrapper.set_project = Mock()
        wrapper.agent_name = "python_developer"
        wrapper.model = "claude-4-5-sonnet"
        wrapper.llm_mode = "custom_proxy"
        wrapper.allowed_tools = ["Read", "Write", "Edit", "Grep", "Glob", "Bash"]
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


class _CapturingBus:
    """Tiny in-memory bus stand-in for unit tests.

    The real EventBus needs a SQLite connection. Tests at this layer do not
    care about persistence — they only need to assert which events were
    emitted in what order.
    """

    def __init__(self) -> None:
        self.events: list[BaseEvent] = []

    def publish(self, event: BaseEvent) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# Task 5 — return shape
# ---------------------------------------------------------------------------


def test_run_tests_returns_new_shape(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree
):
    """run_tests must return {passed, test_results, structured_errors, return_code}."""
    agent = PythonDeveloperAgent()

    with patch("src.agents.base_developer.subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            returncode=0,
            stdout="test_x.py PASSED",
            stderr="",
        )

        result = agent.run_tests(temp_worktree)

    assert set(result.keys()) >= {
        "passed",
        "test_results",
        "structured_errors",
        "return_code",
    }
    assert result["passed"] is True
    assert isinstance(result["structured_errors"], list)
    assert "PASSED" in result["test_results"]


def test_run_tests_failed_populates_structured_errors(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree
):
    """On failure, structured_errors comes from _parse_test_output."""
    agent = PythonDeveloperAgent()

    with patch("src.agents.base_developer.subprocess.run") as mock_run:
        mock_run.return_value = Mock(
            returncode=1,
            stdout="FAILED tests/test_a.py::test_one - AssertionError: nope\n",
            stderr="",
        )

        result = agent.run_tests(temp_worktree)

    assert result["passed"] is False
    assert result["return_code"] == 1
    # Pytest parser should have surfaced one structured error.
    assert len(result["structured_errors"]) == 1
    err = result["structured_errors"][0]
    assert err["rule"] == "test_failed"
    assert "tests/test_a.py" in err["file"]


# ---------------------------------------------------------------------------
# Task 7 — Loop A behavior
# ---------------------------------------------------------------------------


def _passing_test_result() -> dict:
    return {
        "passed": True,
        "test_results": "all good",
        "structured_errors": [],
        "return_code": 0,
    }


def _failing_test_result() -> dict:
    return {
        "passed": False,
        "test_results": "FAILED tests/test_a.py::test_one - boom",
        "structured_errors": [
            {
                "file": "tests/test_a.py",
                "line": 0,
                "rule": "test_failed",
                "message": "boom",
            }
        ],
        "return_code": 1,
    }


def _passing_static_result() -> dict:
    return {
        "passed": True,
        "test_results": "lint clean",
        "structured_errors": [],
        "return_code": 0,
    }


def test_loop_disabled_calls_single_shot(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree, monkeypatch
):
    """When DEV_VERIFIER_LOOP is unset/0, the legacy single-shot path runs."""
    monkeypatch.delenv("DEV_VERIFIER_LOOP", raising=False)
    agent = PythonDeveloperAgent()

    with patch.object(agent, "_implement_feature_single_shot") as single_shot, \
         patch.object(agent, "_implement_feature_with_loop") as loop_path:
        single_shot.return_value = {"success": True}

        agent.implement_feature("task", {}, temp_worktree)

    single_shot.assert_called_once()
    loop_path.assert_not_called()


def test_loop_enabled_passes_first_attempt(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree, monkeypatch
):
    """Flag on, first attempt passes both verifiers → one SDK call, success payload."""
    monkeypatch.setenv("DEV_VERIFIER_LOOP", "1")
    agent = PythonDeveloperAgent()

    sdk_calls = []

    async def fake_execute(prompt, session_id=None, system_prompt=None, cwd=None):
        sdk_calls.append(prompt)
        return {"content": "done", "tool_uses": []}

    agent.agent_sdk.execute_with_tools = fake_execute

    with patch.object(agent, "execute_command") as exec_cmd, \
         patch.object(agent, "run_tests") as run_tests, \
         patch.object(agent, "run_static_checks") as run_static:
        exec_cmd.return_value = {"success": True, "workflow": []}
        run_tests.return_value = _passing_test_result()
        run_static.return_value = _passing_static_result()

        result = agent.implement_feature("task", {}, temp_worktree)

    assert len(sdk_calls) == 1
    assert result["success"] is True
    assert result["attempts"] == 1


def test_loop_retries_with_structured_feedback_then_passes(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree, monkeypatch
):
    """First attempt fails, second passes → 2 SDK calls; 2nd prompt carries errors."""
    monkeypatch.setenv("DEV_VERIFIER_LOOP", "1")
    agent = PythonDeveloperAgent()

    sdk_calls: list[str] = []

    async def fake_execute(prompt, session_id=None, system_prompt=None, cwd=None):
        sdk_calls.append(prompt)
        return {"content": "iter", "tool_uses": []}

    agent.agent_sdk.execute_with_tools = fake_execute

    with patch.object(agent, "execute_command") as exec_cmd, \
         patch.object(agent, "run_tests") as run_tests, \
         patch.object(agent, "run_static_checks") as run_static:
        exec_cmd.return_value = {"success": True, "workflow": []}
        run_tests.side_effect = [
            _failing_test_result(),
            _passing_test_result(),
        ]
        run_static.side_effect = [
            _passing_static_result(),
            _passing_static_result(),
        ]

        result = agent.implement_feature("task", {}, temp_worktree)

    assert len(sdk_calls) == 2
    # Second prompt is the refine prompt: must mention attempt 2 of 3 and
    # surface the structured rule.
    second_prompt = sdk_calls[1]
    assert f"attempt 2 of {MAX_ATTEMPTS}" in second_prompt
    assert "test_failed" in second_prompt
    assert "boom" in second_prompt
    assert result["success"] is True
    assert result["attempts"] == 2


def test_loop_caps_at_three_when_developer_fails_forever(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree, monkeypatch
):
    """Failing forever → exactly MAX_ATTEMPTS SDK calls; raises; emits cap-out."""
    monkeypatch.setenv("DEV_VERIFIER_LOOP", "1")
    agent = PythonDeveloperAgent()

    bus = _CapturingBus()
    agent.set_event_bus(bus, execution_id="exec-123")

    sdk_calls: list[str] = []

    async def fake_execute(prompt, session_id=None, system_prompt=None, cwd=None):
        sdk_calls.append(prompt)
        return {"content": "iter", "tool_uses": []}

    agent.agent_sdk.execute_with_tools = fake_execute

    with patch.object(agent, "execute_command") as exec_cmd, \
         patch.object(agent, "run_tests") as run_tests, \
         patch.object(agent, "run_static_checks") as run_static:
        exec_cmd.return_value = {"success": True, "workflow": []}
        run_tests.return_value = _failing_test_result()
        run_static.return_value = _passing_static_result()

        with pytest.raises(DeveloperCappedOutException):
            agent.implement_feature("task", {}, temp_worktree)

    assert len(sdk_calls) == MAX_ATTEMPTS == 3

    # Bus should have seen MAX_ATTEMPTS TestResultRecorded events, the same
    # number of StaticCheckRecorded events, and exactly one DeveloperCappedOut.
    test_events = [e for e in bus.events if isinstance(e, TestResultRecorded)]
    static_events = [e for e in bus.events if isinstance(e, StaticCheckRecorded)]
    cap_events = [e for e in bus.events if isinstance(e, DeveloperCappedOut)]
    assert len(test_events) == MAX_ATTEMPTS
    assert len(static_events) == MAX_ATTEMPTS
    assert len(cap_events) == 1
    cap = cap_events[0]
    assert cap.attempts == MAX_ATTEMPTS
    assert cap.agent == "python_developer"
    # Errors carried as plain dicts, capped at 10.
    assert isinstance(cap.last_structured_errors, list)
    assert all(isinstance(e, dict) for e in cap.last_structured_errors)


def test_emit_no_op_when_no_bus_attached(
    mock_config, mock_agent_sdk, mock_prompt
):
    """_emit must not raise when no bus has been attached."""
    agent = PythonDeveloperAgent()
    # Constructing the event with execution_id="" mirrors the real loop —
    # the bus would normally fill ts on publish.
    agent._emit(
        TestResultRecorded(
            execution_id="",
            passed=True,
            attempt=1,
            structured_errors_count=0,
        )
    )
    # No exception, no events captured anywhere — just a silent no-op.
