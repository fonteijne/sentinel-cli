"""Tests for :mod:`src.tui.service_client`.

All I/O is mocked:

* HTTP: we inject an :class:`httpx.AsyncClient` built on
  :class:`httpx.MockTransport`. No real sockets, no real server.
* WebSocket: we inject a ``ws_connect_factory`` that mirrors the real
  factory's contract — it is *called* with ``(url, open_timeout=float)``
  and returns an async context manager whose ``__aenter__`` yields an
  async iterator over frames.

This file is the Track 3 contract test for the service client; it does
not import ``websockets`` internals beyond :class:`ConnectionClosed` /
:class:`Close`, which the production client catches and classifies.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from src.tui.service_client import (
    ExecutionAlreadyTerminal,
    ExecutionNotFound,
    ExecutionOut,
    ServiceClient,
    ServicePayloadTooLarge,
    ServiceRateLimited,
    ServiceStreamDropped,
    ServiceUnauthorized,
    ServiceUnavailable,
    ServiceUnreachable,
    StartResult,
)


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


def _exec_row(**overrides: Any) -> dict:
    """Minimal ExecutionOut wire shape; callers override to taste."""
    row = {
        "id": "abc",
        "ticket_id": "T-1",
        "project": "PRJ",
        "kind": "plan",
        "status": "running",
        "phase": None,
        "started_at": datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc).isoformat(),
        "ended_at": None,
        "cost_cents": 0,
        "error": None,
        "metadata": {},
    }
    row.update(overrides)
    return row


def _make_http_client(handler) -> httpx.AsyncClient:
    """Build an ``AsyncClient`` backed by a MockTransport handler."""
    transport = httpx.MockTransport(handler)
    # Note: the real client wires Authorization into default headers, but
    # injected clients are used as-is; the production code never sets
    # headers on the injected handle. For these tests the transport handler
    # does the assertions it cares about.
    return httpx.AsyncClient(transport=transport)


class _FakeWS:
    """Async iterator over a list of frames, optionally raising at end.

    Mirrors what ``websockets.asyncio.client.connect`` returns under
    ``async with``. Frames are iterated via ``async for message in ws``.
    """

    def __init__(
        self,
        frames: Iterable[Any],
        *,
        raise_on_close: Optional[BaseException] = None,
    ) -> None:
        self._frames = list(frames)
        self._raise = raise_on_close

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for f in self._frames:
            yield f
        if self._raise is not None:
            raise self._raise


class _FakeWSConnectCM:
    """Async context manager returned by the fake factory."""

    def __init__(
        self,
        url: str,
        *,
        frames: Iterable[Any] = (),
        raise_on_close: Optional[BaseException] = None,
        raise_on_enter: Optional[BaseException] = None,
        capture: Optional[dict] = None,
    ) -> None:
        self.url = url
        self._frames = frames
        self._raise_on_close = raise_on_close
        self._raise_on_enter = raise_on_enter
        self._capture = capture

    async def __aenter__(self) -> _FakeWS:
        if self._capture is not None:
            self._capture["url"] = self.url
        if self._raise_on_enter is not None:
            raise self._raise_on_enter
        return _FakeWS(self._frames, raise_on_close=self._raise_on_close)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _ws_factory(
    *,
    frames: Iterable[Any] = (),
    raise_on_close: Optional[BaseException] = None,
    raise_on_enter: Optional[BaseException] = None,
    capture: Optional[dict] = None,
):
    """Return a sync callable matching the ``ws_connect_factory`` contract."""

    def _factory(url: str, *, open_timeout: float):
        return _FakeWSConnectCM(
            url,
            frames=frames,
            raise_on_close=raise_on_close,
            raise_on_enter=raise_on_enter,
            capture=capture,
        )

    return _factory


# --------------------------------------------------------------------------- #
# start(...)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_start_fresh_returns_start_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/executions"
        body = json.loads(request.content)
        assert body["project"] == "PRJ"
        assert body["ticket_id"] == "T-1"
        assert body["kind"] == "plan"
        return httpx.Response(
            201,
            json={
                "execution": _exec_row(id="abc", kind="plan"),
                "attached": False,
                "banner": None,
            },
        )

    http = _make_http_client(handler)
    client = ServiceClient("http://localhost:8787", "tok", http_client=http)
    try:
        result = await client.start(project="PRJ", ticket_id="T-1", kind="plan")
    finally:
        await client.aclose()

    assert isinstance(result, StartResult)
    assert result.attached is False
    assert result.banner is None
    assert isinstance(result.execution, ExecutionOut)
    assert result.execution.id == "abc"
    assert result.execution.kind == "plan"


@pytest.mark.asyncio
async def test_start_attached_returns_banner_verbatim() -> None:
    banner = "Attached to run abc12345 started 14s ago"

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "execution": _exec_row(id="abc12345"),
                "attached": True,
                "banner": banner,
            },
        )

    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(handler),
    )
    try:
        result = await client.start(project="PRJ", ticket_id="T-1", kind="plan")
    finally:
        await client.aclose()

    assert result.attached is True
    assert result.banner == banner


@pytest.mark.asyncio
async def test_start_401_raises_unauthorized() -> None:
    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(
            lambda _r: httpx.Response(401, json={"detail": "bad token"})
        ),
    )
    with pytest.raises(ServiceUnauthorized):
        await client.start(project="PRJ", ticket_id="T-1", kind="plan")
    await client.aclose()


@pytest.mark.asyncio
async def test_start_413_raises_payload_too_large() -> None:
    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(
            lambda _r: httpx.Response(413, json={"detail": "too big"})
        ),
    )
    with pytest.raises(ServicePayloadTooLarge):
        await client.start(project="PRJ", ticket_id="T-1", kind="plan")
    await client.aclose()


@pytest.mark.asyncio
async def test_start_429_sets_retry_after() -> None:
    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(
            lambda _r: httpx.Response(
                429, json={"detail": "slow down"}, headers={"Retry-After": "7"}
            )
        ),
    )
    with pytest.raises(ServiceRateLimited) as ei:
        await client.start(project="PRJ", ticket_id="T-1", kind="plan")
    assert ei.value.retry_after == 7
    await client.aclose()


@pytest.mark.asyncio
async def test_start_503_raises_unavailable() -> None:
    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(
            lambda _r: httpx.Response(503, json={"detail": "down"})
        ),
    )
    with pytest.raises(ServiceUnavailable):
        await client.start(project="PRJ", ticket_id="T-1", kind="plan")
    await client.aclose()


@pytest.mark.asyncio
async def test_start_connect_error_raises_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(handler),
    )
    with pytest.raises(ServiceUnreachable):
        await client.start(project="PRJ", ticket_id="T-1", kind="plan")
    await client.aclose()


# --------------------------------------------------------------------------- #
# cancel(...)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cancel_success_200() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"ok": True})

    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(handler),
    )
    assert await client.cancel("abc") is None
    assert seen["path"] == "/executions/abc/cancel"
    await client.aclose()


@pytest.mark.asyncio
async def test_cancel_success_202() -> None:
    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(lambda _r: httpx.Response(202)),
    )
    assert await client.cancel("abc") is None
    await client.aclose()


@pytest.mark.asyncio
async def test_cancel_404_raises_execution_not_found() -> None:
    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(
            lambda _r: httpx.Response(404, json={"detail": "no such"})
        ),
    )
    with pytest.raises(ExecutionNotFound):
        await client.cancel("abc")
    await client.aclose()


@pytest.mark.asyncio
async def test_cancel_409_raises_already_terminal_with_detail() -> None:
    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(
            lambda _r: httpx.Response(409, json={"detail": "succeeded"})
        ),
    )
    with pytest.raises(ExecutionAlreadyTerminal) as ei:
        await client.cancel("abc")
    assert ei.value.detail == "succeeded"
    await client.aclose()


# --------------------------------------------------------------------------- #
# tail(...)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tail_happy_path_yields_frames_then_closes() -> None:
    frames = [
        json.dumps({"kind": "event", "seq": 1, "type": "started", "payload": {}}),
        json.dumps({"kind": "event", "seq": 2, "type": "progress", "payload": {}}),
        json.dumps({"kind": "end", "execution_status": "succeeded"}),
    ]
    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(lambda _r: httpx.Response(500)),
        ws_connect_factory=_ws_factory(frames=frames),
    )

    out: list[dict] = []
    async for frame in client.tail("abc"):
        out.append(frame)

    assert len(out) == 3
    assert out[0]["kind"] == "event" and out[0]["seq"] == 1
    assert out[1]["kind"] == "event" and out[1]["seq"] == 2
    assert out[2]["kind"] == "end"
    await client.aclose()


@pytest.mark.asyncio
async def test_tail_factory_receives_url_with_since_seq_and_encoded_token() -> None:
    capture: dict = {}
    frames = [json.dumps({"kind": "end", "execution_status": "succeeded"})]
    # Use a token with a character that requires URL encoding so we can
    # distinguish encoded vs raw.
    client = ServiceClient(
        "http://localhost:8787",
        "tok/with+special",
        http_client=_make_http_client(lambda _r: httpx.Response(500)),
        ws_connect_factory=_ws_factory(frames=frames, capture=capture),
    )

    async for _f in client.tail("abc", since_seq=7):
        pass

    url = capture["url"]
    parsed = urlparse(url)
    assert parsed.scheme == "ws"
    assert parsed.path == "/executions/abc/stream"
    qs = parse_qs(parsed.query)
    assert qs["since_seq"] == ["7"]
    # The raw token must NOT appear verbatim — it should be percent-encoded.
    assert qs["token"] == ["tok/with+special"]  # parse_qs decodes for us
    assert "tok/with+special" not in parsed.query  # but raw query is encoded
    assert "tok%2Fwith%2Bspecial" in parsed.query
    await client.aclose()


@pytest.mark.asyncio
async def test_tail_close_4404_raises_execution_not_found() -> None:
    close = Close(code=4404, reason="no such execution")
    exc = ConnectionClosedError(rcvd=close, sent=None, rcvd_then_sent=None)
    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(lambda _r: httpx.Response(500)),
        ws_connect_factory=_ws_factory(frames=[], raise_on_close=exc),
    )
    with pytest.raises(ExecutionNotFound):
        async for _f in client.tail("abc"):
            pass
    await client.aclose()


@pytest.mark.asyncio
async def test_tail_close_1008_raises_rate_limited() -> None:
    close = Close(code=1008, reason="ws_connections_per_token_exhausted")
    exc = ConnectionClosedError(rcvd=close, sent=None, rcvd_then_sent=None)
    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(lambda _r: httpx.Response(500)),
        ws_connect_factory=_ws_factory(frames=[], raise_on_close=exc),
    )
    with pytest.raises(ServiceRateLimited) as ei:
        async for _f in client.tail("abc"):
            pass
    assert ei.value.status == 1008
    await client.aclose()


@pytest.mark.asyncio
async def test_tail_close_1000_without_end_raises_stream_dropped() -> None:
    # One event frame (seq=5), then a 1000 close *without* an end frame.
    close = Close(code=1000, reason="")
    exc = ConnectionClosedError(rcvd=close, sent=None, rcvd_then_sent=None)
    frames = [json.dumps({"kind": "event", "seq": 5, "type": "x", "payload": {}})]
    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(lambda _r: httpx.Response(500)),
        ws_connect_factory=_ws_factory(frames=frames, raise_on_close=exc),
    )

    seen: list[dict] = []
    with pytest.raises(ServiceStreamDropped) as ei:
        async for f in client.tail("abc"):
            seen.append(f)
    assert ei.value.last_seq == 5
    assert len(seen) == 1
    await client.aclose()


@pytest.mark.asyncio
async def test_tail_bad_json_raises_stream_dropped_with_last_seq() -> None:
    frames = [
        json.dumps({"kind": "event", "seq": 3, "type": "x", "payload": {}}),
        "{not valid json",
    ]
    client = ServiceClient(
        "http://localhost:8787",
        "tok",
        http_client=_make_http_client(lambda _r: httpx.Response(500)),
        ws_connect_factory=_ws_factory(frames=frames),
    )
    seen: list[dict] = []
    with pytest.raises(ServiceStreamDropped) as ei:
        async for f in client.tail("abc"):
            seen.append(f)
    assert ei.value.last_seq == 3
    assert len(seen) == 1
    await client.aclose()
