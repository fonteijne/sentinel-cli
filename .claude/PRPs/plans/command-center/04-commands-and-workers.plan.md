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
| `src/core/execution/repository.py` | UPDATE — extend with `get_worker`, `set_worker_heartbeat`, `mark_metadata` helpers |
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
- **Env allowlist (not inheritance-by-default)**: Supervisor builds the child env explicitly rather than passing the parent env wholesale. `spawn()` passes `env={...}` to `Process`. Allowlist:
  - `PATH`, `HOME`, `LANG`, `LC_ALL`, `TZ` — baseline system.
  - `DOCKER_HOST`, `DOCKER_CERT_PATH`, `DOCKER_TLS_VERIFY` — DooD requirements.
  - `SENTINEL_*` — Sentinel config.
  - `JIRA_*`, `GITLAB_*`, `ANTHROPIC_*` — external API credentials the orchestrator actually needs.
  - Everything else is dropped. If a future dep needs a var, it gets explicitly added to the allowlist.
- cwd: repository root (`/app` in sentinel-dev) — the orchestrator itself sets worktree cwd via its existing logic.
- Exit code: 0 on `ExecutionStatus.SUCCEEDED`, non-zero otherwise. Supervisor reads row status; exit code is a sanity check.
- **Logs:** worker's `main()` calls `configure_logging()` *first* (before importing anything heavy). `spawn` re-imports the process — `basicConfig` at `cli.py` module top does NOT run in the child. The worker also re-initializes the structured diagnostic file path so `logs/agent_diagnostics.jsonl` keeps being written.
- **Heartbeat:** a daemon thread inside the worker `UPDATE workers SET last_heartbeat_at = now WHERE execution_id = ?` every 5s. Heartbeat writes use their own connection, `BEGIN IMMEDIATE` + `COMMIT`, `busy_timeout=30000`. The thread checks a `threading.Event` between iterations and exits cleanly on shutdown so SIGTERM doesn't abruptly sever a write.
- **Child containers — pre-registration ordering**. When the worker is about to start a per-ticket stack (`docker compose up -p sentinel-<ticket>`), it **first** appends the project name to `executions.metadata_json.compose_projects[]` (`repo.mark_metadata`), commits, **then** invokes `docker compose up`. If the worker dies between the metadata write and `up`, reconciliation sees a project name with no containers — `docker compose down` is a no-op. If the worker dies between `up` and the next metadata write, the name is already recorded and cleanup runs. **The only leak window is between `up` starting and the registry write, which is now closed.**
- **`workers.compose_projects` column duplication:** the same list lives in `workers.compose_projects` (plan 04's migration 002) AND `executions.metadata_json.compose_projects[]`. The `workers` row is source of truth during the run (deleted on reap); the `metadata_json` copy is the archival record used by reconciliation after the worker row is gone. `repo.mark_metadata` writes to both atomically.

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

   Each step is **independently wrapped in try/except** — a failure in one step must not skip the next:
   ```python
   def post_mortem(execution_id: str) -> None:
       # 1. Publish the terminal event FIRST — appending to event log is the cheapest and most important step.
       try: bus.publish(terminal_event_for(execution_id))
       except Exception: logger.exception("post_mortem: publish terminal failed")
       # 2. Docker compose down for each recorded project
       for project in repo.get(execution_id).metadata.get("compose_projects", []):
           try: subprocess.run(["docker","compose","-p",project,"down","-v","--timeout","5"], check=False)
           except Exception: logger.exception("post_mortem: compose down %s failed", project)
       # 3. Worktree prune (best-effort; orchestrator's finally normally handles this)
       try: _prune_worktree_if_any(execution_id)
       except Exception: logger.exception("post_mortem: worktree prune failed")
       # 4. Row state transitions (and the success marker)
       try: repo.record_ended(execution_id, _terminal_status(), error=_error_msg())
       except Exception: logger.exception("post_mortem: record_ended failed")
       # 5. Mark post-mortem complete so reconciliation doesn't re-run us
       try: repo.mark_metadata(execution_id, post_mortem_complete=True)
       except Exception: logger.exception("post_mortem: mark_metadata failed")
   ```
6. Post-mortem is **idempotent**. Each step is defensive; running post_mortem a second time on an already-cleaned row is safe (compose projects gone, terminal event dedup'd by seq, `record_ended` is a no-op if `ended_at is not None`).
7. **Reconciliation sweeps post-mortem-incomplete rows too**: startup includes `status IN ('failed','cancelled') AND json_extract(metadata_json,'$.post_mortem_complete') IS NOT 1` in the set to re-clean. This catches the case where the supervisor itself was killed mid-cleanup.

**GOTCHA — best-effort mid-turn cancel.** The claude-agent-sdk call is synchronous. Cancellation takes effect between turns, which can be minutes. The 30s escalation window is the operator's backstop.

**GOTCHA — container leak on SIGKILL without post-mortem.** If the supervisor itself is killed mid-cancel (operator `kill -9`s the service), the post-mortem never runs. Next startup's reconciliation has access to the compose project names (they're in `metadata_json`) and runs the same cleanup on reconciled rows.

## Startup reconciliation (with heartbeat)

On service boot, `Supervisor.adopt_or_reconcile_on_startup()` walks two sets:

**Set A — in-progress rows** (`status IN ('running','cancelling')`):
```python
for row in repo.list(status=("running","cancelling")):
    worker = repo.get_worker(row.id)                        # reads workers table (plan 04's migration 002)
    alive = bool(worker) and _pid_alive(worker.pid)
    fresh = bool(worker) and (now_utc() - worker.last_heartbeat_at) < timedelta(seconds=30)

    if alive and fresh:
        _adopt(row.id, worker.pid)                          # track in _workers, don't respawn
    elif alive and not fresh:
        # e.g. worker was SIGSTOPped (docker pause). Don't kill a paused job; log and adopt.
        logger.warning("adopting stale-but-alive worker pid=%d execution=%s (heartbeat age=%s)",
                       worker.pid, row.id, now_utc() - worker.last_heartbeat_at)
        _adopt(row.id, worker.pid)
    else:
        # Dead (or never started). Mark failed and run post-mortem.
        post_mortem(row.id)                                 # sets status=failed, runs compose down, marks post_mortem_complete
```

**Set B — post-mortem-incomplete terminal rows** (catches supervisor-killed-mid-cleanup):
```python
for row in repo.list_post_mortem_incomplete():
    post_mortem(row.id)                                     # idempotent re-sweep
```

`_pid_alive(pid)` is `os.kill(pid, 0)`; returns False on `ProcessLookupError`.

This preserves "runs survive the service restarting" for live workers (including SIGSTOPped ones), cleanly reconciles actually-dead ones, and recovers from a crashed post-mortem on the next boot.

---

## Tasks

### Task 1 — CREATE `src/core/persistence/migrations/002_workers.sql`
```sql
CREATE TABLE IF NOT EXISTS workers (
    execution_id      TEXT PRIMARY KEY REFERENCES executions(id) ON DELETE CASCADE,
    pid               INTEGER NOT NULL,
    started_at        TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    compose_projects  TEXT NOT NULL DEFAULT '[]'    -- JSON array; source of truth during the run
);
CREATE INDEX IF NOT EXISTS idx_workers_heartbeat ON workers(last_heartbeat_at);
```

**Note:** `executions.metadata_json.compose_projects[]` (plan 01's `executions` table) retains an archival copy. The `workers` row lives only while the execution is active and is deleted on reap; `metadata_json` outlives it and is what reconciliation reads for terminal rows.

**VALIDATE**: `sqlite3 ~/.sentinel/sentinel.db ".schema workers"` after `ensure_initialized()`.

### Task 2 — CREATE `src/utils/logging_config.py`
Extract logging setup from `cli.py`'s module-level `basicConfig` into a reusable `configure_logging(level: int = logging.INFO, *, enable_jsonl: bool = True) -> None`. Called by CLI, by FastAPI lifespan, and — critically — by the worker entrypoint *before* any other import.

**GOTCHA**: `spawn` re-imports; without this, workers run silently. A test must assert the spawned worker writes a line to the log file.

**VALIDATE**: `pytest tests/core/test_worker_logging.py`.

### Task 3 — CREATE `src/core/execution/worker.py`
Stand-alone entry point (`python -m src.core.execution.worker --execution-id X`).

```python
def main() -> int:
    # Logging FIRST — spawn re-imports, no inherited basicConfig.
    from src.utils.logging_config import configure_logging
    configure_logging()

    # Only now import orchestration + SDK.
    import argparse, signal, threading
    from src.core.persistence.db import connect, ensure_initialized
    from src.core.execution.repository import ExecutionRepository
    from src.core.execution.orchestrator import Orchestrator
    from src.core.events.bus import EventBus

    args = _parse_args()                        # --execution-id
    ensure_initialized()
    conn = connect()                            # worker-owned connection, separate from any parent's
    repo = ExecutionRepository(conn)
    bus = EventBus(conn)                        # explicit: orchestrator requires a bus per plan 01

    # Heartbeat thread (daemon; coordinates with _shutdown event)
    _shutdown = threading.Event()
    def _heartbeat_loop():
        hb_conn = connect()                     # own connection, must not share
        try:
            while not _shutdown.wait(5.0):
                try:
                    hb_conn.execute("BEGIN IMMEDIATE")
                    hb_conn.execute("UPDATE workers SET last_heartbeat_at=? WHERE execution_id=?",
                                    (_now_iso(), args.execution_id))
                    hb_conn.execute("COMMIT")
                except Exception:
                    logger.exception("heartbeat write failed")
        finally:
            hb_conn.close()
    threading.Thread(target=_heartbeat_loop, daemon=True, name="worker-heartbeat").start()

    # SIGTERM/SIGINT → cooperative cancel flag checked between agent turns
    _cancel = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: _cancel.set())

    try:
        orchestrator = Orchestrator(repo=repo, bus=bus, session_tracker=SessionTracker(),
                                    config=get_config(), cancel_flag=_cancel)
        execution = repo.get(args.execution_id)
        method = {"plan": orchestrator.plan, "execute": orchestrator.execute,
                  "debrief": orchestrator.debrief}[execution.kind]
        result = method(execution_id=args.execution_id, **execution.options)
        return 0 if result.status == ExecutionStatus.SUCCEEDED else 1
    finally:
        _shutdown.set()
        conn.close()
```

- **Cancel flag**: `threading.Event` checked by Orchestrator between agent turns; orchestrator observes and bails cleanly.
- **Exit code**: 0 on `ExecutionStatus.SUCCEEDED`, non-zero otherwise. Supervisor cross-checks against row status.

**GOTCHA**: Options are read from `executions.metadata_json`, never argv — keeps the endpoint body small and escape-free.

**GOTCHA**: `configure_logging()` must be the first call; the module-level `logger = logging.getLogger(__name__)` is fine (that's just getting a handle), but any `logger.info(...)` call before `configure_logging` runs into the default root handler and breaks the structured JSONL.

**GOTCHA**: `logging_config.py` must NOT emit log lines at module-import time — doing so forces a default handler to install before `configure_logging()` can set up the intended handlers.

**VALIDATE**: `python -m src.core.execution.worker --help` works; invoking on a seeded execution row runs it to completion and produces events in the DB.

### Task 4 — CREATE `src/core/execution/supervisor.py` + UPDATE repository.py

**Supervisor takes a `connection_factory`, not a shared connection**. Supervisor operations run concurrently from the HTTP threadpool AND the asyncio reaper task; a single shared sqlite3 connection would interleave statements mid-transaction under `check_same_thread=False`. Each supervisor method opens its own connection via the factory and closes it after.

```python
from typing import Callable
import multiprocessing, os, signal, threading

ConnectionFactory = Callable[[], sqlite3.Connection]

def _locked(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper

class Supervisor:
    def __init__(self, connection_factory: ConnectionFactory):
        self._ctx = multiprocessing.get_context("spawn")
        self._workers: dict[str, multiprocessing.Process] = {}
        self._lock = threading.Lock()
        self._connection_factory = connection_factory

    # Every method that touches _workers is @_locked.
    @_locked
    def spawn(self, execution_id: str) -> None:
        env = _build_worker_env()                             # allowlist, see Worker Process Model
        proc = self._ctx.Process(
            target=_worker_entry, args=(execution_id,), env=env, daemon=False,
        )
        proc.start()
        self._workers[execution_id] = proc
        # Register worker row
        with self._connection_factory() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR REPLACE INTO workers(execution_id, pid, started_at, last_heartbeat_at) "
                         "VALUES (?, ?, ?, ?)",
                         (execution_id, proc.pid, _now_iso(), _now_iso()))
            conn.execute("COMMIT")

    @_locked
    def cancel(self, execution_id: str) -> None: ...

    @_locked
    def reap(self) -> int:
        dead = [eid for eid, p in self._workers.items() if not p.is_alive()]
        for eid in dead:
            self._workers[eid].join(timeout=1)
            del self._workers[eid]
            with self._connection_factory() as conn:
                conn.execute("DELETE FROM workers WHERE execution_id=?", (eid,))
            self.post_mortem(eid)                             # ensures terminal event + compose cleanup
        return len(dead)

    @_locked
    def adopt_or_reconcile_on_startup(self) -> tuple[int, int]: ...

    def post_mortem(self, execution_id: str) -> None:
        # Not locked — reentrant; uses its own connection
        with self._connection_factory() as conn:
            ...                                                # see "Cancellation & cleanup" section above

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try: os.kill(pid, 0); return True
        except ProcessLookupError: return False
```

**`@_locked` applies to**: `spawn`, `cancel`, `reap`, `adopt_or_reconcile_on_startup`. `post_mortem` does NOT hold the lock — it's called from `reap` (already locked) and from reconciliation (locked) and opens its own connection, so reentrancy is intentional.

**Repository extension** (update `src/core/execution/repository.py` from plan 01):
Add three methods:
- `get_worker(execution_id) -> Optional[WorkerRow]` — SELECT from `workers` table; returns `WorkerRow(execution_id, pid, started_at, last_heartbeat_at, compose_projects)` TypedDict/dataclass, `last_heartbeat_at` parsed to tz-aware datetime.
- `list_post_mortem_incomplete() -> list[Execution]` — returns terminal rows missing the `post_mortem_complete` metadata flag.
- `mark_metadata(execution_id, **kv) -> None` — shallow-merges `kv` into `metadata_json`; atomic (`BEGIN IMMEDIATE` + `UPDATE` with `json_patch`).

**GOTCHA — periodic reap**: schedule via `asyncio.create_task(periodic_reap(...))` in the FastAPI lifespan (Task 6), interval 5s. `reap()` is sync; call from the event loop via `loop.run_in_executor(None, supervisor.reap)`.

**VALIDATE**: `pytest tests/core/test_supervisor.py`.

### Task 5 — CREATE `src/service/routes/commands.py`
Three endpoints, pydantic `StartExecutionBody` / `ExecutionOptions` with `extra="forbid"`.

```python
@router.post("/executions", status_code=202, response_model=ExecutionOut)
def start(
    body: StartExecutionBody,
    token: Annotated[str, Depends(require_token)],                        # plan 05 dep; gives us token value for scoping
    idempotency_key: Annotated[str | None, Header()] = None,
    repo: Annotated[ExecutionRepository, Depends(get_repo)],
    supervisor: Annotated[Supervisor, Depends(get_supervisor)],
):
    token_prefix = _sha256_prefix(token)                                  # same helper used for audit log
    if idempotency_key:
        existing = repo.find_by_idempotency(token_prefix, idempotency_key)
        if existing:
            return existing                                               # returns regardless of terminal status
    execution = repo.create(
        ticket_id=body.ticket_id, project=body.project, kind=body.kind,
        options=body.options.model_dump(),
        idempotency_token_prefix=token_prefix,
        idempotency_key=idempotency_key,
    )
    try:
        supervisor.spawn(execution.id)                                    # worker transitions row to 'running'
    except Exception as e:
        repo.record_ended(execution.id, ExecutionStatus.FAILED, error=f"spawn_failed: {e}")
        raise HTTPException(status_code=500, detail=f"spawn failed: {e}")
    return execution
```

`project` field charset is tight — compose-project-safe regex prevents shell/path/compose-name injection:
```python
class StartExecutionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: str = Field(pattern=r"^[A-Z][A-Z0-9]+-\d+$")
    project:   str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")         # docker compose project name rules
    kind: ExecutionKind
    options: ExecutionOptions = ExecutionOptions()
```

**Cancel**: 404 if not found; 409 if not in `running`/`queued`/`cancelling`; else `supervisor.cancel(id)`.

**Retry**: 404 if not found; 409 if original still running; else new row with `metadata_json.retry_of = original.id`, copy `ticket_id/project/kind/options`, `supervisor.spawn`.

**Idempotency semantics** (documented behavior):
- `(token_prefix, idempotency_key)` tuple is the dedup key — different tokens can reuse the same key value.
- Returning an existing row returns it regardless of terminal status; callers who want to re-run a failed execution must POST to `/executions/{id}/retry`, which explicitly creates a new execution.
- Missing `Idempotency-Key` header → always creates a new execution (today's default).

**VALIDATE**: `pytest tests/service/test_commands_routes.py`.

### Task 6 — UPDATE `src/service/deps.py` and add lifespan helper

```python
# deps.py
def get_supervisor(request: Request) -> Supervisor:
    return request.app.state.supervisor

@asynccontextmanager
async def command_center_lifespan(app: FastAPI):
    ensure_initialized()
    # Supervisor takes a connection FACTORY, not a connection — each operation opens its own.
    supervisor = Supervisor(connection_factory=connect)
    reaper_task: asyncio.Task | None = None

    try:
        adopted, reconciled = supervisor.adopt_or_reconcile_on_startup()
        logger.info("startup: adopted=%d reconciled=%d", adopted, reconciled)
        app.state.supervisor = supervisor
        reaper_task = asyncio.create_task(_periodic_reap(supervisor))
        app.state.reaper_task = reaper_task
    except Exception:
        # Startup failed AFTER Supervisor init but before yield.
        # Lifespan's @asynccontextmanager semantics do NOT call teardown on setup failure,
        # so we clean up explicitly here.
        if reaper_task is not None:
            reaper_task.cancel()
        supervisor.shutdown()
        raise

    try:
        yield
    finally:
        if reaper_task is not None:
            reaper_task.cancel()
        supervisor.shutdown()                     # cancels in-flight workers; closes nothing we own long-term
```

`Supervisor.shutdown()` is a new public method — takes the lock, iterates `_workers`, calls `cancel(eid)` for each. Replaces the earlier snippet that reached into `supervisor._workers` directly.

Plan 05's `create_app()` does `app = FastAPI(lifespan=command_center_lifespan)`. No `@app.on_event(...)` — deprecated.

**GOTCHA — uvicorn single-process**: `uvicorn.run(create_app(), host=..., port=...)` on an app *instance* (not factory string) keeps a single process, which is required because Supervisor state and SQLite writes are per-process. Document this as intentional in plan 02 and plan 05 (not a "we forgot to scale" bug).

**GOTCHA — startup exception handling**: `@asynccontextmanager` does NOT call teardown if setup raises *before* `yield`. The explicit try/except above is required — without it, a crash in `adopt_or_reconcile_on_startup` leaves the reaper task running and workers unreaped.

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
