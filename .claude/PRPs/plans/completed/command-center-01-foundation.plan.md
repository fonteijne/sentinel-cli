# Feature: Command Center Foundation — Execution entity, SQLite persistence, event bus, CLI-as-client

## Summary

Extract the execution orchestration that currently lives inline in `src/cli.py` into a dedicated `Orchestrator` class, back it with a first-class `Execution` entity persisted to SQLite, and route agent progress through a structured event bus. The CLI commands (`plan`, `execute`, `debrief`) become thin callers of the orchestrator. No HTTP, no WebSocket, no auth — those are follow-up plans (02–05).

## User Story

As a Sentinel maintainer
I want execution state (lifecycle, agent progress, costs, test results, findings) captured in a queryable store and emitted through a typed event bus
So that the future Command Center dashboard and any other consumer can observe and control runs without re-parsing CLI stdout or git state.

## Problem Statement

Today `sentinel execute TICKET` runs orchestration inline inside the Click command body (`src/cli.py:478–1009`). Execution state is implicit in worktree files, Jira/GitLab side-effects, and plain-text logs. There is no stable run identity, no lifecycle, no way to query "what happened on this run" after the fact, and no way for a non-CLI consumer to observe progress.

## Solution Statement

1. Introduce `core.execution.Execution` (model), `ExecutionRepository` (SQLite-backed), and `Orchestrator` (the class that owns `plan/execute/debrief`).
2. Introduce `core.events.EventBus` — in-process pub/sub where every `publish()` persists the event to the `events` table *before* firing subscribers. Persistence is the source of truth.
3. Extend `BaseAgent` and `AgentSDKWrapper` to emit typed events when given an event bus; stay silent when not (preserves existing test fixtures).
4. Refactor `cli.plan`, `cli.execute`, `cli.debrief` to: create an `Execution`, build an `Orchestrator` wired to repo + bus + SessionTracker, call the orchestrator, report results.

## Metadata

| Field            | Value |
|------------------|-------|
| Type             | REFACTOR |
| Complexity       | HIGH |
| Systems Affected | `src/cli.py`, `src/agents/*`, `src/agent_sdk_wrapper.py`, `src/session_tracker.py`, `pyproject.toml` |
| Dependencies     | stdlib `sqlite3`, existing `pydantic ^2.5`, `click ^8.1` — **no new deps** |
| Estimated Tasks  | 12 |

---

## UX Design

This is a backend refactor; "UX" here is the *data-flow transformation*, not visual UI.

### Before State
```
╔══════════════════════════════════════════════════════════════════╗
║ $ sentinel execute PROJ-123                                      ║
║                                                                  ║
║  cli.execute() ─── drives ───► Agents directly                   ║
║       │                          │                               ║
║       ├── stdout logs           ├── logger.info (plain text)     ║
║       ├── Jira comments         ├── returns result dict          ║
║       ├── GitLab MR updates     └── writes diagnostics.jsonl     ║
║       └── worktree files                                         ║
║                                                                  ║
║  STATE: implicit in git, GitLab MR, logs/*.jsonl                 ║
║  QUERY: grep logs or look at MR                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

### After State
```
╔══════════════════════════════════════════════════════════════════╗
║ $ sentinel execute PROJ-123                                      ║
║                                                                  ║
║  cli.execute()                                                   ║
║       │                                                          ║
║       ▼                                                          ║
║  Orchestrator(repo, bus, session_tracker)                        ║
║       │                                                          ║
║       ├─► creates Execution row (status=running)                 ║
║       ├─► drives Agents (unchanged external behaviour)           ║
║       │     │                                                    ║
║       │     └─► bus.publish(AgentStarted/ToolCalled/…)           ║
║       │              │                                           ║
║       │              ▼                                           ║
║       │         events table (append-only, seq per execution)    ║
║       │              │                                           ║
║       │              └─► subscribers (logger adapter today,      ║
║       │                               WebSocket tomorrow)        ║
║       │                                                          ║
║       └─► updates Execution row (status=done/failed, cost, etc.) ║
║                                                                  ║
║  STATE: ~/.sentinel/sentinel.db (executions, events, agent_results)║
║  QUERY: sqlite3 or (plan 02) HTTP API                            ║
╚══════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location | Before | After | Impact |
|---|---|---|---|
| `sentinel execute` | Inline orchestration in Click body | Delegates to `Orchestrator.execute(execution_id)` | CLI stays a thin client |
| Agent `logger.info(...)` | Only stdout/logs | Same logger calls **plus** typed events on bus when bus injected | Observability without losing existing logs |
| Post-run state | Grep logs, look at MR | `SELECT … FROM executions/events` | Queryable history |

---

## Mandatory Reading

Read before touching any task.

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `src/cli.py` | 478–1009 | The `execute` command body we're extracting — the orchestration we are literally moving |
| P0 | `src/agents/base_agent.py` | 18–218 | BaseAgent surface; where event emission attaches |
| P0 | `src/session_tracker.py` | 1–151 | The *only* existing persistence pattern; JSON file with `~/.sentinel/` parent-dir creation idiom to mirror |
| P1 | `src/agent_sdk_wrapper.py` | 136–164 | `_write_diagnostic` — our prior art for structured event emission; events table schema should cover what this writes |
| P1 | `src/agents/base_developer.py` | 744–750 | Canonical developer-result dict shape — drives `agent_results` table |
| P1 | `src/agents/security_reviewer.py` | 708–719 | Reviewer result dict shape |
| P1 | `src/config_loader.py` | 31–150, 431–441 | `get_config()` singleton — the pattern repo/bus/orchestrator accessors should mirror |
| P2 | `tests/test_base_agent.py` | 1–100 | Fixture style for mocking `get_config`, `AgentSDKWrapper`, prompt loader — reuse for new tests |
| P2 | `tests/test_session_tracker.py` | 27–75 | `tmp_path` + `__init__` patching pattern for isolating filesystem state in tests |

No external documentation required for this plan — everything is stdlib + existing deps.

---

## Patterns to Mirror

**LOGGER_INSTANTIATION** (every new module starts with this, module-level):
```python
# SOURCE: src/session_tracker.py:8
# COPY VERBATIM:
import logging

logger = logging.getLogger(__name__)
```

**PERSISTENT_STATE_LOCATION** (Sentinel's convention for user-scoped state files):
```python
# SOURCE: src/session_tracker.py:19-20
# COPY PATTERN (swap file name):
self.sessions_file = Path.home() / ".sentinel" / "sessions.json"
self.sessions_file.parent.mkdir(parents=True, exist_ok=True)
# → for us: Path.home() / ".sentinel" / "sentinel.db"
```

**STRUCTURED_EVENT_EMISSION** (prior art to subsume, not contradict):
```python
# SOURCE: src/agent_sdk_wrapper.py:152-161
# Event bus should persist entries with the SAME shape so diagnostics.jsonl and events table stay cross-readable:
entry = {
    "ts": datetime.now(timezone.utc).isoformat(),
    "agent": self.agent_name,
    "event": event,
    "cwd": cwd,
    **data,
}
```

**SINGLETON_ACCESSOR** (how `ConfigLoader` exposes itself; repo/bus/orchestrator factories follow this):
```python
# SOURCE: src/config_loader.py:431-441
_config: Optional[ConfigLoader] = None

def get_config() -> ConfigLoader:
    global _config
    if _config is None:
        _config = ConfigLoader()
    return _config
```

**AGENT_PROGRESS_LOG** (the existing logger.info call — keep this, add event emission alongside):
```python
# SOURCE: src/agents/base_agent.py:153-156
logger.info(
    f"[LLM] {self.agent_name}: sending request "
    f"(prompt={len(content)} chars, cwd={cwd}, session={self.session_id}, max_turns={max_turns})"
)
```

**TEST_FIXTURE_STYLE** (SessionTracker test — use identically for repository test):
```python
# SOURCE: tests/test_session_tracker.py:27-75
@pytest.fixture
def tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(SessionTracker, "__init__", lambda self: None)
    t = SessionTracker()
    t.sessions_file = tmp_path / "sessions.json"
    t.sessions_file.parent.mkdir(parents=True, exist_ok=True)
    return t
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `src/core/__init__.py` | CREATE | New package root |
| `src/core/persistence/__init__.py` | CREATE | Package marker, re-exports `get_db`, `run_migrations` |
| `src/core/persistence/db.py` | CREATE | SQLite connection helper, migration runner, `DB_PATH` |
| `src/core/persistence/migrations/001_init.sql` | CREATE | Initial schema |
| `src/core/events/__init__.py` | CREATE | Re-exports `EventBus`, event types |
| `src/core/events/types.py` | CREATE | Event pydantic models (typed payloads) |
| `src/core/events/bus.py` | CREATE | `EventBus` class: persist-then-publish |
| `src/core/execution/__init__.py` | CREATE | Re-exports `Execution`, `ExecutionStatus`, `Orchestrator`, `ExecutionRepository` |
| `src/core/execution/models.py` | CREATE | `Execution` pydantic model + `ExecutionStatus` enum |
| `src/core/execution/repository.py` | CREATE | CRUD over `executions` + `agent_results` |
| `src/core/execution/orchestrator.py` | CREATE | Owns `plan`, `execute`, `debrief` flows — lifted from `cli.py` |
| `tests/core/__init__.py` | CREATE | Test package marker |
| `tests/core/test_persistence.py` | CREATE | Migration + schema validation |
| `tests/core/test_event_bus.py` | CREATE | Persist-then-publish semantics |
| `tests/core/test_execution_repository.py` | CREATE | CRUD + lifecycle transitions |
| `tests/core/test_orchestrator.py` | CREATE | Orchestrator happy path + failure path (agents mocked) |
| `src/agents/base_agent.py` | UPDATE | Accept optional `event_bus` + `execution_id`; emit `AgentStarted`/`AgentMessageSent`/`AgentResponseReceived` |
| `src/agent_sdk_wrapper.py` | UPDATE | When event bus present, also publish `ToolCalled` events (keeps writing diagnostics.jsonl too) |
| `src/cli.py` | UPDATE | `plan`, `execute`, `debrief` build an `Orchestrator` and delegate; stop containing orchestration logic |
| `src/session_tracker.py` | (untouched) | No change in this plan. Legacy `sessions.json` coexists with the new DB; a future plan may subsume it. |

---

## Database Schema (migration 001_init.sql)

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
    id                TEXT PRIMARY KEY,            -- uuid4 hex
    ticket_id         TEXT NOT NULL,
    project           TEXT NOT NULL,
    kind              TEXT NOT NULL,               -- ExecutionKind enum value
    status            TEXT NOT NULL,               -- ExecutionStatus enum value
    phase             TEXT,                        -- current agent/step label
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    cost_cents        INTEGER NOT NULL DEFAULT 0,
    error             TEXT,
    idempotency_token_prefix  TEXT,                -- sha256(token)[:8]; nullable for CLI-triggered runs
    idempotency_key   TEXT,                        -- nullable; set by plan 04 POST endpoint
    metadata_json     TEXT NOT NULL DEFAULT '{}'
);
-- Idempotency is scoped by token so keys from different tokens can't collide (forward-compatible with multi-user).
CREATE UNIQUE INDEX IF NOT EXISTS idx_executions_idempotency
    ON executions(idempotency_token_prefix, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_executions_ticket ON executions(ticket_id);
CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);
CREATE INDEX IF NOT EXISTS idx_executions_started ON executions(started_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id  TEXT NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,                -- monotonic per execution
    ts            TEXT NOT NULL,                   -- ISO-8601 UTC, tz-aware
    agent         TEXT,                            -- nullable (system events)
    type          TEXT NOT NULL,                   -- event type name (stable identifier)
    payload_json  TEXT NOT NULL,
    UNIQUE(execution_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_execution ON events(execution_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);          -- for retention sweeps + cross-execution time filters

CREATE TABLE IF NOT EXISTS agent_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id  TEXT NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    agent         TEXT NOT NULL,
    result_json   TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_results_execution ON agent_results(execution_id);
```

### Event Types (src/core/events/types.py)

Each is a pydantic model with `execution_id`, `ts`, `agent` (opt), and a typed payload. Event `type` strings are stable identifiers — persisted; never rename.

**Lifecycle:**
- `ExecutionStarted` — `{kind, ticket_id, project}`
- `ExecutionCompleted` — `{status, cost_cents}`
- `ExecutionFailed` — `{error}`
- `ExecutionCancelling` — `{}` (operator requested stop; worker is winding down)
- `ExecutionCancelled` — `{}` (terminal)
- `PhaseChanged` — `{phase}` (e.g. "planning", "implementing", "reviewing")

**Agent / tool:**
- `AgentStarted` / `AgentFinished` — `{agent, session_id}`
- `AgentMessageSent` — `{prompt_chars, cwd, max_turns}`
- `AgentResponseReceived` — `{response_chars, tool_uses_count, elapsed_s}`
- `ToolCalled` — `{tool, args_summary}`

**Results:**
- `TestResultRecorded` — `{success, return_code}`
- `FindingPosted` — `{severity, summary}`
- `CostAccrued` — `{tokens_in, tokens_out, cents}` — emitted by `AgentSDKWrapper` after each SDK call; Orchestrator subscribes and calls `repo.add_cost`

**Interactive / revision:**
- `DebriefTurn` — `{turn_index, prompt_chars, response_chars}` — one per debrief round-trip (Jira conversation loop).
- `RevisionRequested` — `{revise_of_execution_id, reason}` — emitted when `execute --revise` starts; cross-links the new execution to its source. See plan 01 Task 10 for same-row-vs-linked-child decision (we create a linked child; `retry_of` already proves the pattern).

**Error-class differentiation:**
- `RateLimited` — `{retry_after_s}` — Anthropic 429/529 or equivalent. **Observational only; does NOT transition `ExecutionStatus`.** Run stays `running`; orchestrator handles backoff.

**Constants to export from `types.py`:**
```python
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset({
    "execution.completed", "execution.failed", "execution.cancelled"
})
```
Consumers (plan 03 WS tail) use this to decide when to close the stream.

### ExecutionStatus enum

`queued | running | cancelling | succeeded | failed | cancelled`

`cancelling` is transitional: set by plan 04's cancel endpoint, cleared when the worker reaches a clean stop (becomes `cancelled`) or is force-killed.

### ExecutionKind enum

`plan | execute | debrief`

Used in the `executions.kind` column and in plan 04's `POST /executions` body. Pydantic rejects unknown values at the HTTP boundary, not inside the agent.

---

## NOT Building (Scope Limits)

- **HTTP / WebSocket** — plans 02 and 03.
- **Out-of-process workers** — plan 04. Foundation runs Orchestrator in-process from the CLI; a crash kills the run the same as today.
- **Auth / token / network binding** — plan 05.
- **Replacing SessionTracker** — keep it; new DB supplements.
- **Replacing logger calls with events** — events are *added alongside*, existing `logger.info(...)` calls stay.
- **Replacing `logs/agent_diagnostics.jsonl`** — keep it (sentinel-dev operators rely on tailing this file during development). Event bus publishes the same shape to the DB.
- **Migrating `beads_manager.py`** — untouched.
- **Migrating `~/.sentinel/sessions.json` into the DB** — coexistence only; a follow-up plan can subsume it.
- **Changing agent behaviour** — no agent output changes.
- **Retention / archival of `events` and `agent_results`** — deferred. Add `idx_events_ts` now so a future sweep is fast; no TTL yet.
- **`succeeded_with_warnings` status** — today binary success/failure. If post-implementation work (e.g. GitLab push) fails after agents succeeded, we mark `failed` with the error message; operators read findings/warnings via `agent_results` and `events`.
- **Forced post-mortem re-run** — operators who need to force re-cleanup of a terminal row do it via `sqlite3 'UPDATE executions SET metadata_json = json_remove(metadata_json, "$.post_mortem_complete") WHERE id = ?'` and restart the service. No endpoint.

---

## Step-by-Step Tasks

Execute in order. Each task is independently testable.

### Task 1 — CREATE `src/core/persistence/db.py`

- **ACTION**: Create SQLite connection factory and migration runner. **No module-level singleton connection** — every caller gets its own connection.
- **IMPLEMENT**:
  - `DB_PATH` resolution order: `os.environ["SENTINEL_DB_PATH"]` → `Path.home() / ".sentinel" / "sentinel.db"`. Expose as module-level `get_db_path() -> Path` (called each time, not memoized — test-friendly).
  - `connect() -> sqlite3.Connection` — returns a **new** connection, fully configured:
    ```python
    conn = sqlite3.connect(
        get_db_path(),
        timeout=30,
        isolation_level=None,          # we manage transactions explicitly
        check_same_thread=False,       # FastAPI threadpool + worker subprocesses need this
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn
    ```
  - `ensure_initialized()` — idempotent; opens a connection, asserts SQLite JSON1 features + minimum version, runs `run_migrations`, closes. Called once at CLI startup and again by plan 02's FastAPI lifespan. Version assertion:
    ```python
    (major, minor, patch) = [int(x) for x in sqlite3.sqlite_version.split(".")[:3]]
    if (major, minor) < (3, 38):
        raise RuntimeError(
            f"SQLite >= 3.38 required for json_insert('$[#]') append syntax; "
            f"found {sqlite3.sqlite_version}. Upgrade Python / sqlite3."
        )
    conn.execute("SELECT json_extract('{}','$.x')")    # fails loudly if JSON1 not compiled in
    ```
  - `run_migrations(conn)` — reads `migrations/*.sql` in sorted filename order, executes any whose `version` (leading digits) is not in `schema_migrations`; records applied versions with UTC ISO timestamp. Wrap each migration in `BEGIN IMMEDIATE` / `COMMIT`.
- **MIRROR**: `src/session_tracker.py:19-20` for the fallback path convention (`Path.home() / ".sentinel" / ...`) and `parent.mkdir(parents=True, exist_ok=True)`.
- **GOTCHA**: **Do not keep a module-level connection**. sqlite3 connections have per-connection state (transactions, row_factory); sharing one across threads/processes corrupts state and raises `ProgrammingError`. Every repo/bus/route gets its own via dependency injection.
- **GOTCHA**: `isolation_level=None` means *autocommit mode*. Writers MUST explicitly `BEGIN IMMEDIATE` / `COMMIT`. This prevents the Python sqlite3 driver's implicit transaction management from fighting WAL.
- **GOTCHA**: `SENTINEL_DB_PATH` env override exists because `Path.home()` resolves differently on host vs. inside `sentinel-dev` (not bind-mounted). Ops workflows that must share a DB can point both at `/app/state/sentinel.db` (a mount the user adds).
- **GOTCHA — validate the override**. `SENTINEL_DB_PATH` is user-controlled; an attacker or misconfig can point it at `/dev/null`, a block device, or a symlink. Reject non-regular targets at startup:
  ```python
  import stat as _stat
  def get_db_path() -> Path:
      raw = os.environ.get("SENTINEL_DB_PATH")
      path = Path(raw).expanduser().resolve(strict=False) if raw else Path.home() / ".sentinel" / "sentinel.db"
      if path.exists() and not _stat.S_ISREG(path.stat().st_mode):
          raise RuntimeError(f"SENTINEL_DB_PATH must resolve to a regular file, got {path} (mode={path.stat().st_mode:o})")
      path.parent.mkdir(parents=True, exist_ok=True)
      return path
  ```
  Note `stat()` (not `lstat()`) — we follow symlinks and check the *target*; a symlink to a regular file is fine.
- **VALIDATE**: `python -c "from src.core.persistence.db import connect, ensure_initialized; ensure_initialized(); c = connect(); c.execute('SELECT 1').fetchone()"` — no exception, `~/.sentinel/sentinel.db` created.
- **VALIDATE**: `SENTINEL_DB_PATH=/dev/null python -c "from src.core.persistence.db import connect; connect()"` raises RuntimeError.

### Task 2 — CREATE `src/core/persistence/migrations/001_init.sql`

- **ACTION**: Paste schema from "Database Schema" section above.
- **VALIDATE**: `sqlite3 ~/.sentinel/sentinel.db ".schema"` shows all four tables.

### Task 3 — CREATE `src/core/events/types.py`

- **ACTION**: Pydantic models for every event in "Event Types" list + `TERMINAL_EVENT_TYPES` constant.
- **IMPLEMENT**:
  - Base class `SentinelEvent(BaseModel)` with fields:
    - `execution_id: str`
    - `ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))` — **tz-aware**, never naive `utcnow()` (deprecated in 3.12)
    - `agent: Optional[str] = None`
    - `type: str` (set by subclass via `Literal`)
  - Subclasses override `type` with `Literal["execution.started"]`, etc. Payload fields are direct attributes (no nested `payload` dict — the WS serializer in plan 03 re-nests non-envelope fields on the way out).
  - Discriminated union `AnyEvent = Annotated[Union[...], Field(discriminator="type")]` for typed field use; export `AnyEventAdapter = TypeAdapter(AnyEvent)` for **rehydrating events from DB rows**.
  - Constant: `TERMINAL_EVENT_TYPES: frozenset[str] = frozenset({"execution.completed","execution.failed","execution.cancelled"})`.
- **Persistence contract (critical):** `payload_json` stores the **full** `model_dump(mode="json")` — including `type`. Excluding `type` here would break `AnyEventAdapter.validate_python(json.loads(payload_json))` because the discriminator needs it. The envelope columns (`execution_id`, `seq`, `ts`, `agent`, `type`) duplicate some of what's in `payload_json`; the duplication is intentional (columns for queries, JSON for round-trip fidelity).
- **MIRROR**: pydantic v2 usage in `src/agents/*` (already v2 across codebase).
- **GOTCHA**: Event `type` strings are persisted — never rename. Additions are fine.
- **GOTCHA**: `datetime.utcnow()` is deprecated and returns naive datetimes; a naive/aware mix breaks comparisons. Always `datetime.now(timezone.utc)`.
- **VALIDATE**: `python -c "from src.core.events.types import ExecutionStarted, TERMINAL_EVENT_TYPES, AnyEventAdapter; import json; e = ExecutionStarted(execution_id='x', kind='execute', ticket_id='T-1', project='P'); dumped = e.model_dump_json(); rehydrated = AnyEventAdapter.validate_python(json.loads(dumped)); assert rehydrated.ts.tzinfo is not None and rehydrated.type == 'execution.started'"` — passes.

### Task 4 — CREATE `src/core/events/bus.py`

- **ACTION**: In-process pub/sub with persist-first semantics. Bus persists every event to SQLite *before* firing subscribers.
- **IMPLEMENT**:
  ```python
  class EventBus:
      def __init__(self, conn: sqlite3.Connection) -> None:
          self._conn = conn                       # caller-owned; one conn per bus instance
          self._subscribers: list[Callable[[SentinelEvent], None]] = []
          self._seq_lock = threading.Lock()       # serializes seq allocation within this process

      MAX_PAYLOAD_BYTES = 64 * 1024                     # 64 KiB hard cap per event

      def publish(self, event: SentinelEvent) -> None:
          payload = event.model_dump_json()
          if len(payload.encode("utf-8")) > self.MAX_PAYLOAD_BYTES:
              # Oversized payload (runaway agent response): rebuild a smaller valid JSON object.
              # Never byte-slice a JSON string — can split multibyte chars / escapes → invalid JSON.
              truncated = {**event.model_dump(mode="json"),
                           "_truncated": True,
                           "_original_bytes": len(payload.encode("utf-8"))}
              # Best-effort: shrink the biggest string field (usually a response dump).
              for k, v in list(truncated.items()):
                  if isinstance(v, str) and len(v) > 4096:
                      truncated[k] = v[:4096] + "…"
              payload = json.dumps(truncated, ensure_ascii=False)
              if len(payload.encode("utf-8")) > self.MAX_PAYLOAD_BYTES:
                  # Fallback: discard all string payload fields, keep envelope + marker only.
                  envelope_only = {"execution_id": event.execution_id,
                                   "type": event.type,
                                   "ts": event.ts.isoformat(),
                                   "agent": event.agent,
                                   "_truncated": True,
                                   "_reason": "oversize_after_shrink",
                                   "_original_bytes": truncated["_original_bytes"]}
                  payload = json.dumps(envelope_only)

          with self._seq_lock:
              self._conn.execute("BEGIN IMMEDIATE")
              try:
                  row = self._conn.execute(
                      "SELECT COALESCE(MAX(seq), 0) FROM events WHERE execution_id = ?",
                      (event.execution_id,),
                  ).fetchone()
                  seq = row[0] + 1
                  # Persist the FULL dump (including `type`) so AnyEventAdapter can rehydrate from payload_json alone.
                  # Envelope columns duplicate a few fields for query speed; source of truth is payload_json.
                  self._conn.execute(
                      "INSERT INTO events(execution_id, seq, ts, agent, type, payload_json) "
                      "VALUES (?, ?, ?, ?, ?, ?)",
                      (event.execution_id, seq, event.ts.isoformat(), event.agent,
                       event.type, payload),
                  )
                  self._conn.execute("COMMIT")
              except Exception:
                  self._conn.execute("ROLLBACK")
                  raise
          # Subscriber dispatch is OUTSIDE the lock — slow subscribers must not serialize publishers
          for sub in list(self._subscribers):
              try:
                  sub(event)
              except Exception:
                  logger.exception("event subscriber raised")

      def subscribe(self, cb: Callable[[SentinelEvent], None]) -> Callable[[], None]:
          self._subscribers.append(cb)
          def _unsub() -> None:
              try: self._subscribers.remove(cb)
              except ValueError: pass
          return _unsub
  ```
- **GOTCHA — subscriber exceptions MUST NOT bubble**. A dashboard-side bug cannot be allowed to crash a run.
- **GOTCHA — the `_seq_lock` is process-local**. Two processes (e.g. the service and a plan-04 subprocess worker) each hold their own bus with their own lock — but SQLite's `BEGIN IMMEDIATE` serializes the writes at the DB level, so `seq` stays monotonic per `execution_id` across processes. The in-memory subscriber list, however, is **not** cross-process. Plan 03's live WebSocket tail therefore reads from the DB, not via `subscribe()`. Keep bus subscription for local consumers only (e.g., the CLI's human-readable log adapter in Task 10).
- **GOTCHA — subscribers run on the publishing thread**. If a subscriber does real work it blocks other publishers waiting on the lock? No — lock is released before dispatch. But a slow subscriber still ties up the publishing thread. Subscribers that bridge to asyncio (none in plan 01; relevant for plan 03) must do only `loop.call_soon_threadsafe(...)` and return immediately.
- **GOTCHA — payload size cap**. `MAX_PAYLOAD_BYTES=65536` prevents a rogue agent response from filling the DB. On overflow we truncate the largest string field and mark `_truncated: true` — the dashboard can show a placeholder and a link to the full response (future: worker-log GET in plan 06).
- **VALIDATE**: `pytest tests/core/test_event_bus.py` — see Task 12.

### Task 5 — CREATE `src/core/execution/models.py`

- **ACTION**: `ExecutionStatus` (str enum: `queued | running | cancelling | succeeded | failed | cancelled` — **all six**, per §ExecutionStatus enum above) and `ExecutionKind` (str enum: `plan | execute | debrief`) and `Execution` pydantic model mirroring the `executions` columns.
- **VALIDATE**: `python -c "from src.core.execution.models import Execution, ExecutionStatus, ExecutionKind; assert ExecutionStatus.CANCELLING.value == 'cancelling'"`.

### Task 6 — CREATE `src/core/execution/repository.py`

- **ACTION**: CRUD + lifecycle transitions over `executions`, `events`, `agent_results`.
- **Constructor contract**: `ExecutionRepository(conn: sqlite3.Connection)` — caller owns the connection lifetime. Every repo instance is bound to one connection. **Never share a connection across threads** — FastAPI's `get_db_conn` creates one per request; the worker creates one; the supervisor takes a `connection_factory`, not a single conn (see plan 04).
- **Methods:**
  - `create(ticket_id, project, kind, *, options: dict | None = None, idempotency_key: str | None = None, idempotency_token_prefix: str | None = None) -> Execution` — `options` is shallow-written into `metadata_json` under key `options`; everything else keyed explicitly.
  - `get(id) -> Optional[Execution]`
  - `find_by_idempotency(token_prefix, key) -> Optional[Execution]` — token-scoped; `(token_prefix, key)` is the unique tuple
  - `list(*, project=None, ticket_id=None, status=None, kind=None, before=None, limit=50) -> list[Execution]`
  - `set_status(id, status, error=None)` / `set_phase(id, phase)`
  - `add_cost(id, cents)` — atomic `UPDATE executions SET cost_cents = cost_cents + ?`
  - `record_agent_result(id, agent, result_dict)` / `list_agent_results(id) -> list[dict]`
  - `record_ended(id, status, error=None)` — sets `ended_at = now`, `status`, and `error`
  - `iter_events(execution_id, since_seq=0, limit=500) -> Iterator[EventRow]` — yields `EventRow` objects
  - `latest_event_seq(execution_id) -> int`
  - `mark_metadata(id, **kv)` — shallow-merges into `metadata_json`; used for `retry_of`, `compose_projects`, `post_mortem_complete`
- **`EventRow` shape** (stable contract; plan 02 and plan 03 both consume):
  ```python
  class EventRow(TypedDict):
      seq: int
      ts: str              # ISO-8601 string from DB; consumers parse if they need datetime
      agent: Optional[str]
      type: str
      payload: dict        # already-parsed from payload_json (not the raw string)
  ```
  `iter_events` does the `json.loads(payload_json)` itself — consumers never touch the raw string.
- **GOTCHA**: Always write `metadata_json` as JSON string, never None — schema default is `'{}'` but code should be explicit.
- **GOTCHA**: Writers wrap multi-statement operations in `BEGIN IMMEDIATE` / `COMMIT`. Readers don't need transactions (WAL snapshot isolation).
- **GOTCHA — `find_by_idempotency` semantics**: returns the existing row regardless of its terminal status. A POST with a previously-used `(token_prefix, key)` does NOT re-run a failed execution; caller uses `POST /executions/{id}/retry` for that.
- **VALIDATE**: `pytest tests/core/test_execution_repository.py`.

### Task 7 — CREATE `src/core/execution/orchestrator.py`

- **ACTION**: Lift orchestration from `cli.execute` (cli.py:478–1009), `cli.plan` (cli.py:89–181), `cli.debrief` (cli.py:197–280) into three methods on `Orchestrator`.
- **IMPLEMENT**:
  - `__init__(self, repo, bus, session_tracker, config)`:
    - Register `_cost_subscriber`: `self._bus.subscribe(lambda e: self._repo.add_cost(e.execution_id, e.cents) if e.type == "cost.accrued" else None)`.
    - This is the **one mandatory Orchestrator subscriber**; others are optional (CLI log adapter, dashboard notifier).
  - `plan(ticket_id, project, **opts) -> Execution`
  - `execute(ticket_id, project, **opts) -> Execution`
  - `debrief(ticket_id, project, **opts) -> Execution`
  - Each method: (a) `repo.create(kind=...)`; (b) publish `ExecutionStarted`; (c) instantiate agents **passing `event_bus=self.bus` and `execution_id=exec.id`**; (d) call agents as the CLI does today; (e) `repo.record_agent_result` after each agent; (f) `repo.set_phase` between phases; (g) on exception → `repo.record_ended(failed, error=str(e))` + publish `ExecutionFailed` + re-raise; (h) on success → `repo.record_ended(succeeded)` + `ExecutionCompleted`.
- **GOTCHA**: The existing CLI flows do a lot of incidental work (Jira comments, GitLab MR updates, container setup). Do NOT reimplement those here — keep them as methods on Orchestrator OR helper functions it calls. This plan is a move, not a redesign.
- **GOTCHA**: Re-raising after marking failed is important so the CLI's non-zero exit behaviour is preserved.
- **VALIDATE**: `pytest tests/core/test_orchestrator.py`.

### Task 8 — UPDATE `src/agents/base_agent.py`

- **ACTION**: Add optional event plumbing.
- **IMPLEMENT**:
  - Add constructor kwargs `event_bus: Optional["EventBus"] = None`, `execution_id: Optional[str] = None`. Store on `self`.
  - In `_send_message_async` (base_agent.py:129-180), around the existing `logger.info` calls at 153 and 166, call `self._emit(AgentMessageSent(...))` and `self._emit(AgentResponseReceived(...))`.
  - Add `_emit(event)` helper that no-ops when `event_bus is None`.
- **MIRROR**: Keep logger.info calls exactly as-is — events are additive.
- **GOTCHA**: Existing tests (`tests/test_base_agent.py`) construct agents without the new kwargs — defaults must make this a no-op.
- **VALIDATE**: `pytest tests/test_base_agent.py` — must still pass unchanged.

### Task 9 — UPDATE `src/agent_sdk_wrapper.py`

- **ACTION**: When Orchestrator passes an event bus through to agents, publish `ToolCalled`, `CostAccrued`, and (on throttle errors) `RateLimited` events — in addition to `_write_diagnostic`. Do not remove the diagnostic file.
- **IMPLEMENT**:
  - Accept `event_bus` + `execution_id` as optional attributes set by BaseAgent after construction (keeps the existing `AgentSDKWrapper(agent_name, config)` signature stable).
  - On each SDK tool_use: publish `ToolCalled(tool, args_summary)`.
  - On each SDK response with usage info: publish `CostAccrued(tokens_in, tokens_out, cents)`. Compute cents using the project's existing cost table if present; otherwise `cents=0` and only the token counts are carried (still observable).
  - On 429/529 / rate-limit exception: publish `RateLimited(retry_after_s)` before re-raising or backing off.
  - **Single `entry_dict()` helper MUST back both `_write_diagnostic` JSONL output and `bus.publish`.** Helper lives in `src/agent_sdk_wrapper.py` (same module already owns `_write_diagnostic`). Both paths call it; no drift possible by construction. Named test in Task 11: `tests/test_agent_sdk_wrapper.py::test_entry_dict_jsonl_bus_parity` — builds one event, asserts the JSONL line and the `events.payload_json` row decode to the same dict.
- **GOTCHA — cost as a column needs a writer**. `executions.cost_cents` is useless without `CostAccrued` events flowing; an Orchestrator-side subscriber does `repo.add_cost(execution_id, cents)` for every one. Wire that in Task 7: Orchestrator `__init__` registers `bus.subscribe(_cost_subscriber)`.
- **VALIDATE**: Existing diagnostics.jsonl output unchanged; new `ToolCalled` / `CostAccrued` rows appear in `events` when executed via Orchestrator; `SELECT SUM(cost_cents) FROM executions` > 0 after a real run; `tests/test_agent_sdk_wrapper.py::test_entry_dict_jsonl_bus_parity` passes.

### Task 10 — UPDATE `src/cli.py` (plan / execute / debrief)

- **ACTION**: Replace inline orchestration with Orchestrator calls.
- **IMPLEMENT** (shape, for each of the three commands):
  ```python
  from src.core.execution import Orchestrator, ExecutionRepository
  from src.core.events import EventBus
  from src.core.persistence import connect, ensure_initialized

  # inside the Click command:
  ensure_initialized()                       # idempotent; runs migrations first time
  conn = connect()                           # caller-owned connection
  repo = ExecutionRepository(conn)
  bus = EventBus(conn)
  orchestrator = Orchestrator(repo=repo, bus=bus, session_tracker=SessionTracker(), config=get_config())
  try:
      execution = orchestrator.execute(ticket_id=ticket, project=project, revise=revise, ...)
  finally:
      conn.close()
  if execution.status is ExecutionStatus.FAILED:
      raise click.ClickException(execution.error or "execution failed")
  ```
- **GOTCHA**: `cli.execute` contains at least two major branches (`--revise` vs. normal, starting ~line 509 and ~line 722). Both must route through Orchestrator — do not leave one branch inline. The `--revise` flow reuses the same execution row (or creates a linked child with `metadata_json.revise_of = <original>`) — pick one and document on the `Execution` model.
- **GOTCHA**: `debrief`'s interactive Jira conversation loop is a single Execution with multiple `DebriefTurn` events (add to event types if driven from the dashboard; for plan 01 the CLI drives turns and emits them).
- **GOTCHA**: Preserve existing stdout output for humans — subscribe a simple `_log_subscriber(event)` on the bus that prints a one-line summary per event when running interactively.
- **VALIDATE**: Manual smoke test — `sentinel execute <known-ticket>` completes end-to-end on a fixture project; `SELECT * FROM executions ORDER BY started_at DESC LIMIT 1` shows the run; `SELECT COUNT(*) FROM events WHERE execution_id = ?` > 0.

### Task 11 — CREATE tests

Files: `tests/core/test_persistence.py`, `tests/core/test_event_bus.py`, `tests/core/test_execution_repository.py`, `tests/core/test_orchestrator.py`.

- **MIRROR**: fixture style from `tests/test_session_tracker.py:27-75` (tmp_path + monkeypatch), mocking style from `tests/test_base_agent.py:1-100`.
- **Coverage targets**:
  - persistence: migration idempotency (run twice, schema_migrations has one row), schema shape.
  - event_bus: persist-first (row exists when subscriber raises), seq monotonic, concurrent publish safety.
  - repository: full create→running→succeeded lifecycle, status filters on list, agent_results insert.
  - orchestrator: happy path with mocked agents emits `ExecutionStarted`+`ExecutionCompleted`; exception path emits `ExecutionFailed` and marks row failed; re-raise preserved.
- **VALIDATE**: `pytest tests/core/ -v` — all pass.

---

## Testing Strategy

### Unit Tests to Write

| File | Cases | Validates |
|---|---|---|
| `tests/core/test_persistence.py` | Migration runs once; schema present; WAL enabled | db.py |
| `tests/core/test_event_bus.py` | Persist-first; subscriber exception swallowed; seq monotonic | bus.py |
| `tests/core/test_execution_repository.py` | Lifecycle; list filters; record_agent_result JSON round-trip | repository.py |
| `tests/core/test_orchestrator.py` | Happy path events; failure path events + row marked failed; re-raise | orchestrator.py |

### Regression Check

| Existing file | Must still pass | Why at risk |
|---|---|---|
| `tests/test_base_agent.py` | Y | BaseAgent signature extended |
| `tests/test_session_tracker.py` | Y | SessionTracker untouched structurally |

### Edge Cases

- [ ] CLI invoked with no `~/.sentinel/` dir (fresh install) — DB created, migrations run, execution proceeds
- [ ] CLI invoked while another run is writing — WAL lets readers proceed; writers serialize
- [ ] Orchestrator crash mid-run — `executions.status = 'running'` left behind; plan 04 reconciles, plan 01 just documents this as known
- [ ] Agent raises — `ExecutionFailed` published *and* row marked failed *and* exception re-raised to CLI
- [ ] Subscriber raises — event still persisted; other subscribers still fire; run continues
- [ ] Legacy `~/.sentinel/sessions.json` exists — SessionTracker behaviour unchanged; new DB coexists

---

## Validation Commands

### Level 1 — Static analysis
```bash
poetry run ruff check src/core tests/core || poetry run flake8 src/core tests/core
poetry run mypy src/core   # if mypy is configured; skip if not
```

### Level 2 — Unit tests
```bash
poetry run pytest tests/core -v
poetry run pytest tests/test_base_agent.py tests/test_session_tracker.py -v   # regression
```

### Level 3 — Full suite
```bash
poetry run pytest -x
```

### Level 4 — Manual smoke (must be run in `sentinel-dev` container, not this sandbox)
```bash
# fresh start
rm -f ~/.sentinel/sentinel.db
sentinel execute <known-good-ticket>

# verify
sqlite3 ~/.sentinel/sentinel.db \
  "SELECT id, ticket_id, kind, status, started_at FROM executions ORDER BY started_at DESC LIMIT 3;"
sqlite3 ~/.sentinel/sentinel.db \
  "SELECT type, COUNT(*) FROM events GROUP BY type;"
```
Expect: one `succeeded` execution row, non-zero counts for `execution.started`, `agent.message_sent`, `tool.called`, `execution.completed`.

---

## Acceptance Criteria

- [ ] `sentinel execute <ticket>` produces the same end-user artefacts (MR, Jira comments, worktree state) as before
- [ ] A row exists in `executions` with correct `status`, `started_at`, `ended_at`, `cost_cents` after each run
- [ ] `events` rows captured for every agent message/response and tool call
- [ ] Failure path writes `status='failed'` and surfaces a non-zero CLI exit
- [ ] `tests/core/` pass; existing tests still pass unchanged
- [ ] No new runtime dependencies in `pyproject.toml`
- [ ] `logs/agent_diagnostics.jsonl` still written (don't break sentinel-dev operator tail)

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Extraction from `cli.py` misses an incidental side-effect (e.g. a specific Jira comment) | MED | MED | Extract by *moving* helpers and calling them from Orchestrator rather than rewriting; checklist existing MR/Jira interactions from cli.py:478-1009 before approving |
| SQLite `database is locked` under concurrent writes (e.g. plan 04 worker + plan 02 read endpoints + event bus commits) | MED | MED | `PRAGMA busy_timeout=30000`, `BEGIN IMMEDIATE` for writers, WAL for concurrent readers, `check_same_thread=False` on all connections, *no* module-level shared connection |
| Shared DB ambiguity: `Path.home()` differs host vs. sentinel-dev container (host HOME not bind-mounted) | MED | LOW | `SENTINEL_DB_PATH` env override documented; operators who need a shared DB point both at a mounted path |
| Event schema churn breaks replay after real data accumulates | MED | MED | Event `type` strings are now stable identifiers; any change ships with a migration that rewrites payload_json or adds a new type rather than renaming |
| Added constructor kwarg on BaseAgent breaks third-party agents | LOW | LOW | Kwargs default to `None`; existing call sites unchanged |
| `~/.sentinel/sentinel.db` ends up committed if a user runs from the repo root | LOW | LOW | Path is under `$HOME`, not cwd; `.gitignore` already covers `*.db`; verify once |

---

## Notes

- This plan deliberately keeps two parallel telemetry sinks alive (`logger.info` + `logs/agent_diagnostics.jsonl` + new `events` table). Collapse is a later decision — today operators rely on tailing the jsonl file in `sentinel-dev`, and we shouldn't trade a working workflow for a cleaner diagram. A single `entry_dict()` helper backs both so the shapes cannot drift.
- `cli.py` is 2500 lines. Extract narrowly. Anything that isn't orchestration (projects, auth, validate, info, reset, status) stays put.
- **HOME ambiguity:** `~/.sentinel/sentinel.db` resolves to the container HOME inside `sentinel-dev` and to the host HOME outside. Running `sentinel` in both places creates two independent DBs. If sharing is required, set `SENTINEL_DB_PATH` (e.g. to a path under the `sentinel-projects` volume) in both environments.
- **Retention:** `events` and `agent_results` grow unbounded. `idx_events_ts` is in place; a retention-sweep plan is a follow-up, not a blocker.
- **Out-of-process executions (plan 04):** the bus subscriber list is process-local. Consumers that need events from subprocess workers (plan 03's WebSocket tail) read from the DB, not via `subscribe()`.
- Commit the work on `experimental/command-center-01-foundation` per session branch rule.
