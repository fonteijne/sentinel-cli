"""Unit tests for Agent SDK wrapper."""

import pytest
from unittest.mock import AsyncMock, patch
from src.agent_sdk_wrapper import AgentSDKWrapper
from src.config_loader import get_config


@pytest.fixture
def mock_agent_sdk_client():
    """Mock Agent SDK client for all tests."""
    with patch("src.agent_sdk_wrapper.ClaudeSDKClient") as mock:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None

        # Mock async iteration
        async def mock_receive():
            from claude_agent_sdk.types import AssistantMessage, TextBlock
            yield AssistantMessage(content=[
                TextBlock(text="Test response from Agent SDK")
            ], model="claude-4-5-haiku")

        client.receive_response = mock_receive
        client.session_id = "test-session-123"

        mock.return_value = client
        yield client


def test_initialization_with_config():
    """Test AgentSDKWrapper initializes with config."""
    config = get_config()
    wrapper = AgentSDKWrapper("test_agent", config)

    assert wrapper.agent_name == "test_agent"
    assert wrapper.llm_mode == "subscription"  # Default mode before set_project


def test_custom_proxy_mode_detection():
    """Test custom proxy mode when both API_KEY and BASE_URL set."""
    import os
    # Save original values
    orig_api_key = os.environ.get("ANTHROPIC_API_KEY")
    orig_base_url = os.environ.get("ANTHROPIC_BASE_URL")
    orig_auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")

    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    os.environ["ANTHROPIC_BASE_URL"] = "https://proxy.example.com"
    try:
        config = get_config()
        wrapper = AgentSDKWrapper("test_agent", config)
        wrapper.set_project("TEST")
        assert wrapper.llm_mode == "custom_proxy"
        assert os.environ.get("ANTHROPIC_AUTH_TOKEN") == "test-key"
        assert os.environ.get("ANTHROPIC_BASE_URL") == "https://proxy.example.com"
    finally:
        # Restore original values
        if orig_api_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = orig_api_key
        else:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        if orig_base_url is not None:
            os.environ["ANTHROPIC_BASE_URL"] = orig_base_url
        else:
            os.environ.pop("ANTHROPIC_BASE_URL", None)
        if orig_auth_token is not None:
            os.environ["ANTHROPIC_AUTH_TOKEN"] = orig_auth_token
        else:
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


def test_direct_api_mode_detection():
    """Test direct API mode when only API_KEY set."""
    import os
    # Save original values
    orig_api_key = os.environ.get("ANTHROPIC_API_KEY")
    orig_base_url = os.environ.get("ANTHROPIC_BASE_URL")
    orig_auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")

    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
    os.environ.pop("ANTHROPIC_BASE_URL", None)
    try:
        config = get_config()
        wrapper = AgentSDKWrapper("test_agent", config)
        wrapper.set_project("TEST")
        # Should be direct_api mode
        assert wrapper.llm_mode == "direct_api"
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-test-key"
        # AUTH_TOKEN and BASE_URL should be cleared
        assert os.environ.get("ANTHROPIC_AUTH_TOKEN") is None
        assert os.environ.get("ANTHROPIC_BASE_URL") is None
    finally:
        # Restore original values
        if orig_api_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = orig_api_key
        else:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        if orig_base_url is not None:
            os.environ["ANTHROPIC_BASE_URL"] = orig_base_url
        else:
            os.environ.pop("ANTHROPIC_BASE_URL", None)
        if orig_auth_token is not None:
            os.environ["ANTHROPIC_AUTH_TOKEN"] = orig_auth_token
        else:
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


def test_subscription_mode_detection():
    """Test subscription mode when no credentials set."""
    import os
    # Save original values
    orig_api_key = os.environ.get("ANTHROPIC_API_KEY")
    orig_base_url = os.environ.get("ANTHROPIC_BASE_URL")
    orig_auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")

    os.environ.pop("ANTHROPIC_API_KEY", None)
    os.environ.pop("ANTHROPIC_BASE_URL", None)
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
    try:
        config = get_config()
        wrapper = AgentSDKWrapper("test_agent", config)
        wrapper.set_project("TEST")
        assert wrapper.llm_mode == "subscription"
        # All auth env vars should be cleared
        assert os.environ.get("ANTHROPIC_API_KEY") is None
        assert os.environ.get("ANTHROPIC_AUTH_TOKEN") is None
        assert os.environ.get("ANTHROPIC_BASE_URL") is None
    finally:
        # Restore original values
        if orig_api_key is not None:
            os.environ["ANTHROPIC_API_KEY"] = orig_api_key
        if orig_base_url is not None:
            os.environ["ANTHROPIC_BASE_URL"] = orig_base_url
        if orig_auth_token is not None:
            os.environ["ANTHROPIC_AUTH_TOKEN"] = orig_auth_token


@pytest.fixture
def setup_test_env():
    """Set up test environment with custom proxy credentials."""
    import os
    # Save original values
    orig_api_key = os.environ.get("ANTHROPIC_API_KEY")
    orig_base_url = os.environ.get("ANTHROPIC_BASE_URL")
    orig_auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")

    # Set test credentials for custom proxy mode
    os.environ["ANTHROPIC_API_KEY"] = "test-key"
    os.environ["ANTHROPIC_BASE_URL"] = "https://test.proxy.com"

    yield

    # Restore original values
    if orig_api_key is not None:
        os.environ["ANTHROPIC_API_KEY"] = orig_api_key
    else:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    if orig_base_url is not None:
        os.environ["ANTHROPIC_BASE_URL"] = orig_base_url
    else:
        os.environ.pop("ANTHROPIC_BASE_URL", None)
    if orig_auth_token is not None:
        os.environ["ANTHROPIC_AUTH_TOKEN"] = orig_auth_token
    else:
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


@pytest.mark.asyncio
async def test_execute_with_tools_returns_response(mock_agent_sdk_client, setup_test_env):
    """Test execute_with_tools returns response."""
    config = get_config()
    wrapper = AgentSDKWrapper("test_agent", config)
    wrapper.set_project("TEST")

    response = await wrapper.execute_with_tools("Test prompt")

    assert "content" in response
    assert "tool_uses" in response
    assert "session_id" in response
    assert response["content"] == "Test response from Agent SDK"


@pytest.mark.asyncio
async def test_session_id_preserved_across_calls(mock_agent_sdk_client, setup_test_env):
    """Test that session ID is preserved across calls."""
    config = get_config()
    wrapper = AgentSDKWrapper("test_agent", config)
    wrapper.set_project("TEST")

    response1 = await wrapper.execute_with_tools("First message")
    session_id = response1["session_id"]

    response2 = await wrapper.execute_with_tools("Second message", session_id=session_id)

    assert response2["session_id"] == session_id
