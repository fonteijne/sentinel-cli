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

1. Bearer-token FastAPI dependency applied globally via a router-level dependency.
2. Token resolved at startup from (in order): `SENTINEL_SERVICE_TOKEN` env var → `~/.sentinel/service_token` file → auto-generate (and persist with `chmod 600`) if none exists.
3. CORS middleware with explicit allowlist from config; default empty (only same-origin works, which is fine for same-host dashboard dev).
4. Default bind address `127.0.0.1`; `sentinel serve --host 0.0.0.0` prints a warning unless `--i-know-what-im-doing` is passed.
5. Audit log line on each auth failure with client IP + route.

## Metadata

| Field | Value |
|---|---|
| Type | ENHANCEMENT |
| Complexity | LOW |
| Systems Affected | `src/service/auth.py` (new), `src/service/app.py`, `src/cli.py`, docs |
| Dependencies | None new |
| Estimated Tasks | 5 |
| Prerequisite | Plans 02, 03, 04 |

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
TOKEN_FILE = Path.home() / ".sentinel" / "service_token"

def load_or_create_token() -> str:
    env = os.environ.get("SENTINEL_SERVICE_TOKEN")
    if env:
        return env
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)
    TOKEN_FILE.chmod(0o600)
    logger.warning("generated new service token at %s", TOKEN_FILE)
    return token

def require_token(request: Request) -> None:
    expected = request.app.state.service_token
    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(presented, expected):
        logger.warning("auth failure from %s %s %s", request.client.host, request.method, request.url.path)
        raise HTTPException(status_code=401, detail="unauthorized")
```

**GOTCHA**: Use `secrets.compare_digest` — timing-safe. `==` is not.

**GOTCHA**: Token file creation races if two processes boot simultaneously. Acceptable for single-host dev; document. A lockfile would be over-engineering.

**VALIDATE**: Unit tests in Task 5.

### Task 2 — UPDATE `src/service/app.py`

```python
def create_app() -> FastAPI:
    app = FastAPI(title="Sentinel Command Center API", version="0.1")
    app.state.service_token = load_or_create_token()

    cors_origins = get_config().get("service.cors_origins", [])
    if cors_origins:
        app.add_middleware(CORSMiddleware,
                           allow_origins=cors_origins,
                           allow_credentials=True,
                           allow_methods=["GET","POST"],
                           allow_headers=["authorization","content-type"])

    # Auth applies to every route EXCEPT /healthz
    protected = APIRouter(dependencies=[Depends(require_token)])
    protected.include_router(executions.router)
    protected.include_router(stream.router)          # plan 03
    protected.include_router(commands.router)        # plan 04
    app.include_router(protected)

    @app.get("/healthz")                              # unauthenticated on purpose
    def healthz(...): ...

    return app
```

**GOTCHA — WebSocket auth**: FastAPI's `Depends(require_token)` works on WebSocket routes but the header must be sent on the upgrade request. Dashboards that can't set headers on a WS (browsers can't natively) will need `?token=` query param as fallback. Add that to `require_token` — accept `Authorization` header OR `token` query param for WS routes only.

**VALIDATE**: `pytest tests/service` — all prior tests need `Authorization: Bearer <token>` in their `TestClient` fixtures; update fixtures centrally.

### Task 3 — UPDATE `src/cli.py`

```python
@cli.command()
@click.option("--host", default=None, help="Bind address (default: config or 127.0.0.1)")
@click.option("--port", default=8787, show_default=True, type=int)
@click.option("--i-know-what-im-doing", is_flag=True, hidden=True)
def serve(host, port, i_know_what_im_doing):
    from src.service.auth import load_or_create_token
    from src.service.app import create_app
    host = host or get_config().get("service.bind_address", "127.0.0.1")
    if host in ("0.0.0.0", "::") and not i_know_what_im_doing:
        raise click.ClickException(
            f"Refusing to bind {host} without --i-know-what-im-doing. "
            "Use 127.0.0.1 or the docker network IP."
        )
    token = load_or_create_token()
    click.echo(f"Token: {token[:6]}... (full value in ~/.sentinel/service_token)")
    uvicorn.run(create_app(), host=host, port=port)
```

**VALIDATE**: `sentinel serve --host 0.0.0.0` exits 1; with `--i-know-what-im-doing` it runs and prints a warning.

### Task 4 — UPDATE `config/config.yaml`

Add:
```yaml
service:
  bind_address: "127.0.0.1"
  cors_origins: []   # e.g. ["http://localhost:3000"] for a local dashboard dev server
```

### Task 5 — CREATE `tests/service/test_auth.py`

- No header → 401
- Wrong scheme (`Basic`) → 401
- Correct token → 200
- Timing-safe comparison (hard to test; assert `secrets.compare_digest` is called via monkeypatch)
- `/healthz` reachable without a token
- WebSocket rejects without token, accepts with `?token=`
- Token file auto-created with mode 0600; env var wins over file

Update existing fixtures in `tests/service/test_executions_routes.py`, `tests/service/test_stream.py`, `tests/service/test_commands_routes.py` to include `Authorization: Bearer <token>` (centralise via a `client` fixture in `tests/service/conftest.py`).

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
curl -s http://127.0.0.1:8787/healthz                                            # expect 200
stat -c '%a' ~/.sentinel/service_token                                           # expect 600
```

## Acceptance Criteria

- [ ] All protected routes reject requests without a valid bearer token (401)
- [ ] `/healthz` is reachable unauthenticated
- [ ] WebSocket route accepts `?token=` or `Authorization` header
- [ ] Binding to `0.0.0.0` requires the escape-hatch flag
- [ ] Token file auto-created with mode 0600
- [ ] Env var overrides file
- [ ] CORS defaults closed; configurable allowlist
- [ ] Auth failures produce a log line with client IP + route

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Token leaks to logs or process listings | MED | HIGH | Never log full token (CLI prints prefix only); `chmod 600`; no query string on HTTP routes (only WS) |
| `secrets.compare_digest` compares against stored token that's been mutated under us | LOW | LOW | Token is loaded once at startup into `app.state.service_token`; file edits require restart |
| `?token=` on WS ends up in access logs / reverse-proxy logs | MED | MED | Document explicitly; recommend header-capable proxies; no action in code beyond opt-in |
| Breaking existing test fixtures en masse | HIGH | LOW | Centralise in `conftest.py` so the change is one place |

## Notes

- Branch: `experimental/command-center-05-auth`.
- Deliberately single shared-secret auth. A real user/role model is a follow-up, out of scope here.
- `healthz` is unauthenticated so container health probes (future compose `healthcheck`) don't need the token.
