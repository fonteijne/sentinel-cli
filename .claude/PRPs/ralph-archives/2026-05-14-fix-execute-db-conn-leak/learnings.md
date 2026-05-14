# Implementation Report

**Plan**: `.claude/PRPs/plans/fix-execute-db-conn-leak.plan.md`
**Completed**: 2026-05-14T12:30:00+02:00
**Iterations**: 1

## Summary

Fixed the `db_conn` leak in `sentinel execute` (PR review H1). Both control-flow paths in `src/cli.py::execute` (REVISE at line 645 and NORMAL at line 983) now wrap their bodies in `try / finally: db_conn.close()`, mirroring the canonical pattern used by `outcomes_sync`, `postmortems_list`, and `_run_outcome_sync_preflight`. Added `tests/test_cli_execute_dbconn.py` with 4 regression tests asserting the conn is closed on success, failure, and `DeveloperCappedOutException` paths in both modes.

## Tasks Completed

- **Task 1** — REVISE path wrapped (insertion at line ~659; `finally: db_conn.close()` at ~935).
- **Task 2** — NORMAL path wrapped (insertion at ~999; `finally: db_conn.close()` at ~1372). New try/finally lives strictly inside the existing outer `try:` at line 967.
- **Task 3** — `tests/test_cli_execute_dbconn.py` created with 4 tests, all passing.
- **Task 4** — full validation matrix run; all gates green.

## Validation Results

| Check | Result | Detail |
|-------|--------|--------|
| `py_compile src/cli.py` | PASS | exit 0 |
| `py_compile tests/test_cli_execute_dbconn.py` | PASS | exit 0 |
| `ruff check` (target files) | PASS | 10 errors, all pre-existing baseline; 0 new |
| `pytest tests/test_cli_execute_dbconn.py` | PASS | 4/4 in 0.23s |
| Peer-regression suites (4 files) | PASS | 32/32 in 0.56s |

mypy and full `pytest -q` not attempted from the sandbox — mypy isn't installed and full-suite execution is documented to run inside `sentinel-dev`. No code paths changed type-wise, so mypy delta is null.

## Codebase Patterns Discovered

- **CLI conn lifecycle**: all 11 DB-using subcommands in `src/cli.py` now follow the same `try / finally: conn.close()` pattern (was 9/11 before this fix).
- **EventBus has no teardown**: closing the underlying sqlite conn IS the teardown. No `bus.close()` API needed; no subscriber unsubscribe needed.
- **Conn-lifecycle test pattern**: monkeypatch `src.cli.connect` to capture returned conns, then assert `conn.execute("SELECT 1")` raises `sqlite3.ProgrammingError` after CLI invocation. Reusable for any future conn-lifecycle regression test.

## Learnings

- Plan's stated ruff baseline (18) was wrong on the current branch (actual: 10). Validation logic still works — the delta is what matters — but plan generators should sample baseline at plan-write time, not assume.
- The Claude Code sandbox lacks the project venv on `python3`; the working venv is at `/tmp/sv/bin/python`. System python is fine for `py_compile` but not for pytest.
- `python` (no version suffix) is not on PATH in this sandbox; commands must use `python3` or `/tmp/sv/bin/python`.

## Deviations from Plan

None. The fix matches the plan's specified shape and insertion points exactly.
