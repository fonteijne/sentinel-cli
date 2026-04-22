# Feature: Command Center — Auth & Network Binding

## Summary

Add bearer-token authentication to every service route, lock down network binding to the Docker Compose network (or an explicit bind address), and restrict CORS to an allowlist. This is the finishing seal before the service can be exposed to anything but a trusted dev machine.

## User Story

As a Sentinel operator
I want the Command Center API to reject unauthenticated requests and refuse to listen on 0.0.0.0 by default
So that running `sentinel serve` in a shared environment doesn't immediately expose execution control to the whole network.

## Problem Statement

Plans 02–04 produce a fully functional API that is:
- Unauthenticated — anyone who can reach the port can start/cancel/retry runs
- CORS-wide-open from a browser
- Bound to `127.0.0.1` by default but easily overridden to `0.0.0.0` without any auth gate

## Solution Statement

1. Bearer-token FastAPI dependency applied via a protected-router wrapper (this plan owns the final `create_app()` composition).
2. Token resolved at startup from (in order): `SENTINEL_SERVICE_TOKEN` env var → `~/.sentinel/service_token` file → **atomic** create-if-missing (`O_CREAT|O_EXCL`, mode 0o600).
3. CORS middleware with explicit allowlist from config; default empty (same-origin only). **Validated at startup**: reject configurations that combine `allow_credentials=True` with `"*"` (browsers silently fail).
4. Default bind `127.0.0.1`; `--host 0.0.0.0` guarded by `--i-know-what-im-doing` flag.
5. **Audit log line on every authenticated write** (POST to /executions*), plus on auth failures — for a service that touches git/GitLab/Jira, "who triggered run X" matters.
6. WebSocket fallback: accept bearer token via `Authorization` header OR `?token=` query param (WS only). Document the log-leakage trade-off.
7. Per-token rate limits enforced at the auth dependency layer — limits are part of auth because the subject is the token.

## Metadata

| Field | Value |
|---|---|
| Type | ENHANCEMENT |
| Complexity | LOW |
| Systems Affected | `src/service/auth.py` (new), `src/service/rate_limit.py` (new), `src/service/app.py` (rewrites 02's factory), `src/cli.py`, `config/config.yaml` |
| Dependencies | None new |
| Estimated Tasks | 6 |
| Prerequisite | Plans 02, 03, 04 (05 owns the final `create_app()` composition) |

---

## Mandatory Reading

| Priority | File | Why |
|---|---|---|
| P0 | `src/service/app.py` (from plan 02+) | Where middleware + router deps attach |
| P0 | `src/config_loader.py:31-150` | How to surface new config keys (`service.cors_origins`, `service.bind_address`) |
| P1 | [FastAPI security — Bearer](https://fastapi.tiangolo.com/tutorial/security/http-basic-auth/) | Idiomatic dependency style |
| P1 | [Starlette CORSMiddleware](https://www.starlette.io/middleware/#corsmiddleware) | Allowlist semantics |

---

## Files to Change

| File | Action |
|---|---|
| `src/service/auth.py` | CREATE — `require_token` dependency, `load_or_create_token()` |
| `src/service/app.py` | UPDATE — apply dep to router, add CORS, bind warning |
| `src/cli.py` | UPDATE — `sentinel serve` reads/creates token, enforces `--host 0.0.0.0` guard |
| `tests/service/test_auth.py` | CREATE |
| `config/config.yaml` | UPDATE — document `service.cors_origins` (default `[]`), `service.bind_address` (default `127.0.0.1`) |

---

## Tasks

### Task 1 — CREATE `src/service/auth.py`

```python
import logging, os, secrets
from pathlib import Path
from fastapi import HTTPException, Request
from starlette.websockets import WebSocket

from fastapi import WebSocketException                   # re-export of starlette.exceptions

logger = logging.getLogger(__name__)

TOKEN_FILE = Path.home() / ".sentinel" / "service_token"

def load_or_create_token() -> str:
    env = os.environ.get("SENTINEL_SERVICE_TOKEN")
    if env:
        return env
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    try:
        # Atomic create: O_EXCL fails if another process raced us; mode bits applied before write.
        fd = os.open(TOKEN_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, token.encode("ascii"))
        finally:
            os.close(fd)
        logger.warning("generated new service token at %s", TOKEN_FILE)
        return token
    except FileExistsError:
        # Another process won the race and wrote the file — read theirs.
        return TOKEN_FILE.read_text().strip()

def _extract_bearer(request: Request | WebSocket) -> str:
    # HTTP: require Authorization header.
    # WebSocket: accept header OR ?token= query param (browsers can't set headers on WS).
    header = request.headers.get("authorization", "") if hasattr(request, "headers") else ""
    scheme, _, presented = header.partition(" ")
    if scheme.lower() == "bearer" and presented:
        return presented
    if isinstance(request, WebSocket):
        return request.query_params.get("token", "") or ""
    return ""

def require_token(request: Request) -> str:
    expected = request.app.state.service_token
    presented = _extract_bearer(request)
    if not presented or not secrets.compare_digest(presented, expected):
        logger.warning("auth failure from %s %s %s", request.client.host if request.client else "?",
                       request.method, request.url.path)
        raise HTTPException(status_code=401, detail="unauthorized")
    return presented                                   # callers may log prefix for audit

async def require_token_ws(ws: WebSocket) -> str:
    expected = ws.app.state.service_token
    presented = _extract_bearer(ws)
    if not presented or not secrets.compare_digest(presented, expected):
        logger.warning("ws auth failure from %s path=%s",
                       ws.client.host if ws.client else "?", ws.url.path)
        # WebSocketException is FastAPI's re-export of starlette's; it surfaces as a clean
        # close with the given code instead of a stack trace (HTTPException doesn't work on
        # WS — the socket is already closed by the time the exception bubbles).
        raise WebSocketException(code=1008)             # 1008 = Policy Violation
    return presented
```

**GOTCHA — timing-safe comparison**. `secrets.compare_digest` is required; `==` short-circuits on first differing byte.

**GOTCHA — atomic token creation**. `O_CREAT | O_EXCL` with `mode=0o600` at creation avoids the write→chmod window where the file is briefly world-readable. If another process got here first, we raise — caller (the CLI) should retry via the existing-file branch.

**GOTCHA — WebSocket auth close codes**. The WS dep raises `WebSocketException(code=1008)`, not `HTTPException`. Starlette translates the close code to an HTTP 403 *during the handshake* (before the upgrade completes), which is what the client sees. Tests assert 403 handshake-level for WS + 401 for HTTP.

**GOTCHA — token in query string**. `?token=` lands in access logs / reverse-proxy logs / browser history. Document and gate behind WS-only; HTTP never accepts query-string token.

**VALIDATE**: Unit tests in Task 6.

### Task 1b — CREATE `src/service/rate_limit.py` + wire into write endpoints

Sliding-window per-token counters held in memory (single-process, non-persistent — restart resets).

```python
class TokenRateLimiter:
    def __init__(self, max_concurrent: int, max_per_minute: int):
        self._max_concurrent = max_concurrent
        self._max_per_min = max_per_minute
        self._in_flight: dict[str, int] = defaultdict(int)
        self._window:    dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check_and_reserve(self, token_key: str) -> tuple[bool, int]:
        """Returns (allowed, retry_after_seconds_if_denied)."""
        with self._lock:
            now = time.monotonic()
            # prune window
            w = self._window[token_key]
            while w and now - w[0] > 60.0:
                w.popleft()
            # check windowed
            if len(w) >= self._max_per_min:
                retry = int(60.0 - (now - w[0])) + 1
                return (False, retry)
            # check concurrent
            if self._in_flight[token_key] >= self._max_concurrent:
                return (False, 1)                      # retry in ~1s; concurrent slot frees on release
            # reserve
            w.append(now)
            self._in_flight[token_key] += 1
            return (True, 0)

    def release(self, token_key: str) -> None:
        with self._lock:
            self._in_flight[token_key] = max(0, self._in_flight[token_key] - 1)
```

Keyed by **sha256(token)[:8]** — same helper used for audit logs; never the raw token.

### Wiring the limiter

The limiter is applied **only to the write router** (`commands.router` from plan 04). Read and stream routes are exempt by design — polling a read endpoint from a dashboard is expected to exceed 30/minute.

The wiring lives in plan 05's `auth.py`:

```python
# auth.py
def require_token_and_write_slot(request: Request) -> str:
    token = require_token(request)                      # existing dep; raises 401
    key = _sha256_prefix(token)
    allowed, retry_after = request.app.state.rate_limiter.check_and_reserve(key)
    if not allowed:
        raise HTTPException(status_code=429, detail="rate limit", headers={"Retry-After": str(retry_after)})
    # Release on response finish via BackgroundTask
    request.state.release_rl = lambda: request.app.state.rate_limiter.release(key)
    return token

# Middleware that runs `release_rl` after the response
class ReleaseRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        release = getattr(request.state, "release_rl", None)
        if release: release()
        return response
```

In `create_app()` (this plan's Task 2):
```python
# Apply limiter to write router (plan 04) only
http_write_protected = APIRouter(dependencies=[Depends(require_token_and_write_slot)])
http_write_protected.include_router(commands.router)
app.include_router(http_write_protected)
app.add_middleware(ReleaseRateLimitMiddleware)
```

**GOTCHA**: If the route handler raises, `call_next` still returns (FastAPI converts to 500); `release` runs. If the middleware itself fails mid-dispatch, the concurrent slot leaks until next process restart. Acceptable for an in-memory limiter.

Defaults from config (see Task 5):
- `service.rate_limits.max_concurrent`: 3
- `service.rate_limits.max_per_minute`: 30

**VALIDATE**: Unit test concurrent + windowed limits; integration test that a 4th concurrent `POST /executions` with the same token returns 429 with `Retry-After`.

### Task 2 — UPDATE `src/service/app.py` (this plan owns the final factory)

```python
from contextlib import asynccontextmanager
from fastapi import APIRouter, Depends, FastAPI
from starlette.middleware.cors import CORSMiddleware

from src.config_loader import get_config
from src.core.persistence.db import ensure_initialized
from src.service.auth import load_or_create_token, require_token, require_token_ws
from src.service.deps import command_center_lifespan, get_db_conn   # lifespan from plan 04
from src.service.rate_limit import TokenRateLimiter
from src.service.routes import executions, stream, commands


def _validate_cors(origins: list[str]) -> None:
    if "*" in origins:
        raise RuntimeError(
            "service.cors_origins=['*'] is incompatible with allow_credentials=True; "
            "browsers silently reject. Use explicit origins."
        )


def create_app() -> FastAPI:
    ensure_initialized()
    cfg = get_config()

    app = FastAPI(
        title="Sentinel Command Center API",
        version="0.1",
        lifespan=command_center_lifespan,          # plan 04: adopts workers, reaper task, supervisor
    )
    app.state.service_token = load_or_create_token()
    app.state.rate_limiter = TokenRateLimiter(
        max_concurrent=int(cfg.get("service.rate_limits.max_concurrent", 3)),
        max_per_minute=int(cfg.get("service.rate_limits.max_per_minute", 30)),
    )

    cors_origins = cfg.get("service.cors_origins", []) or []
    _validate_cors(cors_origins)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["authorization", "content-type", "idempotency-key"],
        )

    # Unauthenticated: healthcheck only (container probes, future compose healthcheck)
    @app.get("/health")
    def health(conn=Depends(get_db_conn)) -> dict:
        conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "db": "ok"}

    # Read-only HTTP routes: bearer auth, no rate limit
    http_read_protected = APIRouter(dependencies=[Depends(require_token)])
    http_read_protected.include_router(executions.router)         # plan 02 (read)
    app.include_router(http_read_protected)

    # Write HTTP routes: bearer auth + per-token concurrent/minute rate limit
    http_write_protected = APIRouter(dependencies=[Depends(require_token_and_write_slot)])
    http_write_protected.include_router(commands.router)          # plan 04 (write)
    app.include_router(http_write_protected)

    # WebSocket: separate dep because Starlette raises differently on WS
    ws_protected = APIRouter(dependencies=[Depends(require_token_ws)])
    ws_protected.include_router(stream.router)                    # plan 03 (stream)
    app.include_router(ws_protected)

    app.add_middleware(ReleaseRateLimitMiddleware)                # releases concurrent slot after each write response
    return app
```

**GOTCHA — WebSocket dep raises different status**. `require_token_ws` closes with code 1008 + raises 403 (not 401). Document in tests.

**GOTCHA — single-process by design**. `uvicorn.run(create_app(), ...)` on an app instance (not factory string). Supervisor state + SQLite connections are per-process; multi-worker breaks both. This is intentional.

**VALIDATE**: `pytest tests/service` — all prior tests now use an authenticated `TestClient` fixture.

### Task 3 — UPDATE `src/cli.py`

```python
@cli.command()
@click.option("--host", default=None, help="Bind address (default: config or 127.0.0.1)")
@click.option("--port", default=None, type=int, help="Port (default: config or 8787)")
@click.option("--show-token-prefix", is_flag=True, help="Print first 6 chars of the token on startup")
@click.option("--i-know-what-im-doing", is_flag=True, hidden=True)
def serve(host, port, show_token_prefix, i_know_what_im_doing):
    from src.service.auth import load_or_create_token
    from src.service.app import create_app
    cfg = get_config()
    host = host or cfg.get("service.bind_address", "127.0.0.1")
    port = port or int(cfg.get("service.port", 8787))
    if host in ("0.0.0.0", "::") and not i_know_what_im_doing:
        raise click.ClickException(
            f"Refusing to bind {host} without --i-know-what-im-doing. "
            "Use 127.0.0.1 or the docker network IP."
        )
    token = load_or_create_token()
    if show_token_prefix:
        click.echo(f"Token: {token[:6]}... (full value in ~/.sentinel/service_token)")
    uvicorn.run(create_app(), host=host, port=port)
```

Opt-in `--show-token-prefix` avoids CI-scrollback capture by default.

**VALIDATE**: `sentinel serve --host 0.0.0.0` exits 1; with `--i-know-what-im-doing` it runs. `sentinel serve` (no flags) does NOT print the token prefix.

### Task 4 — Audit logging for writes

Add a FastAPI dependency `audit_write(request, token=Depends(require_token))` that logs a structured line for every successful POST to `/executions*`:

```
logger.info("audit write user=%s ip=%s method=%s path=%s",
            token_prefix(token), request.client.host, request.method, request.url.path)
```

Where `token_prefix(token)` = `sha256(token)[:8]` — stable identifier without the secret. Apply as an additional dependency on the `commands.router` (plan 04), not on the read router.

**Why not per-route:** all write endpoints get audited the same way; centralising keeps the endpoints clean.

### Task 5 — UPDATE `config/config.yaml`

```yaml
service:
  bind_address: "127.0.0.1"
  port: 8787
  cors_origins: []           # e.g. ["http://localhost:3000"] — must NOT contain "*"
  rate_limits:
    max_concurrent: 3         # per token
    max_per_minute: 30        # per token
```

### Task 6 — CREATE `tests/service/test_auth.py` + central conftest

- HTTP GET no header → 401
- HTTP GET wrong scheme (`Basic ...`) → 401
- HTTP GET correct token → 200
- HTTP GET wrong token → 401; audit log line written
- `/health` reachable without a token → 200
- WebSocket connect without token → closed with 1008 / 403
- WebSocket connect with `Authorization` header → accepted
- WebSocket connect with `?token=` query param → accepted
- WebSocket connect with wrong `?token=` → 1008 / 403
- HTTP with `?token=wrong` (no header) → 401 (query-param fallback must NOT apply to HTTP)
- Token file: auto-created with `stat().st_mode & 0o777 == 0o600`; env var wins over file
- Atomic create: second concurrent call to `load_or_create_token()` observes the token written by the first (no races corrupt the file)
- `_validate_cors(["*"])` raises `RuntimeError` at startup
- Rate limit: 4th concurrent start from same token → 429 with `Retry-After`
- Rate limit: 31st start in a minute → 429

Centralise `TestClient` fixtures in `tests/service/conftest.py`:
```python
@pytest.fixture
def authed_client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("SENTINEL_SERVICE_TOKEN", "test-token-abc")
    monkeypatch.setenv("SENTINEL_DB_PATH", str(tmp_path / "test.db"))
    from src.service.app import create_app
    with TestClient(create_app()) as c:
        c.headers["Authorization"] = "Bearer test-token-abc"
        yield c
```

All prior route tests (`test_executions_routes.py`, `test_stream.py`, `test_commands_routes.py`) use this fixture.

**VALIDATE**: `pytest tests/service -v`.

---

## Validation Commands

```bash
poetry run pytest tests/service -v
poetry run pytest -x

# manual:
unset SENTINEL_SERVICE_TOKEN
rm -f ~/.sentinel/service_token
sentinel serve &
TOKEN=$(cat ~/.sentinel/service_token)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8787/executions | jq
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/executions        # expect 401
curl -s http://127.0.0.1:8787/health                                            # expect 200
stat -c '%a' ~/.sentinel/service_token                                           # expect 600
```

## Acceptance Criteria

- [ ] All protected routes reject requests without a valid bearer token (401)
- [ ] `/health` is reachable unauthenticated
- [ ] WebSocket route accepts `?token=` or `Authorization` header
- [ ] Binding to `0.0.0.0` requires the escape-hatch flag
- [ ] Token file auto-created with mode 0600
- [ ] Env var overrides file
- [ ] CORS defaults closed; configurable allowlist
- [ ] Auth failures produce a log line with client IP + route

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Token file briefly world-readable (write→chmod window) | (was MED) — RESOLVED | — | `O_CREAT\|O_EXCL` with mode 0o600 at creation; no second syscall. |
| Token leaks to stdout / scrollback | MED | MED | `--show-token-prefix` is opt-in; default silent. File path printed, not value. |
| Token env var visible in `ps -eww` | LOW | MED | Documented: prefer the file-based token on shared hosts; env var is for CI. |
| `?token=` on WS ends up in access logs / reverse-proxy logs | MED | MED | Documented. Recommend header-capable proxies. HTTP never accepts the query-string token. |
| `secrets.compare_digest` compares against stored token that's been mutated under us | LOW | LOW | Token loaded once at startup into `app.state.service_token`; file edits require restart. |
| CORS wildcard + credentials footgun | MED | LOW | `_validate_cors` raises at startup when `"*"` is present. Caught by unit test. |
| Leaked bearer → unbounded Anthropic spend | MED | HIGH | Per-token rate limits (concurrent + per-minute) default 3/30; configurable. 429 + `Retry-After`. |
| Breaking existing test fixtures en masse | HIGH | LOW | Central `authed_client` fixture in `tests/service/conftest.py`; one place to change. |
| WS close code / status code surprises | MED | LOW | Tests assert 1008/403 on WS and 401 on HTTP explicitly. |

## Notes

- Branch: `experimental/command-center-05-auth`.
- Deliberately single shared-secret auth. A real user/role model (multi-user, scoped tokens) is a follow-up, out of scope here.
- `/health` is unauthenticated so container health probes (future compose `healthcheck`) don't need the token.
- Audit = structured log line, not a DB table. If auditability needs to survive log rotation, a later plan adds an `audit_log` table.
