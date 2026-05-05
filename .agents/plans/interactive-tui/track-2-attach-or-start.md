# Track 2 — Attach-or-start service endpoints

## Goal

Give the TUI a single HTTP surface it can POST to and always get a useful response: either the run it just started, or the run that was already running for the same `(project, ticket_id, kind)`. Plus a cancel endpoint for active runs.

## Current state

`src/service/routes/executions.py` is **read-only** — `GET /executions`, `GET /executions/{id}`, `/events`, `/agent-results`. The Orchestrator (`src/core/execution/orchestrator.py`) owns `begin()` and the workflow verbs; the Supervisor (`src/core/execution/supervisor.py`) owns subprocess spawn and cancel. No write endpoint wires those together.

## Deliverables

### 1. `POST /executions`

**Request** (`ExecutionCreate`):

```json
{
  "project": "banv",
  "ticket_id": "BANV-1234",
  "kind": "plan",
  "options": {}
}
```

- `kind` ∈ `{plan, execute, debrief}` — validated against `ExecutionKind`.
- `options` — free-form dict; server caps the JSON-encoded size at ~8 KB; unknown keys passed through to the Orchestrator and ignored there if unrecognised.

**Response** (`ExecutionStartResponse`):

```json
{
  "execution": { /* ExecutionOut */ },
  "attached": false,
  "banner": null
}
```

- `attached=true` when the response is an existing active run rather than a freshly-spawned one.
- `banner` is a human-readable string the TUI renders verbatim. Populated only when `attached=true`: `"Attached to run {short_id} started {rel} ago"`. Service formats the relative-time portion (e.g., `"14m ago"`).

**Logic** (single `BEGIN IMMEDIATE` transaction):

1. Look up active row: `project=? AND ticket_id=? AND kind=? AND status IN ('pending','running')`. LIMIT 1, newest first.
2. **Hit** → return that row, `attached=true`, populate `banner`.
3. **Miss** →
   - `orchestrator.begin(ticket_id=..., project=..., kind=..., options=...)` creates the row.
   - `supervisor.spawn(execution_id)` forks the worker.
   - Return the fresh row, `attached=false`, `banner=null`.

Transaction serialises concurrent POSTs; the second POST sees the first's freshly-inserted row and attaches.

**Status codes**:

- `201` on fresh start.
- `200` on attach.
- `400` on bad kind / unknown project / malformed options.
- `413` on options payload over the cap.
- `503` if the Supervisor cannot spawn (e.g., Docker socket unreachable) — include the underlying cause in `detail`.

### 2. `POST /executions/{id}/cancel`

**Request**: empty body (or `{reason?: str}` — optional).

**Response** (`ExecutionCancelResponse`):

```json
{
  "execution": { /* ExecutionOut with status=cancelled|running (in-flight) */ },
  "signalled": true
}
```

- Calls `supervisor.cancel(execution_id)`. Idempotent.
- `202` if the signal was sent (run transitioning to cancelled).
- `409` if the run is already terminal (succeeded / failed / cancelled / crashed) — include the current status.
- `404` if unknown id.

### 3. Repository helper (if needed)

`ExecutionRepository.find_active(project, ticket_id, kind) -> Optional[Execution]`. Only add if the existing `list()` signature can't be used cleanly for the attach lookup inside the transaction. Prefer reusing `list()` with `status IN (...)` to avoid a second code path.

### 4. Schemas

Add to `src/service/schemas.py`:

- `ExecutionCreate` — request.
- `ExecutionStartResponse` — wrap `ExecutionOut` with `attached: bool` and `banner: Optional[str]`.
- `ExecutionCancelResponse` — wrap `ExecutionOut` with `signalled: bool`.

Keep `ExecutionOut` unchanged.

## Files touched

| File | Change |
|---|---|
| `src/service/routes/executions.py` | Add POST create + POST cancel handlers. |
| `src/service/schemas.py` | Three new models. |
| `src/core/execution/repository.py` | `find_active` if needed; otherwise untouched. |
| `tests/service/test_executions_write.py` | New file: start / attach / cancel / 404 / 409 / 413. |

## Explicitly out of scope

- Supervisor / Orchestrator internals. If `spawn` / `cancel` signatures are different than assumed, shim at the route layer, not upstream.
- Migrations. No schema changes.
- Event bus. Workflow emission is already wired end-to-end.
- Auth. `require_token*` dependencies already guard the router.
- Rate limit behaviour. The per-token limiter already applies.

## Assumptions to confirm before coding

1. `Supervisor` exposes `spawn(execution_id: str)` and `cancel(execution_id: str)` or equivalent — otherwise what's the current shape?
2. `ExecutionStatus` terminal set covers succeeded/failed/cancelled/crashed. Cancel's 409 check depends on this enumeration.
3. `ExecutionRepository.list(...)` can filter by `status IN (pending, running)`. If it only takes a single status, add `find_active`.

Consult `cc-supervisor-expert` and `cc-persistence-expert` for these — quick reads, not full tasks.

## Gotchas

- **Stale running row between worker crash and reaper sweep.** POST can attach to a dead row. The reaper flips it to `CRASHED` within seconds; TUI retries and starts fresh. Acceptable in v1. If we want to pre-empt it, have the attach lookup also check `last_heartbeat_at > now - threshold` — but that's Supervisor contract, not route logic.
- **Worktree doesn't exist for `kind=execute`**: not this endpoint's problem. The spawned worker fails loudly; the TUI surfaces it via the stream.
- **Bad `options` JSON size** (>8 KB): reject early with 413 before touching the DB.
- **Cancel of a `pending` run**: row exists, worker may not yet. `supervisor.cancel` must handle both "process is running" and "process never started" cleanly. Confirm with `cc-supervisor-expert`.

## Tests (target coverage)

- Fresh start → 201, row created, worker spawned (mock supervisor).
- Second POST with same triple while first is running → 200, `attached=true`, `banner` populated, same id.
- POST with unknown kind → 400.
- POST with 20 KB options → 413.
- Cancel running run → 202, supervisor.cancel called with the id.
- Cancel terminal run → 409, status echoed.
- Cancel unknown id → 404.
- Cancel idempotency: two quick cancels → first 202, second 202 (signal already in flight) or 409 (if already transitioned). Both acceptable; pin the behaviour in the test.

## Acceptance

- `curl -XPOST /executions` with valid body spawns a worker and returns the row.
- A second `curl` with the same body while the first is running returns the same id with `attached=true` and a banner like `"Attached to run abc12345 started 14s ago"`.
- `curl -XPOST /executions/{id}/cancel` cleanly terminates an in-flight run.
- Unit + integration tests all pass. `cc-plan-reviewer` signs off that no invariant from the five Command Center plan files is violated.

## Delegation plan

1. `cc-fastapi-expert` → routes + schemas.
2. `cc-supervisor-expert` → 5-minute consult: confirm spawn / cancel signatures and edge cases (pending-state cancel, already-terminal cancel).
3. `cc-persistence-expert` → `find_active` if needed; otherwise nothing.
4. `cc-test-harness-expert` → `tests/service/test_executions_write.py`.
5. `cc-plan-reviewer` → cross-plan consistency check before merge.
