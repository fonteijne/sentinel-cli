"""Bearer-token authentication + WS handshake auth for the Command Center.

Plan 05 owns the final security seal:

* ``load_or_create_token`` resolves the service token with an env-var → file →
  atomic-create fallback. The token file lives at ``~/.sentinel/service_token``
  with mode ``0o600``. Writers use a PID-unique tmp file + ``os.link`` for
  atomic create-if-not-exists — ``os.link`` fails with ``FileExistsError`` if
  the target exists, so a slow writer CANNOT overwrite an earlier winner's
  token. Losers read via ``_read_token_file`` which retries on transient
  ``FileNotFoundError`` between the loser's check and the winner's link.

* ``require_token`` is the HTTP dep. It pulls the expected token from
  ``app.state.service_token`` and compares with ``secrets.compare_digest`` —
  ``==`` short-circuits and would leak timing info on a remote attacker.

* ``require_token_ws`` is the WebSocket variant. ``HTTPException`` does nothing
  after the handshake has begun, so we raise ``WebSocketException(code=1008)``.
  Starlette translates that to a 403 during the handshake (before upgrade).

* ``require_token_and_write_slot`` combines auth + per-token rate limit in a
  single generator dep. FastAPI runs the code after ``yield`` on both success
  and exception paths, so every successful ``check_and_reserve`` is paired with
  a ``release`` regardless of whether the handler returned or raised.

Query-string ``?token=`` is a WebSocket-only fallback and *only* accepted from
loopback clients — query strings land in reverse-proxy and access logs.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException, Request, WebSocketException
from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)

TOKEN_FILE = Path.home() / ".sentinel" / "service_token"

# secrets.token_urlsafe(32) produces ~43 chars; anything below 32 is either a
# truncated write or a user-injected typo — refuse and let the caller retry.
_MIN_TOKEN_LEN = 32

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _read_token_file(
    path: Path, *, attempts: int = 10, delay_s: float = 0.02
) -> str:
    """Read + validate a token file with a brief retry loop.

    Two transient conditions both deserve the same retry treatment:

    * ``FileNotFoundError`` — the loser of the link race peeked between its
      own ``FileExistsError`` (somewhere in the timeline) and the winner's
      ``os.link``. The target appears momentarily. Sleep and recheck.
    * Short read — the winner wrote but hasn't yet synced to the dir entry
      visible to readers. Sleep and recheck.

    Exhausting all attempts with the file still absent raises, so callers
    always get a token string or an exception — never a silent empty value.
    """

    for _ in range(attempts):
        try:
            value = path.read_text().strip()
        except FileNotFoundError:
            time.sleep(delay_s)
            continue
        if len(value) >= _MIN_TOKEN_LEN:
            return value
        time.sleep(delay_s)
    raise RuntimeError(
        f"{path} missing or truncated after {attempts} read attempts"
    )


def load_or_create_token() -> str:
    """Resolve the service token: env var → existing file → atomic create.

    Priority ordering is load-bearing: CI pipelines and docker-compose pass the
    token via ``SENTINEL_SERVICE_TOKEN``; the file path is for interactive
    laptops where the token should survive restarts without being typed.

    The create path uses ``os.link`` for atomic "create if not exists". A
    plain ``os.rename`` would silently OVERWRITE an earlier winner's file if
    a slow second writer's rename arrived after the winner's rename had
    already consumed the shared tmp path — causing two processes to return
    different tokens. ``os.link`` raises ``FileExistsError`` if the target
    exists, which is exactly the semantics we want.

    Each caller uses a unique PID-derived tmp path so a stale tmp left by a
    crashed prior process never blocks fresh callers.
    """

    env = os.environ.get("SENTINEL_SERVICE_TOKEN")
    if env:
        return env
    if TOKEN_FILE.exists():
        return _read_token_file(TOKEN_FILE)

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)

    # Unique tmp per caller: PID + 4 bytes of entropy. Collision-free across
    # concurrent processes and immune to stale tmp blocking from prior crashes.
    tmp = TOKEN_FILE.with_suffix(
        f".tmp.{os.getpid()}.{secrets.token_hex(4)}"
    )
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, token.encode("ascii"))
    finally:
        os.close(fd)

    try:
        # Atomic create-if-not-exists. We always drop our tmp afterwards:
        # on success the hardlink means our data lives at TOKEN_FILE; on
        # FileExistsError someone beat us and our candidate is discarded.
        os.link(tmp, TOKEN_FILE)
    except FileExistsError:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        return _read_token_file(TOKEN_FILE)

    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    logger.warning("generated new service token at %s", TOKEN_FILE)
    return token


def token_prefix(token: str) -> str:
    """Stable short identifier for audit logs + rate-limit keying.

    SHA-256 not because we're storing it, but because a raw prefix of the
    token itself would leak the first eight characters. Collision probability
    at 2^32 is negligible for our scale.
    """

    return hashlib.sha256(token.encode("ascii")).hexdigest()[:8]


def _extract_bearer(request: Request | WebSocket) -> str:
    """Pull the presented credential from a request or WebSocket handshake.

    HTTP always requires an ``Authorization: Bearer …`` header — query-string
    tokens would land in access logs. WebSocket prefers the header too but
    accepts ``?token=…`` as a loopback-only fallback (browsers can't set
    ``Authorization`` on a WebSocket upgrade).
    """

    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() == "bearer" and presented:
        return presented
    if isinstance(request, WebSocket):
        client_host = request.client.host if request.client else ""
        if client_host in _LOOPBACK_HOSTS:
            return request.query_params.get("token", "") or ""
    return ""


def require_token(request: Request) -> str:
    """HTTP bearer-auth dep — attach via ``Depends`` on a protected router."""

    expected = request.app.state.service_token
    presented = _extract_bearer(request)
    if not presented or not secrets.compare_digest(presented, expected):
        logger.warning(
            "auth failure from %s %s %s",
            request.client.host if request.client else "?",
            request.method,
            request.url.path,
        )
        raise HTTPException(status_code=401, detail="unauthorized")
    return presented


async def require_token_ws(ws: WebSocket) -> str:
    """WebSocket bearer-auth dep — raise close-code 1008 on failure.

    ``HTTPException`` doesn't apply cleanly to a WebSocket: by the time the
    exception bubbles, Starlette has begun the upgrade. ``WebSocketException``
    surfaces as a handshake-level 403 on the client side.
    """

    expected = ws.app.state.service_token
    presented = _extract_bearer(ws)
    if not presented or not secrets.compare_digest(presented, expected):
        logger.warning(
            "ws auth failure from %s path=%s",
            ws.client.host if ws.client else "?",
            ws.url.path,
        )
        raise WebSocketException(code=1008)
    return presented


def require_token_and_write_slot(request: Request) -> Iterator[str]:
    """Auth + per-token rate limit for write endpoints.

    Generator dep: FastAPI calls ``generator.close()`` after the response is
    produced (success path) or the exception has been converted (error path).
    The single ``try/finally`` around ``yield`` therefore covers every
    post-reservation outcome, including 4xx/5xx raised by the handler.

    If ``require_token`` raises 401, we never reach ``check_and_reserve`` and
    there is nothing to release. If ``check_and_reserve`` returns
    ``(False, ...)`` we raise 429 *before* ``yield`` — still nothing to
    release. The invariant "every reserve is paired with a release" holds.
    """

    token = require_token(request)
    key = token_prefix(token)
    # Stash the prefix so plan 04's start() can scope the idempotency lookup
    # per token.
    request.state.token_prefix = key

    allowed, retry_after = request.app.state.rate_limiter.check_and_reserve(key)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="rate limit",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        yield token
    finally:
        request.app.state.rate_limiter.release(key)


def audit_write(request: Request) -> None:
    """Structured audit line for every authenticated write request.

    Applied as an additional dep on the write router. It fires AFTER
    ``require_token_and_write_slot`` (auth + rate limit), so the fact that
    the line is emitted means auth succeeded and a rate-limit slot was
    reserved. It does NOT guarantee the handler returned 2xx — a
    pydantic-validation 4xx or a handler 5xx still produces an audit line
    because the attempt itself is the audit-relevant event.

    Operators reading the log: every audit line represents an AUTHORISED
    ATTEMPT, not a confirmed state change. Correlate with the response
    status (FastAPI access logs) or the `execution.started` event to
    distinguish successful writes from rejected attempts.
    """

    key = getattr(request.state, "token_prefix", "?")
    client_host = request.client.host if request.client else "?"
    logger.info(
        "audit write user=%s ip=%s method=%s path=%s",
        key,
        client_host,
        request.method,
        request.url.path,
    )
