"""Rate-limiter unit tests.

The HTTP-shaped ``TokenRateLimiter`` is covered indirectly by the write-route
tests; this file focuses on ``WsConnectionLimiter`` which has no equivalent
integration coverage outside of ``test_stream.py``.
"""

from __future__ import annotations

import pytest

from src.service.rate_limit import WsConnectionLimiter


@pytest.mark.asyncio
async def test_ws_connection_limiter_caps_per_token() -> None:
    """cap acquires succeed, the next one fails, a release re-opens a slot."""

    limiter = WsConnectionLimiter(max_per_token=2)

    assert await limiter.acquire("tok") is True
    assert await limiter.acquire("tok") is True
    # Third attempt at the same prefix must be denied (non-blocking False).
    assert await limiter.acquire("tok") is False

    await limiter.release("tok")
    # After one release a new slot is available.
    assert await limiter.acquire("tok") is True


@pytest.mark.asyncio
async def test_ws_connection_limiter_is_per_token() -> None:
    """Different token prefixes have independent counters."""

    limiter = WsConnectionLimiter(max_per_token=1)

    assert await limiter.acquire("alpha") is True
    assert await limiter.acquire("alpha") is False
    # A completely different token is unaffected.
    assert await limiter.acquire("bravo") is True


@pytest.mark.asyncio
async def test_ws_connection_limiter_release_clamped_at_zero() -> None:
    """A spurious release never pushes the count negative (silent overrun)."""

    limiter = WsConnectionLimiter(max_per_token=1)

    # Release with no prior acquire — must not crash and must not create slack.
    await limiter.release("tok")
    assert await limiter.acquire("tok") is True
    assert await limiter.acquire("tok") is False
