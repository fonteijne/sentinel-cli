# Feature: Validate `SENTINEL_DB_PATH` resolution with a one-shot resolved-path log (PR review M1)

## Summary

`_resolve_path` in `src/core/persistence/db.py` reads `SENTINEL_DB_PATH` from the
environment and returns it after `.expanduser().resolve()` with no further
sanity checks. There is no audit trail for *which* file the operator's process
actually opened, no warning if the path looks suspicious (e.g. ends in
`.txt`, `.sock`, or a random extension), and no defensive handling of symlink
loops in `Path.resolve()`. This is **defense-in-depth** — `SENTINEL_DB_PATH` is
operator-controlled, so this is not exploitable from outside, but a typo or a
stale symlink in the operator's environment would silently write the database
to an unexpected location.

This plan implements **Option 3 (silent-allow + loud audit log) with a small
hardening hat-tip to Option 1**:

1. Once per Python process, the **first** `connect()` call emits a single
   `logger.info(...)` line announcing the resolved DB path and the source
   (explicit-arg / env-var / default). This is the audit trail operators can
   `grep` to confirm where Sentinel is writing.
2. If the resolved path's suffix is not in `{.db, .sqlite, .sqlite3}` (and it
   is not `:memory:`), emit one `logger.warning(...)` line per process. Loud
   for typos like `SENTINEL_DB_PATH=/tmp/sentinel.txt`, but does not refuse.
3. Wrap the `.resolve()` call in a `try/except OSError` so a symlink loop
   surfaces as a clear `ValueError` instead of an opaque `OSError` from deep
   inside `pathlib`.

The change is **silent on the happy path** (info is below the default
WARNING threshold for pytest, so existing tests with `tmp_path / "sentinel.db"`
do not see any new output). The change is **loud only on suspicious input**
(non-DB extension). The change is **never blocking** — Sentinel will still
open the path the operator asked for.

No public API changes. `connect()` and `apply_migrations()` keep their
current signatures.

## User Story

As a Sentinel operator running `sentinel execute` on a host where
`SENTINEL_DB_PATH` may be set in `.env`, a wrapper script, or a stale symlink
I want one log line per process telling me which file Sentinel actually opened
So that a typo or symlink-target drift is visible in stderr / log shipping
instead of silently routing my database to `/tmp/sentinl.db` for the next six
months.

## Problem Statement

`src/core/persistence/db.py:29-34` (verified present at the time of this plan):

```python
def _resolve_path(path: Optional[str]) -> Path:
    """Resolve the DB path. Precedence: explicit arg > env > default."""
    raw = path or os.getenv("SENTINEL_DB_PATH") or _DEFAULT_DB_PATH
    if raw == ":memory:":
        return Path(":memory:")
    return Path(raw).expanduser().resolve()
```

Concrete failure modes that are silently accepted today:

1. **Typo**: `SENTINEL_DB_PATH=~/.sentinl/sentinel.db` (missing `e`) silently
   creates a fresh DB in a parallel directory; the operator believes they are
   pointed at `~/.sentinel/sentinel.db`. Months of data accrue in the wrong
   place.
2. **Wrong extension**: `SENTINEL_DB_PATH=/tmp/sentinel.txt` works (SQLite
   does not care about extension), but is almost certainly an error.
3. **Symlink loop**: a malformed symlink chain causes `Path.resolve(strict=
   False)` to raise `OSError: [Errno 40] Too many levels of symbolic links`
   with no Sentinel-specific context.
4. **No audit trail**: there is no log line confirming where the DB was
   opened. Operators cannot confirm a config change took effect without
   running `lsof` or `strace`.

Testable: after `connect()` returns on a process's first call, the captured
`caplog.records` for the `src.core.persistence.db` logger must contain
**exactly one** INFO record with the resolved path. A second call in the same
process must emit **zero** new records (the once-per-process invariant).

## Solution Statement

Add a module-level `logger = logging.getLogger(__name__)` and a
`_path_logged: bool = False` flag. On the first `connect()` invocation,
`_resolve_path` (or a tiny new sibling helper `_audit_resolved_path`) logs
the resolved path at INFO with a structured "source" tag, sets the flag, and
warns at WARNING if the path's suffix is suspicious. Wrap `.resolve()` in
`try/except OSError` and re-raise as `ValueError("SENTINEL_DB_PATH could not
be resolved: %s")` for symlink-loop friendliness.

The implementation follows the project's logging convention exactly: one
`logger = logging.getLogger(__name__)` at module scope, no separate logging
config (Sentinel uses Python's stdlib root logger configured at the CLI entry
point — see `src/core/learning/cache_invalidator.py:21` for the canonical
pattern).

## Metadata

| Field            | Value                                                                |
| ---------------- | -------------------------------------------------------------------- |
| Type             | BUG_FIX (defense-in-depth hardening)                                 |
| Complexity       | LOW                                                                  |
| Severity         | MEDIUM (PR-review classification, not production-blocking)           |
| Behavior         | **Silent allow** on happy path; **loud warn** on suspicious extension; **never blocks** |
| Systems Affected | `src/core/persistence/db.py`; `tests/core/test_persistence.py`       |
| Dependencies     | None new. `logging` is stdlib.                                       |
| Estimated Tasks  | 4                                                                    |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║  BEFORE — env var honored verbatim, no audit trail                           ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   ┌──────────────────┐    ┌──────────────────┐    ┌─────────────────────┐    ║
║   │ operator sets    │ ──►│ _resolve_path    │ ──►│ sqlite3.connect(    │    ║
║   │ SENTINEL_DB_PATH │    │   (no logging)   │    │   resolved_path)    │    ║
║   │ = /tmp/snt.txt   │    │   (no validation)│    │                     │    ║
║   └──────────────────┘    └──────────────────┘    └─────────────────────┘    ║
║                                                              │                ║
║                                                              ▼                ║
║                                              ┌─────────────────────────────┐  ║
║                                              │ DB silently created at      │  ║
║                                              │ /tmp/snt.txt — typo not     │  ║
║                                              │ surfaced. Operator assumes  │  ║
║                                              │ ~/.sentinel/sentinel.db.    │  ║
║                                              └─────────────────────────────┘  ║
║                                                                               ║
║   PAIN_POINT: typo / stale symlink / wrong extension all silent.              ║
║   DATA_FLOW: env → resolve → connect → write (no observability).              ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║  AFTER — first connect() per process logs resolved path + source              ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   ┌──────────────────┐    ┌──────────────────────────┐    ┌────────────────┐ ║
║   │ operator sets    │ ──►│ _resolve_path             │ ──►│ sqlite3.connect│ ║
║   │ SENTINEL_DB_PATH │    │  → resolve symlinks       │    │  (resolved)    │ ║
║   │ = /tmp/snt.txt   │    │  → first call: log INFO   │    │                │ ║
║   └──────────────────┘    │      "Sentinel DB path:   │    └────────────────┘ ║
║                           │       /tmp/snt.txt        │                       ║
║                           │       (source=env)"       │                       ║
║                           │  → suffix not .db/.sqlite│                       ║
║                           │      → log WARNING        │                       ║
║                           │  → catch OSError →        │                       ║
║                           │      ValueError(loop msg)│                       ║
║                           └──────────────────────────┘                       ║
║                                                                               ║
║   VALUE_ADD: one grep-able audit line per process; loud warn on weird ext;    ║
║              clear error on symlink loops; no blocking on legitimate paths.   ║
║   DATA_FLOW: unchanged on the happy path — only observability added.          ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                        | Before                                     | After                                                                | User Impact                                                |
| ------------------------------- | ------------------------------------------ | -------------------------------------------------------------------- | ---------------------------------------------------------- |
| First `connect()` per process   | silent                                     | one INFO log: "Sentinel DB path: <abs> (source=env\|arg\|default)"   | grep-able audit trail in stderr / log shipping             |
| Second+ `connect()` per process | silent                                     | silent (same — once-per-process flag suppresses re-log)              | no log spam in long-lived processes                        |
| `connect()` w/ suspicious ext   | silent                                     | one WARNING log: "Sentinel DB path has unusual suffix: '.txt'"       | typos visible without raising; never blocks                |
| `connect()` w/ symlink loop     | opaque `OSError: Too many levels...`       | `ValueError("SENTINEL_DB_PATH could not be resolved: <reason>")`     | clearer error message; same exit behavior (raises)         |
| `connect()` happy path          | unchanged                                  | unchanged (info hidden under default WARNING level in tests/Click)   | none                                                       |
| `connect()` w/ explicit arg     | unchanged                                  | source=`arg` in log                                                  | operators can tell whether explicit arg or env var won     |
| `connect(":memory:")` / tests   | unchanged                                  | unchanged — `:memory:` short-circuits before logging+suffix-check    | no log noise from in-memory tests                          |
| Public API of `connect()`       | unchanged                                  | unchanged                                                            | no caller migration                                        |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File                                              | Lines       | Why Read This                                                                    |
| -------- | ------------------------------------------------- | ----------- | -------------------------------------------------------------------------------- |
| P0       | `src/core/persistence/db.py`                      | 1-60        | The function under repair (`_resolve_path` and `connect`); all changes localize here |
| P0       | `src/core/learning/cache_invalidator.py`          | 1-50        | Canonical project logging pattern: `logger = logging.getLogger(__name__)`        |
| P0       | `tests/core/test_persistence.py`                  | 1-72        | Existing persistence tests; new tests live here; pattern uses tmp_path + monkeypatch |
| P1       | `src/core/events/bus.py`                          | 22-30       | Second example of the project's logger declaration idiom                          |
| P1       | `src/core/learning/extract.py`                    | 16-35       | Third example of the same idiom — confirms convention                             |
| P1       | `tests/test_cli_outcomes.py`                      | 42-60       | Reference for `db_path` fixture using `SENTINEL_DB_PATH` env var                  |
| P1       | `tests/test_cli_postmortems.py`                   | 1-100       | Same fixture pattern — confirms tmp_path-based DB paths are the project norm      |
| P2       | `src/core/persistence/__init__.py`                | 1-58        | Confirms the public API surface — only `connect` and `apply_migrations` are exposed |
| P2       | `.claude/PRPs/plans/completed/fix-execute-db-conn-leak.plan.md` | 1-100 | Companion plan style — small, surgical, defensive-only fixes in this codebase    |
| P2       | `.claude/PRPs/reviews/feat-sentinel-learning-system-review.md` | M1 section | Source review note — verify scope match                                            |

**External Documentation:**

| Source                                                                                                            | Section                                | Why Needed                                                                                  |
| ----------------------------------------------------------------------------------------------------------------- | -------------------------------------- | ------------------------------------------------------------------------------------------- |
| [Python pathlib docs (3.11)](https://docs.python.org/3.11/library/pathlib.html#pathlib.Path.resolve)              | `Path.resolve(strict=False)`           | Confirms behavior on symlink loops: raises `OSError`, NOT `RuntimeError`. Strict=False does not silence loop errors. |
| [Python logging docs (3.11)](https://docs.python.org/3.11/library/logging.html#logging.Logger.info)               | Levels                                 | INFO is below the default WARNING threshold — silent in pytest by default, visible in production via Sentinel CLI's `logging.basicConfig(level=INFO)` (verified at `src/cli.py` entrypoint init) |
| [pytest caplog docs (8.x)](https://docs.pytest.org/en/stable/how-to/logging.html#caplog-fixture)                  | `caplog.set_level()`                   | Tests must call `caplog.set_level(logging.INFO, logger="src.core.persistence.db")` to see INFO records |

---

## Patterns to Mirror

**LOGGER_DECLARATION (mirror exactly):**

```python
# SOURCE: src/core/learning/cache_invalidator.py:14-21
# COPY THIS PATTERN at top of src/core/persistence/db.py
import logging
...
logger = logging.getLogger(__name__)
```

**LOGGER_USAGE (mirror exactly — the project uses %-style format strings, not f-strings, for log messages):**

```python
# SOURCE: src/core/learning/cache_invalidator.py:42-48
# COPY THIS PATTERN:
logger.info(
    "prompt cache invalidator completed: %d entries cleared",
    cleared,
)
logger.error("prompt cache invalidator crashed", exc_info=True)
```

**Note**: %-style `%s`/`%d` placeholders, not f-strings. This is consistent
across `cache_invalidator.py`, `outcome_sync.py`, `propose_overlay.py`,
`post_execute.py`, and `bus.py`. Mirror it.

**DEFAULT_PATH_CONSTANT (already present — leave alone):**

```python
# SOURCE: src/core/persistence/db.py:26
_DEFAULT_DB_PATH = "~/.sentinel/sentinel.db"
```

**TEST_FIXTURE (mirror for new tests in `tests/core/test_persistence.py`):**

```python
# SOURCE: tests/core/test_persistence.py:38-52
# COPY THIS PATTERN:
def test_pragmas_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))

    conn = connect()
    ...
```

**CAPLOG_PATTERN (new — but standard pytest):**

```python
# Standard pytest pattern; no in-repo example yet because no persistence-layer
# tests log today. From Python docs and pytest docs:
def test_xxx(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="src.core.persistence.db")
    ...
    records = [r for r in caplog.records if r.name == "src.core.persistence.db"]
    assert len(records) == 1
```

---

## Files to Change

| File                                  | Action | Justification                                                                          |
| ------------------------------------- | ------ | -------------------------------------------------------------------------------------- |
| `src/core/persistence/db.py`          | UPDATE | Add module-level logger + `_path_logged` flag; harden `_resolve_path` with audit log + suffix warn + OSError-to-ValueError |
| `tests/core/test_persistence.py`      | UPDATE | Add 5 new tests covering: first-call logs, second-call silent, env source vs arg vs default source, suspicious-suffix warn, symlink-loop ValueError |

No new files. No `__init__.py` changes (logger is a private module detail).
No public API changes.

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **No path-confinement enforcement.** We will NOT refuse a path outside
  `~/.sentinel`. The brief explicitly calls this defense-in-depth, and tests
  use `tmp_path` (which is *always* outside `~/.sentinel`). Refusing would
  break six existing test files with no security gain.
- **No `SENTINEL_DB_PATH_UNRESTRICTED=1` overlay flag.** Adding an opt-out env
  var trades one piece of defense-in-depth for two. The audit-log-only
  approach is strictly less complex.
- **No CLI-level "current DB path" subcommand.** Out of scope for an M-class
  fix. Operators can `grep` the audit log line.
- **No structured logging migration.** Sentinel uses stdlib %-style logging
  consistently. Don't introduce JSON logging here.
- **No change to `_DEFAULT_DB_PATH`.** Constant stays `~/.sentinel/sentinel.db`.
- **No migration-time validation.** `apply_migrations` is unchanged. Our
  validation lives in `_resolve_path` / `connect`, before any migration runs.
- **No fix for unrelated review findings.** This plan addresses M1 only.
- **No retroactive logging.** We do not log paths used by `connect()` calls
  that happened before this fix landed; only the first call after the patch
  in any new process.
- **No async-safety / multiprocessing.** The `_path_logged` module flag is a
  per-process Python-level boolean. Sub-processes (e.g. inside DooD test
  runners) will each log once. That is the desired behavior.
- **No suffix whitelist customization.** The set `{.db, .sqlite, .sqlite3}`
  is hardcoded. If a future user needs `.s3db`, they will edit the source.
  Out of scope for an M1 fix.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/core/persistence/db.py` — add logger + reset hook

- **ACTION**: Add `import logging` to the existing import block, add module-
  level `logger = logging.getLogger(__name__)`, and add a module-level
  `_path_logged: bool = False` flag plus a `_KNOWN_DB_SUFFIXES` constant.
- **CONCRETE EDITS**:
  - In the import block (currently lines 16-23), insert `import logging` in
    alphabetical order (after `import os`, before `import re`).
  - After `_DEFAULT_DB_PATH = "~/.sentinel/sentinel.db"` (line 26), add:
    ```python
    _KNOWN_DB_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})

    logger = logging.getLogger(__name__)

    # Per-process flag: emit the resolved-path audit log only on first connect().
    # Re-importing the module (e.g. in fresh subprocesses) resets this. Tests
    # that need a clean slate should monkeypatch this back to False.
    _path_logged: bool = False
    ```
- **MIRROR**: `src/core/learning/cache_invalidator.py:14-21` — same idiom
  (`import logging` then `logger = logging.getLogger(__name__)`).
- **GOTCHA 1**: Do NOT call `logging.basicConfig()`. The CLI entrypoint
  configures the root logger; this module just gets a child logger.
- **GOTCHA 2**: The `_path_logged` flag is intentionally module-level (not a
  function attribute) so tests can `monkeypatch.setattr("src.core.persistence.db._path_logged", False)` to reset between tests. This is more discoverable than a function attribute.
- **VALIDATE**: `python -m py_compile src/core/persistence/db.py` (must exit 0); `ruff check src/core/persistence/db.py` (no new errors).

### Task 2: UPDATE `src/core/persistence/db.py` — harden `_resolve_path`

- **ACTION**: Rewrite `_resolve_path` to (a) classify the source of the raw
  path, (b) catch `OSError` from `.resolve()` and re-raise as `ValueError`,
  (c) on first call log the resolved path at INFO, (d) on first call warn at
  WARNING if the suffix is unusual, (e) flip the `_path_logged` module flag.
- **NEW SHAPE**:
  ```python
  def _resolve_path(path: Optional[str]) -> Path:
      """Resolve the DB path. Precedence: explicit arg > env > default.

      Side effects (defense-in-depth):
        - On first call per process, logs the resolved path at INFO so
          operators have a grep-able audit line ("source=arg|env|default").
        - On first call per process, logs a WARNING if the resolved path's
          suffix is not one of {.db, .sqlite, .sqlite3} — typos like
          ``SENTINEL_DB_PATH=/tmp/sentinel.txt`` surface without blocking.
        - Surfaces ``OSError`` from ``Path.resolve`` (e.g. symlink loops)
          as a ``ValueError`` with Sentinel-specific context.
      """
      global _path_logged

      if path is not None:
          raw, source = path, "arg"
      elif (env_val := os.getenv("SENTINEL_DB_PATH")) is not None:
          raw, source = env_val, "env"
      else:
          raw, source = _DEFAULT_DB_PATH, "default"

      if raw == ":memory:":
          # Short-circuit: in-memory DBs are always intentional, never logged
          # (we'd just spam test output).
          return Path(":memory:")

      try:
          resolved = Path(raw).expanduser().resolve()
      except OSError as exc:
          raise ValueError(
              f"SENTINEL_DB_PATH could not be resolved (source={source}, raw={raw!r}): {exc}"
          ) from exc

      if not _path_logged:
          logger.info(
              "Sentinel DB path: %s (source=%s)",
              resolved,
              source,
          )
          if resolved.suffix not in _KNOWN_DB_SUFFIXES:
              logger.warning(
                  "Sentinel DB path has unusual suffix %r (expected one of %s); "
                  "proceeding anyway, but verify SENTINEL_DB_PATH is correct.",
                  resolved.suffix,
                  sorted(_KNOWN_DB_SUFFIXES),
              )
          _path_logged = True

      return resolved
  ```
- **MIRROR**:
  - %-style log format: `src/core/learning/cache_invalidator.py:42-48`.
  - `try/except OSError` re-raise pattern: matches the broader Sentinel
    convention of converting low-level errors to domain-specific errors at
    module boundaries (see `src/core/persistence/db.py:49-52` — already
    raises `ValueError` for "not a regular file").
- **GOTCHA 1**: The current code uses `path or os.getenv(...) or _DEFAULT_DB_PATH`. **This swallows empty strings** (treats `""` like None). The new
  walrus-based explicit branching is **stricter**: an explicit `path=""` is
  now treated as `arg=""`, which `.resolve()` will turn into the CWD. To
  preserve current behavior exactly, keep the `or` semantics: write
  `if path:` (truthy check) instead of `if path is not None:`. Test in
  `tests/core/test_persistence.py` to confirm — the existing `connect()` call
  passes `path=None` everywhere, so this is theoretical, but match the prior
  semantics to avoid surprise. **DECISION**: use `if path:` (truthy) on both
  branches to mirror the old `or` chain exactly.
- **GOTCHA 2**: `Path.resolve(strict=False)` does NOT silence symlink-loop
  `OSError(ELOOP)`. Confirmed via Python 3.11 docs. The `try/except OSError`
  is required.
- **GOTCHA 3**: The walrus assignment `(env_val := os.getenv(...))` is fine
  on Python 3.8+. Sentinel's `pyproject.toml` requires `python = "^3.11"`, so
  this is safe.
- **GOTCHA 4**: `resolved.suffix` returns `""` for paths with no extension
  (e.g. `/tmp/sentinel`). That is unusual for a DB file, so the warning
  fires correctly. **Do not** treat empty suffix as a special case.
- **GOTCHA 5**: The `:memory:` short-circuit must run **before** the
  resolve-and-log block. Otherwise we'd log "Sentinel DB path: :memory:"
  every time anyone runs an in-memory test. The shape above respects this.
- **GOTCHA 6**: Use `global _path_logged` at the top of the function. Mypy
  warns on unannotated global rebinds, but the module-level annotation
  (`_path_logged: bool = False`) is sufficient to satisfy strict mode.
- **GOTCHA 7**: Match the existing function signature exactly:
  `def _resolve_path(path: Optional[str]) -> Path:`. No new parameters.
- **VALIDATE**: `python -m py_compile src/core/persistence/db.py`; then
  `ruff check src/core/persistence/db.py`; then `mypy src/core/persistence/db.py`.
  Mypy must show 0 new errors versus the branch baseline.

### Task 3: UPDATE `tests/core/test_persistence.py` — add 5 new tests

- **ACTION**: Append new tests covering (a) first-call info log, (b)
  second-call silent, (c) suspicious-suffix warning, (d) `:memory:` is
  silent, (e) `OSError` → `ValueError` translation on symlink loop.
- **NEW IMPORT BLOCK ADDITIONS** (top of file, alongside existing imports):
  ```python
  import logging
  import os
  ```
- **TEST 1 — first-call logs the resolved path at INFO**:
  ```python
  def test_resolve_path_logs_resolved_path_on_first_connect(
      tmp_path: Path,
      monkeypatch: pytest.MonkeyPatch,
      caplog: pytest.LogCaptureFixture,
  ) -> None:
      """First connect() per process must emit one INFO line with the resolved path + source."""
      from src.core.persistence import db as db_module

      monkeypatch.setattr(db_module, "_path_logged", False)
      db_path = tmp_path / "sentinel.db"
      monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
      caplog.set_level(logging.INFO, logger="src.core.persistence.db")

      conn = connect()
      try:
          info_records = [
              r for r in caplog.records
              if r.name == "src.core.persistence.db" and r.levelno == logging.INFO
          ]
          assert len(info_records) == 1, [r.getMessage() for r in info_records]
          assert str(db_path.resolve()) in info_records[0].getMessage()
          assert "source=env" in info_records[0].getMessage()
      finally:
          conn.close()
  ```
- **TEST 2 — second-call is silent (once-per-process)**:
  ```python
  def test_resolve_path_logs_only_once_per_process(
      tmp_path: Path,
      monkeypatch: pytest.MonkeyPatch,
      caplog: pytest.LogCaptureFixture,
  ) -> None:
      """A second connect() in the same process must NOT re-log."""
      from src.core.persistence import db as db_module

      monkeypatch.setattr(db_module, "_path_logged", False)
      db_path = tmp_path / "sentinel.db"
      monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
      caplog.set_level(logging.INFO, logger="src.core.persistence.db")

      conn1 = connect()
      conn1.close()
      caplog.clear()

      conn2 = connect()
      try:
          db_records = [r for r in caplog.records if r.name == "src.core.persistence.db"]
          assert db_records == [], [r.getMessage() for r in db_records]
      finally:
          conn2.close()
  ```
- **TEST 3 — suspicious suffix emits WARNING**:
  ```python
  def test_resolve_path_warns_on_unusual_suffix(
      tmp_path: Path,
      monkeypatch: pytest.MonkeyPatch,
      caplog: pytest.LogCaptureFixture,
  ) -> None:
      """A non-{.db,.sqlite,.sqlite3} suffix must produce one WARNING (not block)."""
      from src.core.persistence import db as db_module

      monkeypatch.setattr(db_module, "_path_logged", False)
      db_path = tmp_path / "sentinel.txt"  # wrong extension
      monkeypatch.setenv("SENTINEL_DB_PATH", str(db_path))
      caplog.set_level(logging.INFO, logger="src.core.persistence.db")

      conn = connect()
      try:
          warning_records = [
              r for r in caplog.records
              if r.name == "src.core.persistence.db" and r.levelno == logging.WARNING
          ]
          assert len(warning_records) == 1
          assert ".txt" in warning_records[0].getMessage()
          # Connection still opened — never blocking.
          assert conn.execute("SELECT 1").fetchone()[0] == 1
      finally:
          conn.close()
  ```
- **TEST 4 — `:memory:` is silent**:
  ```python
  def test_resolve_path_memory_db_is_silent(
      monkeypatch: pytest.MonkeyPatch,
      caplog: pytest.LogCaptureFixture,
  ) -> None:
      """In-memory connections must not produce any audit log."""
      from src.core.persistence import db as db_module

      monkeypatch.setattr(db_module, "_path_logged", False)
      monkeypatch.setenv("SENTINEL_DB_PATH", ":memory:")
      caplog.set_level(logging.INFO, logger="src.core.persistence.db")

      conn = connect()
      try:
          db_records = [r for r in caplog.records if r.name == "src.core.persistence.db"]
          assert db_records == [], [r.getMessage() for r in db_records]
      finally:
          conn.close()
  ```
- **TEST 5 — symlink loop translates `OSError` → `ValueError`**:
  ```python
  def test_resolve_path_raises_valueerror_on_symlink_loop(
      tmp_path: Path,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      """A symlink loop should surface as ValueError (not opaque OSError)."""
      from src.core.persistence import db as db_module

      monkeypatch.setattr(db_module, "_path_logged", False)

      loop_a = tmp_path / "a"
      loop_b = tmp_path / "b"
      loop_a.symlink_to(loop_b)
      loop_b.symlink_to(loop_a)

      monkeypatch.setenv("SENTINEL_DB_PATH", str(loop_a / "sentinel.db"))

      with pytest.raises(ValueError, match="SENTINEL_DB_PATH could not be resolved"):
          connect()
  ```
- **MIRROR**: existing `test_pragmas_enabled` at `tests/core/test_persistence.py:38-52` — same
  `tmp_path` + `monkeypatch.setenv("SENTINEL_DB_PATH", ...)` shape.
- **GOTCHA 1**: Each test must reset `_path_logged` via `monkeypatch.setattr`
  because earlier tests in the same pytest session may have flipped it.
  `monkeypatch` automatically reverts after the test, so cross-test
  contamination is bounded.
- **GOTCHA 2**: `caplog.set_level(logging.INFO, logger="src.core.persistence.db")`
  is required because the default root level is WARNING. Without this the
  INFO record is dropped before `caplog` ever sees it. Confirmed at
  https://docs.pytest.org/en/stable/how-to/logging.html#caplog-fixture.
- **GOTCHA 3**: When asserting log content, use `record.getMessage()` (the
  fully-formatted string) rather than `record.msg` (the raw format template)
  — `%s` placeholders are not substituted in `record.msg`.
- **GOTCHA 4**: Test 5 (symlink loop) is POSIX-specific. Sentinel's CI runs
  on Linux containers (per `Dockerfile`), so this is fine. If we ever need
  Windows compatibility, mark this with `@pytest.mark.skipif(sys.platform ==
  "win32", reason="POSIX symlink semantics")`. Not adding that today.
- **GOTCHA 5**: The existing `test_apply_migrations_is_idempotent` and
  `test_executions_events_agent_results_tables_exist` use raw
  `sqlite3.connect(":memory:")` — they bypass `_resolve_path` entirely, so
  they will not trigger logging side effects regardless of `_path_logged`.
  No change needed there.
- **GOTCHA 6**: Tests that DO go through `connect()` (only
  `test_pragmas_enabled` in this file) will now produce one INFO log line.
  This is harmless in pytest (default level WARNING suppresses it from
  stdout), but if we ever care about log noise, the existing test can
  monkeypatch `_path_logged` to True to suppress. **Decision**: leave
  `test_pragmas_enabled` untouched. It does not assert log content.
- **VALIDATE**: `pytest -q tests/core/test_persistence.py -x`. All 8 tests
  (3 existing + 5 new) must pass.

### Task 4: VALIDATE — full diff review and lint sweep

- **ACTION**: Run the project's standard validation gates and confirm no new
  errors are introduced.
- **STEPS**:
  1. `git diff src/core/persistence/db.py` — visual sanity. The diff should
     be: 1 new import (`import logging`), 1 new constant
     (`_KNOWN_DB_SUFFIXES`), 1 new logger declaration, 1 new module flag
     (`_path_logged`), and a rewritten `_resolve_path` body (~25 lines).
     Nothing else touched. `connect()` and `apply_migrations()` are
     unchanged.
  2. `git diff tests/core/test_persistence.py` — should add 2 imports
     (`logging`, `os`) and 5 new test functions. No edits to existing tests.
  3. `ruff check src/core/persistence/db.py tests/core/test_persistence.py`
     — must show 0 new errors versus the branch baseline.
  4. `mypy src/core/persistence/db.py tests/core/test_persistence.py` —
     must show 0 new errors versus the branch baseline (26 pre-existing on
     `feat/sentinel-learning-system`).
  5. `pytest -q tests/core/test_persistence.py -x` — 8/8 pass.
  6. **Regression run on every test that touches `SENTINEL_DB_PATH`**:
     ```
     pytest -q tests/test_cli_outcomes.py tests/test_cli_postmortems.py \
                tests/test_cli_learning.py tests/test_cli_execute_dbconn.py \
                tests/integration/test_phase2c_promotion.py
     ```
     Expect: same pass/fail counts as before this PR.
  7. **Manual check**: `python -c "from src.core.persistence import connect; c = connect(); c.close()"`
     in a fresh shell with `SENTINEL_DB_PATH` unset — should print one INFO
     line if logging is configured, or be silent if not (Sentinel's CLI
     entrypoint will configure logging; `python -c` will not). This is
     informational only; it does not gate the validation.
- **VALIDATE**: All commands exit 0.

---

## Testing Strategy

### Unit Tests to Write

| Test File                              | Test Cases                                                                     | Validates                                              |
| -------------------------------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------ |
| `tests/core/test_persistence.py` (UPDATE) | first-call-info, only-once, suffix-warn, memory-silent, symlink-loop-valueerror | All four `_resolve_path` defense-in-depth properties   |

### Edge Cases Checklist

- [x] First `connect()` call per process logs INFO with resolved path + source.
- [x] Second `connect()` call in the same process is silent.
- [x] `:memory:` connection produces no log lines (covers all in-memory tests).
- [x] Suspicious suffix (`.txt`, `.sock`, `""`) produces one WARNING; connection still opens.
- [x] Symlink loop produces `ValueError` (not raw `OSError`); message includes raw path.
- [x] `connect(path="/explicit/abs/path.db")` logs `source=arg`.
- [x] `connect()` with no env var and no arg logs `source=default` and resolves `~/.sentinel/sentinel.db`. (Implicitly covered by `_DEFAULT_DB_PATH` branch — test it explicitly only if a regression appears.)
- [x] Existing `test_pragmas_enabled` continues to pass (regression).
- [x] Existing `test_apply_migrations_is_idempotent` and `test_executions_events_agent_results_tables_exist` continue to pass (they bypass `_resolve_path`, so cannot regress).
- [x] All 6 cross-suite `SENTINEL_DB_PATH`-using tests still pass with no new noise on stdout/stderr.

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
ruff check src/core/persistence/db.py tests/core/test_persistence.py
mypy src/core/persistence/db.py tests/core/test_persistence.py
python -m py_compile src/core/persistence/db.py
```

**EXPECT**: Exit 0. No new ruff/mypy errors versus the
`feat/sentinel-learning-system` branch baseline.

### Level 2: UNIT_TESTS

```bash
pytest -q tests/core/test_persistence.py -x
```

**EXPECT**: 8 tests pass (3 pre-existing + 5 new).

### Level 3: PEER_REGRESSION

```bash
pytest -q \
  tests/test_cli_outcomes.py \
  tests/test_cli_postmortems.py \
  tests/test_cli_learning.py \
  tests/test_cli_execute_dbconn.py \
  tests/integration/test_phase2c_promotion.py
```

**EXPECT**: Identical pass/fail counts versus pre-PR baseline. Tests that
already use `SENTINEL_DB_PATH` should still pass; new INFO log line is
silent at default pytest log level (WARNING).

### Level 4: FULL_SUITE (run inside `sentinel-dev`, per CLAUDE.md)

```bash
pytest -q
```

**EXPECT**: At minimum, the per-branch baseline is maintained. Net delta
from this PR should be +5 passing, ±0 failing, ±0 errors.

### Level 5: BUILD

N/A — pure Python, no build step.

### Level 6: MANUAL_VALIDATION

Inside `sentinel-dev`:

1. **Audit log on happy path**:
   ```
   SENTINEL_DB_PATH=/tmp/audit-test.db python -c \
     "import logging; logging.basicConfig(level=logging.INFO); \
      from src.core.persistence import connect; \
      c = connect(); c.close()"
   ```
   Expect one line on stderr: `INFO src.core.persistence.db: Sentinel DB
   path: /tmp/audit-test.db (source=env)`.

2. **Suspicious suffix warning**:
   ```
   SENTINEL_DB_PATH=/tmp/audit-test.txt python -c \
     "import logging; logging.basicConfig(level=logging.INFO); \
      from src.core.persistence import connect; \
      c = connect(); c.close()"
   ```
   Expect two lines: the INFO above, plus a WARNING about the unusual `.txt`
   suffix. The connection still succeeds.

3. **Symlink loop ValueError**:
   ```
   ln -s /tmp/loop-a /tmp/loop-b
   ln -s /tmp/loop-b /tmp/loop-a
   SENTINEL_DB_PATH=/tmp/loop-a/x.db python -c \
     "from src.core.persistence import connect; connect()"
   ```
   Expect `ValueError: SENTINEL_DB_PATH could not be resolved (source=env, raw='/tmp/loop-a/x.db'): [Errno 40] Too many levels of symbolic links: '/tmp/loop-a'`.

---

## Acceptance Criteria

- [ ] `_resolve_path` logs the resolved path + source at INFO once per process.
- [ ] `_resolve_path` logs WARNING once per process if the suffix is not in `{.db, .sqlite, .sqlite3}`.
- [ ] `_resolve_path` re-raises `OSError` from `Path.resolve()` as `ValueError` with Sentinel-specific context.
- [ ] `:memory:` short-circuit produces no log lines.
- [ ] Public API (`connect`, `apply_migrations`) is byte-identical in signature and import surface.
- [ ] No CLI behavior change: same exit codes, same stdout text, same stderr aside from the new audit/warn lines.
- [ ] Pattern matches existing module-level logger idiom (`logging.getLogger(__name__)`).
- [ ] Five new tests pass; eight total tests in `tests/core/test_persistence.py` pass.
- [ ] No regressions in the six tests that already monkeypatch `SENTINEL_DB_PATH`.
- [ ] Level 1-3 validation gates pass with no new errors.
- [ ] `git diff` is small and surgical: ~30 lines added in `db.py`, ~5 test functions added.

---

## Completion Checklist

- [x] Task 1 done — logger and module flag added; `python -m py_compile` clean.
- [x] Task 2 done — `_resolve_path` rewritten; mypy clean.
- [x] Task 3 done — 5 new tests added; pytest green (8/8 in test_persistence.py).
- [x] Task 4 done — full validation matrix green, no regressions in any of the 6 cross-suite SENTINEL_DB_PATH-using tests (38/38 pass).
- [x] Diff reviewed by hand — confirm no behavior change beyond logging + ValueError translation.
- [ ] PR review issue M1 referenced in commit message. _(deferred to commit step — out of Ralph scope)_

### Implementation Notes

- **Plan deviation (minor)**: Plan specified catching only `OSError` from
  `Path.resolve()`, citing Python 3.11 docs. In practice, CPython 3.11's
  `pathlib.Path.resolve` re-raises `OSError(ELOOP)` as `RuntimeError("Symlink
  loop from %r")` (see `Lib/pathlib.py:991` — `check_eloop`). The
  implementation therefore catches `(OSError, RuntimeError)` to translate
  symlink-loop errors as the plan intended. The user-facing error message
  format is unchanged.
- **Plan deviation (minor)**: Plan asked to add `import os` to test file, but
  the new tests don't use `os` directly (all env-var manipulation goes through
  `monkeypatch.setenv`). Removed to satisfy ruff F401.
- **Plan deviation (minor)**: Used the walrus operator with truthy semantics
  (`elif env_val := os.getenv(...)`) which already short-circuits on empty
  string — equivalent to the plan's "use `if path:` (truthy)" decision.
- **Full-suite delta**: 1047 → 1052 passing (+5 new tests), 32 → 32 failing
  (no regressions; pre-existing failures in environment_manager,
  jira_server_client, plan_generator, worktree_manager, agent_sdk_*).

---

## Risks and Mitigations

| Risk                                                                                | Likelihood | Impact | Mitigation                                                                                                                                                                                              |
| ----------------------------------------------------------------------------------- | ---------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_path_logged` flag leaks across pytest tests, causing test 1 to see no log         | MEDIUM     | LOW    | Every new test calls `monkeypatch.setattr(db_module, "_path_logged", False)` first. `monkeypatch` auto-reverts after the test.                                                                            |
| %-style log message accidentally f-strings (Bandit / ruff `G004` warning)           | LOW        | LOW    | Plan dictates `%s`/`%d` placeholders explicitly. ruff `G` rules already enforced on this branch — would surface in Level 1.                                                                              |
| Suspicious-suffix warning fires for legitimate paths (e.g. operator uses `.s3db`)   | LOW        | LOW    | Hardcoded set of three suffixes is conservative. Operator can ignore the warning, edit the source if frequent. Docs note this is non-blocking.                                                            |
| Symlink-loop test fails on non-POSIX                                                | LOW        | LOW    | Sentinel CI is Linux-only. If portability becomes a concern, add `@pytest.mark.skipif`.                                                                                                                  |
| INFO log line breaks a test that asserts on full stderr content                     | LOW        | MED    | Default pytest log level is WARNING — INFO is suppressed by default. Existing tests don't capture stderr line-by-line; spot-check during Level 3 regression. The 6 cross-suite tests do not assert on persistence logs. |
| Walrus operator `:=` confuses an older mypy / ruff config                           | LOW        | LOW    | Sentinel pyproject.toml requires Python ≥3.11. Walrus has been mypy-clean since 0.770. Verified by Level 1.                                                                                              |
| `Path.resolve()` blocks on a slow / hung NFS mount                                  | LOW        | MED    | Out of scope for this fix; the existing code already calls `.resolve()` and would exhibit the same pause. Not introduced by this change.                                                                  |
| Operators rely on the silent behavior to suppress audit logs (compliance scenario)  | LOW        | LOW    | INFO-level logs are configurable per Python's logging system; operators can `logging.getLogger("src.core.persistence.db").setLevel(logging.WARNING)` to silence the audit line if needed.                |

---

## Notes

**Why option 3 (audit log) over option 2 (allowlist enforcement)?** Tests use
absolute `tmp_path / "sentinel.db"` (which is *always* outside `~/.sentinel`),
so any "refuse paths outside the base directory" rule would either:
(a) break six existing test files (harmful), or
(b) require a `SENTINEL_DB_PATH_UNRESTRICTED=1` overlay flag (more
complexity, more code paths, more docs). The brief explicitly asks for "the
lightest option that meaningfully reduces footgun risk." A one-shot audit log
fits the bill: it makes the actual behavior visible without changing it.

**Why the `_KNOWN_DB_SUFFIXES` warning if we're not blocking?** Defense-in-
depth means making typos visible. SQLite does not care about file
extensions, but humans do — `SENTINEL_DB_PATH=/tmp/sentinel.txt` is
overwhelmingly more likely to be a typo than an intentional choice. A
WARNING line costs nothing on the legitimate path and surfaces the typo
immediately.

**Why catch `OSError` and re-raise as `ValueError`?** Two reasons:
(a) the existing code at `db.py:49-52` already uses `ValueError` to signal
"the path you gave us is bad"; converting `OSError` to `ValueError` keeps the
external error surface consistent. (b) The `OSError` from a symlink loop
includes a confusing errno (40 / `ELOOP`) and no Sentinel context; the
wrapped `ValueError` includes the raw env-var value and the source tag, which
is what an operator needs.

**Why `_path_logged` as a module-level flag (not `functools.lru_cache`)?**
`lru_cache` would key on the input, so two different paths in the same
process would log twice (which is actually desirable in some operator
scenarios — config-reload loops). The "once per process, regardless of
path" behavior is a **deliberate** UX choice: in the vast majority of
operator workflows, only one DB is opened per process; spamming the log on
every internal `connect()` would dilute the signal. If a future operator use
case wants per-path logging, that is a separate enhancement. We can also
expose a `reset_audit_log()` helper if a long-lived service needs to re-log
after a config reload — but **not in this PR**.

**Why log to `src.core.persistence.db` (the full module path)?** Python's
`logging.getLogger(__name__)` idiom uses the dotted module path. Sentinel's
log shipping (when configured) preserves this name, so operators can filter
on `src.core.persistence.db` to find DB-related events. Mirrors
`cache_invalidator.py:21`, `outcome_sync.py:46`, `propose_overlay.py:43`,
`post_execute.py:37`, `bus.py:30`. **Do not** rename the logger.

**Why no `connect()` API change?** The brief explicitly forbids it. The
audit log lives inside `_resolve_path`, which is private. `connect(path=...)`
remains source-compatible with every existing caller in the codebase
(verified: 11 call sites, none passing kwargs other than `path`).

**Compatibility with H1-H7 fixes**: This change is fully orthogonal. None of
H1-H7 touch `_resolve_path`; this M1 fix touches no function H1-H7 modified.
The diff is entirely additive (new import, new constants, new function body
shape, new tests). Landing this after H1-H7 is the right ordering — defense
in depth comes after the higher-severity issues are settled.

**Why no migration to `connect()` for the audit log instead of
`_resolve_path`?** `connect()` has two early-exit branches (the `:memory:`
short-circuit and the "not a regular file" `ValueError`). Putting the audit
log in `_resolve_path` ensures it fires for **every** real path resolution,
including ones that go on to fail validation. That's the correct ordering: an
operator with a bad path (e.g. pointing at a directory) should still see the
audit log line first so they can confirm what was attempted.

**Pattern faithfulness check**: 5 of 5 other `src/core/**` modules with
non-trivial logic declare a logger via the same idiom
(`logging.getLogger(__name__)`). After this fix, 6 of 6 will conform. No new
patterns are introduced.

**Confidence note**: The plan asks for a small, idiomatic, well-precedented
patch. The change is purely additive (1 import, 4 module-level lines, ~25
lines of new function body) and 5 new tests. The only realistic risk is
flag-leakage between tests, mitigated by `monkeypatch.setattr` in every test.
**Confidence score: 9/10 for one-pass implementation success.**
