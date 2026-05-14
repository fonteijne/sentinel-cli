# Implementation Report: M5 — Preflight Time Budget

**Plan**: `.claude/PRPs/plans/m5-preflight-time-budget.plan.md`
**Completed**: 2026-05-14
**Iterations**: 1

## Summary

Added a cooperative wall-clock budget to the outcome-sync preflight that runs at
the start of every `sentinel plan` and `sentinel execute`. A total budget of
30 seconds (env-tunable via `OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS`) is enforced
via a `time.monotonic()` deadline checked between projects and between MRs
inside `OutcomeSyncService.sync()`. When the budget is exhausted, a single
WARNING is logged with synced/remaining counts and the remaining project list.
The flag-off no-op path and `sentinel outcomes sync` (operator-driven) paths
are unchanged.

## Tasks Completed

- [x] Task 1: `_outcome_sync_preflight_budget_seconds` helper added to `src/cli.py`
- [x] Task 2: `deadline: Optional[float] = None` kwarg added to `OutcomeSyncService.sync()`; mid-loop + pre-revert-fetch checks added
- [x] Task 3: `_run_outcome_sync_preflight` bounded by deadline; loud WARNING on cap-hit
- [x] Task 4: 3 new tests in `tests/core/test_outcome_sync.py::TestSyncDeadline`
- [x] Task 5: 4 new tests in `tests/test_cli_outcomes.py`
- [x] Task 6: Pre-existing test suites still pass (39/39 in 3 target files)
- [x] Task 7: Level 1-3 validation pipeline — zero new ruff/mypy errors, full suite has only pre-existing failures

## Files Changed

| File | Change |
| ---- | ------ |
| `src/cli.py` | Added `_outcome_sync_preflight_budget_seconds()` helper (after `_outcome_sync_enabled()`); rewrote `_run_outcome_sync_preflight` to enforce deadline, log structured WARNING, propagate deadline into `service.sync()` |
| `src/core/learning/outcome_sync.py` | Added `import time`; added `deadline: Optional[float] = None` keyword-only arg to `sync()`; added pre-revert-fetch deadline check; added per-MR loop deadline check (placed BEFORE `summary.mrs_seen += 1` so the counter reflects MRs actually processed) |
| `tests/core/test_outcome_sync.py` | Added `import logging`; added `TestSyncDeadline` class with 3 tests |
| `tests/test_cli_outcomes.py` | Added 4 new tests for the M5 budget surface |

## Validation Results

| Check | Result | Notes |
| ----- | ------ | ----- |
| Ruff (changed files) | PASS | Same 11 pre-existing errors as `main` baseline; zero new lint errors |
| Mypy (changed src) | PASS | Same 7 pre-existing errors as `main` baseline; zero new type errors |
| Tests in `tests/test_cli_outcomes.py` | PASS | 8/8 (4 pre-existing + 4 new) |
| Tests in `tests/core/test_outcome_sync.py` | PASS | 33/33 (30 pre-existing + 3 new) |
| Tests in `tests/integration/test_phase3a_outcomes.py` | PASS | 1/1 |
| Full suite (`pytest tests/`) | PASS for in-scope | 1020 passed, 26 pre-existing failures only (test_environment_manager / test_jira_server_client / test_plan_generator / test_worktree_manager) |

## Acceptance Criteria

- [x] `OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS` env var read at call time (function-local `os.getenv`)
- [x] Default 30s; 0 / negative / non-numeric coerced to default
- [x] `_run_outcome_sync_preflight` exits early when deadline crossed; logs ONE WARNING with `synced=`, `remaining=`, `elapsed=`, `budget=`, `remaining_projects=` tokens
- [x] `OutcomeSyncService.sync(deadline=...)` short-circuits between MRs + before `_fetch_revert_candidates`; partial summary returned without advancing watermark past unhandled MRs
- [x] `deadline=None` (default) preserves existing behavior — full backward-compat
- [x] `sentinel outcomes sync` CLI subcommand unchanged (no deadline applied)
- [x] Flag-off no-op path unchanged (call sites at `cli.py:229-233` and `:624-628` not modified)
- [x] All 7 new tests pass
- [x] Mypy + ruff: no new errors on changed lines

## Deviations from Plan

1. **Test for `test_preflight_logs_loud_warning_when_budget_exhausted`** —
   The plan's suggested approach (`OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS=0.0001`)
   is timing-dependent and proved flaky on this machine: the mocked sync call
   completed within 0.1ms, so the deadline check at iter 1 never tripped.
   Replaced with a deterministic `patch.object(time_module, "monotonic", ...)`
   that returns a fake clock advancing past the deadline mid-loop. Same
   coverage with zero flakiness, in keeping with the plan's "deterministic;
   no sleeping" gotcha (Risks table, row 4).

2. **Second `_seed_execution` for `test_deadline_mid_loop_advances_only_past_handled_mrs`** —
   The plan's draft only seeded one execution row but referenced two MRs with
   different ticket IDs. To make the watermark advance to MR #2's `updated_at`
   in the unbounded re-run, both tickets need a matching execution row.

## Codebase Patterns Discovered

- **`time.monotonic` patching pattern for sync code**: when the SUT does
  `import time; time.monotonic()` inside a function body (deferred import),
  patch `time.monotonic` on the actual `time` module (`patch.object(time_module,
  "monotonic", ...)`) — not `cli_module.time` which doesn't exist.
- **Function-local env var helpers** (`_outcome_sync_enabled`,
  `_outcome_sync_preflight_budget_seconds`): consistent project pattern for
  hot-reload-friendly env flags. Read at call time, not module import.
- **Watermark idempotency invariant**: `OutcomeSyncService.sync()` only advances
  `last_seen_updated_at` past MRs whose `_process_mr` returned `handled=True`.
  This made the deadline-break at the loop boundary automatically idempotent —
  no extra bookkeeping required.

## Follow-up Work (out of scope)

None required. Future enhancements noted in the plan's "Notes" section:
- Rotate project sync order by `last_synced_at ASC` so a slow project doesn't
  always consume the budget first.
- Emit a `PreflightBudgetExhausted` event for dashboards.
- Per-call-site budget (lower for `plan`, higher for `execute`).
