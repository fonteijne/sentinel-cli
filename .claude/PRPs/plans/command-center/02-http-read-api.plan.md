# Feature: Command Center — HTTP Read API

## Summary

Add a FastAPI service that exposes read-only endpoints over the execution, event, and agent-result data produced by the Foundation plan. No auth, no writes, no streams — those are plans 03–05.

## User Story

As a Command Center dashboard (or any HTTP client)
I want to list executions, fetch a single execution's state, paginate through its events, and read its agent results over HTTP
So that I can render an overview of what Sentinel has done and is doing without parsing logs or hitting SQLite directly.

## Problem Statement

After Foundation (plan 01), execution state lives in `~/.sentinel/sentinel.db` but has no network-accessible surface. A dashboard cannot run inside sentinel-dev without a read API.

## Solution Statement

A minimal FastAPI app (factory pattern) mounted with a single `executions` router, backed directly by `ExecutionRepository` from Foundation. Uvicorn runs it via a new `sentinel serve` CLI command. Bound to `127.0.0.1` by default; plan 05 adds auth + proper network binding.

## Metadata

| Field | Value |
|---|---|
| Type | NEW_CAPABILITY |
| Complexity | LOW |
| Systems Affected | `src/service/*` (new), `src/cli.py`, `pyproject.toml` |
| Dependencies | `fastapi ^0.110`, `uvicorn[standard] ^0.27` (new), existing `pydantic ^2.5` |
| Estimated Tasks | 6 |
| Prerequisite | [`01-foundation.plan.md`](01-foundation.plan.md) must be complete |

---

## Endpoints

| Method | Path | Returns | Query params |
|---|---|---|---|
| GET | `/health` | `{"status":"ok","db":"ok"}` | — |
| GET | `/executions` | Paginated list of `Execution` | `project`, `ticket_id`, `status`, `kind`, `limit` (default 50, max 200), `before` (ISO timestamp) |
| GET | `/executions/{id}` | Single `Execution` or 404 | — |
| GET | `/executions/{id}/events` | Paginated list of events | `since_seq` (default 0), `limit` (default 200, max 1000) |
| GET | `/executions/{id}/agent-results` | List of recorded agent results | — |

All list endpoints return `{"items": [...], "next_cursor": <opaque or null>}`. No aggregate counts, no search — dashboard can do that client-side or in a later plan.

---

## Mandatory Reading

| Priority | File | Why |
|---|---|---|
| P0 | `.claude/PRPs/plans/command-center/01-foundation.plan.md` | Defines `Execution`, `ExecutionRepository`, `events` table |
| P0 | `src/core/execution/repository.py` (from plan 01) | The methods the routes will call |
| P1 | `src/cli.py:1-40, 2119+` | Click command registration patterns to mirror for `sentinel serve` |
| P2 | [FastAPI docs — Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/) | Connection-per-request pattern |
| P2 | [FastAPI docs — Response models](https://fastapi.tiangolo.com/tutorial/response-model/) | Decouple ORM-ish objects from API shapes |

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `src/service/__init__.py` | CREATE | Package marker |
| `src/service/app.py` | CREATE | FastAPI factory `create_app() -> FastAPI` |
| `src/service/deps.py` | CREATE | Request-scoped dependency injectors (`get_repo`, `get_db_conn`) |
| `src/service/schemas.py` | CREATE | Pydantic response models: `ExecutionOut`, `EventOut`, `AgentResultOut`, `ListResponse[T]` |
| `src/service/routes/__init__.py` | CREATE | Package marker |
| `src/service/routes/executions.py` | CREATE | Five endpoints above |
| `tests/service/__init__.py` | CREATE | — |
| `tests/service/test_executions_routes.py` | CREATE | `TestClient` coverage |
| `src/cli.py` | UPDATE | Add `sentinel serve` Click command |
| `pyproject.toml` | UPDATE | Add `fastapi`, `uvicorn[standard]` |

---

## Patterns to Mirror

**Click command registration** — same shape as existing `plan`/`execute`/`debrief`:
```python
# SOURCE: src/cli.py (plan/execute/debrief command decorators)
@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8787, show_default=True, type=int)
def serve(host: str, port: int) -> None:
    """Start the Sentinel read-only HTTP API."""
    import uvicorn
    from src.service.app import create_app
    uvicorn.run(create_app(), host=host, port=port, log_config=None)
```

**Logger** — every new module starts with the same module-level logger idiom (see Foundation plan).

---

## NOT Building

- Write/command endpoints → plan 04
- Streaming → plan 03
- Auth, CORS, non-localhost binding → plan 05
- OpenAPI hand-curation beyond what FastAPI auto-generates
- Prometheus/metrics
- Pagination by keyset on `executions` (timestamp-before cursor is enough; revisit if needed)

---

## Tasks

### Task 1 — CREATE `src/service/schemas.py`
Response models that mirror Foundation entities but are explicit API shapes (not leaking SQL columns directly). Include `ListResponse[T]` generic with `items`, `next_cursor`. Events serialize `payload_json` back to a dict.

**VALIDATE**: `python -c "from src.service.schemas import ExecutionOut, EventOut"`

### Task 2 — CREATE `src/service/deps.py`
- `get_db_conn()` — yields a connection from `core.persistence.db.get_db()`; FastAPI manages lifetime via dependency.
- `get_repo(conn=Depends(get_db_conn))` — returns `ExecutionRepository(conn)`.

**GOTCHA**: The Foundation DB singleton returns the same connection object; FastAPI workers are single-process for this plan — fine. Plan 04 adds worker concurrency and may need a pool; not our problem yet.

**VALIDATE**: Imported by routes without circular imports.

### Task 3 — CREATE `src/service/routes/executions.py`
Five endpoints. Thin — each is `repo.method(...)` → schema conversion → return.

- 404 via `HTTPException(404)` when `get` returns None.
- `before` param accepts ISO-8601; parse with `datetime.fromisoformat`; reject invalid with 422 (FastAPI does this via pydantic if you type the param).
- `limit` clamped server-side even if client sends more (don't trust, enforce).

**VALIDATE**: `pytest tests/service/test_executions_routes.py`

### Task 4 — CREATE `src/service/app.py`
```python
def create_app() -> FastAPI:
    app = FastAPI(title="Sentinel Command Center API", version="0.1")
    app.include_router(executions.router)
    @app.get("/health")
    def health(conn=Depends(get_db_conn)):
        conn.execute("SELECT 1").fetchone()
        return {"status": "ok", "db": "ok"}
    return app
```
No middleware yet — plan 05 adds auth + CORS.

**VALIDATE**: `python -c "from src.service.app import create_app; app = create_app(); print([r.path for r in app.routes])"`

### Task 5 — UPDATE `src/cli.py`
Add `sentinel serve` (snippet above). Do not import FastAPI at module top of cli.py — keep the import inside the command body so CLI startup time isn't penalised.

**VALIDATE**: `sentinel serve --help` shows options; `sentinel serve --port 0` starts and exits cleanly on Ctrl-C (manual).

### Task 6 — CREATE `tests/service/test_executions_routes.py`
Use `fastapi.testclient.TestClient`. Fixture that seeds a temp DB with a handful of executions + events + agent_results, then exercises each endpoint.

**MIRROR**: `tests/test_session_tracker.py` for tmp_path + dependency override (FastAPI `app.dependency_overrides[get_db_conn] = lambda: sqlite3.connect(tmp_db_path)`).

**Cases**:
- list with project/status/ticket filters
- single GET 200 + 404
- events pagination via `since_seq`
- `limit` clamped when client requests 10000
- invalid ISO `before` → 422
- `/health` returns ok

**VALIDATE**: `pytest tests/service -v`

---

## Validation Commands

```bash
poetry add fastapi 'uvicorn[standard]'
poetry run pytest tests/service -v
poetry run pytest -x                                  # full regression
poetry run python -c "from src.service.app import create_app; create_app()"
```

Manual (in sentinel-dev):
```bash
sentinel serve --port 8787 &
curl -s http://127.0.0.1:8787/health | jq
curl -s 'http://127.0.0.1:8787/executions?limit=5' | jq '.items[0]'
```

---

## Acceptance Criteria

- [ ] All five endpoints return correct shapes against a populated DB
- [ ] Filters and pagination work and are clamped
- [ ] `sentinel serve` starts/stops cleanly
- [ ] No new test failures; no change to Foundation tests
- [ ] OpenAPI docs auto-available at `/docs`

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SQLite connection shared across threads under uvicorn workers | MED | MED | Keep uvicorn `--workers 1` default; document; revisit with pool in plan 04 |
| Events response grows large for long runs | MED | LOW | Cap `limit` at 1000; require cursor for more |
| FastAPI adds non-trivial import time to CLI startup | LOW | LOW | Import inside `serve` command body only |

## Notes

- Branch: `experimental/command-center-02-read-api`.
- This plan is deliberately small so it can land quickly after Foundation and unblock plans 03/04.
