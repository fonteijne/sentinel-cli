# Implementation Report: M1 — SENTINEL_DB_PATH Validation

**Plan**: `.claude/PRPs/plans/m1-sentinel-db-path-validation.plan.md`
**Completed**: 2026-05-14
**Iterations**: 1

## Summary

Hardened `_resolve_path` in `src/core/persistence/db.py` with defense-in-depth
observability:

1. One INFO log line per process announcing the resolved DB path and source
   (`arg` / `env` / `default`) — grep-able audit trail.
2. One WARNING log line per process if the resolved path's suffix is not in
   `{.db, .sqlite, .sqlite3}`. Non-blocking; surfaces typos like
   `SENTINEL_DB_PATH=/tmp/sentinel.txt`.
3. `Path.resolve()` errors (both `OSError` and Python 3.11's
   `RuntimeError("Symlink loop ...")`) are translated to `ValueError` with
   Sentinel-specific context.

The change is silent on the happy path (INFO is below pytest's default
WARNING level), loud only on suspicious input, and never blocking.

## Tasks Completed

- [x] Task 1: `import logging` + `logger = logging.getLogger(__name__)` +
  `_path_logged: bool = False` + `_KNOWN_DB_SUFFIXES` constant added to
  `src/core/persistence/db.py`.
- [x] Task 2: `_resolve_path` rewritten with source-classification, OSError/
  RuntimeError catch, and once-per-process audit log + suspicious-suffix
  warning.
- [x] Task 3: Five new tests appended to `tests/core/test_persistence.py`
  covering first-call info log, second-call silent, suspicious-suffix warn,
  `:memory:` silent, and symlink-loop ValueError translation.
- [x] Task 4: Full validation matrix green.

## Validation Results

| Check                          | Result                                |
|--------------------------------|---------------------------------------|
| `python3 -m py_compile`        | PASS                                  |
| `ruff check`                   | PASS (0 errors)                       |
| `mypy`                         | PASS (0 errors)                       |
| `pytest tests/core/test_persistence.py` | PASS (8/8)                   |
| Level 3 peer regression (5 files) | PASS (38/38)                       |
| Full suite (`pytest -q`)       | 1052 passed, 32 pre-existing failed  |

Baseline (without changes): 1047 passed, 32 failed. Delta: **+5 passing, ±0
failing**.

## Codebase Patterns Discovered

- `pathlib.Path.resolve()` in CPython 3.11+ re-raises `OSError(ELOOP)` as
  `RuntimeError("Symlink loop from %r")`. Catch both `(OSError, RuntimeError)`
  when translating resolution failures.
- `caplog.set_level(logging.INFO, logger="<module path>")` is required to
  capture INFO records — pytest's default level is WARNING.
- `monkeypatch.setattr(module, "_module_flag", value)` auto-reverts after
  the test, so module-level mutable state can be safely reset per-test.

## Deviations from Plan

1. **OSError → (OSError, RuntimeError)**: Plan claimed Python 3.11's
   `Path.resolve(strict=False)` raises `OSError` on symlink loops. In
   practice CPython 3.11 wraps it in `RuntimeError`. Implementation catches
   both. Same user-facing error message.
2. **Removed `import os` from test imports**: Plan specified adding it, but
   it was unused (env-var manipulation uses `monkeypatch.setenv`). Removed
   to satisfy `ruff F401`.
3. **Walrus + truthy**: Used `elif env_val := os.getenv("SENTINEL_DB_PATH"):`
   which short-circuits on empty string and matches prior `or`-chain
   semantics in one expression.

## Files Changed

- `src/core/persistence/db.py` — +44 lines (new logger, flag, constant,
  rewritten `_resolve_path` body)
- `tests/core/test_persistence.py` — +127 lines (5 new tests + `import logging`)

## Follow-up Work

None required. The plan is fully executed and all acceptance criteria met.
