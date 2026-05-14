"""Tests for the Phase 2B Task 8 cancellation seam.

Two surfaces under test:

  1. ``AgentSDKWrapper.request_cancel`` / ``wait_for_idle`` — the bare seam
     that lets a caller signal an in-flight ``client.receive_response()`` loop
     to stop on its next message and wait for the stream to drain. The wrapper
     never exposes ``_stream_active`` to user code; tests poke it directly to
     simulate "stream is running".

  2. ``BaseAgent._safe_reset_session`` — the consumer of the seam. Replaces
     bare ``self.session_id = None; self.messages.clear()`` calls so a
     concurrent SDK stream cannot lose its session-id binding mid-flight.

The wrapper is constructed via ``__new__`` to avoid initializing the SDK,
config loader, guardrails, etc. — none of those affect the cancellation flags.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import Mock

import pytest

from src.agent_sdk_wrapper import AgentSDKWrapper
from src.agents.base_agent import BaseAgent


# ---------------------------------------------------------------------------
# Helpers — bare wrapper without __init__ side-effects.
# ---------------------------------------------------------------------------


def _bare_wrapper() -> AgentSDKWrapper:
    """Construct a wrapper instance bypassing ``__init__`` so we don't load
    config or guardrails. The cancellation seam only touches two flags.
    """
    w = AgentSDKWrapper.__new__(AgentSDKWrapper)
    w._stream_active = False
    w._cancel_requested = False
    return w


# ---------------------------------------------------------------------------
# 1. request_cancel flips the flag.
# ---------------------------------------------------------------------------


def test_request_cancel_sets_flag():
    w = _bare_wrapper()
    assert w._cancel_requested is False
    w.request_cancel()
    assert w._cancel_requested is True


# ---------------------------------------------------------------------------
# 2. wait_for_idle returns immediately when stream is already idle.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_idle_returns_immediately_when_idle():
    w = _bare_wrapper()
    w._stream_active = False
    t0 = time.monotonic()
    await w.wait_for_idle(timeout=2.0)
    assert time.monotonic() - t0 < 0.2  # essentially zero


# ---------------------------------------------------------------------------
# 3. wait_for_idle waits then returns when a fake stream goes idle.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_idle_returns_when_stream_goes_idle():
    w = _bare_wrapper()
    w._stream_active = True

    async def flip_after_delay() -> None:
        await asyncio.sleep(0.1)
        w._stream_active = False

    asyncio.create_task(flip_after_delay())
    t0 = time.monotonic()
    await w.wait_for_idle(timeout=2.0)
    elapsed = time.monotonic() - t0
    # Returned shortly after the flip — well under the 2 s timeout, and at
    # least a fraction of the 100 ms delay.
    assert elapsed < 1.0
    assert elapsed >= 0.05
    assert w._stream_active is False


# ---------------------------------------------------------------------------
# 4. wait_for_idle honors timeout when stream stays active forever.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_idle_honors_timeout():
    w = _bare_wrapper()
    w._stream_active = True  # never flips

    t0 = time.monotonic()
    await w.wait_for_idle(timeout=0.2)
    elapsed = time.monotonic() - t0
    # Returns close to the timeout, never raises.
    assert 0.15 <= elapsed < 0.5
    # Stream still considered active — the seam doesn't force-clear.
    assert w._stream_active is True


# ---------------------------------------------------------------------------
# 5. _safe_reset_session: clears state and asks the SDK to cancel.
# ---------------------------------------------------------------------------


class _ConcreteAgent(BaseAgent):
    """Minimal concrete subclass — BaseAgent is abstract via ``run``.

    We only need the inherited ``_safe_reset_session`` method; ``run`` is
    never invoked in these tests.
    """

    def run(self, *args, **kwargs):  # pragma: no cover — never called
        raise NotImplementedError


class _FakeAgentSDK:
    """Records ``request_cancel`` calls; ``wait_for_idle`` returns immediately.

    Mirrors the seam shape on ``AgentSDKWrapper`` — only the methods
    ``_safe_reset_session`` actually calls. We don't subclass the real
    wrapper so we don't need to construct one.
    """

    def __init__(self) -> None:
        self.cancel_calls = 0
        self.wait_calls = 0

    def request_cancel(self) -> None:
        self.cancel_calls += 1

    async def wait_for_idle(self, timeout: float = 5.0) -> None:
        self.wait_calls += 1


def test_safe_reset_session_clears_state_and_cancels():
    """Bare BaseAgent instance via ``__new__`` — we don't need a real SDK or
    config to test the reset semantics; only the three attributes the helper
    touches.
    """
    agent = _ConcreteAgent.__new__(_ConcreteAgent)
    fake = _FakeAgentSDK()
    agent.agent_sdk = fake
    agent.session_id = "sess-1"
    agent.messages = [{"role": "user", "content": "hi"}]

    agent._safe_reset_session()

    assert fake.cancel_calls == 1
    assert agent.session_id is None
    assert agent.messages == []


def test_safe_reset_session_proceeds_when_no_sdk_attached():
    """If no SDK wrapper is attached (subclass without it), the helper still
    clears session state — the cancel call is best-effort.
    """
    agent = _ConcreteAgent.__new__(_ConcreteAgent)
    # Deliberately no agent_sdk attribute.
    agent.session_id = "sess-2"
    agent.messages = [{"role": "user", "content": "hi"}]

    agent._safe_reset_session()

    assert agent.session_id is None
    assert agent.messages == []


def test_safe_reset_session_swallows_request_cancel_errors():
    """A flaky ``request_cancel`` must not block the reset — session state is
    still cleared.
    """
    agent = _ConcreteAgent.__new__(_ConcreteAgent)
    sdk = Mock()
    sdk.request_cancel = Mock(side_effect=RuntimeError("boom"))

    async def _idle(timeout: float = 5.0) -> None:
        return None

    sdk.wait_for_idle = _idle
    agent.agent_sdk = sdk
    agent.session_id = "sess-3"
    agent.messages = [{"role": "user", "content": "hi"}]

    agent._safe_reset_session()  # must not raise

    assert agent.session_id is None
    assert agent.messages == []
