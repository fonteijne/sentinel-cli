# Command Center — Gap Closure (BLOCKERs + HIGH)

**Source:** `.claude/PRPs/plans/command-center/GAP_ANALYSIS.md` (2026-04-24)
**Scope:** G-00 through G-09 — the 3 BLOCKERs and 7 HIGH gaps.
**Out of scope (this plan):** G-10..G-26 (MEDIUM/LOW) and R-1..R-19 (residuals).
**Branch:** `experimental/command-center-07-gap-closure` (per session rule; use the `cc-branch` skill).

---

## Summary

Plans 01–06 shipped a Command Center backend whose HTTP surface looks alive but whose middle is hollow: `POST /executions` returns 202 for a run that does no agent work, `register_compose_project` has zero callers so compose cleanup sees an empty list, and `executions.status` is stamped `running` at insertion time so the documented queued→running handshake is dead. This plan closes those three BLOCKERs, lands the orchestrator extraction they all hinge on, and brings seven HIGH gaps along with it (cancel_flag actually checked, six orphaned event types emitted, SDK-wrapper rate-limit emission wired, entry_dict parity test written, `set_worker_heartbeat` called instead of raw SQL, supervisor env race closed, WS connection cap enforced).

## User Story

As a **Sentinel operator driving runs through the Command Center**,
I want **the HTTP command path to actually run the agent flow, clean up its containers, emit its lifecycle events, and honour its documented status transitions**,
so that **a dashboard built on plans 01–06 shows truthful state and operators don't leak containers on cancel/failure**.

## Problem Statement

From `GAP_ANALYSIS.md`:

- **G-00** — Orchestrator has no `plan()` / `execute()` / `debrief()` methods; `worker.py` falls through to `_scaffold_run()` which emits only lifecycle events. POST `/executions` returns 202 for a "succeeded" execution that did zero agent work.
- **G-01** — `register_compose_project` is defined but never called, so `post_mortem`'s read-side always sees an empty list; the plan-04 acceptance criterion "per-ticket appserver containers cleaned up on cancel/failure/orphan" is unmet in practice.
- **G-02** — `repo.create()` hard-codes `status=RUNNING`; the worker's queued→running transition is dead code; Set-C startup reconciliation (orphaned queued rows) cannot populate.
- **G-03..G-09** — seven HIGH-severity documented-acceptance-criteria misses that all converge around the orchestrator and the SDK wrapper.

All ten are verified by code read (or cc-plan-reviewer audit) against plans 01, 03, 04, and 05.

## Solution Statement

Land the orchestrator extraction as the keystone, piggyback on it to close G-01/G-03/G-04, then sweep four independent fixes (G-02 status default, G-05 rate-limit emission, G-07 parity test, G-08/G-09 supervisor hygiene) and finally close G-06 with a per-token WS connection semaphore.

Five phases, each independently mergeable and validated at its own gate. Phases 1 and 2 must land in order (phase 2 reshapes the reconciliation tests phase 1 depends on). Phases 3, 4, 5 can run in parallel worktrees once phase 2 is merged.

---

## Metadata

| Field             | Value                                                                                                                               |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Type              | ENHANCEMENT (shipping specified-but-unshipped behaviour)                                                                            |
| Complexity        | HIGH — orchestrator extraction touches `cli.py`, `base_agent.py`, `agent_sdk_wrapper.py`, `orchestrator.py`, `worker.py`            |
| Systems Affected  | `src/core/execution/*`, `src/core/events/*`, `src/agents/base_agent.py`, `src/agent_sdk_wrapper.py`, `src/cli.py`, `src/service/*`  |
| Dependencies      | None added. Uses existing `claude-agent-sdk`, `fastapi ≥ 0.110`, `websockets`, `pydantic v2`, `pytest`.                             |
| Estimated Tasks   | 24 atomic tasks across 5 phases                                                                                                     |
| Plans referenced  | 01 (foundation), 03 (stream), 04 (commands/workers), 05 (auth/binding)                                                              |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                              BEFORE STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  HTTP CLIENT              FASTAPI SERVICE            WORKER SUBPROCESS        ║
║  ─────────                ────────────────           ─────────────────        ║
║                                                                               ║
║  POST /executions ──────► commands.start                                      ║
║                            repo.create(status=RUNNING) ◄─── G-02 hard-coded   ║
║                            supervisor.spawn ─────────► worker._worker_main    ║
║                             (202 ExecutionOut                                 ║
║                              status=RUNNING)                                  ║
║                                                         _resolve_method(kind) ║
║                                                                    │          ║
║                                                                    ▼          ║
║                                                          mapping = {PLAN,     ║
║                                                            EXECUTE, DEBRIEF}  ║
║                                                          getattr(orc,"plan")  ║
║                                                             returns None      ║
║                                                                    │          ║
║                                                                    ▼          ║
║                                                          _scaffold_run()  ◄── G-00
║                                                           orc.complete()      ║
║                                                            (no agents ran)    ║
║                                                                               ║
║  WS /stream ────────────► stream.stream                                       ║
║   ?token=...              (no cap per token)    ◄─── G-06 threadpool drain    ║
║                                                                               ║
║  [cancel / failure path]                                                      ║
║                          supervisor.post_mortem                               ║
║                           reads metadata["compose_projects"] = [] ◄── G-01    ║
║                           no `docker compose down` runs                       ║
║                           containers leak                                     ║
║                                                                               ║
║  PAIN_POINT:                                                                  ║
║   - Dashboard sees "succeeded" runs that did zero work                        ║
║   - `cancel_flag` set on orchestrator but never checked (G-03)                ║
║   - Six declared event types never emitted (G-04)                             ║
║   - Anthropic throttling never surfaced to dashboards (G-05)                  ║
║   - Orphaned queued rows unrecoverable (G-02 side effect)                     ║
║   - Containers leak on cancel/failure (G-01)                                  ║
║   - WS connection pool exhausted by misbehaving clients (G-06)                ║
║                                                                               ║
║  DATA_FLOW:                                                                   ║
║   POST /executions → row RUNNING → spawn → scaffold → events: started,        ║
║     completed (3s). No agent work. No compose projects. No cost.              ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                               AFTER STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  HTTP CLIENT              FASTAPI SERVICE            WORKER SUBPROCESS        ║
║  ─────────                ────────────────           ─────────────────        ║
║                                                                               ║
║  POST /executions ──────► commands.start                                      ║
║                            repo.create(status=QUEUED)  ◄─── G-02 fixed        ║
║                            supervisor.spawn ─────────► worker._worker_main    ║
║                             (202 ExecutionOut                                 ║
║                              status=QUEUED)            set_status(RUNNING)    ║
║                                                         _resolve_method(kind) ║
║                                                                    │          ║
║                                                                    ▼          ║
║                                                          orchestrator.plan /  ║
║                                                                   .execute /  ║
║                                                                   .debrief    ║
║                                                                    │          ║
║                                                                    ▼          ║
║                                                          ┌─ set_phase()       ║
║                                                          │   cancel_flag chk ─┼─ G-03
║                                                          ├─ BaseAgent emits   ║
║                                                          │   AgentStarted    ─┼─ G-04
║                                                          │   (then runs)     │║
║                                                          │   AgentFinished   │║
║                                                          ├─ register_compose ─┼─ G-01
║                                                          │   _project()      │║
║                                                          │   → compose up     ║
║                                                          ├─ SDK wrapper       ║
║                                                          │   ToolCalled       ║
║                                                          │   CostAccrued      ║
║                                                          │   RateLimited     ─┼─ G-05
║                                                          ├─ DebriefTurn      ─┼─ G-04
║                                                          │   RevisionRequested║
║                                                          │   TestResultRec   │║
║                                                          │   FindingPosted   │║
║                                                          └─ orchestrator.    │ ║
║                                                             complete/fail    │ ║
║                                                                               ║
║  WS /stream ────────────► require_token_ws                                    ║
║   ?token=...              (per-token semaphore, cap=10) ◄─── G-06 fixed       ║
║                           stream.stream                                       ║
║                                                                               ║
║  [cancel / failure path]                                                      ║
║                          supervisor.post_mortem                               ║
║                           reads metadata["compose_projects"] = [ticket-xyz]   ║
║                           `docker compose -p ticket-xyz down -v` runs   ◄─ G-01
║                           containers cleaned                                  ║
║                                                                               ║
║  VALUE_ADD:                                                                   ║
║   - Dashboard sees real agent timeline (AgentStarted/Finished per agent)      ║
║   - Operators cancel mid-flight with cooperative exit at phase boundaries     ║
║   - Anthropic throttling surfaces as observable events                        ║
║   - Compose containers cleaned on every cancel/failure                        ║
║   - Orphaned queued rows recoverable on service restart                       ║
║   - WS can no longer exhaust threadpool (cap=10 per token)                    ║
║                                                                               ║
║  DATA_FLOW:                                                                   ║
║   POST /executions → row QUEUED → spawn → set_status(RUNNING) → plan/exec/    ║
║     debrief → emits: started, phase.changed (×N), agent.started,              ║
║     agent.message_sent, tool.called (×M), cost.accrued, agent.response,       ║
║     agent.finished, [debrief.turn | revision.requested | test.result |        ║
║     finding.posted], [rate_limited on 429/529], completed. Compose            ║
║     projects registered → cleaned on post_mortem.                             ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                              | Before                                               | After                                               | User Impact                                      |
| ------------------------------------- | ---------------------------------------------------- | --------------------------------------------------- | ------------------------------------------------ |
| `POST /executions` response           | `status=running` immediately                         | `status=queued`; worker transitions to `running`    | Dashboards distinguish "queued" from "running"   |
| Event stream for a remote run         | 3 events (started, phase, completed)                 | 15–50+ events incl. tool.called, cost.accrued, agent.* | Timeline is useful                            |
| Cancel of a remote run                | SIGTERM→SIGINT→SIGKILL, containers leak              | Cooperative exit at phase boundary + compose down   | Clean cancel; no orphaned containers             |
| WS `/executions/{id}/stream`          | Unlimited connections per token                      | HTTP 403 close-code 1008 after 10 concurrent        | Misbehaving client can't brown out service       |
| Post-mortem behaviour                 | `compose_projects: []` always                        | Populated list → `docker compose down` per project  | Ops no longer sees leftover `appserver-*` stacks |
| Throttled runs                        | Silent retries inside SDK                            | `rate_limited` event visible on stream              | Dashboard renders "Anthropic is throttling"      |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task.**

| Priority | File                                        | Lines       | Why Read This                                                                                       |
| -------- | ------------------------------------------- | ----------- | --------------------------------------------------------------------------------------------------- |
| P0       | `src/core/execution/orchestrator.py`        | 1–215       | Current scope (`begin`/`complete`/`fail`/`set_phase`/`record_agent_result`) — we're extending it    |
| P0       | `src/core/execution/worker.py`              | 75–218      | Heartbeat thread, `_resolve_method`, `_scaffold_run` (what we're replacing)                         |
| P0       | `src/cli.py`                                | 297–1339    | The three Click commands (`plan`, `debrief`, `execute`) whose agent loops we extract                |
| P0       | `src/core/events/types.py`                  | all         | Event catalogue — we emit 6 orphaned types and respect `TERMINAL_EVENT_TYPES`                       |
| P0       | `src/agents/base_agent.py`                  | 21–320      | `attach_events`, `_emit_*`, `_send_message_async` — where `AgentStarted/Finished` bookend           |
| P0       | `src/agent_sdk_wrapper.py`                  | 200–522     | `_publish_*` helpers + the stream loop where we add 429/529 handling (G-05)                         |
| P0       | `src/core/execution/repository.py`          | 89–140, 150–164, 266–290, 390–400, 416–451 | `create`, `find_by_idempotency`, `mark_metadata`, `set_worker_heartbeat`, `register_compose_project` |
| P0       | `src/core/execution/supervisor.py`          | 119–199, 344–448 | `spawn`, `cancel`, `post_mortem` — env race lives in `spawn`                                    |
| P0       | `src/service/routes/stream.py`              | all         | WS endpoint where per-token semaphore goes                                                          |
| P0       | `src/service/auth.py`                       | 169–222, 225–256 | `_extract_bearer`, `require_token_ws`, `require_token_and_write_slot` (pattern for the semaphore)  |
| P1       | `src/service/app.py`                        | 75–170      | Composition pattern; WS router includes `require_token_ws` only                                     |
| P1       | `tests/core/test_orchestrator.py`           | all         | Test pattern to mirror for new `plan/execute/debrief` tests                                         |
| P1       | `tests/core/test_supervisor.py`             | 150–220     | Env-race test vectors; the `# Coerce status to QUEUED` comment at line 172 goes away after G-02     |
| P1       | `tests/service/test_stream.py`              | 70–110      | WS test pattern to mirror for the G-06 cap                                                          |
| P2       | `tests/test_agent_sdk_wrapper.py`           | all         | Wrapper test conventions for G-05 and G-07                                                          |

### External Documentation

| Source                                                                                                                                   | Section                                         | Why Needed                                                                                   |
| ---------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- | -------------------------------------------------------------------------------------------- |
| [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/#using-depends-and-others)                                          | "Using Depends and others"                      | Router-level `dependencies=[Depends(...)]` on `@router.websocket` (FastAPI ≥ 0.110)          |
| [Claude Agent SDK — errors](https://docs.claude.com/en/api/messages-streaming#error-events)                                              | "Error events" / overload                       | 429 (rate_limit_error) vs 529 (overloaded_error) payload shapes for G-05                     |
| [multiprocessing.Process](https://docs.python.org/3.11/library/multiprocessing.html#multiprocessing.Process)                             | Constructor signature                           | Confirm no `env=` kwarg (G-09 rationale); we fix with a narrow lock, not a Popen rewrite     |
| [Python `asyncio.Semaphore`](https://docs.python.org/3.11/library/asyncio-sync.html#asyncio.Semaphore)                                   | Acquire/release semantics                       | Semaphore choice for G-06 per-token cap                                                      |

**GOTCHA roll-up** (from `cc-gotcha-check` over plans 01/03/04/05):

- Never use naive `utcnow()`; use `datetime.now(timezone.utc)` (all 5 plans).
- Every publish goes through `EventBus.publish` which **persists before dispatch**; never write directly to `events`.
- Event-type wire strings are immutable once real data lands — do NOT rename `rate_limited` to `rate.limited` in this plan (G-19 is noted but out of scope).
- `BEGIN IMMEDIATE` for every writer; `COMMIT`/`ROLLBACK` discipline.
- `connect()` factory per caller — never share a sqlite3 connection across threads.
- `spawn` (not `fork`) for `multiprocessing` — `configure_logging()` must be re-called inside the child.
- WebSocket query-param `?token=` is **loopback-only** (`src/service/auth.py:_LOOPBACK_HOSTS`). G-06 must not relax that.
- `extra="forbid"` on all write schemas (plan 04 §Request schemas) — do not widen.

---

## Patterns to Mirror

### NAMING_CONVENTION (method + module)

```python
# SOURCE: src/core/execution/orchestrator.py:86-117
# COPY THIS PATTERN:
def begin(
    self,
    ticket_id: str,
    project: str,
    kind: ExecutionKind,
    options: Optional[Dict[str, Any]] = None,
) -> Execution:
    execution = self.repo.create(
        ticket_id=ticket_id, project=project, kind=kind, options=options
    )
    self.bus.publish(
        ExecutionStarted(
            execution_id=execution.id,
            kind=kind.value,
            ticket_id=ticket_id,
            project=project,
        )
    )
    return execution
```

### EVENT_EMISSION_PATTERN (new event types)

```python
# SOURCE: src/agents/base_agent.py:281-313
# COPY THIS PATTERN — every new emission is a private method that guards on bus/execution_id:
def _emit_message_sent(
    self, *, prompt_chars: int, cwd: Optional[str], max_turns: Optional[int]
) -> None:
    if self.event_bus is None or self.execution_id is None:
        return
    from src.core.events import AgentMessageSent
    try:
        self.event_bus.publish(
            AgentMessageSent(
                execution_id=self.execution_id,
                agent=self.agent_name,
                prompt_chars=prompt_chars,
                cwd=cwd,
                max_turns=max_turns,
            )
        )
    except Exception:
        logger.exception("failed to publish AgentMessageSent event")
```

### ERROR_HANDLING (supervisor-internal)

```python
# SOURCE: src/core/execution/supervisor.py:167-199
# COPY THIS PATTERN — try with terminal-event publish, then escalate:
self._signal(pid, signal.SIGTERM)
if self._wait_exit(execution_id, CANCEL_GRACE_SIGTERM_S):
    return
logger.warning("cancel: SIGTERM timed out pid=%d, sending SIGINT", pid)
self._signal(pid, signal.SIGINT)
```

### REPOSITORY_TXN_PATTERN (BEGIN IMMEDIATE)

```python
# SOURCE: src/core/execution/repository.py:390-400
# COPY THIS PATTERN for every new write:
self._conn.execute("BEGIN IMMEDIATE")
try:
    self._conn.execute(
        "UPDATE workers SET last_heartbeat_at = ? WHERE execution_id = ?",
        (_now_iso(), execution_id),
    )
    self._conn.execute("COMMIT")
except Exception:
    self._conn.execute("ROLLBACK")
    raise
```

### FASTAPI_WS_DEP_PATTERN (the G-06 semaphore)

```python
# SOURCE: src/service/auth.py:225-256 (generator-dep for write slot)
# MIRROR at WebSocket granularity — but use a Semaphore per token, not a counter+window.
# The existing rate_limit.py TokenRateLimiter is HTTP-shaped (check_and_reserve + release on
# request completion). WS connections are long-lived, so we add a dedicated per-token
# asyncio.Semaphore map. See Task 5.1 for full signature.
```

### PYTEST_FIXTURE_PATTERN (per-test DB)

```python
# SOURCE: tests/core/test_orchestrator.py:14-22
# COPY THIS PATTERN for every new test file:
@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
    ensure_initialized()
    conn = connect()
    yield conn
    conn.close()
```

### WS_TEST_PATTERN

```python
# SOURCE: tests/service/test_stream.py:71-108
# COPY THIS PATTERN for the G-06 cap test:
with authed_client.websocket_connect(f"/executions/{execution_id}/stream") as ws:
    for _ in range(4):
        frames.append(ws.receive_json())
```

---

## Files to Change

| File                                                            | Action | Phase | Justification                                                                                    |
| --------------------------------------------------------------- | ------ | ----- | ------------------------------------------------------------------------------------------------ |
| `src/core/execution/orchestrator.py`                            | UPDATE | 1     | Add `plan()`, `execute()`, `debrief()` methods; wire `cancel_flag.is_set()` at phase boundaries  |
| `src/core/execution/worker.py`                                  | UPDATE | 1,2   | Remove scaffold fallback path; land queued→running transition; use `repo.set_worker_heartbeat`    |
| `src/agents/base_agent.py`                                      | UPDATE | 1     | Emit `AgentStarted`/`AgentFinished` in `_send_message_async` bookend                              |
| `src/agent_sdk_wrapper.py`                                      | UPDATE | 3     | Wire `_publish_rate_limited` on 429/529 in the stream loop; write parity test in same phase      |
| `src/core/execution/repository.py`                              | UPDATE | 2     | Default `status=QUEUED` in `create()`; remove hard-coded `RUNNING`                                |
| `src/cli.py`                                                    | UPDATE | 1     | Plan/execute/debrief Click bodies become thin Orchestrator callers                                |
| `src/core/execution/supervisor.py`                              | UPDATE | 4     | Serialize `spawn` env swap under the existing `_lock`; assertion/log when post_mortem sees empty  |
| `src/service/routes/stream.py`                                  | UPDATE | 5     | Per-token semaphore (cap=10) before the WS accept                                                 |
| `src/service/rate_limit.py`                                     | UPDATE | 5     | Add `WsConnectionLimiter` sibling class (or extend `TokenRateLimiter`) — see task 5.1            |
| `src/service/app.py`                                            | UPDATE | 5     | Instantiate `WsConnectionLimiter` on `app.state`; config key `service.rate_limits.ws_concurrent_per_token` |
| `src/service/auth.py`                                           | UPDATE | 5     | `require_token_ws` returns `(token, token_prefix)` so the semaphore can key on it                 |
| `tests/core/test_orchestrator.py`                               | UPDATE | 1     | Add tests for `plan`, `execute`, `debrief` happy-path + cancel-flag exit                          |
| `tests/core/test_supervisor.py`                                 | UPDATE | 2,4   | Remove "Coerce status to QUEUED" comment; add env-race test                                        |
| `tests/core/test_worker.py`                                     | UPDATE | 2     | Queued→running transition is now live (delete `_scaffold_run` tests)                              |
| `tests/core/test_event_bus.py`                                  | UPDATE | 1     | Coverage for the 6 newly-emitted event types                                                      |
| `tests/test_agent_sdk_wrapper.py`                               | UPDATE | 3     | **`test_entry_dict_jsonl_bus_parity`** (G-07, named by plan 01 Task 9) + 429/529 emission test    |
| `tests/service/test_stream.py`                                  | UPDATE | 5     | `test_ws_connection_cap_per_token` — 11th connection rejected with close-code 1008 or 429        |
| `tests/integration/test_end_to_end.py`                          | UPDATE | 1     | End-to-end: POST /executions with a mock agent → observe full event timeline                      |

**Files NOT to change:**
- `src/core/persistence/migrations/001_init.sql` (schema stays identical)
- `src/core/persistence/migrations/002_workers.sql`
- `src/core/events/types.py` — the 6 orphaned types already exist; we only start emitting.
  Do NOT rename `rate_limited` in this plan (that's G-19, out of scope; forward-only migration applies.)
- `docker-compose.yml` — no deploy changes.

---

## NOT Building (Scope Limits)

- **G-10..G-17 (MEDIUM), G-18..G-26 (LOW), R-1..R-19 (residuals).** Tracked, not shipped here.
- **G-11 read-endpoint rate limit.** Adjacent to G-06 but requires a different dep shape on read routers; filed for a follow-up.
- **G-13/G-21 idempotency-without-auth.** Same router; keeping the dep tree undisturbed in this plan so G-06's semaphore is the only router-level change.
- **Renaming `rate_limited` → `rate.limited` (G-19).** Wire strings are immutable after real data; we only emit the existing string.
- **Dashboard/UI.** Explicitly out of scope across all command-center plans.
- **Replacing SessionTracker with DB storage (R-16).** Orphaned event types get emitted; session lifecycle stays where it is.
- **Multiprocessing Popen rewrite (alternative to G-09).** We serialize env under the existing lock; a full Popen path is a larger refactor filed separately.
- **Worker-log HTTP endpoint.** Still follow-up per plan 06.

---

## Step-by-Step Tasks

Execute phases in order. Tasks within a phase can parallelize **only if** noted. Each phase ends at a validation gate.

---

### PHASE 1 — Orchestrator extraction (closes G-00, G-01, G-03, partial G-04)

Keystone phase. Per the gap analysis: *"fixing G-00 likely pulls G-01 and G-03 along with it (the orchestrator extraction is the natural place to register compose projects and to check `cancel_flag` between phases)."*

#### Task 1.1: UPDATE `src/core/execution/orchestrator.py` — add `set_phase` cancel check

- **ACTION**: Modify `set_phase` to raise `ExecutionCancelled` when `cancel_flag.is_set()`.
- **IMPLEMENT**: Before the existing `self.repo.set_phase(...)` and publish, if `self.cancel_flag is not None and self.cancel_flag.is_set():` raise a new `OrchestratorCancelled` internal exception (defined at module top). Do not publish `PhaseChanged` in the cancel path — `post_mortem` owns terminal events.
- **MIRROR**: `src/core/execution/orchestrator.py:115-117` (existing `set_phase`)
- **GOTCHA**: `cancel_flag` is `threading.Event` here, not `asyncio.Event`. `is_set()` is side-effect-free.
- **VALIDATE**: `pytest tests/core/test_orchestrator.py::test_set_phase_raises_when_cancelled`

#### Task 1.2: UPDATE `src/core/execution/orchestrator.py` — add `plan(execution_id, **options)`

- **ACTION**: Add a method that loads the row, does worktree setup (delegating to existing CLI helpers — see Task 1.6), emits phase changes, runs `PlanGeneratorAgent`, records the agent result, and returns an object with `.status`.
- **IMPLEMENT**:
  - Signature: `def plan(self, execution_id: str, **options: Any) -> PlanResult` where `PlanResult` is a small dataclass with `status: ExecutionStatus` and `details: dict`.
  - Call `set_phase(execution_id, "worktree")` then `set_phase(execution_id, "planning")`.
  - Instantiate `PlanGeneratorAgent` and call `agent.attach_events(self.bus, execution_id)` then `agent.run(...)`.
  - Call `self.record_agent_result(execution_id, agent.agent_name, result)`.
  - On exception, call `self.fail(execution_id, error=str(exc))` and re-raise.
- **MIRROR**: `src/cli.py:329-412` — the current `plan` Click body is the reference flow.
- **GOTCHA**: The CLI currently does `orchestrator.run(...)` as a context manager which calls `begin`. The new `plan` method must be called **after** the worker has already transitioned the row to `RUNNING` (Task 2.1). Do NOT call `begin` inside `plan`.
- **GOTCHA**: `AgentStarted`/`AgentFinished` are emitted by `BaseAgent` (Task 1.5), not here.
- **VALIDATE**: `pytest tests/core/test_orchestrator.py::test_plan_happy_path_records_result_and_completes`

#### Task 1.3: UPDATE `src/core/execution/orchestrator.py` — add `debrief(execution_id, **options)`

- **ACTION**: Analogous to `plan`, but runs `FunctionalDebriefAgent` and emits `DebriefTurn` events per iteration and `RevisionRequested` when a revision loop is requested.
- **IMPLEMENT**:
  - Phase: `"worktree"` → `"debriefing"`.
  - For each turn in the debrief loop: emit `DebriefTurn(turn_index=i, prompt_chars=..., response_chars=...)` **from inside the Orchestrator**, not from the agent (the agent returns; the orchestrator sees the request/response sizes).
  - If a revision is requested (result["revise"]==True), emit `RevisionRequested(revise_of_execution_id=execution_id, reason=result.get("reason"))` and decide whether to schedule a new execution (out of scope — just emit the event).
- **MIRROR**: `src/cli.py:427-531` (existing debrief Click body)
- **GOTCHA**: Debrief today is single-turn via `FunctionalDebriefAgent.run`. Emit `DebriefTurn(turn_index=1, ...)` once and document that multi-turn debrief is deferred.
- **VALIDATE**: `pytest tests/core/test_orchestrator.py::test_debrief_emits_debrief_turn_event`

#### Task 1.4: UPDATE `src/core/execution/orchestrator.py` — add `execute(execution_id, **options)` with compose registration

- **ACTION**: Run developer → security → optional Drupal reviewer loop, register the compose project once per run, emit orphaned event types in context.
- **IMPLEMENT**:
  - Phase sequence: `"worktree"` → `"setup_compose"` → `"iteration_1"` → ... → terminal.
  - Before the first `docker compose up` (or equivalent), call `self.repo.register_compose_project(execution_id, compose_project_name)` **in the same DB connection owned by the orchestrator**. The project name is derived from ticket_id (match existing CLI default: `ticket_id.split("-")[0].lower()`).
  - Per iteration: call `set_phase(execution_id, f"iteration_{i}")` (which now does the cancel check per Task 1.1).
  - Emit `TestResultRecorded(success=bool, return_code=int)` when security-reviewer's test invocation returns. Emit `FindingPosted(severity=..., summary=...)` when Drupal reviewer posts non-empty findings.
  - Note: the compose project name is a STRING, not an ID. Per `repo.register_compose_project` (line 416-451), it writes into `workers.compose_projects` AND `executions.metadata_json.compose_projects[]`.
- **MIRROR**: `src/cli.py:745-1339` (existing execute Click body); `src/core/execution/repository.py:416-451` (register_compose_project signature).
- **GOTCHA**: `register_compose_project` uses `json_insert(... '$[#]', ?)` twice (once for workers, once for executions); it's one call from the orchestrator, one BEGIN IMMEDIATE transaction.
- **GOTCHA**: DooD tests in the Claude Code sandbox cannot actually run `docker compose up`. Mock the compose subprocess in tests; end-to-end validation happens in `sentinel-dev`.
- **VALIDATE**:
  - `pytest tests/core/test_orchestrator.py::test_execute_registers_compose_project_before_up`
  - Integration: `pytest tests/integration/test_end_to_end.py::test_post_executions_cleans_up_compose_project_on_cancel`

#### Task 1.5: UPDATE `src/agents/base_agent.py` — emit `AgentStarted`/`AgentFinished`

- **ACTION**: Bookend `_send_message_async` (or its synchronous wrapper — whichever is the outer entry) with `AgentStarted` / `AgentFinished` emission. `session_id` is optional on both types; pass `self.session_id` if available.
- **IMPLEMENT**: Two new private methods `_emit_started()` and `_emit_finished(status: str, elapsed_s: float)`. Call `_emit_started` at the top of `run` (base class entry). Call `_emit_finished` in a `finally` block that captures success / exception.
- **MIRROR**: `src/agents/base_agent.py:281-313` (`_emit_message_sent`, `_emit_response_received`) — same guard, same try/except.
- **GOTCHA**: `AgentStarted`/`AgentFinished` are subclasses of `ExecutionEvent` in `src/core/events/types.py`; confirm the required fields before adding to call site (session_id is Optional).
- **GOTCHA**: Do NOT emit from `__init__`. Emission must be keyed to an actual `run(...)` entry, because an agent may be instantiated and never run.
- **VALIDATE**:
  - `pytest tests/agents/test_base_agent.py::test_agent_started_finished_bookend_run`
  - Event-type audit: `cc-event-audit` skill should now show 0 orphaned types (was 6; the 6th is `RateLimited`, which Phase 3 closes).

#### Task 1.6: UPDATE `src/cli.py` — thin the plan/execute/debrief Click bodies

- **ACTION**: Replace the inline agent orchestration in the three Click commands with calls to `Orchestrator.plan / execute / debrief`. Side-effects (git push, GitLab MR update, Jira comment) stay in the Click command, AFTER the orchestrator method returns — the orchestrator owns the agent run, the CLI owns the incidental outcomes.
- **IMPLEMENT**:
  - Keep `with orchestrator.run(ticket_id=..., kind=ExecutionKind.PLAN, options=...) as execution:` context manager.
  - Inside the `with`: call `orchestrator.plan(execution.id, **options)` and read the result.
  - After the `with` exits normally: do GitLab/Jira side-effects from `result.details`.
  - Same refactor for `debrief` and `execute`.
- **MIRROR**: Current CLI structure (`src/cli.py:297-412, 427-531, 745-1339`).
- **GOTCHA**: The current `execute` command has cascading side-effects between iterations (e.g., running `vendor/bin/phpunit` inside appserver). Those belong in `Orchestrator.execute`, not the CLI — they're part of the agent loop. Only post-orchestrator steps (final git push, MR creation) stay in the Click body.
- **GOTCHA**: `--remote` path (`src/cli.py:138-241`) is not affected by this task; it already goes through HTTP.
- **VALIDATE**:
  - `pytest tests/test_cli.py::test_plan_command_calls_orchestrator_plan`
  - Manual: `sentinel plan PROJ-123 --dry-run` produces the same events as before (observed via a temporary bus subscriber).

#### Task 1.7: UPDATE `src/core/execution/worker.py` — remove scaffold fallback, wire Orchestrator verbs

- **ACTION**: Delete `_scaffold_run` and the `if method is None: return _scaffold_run(...)` fall-through; after Task 1.2–1.4 all three kinds resolve to real methods.
- **IMPLEMENT**:
  - Keep `_resolve_method` as-is (it still maps kind → attribute name).
  - If `method is None` now: it's a BUG, not a scaffold path. Log loudly and call `orchestrator.fail(execution_id, error="orchestrator method not found")`, then return exit code 1.
- **MIRROR**: `src/core/execution/worker.py:114-156` (main worker loop).
- **GOTCHA**: This task must land in the same PR as Tasks 1.2/1.3/1.4, otherwise worker will blow up with "method not found" on every run.
- **VALIDATE**: `pytest tests/core/test_worker.py::test_worker_no_longer_falls_through_to_scaffold`

#### Phase 1 Validation Gate

```bash
# Static
ruff check src/ tests/ && mypy src/
# Unit
pytest tests/core/test_orchestrator.py tests/core/test_worker.py tests/agents/test_base_agent.py tests/core/test_event_bus.py -v
# Integration
pytest tests/integration/test_end_to_end.py::test_post_executions_emits_agent_lifecycle_events -v
# Event audit
# (uses the cc-event-audit skill — must report 0 orphaned types except RateLimited)
```

**Gate:** All green. 5 previously-orphaned event types (`AgentStarted`, `AgentFinished`, `TestResultRecorded`, `FindingPosted`, `DebriefTurn`, `RevisionRequested`) now have emit sites. `_scaffold_run` deleted.

---

### PHASE 2 — Status default (closes G-02)

Tiny change, but reshapes reconciliation tests. Lands immediately after Phase 1 so the worker's queued→running transition (already coded at `worker.py:119-123`) becomes live for the first time.

#### Task 2.1: UPDATE `src/core/execution/repository.py` — default to `QUEUED`

- **ACTION**: Change `create()` to insert with `ExecutionStatus.QUEUED.value` instead of `ExecutionStatus.RUNNING.value`.
- **IMPLEMENT**: In `repository.py:118` and the INSERT on line 135, swap `ExecutionStatus.RUNNING` → `ExecutionStatus.QUEUED`. Also change the returned `Execution(... status=...)` on line ~137 to match.
- **MIRROR**: `src/core/execution/repository.py:89-140` — only the status constant changes.
- **GOTCHA**: The plan-04 API contract (`POST /executions` → 202 status=queued) now holds; the `ExecutionOut` returned by `commands.start` will show `status=queued` for the ~milliseconds between `repo.create` and the worker's `set_status(RUNNING)` call.
- **VALIDATE**:
  - `pytest tests/core/test_execution_repository.py::test_create_defaults_to_queued`
  - `pytest tests/service/test_commands_routes.py::test_post_executions_returns_queued_initially` (new test)

#### Task 2.2: UPDATE `tests/core/test_supervisor.py` — remove coercion comment

- **ACTION**: Delete the `# Coerce status to QUEUED since create() defaults to RUNNING` workaround at `tests/core/test_supervisor.py:172` and the line below it (the explicit `repo.set_status(..., QUEUED)` call).
- **IMPLEMENT**: The test should now exercise `create()` directly and observe a `QUEUED` row.
- **MIRROR**: N/A — this is straight cleanup.
- **VALIDATE**: `pytest tests/core/test_supervisor.py -v`

#### Task 2.3: UPDATE `tests/core/test_worker.py` — assert queued→running transition

- **ACTION**: Add (or un-skip) `test_worker_transitions_queued_to_running` that starts a worker from a `QUEUED` row and asserts the row is `RUNNING` once the heartbeat thread has started.
- **IMPLEMENT**: Use existing fixtures; mock `_build_orchestrator` so `plan`/`execute`/`debrief` don't actually run agents; assert status via `repo.get(...)`.
- **MIRROR**: Existing worker test patterns in `tests/core/test_worker.py`.
- **VALIDATE**: `pytest tests/core/test_worker.py::test_worker_transitions_queued_to_running -v`

#### Task 2.4: UPDATE `src/core/execution/supervisor.py` — Set-C reconciliation now populable

- **ACTION**: Verify `adopt_or_reconcile_on_startup`'s Set C (orphaned queued rows) actually matches rows. If it was previously dead because no rows were ever `QUEUED`, ensure the test for it exists and passes.
- **IMPLEMENT**: No code change expected — but audit `supervisor.py:adopt_or_reconcile_on_startup` to confirm Set C's SELECT uses `status = 'queued'` and transitions to `FAILED` with error `'orphaned_on_restart'`. Add a test if missing.
- **MIRROR**: `src/core/execution/supervisor.py` (adopt_or_reconcile_on_startup).
- **VALIDATE**: `pytest tests/core/test_supervisor.py::test_reconcile_orphaned_queued_rows -v`

#### Phase 2 Validation Gate

```bash
pytest tests/core/ -v
```

**Gate:** All green. `tests/core/test_supervisor.py:172` no longer has the coercion comment. `status=queued` is observable in the window between `POST /executions` and the worker starting.

---

### PHASE 3 — SDK wrapper rate-limit emission + parity test (closes G-05, G-07)

Self-contained in `src/agent_sdk_wrapper.py` and `tests/test_agent_sdk_wrapper.py`. Can run in parallel with phases 4 and 5.

#### Task 3.1: UPDATE `src/agent_sdk_wrapper.py` — call `_publish_rate_limited` on 429/529

- **ACTION**: In `execute_with_tools`'s stream loop (`src/agent_sdk_wrapper.py:441-522`), wrap the `async for message in client.receive_response():` in a `try:` that catches the claude-agent-sdk's rate-limit exception class and publishes `RateLimited(retry_after_s=...)` before re-raising or sleeping.
- **IMPLEMENT**:
  - Inspect the installed `claude-agent-sdk` version (from `requirements*.txt` / `pyproject.toml`) to confirm the exception class name. Most SDKs surface 429 as `anthropic.RateLimitError` and 529 as `anthropic.APIStatusError` with `status_code == 529` — confirm by reading the SDK's error module before coding.
  - The `retry_after_s` is on the exception (`exc.response.headers.get("retry-after")` for 429) or falls back to `None`.
  - Call `self._publish_rate_limited(retry_after_s=retry_after)`. The helper already guards on `event_bus is None`.
  - After publish: re-raise. The existing supervisor/worker chain handles the failure. Do NOT sleep/retry here (the SDK does its own retry; we emit the observation).
- **MIRROR**: `src/agent_sdk_wrapper.py:244-258` (helper signature) + `:484-501` (existing per-message handling).
- **GOTCHA**: The plan-01 event `RateLimited` has wire-string `rate_limited` (underscore). That's G-19 — do not rename in this plan. The wire string is load-bearing if any real data exists in any DB.
- **GOTCHA**: The CLI's stream loop does not render `rate_limited` events specially yet. Dashboard work is out of scope here; emission is sufficient.
- **VALIDATE**: `pytest tests/test_agent_sdk_wrapper.py::test_publish_rate_limited_fires_on_429 -v`

#### Task 3.2: WRITE `tests/test_agent_sdk_wrapper.py::test_entry_dict_jsonl_bus_parity` (G-07)

- **ACTION**: Create the parity test explicitly named in plan 01 Task 9.
- **IMPLEMENT**:
  - Seed a run where the SDK wrapper handles a single `ToolUseBlock` and a single `ResultMessage` with usage.
  - Capture both (a) the JSONL line written to `/app/logs/agent_diagnostics.jsonl` (via `_write_diagnostic`) and (b) the `events.payload_json` rows inserted by the bus.
  - Assert they contain the same semantic fields (`tool` name, `args_summary`, `tokens_in`, `tokens_out`, `cents`). Exact format divergence is allowed (JSONL includes raw SDK metadata; bus payload is schema-enforced) — the test guards against **silent drift**: if a field is added to one surface and not the other without a test update, this fails.
- **MIRROR**: Existing tests in `tests/test_agent_sdk_wrapper.py` for fixture + mocking style.
- **GOTCHA**: The diagnostic JSONL path is `/app/logs/agent_diagnostics.jsonl` at runtime; the test should use `tmp_path` and monkeypatch the path.
- **GOTCHA**: The bus persists before dispatch; a synchronous `conn.execute("SELECT payload_json FROM events WHERE execution_id=?")` is sufficient to read back.
- **VALIDATE**: `pytest tests/test_agent_sdk_wrapper.py::test_entry_dict_jsonl_bus_parity -v`

#### Phase 3 Validation Gate

```bash
ruff check src/agent_sdk_wrapper.py tests/test_agent_sdk_wrapper.py
mypy src/agent_sdk_wrapper.py
pytest tests/test_agent_sdk_wrapper.py -v
```

**Gate:** All green. `RateLimited` now has an emit site; `cc-event-audit` reports zero orphaned types.

---

### PHASE 4 — Supervisor hygiene (closes G-08, G-09)

Two independent fixes in `supervisor.py` and `worker.py`. Can run in parallel with phases 3 and 5.

#### Task 4.1: UPDATE `src/core/execution/supervisor.py` — serialize env swap under `_lock`

- **ACTION**: The current `spawn` already holds `self._lock` via `@_locked`. The race is between `spawn`'s os.environ clear/update and the HTTP threadpool's handlers that read env at call time. Tighten the window.
- **IMPLEMENT**:
  - Move the `os.environ` clear/update/restore to the narrowest possible interval around `proc.start()`.
  - Document the hold window in a short `# GOTCHA:` comment (single line) referencing the trade-off. (One line only — per house rule, no multi-line comment blocks.)
  - Add `assert self._lock_is_held()` (or equivalent) if a debug-assertion hook exists; otherwise skip — do not invent infrastructure.
  - **Do not switch to `subprocess.Popen`.** That's a larger refactor filed separately; it will break DooD env inheritance and is not justified by the gap severity.
- **MIRROR**: `src/core/execution/supervisor.py:119-165` (existing spawn).
- **GOTCHA**: The test for this (Task 4.2) exercises concurrent spawns. If the repository were using an in-memory SQLite shared connection, the test would be racy; it isn't (plans use per-caller `connect()` via `_connection_factory`).
- **GOTCHA**: `os.environ.clear()` followed by `.update()` is atomic under the GIL for the dict mutation itself, but another thread reading `os.environ["PATH"]` mid-swap sees an empty string. The `_lock` serialization is the fix.
- **VALIDATE**: `pytest tests/core/test_supervisor.py::test_spawn_env_isolation_under_concurrent_requests -v`

#### Task 4.2: WRITE `tests/core/test_supervisor.py::test_spawn_env_isolation_under_concurrent_requests`

- **ACTION**: Concurrent-spawn test that asserts no env leakage.
- **IMPLEMENT**:
  - Use `concurrent.futures.ThreadPoolExecutor(max_workers=4)` to call `supervisor.spawn` four times concurrently against different execution IDs.
  - In the test worker entry (mocked), assert that `os.environ["SENTINEL_TEST_MARKER"]` matches the expected per-execution value (passed via the env allowlist).
  - Assert no race: set each execution's marker to its own ID; each subprocess must see its own marker.
- **MIRROR**: `tests/core/test_supervisor.py` existing spawn tests.
- **GOTCHA**: Tests in the Claude Code sandbox cannot `multiprocessing.Process.start()` with DooD; mock the process class if the actual spawn fails. Goal is the env-swap logic, not the child.
- **VALIDATE**: `pytest tests/core/test_supervisor.py::test_spawn_env_isolation_under_concurrent_requests -v`

#### Task 4.3: UPDATE `src/core/execution/worker.py` — use `repo.set_worker_heartbeat` (G-08)

- **ACTION**: Replace the raw SQL `UPDATE workers SET last_heartbeat_at = ?` in `_heartbeat_loop` (`worker.py:80-85`) with a call to `repo.set_worker_heartbeat(execution_id)`.
- **IMPLEMENT**:
  - Instantiate a per-thread `hb_repo = ExecutionRepository(hb_conn)` inside the heartbeat loop (the connection already lives there).
  - Replace the `execute/COMMIT/ROLLBACK` dance with `hb_repo.set_worker_heartbeat(execution_id)`.
  - Behaviour must be identical (same SQL, same `BEGIN IMMEDIATE`). Only testability improves — tests can now mock `set_worker_heartbeat`.
- **MIRROR**: `src/core/execution/repository.py:390-400` (existing method).
- **GOTCHA**: The heartbeat thread owns its own `hb_conn` (per the connection-per-caller rule). Do not share with the main worker connection.
- **VALIDATE**:
  - `pytest tests/core/test_worker.py::test_heartbeat_calls_repo_method -v` (new)
  - `pytest tests/core/test_worker_logging.py -v` (regression)

#### Phase 4 Validation Gate

```bash
pytest tests/core/test_supervisor.py tests/core/test_worker.py tests/core/test_worker_logging.py -v
```

**Gate:** All green. No raw SQL heartbeat in `worker.py`. Concurrent-spawn env test passes.

---

### PHASE 5 — WS connection cap (closes G-06)

Single-file-ish change: adds a per-token `asyncio.Semaphore` consulted before `ws.accept()`.

#### Task 5.1: UPDATE `src/service/rate_limit.py` — add `WsConnectionLimiter`

- **ACTION**: New class alongside `TokenRateLimiter`. Per-token `asyncio.Semaphore(value=max_per_token)`. Supports `acquire(token_prefix)` and `release(token_prefix)` as `async` methods.
- **IMPLEMENT**:
  - Signature:
    ```python
    class WsConnectionLimiter:
        def __init__(self, max_per_token: int) -> None: ...
        async def acquire(self, token_prefix: str) -> bool:
            """Return True if slot acquired, False if token is at cap (non-blocking)."""
        async def release(self, token_prefix: str) -> None: ...
    ```
  - Internally: `dict[str, asyncio.Semaphore]` guarded by an `asyncio.Lock` (not `threading.Lock` — FastAPI runs the WS handler on the event loop). Use `semaphore.locked()` / `sem._value` (or track value explicitly) to decide non-blocking acquire.
  - Simpler alternative: `dict[str, int]` counter + cap check, guarded by `asyncio.Lock`. Prefer this — the Semaphore is overkill for a non-blocking admission decision. One method uses `async with self._lock:` and bumps/decrements the counter, rejecting if >= cap.
- **MIRROR**: `src/service/rate_limit.py:TokenRateLimiter` for shape (check_and_reserve returns `(allowed, retry_after)`); adapt to async.
- **GOTCHA**: Do not reuse `TokenRateLimiter.check_and_reserve` — that one is sync and window-based; WS connections are long-lived and need a connection-count cap, not a request-rate cap. Keep them separate.
- **GOTCHA**: Config key per `bd-residuals.md` item 4: `service.rate_limits.ws_concurrent_per_token`. Default: 10.
- **VALIDATE**: `pytest tests/service/test_rate_limit.py::test_ws_connection_limiter_caps_per_token -v` (new)

#### Task 5.2: UPDATE `src/service/app.py` — instantiate `WsConnectionLimiter`

- **ACTION**: In `create_app`, add `app.state.ws_limiter = WsConnectionLimiter(max_per_token=cfg.get("service.rate_limits.ws_concurrent_per_token", 10))`.
- **IMPLEMENT**: Next to the existing `app.state.rate_limiter = ...` line (app.py:101-104).
- **MIRROR**: `src/service/app.py:100-108`.
- **GOTCHA**: `ConfigLoader.get` should return int via int(); keep the same `int(cfg.get(...))` pattern already used for `max_concurrent`.
- **VALIDATE**: Covered by Task 5.4's integration test.

#### Task 5.3: UPDATE `src/service/auth.py` — expose `token_prefix` from `require_token_ws`

- **ACTION**: Change `require_token_ws`'s return type from `str` (the raw token) to `tuple[str, str]` (token, token_prefix) **OR** add a second dep `ws_token_prefix(ws: WebSocket) -> str` that decodes the prefix. Second option is cleaner.
- **IMPLEMENT**:
  - Add `async def ws_token_prefix(ws: WebSocket) -> str` that re-extracts the bearer (reusing `_extract_bearer`) and returns `token_prefix(token)`.
  - Do not break existing callers of `require_token_ws`.
- **MIRROR**: `src/service/auth.py:205-222` (require_token_ws) and `:158-167` (token_prefix helper).
- **GOTCHA**: `_extract_bearer` already handles the loopback `?token=` fallback — do not re-implement the logic.
- **VALIDATE**: Covered by Task 5.4's test.

#### Task 5.4: UPDATE `src/service/routes/stream.py` — acquire/release around the WS handler

- **ACTION**: Before `ws.accept()`, call `ws_limiter.acquire(token_prefix)`. If rejected, close with `code=1008` (policy violation) and return. In the `finally` block at the end of the handler, call `ws_limiter.release(token_prefix)`.
- **IMPLEMENT**:
  - Add a new FastAPI dep: `token_prefix: Annotated[str, Depends(ws_token_prefix)]`.
  - Acquire BEFORE `await ws.accept()`. If denied: `await ws.close(code=1008)` and return. Do not accept and then close — the client should see the policy failure at handshake.
  - Release in `finally` — guaranteed paired.
- **MIRROR**: `src/service/routes/stream.py:55-119` (current endpoint); `src/service/auth.py:225-256` (generator-dep pattern for auth+rate-slot).
- **GOTCHA**: The current `stream` handler accepts `ws` unconditionally (`await ws.accept()` on line 62). If we move `acquire` before accept, the denied handshake closes with code 1008 without a WS subprotocol frame — which is fine per RFC 6455. Clients should treat 1008 as "at cap, retry later".
- **GOTCHA**: Use `await ws.close(code=1008, reason="ws_connections_per_token_exhausted")` so a thoughtful client can log the reason.
- **GOTCHA**: Do NOT relax the loopback restriction on `?token=` — G-10 is a separate gap and out of scope.
- **VALIDATE**:
  - `pytest tests/service/test_stream.py::test_ws_connection_cap_per_token -v`
  - Test should open `cap+1` connections; assert the (cap+1)th fails the handshake; assert that closing one of the first `cap` allows a new connection to succeed.

#### Phase 5 Validation Gate

```bash
ruff check src/service/ tests/service/
mypy src/service/
pytest tests/service/ -v
```

**Gate:** All green. Operators can observe that the 11th WS connection per token is rejected at handshake; the first 10 work normally.

---

## Testing Strategy

### Unit Tests to Write (totals)

| Test File                                        | New Tests                                                                                                                          | Validates                        |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| `tests/core/test_orchestrator.py`                | `test_set_phase_raises_when_cancelled`, `test_plan_happy_path_records_result_and_completes`, `test_debrief_emits_debrief_turn_event`, `test_execute_registers_compose_project_before_up` | G-00, G-01, G-03, G-04 |
| `tests/core/test_worker.py`                      | `test_worker_transitions_queued_to_running`, `test_worker_no_longer_falls_through_to_scaffold`, `test_heartbeat_calls_repo_method` | G-00, G-02, G-08                 |
| `tests/core/test_supervisor.py`                  | `test_spawn_env_isolation_under_concurrent_requests`, `test_reconcile_orphaned_queued_rows`                                        | G-09, G-02                       |
| `tests/core/test_execution_repository.py`        | `test_create_defaults_to_queued`                                                                                                   | G-02                             |
| `tests/agents/test_base_agent.py`                | `test_agent_started_finished_bookend_run`                                                                                          | G-04                             |
| `tests/test_agent_sdk_wrapper.py`                | `test_publish_rate_limited_fires_on_429`, `test_publish_rate_limited_fires_on_529`, **`test_entry_dict_jsonl_bus_parity`**         | G-05, G-07                       |
| `tests/service/test_rate_limit.py`               | `test_ws_connection_limiter_caps_per_token`                                                                                        | G-06                             |
| `tests/service/test_stream.py`                   | `test_ws_connection_cap_per_token`                                                                                                 | G-06                             |
| `tests/service/test_commands_routes.py`          | `test_post_executions_returns_queued_initially`                                                                                    | G-02                             |
| `tests/integration/test_end_to_end.py`           | `test_post_executions_emits_agent_lifecycle_events`, `test_post_executions_cleans_up_compose_project_on_cancel`                    | G-00, G-01, G-04                 |

### Edge Cases Checklist

- [ ] Cancel flag set mid-phase — `set_phase` raises; `post_mortem` cleans up.
- [ ] Cancel flag set BETWEEN phases — next `set_phase` raises at the boundary (G-03 acceptance).
- [ ] `register_compose_project` called twice for the same project — JSON array contains duplicate (current repo behaviour); asserted explicitly.
- [ ] `register_compose_project` called for an execution without a `workers` row — current SQL is a no-op UPDATE; assert that `executions.metadata_json` still gets the project.
- [ ] Worker crashes between `set_status(RUNNING)` and first heartbeat — reconciliation Set A catches it.
- [ ] Worker never starts (supervisor crashes before `proc.start()`) — reconciliation Set C catches the `QUEUED` row (G-02).
- [ ] Rate-limit exception of an unknown class (not 429/529) — falls through existing handler; no `RateLimited` emission.
- [ ] Rate-limit exception inside tool-use loop vs. top-level query — both code paths emit (guard via try/except wrapping).
- [ ] 11th WS connection for the same token — rejected at handshake.
- [ ] 11th WS connection for a **different** token — accepted (per-token, not global).
- [ ] Heartbeat `set_worker_heartbeat` called while the workers row has already been deleted by post_mortem — no-op UPDATE; no exception.
- [ ] Concurrent `supervisor.spawn` from 4 threads with different env allowlists — each child sees only its own env (G-09).

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
ruff check src/ tests/ && mypy src/
```

**EXPECT:** Exit 0, no errors.

### Level 2: UNIT_TESTS (fast)

```bash
# Per-phase (first four) — lets you run phases in parallel worktrees
pytest tests/core/ tests/agents/ -v           # Phase 1, 2, 4
pytest tests/test_agent_sdk_wrapper.py -v     # Phase 3
pytest tests/service/ -v                      # Phase 5
```

### Level 3: FULL_SUITE

```bash
pytest -v
```

**EXPECT:** All pass. Zero orphaned event types when `cc-event-audit` runs (except `rate_limited` wire-string parity issue, which is G-19, out of scope).

### Level 4: DATABASE_VALIDATION

Use `cc-schema-inspect` skill:

- [ ] Executions table row counts show `QUEUED` rows appearing (previously 0 lifetime).
- [ ] `events.type` distribution shows all 17 declared types with non-zero counts after a smoke run.
- [ ] `executions.metadata_json->'compose_projects'` is a non-empty array for at least one run.

### Level 5: BROWSER_VALIDATION

Not applicable — no UI in this plan. However, the Swagger spec (`/openapi.json` when `SENTINEL_ENABLE_DOCS=true`) should show unchanged endpoint signatures.

### Level 6: MANUAL_VALIDATION (in `sentinel-dev` container)

Run from `sentinel-dev` (Docker socket available):

```bash
# Smoke: end-to-end POST /executions with real appserver
sentinel serve &
curl -X POST localhost:8787/executions \
    -H "Authorization: Bearer $(cat ~/.sentinel/service_token)" \
    -H "Content-Type: application/json" \
    -d '{"ticket_id":"SMOKE-1","project":"smoke","kind":"plan"}'

# Should return 202 with status=queued (G-02)
# Watch the WS stream — should see agent.started, agent.message_sent, tool.called, etc.
```

Then:

```bash
# Cancel and verify compose cleanup
curl -X POST localhost:8787/executions/<id>/cancel \
    -H "Authorization: Bearer $(cat ~/.sentinel/service_token)"
docker ps --filter "name=appserver-smoke-" # Should be empty after ~30s
```

---

## Acceptance Criteria

- [ ] `POST /executions` + WS stream yields a full event timeline (≥ `agent.started`, `agent.message_sent`, ≥1× `tool.called`, ≥1× `cost.accrued`, `agent.response_received`, `agent.finished`, `execution.completed`) — **G-00, G-04 partial**.
- [ ] After a cancelled or failed run, `docker ps --filter name=appserver-<project>-*` is empty within 60s of the terminal event — **G-01**.
- [ ] `POST /executions` returns `{"status":"queued",...}` in the 202 body; WS stream's first frame's execution row has transitioned to `running` before `execution.started` is emitted — **G-02**.
- [ ] Cancelling a run that's mid-execution causes the orchestrator to exit at the next `set_phase` boundary (observable: no new `tool.called` events after the cancel) — **G-03**.
- [ ] All 17 declared event types have ≥1 emit site. `cc-event-audit` reports no orphaned types (except the unresolved G-19 wire-string concern, which is not about emission) — **G-04**.
- [ ] Triggering an Anthropic 429 (in test via SDK mock) produces exactly one `rate_limited` event on the stream — **G-05**.
- [ ] 11th concurrent WS connection for a single token is rejected with close-code 1008 — **G-06**.
- [ ] `tests/test_agent_sdk_wrapper.py::test_entry_dict_jsonl_bus_parity` exists and passes — **G-07**.
- [ ] `grep -rn "UPDATE workers SET last_heartbeat_at" src/core/execution/worker.py` returns zero hits (only the `repo.set_worker_heartbeat` method uses that SQL) — **G-08**.
- [ ] Concurrent `supervisor.spawn` test passes (no env leakage between spawns) — **G-09**.
- [ ] No regression in existing `tests/` (`pytest -v` all green).

---

## Completion Checklist

- [ ] Phase 1: Orchestrator extraction merged (`experimental/command-center-07-gap-closure`).
- [ ] Phase 2: Status default = QUEUED merged.
- [ ] Phase 3: SDK wrapper rate-limit emission + parity test merged.
- [ ] Phase 4: Supervisor env race + heartbeat method merged.
- [ ] Phase 5: WS connection cap merged.
- [ ] Level 1: ruff + mypy clean.
- [ ] Level 2: per-phase pytest green.
- [ ] Level 3: full pytest green.
- [ ] Level 4: `cc-schema-inspect` confirms event-type coverage.
- [ ] Level 6: manual smoke in `sentinel-dev` (user runs; documented in session handoff).
- [ ] Gap analysis updated: G-00..G-09 marked closed; follow-ups filed for G-10..G-26 and residuals.

---

## Risks and Mitigations

| Risk                                                                                                            | Likelihood | Impact  | Mitigation                                                                                                                                                                                                                               |
| --------------------------------------------------------------------------------------------------------------- | ---------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Phase 1 regresses `sentinel plan` / `execute` / `debrief` CLI behaviour for non-remote users                     | MED        | HIGH    | Task 1.6 mirrors the existing Click bodies exactly; all three commands have integration tests; non-remote is the dominant dev path today                                                                                                 |
| Phase 1 changes cost-accrual subscriber ordering and double-counts                                               | LOW        | HIGH    | The cost subscriber is registered in `Orchestrator.__init__` (one place). Tasks 1.2–1.4 do not add subscribers. Test: `test_execute_cost_cents_matches_sum_of_events`                                                                    |
| G-02 status default breaks dashboards that already assume 202 = running                                          | LOW        | MEDIUM  | The gap analysis is explicit that plan 04's API contract says `status=queued`; any dashboard consuming the broken behaviour is itself wrong. Run smoke test in sentinel-dev before deploy.                                                |
| `register_compose_project` called in the orchestrator but the worker row isn't yet in `workers` (race)           | LOW        | MEDIUM  | `workers` row is INSERTed in `supervisor.spawn` BEFORE `proc.start()`. By the time `Orchestrator.execute` runs inside the worker, the row exists. Task 1.4 asserts this ordering with a test.                                            |
| WS semaphore bug causes permanent starvation (release not called)                                                | LOW        | HIGH    | Task 5.4 uses the generator/finally pattern, identical to the HTTP `require_token_and_write_slot` rate-slot pattern. Test `test_ws_connection_cap_releases_on_disconnect` covers clean + dirty disconnect.                              |
| Emitting 6 new event types causes `events` table growth to exceed plan assumptions                               | LOW        | LOW     | Plan 01 already sized the table for this traffic. Retention/sweep is residual R-14, out of scope.                                                                                                                                         |
| `register_compose_project` JSON write fails silently for missing workers row                                     | LOW        | MEDIUM  | Existing SQL uses `UPDATE workers WHERE execution_id=?` which is a no-op for missing row. Add an assertion in Task 1.4's test that `workers.compose_projects` is populated.                                                              |
| SDK rate-limit exception class differs between claude-agent-sdk versions                                         | MED        | LOW     | Task 3.1 explicitly calls for reading the installed SDK's error module before coding. If the class is wrong, the test fails and we patch immediately. No production traffic yet.                                                         |
| Parallel phases 3/4/5 merge-conflict                                                                              | LOW        | LOW     | Files touched are disjoint: phase 3 = `agent_sdk_wrapper.py`, phase 4 = `supervisor.py` + `worker.py`, phase 5 = `service/*`. Confirmed by "Files to Change" table.                                                                        |

---

## Notes

**Why phased rather than monolithic.** Per the gap analysis's own "Suggested sequencing" section, the fixes break naturally along file boundaries. Phase 1 is the keystone (5 files); phases 2–5 are narrow (1–3 files each). This structure lets phases 3, 4, 5 run in parallel worktrees after phase 2 merges — the existing `experimental/command-center-*` branch rule already provides the isolation.

**Why we do NOT rename `rate_limited` to `rate.limited` (G-19).** Wire strings are immutable once any real data lands in `events.type`. The audit's own note says "Forward-compatible to rename before any real data lands; after that, forward-only migration applies." Until this plan lands, emission count for `rate_limited` is zero — but lifestyle on the experimental branches may have written events. We defer the rename to a separate migration plan so the data question can be settled with fresh eyes.

**Why we do NOT consolidate retry_of to root (G-12) in this plan.** That's an API-shape decision that should be made alongside the dashboard work; it's listed as MEDIUM and explicitly out of scope per the user's chosen scope.

**Session handoff note.** When this plan is complete, the gap analysis should be updated: strike G-00..G-09 from the BLOCKER/HIGH sections and add the completion date. The MEDIUM/LOW/Residuals stay on the backlog, tracked in whichever replacement for `bd` the worktree offers when it comes back online.

**Confidence score: 8/10 for one-pass implementation success.**
- The orchestrator extraction (Phase 1) has the most moving pieces; the other four phases are tight.
- Counter-weight: the existing codebase has excellent patterns to mirror — `_publish_*` helpers, the generator-dep auth pattern, the `BEGIN IMMEDIATE` repo pattern — so new code has a clear template.
- Risk area: SDK rate-limit exception class identification (Task 3.1). If the SDK's exception surface isn't what we expect, Task 3.1 slips by a day while we probe.
