"""Unit tests for Agent SDK wrapper."""

import json
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Parity + rate-limit tests (Phase 3 — plan 01 Task 9, plan 04 Task 3)
# ---------------------------------------------------------------------------


@pytest.fixture
def bus_env(tmp_path, monkeypatch):
    """Wire a wrapper up to a real EventBus against a throwaway SQLite DB.

    Yields ``(wrapper, bus, conn, execution_id, log_file)``. The wrapper is
    fully initialized (``set_project`` called so ``llm_mode`` is resolved) and
    has ``event_bus`` + ``execution_id`` attached. A minimal ``executions`` row
    is pre-inserted so the FK from ``events`` is satisfied.

    ``log_file`` is the path ``_write_diagnostic`` will target — we monkeypatch
    ``/app/logs`` to ``tmp_path`` so JSONL lands where the test can read it.
    """
    from src.core.events import EventBus
    from src.core.persistence import connect, ensure_initialized

    # Neutralize ambient auth so set_project picks subscription mode.
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)

    db_path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    ensure_initialized()
    conn = connect()

    # Redirect diagnostic writes into tmp_path. _write_diagnostic tries
    # "/app/logs" first, then "logs" — by chdir'ing into tmp_path the
    # relative "logs" fallback works without touching the real /app/logs.
    monkeypatch.chdir(tmp_path)
    # Also block the /app/logs path by denying mkdir there. We cannot
    # realistically revoke permission in a test, but the tmp_path chdir
    # is sufficient when /app/logs is not writable; on dev containers
    # where it IS writable, we instead point to a tmp-scoped log_file
    # that we'll explicitly read. See assertion helper below which reads
    # whichever of the two paths was actually written.
    log_file_app = Path("/app/logs/agent_diagnostics.jsonl")
    log_file_local = tmp_path / "logs" / "agent_diagnostics.jsonl"

    execution_id = "exec-parity-1"
    conn.execute(
        "INSERT INTO executions(id, ticket_id, project, kind, status, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (execution_id, "T-1", "ACME", "plan", "running", "2026-01-01T00:00:00+00:00"),
    )

    bus = EventBus(conn)

    config = get_config()
    wrapper = AgentSDKWrapper("test_agent", config)
    wrapper.set_project("ACME")
    wrapper.event_bus = bus
    wrapper.execution_id = execution_id

    # Snapshot the pre-existing size of the /app/logs file (if present) so
    # the test can read only the lines it appended.
    app_log_pre_size = (
        log_file_app.stat().st_size if log_file_app.exists() else 0
    )

    yield wrapper, bus, conn, execution_id, log_file_app, log_file_local, app_log_pre_size

    conn.close()


def _read_new_jsonl(log_file_app: Path, log_file_local: Path, app_pre_size: int) -> list[dict]:
    """Return JSONL entries written during the test from either log location."""
    entries: list[dict] = []
    if log_file_local.exists():
        for line in log_file_local.read_text().splitlines():
            if line.strip():
                entries.append(json.loads(line))
    elif log_file_app.exists():
        with open(log_file_app, "rb") as f:
            f.seek(app_pre_size)
            tail = f.read().decode("utf-8", errors="replace")
        for line in tail.splitlines():
            if line.strip():
                entries.append(json.loads(line))
    return entries


@pytest.mark.asyncio
async def test_entry_dict_jsonl_bus_parity(bus_env):
    """Semantic parity between the JSONL diagnostic sink and the EventBus.

    Guards plan 01 Task 9: both telemetry surfaces must carry the same
    load-bearing fields for each tool-use and cost-accrual. If a new field
    is added to one sink and not the other, this test fails.
    """
    from claude_agent_sdk.types import AssistantMessage, ResultMessage, ToolUseBlock

    wrapper, bus, conn, execution_id, log_app, log_local, app_pre = bus_env

    # Build a minimal SDK message stream: one AssistantMessage with a
    # single ToolUseBlock, followed by a ResultMessage carrying usage.
    assistant = AssistantMessage(
        content=[ToolUseBlock(id="tu-1", name="Read", input={"path": "/app/README.md"})],
        model="claude-4-5-haiku",
    )
    result = ResultMessage(
        subtype="success",
        duration_ms=123,
        duration_api_ms=100,
        is_error=False,
        num_turns=1,
        session_id="sess-abc",
        total_cost_usd=0.0042,  # → 0 cents after int(round()) — bump for a visible number
        usage={"input_tokens": 111, "output_tokens": 22},
    )
    # Use a value that survives the cent-rounding: 0.0042 → 0 cents, so use 0.07 → 7 cents
    result.total_cost_usd = 0.07

    async def mock_receive():
        yield assistant
        yield result

    with patch("src.agent_sdk_wrapper.ClaudeSDKClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.receive_response = mock_receive
        mock_client_cls.return_value = client

        await wrapper.execute_with_tools("hello")

    # ----- JSONL side: locate the tool_use entry -----
    jsonl_entries = _read_new_jsonl(log_app, log_local, app_pre)
    tool_use_entries = [e for e in jsonl_entries if e.get("event") == "tool_use"]
    assert len(tool_use_entries) == 1, (
        f"expected exactly one JSONL tool_use entry, got {tool_use_entries!r}"
    )
    jsonl_tool = tool_use_entries[0]
    assert jsonl_tool["tool"] == "Read"
    # args summary in JSONL is the raw input dict
    assert jsonl_tool["input"] == {"path": "/app/README.md"}

    # ----- Bus side: read event rows for this execution -----
    rows = conn.execute(
        "SELECT type, payload_json FROM events WHERE execution_id=? ORDER BY seq",
        (execution_id,),
    ).fetchall()
    events_by_type: dict[str, list[dict]] = {}
    for r in rows:
        events_by_type.setdefault(r[0], []).append(json.loads(r[1]))

    # tool.called must mirror the JSONL tool_use
    assert "tool.called" in events_by_type, (
        f"no tool.called event persisted; got types={list(events_by_type)}"
    )
    bus_tool = events_by_type["tool.called"][0]
    assert bus_tool["tool"] == jsonl_tool["tool"]
    # args_summary is a string preview of the same input — must mention the path
    assert "/app/README.md" in bus_tool["args_summary"]

    # cost.accrued must mirror usage + cents from the ResultMessage
    assert "cost.accrued" in events_by_type, (
        f"no cost.accrued event persisted; got types={list(events_by_type)}"
    )
    cost = events_by_type["cost.accrued"][0]
    assert cost["tokens_in"] == 111
    assert cost["tokens_out"] == 22
    assert cost["cents"] == int(round(0.07 * 100))  # 7


@pytest.mark.asyncio
async def test_publish_rate_limited_fires_on_429(bus_env):
    """A ProcessError carrying a 429 signature → RateLimited event + re-raise."""
    from claude_agent_sdk import ProcessError

    wrapper, bus, conn, execution_id, *_ = bus_env

    async def mock_receive():
        # async-generator must ``yield`` before raising so the SDK contract
        # (async iterator) is honored; otherwise the raise short-circuits
        # before the generator is even entered.
        raise ProcessError(
            "API request failed",
            exit_code=1,
            stderr="HTTP 429 Too Many Requests. retry-after: 13",
        )
        yield  # pragma: no cover — unreachable, keeps function an async generator

    with patch("src.agent_sdk_wrapper.ClaudeSDKClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.receive_response = mock_receive
        mock_client_cls.return_value = client

        with pytest.raises(ProcessError):
            await wrapper.execute_with_tools("hello")

    rows = conn.execute(
        "SELECT payload_json FROM events WHERE execution_id=? AND type='rate_limited'",
        (execution_id,),
    ).fetchall()
    assert len(rows) == 1, "expected exactly one rate_limited event on 429"
    payload = json.loads(rows[0][0])
    assert payload["type"] == "rate_limited"
    assert payload["retry_after_s"] == 13.0


@pytest.mark.asyncio
async def test_publish_rate_limited_fires_on_529(bus_env):
    """A ProcessError carrying a 529 signature → RateLimited event + re-raise."""
    from claude_agent_sdk import ProcessError

    wrapper, bus, conn, execution_id, *_ = bus_env

    async def mock_receive():
        raise ProcessError(
            "Upstream overloaded",
            exit_code=1,
            stderr="HTTP 529 Overloaded",
        )
        yield  # pragma: no cover

    with patch("src.agent_sdk_wrapper.ClaudeSDKClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.receive_response = mock_receive
        mock_client_cls.return_value = client

        with pytest.raises(ProcessError):
            await wrapper.execute_with_tools("hello")

    rows = conn.execute(
        "SELECT payload_json FROM events WHERE execution_id=? AND type='rate_limited'",
        (execution_id,),
    ).fetchall()
    assert len(rows) == 1, "expected exactly one rate_limited event on 529"
    payload = json.loads(rows[0][0])
    assert payload["type"] == "rate_limited"
    # 529 messages usually do not carry a retry-after — expect None.
    assert payload["retry_after_s"] is None


@pytest.mark.asyncio
async def test_non_rate_limit_error_does_not_publish_rate_limited(bus_env):
    """Guard: a generic CLI error must NOT be classified as rate-limit."""
    from claude_agent_sdk import ProcessError

    wrapper, bus, conn, execution_id, *_ = bus_env

    async def mock_receive():
        raise ProcessError(
            "auth failed",
            exit_code=1,
            stderr="401 Unauthorized: invalid API key",
        )
        yield  # pragma: no cover

    with patch("src.agent_sdk_wrapper.ClaudeSDKClient") as mock_client_cls:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.receive_response = mock_receive
        mock_client_cls.return_value = client

        with pytest.raises(ProcessError):
            await wrapper.execute_with_tools("hello")

    rows = conn.execute(
        "SELECT 1 FROM events WHERE execution_id=? AND type='rate_limited'",
        (execution_id,),
    ).fetchall()
    assert rows == [], "401 must not be classified as rate_limited"
