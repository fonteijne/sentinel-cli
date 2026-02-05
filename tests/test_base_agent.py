"""Unit tests for BaseAgent and agent base classes."""

from typing import Any
from unittest.mock import Mock, patch

import pytest

from src.agents.base_agent import (
    BaseAgent,
    ImplementationAgent,
    PlanningAgent,
    ReviewAgent,
)


class ConcreteAgent(BaseAgent):
    """Concrete implementation for testing BaseAgent."""

    def run(self, **kwargs: Any) -> Any:
        """Test implementation of run method."""
        return {"status": "success", "kwargs": kwargs}


@pytest.fixture
def mock_config():
    """Mock configuration loader."""
    with patch("src.agents.base_agent.get_config") as mock:
        config = Mock()
        config.get_agent_config.return_value = {
            "model": "claude-4-5-haiku",
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
        # Mock the async method with a coroutine matching actual signature
        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {
                "content": "Test response from LLM",
                "tool_uses": [],
                "session_id": "test-session-123"
            }
        wrapper.execute_with_tools = mock_execute
        wrapper.set_project = Mock()
        wrapper.agent_name = "test_agent"
        wrapper.model = "claude-4-5-haiku"
        wrapper.llm_mode = "custom_proxy"
        wrapper.allowed_tools = ["Read", "Grep", "Glob"]
        mock.return_value = wrapper
        yield wrapper


@pytest.fixture
def mock_prompt():
    """Mock prompt loader."""
    with patch("src.agents.base_agent.load_agent_prompt") as mock:
        mock.return_value = "Test system prompt"
        yield mock


@pytest.fixture
def mock_command_executor():
    """Mock command executor."""
    with patch("src.agents.base_agent.execute_command") as mock:
        mock.return_value = {"success": True, "output": "Command executed"}
        yield mock


class TestBaseAgent:
    """Test suite for BaseAgent class."""

    def test_init_default_params(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test initialization with default parameters."""
        agent = ConcreteAgent("test_agent")

        assert agent.agent_name == "test_agent"
        assert agent.model == "claude-4-5-haiku"
        assert agent.temperature == 0.2
        assert agent.system_prompt == "Test system prompt"
        assert agent.messages == []

        mock_config.get_agent_config.assert_called_once_with("test_agent")
        mock_prompt.assert_called_once_with("test_agent")

    def test_init_custom_model(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test initialization with custom model."""
        agent = ConcreteAgent("test_agent", model="claude-opus-4-5")

        assert agent.model == "claude-opus-4-5"

    def test_init_custom_temperature(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test initialization with custom temperature."""
        agent = ConcreteAgent("test_agent", temperature=0.8)

        assert agent.temperature == 0.8

    def test_init_prompt_not_found(self, mock_config, mock_agent_sdk):
        """Test initialization when prompt file is not found."""
        with patch("src.agents.base_agent.load_agent_prompt") as mock_prompt:
            mock_prompt.side_effect = FileNotFoundError("Prompt not found")

            agent = ConcreteAgent("test_agent")

            assert agent.system_prompt == ""

    def test_add_system_message_first_time(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test adding system message when messages list is empty."""
        agent = ConcreteAgent("test_agent")
        agent._add_system_message()

        assert len(agent.messages) == 1
        assert agent.messages[0]["role"] == "system"
        assert agent.messages[0]["content"] == "Test system prompt"

    def test_add_system_message_already_exists(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test that system message is not duplicated."""
        agent = ConcreteAgent("test_agent")
        agent.messages = [{"role": "system", "content": "Existing system prompt"}]

        agent._add_system_message()

        assert len(agent.messages) == 1
        assert agent.messages[0]["content"] == "Existing system prompt"

    def test_add_system_message_inserts_at_beginning(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test that system message is inserted at the beginning."""
        agent = ConcreteAgent("test_agent")
        agent.messages = [
            {"role": "user", "content": "User message"},
            {"role": "assistant", "content": "Assistant message"},
        ]

        agent._add_system_message()

        assert len(agent.messages) == 3
        assert agent.messages[0]["role"] == "system"
        assert agent.messages[1]["role"] == "user"

    def test_send_message_basic(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test sending a basic message."""
        agent = ConcreteAgent("test_agent")

        response = agent.send_message("Hello, agent!")

        assert response == "Test response from LLM"
        assert len(agent.messages) == 2  # user, assistant (system not in messages)

        # Check user message was added
        assert agent.messages[0]["role"] == "user"
        assert agent.messages[0]["content"] == "Hello, agent!"

        # Check assistant response was added
        assert agent.messages[1]["role"] == "assistant"
        assert agent.messages[1]["content"] == "Test response from LLM"

    def test_send_message_custom_role(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test sending a message with custom role."""
        agent = ConcreteAgent("test_agent")

        response = agent.send_message("Test content", role="assistant")

        # Should have assistant + assistant (from response)
        assert len(agent.messages) == 2
        assert agent.messages[0]["role"] == "assistant"
        assert agent.messages[0]["content"] == "Test content"

    def test_send_message_not_implemented_error(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test send_message when Agent SDK raises exception."""
        # Mock to raise exception
        async def mock_execute_error(prompt, session_id=None):
            raise RuntimeError("Agent SDK error")

        mock_agent_sdk.execute_with_tools = mock_execute_error

        agent = ConcreteAgent("test_agent")

        # Should raise exception (no longer catches NotImplementedError)
        try:
            response = agent.send_message("Test")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "Agent SDK error" in str(e)

    def test_send_message_maintains_history(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test that message history is maintained across multiple calls."""
        agent = ConcreteAgent("test_agent")

        agent.send_message("First message")
        agent.send_message("Second message")

        # user1 + assistant1 + user2 + assistant2
        assert len(agent.messages) == 4
        assert agent.messages[0]["content"] == "First message"
        assert agent.messages[2]["content"] == "Second message"

    def test_execute_command(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_command_executor
    ):
        """Test executing a custom command."""
        agent = ConcreteAgent("test_agent")

        result = agent.execute_command(
            "test-command", {"param1": "value1", "param2": "value2"}
        )

        assert result["success"] is True
        assert result["output"] == "Command executed"

        mock_command_executor.assert_called_once_with(
            "test-command",
            {"param1": "value1", "param2": "value2"},
            agent_type="test_agent",
        )

    def test_clear_history(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test clearing message history."""
        agent = ConcreteAgent("test_agent")
        agent.messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "User"},
            {"role": "assistant", "content": "Assistant"},
        ]

        agent.clear_history()

        assert len(agent.messages) == 0

    def test_get_history(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test getting message history."""
        agent = ConcreteAgent("test_agent")
        agent.messages = [
            {"role": "system", "content": "System"},
            {"role": "user", "content": "User"},
        ]

        history = agent.get_history()

        assert len(history) == 2
        assert history[0]["role"] == "system"
        assert history[1]["role"] == "user"

        # Verify it's a copy
        history.append({"role": "assistant", "content": "Test"})
        assert len(agent.messages) == 2  # Original unchanged

    def test_run_must_be_implemented(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that run method must be implemented by subclasses."""
        agent = ConcreteAgent("test_agent")
        result = agent.run(test_param="value")

        assert result["status"] == "success"
        assert result["kwargs"]["test_param"] == "value"

    def test_system_prompt_added_on_first_send(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test that system prompt is included in prompts (not in messages)."""
        agent = ConcreteAgent("test_agent")

        assert len(agent.messages) == 0

        agent.send_message("Test")

        # System prompt not in messages list - it's in _build_prompt()
        assert agent.messages[0]["role"] == "user"
        assert agent.messages[0]["content"] == "Test"
        # Verify system prompt was set during init
        assert agent.system_prompt == "Test system prompt"


class TestPlanningAgent:
    """Test suite for PlanningAgent class."""

    def test_init(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test PlanningAgent initialization."""

        class ConcretePlanning(PlanningAgent):
            def run(self, **kwargs: Any) -> Any:
                return {}

        agent = ConcretePlanning("test_planner")

        assert agent.agent_name == "test_planner"
        assert isinstance(agent, BaseAgent)
        assert isinstance(agent, PlanningAgent)

    def test_inherits_base_functionality(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that PlanningAgent inherits all base functionality."""

        class ConcretePlanning(PlanningAgent):
            def run(self, **kwargs: Any) -> Any:
                return {}

        agent = ConcretePlanning("test_planner", model="claude-opus-4-5")

        assert agent.model == "claude-opus-4-5"
        assert hasattr(agent, "send_message")
        assert hasattr(agent, "execute_command")


class TestImplementationAgent:
    """Test suite for ImplementationAgent class."""

    def test_init(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test ImplementationAgent initialization."""

        class ConcreteImplementation(ImplementationAgent):
            def run(self, **kwargs: Any) -> Any:
                return {}

        agent = ConcreteImplementation("test_impl")

        assert agent.agent_name == "test_impl"
        assert isinstance(agent, BaseAgent)
        assert isinstance(agent, ImplementationAgent)

    def test_inherits_base_functionality(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that ImplementationAgent inherits all base functionality."""

        class ConcreteImplementation(ImplementationAgent):
            def run(self, **kwargs: Any) -> Any:
                return {}

        agent = ConcreteImplementation("test_impl", temperature=0.5)

        assert agent.temperature == 0.5
        assert hasattr(agent, "send_message")
        assert hasattr(agent, "execute_command")


class TestReviewAgent:
    """Test suite for ReviewAgent class."""

    def test_init_without_veto(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test ReviewAgent initialization without veto power."""

        class ConcreteReview(ReviewAgent):
            def run(self, **kwargs: Any) -> Any:
                return {}

        agent = ConcreteReview("test_reviewer")

        assert agent.agent_name == "test_reviewer"
        assert agent.veto_power is False
        assert isinstance(agent, BaseAgent)
        assert isinstance(agent, ReviewAgent)

    def test_init_with_veto(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test ReviewAgent initialization with veto power."""

        class ConcreteReview(ReviewAgent):
            def run(self, **kwargs: Any) -> Any:
                return {}

        agent = ConcreteReview("test_reviewer", veto_power=True)

        assert agent.veto_power is True

    def test_inherits_base_functionality(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that ReviewAgent inherits all base functionality."""

        class ConcreteReview(ReviewAgent):
            def run(self, **kwargs: Any) -> Any:
                return {}

        agent = ConcreteReview("test_reviewer", model="claude-sonnet-4-5")

        assert agent.model == "claude-sonnet-4-5"
        assert hasattr(agent, "send_message")
        assert hasattr(agent, "execute_command")
