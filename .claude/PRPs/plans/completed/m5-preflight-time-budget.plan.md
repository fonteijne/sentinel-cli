# Feature: M5 — Bounded Execution Time for `_run_outcome_sync_preflight`

## Summary

Add a cooperative wall-clock budget to the outcome-sync preflight that runs at
the start of every `sentinel plan` and `sentinel execute`. A total budget of
30 seconds (env-tunable) is enforced via a `time.monotonic()` deadline checked
(a) between projects in the preflight loop and (b) between MRs inside
`OutcomeSyncService.sync()`. When the budget is exhausted, a single loud
WARNING is logged that names the projects synced, the projects remaining,
the elapsed time, and the configured budget. The flag-off no-op path is
unchanged. The `sentinel outcomes sync` CLI subcommand stays unbounded
(operator-driven; the budget is preflight-only).

## User Story

As a Sentinel operator
I want the outcome-sync preflight to be bounded by a configurable wall-clock budget
So that `plan`/`execute` cannot be silently delayed by slow GitLab syncs, and
when a delay does happen I am loudly told which projects are misbehaving.

## Problem Statement

`_run_outcome_sync_preflight` (`src/cli.py:1808`) loops over every project in
`project_sync_state` and calls `OutcomeSyncService.sync(project=proj)` for each.
There is no time cap. With many projects × stale watermarks × `list_merged_mrs_since`
paginating up to 1000 pages of 100 MRs (`src/gitlab_client.py:293`), the worst-case
preflight wall-clock is unbounded. The flag-on exception path swallows errors
correctly, but a slow (not failing) sync silently delays the entrypoint with
no operator signal.

After H5's optimization (`_fetch_revert_candidates` is now one paginated call
per sync rather than one per Sentinel MR), the typical preflight is much faster,
but the worst-case is unchanged.

## Solution Statement

Introduce a deadline-based cooperative cancellation pattern:

1. The preflight reads `OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS` (default 30) and
   computes `deadline = time.monotonic() + budget` once.
2. Add an optional `deadline: Optional[float] = None` keyword argument to
   `OutcomeSyncService.sync()`. When provided, the per-MR loop and the
   pre-revert-fetch checkpoint short-circuit when `time.monotonic() >= deadline`.
3. The preflight loop itself checks the deadline between projects; on
   exhaustion it logs a structured WARNING and breaks.
4. Watermark idempotency is preserved automatically: the existing
   `max_updated_at_handled` invariant only advances past MRs whose
   `_process_mr` returned `handled=True`; a deadline-triggered `break`
   leaves un-iterated MRs un-handled, so the next preflight resumes cleanly.

Why total-budget-with-mid-loop-checks (option 1, refined) over per-project
(option 2) or both (option 3):

- Operator's real complaint is total perceived latency on `plan`/`execute`.
  Per-project caps still let N×10s = 30s+ slip through silently when N
  grows.
- Putting the deadline check inside `sync()` between MRs gives the same
  fairness benefit as per-project caps for the dominant case (one project
  with a deep backlog) — without a second clock.
- One env var, one log message, one place to debug.

`signal.alarm` is rejected (POSIX-only, breaks under threading).
`concurrent.futures` is rejected (`Future.cancel()` cannot abort a
`requests.get()` in flight; the worker thread keeps consuming sockets and
DB connections after the future is abandoned).

## Metadata

| Field            | Value                                                                |
| ---------------- | -------------------------------------------------------------------- |
| Type             | ENHANCEMENT                                                          |
| Complexity       | LOW                                                                  |
| Systems Affected | `src/cli.py`, `src/core/learning/outcome_sync.py`, tests             |
| Dependencies     | Python stdlib only (`time.monotonic`); no new packages               |
| Estimated Tasks  | 7                                                                    |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                              BEFORE STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   sentinel plan TICKET-123                                                    ║
║          │                                                                    ║
║          ▼                                                                    ║
║   ┌───────────────────────┐                                                   ║
║   │ OUTCOME_SYNC_ENABLED  │── flag on ──┐                                     ║
║   └───────────────────────┘             ▼                                     ║
║                          ┌───────────────────────────────────┐                ║
║                          │  _run_outcome_sync_preflight      │                ║
║                          │  ┌─────────────────────────────┐  │                ║
║                          │  │ for proj in N projects:     │  │                ║
║                          │  │   service.sync(project=...) │  │ ← UNBOUNDED    ║
║                          │  │     [paginate up to 1000    │  │   silent       ║
║                          │  │      pages × 100 MRs]       │  │   delay        ║
║                          │  └─────────────────────────────┘  │                ║
║                          └───────────────────────────────────┘                ║
║                                       │                                       ║
║                                       ▼                                       ║
║                                  Plan workflow                                ║
║                                                                               ║
║   PAIN_POINT: Operator sees plan "hung". No log shows which project. No       ║
║               way to bound perceived latency. Worst case scales linearly with ║
║               project count × backlog depth.                                  ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                               AFTER STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   sentinel plan TICKET-123                                                    ║
║          │                                                                    ║
║          ▼                                                                    ║
║   ┌───────────────────────┐                                                   ║
║   │ OUTCOME_SYNC_ENABLED  │── flag on ──┐                                     ║
║   └───────────────────────┘             ▼                                     ║
║                          ┌─────────────────────────────────────────┐          ║
║                          │  _run_outcome_sync_preflight            │          ║
║                          │   deadline = monotonic() + budget       │          ║
║                          │  ┌───────────────────────────────────┐  │          ║
║                          │  │ for proj in N projects:           │  │          ║
║                          │  │   if monotonic() >= deadline:     │  │          ║
║                          │  │     log.warning(remaining=N-i…)   │  │ ← BOUND  ║
║                          │  │     break                         │  │   30s    ║
║                          │  │   service.sync(project=…,         │  │          ║
║                          │  │                deadline=deadline)  │  │          ║
║                          │  │       └─ checks deadline between   │  │          ║
║                          │  │           MRs; returns partial     │  │          ║
║                          │  │           summary                  │  │          ║
║                          │  └───────────────────────────────────┘  │          ║
║                          └─────────────────────────────────────────┘          ║
║                                       │                                       ║
║                                       ▼                                       ║
║                                  Plan workflow                                ║
║                                                                               ║
║   VALUE_ADD:                                                                  ║
║     • Bounded perceived latency on plan/execute.                              ║
║     • Loud structured WARNING on cap-hit (project list + counts).             ║
║     • Idempotent: skipped projects retry on next preflight (watermark         ║
║       only advanced past handled MRs).                                        ║
║     • Tunable via OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS without code edit.    ║
║     • `sentinel outcomes sync` (operator command) stays unbounded.            ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                                          | Before                                | After                                                                | User Impact                                                 |
| ----------------------------------------------------------------- | ------------------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------- |
| `sentinel plan` / `sentinel execute` (preflight)                  | Unbounded sync                        | Bounded by `OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS` (default 30s)     | Plan/execute return predictably; loud log on overrun        |
| `OutcomeSyncService.sync()` API                                   | No `deadline` kwarg                   | Optional `deadline: float \| None` kwarg; default None = unbounded   | Backward-compatible; existing callers unchanged             |
| `sentinel outcomes sync` CLI                                      | Unbounded                             | Unbounded (unchanged — operator-explicit)                            | No change                                                   |
| Logs                                                              | No signal                             | One WARNING line on cap-hit listing remaining projects               | Operators see slow preflights as a single loud line in logs |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File                                            | Lines     | Why Read This                                                                              |
| -------- | ----------------------------------------------- | --------- | ------------------------------------------------------------------------------------------ |
| P0       | `src/cli.py`                                    | 86-95     | `_outcome_sync_enabled` env-flag pattern to MIRROR for the new budget env var              |
| P0       | `src/cli.py`                                    | 1808-1832 | `_run_outcome_sync_preflight` — function we're modifying                                   |
| P0       | `src/core/learning/outcome_sync.py`             | 218-357   | `OutcomeSyncService.sync()` — add `deadline` kwarg here; understand `max_updated_at_handled` invariant |
| P0       | `src/core/learning/outcome_sync.py`             | 262-323   | Per-MR loop where between-MR deadline check goes                                           |
| P1       | `src/cli.py`                                    | 224-234   | Plan call site (preflight wrapper); MUST stay unchanged                                    |
| P1       | `src/cli.py`                                    | 622-628   | Execute call site (preflight wrapper); MUST stay unchanged                                 |
| P1       | `src/cli.py`                                    | 3460-3515 | `outcomes sync` subcommand — MUST NOT pass deadline (operator-driven path)                 |
| P1       | `src/agent_sdk_wrapper.py`                      | 358-428   | Reference for `time.monotonic()` deadline pattern in this codebase                         |
| P2       | `tests/test_cli_outcomes.py`                    | all       | Test file we'll extend; mirror its fixtures and patterns                                   |
| P2       | `tests/core/test_outcome_sync.py`               | 1-105     | Service-level test patterns (Mock(spec=GitLabClient), `_make_mr`, `_make_gitlab_mock`)     |
| P2       | `src/gitlab_client.py`                          | 287-365   | `list_merged_mrs_since` — confirms pagination is the latency hotspot                       |

**External Documentation:**

| Source                                                                                                                | Section                          | Why Needed                                                         |
| --------------------------------------------------------------------------------------------------------------------- | -------------------------------- | ------------------------------------------------------------------ |
| [Python time docs](https://docs.python.org/3.11/library/time.html#time.monotonic)                                      | `time.monotonic`                 | Canonical wall-clock budget primitive; not affected by NTP drift   |
| [Python concurrent.futures docs](https://docs.python.org/3.11/library/concurrent.futures.html#concurrent.futures.Future.cancel) | `Future.cancel`                  | Confirms that we cannot truly cancel an in-flight HTTP request — justifies cooperative-deadline approach over `Future.result(timeout=)` |

---

## Patterns to Mirror

**ENV_FLAG_HELPER (function-style, read at call time):**

```python
# SOURCE: src/cli.py:86-95
# COPY THIS PATTERN (rename, change var name, change default):
def _outcome_sync_enabled() -> bool:
    """Phase 3A feature flag — set OUTCOME_SYNC_ENABLED=1 to enable
    pull-on-demand outcome ingestion at the start of plan/execute and via
    the explicit `sentinel outcomes sync` subcommand.

    Default off until the exit-criterion fixture (one merged MR + one
    reverted MR + one post-merge-CI failure) tags correctly. Mirrors the
    EXTRACTION_ENABLED / OVERLAY_PROPOSER_ENABLED pattern.
    """
    return os.getenv("OUTCOME_SYNC_ENABLED", "0") == "1"
```

**DEADLINE_PATTERN (existing in codebase — must mirror exactly):**

```python
# SOURCE: src/agent_sdk_wrapper.py:65-66
# COPY THIS PATTERN (use time.monotonic, not asyncio):
deadline = asyncio.get_event_loop().time() + timeout
while self._stream_active and asyncio.get_event_loop().time() < deadline:
    ...

# Equivalent for sync code (we'll use this):
import time  # already imported in cli.py
deadline = time.monotonic() + budget_seconds
# ...
if time.monotonic() >= deadline:
    break
```

**LOGGING_PATTERN (module-level logger, lazy %-formatting):**

```python
# SOURCE: src/cli.py:140 + src/core/learning/outcome_sync.py:46
# COPY THIS PATTERN:
logger = logging.getLogger(__name__)
# ...
logger.warning("outcome sync preflight failed: %s", e)
# Multi-arg form for structured warnings (mirror gitlab_client.py:354-362):
logger.warning(
    "outcome sync preflight budget exhausted after %ds: synced=%d remaining=%d "
    "(remaining_projects=%s, budget_s=%d)",
    elapsed_s, synced, remaining, remaining_projects, budget_s,
)
```

**PREFLIGHT_GUARD_PATTERN (must remain at call sites unchanged):**

```python
# SOURCE: src/cli.py:229-233 (and again at 624-628)
# DO NOT MODIFY (this is the call site; only the preflight body changes):
if _outcome_sync_enabled():
    try:
        _run_outcome_sync_preflight(project=None)
    except Exception as e:
        logger.warning("outcome sync preflight failed: %s", e)
```

**SERVICE_KWARG_DEFAULT_PATTERN (keyword-only args with defaults):**

```python
# SOURCE: src/core/learning/outcome_sync.py:218-225
# COPY THIS PATTERN (add `deadline: Optional[float] = None` after `dry_run`):
def sync(
    self,
    *,
    project: str,
    since: Optional[str] = None,
    full_backfill: bool = False,
    dry_run: bool = False,
    deadline: Optional[float] = None,  # <-- new, keyword-only, defaults to None
) -> OutcomeSyncSummary:
```

**TEST_FIXTURE_PATTERN (CliRunner + mocked GitLabClient + tmp DB):**

```python
# SOURCE: tests/test_cli_outcomes.py:42-59
# COPY THIS PATTERN for new preflight tests:
@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "sentinel.db"
    monkeypatch.setenv("SENTINEL_DB_PATH", str(path))
    conn = connect(str(path))
    try:
        apply_migrations(conn)
    finally:
        conn.close()
    yield path
```

**SERVICE_TEST_PATTERN (in-memory DB + mocked GitLab):**

```python
# SOURCE: tests/core/test_outcome_sync.py:93-105
# COPY THIS PATTERN for new sync(deadline=...) tests:
def _make_gitlab_mock(
    *,
    merged_mrs: Optional[List[Dict[str, Any]]] = None,
    pipelines: Optional[List[Dict[str, Any]]] = None,
    merge_requests: Optional[List[Dict[str, Any]]] = None,
) -> Mock:
    gl = Mock(spec=GitLabClient)
    gl.list_merged_mrs_since.return_value = merged_mrs or []
    gl.list_pipelines_for_commit.return_value = pipelines or []
    gl.list_merge_requests.return_value = merge_requests or []
    return gl
```

---

## Files to Change

| File                                       | Action | Justification                                                                                       |
| ------------------------------------------ | ------ | --------------------------------------------------------------------------------------------------- |
| `src/cli.py`                               | UPDATE | Add `_outcome_sync_preflight_budget_seconds()` helper and bound the loop in `_run_outcome_sync_preflight` |
| `src/core/learning/outcome_sync.py`        | UPDATE | Add optional `deadline: Optional[float] = None` kwarg to `sync()`; check between MRs                |
| `tests/test_cli_outcomes.py`               | UPDATE | Add cap-hit / loud-log / idempotency tests for the preflight                                        |
| `tests/core/test_outcome_sync.py`          | UPDATE | Add `sync(deadline=...)` tests: short-circuit between MRs, watermark advances only past handled MRs |

No new files. No migrations. No new dependencies.

---

## NOT Building (Scope Limits)

- **NOT** moving the preflight to a background thread / async task. (Out of scope per the issue brief; the cap is the requested fix, not the architecture.)
- **NOT** adding a per-project cap. The total-budget + between-MR check inside `sync()` already handles the "one bad project" case fairly without a second clock to debug.
- **NOT** removing the preflight (D8 from the review).
- **NOT** adding a deadline to `sentinel outcomes sync`. That command is operator-explicit; if an operator runs it, they accept the wait.
- **NOT** propagating the deadline below `sync()` (e.g., into `list_merged_mrs_since` pagination). The check between MRs is granular enough — `requests.get` for one page is bounded by GitLab's response time, and adding HTTP-level timeout knobs is a separate concern (already partially handled by the GitLab client's existing `max_pages` safety hatch).
- **NOT** changing the env-flag default. The budget env var defaults to `30` (active) but the preflight itself is still gated by `OUTCOME_SYNC_ENABLED`. Setting `OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS=0` should be treated as "use the default" (don't allow operators to disable the cap by accident — see Task 1 GOTCHA).
- **NOT** instrumenting elapsed time as a metric / event. A WARNING log is sufficient for now; metrics are future work.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/cli.py` — add `_outcome_sync_preflight_budget_seconds()` helper

- **ACTION**: Add a new module-level helper next to `_outcome_sync_enabled()` (after line 95).
- **IMPLEMENT**:
  ```python
  def _outcome_sync_preflight_budget_seconds() -> float:
      """M5 budget — total wall-clock cap for the outcome-sync preflight.

      Read at call time so flipping the env var takes effect on the next
      `plan`/`execute`. Default 30 seconds. Mirrors the
      OUTCOME_SYNC_ENABLED / EXTRACTION_ENABLED env-flag pattern.

      Values <= 0 are coerced to the default (operators cannot disable the
      cap accidentally; explicit unbounded mode is the unguarded `sentinel
      outcomes sync` subcommand).
      """
      raw = os.getenv("OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS", "30")
      try:
          parsed = float(raw)
      except ValueError:
          return 30.0
      return parsed if parsed > 0 else 30.0
  ```
- **MIRROR**: `src/cli.py:86-95` — same docstring shape, same `os.getenv` idiom.
- **GOTCHA**: Coerce non-positive and non-numeric values to 30. Operators tweaking the env should never end up with 0s = instant break before any sync runs.
- **VALIDATE**: `cd /workspace/sentinel && poetry run mypy src/cli.py`

### Task 2: UPDATE `src/core/learning/outcome_sync.py` — add `deadline` kwarg to `sync()`

- **ACTION**: Add `deadline: Optional[float] = None` keyword-only argument to `OutcomeSyncService.sync()`.
- **IMPLEMENT**:
  1. Add the kwarg in the signature (after `dry_run: bool = False`).
  2. Update the docstring's `Args:` block to describe `deadline` (semantics: "monotonic-clock deadline; when `time.monotonic() >= deadline` mid-loop, return the partial summary without advancing past unhandled MRs").
  3. Add `import time` at the top of the file (next to other stdlib imports — currently sorted: `import json, logging, re, sqlite3`).
  4. Insert a deadline check **before** the per-MR loop body (between `for mr in mrs:` and `summary.mrs_seen += 1`):
     ```python
     for mr in mrs:
         if deadline is not None and time.monotonic() >= deadline:
             logger.warning(
                 "outcome_sync deadline reached mid-project: project=%s "
                 "mrs_seen=%d mrs_remaining=%d — returning partial summary",
                 project, summary.mrs_seen, len(mrs) - summary.mrs_seen,
             )
             break
         summary.mrs_seen += 1
         ...
     ```
     (Place the check **before** `summary.mrs_seen += 1` so the counter reflects MRs actually processed, not skipped.)
  5. Insert a deadline check **before** `_fetch_revert_candidates` (the second-most expensive call after `list_merged_mrs_since`):
     ```python
     if sentinel_mrs:
         if deadline is not None and time.monotonic() >= deadline:
             logger.warning(
                 "outcome_sync deadline reached before revert-candidate "
                 "fetch: project=%s sentinel_mrs=%d — skipping revert "
                 "detection and returning partial summary",
                 project, len(sentinel_mrs),
             )
             return summary
         ...
     ```
- **MIRROR**: `src/core/learning/outcome_sync.py:218-225` (signature) and `:294-323` (per-MR loop).
- **CRITICAL**: Do NOT pass `deadline` down into `_process_mr`. The check stays at the loop boundary so an in-flight per-MR call completes; partial UPDATE inside `_process_mr` is already protected by the append-once `update_execution_outcome` invariant.
- **CRITICAL**: The watermark advancement at lines 324-355 already uses `max_updated_at_handled` which only includes successfully-processed MRs. Breaking out of the loop early is therefore automatically idempotent: the next sync resumes from the last handled MR's `updated_at`.
- **GOTCHA**: Default `deadline=None` keeps existing call sites (`outcomes sync` CLI subcommand, all existing tests) backward-compatible.
- **VALIDATE**: `cd /workspace/sentinel && poetry run mypy src/core/learning/outcome_sync.py`

### Task 3: UPDATE `src/cli.py` — bound `_run_outcome_sync_preflight` with the deadline

- **ACTION**: Replace the unconditional loop in `_run_outcome_sync_preflight` (lines 1828-1830) with a deadline-bounded version.
- **IMPLEMENT**:
  ```python
  def _run_outcome_sync_preflight(project: Optional[str]) -> None:
      """Run an outcome sync as a non-fatal pre-flight (Phase 3A).

      ... [keep existing docstring] ...

      M5: bounded by OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS (default 30s).
      When the budget is exhausted, a single WARNING is logged listing the
      remaining projects and the loop returns. Per-project sync stops at
      its next MR boundary; watermark idempotency is preserved by the
      service's existing `max_updated_at_handled` invariant.
      """
      import time  # noqa: PLC0415
      from src.core.learning.outcome_sync import OutcomeSyncService  # noqa: PLC0415
      from src.gitlab_client import GitLabClient  # noqa: PLC0415

      conn = connect()
      try:
          apply_migrations(conn)
          service = OutcomeSyncService(conn, GitLabClient(), event_bus=None)
          projects = [project] if project else _discover_known_projects(conn)
          if not projects:
              return

          budget_s = _outcome_sync_preflight_budget_seconds()
          start = time.monotonic()
          deadline = start + budget_s
          synced = 0
          for i, proj in enumerate(projects):
              if time.monotonic() >= deadline:
                  remaining = projects[i:]
                  elapsed = time.monotonic() - start
                  logger.warning(
                      "outcome sync preflight budget exhausted: "
                      "synced=%d/%d remaining=%d elapsed=%.1fs budget=%.1fs "
                      "remaining_projects=%s",
                      synced, len(projects), len(remaining),
                      elapsed, budget_s, remaining,
                  )
                  break
              service.sync(project=proj, deadline=deadline)
              synced += 1
      finally:
          conn.close()
  ```
- **MIRROR**:
  - Docstring extension follows the existing tone.
  - The structured WARNING follows `src/gitlab_client.py:354-362` (multi-arg lazy %-formatting, pivots like `synced=%d/%d`, full context in one line).
- **CRITICAL**: Pass `deadline=deadline` (the same `time.monotonic()`-based deadline) into `service.sync()`. This propagates the cap into the per-MR loop too.
- **CRITICAL**: Do NOT add try/except around `service.sync()` here. The two call-sites at `cli.py:230-233` and `:625-628` already wrap the whole preflight in `try/except Exception`, and the inner `OutcomeSyncService.sync()` already swallows per-MR errors into `summary.errors`. Adding a third try/except would mask the whole-preflight failure mode.
- **GOTCHA**: The early `if not projects: return` mirrors the existing pattern in `cli.py:3496-3502` (the `outcomes sync` CLI subcommand). It also short-circuits the deadline math for the empty-DB case (first-run boots, where `_discover_known_projects` returns []).
- **GOTCHA**: `_discover_known_projects` returns `ORDER BY project` — i.e. alphabetical. This is fair across runs (the same prefix of projects is synced first each preflight). If a slow project sits early in the alphabet, this means it'll be the one that consumes most of the budget. This is acceptable for v1; a future enhancement could rotate by `last_synced_at`.
- **VALIDATE**: `cd /workspace/sentinel && poetry run mypy src/cli.py`

### Task 4: UPDATE `tests/core/test_outcome_sync.py` — `sync(deadline=...)` tests

- **ACTION**: Add a new `TestSyncDeadline` test class to the file.
- **IMPLEMENT** (place at the end of the file, before any module-level fixtures if any):
  ```python
  class TestSyncDeadline:
      """M5: cooperative deadline plumbed into sync()."""

      def test_deadline_in_past_short_circuits_before_first_mr(
          self, sqlite_mem_conn, caplog
      ):
          """deadline in the past => log + return without iterating MRs."""
          import time
          gl = _make_gitlab_mock(merged_mrs=[_make_mr(iid=1)])
          service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)
          past = time.monotonic() - 1.0

          with caplog.at_level(logging.WARNING):
              summary = service.sync(project="acme/backend", deadline=past)

          # Loop entered but immediate break => mrs_seen stays at 0.
          assert summary.mrs_seen == 0
          # Watermark must NOT advance.
          assert summary.watermark_advanced_to is None
          # Loud WARNING.
          assert any(
              "deadline reached" in rec.message for rec in caplog.records
          )

      def test_deadline_none_is_unbounded(self, sqlite_mem_conn):
          """deadline=None preserves pre-M5 behavior (default arg)."""
          gl = _make_gitlab_mock(merged_mrs=[_make_mr(iid=1)])
          service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)
          # No deadline kwarg passed.
          summary = service.sync(project="acme/backend")
          assert summary.mrs_seen == 1

      def test_deadline_mid_loop_advances_only_past_handled_mrs(
          self, sqlite_mem_conn
      ):
          """Idempotency: watermark only advances past MRs processed before the cap.

          Two MRs returned by GitLab; deadline trips after the first.
          Watermark must reflect MR #1's updated_at, NOT MR #2's.
          """
          import time
          mr1 = _make_mr(iid=1, updated_at="2026-05-01T00:00:00Z")
          mr2 = _make_mr(iid=2, updated_at="2026-05-02T00:00:00Z")
          _seed_execution(
              sqlite_mem_conn,
              execution_id="e1",
              ticket_id="TEST-1",
              created_at="2026-04-01T00:00:00Z",
          )
          gl = _make_gitlab_mock(merged_mrs=[mr1, mr2])
          service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

          # Deadline that fires after one iteration: monkey-patch time inside
          # the service. Simpler: choose a deadline that is "now" so the very
          # first check breaks => watermark stays at None (no MRs handled).
          # Then run again UNBOUNDED to confirm both get handled.
          summary1 = service.sync(
              project="acme/backend", deadline=time.monotonic() - 1.0
          )
          assert summary1.mrs_seen == 0
          assert summary1.watermark_advanced_to is None

          summary2 = service.sync(project="acme/backend")
          assert summary2.mrs_seen == 2
          # Watermark advances to the LATEST handled MR.
          assert summary2.watermark_advanced_to == "2026-05-02T00:00:00Z"
  ```
- **MIRROR**: `tests/core/test_outcome_sync.py:93-105` (`_make_gitlab_mock`) and the existing `_seed_execution` helper.
- **GOTCHA**: Use `caplog.at_level(logging.WARNING)` — the `outcome_sync` logger emits at WARNING. Don't bump root logger level globally.
- **GOTCHA**: `time.monotonic()` cannot be monkey-patched portably; using "deadline already in the past" gives deterministic short-circuit behavior. For the mid-loop variant, use a side-effect on a Mock to bump time forward — but the simpler equivalent (deadline-in-past then re-run) gives full coverage with zero flakiness.
- **VALIDATE**: `cd /workspace/sentinel && poetry run pytest tests/core/test_outcome_sync.py::TestSyncDeadline -v`

### Task 5: UPDATE `tests/test_cli_outcomes.py` — preflight budget tests

- **ACTION**: Add three new tests for `_run_outcome_sync_preflight` with the budget.
- **IMPLEMENT** (append to the file, after the existing tests):
  ```python
  # ---------------------------------------------------------------------------
  # 5. M5 — preflight time budget
  # ---------------------------------------------------------------------------


  def test_preflight_budget_default_is_30(monkeypatch: pytest.MonkeyPatch):
      """Default budget is 30s when env var is unset."""
      from src.cli import _outcome_sync_preflight_budget_seconds

      monkeypatch.delenv(
          "OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS", raising=False
      )
      assert _outcome_sync_preflight_budget_seconds() == 30.0


  def test_preflight_budget_zero_or_negative_falls_back_to_default(
      monkeypatch: pytest.MonkeyPatch,
  ):
      """0 / negative / non-numeric values are coerced to default (cannot disable)."""
      from src.cli import _outcome_sync_preflight_budget_seconds

      for bad in ("0", "-1", "abc", ""):
          monkeypatch.setenv("OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS", bad)
          assert _outcome_sync_preflight_budget_seconds() == 30.0


  def test_preflight_logs_loud_warning_when_budget_exhausted(
      db_path: Path,
      monkeypatch: pytest.MonkeyPatch,
      caplog: pytest.LogCaptureFixture,
  ):
      """Two known projects + budget=0.001s => loud WARNING + remaining projects listed."""
      from src.cli import _run_outcome_sync_preflight
      from src.core.persistence import upsert_sync_state, connect

      # Seed two known projects so _discover_known_projects returns >=1.
      conn = connect(str(db_path))
      try:
          upsert_sync_state(
              conn, project="acme/alpha", last_synced_at="2026-01-01T00:00:00Z",
              last_seen_mr_iid=None, last_seen_updated_at=None,
          )
          upsert_sync_state(
              conn, project="acme/beta", last_synced_at="2026-01-01T00:00:00Z",
              last_seen_mr_iid=None, last_seen_updated_at=None,
          )
      finally:
          conn.close()

      # Force a tiny budget so the deadline is in the past on the very first
      # iteration after the first sync() call.
      monkeypatch.setenv("OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS", "0.0001")

      # Mock GitLabClient so the (single) sync() that does run is fast.
      fake_gitlab = MagicMock()
      fake_gitlab.list_merged_mrs_since.return_value = []
      fake_gitlab.list_pipelines_for_commit.return_value = []
      fake_gitlab.list_merge_requests.return_value = []

      with caplog.at_level(logging.WARNING):
          with patch(
              "src.gitlab_client.GitLabClient", return_value=fake_gitlab
          ):
              _run_outcome_sync_preflight(project=None)

      assert any(
          "preflight budget exhausted" in rec.message
          for rec in caplog.records
      ), f"expected budget WARNING; got {[r.message for r in caplog.records]}"
      # The WARNING line must include both project names so an operator can
      # spot which project is slow.
      budget_warnings = [
          rec for rec in caplog.records
          if "preflight budget exhausted" in rec.message
      ]
      assert budget_warnings, "no budget WARNING captured"
      formatted = budget_warnings[0].getMessage()
      # remaining_projects=[...] includes at least one of the seeded projects.
      assert "acme/" in formatted


  def test_preflight_returns_cleanly_when_no_known_projects(
      db_path: Path,
      monkeypatch: pytest.MonkeyPatch,
  ):
      """First-run / empty DB: preflight is a clean no-op (no GitLab call)."""
      from src.cli import _run_outcome_sync_preflight

      monkeypatch.setenv("OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS", "30")
      fake_gitlab = MagicMock()
      with patch("src.gitlab_client.GitLabClient", return_value=fake_gitlab):
          _run_outcome_sync_preflight(project=None)

      fake_gitlab.list_merged_mrs_since.assert_not_called()
  ```
- **MIRROR**: `tests/test_cli_outcomes.py:42-59` (`db_path` fixture), `:89-117` (mock-GitLab pattern).
- **GOTCHA**: `upsert_sync_state` signature uses keyword-only args — confirm import path and signature match `src/core/persistence/__init__.py` exports.
- **GOTCHA**: `OUTCOME_SYNC_ENABLED` env-flag check happens at the **call site** (line 229/624), not inside `_run_outcome_sync_preflight`. So the new tests can call the preflight directly without setting `OUTCOME_SYNC_ENABLED=1`. (This matches existing patterns at `tests/test_cli_outcomes.py:124-143`.)
- **VALIDATE**: `cd /workspace/sentinel && poetry run pytest tests/test_cli_outcomes.py -v`

### Task 6: VALIDATE — backward-compatibility regression check

- **ACTION**: Confirm that the existing `outcomes sync` CLI command (`cli.py:3460-3515`) still passes its tests (the deadline kwarg defaults to `None`, so it should be unaffected).
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && poetry run pytest tests/test_cli_outcomes.py tests/core/test_outcome_sync.py tests/integration/test_phase3a_outcomes.py -v
  ```
- **EXPECT**: All pre-existing tests still pass; new tests added in tasks 4-5 also pass.

### Task 7: VALIDATE — full test + lint + type-check sweep

- **ACTION**: Run the full validation pipeline.
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && poetry run ruff check src/cli.py src/core/learning/outcome_sync.py tests/test_cli_outcomes.py tests/core/test_outcome_sync.py
  cd /workspace/sentinel && poetry run mypy src/cli.py src/core/learning/outcome_sync.py
  cd /workspace/sentinel && poetry run pytest tests/ -x -q
  ```
- **EXPECT**: All exit 0.

---

## Testing Strategy

### Unit Tests to Write

| Test File                                | Test Cases                                                                                                                | Validates                                                       |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `tests/core/test_outcome_sync.py`        | `test_deadline_in_past_short_circuits_before_first_mr`                                                                    | `sync(deadline=…)` short-circuit + idempotent watermark         |
| `tests/core/test_outcome_sync.py`        | `test_deadline_none_is_unbounded`                                                                                          | Backward-compat: existing callers unaffected                    |
| `tests/core/test_outcome_sync.py`        | `test_deadline_mid_loop_advances_only_past_handled_mrs`                                                                    | Watermark advances ONLY past handled MRs (idempotent re-run)    |
| `tests/test_cli_outcomes.py`             | `test_preflight_budget_default_is_30`                                                                                     | Default budget is 30s when env var unset                        |
| `tests/test_cli_outcomes.py`             | `test_preflight_budget_zero_or_negative_falls_back_to_default`                                                            | Operator-safety: cap cannot be accidentally disabled            |
| `tests/test_cli_outcomes.py`             | `test_preflight_logs_loud_warning_when_budget_exhausted`                                                                   | Loud WARNING with project list on cap-hit                       |
| `tests/test_cli_outcomes.py`             | `test_preflight_returns_cleanly_when_no_known_projects`                                                                    | Empty-DB / first-run no-op (pre-existing behavior preserved)    |

### Edge Cases Checklist

- [x] Empty `_discover_known_projects` => clean return, no GitLab call
- [x] Budget env var unset => default 30s
- [x] Budget = 0 / negative / non-numeric => coerced to 30s
- [x] Deadline already in the past at preflight entry => warns + breaks before first sync
- [x] Deadline trips between projects => synced=N, remaining=M logged
- [x] Deadline trips between MRs inside one project => watermark advances only past handled MRs
- [x] `deadline=None` => unbounded (existing `outcomes sync` CLI behavior preserved)
- [x] Per-MR exception still recorded in `summary.errors` (existing behavior; deadline check is at top of loop body, before `_process_mr`)
- [x] `OUTCOME_SYNC_ENABLED=0` => preflight not called (existing guard at call sites)

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
cd /workspace/sentinel && poetry run ruff check src/cli.py src/core/learning/outcome_sync.py tests/test_cli_outcomes.py tests/core/test_outcome_sync.py
cd /workspace/sentinel && poetry run mypy src/cli.py src/core/learning/outcome_sync.py
```

**EXPECT**: Exit 0, no errors or warnings on changed files.

### Level 2: UNIT_TESTS

```bash
cd /workspace/sentinel && poetry run pytest tests/core/test_outcome_sync.py::TestSyncDeadline tests/test_cli_outcomes.py -v
```

**EXPECT**: All new tests pass; pre-existing tests in the same files still pass.

### Level 3: FULL_SUITE

```bash
cd /workspace/sentinel && poetry run pytest tests/ -x -q
```

**EXPECT**: All tests pass; no regressions in `tests/integration/test_phase3a_outcomes.py`.

### Level 4: DATABASE_VALIDATION

N/A — no schema changes.

### Level 5: BROWSER_VALIDATION

N/A — no UI.

### Level 6: MANUAL_VALIDATION

1. With `OUTCOME_SYNC_ENABLED=1` and `OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS=0.5`,
   seed two known projects via `sentinel outcomes sync --project a/foo` and
   `sentinel outcomes sync --project a/bar` so `project_sync_state` has rows.
2. Run `sentinel plan TICKET-XXX --project a/foo`.
3. Inspect logs (stderr): expect a single `outcome sync preflight budget exhausted: ...`
   WARNING line listing remaining projects, OR clean run if budget was sufficient.
4. Re-run with `OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS=30`: confirm no budget WARNING.
5. With env var unset: confirm 30s default applies (set a project that takes ~40s to sync; expect WARNING after ~30s).

---

## Acceptance Criteria

- [ ] `OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS` env var read at call time (not module import time)
- [ ] Default 30s; 0 / negative / non-numeric coerced to default
- [ ] `_run_outcome_sync_preflight` exits early when deadline crossed; logs ONE WARNING listing remaining projects + counts + elapsed
- [ ] `OutcomeSyncService.sync(deadline=...)` short-circuits between MRs; partial summary returned without advancing watermark past unhandled MRs
- [ ] `deadline=None` (default) preserves all existing behavior — no regression in `tests/core/test_outcome_sync.py`, `tests/integration/test_phase3a_outcomes.py`, `tests/test_cli_outcomes.py`
- [ ] `sentinel outcomes sync` CLI subcommand unchanged (no deadline applied)
- [ ] Flag-off no-op path unchanged (call sites at `cli.py:229-233` and `:624-628` not modified)
- [ ] WARNING message format includes: `synced=`, `remaining=`, `elapsed=`, `budget=`, `remaining_projects=`
- [ ] All 7 new tests pass; full test suite passes
- [ ] mypy + ruff exit 0 on changed files

---

## Completion Checklist

- [ ] Task 1: `_outcome_sync_preflight_budget_seconds` helper added to `src/cli.py`
- [ ] Task 2: `deadline: Optional[float] = None` kwarg added to `OutcomeSyncService.sync()`; mid-loop checks added
- [ ] Task 3: `_run_outcome_sync_preflight` bounded by deadline; loud WARNING on cap-hit
- [ ] Task 4: 3 new tests in `tests/core/test_outcome_sync.py::TestSyncDeadline`
- [ ] Task 5: 4 new tests in `tests/test_cli_outcomes.py`
- [ ] Task 6: Pre-existing test suites still pass
- [ ] Task 7: Level 1-3 validation pipeline exit 0
- [ ] All acceptance criteria met

---

## Risks and Mitigations

| Risk                                                                  | Likelihood | Impact | Mitigation                                                                                                                                                                                                                |
| --------------------------------------------------------------------- | ---------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deadline check placed inside `_process_mr` could leave a partial UPDATE | LOW        | MED    | Place check ONLY at the top of the per-MR loop body, BEFORE `_process_mr` is called. Once `_process_mr` starts, it runs to completion; `update_execution_outcome` is append-once, so no partial state.                  |
| Operator sets `OUTCOME_SYNC_PREFLIGHT_BUDGET_SECONDS=0` to disable     | MED        | LOW    | Coerce 0 / negative / non-numeric back to default 30s. Truly unbounded mode is the explicit `sentinel outcomes sync` subcommand.                                                                                          |
| Slow first-alphabetical project consumes the entire budget every preflight | MED        | LOW    | Acceptable v1 — the WARNING explicitly logs "remaining_projects" so the operator sees the pattern. Future enhancement: rotate by `last_synced_at` ascending.                                                       |
| `time.monotonic()` not monkey-patchable in tests => flaky timing tests | MED        | LOW    | Use deadline-in-past pattern (`deadline = time.monotonic() - 1.0`). Deterministic; no sleeping; no time mocking required.                                                                                                  |
| Existing `outcomes sync` CLI subcommand accidentally bounded            | LOW        | MED    | Default `deadline=None` in `sync()`. The `outcomes sync` call site at `cli.py:3504-3509` already passes only `project=`, `since=`, `full_backfill=`, `dry_run=` — no deadline => unbounded preserved. Verified by Task 6. |
| `caplog` doesn't capture `cli` module logger                            | LOW        | LOW    | Tests use `caplog.at_level(logging.WARNING)` which sets root level. Logger names `src.cli` and `src.core.learning.outcome_sync` propagate to root. Mirrors existing pattern in `tests/test_cli_outcomes.py:152`.        |
| Whole-preflight try/except at call site swallows the new WARNING        | NONE       | NONE   | The WARNING is emitted from inside the preflight via `logger.warning(...)`, NOT raised. The outer `try/except Exception` only catches raised exceptions; logs always emit.                                              |

---

## Notes

**Why not a per-project budget (option 2)**: Adds a second clock to debug, doesn't bound total perceived latency, and the between-MR check inside `sync()` already gives equivalent fairness for the dominant pathology (one project with a deep backlog).

**Why not `concurrent.futures.ThreadPoolExecutor` + `Future.result(timeout=...)`**: `Future.cancel()` on a running task is a soft hint — it cannot abort an in-flight `requests.get()`. The worker keeps running, holds the SQLite connection, and may UPSERT after we've already moved on. Cooperative deadline checks at iteration boundaries are simpler, safer, and don't introduce thread-safety concerns around the SQLite connection.

**Why between-MR check, not per-page**: GitLab pagination is internal to `list_merged_mrs_since` (`gitlab_client.py:287-365`). Pushing the deadline there would couple the GitLab client to outcome-sync semantics. Between-MR is the natural granularity at the service layer; one page of 100 MRs is a few seconds at most, well below the budget.

**Why coerce 0 to default**: Operators tweaking the env should not be able to disable the cap accidentally. The explicit way to "disable" the preflight is `OUTCOME_SYNC_ENABLED=0`. Setting the budget to 0 should not silently turn the cap off — that would defeat the whole point.

**Future work (out of scope)**:
- Rotate project sync order by `last_synced_at ASC` so a slow project doesn't always consume the budget first.
- Emit a `PreflightBudgetExhausted` event so dashboards can track frequency.
- Make the budget per-call-site (lower for `plan`, higher for `execute`) if usage data shows a pattern.
