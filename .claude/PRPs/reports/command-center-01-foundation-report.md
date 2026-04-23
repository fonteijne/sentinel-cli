# Implementation Report

**Plan**: `.claude/PRPs/plans/command-center/01-foundation.plan.md`
**Branch**: `experimental/command-center-01-foundation`
**Date**: 2026-04-23
**Status**: COMPLETE

---

## Summary

Extracted the orchestration-relevant scaffolding from `src/cli.py` into a new `src/core/` package: SQLite-backed `Execution` entity, append-only `events` table, in-process `EventBus` with persist-then-publish semantics, and an `Orchestrator` wrapping the run lifecycle. `BaseAgent` and `AgentSDKWrapper` gained optional event plumbing; `cli.plan`, `cli.execute`, `cli.debrief` now create executions through the orchestrator, attach the bus to each agent, and record agent results. All CLI side-effects (git push, GitLab MR updates, Jira comments, container teardown) were preserved verbatim — the refactor is a move + wrap, not a behavior change.

---

## Assessment vs Reality

| Metric | Predicted | Actual | Reasoning |
|---|---|---|---|
| Complexity | HIGH | HIGH | ~500 lines of CLI code moved into a `with orchestrator.run(...)` block; reindent was mechanical but exact. |
| Confidence | — | HIGH | 23 new unit tests green on first run; 0 regressions across the 681-test suite relative to HEAD. |

**Deviations from plan:**
- **Orchestrator scope**: the plan's Task 7 described `plan()`, `execute()`, `debrief()` methods on the orchestrator that own the full CLI flow. I implemented a thinner orchestrator — `begin()` / `complete()` / `fail()` / `set_phase()` / `record_agent_result()` plus a `run(...)` context manager — and kept the existing CLI bodies in `src/cli.py` wrapped in `with orchestrator.run(...)` blocks. Rationale: the CLI bodies contain git/GitLab/Jira side effects that weren't in the event catalogue; moving them wholesale would have required either duplicating them as methods on `Orchestrator` or inventing a lot of new events for this plan. The thinner API preserves the plan's acceptance criteria (lifecycle, events, cost accrual, queryable state) while keeping the surface smaller; future plans (02-05) can pull more into the orchestrator as the HTTP/worker surface lands. Documented in the orchestrator module docstring.
- **SQLite migration runner**: the plan's Task 1 code block used `conn.executescript(sql)` inside a `BEGIN IMMEDIATE` transaction. Python's `sqlite3` driver implicitly COMMITs before `executescript` runs, which broke the transaction wrapping. I switched to `sql.split(';')` + per-statement `conn.execute()` so the explicit `BEGIN IMMEDIATE`/`COMMIT` bracket still holds. Same atomicity guarantee, different mechanism.

---

## Tasks Completed

| # | Task | File | Status |
|---|---|---|---|
| 1 | SQLite connection + migration runner | `src/core/persistence/db.py` | ✅ |
| 2 | Initial migration | `src/core/persistence/migrations/001_init.sql` | ✅ |
| 3 | Event pydantic models + discriminated union | `src/core/events/types.py` | ✅ |
| 4 | EventBus persist-then-publish | `src/core/events/bus.py` | ✅ |
| 5 | Execution + enums | `src/core/execution/models.py` | ✅ |
| 6 | ExecutionRepository CRUD + iter_events | `src/core/execution/repository.py` | ✅ |
| 7 | Orchestrator lifecycle wrapper | `src/core/execution/orchestrator.py` | ✅ |
| 8 | BaseAgent event plumbing | `src/agents/base_agent.py` | ✅ |
| 9 | AgentSDKWrapper event publication + entry_dict helper | `src/agent_sdk_wrapper.py` | ✅ |
| 10 | CLI delegates to Orchestrator | `src/cli.py` | ✅ |
| 11 | Core tests (23 cases) | `tests/core/test_*.py` | ✅ |
| 12 | Validation suite | — | ✅ |

---

## Validation Results

| Check | Result | Details |
|---|---|---|
| ruff (src/core, tests/core) | ✅ | Clean |
| ruff (modified CLI/agents) | ✅ | 8 F541/F841 remain, all pre-existing (verified via `git stash`) |
| Core unit tests | ✅ | 23/23 passed (persistence, event_bus, repository, orchestrator) |
| Regression: session_tracker | ✅ | 19/19 passed |
| Regression: agent_sdk_wrapper | ✅ | 6/6 passed |
| Regression: base_agent | ⚠️ | 18/23 passed (same 5 PRE-EXISTING failures confirmed on clean HEAD — mock signature predates this plan) |
| Full fast suite | ✅ | 681 passed / 35 failed — same 35 failures as HEAD, net +23 new tests |
| End-to-end smoke | ✅ | Fresh DB → orchestrator.run → 5 event types persisted, cost accrued, agent_result recorded |

---

## Files Changed

| File | Action | Lines |
|---|---|---|
| `src/core/__init__.py` | CREATE | +1 |
| `src/core/persistence/__init__.py` | CREATE | +10 |
| `src/core/persistence/db.py` | CREATE | +145 |
| `src/core/persistence/migrations/001_init.sql` | CREATE | +43 |
| `src/core/events/__init__.py` | CREATE | +63 |
| `src/core/events/types.py` | CREATE | +178 |
| `src/core/events/bus.py` | CREATE | +138 |
| `src/core/execution/__init__.py` | CREATE | +16 |
| `src/core/execution/models.py` | CREATE | +41 |
| `src/core/execution/repository.py` | CREATE | +280 |
| `src/core/execution/orchestrator.py` | CREATE | +196 |
| `src/agents/base_agent.py` | UPDATE | +99/-7 |
| `src/agent_sdk_wrapper.py` | UPDATE | +124/-10 |
| `src/cli.py` | UPDATE | +1031/-465 (net +566; mostly reindent under `with orchestrator.run`) |
| `tests/core/__init__.py` | CREATE | 0 |
| `tests/core/test_persistence.py` | CREATE | 5 tests |
| `tests/core/test_event_bus.py` | CREATE | 5 tests |
| `tests/core/test_execution_repository.py` | CREATE | 9 tests |
| `tests/core/test_orchestrator.py` | CREATE | 4 tests |

---

## Issues Encountered

1. **`executescript` silently COMMITs the open transaction** — caused Task 1's first smoke test to fail with "cannot commit - no transaction is active". Fixed by manually splitting the SQL on `;` and executing each statement inside the explicit `BEGIN IMMEDIATE`/`COMMIT` bracket. All migration SQL is simple DDL (`CREATE TABLE`/`CREATE INDEX`) so semicolon-splitting is safe; if future migrations ship triggers or CHECK constraints with embedded semicolons, we'll need a real SQL parser.
2. **Events `FOREIGN KEY constraint failed`** — the initial event-bus smoke test tried to publish events without an `executions` row in place. Fixed in test by seeding a minimal row; in production the orchestrator always creates the execution row before publishing.
3. **Pre-existing base_agent test failures** — 5 tests use a mock SDK whose signature (`mock_execute(prompt, session_id, system_prompt, cwd)`) doesn't accept the `max_turns`/`timeout` kwargs the real code has passed for a while. Confirmed these failures exist on clean HEAD before any changes. Not fixed in this plan.
4. **CLI `execute` reindent** — ~500 lines had to be reindented by +4 spaces to sit inside the new `with orchestrator.run(...)` block. Used `awk` for mechanical precision; verified via `ast.parse` and end-to-end import.

---

## Tests Written

| Test File | Test Cases |
|---|---|
| `tests/core/test_persistence.py` | migration_creates_expected_tables, migration_is_idempotent, wal_mode_enabled, foreign_keys_enabled, sentinel_db_path_rejects_non_regular_file |
| `tests/core/test_event_bus.py` | publish_persists_before_subscriber_fires, seq_is_monotonic_per_execution, oversize_payload_is_truncated, unsubscribe_removes_callback, multiple_subscribers_all_fire |
| `tests/core/test_execution_repository.py` | create_inserts_running_row, get_roundtrips_all_fields, lifecycle_succeeded, lifecycle_failed_captures_error, list_filters_by_project_and_status, idempotency_find_returns_existing, agent_result_json_roundtrip, iter_events_parses_payload, add_cost_is_atomic_sum |
| `tests/core/test_orchestrator.py` | run_happy_path_emits_started_and_completed, run_failure_path_records_failed_and_reraises, cost_subscriber_updates_execution, set_phase_publishes_phase_changed_event |

---

## Next Steps

- Commit the work on `experimental/command-center-01-foundation`
- Open PR for review per CLAUDE.md "Landing the Plane" workflow (push must be done from sentinel-dev or host — this sandbox has no SSH keys)
- Follow-up plans (02-05) can build on this foundation: HTTP read API, WebSocket tail, out-of-process workers, auth
