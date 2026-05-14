# Feature: Fix `db_conn` leak in `cli.py::execute()` (PR review H1)

## Summary

Both control-flow paths inside `sentinel execute` (the `--revise` path and the
normal execute path) call `db_conn = connect(); apply_migrations(db_conn)` but
never close the resulting `sqlite3.Connection`. The connection (and its WAL
files / file descriptors) leaks until the Python process exits. Every other CLI
command in the file (`postmortems_list`, `learning_*`, `outcomes_sync`, the
`_run_outcome_sync_preflight` helper) already follows the
`conn = connect(); try: ... finally: conn.close()` pattern. This plan threads
that exact same pattern into the two `db_conn` open sites in `execute` —
nothing more. No refactor of CLI-wide connection management; no behavior change
on the happy or error paths; no teardown is added to `EventBus` because the bus
holds only a reference to the conn and has no timers, threads, or async
resources to drain (verified at `src/core/events/bus.py:35-94`).

## User Story

As a Sentinel operator running `sentinel execute` repeatedly in a single
long-lived process (e.g. inside `sentinel-dev` during a Ralph loop)
I want each `execute` invocation to release its SQLite connection on exit
So that file descriptors, WAL/SHM files, and SQLite locks don't accumulate and
eventually wedge concurrent commands or trip the OS fd limit.

## Problem Statement

`src/cli.py:645-646` (revise path) and `src/cli.py:983-984` (normal path) both
open a connection that is referenced by `EventBus` and post-execute subscribers
but never closed. Symptoms are bounded today (CLI is mostly one-shot), but:

1. Tests that drive `execute` via Click's `CliRunner` inside one process leak a
   conn per test → flaky `database is locked` and stale WAL on `tmp_path`.
2. Any future caller that invokes `execute` in-process (Ralph, batched
   invocations, future `sentinel watch`) leaks linearly.
3. The bug is asymmetric with the rest of `cli.py` — every other DB-using
   subcommand closes correctly. This is a maintenance hazard: future copy-paste
   from `execute` will replicate the leak.

Testable: after `cli.execute` returns (success or failure), the
`sqlite3.Connection` instance opened inside it must be in the closed state
(asserted by `conn.execute(...)` raising `sqlite3.ProgrammingError`).

## Solution Statement

Wrap each path's body in a `try / finally: db_conn.close()` block, mirroring
the `outcomes_sync` (`cli.py:3466-3506`), `_run_outcome_sync_preflight`
(`cli.py:1818-1826`), and `postmortems_list` (`cli.py:1694-1711`) patterns
already in the file. Add a small regression test that exercises both paths via
`CliRunner` with mocked agents and asserts the captured connection is closed
on return.

## Metadata

| Field            | Value                                                    |
| ---------------- | -------------------------------------------------------- |
| Type             | BUG_FIX                                                  |
| Complexity       | LOW                                                      |
| Systems Affected | `src/cli.py` (execute command only); `tests/`            |
| Dependencies     | None new. Stays inside `sqlite3` stdlib + existing Click |
| Estimated Tasks  | 4                                                        |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║  BEFORE — connection leaks per execute                                        ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   ┌────────────────┐      ┌──────────────────┐      ┌──────────────────┐      ║
║   │ sentinel       │ ───► │ db_conn = connect│ ───► │ EventBus(db_conn)│      ║
║   │ execute T-1    │      │ apply_migrations │      │ subscribers held │      ║
║   └────────────────┘      └──────────────────┘      └──────────────────┘      ║
║                                                              │                ║
║                                                              ▼                ║
║                                              ┌────────────────────────────┐   ║
║                                              │ return / sys.exit(1)       │   ║
║                                              │ db_conn STILL OPEN         │   ║
║                                              │ WAL+SHM files held         │   ║
║                                              │ fd retained until process  │   ║
║                                              │ exit                       │   ║
║                                              └────────────────────────────┘   ║
║                                                                               ║
║   PAIN_POINT: every execute leaks a sqlite conn, asymmetric with peer cmds.   ║
║   DATA_FLOW: conn → migrations → INSERT → bus.publish → … → (no close)        ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║  AFTER — try/finally closes conn on every exit path                           ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   ┌────────────────┐      ┌──────────────────┐      ┌──────────────────┐      ║
║   │ sentinel       │ ───► │ db_conn = connect│ ───► │ try: EventBus +  │      ║
║   │ execute T-1    │      │ apply_migrations │      │      developer + │      ║
║   └────────────────┘      └──────────────────┘      │      iterations  │      ║
║                                                     └─────────┬────────┘      ║
║                                                               │               ║
║                                                               ▼               ║
║                                                  ┌────────────────────────┐   ║
║                                                  │ finally:               │   ║
║                                                  │   db_conn.close()      │   ║
║                                                  │   (logged-and-swallow  │   ║
║                                                  │    on second close)    │   ║
║                                                  └────────────────────────┘   ║
║                                                                               ║
║   VALUE_ADD: parity with peer commands; no fd / WAL leak; safer in-proc reuse ║
║   DATA_FLOW: unchanged on the happy/error paths — only the lifetime ends      ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                              | Before                       | After                          | User Impact                                  |
| ------------------------------------- | ---------------------------- | ------------------------------ | -------------------------------------------- |
| `src/cli.py::execute` revise path     | conn opened, never closed    | conn opened, closed in finally | None visible. Cleaner shutdown.              |
| `src/cli.py::execute` normal path     | conn opened, never closed    | conn opened, closed in finally | None visible. Cleaner shutdown.              |
| `sentinel execute` exit codes         | 0/1/2 unchanged              | 0/1/2 unchanged                | None.                                        |
| Stdout/stderr text                    | unchanged                    | unchanged                      | None.                                        |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File                                              | Lines       | Why Read This                                                                  |
| -------- | ------------------------------------------------- | ----------- | ------------------------------------------------------------------------------ |
| P0       | `src/cli.py`                                      | 592-1380    | The whole `execute` function — both paths, surrounding `try/except/finally`s   |
| P0       | `src/cli.py`                                      | 1802-1826   | `_run_outcome_sync_preflight` — exact try/finally idiom to mirror              |
| P0       | `src/cli.py`                                      | 1693-1715   | `postmortems_list` — short, canonical conn lifecycle pattern in this file      |
| P0       | `src/cli.py`                                      | 3451-3509   | `outcomes_sync` — pattern with EventBus bound to conn, closes anyway           |
| P1       | `src/core/events/bus.py`                          | 1-113       | Confirm EventBus has no teardown method (none needed — see Notes)              |
| P1       | `src/core/execution/post_execute.py`              | 60-145      | Subscribers capture `conn` in a closure but hold no other resources            |
| P1       | `src/core/learning/cache_invalidator.py`          | 1-60        | Same — subscriber holds `prompt_loader`, not the conn directly                 |
| P1       | `src/core/persistence/db.py`                      | 1-60        | `connect()` signature; honors `SENTINEL_DB_PATH`; opens WAL                    |
| P1       | `tests/conftest.py`                               | 1-130       | Shared fixtures (`sqlite_mem_conn`, etc.) — but do NOT reuse for this test     |
| P2       | `tests/test_cli_outcomes.py`                      | 1-120       | `db_path` fixture using `SENTINEL_DB_PATH` env var — best pattern to mirror    |
| P2       | `tests/test_cli_postmortems.py`                   | 1-100       | CliRunner-driven CLI tests with tmp DB (closest stylistically)                 |

**External Documentation:**

| Source                                                                                                                  | Section                          | Why Needed                                                              |
| ----------------------------------------------------------------------------------------------------------------------- | -------------------------------- | ----------------------------------------------------------------------- |
| [Python sqlite3 docs (3.11)](https://docs.python.org/3.11/library/sqlite3.html#sqlite3.Connection.close)               | Connection.close                 | Confirm: idempotent? No — second close is a no-op; ProgrammingError on use after close |
| [Python sqlite3 docs (3.11)](https://docs.python.org/3.11/library/sqlite3.html#sqlite3.ProgrammingError)               | Exceptions                       | The exception we catch in the regression test to assert "closed"        |
| [Click testing docs](https://click.palletsprojects.com/en/8.1.x/testing/)                                              | CliRunner                        | Pattern for invoking `cli` in-process with stub agents                  |

---

## Patterns to Mirror

**CONN_LIFECYCLE (preferred — mirror this exactly):**

```python
# SOURCE: src/cli.py:1693-1715 (postmortems_list)
# COPY THIS PATTERN:
def postmortems_list(stack: Optional[str], limit: int, min_confidence: int) -> None:
    """List active (non-superseded) postmortems."""
    try:
        conn = connect()
        try:
            apply_migrations(conn)
            rows = list_postmortems(
                conn, stack=stack, min_confidence=min_confidence, limit=limit
            )
            ...
        finally:
            conn.close()
    except Exception as exc:
        logger.error("postmortems list failed: %s", exc, exc_info=True)
        click.echo(f"\n❌ Error: {exc}", err=True)
        sys.exit(1)
```

**CONN_LIFECYCLE_WITH_BUS (closest analogue — bus is bound to conn):**

```python
# SOURCE: src/cli.py:3465-3506 (outcomes_sync)
# COPY THIS PATTERN:
try:
    conn = connect()
    try:
        apply_migrations(conn)
        from src.core.events import EventBus
        from src.core.learning.outcome_sync import OutcomeSyncService
        from src.gitlab_client import GitLabClient

        event_bus: Optional[EventBus]
        if dry_run:
            event_bus = None
        else:
            _learning_seed_synthetic_execution(conn, prefix="outcomes-sync")
            event_bus = EventBus(conn)

        service = OutcomeSyncService(conn, GitLabClient(), event_bus=event_bus)
        ...
    finally:
        conn.close()
except Exception as e:
    click.echo(f"❌ outcomes sync failed: {e}", err=True)
    sys.exit(1)
```

Note: closing the conn while `EventBus` still holds a reference is safe — the
bus dies when the function frame exits and its destructor doesn't touch the
conn. We confirmed this pattern is the project's convention.

**TEST_PATTERN_DB_FIXTURE (mirror for the regression test):**

```python
# SOURCE: tests/test_cli_outcomes.py:42-59
# COPY THIS PATTERN:
@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Empty migrated DB at a tmp path the CLI resolves via SENTINEL_DB_PATH."""
    path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(path))

    conn = connect(str(path))
    try:
        apply_migrations(conn)
    finally:
        conn.close()

    yield path
```

**TEST_PATTERN_CLIRUNNER (mirror for the regression test driver):**

```python
# SOURCE: tests/test_cli_outcomes.py:67-117
# COPY THIS PATTERN:
def test_outcomes_sync_disabled_without_dry_run_exits_2(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("OUTCOME_SYNC_ENABLED", raising=False)
    result = runner.invoke(cli, ["outcomes", "sync", "--project", "acme/backend"])
    assert result.exit_code == 2
```

---

## Files to Change

| File                              | Action | Justification                                                                |
| --------------------------------- | ------ | ---------------------------------------------------------------------------- |
| `src/cli.py`                      | UPDATE | Wrap revise path body (645-935) in `try/finally: db_conn.close()`            |
| `src/cli.py`                      | UPDATE | Wrap normal path body (983-1366) in `try/finally: db_conn.close()`           |
| `tests/test_cli_execute_dbconn.py`| CREATE | Regression test asserting closed conn on success and failure paths           |

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **No CLI-wide refactor of connection management.** No conn-as-context-manager helper, no `@with_db` decorator, no migration of `plan` or other commands. The PR review issue is targeted; broader cleanup would balloon the diff and risk regressions in commands that already work.
- **No EventBus teardown method.** EventBus has no threads, no timers, no async tasks, no buffered writes (every `publish` commits synchronously — see `src/core/events/bus.py:94`). The only resource it touches is `self._conn`; closing the conn is the teardown.
- **No subscriber unsubscribe.** Subscribers are closures captured by the bus's local `_subscribers` dict; they go out of scope when the bus does. Adding an `unsubscribe` API is unnecessary for fixing the leak.
- **No change to `_run_outcome_sync_preflight`.** It already closes correctly (`cli.py:1818-1826`).
- **No fix for H2/H3/H4/H5/H6/H7** from the same review. Those are tracked as separate issues; mixing them here would couple unrelated fixes and complicate review.
- **No new fixtures in `tests/conftest.py`.** The regression test is a single file; the existing `db_path`-style fixture stays scoped to it.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/cli.py` — wrap REVISE path with try/finally

- **ACTION**: Insert `try:` immediately after `db_conn.commit()` at line 658, and `finally: db_conn.close()` after the existing `finally:` block ends (line 935, before `return`).
- **CONCRETE INSERTION POINTS**:
  - **Open `try:`** after the existing `db_conn.commit()` at `cli.py:658`, _before_ the `if stack_type and stack_type.startswith("drupal"):` line at 660. Indent the entire body 660-935 by one level.
  - **Add `finally: db_conn.close()`** as a new block at the *same indentation level as the inserted `try:`*, paired with it. This must be placed AFTER the existing `finally:` block at line 927 (which handles container teardown) closes — i.e., between the existing `finally:` body and the `return` at line 935.
- **CRITICAL NESTING**: There is already an inner `try / except DeveloperCappedOutException` (lines 734-744) nested inside an outer `try / finally` (734/927). The *new* outer `try / finally: db_conn.close()` must wrap **both** the bus-wiring block (660-720), the env-mgr block (722-732), and the existing `try/finally` (734-933). Concretely:
  ```
  db_conn.commit()                       # line 658, unchanged
  try:                                   # NEW
      if stack_type and stack_type...    # existing 660 onward, indented +4
      ...
      try:                               # existing 734
          ...
      finally:                           # existing 927
          if env_info and env_info.active:
              ...
  finally:                               # NEW — pairs with NEW try at 658+1
      db_conn.close()
  return                                 # existing 935, dedent unchanged
  ```
- **MIRROR**: `src/cli.py:3466-3506` (`outcomes_sync`) — same shape: outer-try wraps `apply_migrations`, EventBus construction, work, then `finally: conn.close()`.
- **DO NOT TOUCH**: the inner `try / except DeveloperCappedOutException: ... sys.exit(1)` at 735-743. `sys.exit(1)` raises `SystemExit`, which propagates through `finally` and triggers `db_conn.close()` correctly. No explicit close before `sys.exit` is needed — and adding one would diverge from the peer pattern.
- **GOTCHA 1**: `sys.exit(1)` calls inside the body (e.g., line 743, 785) raise `SystemExit`. Python guarantees `finally` blocks run during `SystemExit` unwind. Verified pattern: `outcomes_sync` at `cli.py:3464` does `sys.exit(2)` outside its try/finally; the equivalent inside-finally case is exercised by `postmortems_list` correctly.
- **GOTCHA 2**: The `bus = EventBus(db_conn)` reference at line 682 is local to the function; once `db_conn.close()` runs in `finally`, any later use would raise `sqlite3.ProgrammingError`. Confirm by re-reading the body: there is no subscriber callback that fires *after* the function returns. Subscribers fire synchronously inside `bus.publish` calls during the body. Safe.
- **GOTCHA 3**: Do NOT widen the existing outer `try` at `cli.py:607` — that's the function-wide error-handling try whose `except Exception` at line 1377 prints and exits. Leave it alone; the new try/finally lives strictly *inside* it (between the project resolution and the function-wide except).
- **VALIDATE**: `python -m py_compile src/cli.py` (must exit 0); then `ruff check src/cli.py` (no new errors); then `mypy src/cli.py` (no new errors beyond the 26 pre-existing on this branch).

### Task 2: UPDATE `src/cli.py` — wrap NORMAL execute path with try/finally

- **ACTION**: Same shape as Task 1, applied to the normal-execute path.
- **CONCRETE INSERTION POINTS**:
  - **Open `try:`** after the existing `db_conn.commit()` at `cli.py:996`, _before_ the `if stack_type and stack_type.startswith("drupal"):` at 998.
  - **Add `finally: db_conn.close()`** after the existing function-level `finally:` at line 1368 (which handles `env_mgr.teardown`) closes its body — i.e., paired with the new outer try, ending before the function-wide `except Exception as e:` at 1377.
- **CRITICAL NESTING**: There is already a `try / finally` at 964/1368. The new outer `try / finally: db_conn.close()` must wrap from line 998 (after `commit()`) down to and including the existing 964/1368 try-finally. The shape:
  ```
  db_conn.commit()                       # line 996, unchanged
  try:                                   # NEW
      if stack_type and stack_type...    # 998 onward, indented +4
      ...
      try:                               # existing 964 — wait, see ordering note
          ...
      finally:                           # existing 1368
          ...
  finally:                               # NEW
      db_conn.close()
  ```
- **ORDERING NOTE**: Re-read carefully — the existing `try:` at 964 actually opens *before* the `db_conn = connect()` call at 983 (the `try:` at 964 wraps the plan-file lookup, the developer agent selection, and the conn open + commit). The simplest correct change is to open the new `try:` *after* `db_conn.commit()` at line 996, indent everything from 998 down to (and including) the existing `finally:` body at 1369-1375, and close with `finally: db_conn.close()` immediately after the existing finally body, _still inside_ the outer try at 964.
- **GOTCHA 1**: `sys.exit(1)` at lines 1092, 1119, 1143, 1161, 1237 — all unwind through the new `finally` correctly. Same SystemExit semantics as Task 1.
- **GOTCHA 2**: There are at least 5 `break`/`continue` statements inside the iteration loop (line 1068+); none escape the function frame, so they are not relevant to `finally` ordering. The loop runs entirely inside the new `try`.
- **GOTCHA 3**: At line 968, `sys.exit(1)` is called when the plan file is missing — this happens *before* `db_conn = connect()` at line 983, so the new `finally` is not yet pending. No double-close risk.
- **MIRROR**: same as Task 1 — `src/cli.py:3466-3506`.
- **VALIDATE**: `python -m py_compile src/cli.py`; then `ruff check src/cli.py`; then `mypy src/cli.py`.

### Task 3: CREATE `tests/test_cli_execute_dbconn.py`

- **ACTION**: Create a new test module asserting the regression — `db_conn` is closed after `execute` returns on both success and failure paths, in both revise and normal modes.
- **TEST MATRIX** (4 tests minimum):
  1. `test_execute_normal_path_closes_db_conn_on_success` — happy path, agents stubbed to return approved.
  2. `test_execute_normal_path_closes_db_conn_on_failure` — developer agent stubbed to raise; expect `sys.exit(1)`; conn still closed.
  3. `test_execute_revise_path_closes_db_conn_on_success` — `--revise` flag, MR fetch returns 0 unresolved → early return path.
  4. `test_execute_revise_path_closes_db_conn_on_developer_capped_out` — `DeveloperCappedOutException` path → `sys.exit(1)`; conn closed.
- **HOW TO CAPTURE THE CONN**: monkeypatch `src.cli.connect` to wrap the real `connect()` and stash the returned object on a list:
  ```python
  opened_conns: list[sqlite3.Connection] = []
  real_connect = src.cli.connect
  def _capturing_connect(*args, **kwargs):
      conn = real_connect(*args, **kwargs)
      opened_conns.append(conn)
      return conn
  monkeypatch.setattr("src.cli.connect", _capturing_connect)
  ```
  After invoking the CLI, assert `opened_conns` has at least one entry and that `_assert_closed(opened_conns[-1])` succeeds.
- **HOW TO ASSERT CLOSED**:
  ```python
  def _assert_closed(conn: sqlite3.Connection) -> None:
      with pytest.raises(sqlite3.ProgrammingError):
          conn.execute("SELECT 1")
  ```
  This matches stdlib behavior: `Connection.close()` is idempotent, but any subsequent `.execute` on a closed conn raises `ProgrammingError("Cannot operate on a closed database.")`.
- **MOCKING STRATEGY**:
  - Mock `WorktreeManager.get_worktree_path` to return `tmp_path` so the CLI's worktree gate (line 616-620) passes.
  - Mock `get_config` to return a stub with `get_project_config` returning a dict with `stack_type=""` and `git_url=""`.
  - Mock `EnvironmentManager` so `setup` returns an inactive env_info (avoids docker calls).
  - For normal path: write a fake plan file at `tmp_path / ".agents" / "plans" / f"{ticket_id}.md"`.
  - Mock `PythonDeveloperAgent` (and `DrupalDeveloperAgent` defensively) so `developer.run(...)` returns `{"tasks_completed": 1, "tasks_failed": 0, "config_validation": {}, "regression_errors": []}`. Approve security via `SecurityReviewerAgent.run` returning `{"approved": True, "findings": []}`. Push will fail benignly (no remote), which is OK — the test only cares about conn lifecycle, and the function does NOT `sys.exit` on push failure.
  - For revise path: mock `developer.run_revision` to return `{"feedback_count": 0}` so the early-return path at line 745-748 is exercised.
  - Set `monkeypatch.setenv("SENTINEL_DB_PATH", str(tmp_path / "sentinel.db"))` and `monkeypatch.delenv("DEV_VERIFIER_LOOP", raising=False)` and `monkeypatch.delenv("LOOP_C_ENABLED", raising=False)` to skip bus wiring entirely (simpler test surface).
  - Mock `_outcome_sync_enabled` to return False (or unset `OUTCOME_SYNC_ENABLED`) so the preflight is skipped.
- **MIRROR**: `tests/test_cli_outcomes.py:42-117` — `db_path` fixture and CliRunner usage. `tests/test_cli_postmortems.py` for the broader CLI-test layout.
- **DO NOT MIRROR**: `tests/conftest.py`'s `sqlite_mem_conn` — that's an in-memory DB, but the CLI calls `connect()` itself; we want a `SENTINEL_DB_PATH`-driven file DB.
- **GOTCHA 1**: `CliRunner.invoke(cli, [...], catch_exceptions=False)` — pass `catch_exceptions=False` for the failure-path tests so `SystemExit` propagates as `result.exit_code`. Click 8.x catches `SystemExit` and reports the code regardless, but explicit is better.
- **GOTCHA 2**: The CLI's outer `except Exception as e: sys.exit(1)` at line 1377 swallows non-SystemExit exceptions and calls `sys.exit(1)`. So a stubbed `developer.run` that raises `RuntimeError("boom")` will still exit cleanly via the outer except — and the conn must still be closed because the `finally` runs *before* control reaches the outer except (the new finally is *inside* the outer try).
- **GOTCHA 3**: `connect()` honors `SENTINEL_DB_PATH` (`src/core/persistence/db.py:31`). The fixture sets this; do not pass an explicit path to the CLI.
- **GOTCHA 4**: Do not assert the exact exit code on the "developer raises" test if it depends on Click's catch behavior — assert *only* that the conn is closed. The PR-review constraint is "do not change CLI behavior on the happy or error path", so any exit code shift would be a separate bug.
- **VALIDATE**: `pytest -q tests/test_cli_execute_dbconn.py -x`. All 4 tests must pass.

### Task 4: VALIDATE — full diff review and lint sweep

- **ACTION**: Run the project's standard validation gates and confirm no new errors are introduced.
- **STEPS**:
  1. `git diff src/cli.py` — visual sanity. The diff should be three insertions per path: `try:` line, indentation of body, `finally:\n    db_conn.close()` block. Nothing else. No imports added (the existing `db_conn = connect()` and `apply_migrations` lines are unchanged).
  2. `ruff check src/cli.py tests/test_cli_execute_dbconn.py` — must show 0 new errors versus baseline. Baseline on this branch is 18 ruff errors; after the change it must remain 18.
  3. `mypy src/cli.py tests/test_cli_execute_dbconn.py` — must show 0 new errors versus baseline (26 pre-existing on `feat/sentinel-learning-system`).
  4. `pytest -q tests/test_cli_execute_dbconn.py` — 4 tests pass.
  5. `pytest -q tests/test_cli_outcomes.py tests/test_cli_postmortems.py tests/test_cli_learning.py` — 0 regressions in peer CLI tests.
  6. `pytest -q tests/core/test_post_execute_handoff.py` — 0 regressions in subscriber tests (closing conn after subscribers fire is exactly what we want; this confirms no test depended on a still-open conn after function return).
- **VALIDATE**: All commands exit 0.

---

## Testing Strategy

### Unit Tests to Write

| Test File                              | Test Cases                                                       | Validates                                                |
| -------------------------------------- | ---------------------------------------------------------------- | -------------------------------------------------------- |
| `tests/test_cli_execute_dbconn.py`     | normal-success, normal-failure, revise-success, revise-capped    | `db_conn.close()` runs on every exit path of `execute`   |

### Edge Cases Checklist

- [ ] Normal path, security approves on iteration 1 → conn closed.
- [ ] Normal path, developer raises non-CappedOut exception → outer-except catches, exits, conn closed by finally.
- [ ] Normal path, `DeveloperCappedOutException` → inner except calls `sys.exit(1)`, finally closes conn.
- [ ] Normal path, all tasks failed (line 1141, `sys.exit(1)`) → conn closed.
- [ ] Normal path, config validation fails on last iteration (line 1161, `sys.exit(1)`) → conn closed.
- [ ] Normal path, max iterations reached without security approval (line 1237) → conn closed.
- [ ] Revise path, 0 unresolved discussions → early `return`, conn closed.
- [ ] Revise path, `DeveloperCappedOutException` → conn closed.
- [ ] Revise path, drush config FAILED post-revise (line 785) → conn closed.
- [ ] Plan file missing (line 968) — happens *before* `db_conn = connect()`, so no conn to close. Verify our finally is correctly scoped *after* the connect call (it is — see Task 2 ordering note).
- [ ] Container setup fails before `db_conn = connect()` (line 962) — same as above; no conn to close.

Tests cover the four most representative branches; the rest are derived from inspection of the same code path (closing semantics are uniform).

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
ruff check src/cli.py tests/test_cli_execute_dbconn.py
mypy src/cli.py tests/test_cli_execute_dbconn.py
python -m py_compile src/cli.py
```

**EXPECT**: Exit 0, no new errors versus the `feat/sentinel-learning-system` baseline. Baseline ruff = 18 errors on branch; baseline mypy = 26 errors on branch. New file count must remain ≤ baseline.

### Level 2: UNIT_TESTS

```bash
pytest -q tests/test_cli_execute_dbconn.py -x
```

**EXPECT**: 4 tests pass.

### Level 3: PEER_REGRESSION

```bash
pytest -q tests/test_cli_outcomes.py tests/test_cli_postmortems.py tests/test_cli_learning.py tests/core/test_post_execute_handoff.py
```

**EXPECT**: All pre-existing tests still pass.

### Level 4: FULL_SUITE (run inside `sentinel-dev`, per CLAUDE.md)

```bash
pytest -q
```

**EXPECT**: At minimum, the per-branch baseline of 937 passed / 26 failed (per `phase-3a` implementation report) is maintained. Net delta from this PR should be +4 passing, ±0 failing.

### Level 5: BUILD

N/A — pure Python, no build step.

### Level 6: MANUAL_VALIDATION

Inside `sentinel-dev`:

1. Set `SENTINEL_DB_PATH=/tmp/sentinel-leak-test.db`.
2. Run `lsof -p $$ | grep sentinel-leak-test | wc -l` before invoking.
3. Invoke `sentinel execute <known-ticket> --no-env` (or whatever subset works in dev). Exit code unimportant.
4. Re-run `lsof -p $$ | grep sentinel-leak-test | wc -l`. Should be 0 — no fds held by the parent shell after the subprocess exits.
5. Inspect `/tmp/sentinel-leak-test.db-wal` and `-shm` files: should be either absent or empty (WAL checkpoint runs on clean close).

---

## Acceptance Criteria

- [x] Both `db_conn = connect()` sites in `src/cli.py::execute` are wrapped in `try / finally: db_conn.close()`.
- [x] No CLI behavior change: same exit codes, same stdout/stderr text, same Jira/GitLab side-effects on the happy and error paths.
- [x] Pattern matches `outcomes_sync` / `postmortems_list` / `_run_outcome_sync_preflight` exactly.
- [x] Regression test asserts conn closed on all 4 representative paths.
- [x] Level 1-3 validation gates pass with no new errors.
- [x] `git diff src/cli.py` is small and surgical — only `try:`, indentation, and `finally: db_conn.close()` lines added (plus indentation churn, which a reviewer will tolerate).
- [x] No EventBus changes; no subscriber-API changes; no migration changes; no fixture changes in `tests/conftest.py`.

---

## Completion Checklist

- [x] Task 1 done — revise path wrapped, `python -m py_compile` clean.
- [x] Task 2 done — normal path wrapped, `python -m py_compile` clean.
- [x] Task 3 done — regression test file created, 4 tests pass.
- [x] Task 4 done — full validation matrix green.
- [x] Diff reviewed by hand for indentation correctness (the only realistic foot-gun).
- [ ] PR review issue H1 referenced in commit message. _(deferred to commit step — out of Ralph scope)_

---

## Risks and Mitigations

| Risk                                                                      | Likelihood | Impact   | Mitigation                                                                                                                                                                  |
| ------------------------------------------------------------------------- | ---------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Indentation slip — body of `try:` is 200+ lines per path, miss a level    | MEDIUM     | HIGH     | Use a single editor-side reindent; verify with `python -m py_compile`; review the diff line count (should be roughly +5/-0 lines plus indentation churn)                   |
| `finally: db_conn.close()` runs before a subscriber that needs the conn  | LOW        | MED      | Subscribers fire synchronously inside `bus.publish` calls during the body. No deferred subscriber. Verified at `src/core/events/bus.py:96-103`.                              |
| Test asserts wrong exception type for "closed conn"                       | LOW        | LOW      | Stdlib documents `sqlite3.ProgrammingError`; pin the test to that class (not `Exception`)                                                                                   |
| `sys.exit` mid-body somehow skips finally                                 | LOW        | HIGH     | Python guarantee: `finally` runs during `SystemExit` unwind. Mirrored by `outcomes_sync` which already does this. Regression test specifically exercises a `sys.exit` path. |
| Future caller adds a new `db_conn = connect()` site in `execute`          | LOW        | LOW      | Out of scope for this fix. The right time to address is when CLI-wide refactor lands (intentionally not in this PR).                                                        |
| Reordering changes git blame — annoys archaeology                         | LOW        | LOW      | Acceptable; leak fix outweighs blame churn. Commit message references review H1 for future blame readers.                                                                   |

---

## Notes

**Why no EventBus teardown?** Re-read of `src/core/events/bus.py:35-113` confirms:
- The bus stores only `self._conn` and `self._subscribers` (a `defaultdict(list)` of callables).
- `publish` is fully synchronous: it INSERTs, COMMITs, then iterates subscribers in-process.
- There are no background threads, async tasks, queues, timers, or buffered writes.
- Subscribers are closures that capture `conn` from `register_post_execute_subscribers` (see `src/core/execution/post_execute.py:81-103`). The closures' lifetimes are tied to the `_subscribers` dict; once the bus reference dies (function frame exit), the dict is GC'd along with the closures.

Therefore: closing the underlying `sqlite3.Connection` is sufficient. Adding a `bus.close()` or unsubscribe API would be premature complexity.

**Why mirror `outcomes_sync` instead of factoring out a helper?** The PR review explicitly scopes the fix as targeted (no CLI-wide refactor). Introducing a `with managed_db() as conn:` context manager would be a refactor that touches *every* CLI subcommand. That's the right move eventually, but it's out of scope here. The duplicated `try/finally: conn.close()` pattern is already accepted across 9 sites in `cli.py`; adding two more to match maintains consistency.

**Why 4 test cases not 12?** Each `try/finally` site has one finally clause; the property under test ("conn closed regardless of how the body exits") is uniform across all `sys.exit` / `return` / exception paths. Four representative cases (one per pair of `path × exit-mechanism`) is enough signal. Adding 8 more permutations does not raise confidence — the test would be exercising Python's `finally` semantics, not our code.

**Compatibility with H7 (pretask_sha per attempt) and other High-priority issues**: This fix is fully orthogonal. None of H2-H7 touch the conn lifecycle in `execute`. Landing this first reduces noise for those follow-ups.

**Pattern faithfulness check**: 9 of 9 other DB-using CLI subcommands in `src/cli.py` use exactly this idiom (`postmortems_list`, `learning_list`, `learning_extract`, `learning_propose`, `learning_promote`, `learning_revoke`, `outcomes_sync`, `outcomes_pending`, plus `_run_outcome_sync_preflight`). After this fix, 11 of 11 will conform.

**Confidence note**: The plan asks for a small, idiomatic, well-precedented patch. The only realistic risk is indentation correctness over a long body; mitigated by `py_compile` and ruff. Confidence score: 9/10 for one-pass implementation success.
