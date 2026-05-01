"""Async service client for the Sentinel Command Center HTTP + WS API.

This is the Track 3 TUI-side wrapper around the service contract shipped in
Track 2 (commit ``1a595ab``). It crosses a process boundary: the TUI runs in
its own Textual app process; the service runs separately (``sentinel serve``).
As a *client*, this module does not import internals from ``src.service.*``
beyond what the wire format requires — it owns its own dataclasses for the
execution shape and maps HTTP / WebSocket outcomes onto a small exception
hierarchy the TUI can dispatch on.

The TUI is the one that decides reconnection policy; this client never
auto-reconnects. On an unexpected drop mid-stream it raises
:class:`ServiceStreamDropped` with the last observed ``seq`` so the caller can
decide whether to resume with ``since_seq``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Optional
from urllib.parse import quote

import httpx

# websockets 10.1+ exposes the modern asyncio API under this submodule;
# ``websockets.connect`` at the top level is the legacy name and still works,
# but the explicit import is unambiguous with respect to the sync variant the
# CLI uses for ``--follow`` (``websockets.sync.client``).
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    InvalidStatus,
    WebSocketException,
)

__all__ = [
    "ExecutionOut",
    "StartResult",
    "ServiceClient",
    "ServiceClientError",
    "ServiceUnreachable",
    "ServiceUnauthorized",
    "ServiceRateLimited",
    "ServicePayloadTooLarge",
    "ServiceUnavailable",
    "ExecutionNotFound",
    "ExecutionAlreadyTerminal",
    "ServiceStreamDropped",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ServiceClientError(Exception):
    """Base class for service client failures.

    ``status`` is the HTTP status (or a synthesised equivalent for WS close
    codes). ``detail`` is the server-provided body / reason phrase when
    available, else ``None``.
    """

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail


class ServiceUnreachable(ServiceClientError):
    """Connect-time failure — service is not running / not reachable."""


class ServiceUnauthorized(ServiceClientError):
    """HTTP 401 — token has rotated; the TUI must reopen the dashboard."""


class ServiceRateLimited(ServiceClientError):
    """HTTP 429 (token bucket exhausted) or WS 1008 (per-token WS cap)."""

    def __init__(
        self,
        message: str = "rate limited",
        *,
        status: Optional[int] = 429,
        detail: Optional[str] = None,
        retry_after: Optional[int] = None,
    ) -> None:
        super().__init__(message, status=status, detail=detail)
        self.retry_after = retry_after


class ServicePayloadTooLarge(ServiceClientError):
    """HTTP 413 — request body exceeded the service limit."""


class ServiceUnavailable(ServiceClientError):
    """HTTP 503 — service signalled it cannot currently handle the request."""


class ExecutionNotFound(ServiceClientError):
    """HTTP 404 or WS close 4404 — execution id does not exist."""


class ExecutionAlreadyTerminal(ServiceClientError):
    """HTTP 409 — cancel/retry invoked on a non-active row.

    ``detail`` echoes the current status string ("succeeded" / "failed" / ...)
    when the server reports it, so the TUI can render a useful message.
    """


class ServiceStreamDropped(ServiceClientError):
    """WebSocket closed unexpectedly mid-stream.

    Carries ``last_seq`` (the highest ``seq`` yielded before the drop) so the
    TUI can reconnect with ``since_seq=last_seq`` if it chooses to retry.
    """

    def __init__(
        self,
        message: str,
        *,
        last_seq: int,
        status: Optional[int] = None,
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message, status=status, detail=detail)
        self.last_seq = last_seq


# ---------------------------------------------------------------------------
# Dataclasses (owned by this module — intentionally decoupled from the
# pydantic models in src.service.schemas)
# ---------------------------------------------------------------------------


def _parse_iso(value: Any) -> datetime:
    """Parse an ISO-8601 timestamp from the service.

    The service emits timezone-aware ISO strings (``datetime.isoformat()`` on
    UTC-aware datetimes). ``fromisoformat`` handles this since Python 3.11;
    we accept a trailing ``Z`` defensively in case a future version switches
    formats.
    """

    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    raise TypeError(f"expected ISO string or datetime, got {type(value).__name__}")


@dataclass
class ExecutionOut:
    """Wire shape of an execution row, as seen by the TUI."""

    id: str
    ticket_id: str
    project: str
    kind: str
    status: str
    phase: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime]
    cost_cents: int
    error: Optional[str]
    metadata: dict

    @classmethod
    def from_json(cls, data: dict) -> "ExecutionOut":
        return cls(
            id=data["id"],
            ticket_id=data["ticket_id"],
            project=data["project"],
            kind=data["kind"],
            status=data["status"],
            phase=data.get("phase"),
            started_at=_parse_iso(data["started_at"]),
            ended_at=(
                _parse_iso(data["ended_at"])
                if data.get("ended_at") is not None
                else None
            ),
            cost_cents=int(data.get("cost_cents", 0)),
            error=data.get("error"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class StartResult:
    """Return value of :meth:`ServiceClient.start`.

    ``attached=True`` means the service matched an already-running row on
    ``(project, ticket_id, kind)`` and is returning it rather than starting
    fresh. ``banner`` is a server-side human-readable string; render verbatim.
    """

    execution: ExecutionOut
    attached: bool
    banner: Optional[str] = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class ServiceClient:
    """Async HTTP + WS client for the Command Center service.

    One instance per TUI session. Not thread-safe; the TUI drives it from a
    single Textual worker.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
        ws_connect_factory: Any = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            # Short connect timeout (service is on loopback) but generous
            # read timeout — ``/executions`` can spend a moment queuing.
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=30.0),
            headers={"Authorization": f"Bearer {token}"},
        )
        # Test seam — callers can inject an alternate factory with the same
        # signature as ``websockets.asyncio.client.connect`` (returns an
        # async context manager yielding a connection object).
        self._ws_connect = ws_connect_factory or _default_ws_connect

    # ----- HTTP -----

    async def start(
        self,
        *,
        project: str,
        ticket_id: str,
        kind: str,
        options: Optional[dict] = None,
    ) -> StartResult:
        """POST /executions — attach-or-start.

        Returns a :class:`StartResult` on 200 (attached) and 201 (fresh).
        Raises a typed :class:`ServiceClientError` for any 4xx/5xx.
        """

        body = {
            "project": project,
            "ticket_id": ticket_id,
            "kind": kind,
            "options": options or {},
        }
        try:
            resp = await self._client.post(
                f"{self._base_url}/executions", json=body
            )
        except httpx.ConnectError as exc:
            raise ServiceUnreachable(
                f"service unreachable at {self._base_url}: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise ServiceUnreachable(
                f"transport error talking to {self._base_url}: {exc}"
            ) from exc

        if resp.status_code in (200, 201):
            data = resp.json()
            return StartResult(
                execution=ExecutionOut.from_json(data["execution"]),
                attached=bool(data.get("attached", False)),
                banner=data.get("banner"),
            )
        self._raise_for_status(resp)
        # Unreachable: _raise_for_status always raises for non-2xx, but mypy
        # doesn't know that.
        raise ServiceClientError(
            "unexpected response", status=resp.status_code, detail=resp.text
        )

    async def list_executions(
        self,
        *,
        project: Optional[str] = None,
        ticket_id: Optional[str] = None,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        limit: Optional[int] = None,
        before: Optional[str] = None,
    ) -> tuple[list["ExecutionOut"], Optional[str]]:
        """GET /executions — paged listing filtered by project/status/kind.

        Returns ``(items, next_cursor)``. ``items`` is a list of
        :class:`ExecutionOut`; ``next_cursor`` is the ISO ``started_at`` of
        the oldest row when the page was full, else ``None``.

        Same connect/transport handling as :meth:`start`. Non-2xx responses
        are classified through :meth:`_raise_for_status` — the read endpoint
        shares the same 401/429/503 semantics.
        """

        params: dict[str, str] = {}
        if project is not None:
            params["project"] = project
        if ticket_id is not None:
            params["ticket_id"] = ticket_id
        if status is not None:
            params["status"] = status
        if kind is not None:
            params["kind"] = kind
        if limit is not None:
            params["limit"] = str(int(limit))
        if before is not None:
            params["before"] = before

        try:
            resp = await self._client.get(
                f"{self._base_url}/executions", params=params
            )
        except httpx.ConnectError as exc:
            raise ServiceUnreachable(
                f"service unreachable at {self._base_url}: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise ServiceUnreachable(
                f"transport error talking to {self._base_url}: {exc}"
            ) from exc

        if resp.status_code == 200:
            data = resp.json()
            rows = [
                ExecutionOut.from_json(row) for row in data.get("items", [])
            ]
            cursor = data.get("next_cursor")
            return rows, (cursor if isinstance(cursor, str) else None)
        self._raise_for_status(resp)
        # Unreachable — _raise_for_status always raises for non-2xx.
        raise ServiceClientError(
            "unexpected response", status=resp.status_code, detail=resp.text
        )

    async def cancel(self, execution_id: str) -> None:
        """POST /executions/{id}/cancel — idempotent.

        Treats 200 *or* 202 as success (the service currently returns 200 with
        a body but may evolve to 202; both mean "signal accepted"). 404/409
        map to :class:`ExecutionNotFound` / :class:`ExecutionAlreadyTerminal`.
        """

        try:
            resp = await self._client.post(
                f"{self._base_url}/executions/{execution_id}/cancel"
            )
        except httpx.ConnectError as exc:
            raise ServiceUnreachable(
                f"service unreachable at {self._base_url}: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise ServiceUnreachable(
                f"transport error talking to {self._base_url}: {exc}"
            ) from exc

        if resp.status_code in (200, 202):
            return
        self._raise_for_status(resp, execution_id=execution_id)

    # ----- WebSocket -----

    async def tail(
        self, execution_id: str, *, since_seq: int = 0
    ) -> AsyncIterator[dict]:
        """Stream frames from /executions/{id}/stream.

        Yields raw frame dicts (``{"kind": "event"|"heartbeat"|"end", ...}``).
        On the ``end`` frame the generator yields it and then returns.

        Error mapping:

        * Server close code 4404 → :class:`ExecutionNotFound`
        * Server close code 1008 → :class:`ServiceRateLimited`
          (``ws_connections_per_token_exhausted``)
        * Any unexpected drop (non-clean close, reset, bad JSON) →
          :class:`ServiceStreamDropped` with ``last_seq`` set to the highest
          event seq yielded so far.
        """

        url = self._ws_url(execution_id, since_seq=since_seq)
        last_seq = since_seq
        # ``ws_connect`` is a class whose instances are async context
        # managers; ``open_timeout`` keeps handshake failures from hanging
        # the TUI.
        cm = self._ws_connect(url, open_timeout=10)
        try:
            async with cm as ws:
                try:
                    async for message in ws:
                        try:
                            frame = (
                                json.loads(message)
                                if isinstance(message, (str, bytes))
                                else message
                            )
                        except json.JSONDecodeError as exc:
                            raise ServiceStreamDropped(
                                f"bad frame JSON: {exc}",
                                last_seq=last_seq,
                            ) from exc
                        if not isinstance(frame, dict):
                            raise ServiceStreamDropped(
                                f"unexpected frame type: {type(frame).__name__}",
                                last_seq=last_seq,
                            )
                        if frame.get("kind") == "event":
                            seq = frame.get("seq")
                            if isinstance(seq, int):
                                last_seq = seq
                        yield frame
                        if frame.get("kind") == "end":
                            return
                except ConnectionClosed as exc:
                    # Inspect the close frame to distinguish handshake-time
                    # rejections (4404, 1008) and clean vs unexpected closes.
                    self._raise_for_close(exc, last_seq=last_seq)
                    return
        except ConnectionClosed as exc:
            # Raised from ``async with ws_connect(...)`` when the server
            # refuses the handshake (e.g. 4404) before any frame flows, or
            # when the event loop sees the close before the ``async for``.
            self._raise_for_close(exc, last_seq=last_seq)
            return
        except InvalidStatus as exc:
            # Handshake rejected at the HTTP layer (e.g. 401 when the token
            # query param is missing/invalid, or 403 when the server closes
            # before ``accept()`` as Starlette does for the pre-accept
            # ``ws.close(code=1008, ...)`` branch).
            status = getattr(exc.response, "status_code", None)
            if status == 401:
                raise ServiceUnauthorized(
                    "service rejected token on stream handshake",
                    status=401,
                    detail=None,
                ) from exc
            if status == 403:
                # Starlette translates pre-accept ``close(1008)`` into a 403
                # HTTP reject; we know the only 1008 reason on this endpoint
                # is the per-token WS connection cap.
                raise ServiceRateLimited(
                    "websocket connection limit reached",
                    status=1008,
                    detail="ws_connections_per_token_exhausted",
                    retry_after=None,
                ) from exc
            raise ServiceStreamDropped(
                f"stream handshake rejected (status={status})",
                last_seq=last_seq,
                status=status,
                detail=None,
            ) from exc
        except WebSocketException as exc:
            raise ServiceStreamDropped(
                f"stream error: {exc}",
                last_seq=last_seq,
            ) from exc
        except (OSError, httpx.TransportError) as exc:
            raise ServiceUnreachable(
                f"cannot open stream at {url}: {exc}"
            ) from exc

    # ----- lifecycle -----

    async def aclose(self) -> None:
        """Close the owned HTTP client, if any."""

        if self._owns_client:
            await self._client.aclose()

    # ----- internal helpers -----

    def _ws_url(self, execution_id: str, *, since_seq: int) -> str:
        base = self._base_url
        if base.startswith("http://"):
            ws_base = "ws://" + base[len("http://") :]
        elif base.startswith("https://"):
            ws_base = "wss://" + base[len("https://") :]
        else:
            # No scheme or unknown scheme — assume ws:// (loopback default).
            ws_base = base
        token_q = quote(self._token, safe="")
        return (
            f"{ws_base}/executions/{execution_id}/stream"
            f"?token={token_q}&since_seq={int(since_seq)}"
        )

    def _raise_for_status(
        self,
        resp: httpx.Response,
        *,
        execution_id: Optional[str] = None,
    ) -> None:
        """Map a non-2xx HTTP response onto a typed exception.

        Always raises — return type is ``NoReturn`` in spirit (kept untyped
        to avoid pulling in ``typing.NoReturn`` for a single call site).
        """

        status = resp.status_code
        detail = _extract_detail(resp)
        ctx = f" (execution {execution_id})" if execution_id else ""
        if status == 401:
            raise ServiceUnauthorized(
                f"service rejected token{ctx}", status=status, detail=detail
            )
        if status == 404:
            raise ExecutionNotFound(
                f"execution not found{ctx}", status=status, detail=detail
            )
        if status == 409:
            raise ExecutionAlreadyTerminal(
                f"execution not in a cancellable state{ctx}",
                status=status,
                detail=detail,
            )
        if status == 413:
            raise ServicePayloadTooLarge(
                f"request body too large{ctx}", status=status, detail=detail
            )
        if status == 429:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            raise ServiceRateLimited(
                f"rate limited{ctx}",
                status=status,
                detail=detail,
                retry_after=retry_after,
            )
        if status == 503:
            raise ServiceUnavailable(
                f"service unavailable{ctx}", status=status, detail=detail
            )
        raise ServiceClientError(
            f"service error {status}{ctx}", status=status, detail=detail
        )

    @staticmethod
    def _raise_for_close(
        exc: ConnectionClosed, *, last_seq: int
    ) -> None:
        """Classify a WS ConnectionClosed into the right typed error."""

        code = getattr(exc.rcvd, "code", None) if exc.rcvd else None
        reason = (
            getattr(exc.rcvd, "reason", None) if exc.rcvd else None
        ) or ""
        if code == 4404:
            raise ExecutionNotFound(
                "execution not found", status=4404, detail=reason
            ) from exc
        if code == 1008:
            # The only documented 1008 close reason on this endpoint is the
            # per-token WS cap; preserve the server's phrase in detail.
            raise ServiceRateLimited(
                "websocket connection limit reached",
                status=1008,
                detail=reason or "ws_connections_per_token_exhausted",
                retry_after=None,
            ) from exc
        if code == 1000:
            # Clean close without an ``end`` frame — treat as unexpected drop
            # (the service sends ``end`` before closing 1000 on terminal
            # events).  The caller may still want to reconnect with
            # ``since_seq=last_seq`` to catch up.
            if isinstance(exc, ConnectionClosedError):
                raise ServiceStreamDropped(
                    f"stream closed unexpectedly: {reason}",
                    last_seq=last_seq,
                    status=code,
                    detail=reason,
                ) from exc
            return
        raise ServiceStreamDropped(
            f"stream dropped (code={code}): {reason}",
            last_seq=last_seq,
            status=code,
            detail=reason,
        ) from exc


# ---------------------------------------------------------------------------
# Module helpers
# ---------------------------------------------------------------------------


def _extract_detail(resp: httpx.Response) -> Optional[str]:
    """Best-effort pull of a human-readable detail from an error response.

    FastAPI puts validation errors under ``{"detail": ...}``; we surface that
    verbatim when present, else fall back to the raw text.
    """

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        text = resp.text
        return text or None
    if isinstance(data, dict):
        detail = data.get("detail")
        if detail is None:
            return None
        if isinstance(detail, str):
            return detail
        return json.dumps(detail)
    return str(data)


def _parse_retry_after(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _default_ws_connect(url: str, *, open_timeout: float):
    """Factory returning an async context manager for a WS connection.

    ``websockets.asyncio.client.connect`` is itself a class whose instances
    are async context managers, so we pass the kwargs straight through. Kept
    as a module-level indirection so tests can inject a mock without
    monkeypatching the ``websockets`` package.
    """

    return ws_connect(url, open_timeout=open_timeout)
