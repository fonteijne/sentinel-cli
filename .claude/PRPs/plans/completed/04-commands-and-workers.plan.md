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

1. `ExecutionWorker` (subprocess entry point) — takes `--execution-id`, loads the row, constructs an `Orchestrator`, runs it, exits.
2. `Supervisor` (in-process object owned by the service) — `spawn(execution_id)`, `cancel(execution_id)`, `reap()`, `reconcile_on_startup()`. Tracks live workers by PID.
3. Three write endpoints on the existing FastAPI app.
4. Startup hook that marks any `status='running'` execution without a live worker as `failed` with `error='orphaned_on_restart'`.
5. CLI gains `execute --remote` flag that POSTs to the local service instead of running in-process.

## Metadata

| Field | Value |
|---|---|
| Type | NEW_CAPABILITY |
| Complexity | MEDIUM |
| Systems Affected | `src/core/execution/worker.py` + `supervisor.py` (new), `src/service/routes/commands.py` (new), `src/service/app.py`, `src/cli.py` |
| Dependencies | stdlib `multiprocessing` + `signal` (no new deps) |
| Estimated Tasks | 7 |
| Prerequisite | Plan 01 (Foundation). Independent of 02/03 at the module level but shares the FastAPI app, so 02 should land first. |

---

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/executions` | `{ticket_id, project, kind, options?}` | `202` + `Execution` row (status `queued` → `running`) |
| POST | `/executions/{id}/cancel` | — | `202` + `Execution` (status `cancelling`) |
| POST | `/executions/{id}/retry` | — | `202` + new `Execution` linking to original via `metadata_json.retry_of` |

All are async: the endpoint returns immediately after queueing; real progress comes via plan 03's stream or plan 02's GET.

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
| `src/core/execution/worker.py` | CREATE — subprocess entry point |
| `src/core/execution/supervisor.py` | CREATE — `Supervisor` class |
| `src/service/routes/commands.py` | CREATE — three endpoints |
| `src/service/app.py` | UPDATE — init Supervisor on startup, reconcile, include router |
| `src/service/deps.py` | UPDATE — `get_supervisor()` dependency |
| `src/cli.py` | UPDATE — `execute --remote` flag posts to local service |
| `tests/core/test_supervisor.py` | CREATE |
| `tests/service/test_commands_routes.py` | CREATE |

---

## Worker Process Model

- Start method: `multiprocessing.get_context("spawn")` to avoid inheriting uvicorn's open fds / FastAPI state.
- Entry: `python -m src.core.execution.worker --execution-id <id>` (also allow in-process spawn for tests via the context API).
- Env: inherits `SENTINEL_*`, `DOCKER_HOST`, `PATH`, etc. — important for DooD to keep working.
- cwd: repository root (`/app` in sentinel-dev) — the orchestrator itself sets worktree cwd via its existing logic.
- Exit code: 0 on `ExecutionStatus.SUCCEEDED`, non-zero otherwise. Supervisor reads row status; exit code is a sanity check.
- Logs: inherit stdout/stderr from supervisor; get captured into `logs/workers/<execution_id>.log` if configured.

## Cancellation

- `Supervisor.cancel(execution_id)` → `os.kill(pid, signal.SIGTERM)`.
- Worker installs a SIGTERM handler that:
  1. Sets an `execution.cancelling` flag (published as event + row update).
  2. Lets the current agent request finish (best-effort; agent SDK call is synchronous).
  3. Updates row to `status='cancelled'` and exits cleanly.
- If the worker doesn't exit within 30s after SIGTERM, supervisor sends SIGKILL and marks the row `cancelled` with `error='terminated_after_timeout'`.

**GOTCHA**: The current claude-agent-sdk call is synchronous and may not be interruptible mid-turn. Cancellation is therefore best-effort between agent turns — document this. Do not attempt to interrupt mid-turn.

## Startup Reconciliation

On service boot (`@app.on_event("startup")`):

```sql
UPDATE executions
SET status = 'failed', ended_at = :now, error = 'orphaned_on_restart'
WHERE status IN ('running','cancelling') AND id NOT IN (:live_pids);
```

Since PID tracking is process-local, *all* in-progress rows at startup are considered orphaned — there is no persisted worker registry. Plan documents this. (A later plan could add a `workers` table if we ever run the service in an HA pair.)

---

## Tasks

### Task 1 — CREATE `src/core/execution/worker.py`
Stand-alone entry point (`python -m src.core.execution.worker --execution-id X`). Loads config, opens DB, instantiates `Orchestrator`, calls the kind-specific method (read `kind` off the row), exits with appropriate code. Registers SIGTERM handler.

**GOTCHA**: Worker must re-read `Execution.options` from `metadata_json` rather than accepting them via argv — keeps the endpoint body small, avoids escaping games.

**VALIDATE**: `python -m src.core.execution.worker --help` works; invoking on a seeded execution row runs it to completion.

### Task 2 — CREATE `src/core/execution/supervisor.py`
```python
class Supervisor:
    def __init__(self, ctx=None):
        self._ctx = ctx or multiprocessing.get_context("spawn")
        self._workers: dict[str, multiprocessing.Process] = {}
        self._lock = threading.Lock()

    def spawn(self, execution_id: str) -> None: ...
    def cancel(self, execution_id: str) -> None: ...
    def reap(self) -> None: ...            # remove finished entries; called periodically + on request
    def reconcile_on_startup(self, repo: ExecutionRepository) -> int: ...  # returns # orphaned
```

**GOTCHA**: `reap()` needs to run periodically — schedule via `asyncio.create_task(periodic_reap(...))` on app startup, interval 5s.

**VALIDATE**: `pytest tests/core/test_supervisor.py`.

### Task 3 — CREATE `src/service/routes/commands.py`
Three endpoints. Bodies validated with pydantic schemas. Each:
- `POST /executions` → `repo.create(..., status=queued)` → `supervisor.spawn(id)` → `repo.set_status(running)` (or let worker do that — prefer worker to avoid a race where spawn fails after row is `running`).
- `POST /executions/{id}/cancel` → 404 if not found; 409 if not in a cancellable status; else `supervisor.cancel(id)`.
- `POST /executions/{id}/retry` → 404 if not found; 409 if original still running; else create new row with `metadata_json.retry_of = original.id`, copy `ticket_id/project/kind/options`, spawn.

**VALIDATE**: `pytest tests/service/test_commands_routes.py`.

### Task 4 — UPDATE `src/service/app.py`
```python
@app.on_event("startup")
async def _startup():
    app.state.supervisor = Supervisor()
    n = app.state.supervisor.reconcile_on_startup(get_repo_from_state(app))
    logger.info("reconciled %d orphaned executions", n)
    app.state.reaper_task = asyncio.create_task(_reaper(app.state.supervisor))

@app.on_event("shutdown")
async def _shutdown():
    app.state.reaper_task.cancel()
    for eid in list(app.state.supervisor._workers):
        app.state.supervisor.cancel(eid)
```

**GOTCHA**: Shutdown is best-effort — a cold kill (SIGKILL from the container) leaves `status='running'`. The next startup's reconciliation will clean up.

**VALIDATE**: `create_app()` startup/shutdown runs without error in tests.

### Task 5 — UPDATE `src/service/deps.py`
Add `get_supervisor(request: Request) -> Supervisor` that returns `request.app.state.supervisor`.

### Task 6 — UPDATE `src/cli.py`
Add `--remote` flag on `execute` (and optionally `plan`, `debrief`). When set, HTTP POST to `http://127.0.0.1:${SENTINEL_SERVICE_PORT:-8787}/executions` with the options body; print the returned execution id; optionally tail the stream (plan 03) if stdout is a TTY and `--follow` flag is set.

**GOTCHA**: When the service isn't running, `--remote` should fail fast with a clear message, not fall back to in-process — ambiguity would mask real failures.

**VALIDATE**: `sentinel execute --remote TICKET` returns an id; the run proceeds in a subprocess owned by the service; the CLI can exit without killing the run.

### Task 7 — tests

- `tests/core/test_supervisor.py` — spawn a no-op worker (a trivial `python -c "import sys; sys.exit(0)"` subprocess swapped in via dependency injection); assert tracking; cancel via SIGTERM; reap removes from dict.
- `tests/service/test_commands_routes.py` — POST start → row created, worker spawned (mock Supervisor); cancel happy + 409 paths; retry creates new row with `retry_of` set.

**VALIDATE**: `pytest tests/core tests/service -v`.

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
- [ ] `POST /executions/{id}/cancel` transitions row to `cancelled` within 30s (or kills + marks after)
- [ ] `POST /executions/{id}/retry` creates a new linked row
- [ ] Service restart reconciles orphaned rows
- [ ] DooD still works from inside the worker (container ops in `sentinel execute` still succeed)
- [ ] Foundation + plans 02/03 tests still green

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `spawn` context can't pickle config/orchestrator | HIGH | HIGH | Worker is a module entry point with argv; nothing is pickled — the child reconstructs state from the DB + env |
| Subprocess inherits file descriptors it shouldn't (DB connection, sockets) | MED | MED | Use `spawn`, not `fork`. Verify with `lsof` during a test run |
| Cancel takes too long because agent SDK call is uninterruptible | MED | MED | 30s grace window, then SIGKILL; document best-effort semantics |
| Orphan-on-restart marks *correctly-running* workers as failed during zero-downtime deploys | LOW | HIGH | Out of scope — we have no zero-downtime deploy story; single-instance assumption documented |
| Workers pile up under `/app/logs/workers/` | LOW | LOW | Log rotation is an ops concern; not solved here |

## Notes

- Branch: `experimental/command-center-04-commands-workers`.
- Why not asyncio tasks instead of subprocesses? A raise/crash inside an asyncio task can take down the service or leak handles. Subprocesses give real isolation at the OS level, and `spawn` sidesteps uvicorn/fork hazards. Also: a subprocess is what the existing CLI flow effectively is today — minimal conceptual distance.
