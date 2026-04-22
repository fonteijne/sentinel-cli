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
| Estimated Tasks  | 11 |

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
| `src/session_tracker.py` | UPDATE | No structural change; add a migration helper `migrate_legacy_into_db()` called once on first DB open (non-destructive: legacy JSON remains) |

---

## Database Schema (migration 001_init.sql)

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
    id            TEXT PRIMARY KEY,                -- uuid4 hex
    ticket_id     TEXT NOT NULL,
    project       TEXT NOT NULL,
    kind          TEXT NOT NULL,                   -- 'plan' | 'execute' | 'debrief'
    status        TEXT NOT NULL,                   -- see ExecutionStatus enum
    phase         TEXT,                            -- current agent/step label
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    cost_cents    INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_executions_ticket ON executions(ticket_id);
CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);
CREATE INDEX IF NOT EXISTS idx_executions_started ON executions(started_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id  TEXT NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,                -- monotonic per execution
    ts            TEXT NOT NULL,                   -- ISO-8601 UTC
    agent         TEXT,                            -- nullable (system events)
    type          TEXT NOT NULL,                   -- event type name
    payload_json  TEXT NOT NULL,
    UNIQUE(execution_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_execution ON events(execution_id, seq);

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

Each is a pydantic model with `execution_id`, `ts`, `agent` (opt), and a typed payload. Minimum set for this plan:

- `ExecutionStarted` — `{kind, ticket_id, project}`
- `ExecutionCompleted` — `{status, cost_cents}`
- `ExecutionFailed` — `{error}`
- `PhaseChanged` — `{phase}` (e.g. "planning", "implementing", "reviewing")
- `AgentStarted` / `AgentFinished` — `{agent, session_id}`
- `AgentMessageSent` — `{prompt_chars, cwd, max_turns}`
- `AgentResponseReceived` — `{response_chars, tool_uses_count, elapsed_s}`
- `ToolCalled` — `{tool, args_summary}`
- `TestResultRecorded` — `{success, return_code}`
- `FindingPosted` — `{severity, summary}`

### ExecutionStatus enum

`queued | running | succeeded | failed | cancelled`

---

## NOT Building (Scope Limits)

- **HTTP / WebSocket** — plans 02 and 03.
- **Out-of-process workers** — plan 04. Foundation runs Orchestrator in-process from the CLI; a crash kills the run the same as today.
- **Auth / token / network binding** — plan 05.
- **Replacing SessionTracker** — keep it; new DB supplements.
- **Replacing logger calls with events** — events are *added alongside*, existing `logger.info(...)` calls stay.
- **Replacing `logs/agent_diagnostics.jsonl`** — keep it (sentinel-dev operators rely on tailing this file during development). Event bus publishes the same shape to the DB.
- **Migrating `beads_manager.py`** — untouched.
- **Changing agent behaviour** — no agent output changes.

---

## Step-by-Step Tasks

Execute in order. Each task is independently testable.

### Task 1 — CREATE `src/core/persistence/db.py`

- **ACTION**: Create SQLite connection helper and migration runner.
- **IMPLEMENT**:
  - `DB_PATH = Path.home() / ".sentinel" / "sentinel.db"` (mirror SessionTracker convention — create parent dir)
  - `get_connection() -> sqlite3.Connection` — returns connection with `row_factory=sqlite3.Row`, `PRAGMA foreign_keys=ON`, `PRAGMA journal_mode=WAL`
  - `run_migrations(conn)` — reads `migrations/*.sql` in sorted filename order, executes any whose `version` (leading digits) is not in `schema_migrations`; records applied versions with UTC ISO timestamp
  - Module-level singleton via `get_db()` mirroring `get_config` pattern (see `src/config_loader.py:431-441`)
- **MIRROR**: `src/session_tracker.py:19-20` for path convention; `src/config_loader.py:431-441` for singleton.
- **GOTCHA**: WAL mode needs write permission on the directory — `parent.mkdir(parents=True, exist_ok=True)` is required, same as SessionTracker does.
- **VALIDATE**: `python -c "from src.core.persistence.db import get_db; get_db()"` — no exception, `~/.sentinel/sentinel.db` created.

### Task 2 — CREATE `src/core/persistence/migrations/001_init.sql`

- **ACTION**: Paste schema from "Database Schema" section above.
- **VALIDATE**: `sqlite3 ~/.sentinel/sentinel.db ".schema"` shows all four tables.

### Task 3 — CREATE `src/core/events/types.py`

- **ACTION**: Pydantic models for every event in "Event Types" list.
- **IMPLEMENT**:
  - Base class `SentinelEvent(BaseModel)` with fields: `execution_id: str`, `ts: datetime` (default_factory utcnow), `agent: Optional[str] = None`, `type: str` (set by subclass via `Literal`).
  - Subclasses override `type` with `Literal["execution.started"]`, etc. Payload fields are direct attributes (no nested `payload` dict).
  - Discriminated union `AnyEvent = Annotated[Union[...], Field(discriminator="type")]` for serialization.
- **MIRROR**: pydantic usage in `src/agents/*` (already v2 across codebase).
- **GOTCHA**: Event `type` strings must be stable — they're persisted; renaming breaks replay.
- **VALIDATE**: `python -c "from src.core.events.types import ExecutionStarted; ExecutionStarted(execution_id='x', kind='execute', ticket_id='T-1', project='P')"` parses.

### Task 4 — CREATE `src/core/events/bus.py`

- **ACTION**: In-process pub/sub with persist-first semantics.
- **IMPLEMENT**:
  ```python
  class EventBus:
      def __init__(self, conn: sqlite3.Connection) -> None:
          self._conn = conn
          self._subscribers: list[Callable[[SentinelEvent], None]] = []
          self._seq_lock = threading.Lock()

      def publish(self, event: SentinelEvent) -> None:
          with self._seq_lock:
              seq = self._next_seq(event.execution_id)
              self._conn.execute(
                  "INSERT INTO events(execution_id, seq, ts, agent, type, payload_json) "
                  "VALUES (?, ?, ?, ?, ?, ?)",
                  (event.execution_id, seq, event.ts.isoformat(), event.agent,
                   event.type, event.model_dump_json(exclude={"execution_id","ts","agent","type"})),
              )
              self._conn.commit()
          for sub in list(self._subscribers):
              try: sub(event)
              except Exception:  # never let a subscriber take down a run
                  logger.exception("event subscriber raised")

      def subscribe(self, cb): self._subscribers.append(cb); return lambda: self._subscribers.remove(cb)
  ```
- **GOTCHA**: Subscriber exceptions MUST NOT bubble — they'd crash the run for a dashboard bug. Log and continue.
- **GOTCHA**: `_seq_lock` is required because multiple agents in the same process may publish concurrently. Keep SQLite in WAL so reads from plan 02 don't block writes.
- **VALIDATE**: `pytest tests/core/test_event_bus.py` — see Task 12.

### Task 5 — CREATE `src/core/execution/models.py`

- **ACTION**: `ExecutionStatus` (str enum: queued/running/succeeded/failed/cancelled) and `Execution` pydantic model mirroring the `executions` columns.
- **VALIDATE**: `python -c "from src.core.execution.models import Execution, ExecutionStatus"`.

### Task 6 — CREATE `src/core/execution/repository.py`

- **ACTION**: CRUD + lifecycle transitions over `executions` and `agent_results`.
- **IMPLEMENT**: `create(ticket_id, project, kind) -> Execution`, `get(id)`, `list(filters)`, `set_status(id, status, error=None)`, `set_phase(id, phase)`, `add_cost(id, cents)`, `record_agent_result(id, agent, result_dict)`, `record_ended(id, status, error=None)`.
- **GOTCHA**: Always write `metadata_json` as JSON string, never None — schema default is `'{}'` but code should be explicit.
- **VALIDATE**: `pytest tests/core/test_execution_repository.py`.

### Task 7 — CREATE `src/core/execution/orchestrator.py`

- **ACTION**: Lift orchestration from `cli.execute` (cli.py:478–1009), `cli.plan` (cli.py:89–181), `cli.debrief` (cli.py:197–280) into three methods on `Orchestrator`.
- **IMPLEMENT**:
  - `__init__(self, repo, bus, session_tracker, config)`
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

- **ACTION**: When Orchestrator passes an event bus through to agents, have the wrapper publish `ToolCalled` events (in addition to `_write_diagnostic` — do not remove the diagnostic file).
- **IMPLEMENT**: Accept `event_bus` + `execution_id` via the BaseAgent that owns this wrapper (thread them through), or attach them as optional attributes set by BaseAgent after construction. Prefer the latter to keep the existing `AgentSDKWrapper(agent_name, config)` signature stable.
- **VALIDATE**: Existing diagnostics.jsonl output unchanged; new `ToolCalled` rows appear in `events` when executed via Orchestrator.

### Task 10 — UPDATE `src/cli.py` (plan / execute / debrief)

- **ACTION**: Replace inline orchestration with Orchestrator calls.
- **IMPLEMENT** (shape, for each of the three commands):
  ```python
  from src.core.execution import Orchestrator, ExecutionRepository
  from src.core.events import EventBus
  from src.core.persistence import get_db

  # inside the Click command:
  conn = get_db()
  repo = ExecutionRepository(conn)
  bus = EventBus(conn)
  orchestrator = Orchestrator(repo=repo, bus=bus, session_tracker=SessionTracker(), config=get_config())
  execution = orchestrator.execute(ticket_id=ticket, project=project, revise=revise, ...)
  if execution.status is ExecutionStatus.FAILED:
      raise click.ClickException(execution.error or "execution failed")
  ```
- **GOTCHA**: `cli.execute` contains at least two major branches (`--revise` vs. normal, lines 509-720 vs 722+). Both must route through Orchestrator — do not leave one branch inline.
- **GOTCHA**: Preserve existing stdout output for humans — the CLI's printed messages should not regress. Subscribe a simple `_log_subscriber(event)` on the bus that prints a one-line summary per event when running interactively.
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
| SQLite write contention if two `sentinel` processes run concurrently | LOW | LOW | WAL mode + short transactions handle this; single-writer-per-run is the norm; plan 04 adds supervisor-coordinated workers |
| Event schema churn breaks replay after real data accumulates | MED | MED | Event `type` strings are now stable identifiers; any change ships with a migration that rewrites payload_json or adds a new type rather than renaming |
| Added constructor kwarg on BaseAgent breaks third-party agents | LOW | LOW | Kwargs default to `None`; existing call sites unchanged |
| `~/.sentinel/sentinel.db` ends up committed if a user runs from the repo root | LOW | LOW | Path is under `$HOME`, not cwd; `.gitignore` already covers `*.db`; verify once |

---

## Notes

- This plan deliberately keeps two parallel telemetry sinks alive (`logger.info` + `logs/agent_diagnostics.jsonl` + new `events` table). Collapse is a later decision — today operators rely on tailing the jsonl file in `sentinel-dev`, and we shouldn't trade a working workflow for a cleaner diagram.
- `cli.py` is 2500 lines. Extract narrowly. Anything that isn't orchestration (projects, auth, validate, info, reset, status) stays put.
- Commit the work on `experimental/command-center-01-foundation` per session branch rule.
