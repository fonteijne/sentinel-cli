# Implementation Report

**Plan**: /workspace/sentinel/.claude/PRPs/plans/m6-eventbus-publish-atomic-seq.plan.md
**Completed**: 2026-05-14
**Iterations**: 1

## Summary

Replaced the two-statement (SELECT then INSERT) publish path in `EventBus.publish` with a single atomic `INSERT INTO events ... SELECT ?, COALESCE(MAX(seq), 0)+1, ?, ?, ?, ? FROM events WHERE execution_id = ?` statement. This makes the per-execution `seq` invariant correct-by-construction: two writer connections to the same SQLite DB will serialize on the database write lock and cannot collide on the `(execution_id, seq)` PRIMARY KEY. No schema migration. Module + publish docstrings updated. New regression test exercises two `EventBus` instances over a shared DB file.

## Tasks Completed

- Task 1: `src/core/events/bus.py` — collapsed SELECT+INSERT into a single INSERT...SELECT statement; updated module-level invariant #2 docstring; updated `publish` step-list docstring.
- Task 2: `tests/core/test_event_bus.py` — added `test_concurrent_writers_do_not_collide_on_seq` using `tmp_path` file-DB; added `from pathlib import Path` import; six interleaved publishes across two `EventBus` instances, asserts `[1..6]` and uniqueness.
- Task 3: regression sweep — `tests/core/` 121 pass; full suite 1013 pass.

## Validation Results

| Check | Result |
|-------|--------|
| ruff (bus.py + test_event_bus.py) | PASS |
| mypy (src/core/events/bus.py) | PASS |
| pytest tests/core/test_event_bus.py | PASS (6/6) |
| pytest tests/core/ | PASS (121/121) |
| pytest -q (full) | PASS for all in-scope; 26 pre-existing failures in environment_manager / jira_server_client / plan_generator / worktree_manager are unrelated to M6 (called out by orchestrator) |
| manual validation script | prints `[1, 2, 3, 4, 5, 6]` |

## Codebase Patterns Discovered

- File-DB tests use the `tmp_path` pytest builtin; `:memory:` is incorrect for multi-connection tests because each in-memory connection gets a private database.
- The project uses raw `sqlite3.connect(...)` plus manual `PRAGMA foreign_keys=ON` and `apply_migrations(conn)` for test fixtures (see `_conn_with_execution`); `connect()` from `db.py` is reserved for production paths that want WAL.

## Learnings

- `INSERT INTO t (...) SELECT ..., COALESCE(MAX(seq),0)+1, ... FROM t WHERE execution_id=?` is the cleanest way to express a per-key monotonic counter without schema changes or explicit BEGIN IMMEDIATE.
- The `execution_id` parameter must appear twice in the parameter tuple — once as the inserted column value, once for the `WHERE` clause that scopes the MAX.

## Deviations from Plan

None. Implementation matches the plan exactly.
