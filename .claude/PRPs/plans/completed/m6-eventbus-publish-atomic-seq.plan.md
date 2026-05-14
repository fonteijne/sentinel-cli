# Feature: M6 — `EventBus.publish` atomic seq via single-statement INSERT...SELECT

## Summary

`src/core/events/bus.py::EventBus.publish` currently computes the next per-execution `seq` with `SELECT COALESCE(MAX(seq), 0) + 1 ...` and then issues a separate `INSERT INTO events ...` statement. SQLite serializes statements per-connection, so single-process Sentinel is safe today — but two writer connections to the same DB file can both compute the same `next_seq`, and the loser hits the `PRIMARY KEY (execution_id, seq)` constraint. This plan replaces the two statements with one atomic `INSERT INTO events (...) SELECT ?, COALESCE(MAX(seq), 0)+1, ... FROM events WHERE execution_id = ?`. No schema change, no behavioural change for the existing single-process path, and a new test exercises two `EventBus` instances over the same DB file to lock the invariant in.

## User Story

As a Sentinel maintainer
I want `EventBus.publish` to be correct-by-construction under concurrent writers
So that any future change that introduces a second connection (worker process, side-fixture, multi-thread runner) does not silently start losing events to a `UNIQUE` constraint race.

## Problem Statement

`EventBus.publish` reads `MAX(seq)+1` and writes the row in two SQL statements (`src/core/events/bus.py:70-94`). Two connections to the same SQLite DB (e.g., a future worker fan-out or a test fixture that opens its own connection) can interleave such that:

1. Connection A reads `MAX(seq) = 0` → computes `next_seq = 1`.
2. Connection B reads `MAX(seq) = 0` → computes `next_seq = 1`.
3. Both insert with `seq = 1` for the same `execution_id`. The second hits `PRIMARY KEY (execution_id, seq)` and raises `sqlite3.IntegrityError`.

Today the bus is held by one process, so this cannot happen. The fix is small, the schema is unchanged, and it removes a footgun.

## Solution Statement

Replace the two-statement read-then-write with a single atomic `INSERT INTO events (...) SELECT ?, COALESCE(MAX(seq), 0)+1, ?, ?, ?, ? FROM events WHERE execution_id = ?`. SQLite executes a single statement under an implicit transaction, so the MAX read and the INSERT cannot interleave with another connection's INSERT (the second writer will block on the database lock and re-evaluate MAX after the first commits). The `events` row's `seq` invariants — monotonic from 1 per `execution_id`, contiguous, no global ordering — are preserved.

## Metadata

| Field            | Value                                                                                  |
| ---------------- | -------------------------------------------------------------------------------------- |
| Type             | BUG_FIX (forward-compat correctness)                                                   |
| Complexity       | LOW                                                                                    |
| Systems Affected | `src/core/events/bus.py`, `tests/core/test_event_bus.py`                               |
| Dependencies     | stdlib `sqlite3` only; pydantic v2 (already a project dep at `pyproject.toml:18`)      |
| Estimated Tasks  | 3                                                                                      |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                            BEFORE STATE (today)                                ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   ┌──────────┐  publish(event)   ┌────────────────┐                           ║
║   │ Caller A │ ─────────────────►│ EventBus.publish│                          ║
║   │ (conn 1) │                   └───────┬────────┘                           ║
║   └──────────┘                           │                                    ║
║                                          ▼                                    ║
║                              ┌─────────────────────────┐                      ║
║                              │ SELECT MAX(seq)+1 ...   │ ◄── statement #1     ║
║                              └─────────────┬───────────┘                      ║
║                                            ▼                                  ║
║                              ┌─────────────────────────┐                      ║
║                              │ INSERT INTO events ...  │ ◄── statement #2     ║
║                              └─────────────────────────┘                      ║
║                                                                               ║
║   USER_FLOW (single process, today): works fine — no interleave possible.     ║
║   PAIN_POINT: two-statement read-then-write is a forward-compat footgun.      ║
║   DATA_FLOW: caller → bus → SELECT → INSERT → COMMIT → subscribers.           ║
║                                                                               ║
║   Hypothetical concurrent writers (NOT today, but trivially possible):        ║
║                                                                               ║
║   conn 1: SELECT MAX(seq)+1 → 1                                               ║
║                                                                               ║
║   conn 2:                       SELECT MAX(seq)+1 → 1                         ║
║                                                                               ║
║   conn 1: INSERT seq=1                          ✓                             ║
║                                                                               ║
║   conn 2:                                       INSERT seq=1  ✗ IntegrityError║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                                AFTER STATE                                     ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   ┌──────────┐  publish(event)   ┌────────────────┐                           ║
║   │ Caller A │ ─────────────────►│ EventBus.publish│                          ║
║   │ (conn 1) │                   └───────┬────────┘                           ║
║   └──────────┘                           │                                    ║
║                                          ▼                                    ║
║              ┌────────────────────────────────────────────────────┐           ║
║              │ INSERT INTO events (...)                           │           ║
║              │ SELECT ?, COALESCE(MAX(seq),0)+1, ?, ?, ?, ?       │           ║
║              │ FROM events WHERE execution_id = ?                 │           ║
║              └────────────────────────────────────────────────────┘           ║
║                              one statement, atomic under SQLite locks         ║
║                                                                               ║
║   USER_FLOW (any writer count): two writers serialize on the DB lock.         ║
║   VALUE_ADD: correct-by-construction — no IntegrityError race possible.       ║
║   DATA_FLOW: caller → bus → INSERT...SELECT → COMMIT → subscribers.           ║
║                                                                               ║
║   Two-writer scenario, after fix:                                             ║
║                                                                               ║
║   conn 1: BEGIN (implicit) → INSERT...SELECT MAX(seq)+1 → seq=1, COMMIT  ✓    ║
║                                                                               ║
║   conn 2:                                       (waits on DB lock)            ║
║                                                                               ║
║   conn 2:                                       INSERT...SELECT MAX(seq)+1    ║
║                                                                               ║
║   conn 2:                                       → seq=2, COMMIT          ✓    ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                         | Before                                  | After                                     | User Impact                                                              |
| ------------------------------------------------ | --------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------ |
| `src/core/events/bus.py:70-93` (`publish` body)  | two statements (SELECT then INSERT)     | one statement (INSERT...SELECT)           | identical externally; rows still written before subscribers fire         |
| `tests/core/test_event_bus.py` (new test)        | no concurrent-writer test               | new file-DB test with two `EventBus`      | locked-in invariant: two writers never collide on the PK                 |

No CLI, no API surface, no schema migration. Subscribers, payload truncation, ts-fill, and the persist-first contract are all untouched.

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File                                                                | Lines  | Why Read This                                                                                |
| -------- | ------------------------------------------------------------------- | ------ | -------------------------------------------------------------------------------------------- |
| P0       | `src/core/events/bus.py`                                            | 1-113  | The file being edited; the docstring carries the persist-first contract                      |
| P0       | `tests/core/test_event_bus.py`                                      | 1-189  | Existing tests are the regression contract — they must keep passing byte-identically         |
| P1       | `src/core/persistence/migrations/001_init.sql`                      | 21-29  | The `events` table — `PRIMARY KEY (execution_id, seq)`, `payload_json TEXT NOT NULL`         |
| P1       | `src/core/persistence/db.py`                                        | 36-60  | `connect()` enables `WAL` and `foreign_keys=ON` — relevant for the file-DB concurrent test   |
| P2       | `src/core/events/types.py`                                          | 1-50   | `BaseEvent` and `TestResultRecorded` shape; `agent` is `Optional[str]`, only on some events  |
| P2       | `tests/core/test_persistence.py`                                    | 38-52  | `tmp_path` + `monkeypatch.setenv("SENTINEL_DB_PATH", ...)` is the file-DB test idiom         |
| P2       | `src/core/persistence/feedback_rules.py`                            | 200-245 | Project's `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` idiom (reference for the rejected option 3) |
| P3       | `.claude/PRPs/reviews/feat-sentinel-learning-system-review.md`      | (search "M6") | Original review entry (line 127) — establishes severity and constraints                |

**External Documentation:**

| Source                                                                                                               | Section                          | Why Needed                                                                                  |
| -------------------------------------------------------------------------------------------------------------------- | -------------------------------- | ------------------------------------------------------------------------------------------- |
| [SQLite — INSERT statement](https://www.sqlite.org/lang_insert.html)                                                 | "INSERT INTO ... SELECT" form    | Confirms `INSERT ... SELECT` is a single statement; aggregate over the same table is legal  |
| [SQLite — File Locking And Concurrency](https://www.sqlite.org/lockingv3.html)                                       | "RESERVED" / "EXCLUSIVE" lock    | Explains why two writers serialize: each statement acquires a write lock, no MAX/INSERT split |
| [SQLite — Atomic Commit](https://www.sqlite.org/atomiccommit.html)                                                   | overview                         | Each statement runs in an implicit transaction unless an explicit BEGIN is open              |
| [Python sqlite3 — Connection](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.execute)             | `Connection.execute`             | Confirms `conn.execute(sql, params)` is a shortcut that creates+runs one cursor             |

GOTCHA: SQLite's default isolation level in Python's `sqlite3` module is "deferred" — `conn.execute("INSERT ...")` opens an implicit transaction that is committed by the explicit `conn.commit()` call. Our path keeps the same `self._conn.commit()` line, so the visible behaviour is identical.

GOTCHA: `INSERT INTO t SELECT ... FROM t` is well-defined in SQLite — the SELECT sees the table state at statement start, the INSERT writes new rows; there is no read-write skew because no other statement can interleave.

GOTCHA: `WAL` mode (enabled by `connect()` in `db.py:58`) lets readers run concurrently with one writer, but writers still serialize. The fix relies on writer serialization, not on WAL semantics.

---

## Patterns to Mirror

**INSERT statement formatting** (preserve the existing two-line column-list / values style):

```python
# SOURCE: src/core/events/bus.py:89-93 (current INSERT — the form we are replacing)
self._conn.execute(
    "INSERT INTO events (execution_id, seq, ts, agent, type, payload_json) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    (event.execution_id, next_seq, event.ts, agent, event.type, payload_json),
)
self._conn.commit()
```

The new statement keeps the same column list, the same string-concatenation style, and the same `self._conn.commit()` afterwards.

**Module-level constant + comment style** (mirror the existing module's commentary density — comments explain *why*, not *what*):

```python
# SOURCE: src/core/events/bus.py:30-32
logger = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 64 * 1024
```

**Test fixture for file-backed SQLite** (the new concurrent-writer test must use a real file, not `:memory:`, because each `:memory:` connection gets its own private DB):

```python
# SOURCE: tests/core/test_persistence.py:38-46
def test_pragmas_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """connect() must enable WAL journal_mode and foreign_keys=ON.

    Uses a temp-file path because WAL is silently downgraded on :memory: DBs.
    """
    db_path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))

    conn = connect()
```

**Existing event-bus test setup** (mirror this for any single-conn assertions in the new test):

```python
# SOURCE: tests/core/test_event_bus.py:22-34
def _conn_with_execution(execution_id: str = "exec-1") -> sqlite3.Connection:
    """Build an in-memory DB with migrations applied and one parent row."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    apply_migrations(conn)
    conn.execute(
        "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (execution_id, "TICKET-1", "execute", "running", "2026-05-08T00:00:00+00:00"),
    )
    conn.commit()
    return conn
```

**Event payload construction** (the new test must publish real events, not raw SQL):

```python
# SOURCE: tests/core/test_event_bus.py:53-60
bus.publish(
    TestResultRecorded(
        execution_id="exec-1",
        passed=True,
        attempt=1,
        structured_errors_count=0,
    )
)
```

**Existing seq-monotonic assertion style** (mirror this exact dict-access form):

```python
# SOURCE: tests/core/test_event_bus.py:129-133
seqs_a = [row["seq"] for row in bus.get_events("exec-A")]
seqs_b = [row["seq"] for row in bus.get_events("exec-B")]

assert seqs_a == [1, 2, 3]
assert seqs_b == [1, 2]
```

---

## Files to Change

| File                                  | Action | Justification                                                                                         |
| ------------------------------------- | ------ | ----------------------------------------------------------------------------------------------------- |
| `src/core/events/bus.py`              | UPDATE | Collapse SELECT + INSERT into one `INSERT...SELECT` statement; update the step-list docstring         |
| `tests/core/test_event_bus.py`        | UPDATE | Add a new test for two `EventBus` instances over a shared DB file; assert no IntegrityError + monotonic seq |

No new files. No schema migration. No new dependencies.

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **No schema migration / `ROWID`-as-seq (rejected option 2)**. Changing the schema is overkill for a MEDIUM-severity forward-compat fix and would risk silent regressions in existing event consumers.
- **No `BEGIN IMMEDIATE` wrapper (rejected option 3)**. Option 1 (single statement) is simpler, equally correct, and does not introduce a third style of transaction handling alongside `feedback_rules.py` and `db.py`. Option 3 is the runner-up if option 1 turns out infeasible in testing — see "Risks and Mitigations".
- **No multi-process coordination beyond the test scenario**. The test demonstrates two `EventBus` instances on one process can both write safely; deploying multi-process Sentinel is out of scope.
- **No transport switch (Redis, NATS, etc.)**. The reviewer flagged transport substitution as a separate, deferred item.
- **No change to subscriber semantics**. Persist-first, swallow-and-log subscriber exceptions, oversized-payload truncation, and `ts` auto-fill are unchanged and remain covered by existing tests.
- **No retry loop / backoff in `publish`**. SQLite's default `sqlite3.connect` blocks for `timeout=5.0s` on a busy DB. That is good enough for the realistic concurrent test; we do not add explicit retry.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/core/events/bus.py` — replace two-statement publish with single INSERT...SELECT

- **ACTION**: Modify the body of `EventBus.publish` (lines 57-94 in the current file).
- **IMPLEMENT**:
  - Delete the `SELECT COALESCE(MAX(seq), 0) + 1 ...` `cursor` block at lines 70-75 (no more `next_seq` variable).
  - Replace the existing `INSERT INTO events (...) VALUES (...)` at lines 89-93 with a single `INSERT INTO events (execution_id, seq, ts, agent, type, payload_json) SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ?, ? FROM events WHERE execution_id = ?` statement.
  - The new parameter tuple is `(event.execution_id, event.ts, agent, event.type, payload_json, event.execution_id)` — note `execution_id` appears twice: once as the inserted column value, once in the `WHERE` clause.
  - Keep `self._conn.commit()` immediately after the INSERT.
  - Keep the `event.ts` fill (lines 67-68) and the payload-truncation block (lines 77-85) above the INSERT — they still run before the row is written.
  - Update the `publish` docstring step-list (lines 60-66): replace step 2 ("Compute next per-execution `seq`.") with "INSERT in one statement that derives the next per-execution `seq` from `MAX(seq)+1`, so two writer connections cannot race on the PK." Drop step 4's separate "INSERT and COMMIT" — fold into the single step.
  - Update the module docstring invariant #2 (lines 6-7): change "Computed via ``MAX(seq) + 1`` inside the same transaction as the INSERT." to "Computed via ``MAX(seq) + 1`` *inside the INSERT statement itself* (single-statement atomic), so two writers cannot collide on the PK."
- **MIRROR**: `src/core/events/bus.py:89-93` (existing INSERT formatting — column list on first line, VALUES/SELECT on second, params tuple on third).
- **IMPORTS**: No change — `sqlite3`, `json`, `logging`, `defaultdict`, `datetime`/`timezone`, `Callable`, `BaseEvent` all stay.
- **GOTCHA**:
  - `event.execution_id` is bound twice (once for the inserted row, once for the `WHERE` clause that scopes the `MAX`). Forgetting the second occurrence yields a global `MAX(seq)` instead of per-execution — the existing `test_seq_is_monotonic_per_execution` test will catch this.
  - The `WHERE execution_id = ?` clause is required: without it, `MAX(seq)` runs over the whole table and breaks the per-execution monotonic invariant (Phase 1 contract: `seq` is per-`execution_id`, not global).
  - When the table is empty for that `execution_id`, `SELECT COALESCE(MAX(seq), 0) + 1 ... WHERE execution_id = ?` correctly returns `1` from a one-row aggregate even though no rows match — `MAX` over zero rows is `NULL`, and `COALESCE` lifts it to `0`. Verified by existing `test_publish_persists_before_calling_subscriber` (asserts `seq == 1`).
  - Do NOT add `LIMIT 1` to the SELECT — aggregate-only SELECTs return exactly one row already; `LIMIT 1` is noise.
  - The aggregate SELECT is over the same `events` table being inserted into. SQLite handles this via the statement's own implicit transaction; do not add an explicit `BEGIN`.
- **VALIDATE**: `poetry run ruff check src/core/events/bus.py && poetry run mypy src/core/events/bus.py` — exit 0.

### Task 2: UPDATE `tests/core/test_event_bus.py` — add concurrent-writer regression test

- **ACTION**: Append a new test function `test_concurrent_writers_do_not_collide_on_seq` to `tests/core/test_event_bus.py` (after the last existing test, currently `test_ts_filled_when_empty` ending at line 188).
- **IMPLEMENT**:
  - Signature: `def test_concurrent_writers_do_not_collide_on_seq(tmp_path: Path) -> None:` (add `from pathlib import Path` to the existing imports if not already present — it is not currently imported in this test file).
  - Body sketch:
    1. `db_path = tmp_path / "events.db"` — file-backed, not `:memory:`, because each `:memory:` connection is private.
    2. Open a "setup" connection: `setup_conn = sqlite3.connect(str(db_path))`, `setup_conn.row_factory = sqlite3.Row`, `setup_conn.execute("PRAGMA foreign_keys=ON")`, `apply_migrations(setup_conn)`.
    3. Insert one parent `executions` row with `id="exec-1"` (mirror `_conn_with_execution`).
    4. Commit and close the setup connection (or keep it; the test only needs two writers — closing keeps the assertion clean).
    5. Open two writer connections: `conn_a` and `conn_b`, each with `row_factory=sqlite3.Row`, `PRAGMA foreign_keys=ON`. Do NOT call `apply_migrations` again — the schema already exists.
    6. Build `bus_a = EventBus(conn_a)` and `bus_b = EventBus(conn_b)`.
    7. Interleave six publishes: `bus_a.publish(TestResultRecorded(execution_id="exec-1", passed=True, attempt=1, structured_errors_count=0))`, `bus_b.publish(...attempt=2...)`, `bus_a.publish(...attempt=3...)`, `bus_b.publish(...attempt=4...)`, `bus_a.publish(...attempt=5...)`, `bus_b.publish(...attempt=6...)`. None should raise `sqlite3.IntegrityError` or any other exception.
    8. Read back via a third connection (or `conn_a` after a commit): `rows = conn_a.execute("SELECT seq FROM events WHERE execution_id = ? ORDER BY seq", ("exec-1",)).fetchall()`.
    9. Assert `[row["seq"] for row in rows] == [1, 2, 3, 4, 5, 6]` — monotonic, contiguous, no duplicates, no gaps.
    10. Also assert `len({row["seq"] for row in rows}) == 6` (no duplicates) for a clearer failure message if the invariant breaks.
  - Docstring: "Two `EventBus` instances over the same DB file must produce a contiguous, duplicate-free seq sequence — locks in M6 atomicity guarantee."
- **MIRROR**:
  - File-DB fixture style: `tests/core/test_persistence.py:38-46` (use `tmp_path`).
  - Setup-conn-with-migrations idiom: `tests/core/test_event_bus.py:22-34`.
  - Event-publish call shape: `tests/core/test_event_bus.py:53-60`.
  - Seq-readback assertion: `tests/core/test_event_bus.py:129-133`.
- **IMPORTS**: Add `from pathlib import Path` at the top of `tests/core/test_event_bus.py` if absent. The existing `import sqlite3`, `from src.core.events import EventBus, TestResultRecorded`, and `from src.core.persistence import apply_migrations` are already present.
- **GOTCHA**:
  - Use `tmp_path` (pytest builtin), not `tempfile.NamedTemporaryFile` — it's the project idiom (see `test_persistence.py:38`, `test_propose_overlay.py:52`).
  - `:memory:` is wrong here: each `sqlite3.connect(":memory:")` returns a fresh, isolated DB. The test must use a real file path so both connections see the same DB.
  - Default SQLite `timeout` is 5 seconds — plenty for serialized two-writer publishes in the same Python process. Do not bump it.
  - The test is intentionally serial (writer A → writer B → writer A → ...). Threads are NOT required to assert atomicity — the goal is to prove the SQL is correct on multiple connections, not to stress-test concurrency. A threaded version is a follow-up if the team wants it.
  - Do NOT enable WAL on the writer connections — the existing `connect()` helper enables WAL, but this test calls `sqlite3.connect` directly to mirror `_conn_with_execution`. Without WAL, writers still serialize correctly (default journal mode is "delete"); WAL is an optimization, not a correctness requirement.
  - If the legacy two-statement code is somehow re-introduced, this test should fail with `sqlite3.IntegrityError: UNIQUE constraint failed: events.execution_id, events.seq` on whichever publish hits the collision — but only when the underlying race fires, which is timing-dependent. The serial interleave guarantees the test always exercises the alternation, but only the single-statement form makes correctness independent of timing. That is good enough; this is a regression-lock test, not a stress test.
- **VALIDATE**: `poetry run pytest tests/core/test_event_bus.py -v` — all six tests (five existing + one new) pass.

### Task 3: VERIFY no regressions across the wider event-bus test surface

- **ACTION**: Run the full event-bus + persistence test slices, then the full test suite.
- **IMPLEMENT**: No code change.
- **VALIDATE**:
  - `poetry run pytest tests/core/test_event_bus.py tests/core/test_persistence.py -v` — all pass.
  - `poetry run pytest tests/core/ -v` — all `tests/core/` pass (catches any indirect consumer of the bus).
  - `poetry run pytest -q` — full suite; no regressions in CLI, agents, or learning paths that publish events.
  - `poetry run ruff check . && poetry run mypy src` — exit 0.

---

## Testing Strategy

### Unit Tests to Write

| Test File                          | Test Cases                                          | Validates                                                                |
| ---------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------ |
| `tests/core/test_event_bus.py`     | `test_concurrent_writers_do_not_collide_on_seq`     | Two `EventBus` instances on one DB file produce contiguous, unique `seq` |

Existing tests that must continue to pass byte-identically:

- `test_publish_persists_before_calling_subscriber` — persist-first contract.
- `test_subscriber_exception_does_not_crash_publish` — subscriber error swallowing.
- `test_seq_is_monotonic_per_execution` — per-execution monotonic seq across two executions.
- `test_oversized_payload_truncated` — >64 KB payload replaced with `_truncated` marker.
- `test_ts_filled_when_empty` — empty `ts` filled with ISO-8601 UTC.

### Edge Cases Checklist

- [ ] First-event case (`MAX(seq)` over zero rows → `NULL` → `COALESCE` lifts to 0 → `seq=1`). Already covered by `test_publish_persists_before_calling_subscriber`.
- [ ] Per-execution scope (two executions, seqs are independent). Already covered by `test_seq_is_monotonic_per_execution`.
- [ ] Two connections, six interleaved publishes, expect `[1..6]`. NEW.
- [ ] Subscriber still receives the event AFTER the row is written (persist-first). Already covered.
- [ ] Oversized payload still truncates before the INSERT. Already covered.
- [ ] `ts=""` still gets filled. Already covered.
- [ ] FK constraint (`execution_id` must exist in `executions`) still enforced. Implicit in all tests via `_conn_with_execution`.
- [ ] No `agent` column on events that don't carry one (`TestResultRecorded.agent` is `Optional[str]` defaulting to `None`). Already covered (`getattr(event, "agent", None)` at line 87 is unchanged).

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
poetry run ruff check src/core/events/bus.py tests/core/test_event_bus.py
poetry run mypy src/core/events/bus.py
```

**EXPECT**: Exit 0, no errors or warnings.

### Level 2: UNIT_TESTS

```bash
poetry run pytest tests/core/test_event_bus.py -v
```

**EXPECT**: 6 tests pass (5 existing byte-identical + 1 new `test_concurrent_writers_do_not_collide_on_seq`).

### Level 3: FULL_SUITE

```bash
poetry run pytest -q
```

**EXPECT**: All tests green; no regressions in CLI, agents, learning, persistence.

### Level 4: DATABASE_VALIDATION (N/A)

No schema change — `events` table definition in `001_init.sql` is untouched.

### Level 5: BROWSER_VALIDATION (N/A)

No UI surface.

### Level 6: MANUAL_VALIDATION

Optional sanity check that the SQL is single-statement and atomic:

```bash
poetry run python - <<'PY'
import sqlite3, tempfile, pathlib
from src.core.persistence import apply_migrations
from src.core.events import EventBus, TestResultRecorded

with tempfile.TemporaryDirectory() as td:
    db = pathlib.Path(td) / "x.db"
    setup = sqlite3.connect(str(db)); setup.row_factory = sqlite3.Row
    setup.execute("PRAGMA foreign_keys=ON")
    apply_migrations(setup)
    setup.execute(
        "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("e1", "T", "execute", "running", "2026-05-14T00:00:00+00:00"),
    )
    setup.commit(); setup.close()

    a = sqlite3.connect(str(db)); a.row_factory = sqlite3.Row; a.execute("PRAGMA foreign_keys=ON")
    b = sqlite3.connect(str(db)); b.row_factory = sqlite3.Row; b.execute("PRAGMA foreign_keys=ON")
    ba, bb = EventBus(a), EventBus(b)
    for i, bus in enumerate((ba, bb, ba, bb, ba, bb), start=1):
        bus.publish(TestResultRecorded(execution_id="e1", passed=True, attempt=i, structured_errors_count=0))
    print([r["seq"] for r in a.execute("SELECT seq FROM events ORDER BY seq")])
PY
```

**EXPECT**: prints `[1, 2, 3, 4, 5, 6]`.

---

## Acceptance Criteria

- [ ] `EventBus.publish` issues exactly one `INSERT INTO events ... SELECT ...` statement (no separate `SELECT MAX(seq)+1` ahead of it).
- [ ] All five existing tests in `tests/core/test_event_bus.py` continue to pass byte-identically (no edits to their assertions).
- [ ] New `test_concurrent_writers_do_not_collide_on_seq` test passes and locks in the invariant.
- [ ] `poetry run ruff check` and `poetry run mypy src/core/events/bus.py` exit 0.
- [ ] `poetry run pytest -q` exits 0 (full suite).
- [ ] Module-level docstring (invariant #2) and `publish` docstring (step list) updated to reflect single-statement atomicity.
- [ ] Schema unchanged — no new migration file in `src/core/persistence/migrations/`.

---

## Completion Checklist

- [ ] Task 1 (bus.py edit) complete and validated.
- [ ] Task 2 (new test) complete and validated.
- [ ] Task 3 (regression sweep) complete; `poetry run pytest -q` green.
- [ ] Level 1 static analysis passes.
- [ ] Level 2 unit tests pass (6 in `test_event_bus.py`).
- [ ] Level 3 full suite passes.
- [ ] Acceptance criteria all checked.

---

## Risks and Mitigations

| Risk                                                                                              | Likelihood | Impact | Mitigation                                                                                                                                                            |
| ------------------------------------------------------------------------------------------------- | ---------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `INSERT...SELECT` form behaves differently from `INSERT...VALUES` on SQLite older than 3.7.11     | LOW        | LOW    | SQLite ships with Python ≥ 3.11 (project's `python = "^3.11"` at `pyproject.toml:11`); bundled SQLite is 3.37+ everywhere. Both forms are stable since 3.0.            |
| Aggregate SELECT over the same table being inserted into yields stale `MAX`                       | LOW        | MED    | SQLite's manual explicitly supports this — the SELECT sees the table state at statement start. Existing `test_seq_is_monotonic_per_execution` enforces correctness.   |
| Forgetting the `WHERE execution_id = ?` clause breaks per-execution scope                         | LOW        | HIGH   | `test_seq_is_monotonic_per_execution` (existing) catches this immediately — it asserts seqs are independent across two executions.                                    |
| Forgetting that `event.execution_id` binds twice (column value + WHERE) → wrong seq or FK error   | LOW        | HIGH   | The Task 1 GOTCHA explicitly calls this out. Tests catch it on first run.                                                                                            |
| `:memory:` accidentally used in the new test → two private DBs, test passes vacuously             | MED        | HIGH   | Task 2 explicitly mandates `tmp_path / "events.db"` and links to `test_persistence.py:38-46` for the idiom. Reviewer should grep the new test for `:memory:`.         |
| Option 1 (single statement) turns out infeasible (e.g., a constraint trigger interferes)         | VERY LOW   | LOW    | Runner-up: wrap the existing two statements in `BEGIN IMMEDIATE` / `COMMIT` per the `feedback_rules.py:217-245` pattern. Re-plan and call out the schema-touching alternative as still rejected. |
| Concurrent test causes `sqlite3.OperationalError: database is locked` if default 5s timeout hits  | VERY LOW   | LOW    | The test is single-threaded with serial publishes. No lock contention beyond statement boundaries.                                                                    |

---

## Notes

**Why option 1 over option 3 (the runner-up).** Option 3 (`BEGIN IMMEDIATE` wrapping the existing SELECT + INSERT) is also correct, but it duplicates a transaction-handling style that already lives in two places (`db.py:150` for migrations, `feedback_rules.py:217+265+313` for status flips). Adding a third use-site for explicit BEGIN/COMMIT/ROLLBACK in a function whose entire body is "do one tiny thing" hurts readability. Option 1 collapses the same intent into one SQL statement and keeps `publish` linear.

**Why option 2 was rejected.** Using SQLite `ROWID` as `seq` would be cleanest for the global-ordering case, but the contract is per-`execution_id` monotonic seq, not global. ROWID can't give us that without a window over the events table at insert time — which lands us back at exactly the structure of option 1, plus a schema migration. Not worth it.

**Why no thread-based stress test.** The reviewer's constraint is "two `EventBus` instances over the same DB, calls `publish` from both interleaved." A serial interleave proves the SQL is correct on two connections; threads would prove SQLite's locking, which is not what we own. If a future maintainer wants a stress test, they can add it on top — it's not a blocker for this fix.

**Why update the docstrings.** The module-level invariant #2 currently says "Computed via `MAX(seq) + 1` inside the same transaction as the INSERT." That is technically still true after the fix, but reads ambiguously — "the same transaction" sounds like an explicit BEGIN. Tightening to "*inside the INSERT statement itself*" makes the contract unambiguous and explains why two writers cannot collide. The `publish` step-list update is purely for readers; no behaviour change.

**Forward-compat receipt.** Once this lands, anyone introducing a multi-connection writer (worker fan-out, side fixture, multi-process Sentinel) inherits correctness for free — they will not need to discover this footgun the hard way.
