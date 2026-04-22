# Feature: Command Center — Command Endpoints & Worker Supervisor

## Summary

Introduce an out-of-process worker model so executions don't die with the HTTP request or CLI invocation, add write endpoints (start / cancel / retry), and reconcile orphaned runs on service startup.

## User Story

As a Command Center operator
I want to start a new execution, cancel a running one, or retry a failed one over HTTP
And have those runs survive the service restarting or the CLI disconnecting
So that the dashboard is a real control surface, not a read-only viewer.

## Problem Statement

After plans 01–03 the service is observe-only. Runs still start from the CLI and live in the CLI process; if the CLI exits the run dies, and the HTTP request that triggered it would die with it too. A dashboard cannot meaningfully "start a run" until orchestration is decoupled from the invoker.

## Solution Statement

1. `ExecutionWorker` (subprocess entry point) — takes `--execution-id`, configures logging, loads the row, constructs an `Orchestrator`, runs it, exits.
2. `workers` table (new migration 002) — persists `(execution_id PK, pid, started_at, last_heartbeat_at)`. Worker heartbeats every 5s. Lets the service tell a *legitimately running detached worker* apart from an *orphaned row*.
3. `Supervisor` (in-process object owned by the service) — `spawn`, `cancel`, `reap`, `adopt_or_reconcile_on_startup`. Tracks live workers by PID and DB heartbeat.
4. Three write endpoints on the FastAPI app (plan 05 owns `create_app()` composition; this plan contributes the router).
5. Startup hook: for every row in `running/cancelling`, read `workers.last_heartbeat_at` + `os.kill(pid, 0)`; if alive, adopt back into supervisor registry; if stale and dead, mark `failed` with `error='orphaned_on_restart'`; stale-but-alive logs a warning and adopts anyway.
6. `sentinel execute --remote` — POSTs to local service with bearer auth, optionally `--follow` tails the plan 03 stream.

## Metadata

| Field | Value |
|---|---|
| Type | NEW_CAPABILITY |
| Complexity | MEDIUM |
| Systems Affected | `src/core/execution/worker.py` + `supervisor.py` (new), `src/core/persistence/migrations/002_workers.sql` (new), `src/service/routes/commands.py` (new), `src/service/deps.py`, `src/cli.py`, `src/utils/logging_config.py` (new) |
| Dependencies | stdlib `multiprocessing` + `signal` + `httpx` (already in requests-family deps, but httpx preferred for the CLI `--remote` path — or stay on `requests`) |
| Estimated Tasks | 9 |
| Prerequisite | Plan 01 (Foundation) + Plan 02 (Read API; the FastAPI app factory must exist). Plan 05 still owns the final `create_app()` composition. |

---

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/executions` | `StartExecutionBody` (see below) | `202` + `ExecutionOut` (status `queued`, worker transitions it to `running`) |
| POST | `/executions/{id}/cancel` | — | `202` + `ExecutionOut` (status `cancelling`) |
| POST | `/executions/{id}/retry` | — | `202` + new `ExecutionOut` linking to original via `metadata_json.retry_of` |

All three are declared with explicit `status_code=202` on the FastAPI route decorator (default is 200).

All accept an optional `Idempotency-Key` header. If present, repo's `find_by_idempotency_key` returns the existing execution instead of creating a new one; response is the same shape but the `seq` counter / subprocess is not started again.

All are async: the endpoint returns immediately after queueing; real progress is observed via plan 03's stream or plan 02's GET events.

### Request schemas (pydantic v2, `extra="forbid"`)

```python
class ExecutionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revise: bool = False
    max_turns: int | None = Field(default=None, ge=1, le=200)
    follow_up_ticket: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9]+-\d+$")
    # add explicit fields only — never a free-form dict

class StartExecutionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(pattern=r"^[A-Z][A-Z0-9]+-\d+$")
    project: str = Field(min_length=1, max_length=64)
    kind: ExecutionKind                          # imported from src.core.execution.models
    options: ExecutionOptions = ExecutionOptions()
```

**Why `extra="forbid"`:** the body flows into `metadata_json` and is later read by the worker → orchestrator → agent prompts → `Bash` tool calls. Any free-form dict is a prompt-injection vector. Only explicit, enumerated fields pass the boundary.

### Rate limiting

Per-token cap enforced by the auth dependency (introduced in plan 05):
- `max_concurrent_executions` per token (default 3)
- `max_new_executions_per_minute` per token (default 30)

Exceeding either returns `429` with `Retry-After`. Configured under `service.rate_limits.*` in `config.yaml`.

---

## Mandatory Reading

| Priority | File | Why |
|---|---|---|
| P0 | `.claude/PRPs/plans/command-center/01-foundation.plan.md` | `Orchestrator`, `Execution`, `ExecutionStatus` |
| P0 | `src/agent_sdk_wrapper.py` — DooD interaction | Workers inherit env; confirm nothing relies on being the *first* process after `sentinel` CLI |
| P1 | Docker-out-of-Docker notes in `/workspace/CLAUDE.md` | Subprocess workers must still see `/var/run/docker.sock` |
| P1 | [Python `multiprocessing.get_context('spawn')`](https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods) | `spawn` is required — `fork` after FastAPI/uvicorn has imported non-fork-safe libs is a bug |

---

## Files to Change

| File | Action |
|---|---|
| `src/core/persistence/migrations/002_workers.sql` | CREATE — `workers` table |
| `src/core/execution/worker.py` | CREATE — subprocess entry point |
| `src/core/execution/supervisor.py` | CREATE — `Supervisor` class |
| `src/service/routes/commands.py` | CREATE — three endpoints |
| `src/service/routes/__init__.py` | UPDATE — export `commands.router` |
| `src/service/deps.py` | UPDATE — `get_supervisor()` dependency + `lifespan` helper |
| `src/utils/logging_config.py` | CREATE — `configure_logging()` used by CLI, service, and worker |
| `src/cli.py` | UPDATE — `execute --remote [--follow]` flag posts to local service with bearer auth |
| `tests/core/test_supervisor.py` | CREATE |
| `tests/core/test_worker_logging.py` | CREATE — assert spawned worker writes logs + jsonl |
| `tests/service/test_commands_routes.py` | CREATE |
| `tests/integration/test_end_to_end.py` | CREATE — start → stream → cancel → reconcile |

**Not touched here** (plan 05 owns `create_app()`):
- `src/service/app.py` — 05 wires the `lifespan` that owns Supervisor + reconciliation.

---

## Worker Process Model

- Start method: `multiprocessing.get_context("spawn")` to avoid inheriting uvicorn's open fds / FastAPI state.
- Entry: `python -m src.core.execution.worker --execution-id <id>` (also allow in-process spawn for tests via the context API).
- Env: inherits `SENTINEL_*`, `DOCKER_HOST`, `PATH`, etc. — important for DooD to keep working.
- cwd: repository root (`/app` in sentinel-dev) — the orchestrator itself sets worktree cwd via its existing logic.
- Exit code: 0 on `ExecutionStatus.SUCCEEDED`, non-zero otherwise. Supervisor reads row status; exit code is a sanity check.
- **Logs:** worker's `main()` calls `configure_logging()` *first* (before importing anything heavy). `spawn` re-imports the process — `basicConfig` at `cli.py` module top does NOT run in the child. The worker also re-initializes the structured diagnostic file path so `logs/agent_diagnostics.jsonl` keeps being written.
- **Heartbeat:** a daemon thread inside the worker `UPDATE workers SET last_heartbeat_at = now WHERE execution_id = ?` every 5s. Heartbeat writes use their own connection, `BEGIN IMMEDIATE` + `COMMIT`, `busy_timeout=30000`.
- **Child containers:** the worker records compose project names in `executions.metadata_json.compose_projects[]` as it starts per-ticket `appserver-*` stacks.

## Cancellation & cleanup

Two-stage signal dance:

1. `Supervisor.cancel(execution_id)`:
   - Row `status → cancelling`; publish `ExecutionCancelling`.
   - `os.kill(pid, signal.SIGTERM)`.
2. Worker SIGTERM handler (main thread):
   - Set internal cancel flag; lets current agent turn finish.
   - Publish `ExecutionCancelled` after the current turn; row `status → cancelled`.
   - Exit 0.
3. If the worker hasn't exited within **grace=20s**: `os.kill(pid, signal.SIGINT)` (second try; some SDK calls handle SIGINT but not SIGTERM).
4. If still alive at **kill=30s**: `os.kill(pid, signal.SIGKILL)`.
5. **Post-mortem cleanup (always runs, regardless of how the worker died):**
   - For each compose project in `metadata_json.compose_projects[]`:
     `docker compose -p <name> down -v --timeout 5`.
   - Prune worktree branch if the orchestrator's finally didn't run.
   - Row: if currently `cancelling` → `cancelled` (success), else `failed` with `error='terminated_after_timeout'`.
   - Publish the terminal event (`ExecutionCancelled` or `ExecutionFailed`).
6. Post-mortem is **idempotent** — it may have partially completed inside the worker's `finally` already. Each step is defensive (`docker compose down` on a non-existent project is a no-op warning, not an error).

**GOTCHA — best-effort mid-turn cancel.** The claude-agent-sdk call is synchronous. Cancellation takes effect between turns, which can be minutes. The 30s escalation window is the operator's backstop.

**GOTCHA — container leak on SIGKILL without post-mortem.** If the supervisor itself is killed mid-cancel (operator `kill -9`s the service), the post-mortem never runs. Next startup's reconciliation has access to the compose project names (they're in `metadata_json`) and runs the same cleanup on reconciled rows.

## Startup reconciliation (with heartbeat)

On service boot, `Supervisor.adopt_or_reconcile_on_startup(repo)`:

```python
for row in repo.list(status=("running","cancelling")):
    worker = repo.get_worker(row.id)             # reads workers table
    alive = worker and _pid_alive(worker.pid)
    fresh = worker and (now - worker.last_heartbeat_at) < timedelta(seconds=30)
    if alive and fresh:
        _workers[row.id] = _adopt_existing(worker.pid)      # track but don't respawn
        continue
    # Orphaned — mark failed and run post-mortem cleanup
    repo.record_ended(row.id, ExecutionStatus.FAILED, error="orphaned_on_restart")
    bus.publish(ExecutionFailed(execution_id=row.id, error="orphaned_on_restart"))
    for project in row.metadata.get("compose_projects", []):
        _docker_compose_down_best_effort(project)
```

`_pid_alive(pid)` is `os.kill(pid, 0)`; raises `ProcessLookupError` if dead.

This preserves the user story "runs survive the service restarting" for legitimate in-flight workers and keeps clean reconciliation for actually-dead ones.

---

## Tasks

### Task 1 — CREATE `src/core/persistence/migrations/002_workers.sql`
```sql
CREATE TABLE IF NOT EXISTS workers (
    execution_id      TEXT PRIMARY KEY REFERENCES executions(id) ON DELETE CASCADE,
    pid               INTEGER NOT NULL,
    started_at        TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    compose_projects  TEXT NOT NULL DEFAULT '[]'    -- JSON array, populated by worker as it starts per-ticket stacks
);
CREATE INDEX IF NOT EXISTS idx_workers_heartbeat ON workers(last_heartbeat_at);
```

**VALIDATE**: `sqlite3 ~/.sentinel/sentinel.db ".schema workers"` after `ensure_initialized()`.

### Task 2 — CREATE `src/utils/logging_config.py`
Extract logging setup from `cli.py`'s module-level `basicConfig` into a reusable `configure_logging(level: int = logging.INFO, *, enable_jsonl: bool = True) -> None`. Called by CLI, by FastAPI lifespan, and — critically — by the worker entrypoint *before* any other import.

**GOTCHA**: `spawn` re-imports; without this, workers run silently. A test must assert the spawned worker writes a line to the log file.

**VALIDATE**: `pytest tests/core/test_worker_logging.py`.

### Task 3 — CREATE `src/core/execution/worker.py`
Stand-alone entry point (`python -m src.core.execution.worker --execution-id X`).

```python
def main() -> int:
    from src.utils.logging_config import configure_logging
    configure_logging()
    # ... now safe to import orchestration, SDK, etc.
    from src.core.persistence.db import connect, ensure_initialized
    from src.core.execution.repository import ExecutionRepository
    from src.core.execution.orchestrator import Orchestrator
    # ... parse argv for --execution-id, register SIGTERM/SIGINT handlers, start heartbeat thread, run orchestrator.
```

- Heartbeat daemon thread: opens its own sqlite connection, `UPDATE workers SET last_heartbeat_at = ? WHERE execution_id = ?` every 5s.
- SIGTERM/SIGINT → set cancel flag; cleanup in `finally`: publish terminal event, run `docker compose down` for any project the orchestrator registered, close DB.
- Exit code: 0 on success, non-zero otherwise.

**GOTCHA**: Options are read from `executions.metadata_json`, never argv — keeps the endpoint body small and escape-free.

**VALIDATE**: `python -m src.core.execution.worker --help` works; invoking on a seeded execution row runs it to completion and produces events in the DB.

### Task 4 — CREATE `src/core/execution/supervisor.py`

```python
class Supervisor:
    def __init__(self, repo: ExecutionRepository, bus: EventBus):
        self._ctx = multiprocessing.get_context("spawn")
        self._workers: dict[str, multiprocessing.Process] = {}
        self._lock = threading.Lock()
        self._repo = repo
        self._bus = bus

    def spawn(self, execution_id: str) -> None: ...
    def cancel(self, execution_id: str) -> None: ...
    def reap(self) -> int: ...                   # called by periodic task + on request
    def adopt_or_reconcile_on_startup(self) -> tuple[int, int]: ...   # (adopted, reconciled)
    def post_mortem(self, execution_id: str) -> None: ...             # docker compose down, etc.

    def _pid_alive(self, pid: int) -> bool:
        try: os.kill(pid, 0); return True
        except ProcessLookupError: return False
```

All reads/writes of `_workers` take `_lock` — enforce with a small `@_locked` decorator.

**GOTCHA — periodic reap**: schedule via `asyncio.create_task(periodic_reap(...))` in the FastAPI lifespan (Task 6), interval 5s.

**VALIDATE**: `pytest tests/core/test_supervisor.py`.

### Task 5 — CREATE `src/service/routes/commands.py`
Three endpoints, pydantic `StartExecutionBody` / `ExecutionOptions` with `extra="forbid"`.

```python
@router.post("/executions", status_code=202, response_model=ExecutionOut)
def start(
    body: StartExecutionBody,
    idempotency_key: Annotated[str | None, Header()] = None,
    repo: Annotated[ExecutionRepository, Depends(get_repo)] = ...,
    supervisor: Annotated[Supervisor, Depends(get_supervisor)] = ...,
):
    if idempotency_key:
        existing = repo.find_by_idempotency_key(idempotency_key)
        if existing:
            return existing
    execution = repo.create(..., idempotency_key=idempotency_key)
    supervisor.spawn(execution.id)                 # worker transitions row to 'running'
    return execution
```

Cancel: 404 if not found; 409 if not in `running`/`queued`/`cancelling`; else `supervisor.cancel(id)`.

Retry: 404 if not found; 409 if original still running; else new row with `metadata_json.retry_of = original.id`, copy `ticket_id/project/kind/options`, `supervisor.spawn`.

**GOTCHA — race on spawn failure**: if `supervisor.spawn` raises after `repo.create`, the row is `queued` with no worker. Catch in the endpoint, `repo.record_ended(id, FAILED, error="spawn_failed: ...")`, return 500 with a descriptive body.

**VALIDATE**: `pytest tests/service/test_commands_routes.py`.

### Task 6 — UPDATE `src/service/deps.py` and add lifespan helper

```python
# deps.py
def get_supervisor(request: Request) -> Supervisor:
    return request.app.state.supervisor

@asynccontextmanager
async def command_center_lifespan(app: FastAPI):
    ensure_initialized()
    conn = connect()
    repo = ExecutionRepository(conn)
    bus = EventBus(conn)
    supervisor = Supervisor(repo=repo, bus=bus)
    adopted, reconciled = supervisor.adopt_or_reconcile_on_startup()
    logger.info("startup: adopted=%d reconciled=%d", adopted, reconciled)
    app.state.supervisor = supervisor
    app.state.reaper_task = asyncio.create_task(_periodic_reap(supervisor))
    try:
        yield
    finally:
        app.state.reaper_task.cancel()
        for eid in list(supervisor._workers):
            supervisor.cancel(eid)
        conn.close()
```

Plan 05's `create_app()` does `app = FastAPI(lifespan=command_center_lifespan)`. No `@app.on_event(...)` — deprecated.

**GOTCHA — uvicorn single-process**: `uvicorn.run(create_app(), host=..., port=...)` on an app *instance* (not factory string) keeps a single process, which is required because Supervisor state and SQLite writes are per-process. Document this as intentional in plan 02 and plan 05 (not a "we forgot to scale" bug).

### Task 7 — UPDATE `src/cli.py` — add `execute --remote [--follow]`

- Read bearer token from `SENTINEL_SERVICE_TOKEN` env or `~/.sentinel/service_token` file (file path + permission semantics defined in plan 05).
- Base URL: `SENTINEL_SERVICE_URL` env (default `http://127.0.0.1:8787`), with `service.port` / `service.bind_address` also readable from config as fallback.
- POST to `/executions` with `Authorization: Bearer <token>` and JSON body built from CLI options. Support `Idempotency-Key` header if user passes `--idempotency-key K` (otherwise omit).
- If `--follow`: open `ws://.../executions/{id}/stream?since_seq=0` with the same bearer token (via `Authorization` header or `?token=` fallback per plan 05's WS auth) and print incoming events as plain-text one-liners.
- Clear errors:
  - Service unreachable → exit 3 with "service not running at {url} — start it with `sentinel serve`".
  - 401 → exit 4 with "invalid token at ~/.sentinel/service_token".
  - 429 → exit 5 with "rate limit reached; retry after {Retry-After}s".

**GOTCHA**: `--remote` must never silently fall back to in-process. Ambiguity masks real failures.

**VALIDATE**: `sentinel execute --remote TICKET --follow` starts a run, streams events, survives the CLI exiting before the run completes.

### Task 8 — tests

- `tests/core/test_supervisor.py` — spawn a no-op worker (trivial `sys.exit(0)` entrypoint); assert `_workers` tracks it; cancel via SIGTERM; reap removes from dict. Reconcile: seed `running` row with dead PID → `failed`; seed `running` row with live PID + fresh heartbeat → adopted (dict size +=1).
- `tests/core/test_worker_logging.py` — spawn the real worker with a seeded no-op execution; assert that `logs/cli_stderr.log` (or the configured file) contains at least one INFO line, and `logs/agent_diagnostics.jsonl` is appended to.
- `tests/service/test_commands_routes.py` — POST start with valid body → 202 + row; `extra="forbid"` rejects unknown keys → 422; idempotency key deduplicates; cancel happy + 409 paths; retry creates linked row.
- `tests/integration/test_end_to_end.py` — drive the full flow using `TestClient` + a fake Orchestrator that emits 5 events then completes. Assert: POST → stream → events arrive in order → terminal frame → row is `succeeded`. Also: POST → cancel → row is `cancelled` with matching events.

**VALIDATE**: `pytest tests/core tests/service tests/integration -v`.

---

## Validation Commands

```bash
poetry run pytest tests/core tests/service -v
poetry run pytest -x

# manual in sentinel-dev:
sentinel serve --port 8787 &
curl -X POST http://127.0.0.1:8787/executions \
  -H 'Content-Type: application/json' \
  -d '{"ticket_id":"PROJ-123","project":"proj","kind":"execute","options":{}}'
# follow in another terminal:
websocat 'ws://127.0.0.1:8787/executions/<id>/stream'
# cancel:
curl -X POST http://127.0.0.1:8787/executions/<id>/cancel
# simulate restart:
kill -9 $(pgrep -f "sentinel serve")
sentinel serve --port 8787 &
# the row should now be failed with error=orphaned_on_restart
```

## Acceptance Criteria

- [ ] `POST /executions` starts a subprocess; CLI exit does not kill it
- [ ] `POST /executions/{id}/cancel` transitions row to `cancelled` within 30s (or kills + marks after with post-mortem cleanup)
- [ ] `POST /executions/{id}/retry` creates a new linked row
- [ ] `extra="forbid"` rejects unknown body fields with 422
- [ ] `Idempotency-Key` deduplicates retries of the same POST
- [ ] Service restart **adopts** live detached workers (heartbeat + PID check) and **reconciles** dead ones
- [ ] Per-ticket `appserver-*` containers are cleaned up on cancel/failure/orphan (no leaks)
- [ ] Worker writes to both `logs/*.log` (via `configure_logging`) AND `logs/agent_diagnostics.jsonl`
- [ ] Integration test covers start → stream → cancel → reconcile
- [ ] Foundation + plans 02/03 tests still green

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `spawn` context can't pickle config/orchestrator | HIGH | HIGH | Worker is a module entry point with argv; nothing is pickled — the child reconstructs state from the DB + env |
| Subprocess inherits file descriptors it shouldn't (DB connection, sockets) | MED | MED | Use `spawn`, not `fork`. Verify with `lsof` during a test run |
| Cancel takes too long because agent SDK call is uninterruptible | MED | MED | 20s SIGTERM → 10s SIGINT → SIGKILL escalation; post-mortem cleanup runs regardless |
| Worker logging silently disabled because `spawn` re-imports without running `basicConfig` | HIGH (if unaddressed) | MED | `configure_logging()` is the first call in `worker.main`; `test_worker_logging` guards this |
| Per-ticket `appserver-*` containers leak on SIGKILL | HIGH (if unaddressed) | MED | Supervisor post-mortem reads `metadata_json.compose_projects[]` and runs `docker compose down` idempotently |
| `_workers` dict mutated from both HTTP thread and asyncio reaper | MED | MED | `threading.Lock` enforced on all read/write paths via `@_locked` decorator |
| HA deploy false-reconciles a peer's in-flight workers | LOW | HIGH | Out of scope — explicit single-instance assumption. Documented. |
| Worker log files grow unbounded under `/app/logs/workers/` | LOW | LOW | Log rotation is ops; file issue as follow-up debt |
| Leaked bearer token triggers unlimited runs (Anthropic $$) | MED | HIGH | Per-token concurrency + per-minute rate cap enforced in plan 05's auth dep; config-driven |

## Notes

- Branch: `experimental/command-center-04-commands-workers`.
- Why not asyncio tasks instead of subprocesses? A raise/crash inside an asyncio task can take down the service or leak handles. Subprocesses give real isolation at the OS level, and `spawn` sidesteps uvicorn/fork hazards. Also: a subprocess is what the existing CLI flow effectively is today — minimal conceptual distance.
- Why DB-heartbeats instead of a PID file? Two reasons: (a) the `workers` table survives reboots and is queryable for operator tooling; (b) SQLite-level coordination reuses the WAL we already need. A PID file is fine fallback if `workers` ever has to be dropped.
- Plan 05's auth dep layers **on top of** these endpoints; this plan deliberately leaves the routes unauthenticated in isolation so tests can exercise them without the token dance. When 05 lands, the `protected` router wraps all three endpoints.
