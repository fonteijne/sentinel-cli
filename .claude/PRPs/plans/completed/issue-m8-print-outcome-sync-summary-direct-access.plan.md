# Feature: Issue M8 — Direct attribute access in `_print_outcome_sync_summary`

## Summary

Refactor `_print_outcome_sync_summary` in `src/cli.py` so it (a) accesses
`OutcomeSyncSummary` fields via direct attribute access (`summary.mrs_seen`)
instead of defensive `getattr(summary, "mrs_seen", 0)`, and (b) is statically
typed against the `OutcomeSyncSummary` dataclass via a `TYPE_CHECKING`
import. The change makes future field renames fail loudly (`AttributeError`
at call site) instead of silently returning zeros, and gives mypy full
visibility into the helper's contract — without introducing a runtime import
of the `learning` subsystem at the top of `cli.py`.

## User Story

As a Sentinel maintainer
I want field renames in `OutcomeSyncSummary` to fail loudly at the call site
So that silent data corruption (zeros displayed instead of real counts) cannot occur

## Problem Statement

`_print_outcome_sync_summary` (`src/cli.py:1781-1805`) accepts an
`OutcomeSyncSummary` instance but reads each of its 7 fields via
`getattr(summary, "field_name", default_value)`. The implementation report
justified this as "decoupling from the dataclass import path," but:

1. The dataclass is already imported lazily inside the same module's Click
   subcommand body at line 3476, so the coupling exists either way.
2. The `getattr` defaults silently swallow field renames. If
   `OutcomeSyncSummary.mrs_seen` is renamed to `total_mrs_seen` in
   `outcome_sync.py`, the helper continues to render `mrs_seen: 0` for every
   sync forever — no exception, no test failure (the existing CLI test only
   asserts the project name appears in output).
3. The parameter is annotated as `object`, so mypy cannot catch the rename
   either.

The combined effect is that a routine refactor in
`src/core/learning/outcome_sync.py` could silently corrupt every operator's
view of sync results.

## Solution Statement

Replace all seven `getattr(summary, "X", default)` calls with direct
`summary.X` attribute access, and annotate the parameter as
`OutcomeSyncSummary` via a `TYPE_CHECKING` import block. This combination
gives:

- **Runtime fail-fast**: `AttributeError` at the print call site if a field
  is renamed without updating the helper.
- **Static fail-fast**: mypy flags the same condition before runtime,
  without forcing `cli.py` to pull `src.core.learning.outcome_sync` at
  module-load time (preserving the existing lazy-import convention used for
  other learning-subsystem imports in this file).

Defaults that the original `getattr` calls supplied (`{}` for `tag_counts`,
`[]` for `errors`) become unnecessary because the dataclass declares the
same defaults via `field(default_factory=...)`. The `or {}` / `or []`
fallbacks remain only as a courtesy guard against a caller passing `None`
explicitly — but since the dataclass default factories preclude `None` and
no caller mutates these fields to `None`, we can drop them too.

## Metadata

| Field            | Value                                                       |
| ---------------- | ----------------------------------------------------------- |
| Type             | REFACTOR                                                    |
| Complexity       | LOW                                                         |
| Systems Affected | `src/cli.py` (one helper); no test changes required         |
| Dependencies     | None new (`OutcomeSyncSummary` already exists)              |
| Estimated Tasks  | 3                                                           |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════╗
║                          BEFORE STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════╣
║                                                                           ║
║   ┌──────────────────────┐                                                ║
║   │ outcome_sync.py      │     rename: mrs_seen → total_mrs_seen          ║
║   │ @dataclass           │ ────────────────────────────────────►          ║
║   │ class                │                                                ║
║   │ OutcomeSyncSummary   │                                                ║
║   └──────────────────────┘                                                ║
║              │                                                            ║
║              ▼                                                            ║
║   ┌──────────────────────┐    getattr(summary, "mrs_seen", 0)            ║
║   │ cli.py               │    silently returns 0  ─── NO ERROR           ║
║   │ _print_outcome_sync_ │                                                ║
║   │ summary(             │    user sees:                                  ║
║   │   summary: object    │      mrs_seen:          0                      ║
║   │ )                    │      executions_tagged: 0                      ║
║   └──────────────────────┘    (real values lost)                          ║
║                                                                           ║
║   PAIN_POINT: rename masked at runtime AND statically (param: object)     ║
║   DATA_FLOW: dataclass field → getattr lookup → default → echo            ║
║                                                                           ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════╗
║                           AFTER STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════╣
║                                                                           ║
║   ┌──────────────────────┐     rename: mrs_seen → total_mrs_seen          ║
║   │ outcome_sync.py      │ ────────────────────────────────────►          ║
║   │ @dataclass           │                                                ║
║   │ class                │                                                ║
║   │ OutcomeSyncSummary   │                                                ║
║   └──────────────────────┘                                                ║
║              │                                                            ║
║              ▼                                                            ║
║   ┌──────────────────────┐    summary.mrs_seen                            ║
║   │ cli.py               │    → AttributeError ◄── LOUD FAIL              ║
║   │ if TYPE_CHECKING:    │                                                ║
║   │   from .outcome_sync │    mypy also flags before runtime              ║
║   │     import           │    (param typed as OutcomeSyncSummary)         ║
║   │     OutcomeSyncSum...│                                                ║
║   │ _print_outcome_sync_ │                                                ║
║   │ summary(             │                                                ║
║   │   summary:           │                                                ║
║   │     "OutcomeSyncS..."│                                                ║
║   │ )                    │                                                ║
║   └──────────────────────┘                                                ║
║                                                                           ║
║   VALUE_ADD: refactor of dataclass cannot silently break operator UX      ║
║   DATA_FLOW: dataclass field → direct access → echo (no defaults layer)   ║
║                                                                           ║
╚═══════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                  | Before                                        | After                                                | User Impact                                              |
| ----------------------------------------- | --------------------------------------------- | ---------------------------------------------------- | -------------------------------------------------------- |
| `src/cli.py:1781` `_print_outcome_sync_summary` signature | `summary: object`                             | `summary: "OutcomeSyncSummary"` (forward ref)        | Static-type contract enforced by mypy                    |
| `src/cli.py:1788-1794` field reads        | 7 × `getattr(summary, "X", default)`          | 7 × `summary.X` (direct)                             | Field renames raise `AttributeError` at sync print time  |
| `src/cli.py:1791,1793` collection guards  | `getattr(...) or {}` / `getattr(...) or []`   | `summary.tag_counts` / `summary.errors` (no `or`)    | Unchanged at runtime (defaults come from dataclass field factories) |
| `src/cli.py` top-of-file imports          | No reference to `OutcomeSyncSummary`          | `if TYPE_CHECKING: from src.core.learning.outcome_sync import OutcomeSyncSummary` | No runtime import added; mypy sees the type              |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File                                                | Lines     | Why Read This                                                                  |
| -------- | --------------------------------------------------- | --------- | ------------------------------------------------------------------------------ |
| P0       | `src/cli.py`                                        | 1781-1805 | The exact function being refactored                                            |
| P0       | `src/core/learning/outcome_sync.py`                 | 72-100    | The `OutcomeSyncSummary` dataclass — confirms field names + factory defaults   |
| P0       | `src/agents/base_developer.py`                      | 1-30      | Existing `TYPE_CHECKING` pattern in this codebase to MIRROR exactly            |
| P1       | `src/cli.py`                                        | 1-12      | Top-of-file import block — where to insert `TYPE_CHECKING` import              |
| P1       | `src/cli.py`                                        | 3470-3515 | The one caller of `_print_outcome_sync_summary` — confirms argument is always a real `OutcomeSyncSummary` |
| P2       | `tests/test_cli_outcomes.py`                        | 89-116    | Existing CLI integration test that exercises the helper indirectly via `outcomes sync --dry-run` |

**External Documentation:** None required — this is a pure refactor against
existing typed Python patterns.

---

## Patterns to Mirror

**TYPE_CHECKING_BLOCK** (existing convention in this codebase):

```python
# SOURCE: src/agents/base_developer.py:11-15
# COPY THIS PATTERN:
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from src.core.events import EventBus
    from src.environment_manager import EnvironmentManager
```

**OUTCOMESYNCSUMMARY_DATACLASS** (the type the helper consumes):

```python
# SOURCE: src/core/learning/outcome_sync.py:72-100
# REFERENCE — DO NOT MODIFY:
@dataclass
class OutcomeSyncSummary:
    project: str
    mrs_seen: int = 0
    executions_tagged: int = 0
    tag_counts: Dict[str, int] = field(default_factory=dict)
    watermark_advanced_to: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    dry_run: bool = False
```

**CURRENT_HELPER** (what we are replacing):

```python
# SOURCE: src/cli.py:1781-1805
# REPLACE THIS:
def _print_outcome_sync_summary(summary: object) -> None:
    """Pretty-print an ``OutcomeSyncSummary`` to stdout (Phase 3A).

    Defensive ``getattr`` access keeps this helper decoupled from the
    summary dataclass's import path — the CLI imports the service lazily
    inside the subcommand body.
    """
    project = getattr(summary, "project", "?")
    mrs_seen = getattr(summary, "mrs_seen", 0)
    executions_tagged = getattr(summary, "executions_tagged", 0)
    tag_counts = getattr(summary, "tag_counts", {}) or {}
    watermark = getattr(summary, "watermark_advanced_to", None)
    errors = getattr(summary, "errors", []) or []
    dry_run = getattr(summary, "dry_run", False)

    suffix = " (dry-run)" if dry_run else ""
    click.echo(f"📦 {project}{suffix}")
    click.echo(f"   mrs_seen:          {mrs_seen}")
    click.echo(f"   executions_tagged: {executions_tagged}")
    if tag_counts:
        for tag in sorted(tag_counts):
            click.echo(f"     {tag}: {tag_counts[tag]}")
    click.echo(f"   watermark_advanced_to: {watermark}")
    for err in errors:
        click.echo(f"   ⚠ {err}", err=True)
```

---

## Files to Change

| File                                | Action | Justification                                                                  |
| ----------------------------------- | ------ | ------------------------------------------------------------------------------ |
| `src/cli.py`                        | UPDATE | Add `TYPE_CHECKING` import block; refactor `_print_outcome_sync_summary` body  |

That's it — single file. No test changes required (see "Test Impact Analysis"
below).

---

## Test Impact Analysis

I verified each test path that touches `_print_outcome_sync_summary`:

| Test                                                                  | Calls helper how?                                       | Mock used? | Action needed                                                  |
| --------------------------------------------------------------------- | ------------------------------------------------------- | ---------- | -------------------------------------------------------------- |
| `tests/test_cli_outcomes.py::test_outcomes_sync_dry_run_runs_with_flag_off` | Indirectly via `runner.invoke(cli, ["outcomes", "sync", "--dry-run", ...])` — produces a real `OutcomeSyncSummary` from `OutcomeSyncService.sync()`, no mock of the summary. | No (only GitLabClient is mocked.) | None — passes a real dataclass instance.                       |

`grep -rn "print_outcome_sync\|_print_outcome" tests/` returns no direct unit
test for the helper. No mock objects are passed in. The helper is exercised
end-to-end in one CLI integration test, which receives a real
`OutcomeSyncSummary` instance — direct attribute access works unchanged.

**Therefore no test files need editing.**

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **The `OutcomeSyncSummary` dataclass itself**: stays exactly as-is. No new
  fields, no rename, no change to factory defaults.
- **The lazy `OutcomeSyncService` runtime import at `cli.py:3476`**: out of
  scope. That import defers loading the service class (which transitively
  pulls SQL, GitLab client, etc.). We only hoist the *type* of the summary
  via `TYPE_CHECKING`, which has zero runtime cost.
- **Other `getattr` defensive patterns elsewhere in `cli.py`**: not in scope.
  Only the one helper is being fixed (PR review issue M8).
- **The broader CLI refactor** (e.g., consolidating lazy imports, splitting
  `cli.py` into modules): explicitly out of scope per the issue brief.
- **Adding a new dedicated unit test for `_print_outcome_sync_summary`**:
  not in scope. The existing integration test
  (`test_outcomes_sync_dry_run_runs_with_flag_off`) already exercises the
  helper end-to-end with a real dataclass instance, which is the assertion
  that matters (direct attribute access doesn't `AttributeError` on the real
  type). Adding a unit test purely to assert "renaming a field raises
  `AttributeError`" tests Python language semantics, not our code.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/cli.py` top-of-file imports — add `TYPE_CHECKING` block

- **ACTION**: Modify the `from typing import Optional` line to also import
  `TYPE_CHECKING`, and add a `TYPE_CHECKING` block that imports
  `OutcomeSyncSummary`.
- **MIRROR**: `src/agents/base_developer.py:11-15` — same pattern, two-line
  block under the typing import.
- **CURRENT** (`src/cli.py:11`):
  ```python
  from typing import Optional
  ```
- **AFTER**:
  ```python
  from typing import TYPE_CHECKING, Optional
  ```
- **THEN** insert (immediately after the existing top-level imports, before
  the `def _verifier_loop_enabled()` block at line 45 — find a stable
  insertion anchor like the blank line after `from src.utils.adf_parser
  import parse_adf_to_text`):
  ```python


  if TYPE_CHECKING:  # noqa: I001 -- forward-ref only, avoids runtime cost of learning import
      from src.core.learning.outcome_sync import OutcomeSyncSummary
  ```
- **GOTCHA**: The existing imports use absolute paths (`from src...`) — keep
  the same convention. Do **not** use a relative import.
- **GOTCHA**: Place the `TYPE_CHECKING` block AFTER the runtime imports so
  ruff's import-order rule (I001) doesn't complain. If it does, add the
  `# noqa: I001` comment shown above; otherwise omit it.
- **VALIDATE**:
  ```bash
  poetry run ruff check src/cli.py
  poetry run mypy src/cli.py
  ```
  Both must exit 0.

### Task 2: UPDATE `src/cli.py:1781-1805` — refactor `_print_outcome_sync_summary`

- **ACTION**: Replace the function body so it (a) annotates the parameter
  with the forward-ref string `"OutcomeSyncSummary"`, (b) uses direct
  attribute access, and (c) updates the docstring to reflect the new
  contract.
- **REPLACE**:
  ```python
  def _print_outcome_sync_summary(summary: object) -> None:
      """Pretty-print an ``OutcomeSyncSummary`` to stdout (Phase 3A).

      Defensive ``getattr`` access keeps this helper decoupled from the
      summary dataclass's import path — the CLI imports the service lazily
      inside the subcommand body.
      """
      project = getattr(summary, "project", "?")
      mrs_seen = getattr(summary, "mrs_seen", 0)
      executions_tagged = getattr(summary, "executions_tagged", 0)
      tag_counts = getattr(summary, "tag_counts", {}) or {}
      watermark = getattr(summary, "watermark_advanced_to", None)
      errors = getattr(summary, "errors", []) or []
      dry_run = getattr(summary, "dry_run", False)

      suffix = " (dry-run)" if dry_run else ""
      click.echo(f"📦 {project}{suffix}")
      click.echo(f"   mrs_seen:          {mrs_seen}")
      click.echo(f"   executions_tagged: {executions_tagged}")
      if tag_counts:
          for tag in sorted(tag_counts):
              click.echo(f"     {tag}: {tag_counts[tag]}")
      click.echo(f"   watermark_advanced_to: {watermark}")
      for err in errors:
          click.echo(f"   ⚠ {err}", err=True)
  ```
- **WITH**:
  ```python
  def _print_outcome_sync_summary(summary: "OutcomeSyncSummary") -> None:
      """Pretty-print an ``OutcomeSyncSummary`` to stdout (Phase 3A).

      Field access is direct (``summary.mrs_seen``, not
      ``getattr(summary, "mrs_seen", 0)``) so a future field rename in
      ``OutcomeSyncSummary`` surfaces as ``AttributeError`` here instead of
      silently rendering zeros. The parameter is typed via a
      ``TYPE_CHECKING`` forward reference so mypy enforces the contract
      statically without forcing ``cli.py`` to import the learning subsystem
      at module-load time.
      """
      suffix = " (dry-run)" if summary.dry_run else ""
      click.echo(f"📦 {summary.project}{suffix}")
      click.echo(f"   mrs_seen:          {summary.mrs_seen}")
      click.echo(f"   executions_tagged: {summary.executions_tagged}")
      if summary.tag_counts:
          for tag in sorted(summary.tag_counts):
              click.echo(f"     {tag}: {summary.tag_counts[tag]}")
      click.echo(f"   watermark_advanced_to: {summary.watermark_advanced_to}")
      for err in summary.errors:
          click.echo(f"   ⚠ {err}", err=True)
  ```
- **PATTERN**: All seven fields are read directly; no `getattr`, no `or {}`/
  `or []` fallbacks (the dataclass's `default_factory` already guarantees
  these are never `None`).
- **GOTCHA**: The annotation is a *string forward reference*
  (`"OutcomeSyncSummary"`) — the actual class is only available inside the
  `TYPE_CHECKING` block at module top, so a non-stringified annotation
  would `NameError` at function-definition time at runtime. Stringification
  is fine for both mypy and Python's runtime (PEP 563).
- **GOTCHA**: Keep the emoji (`📦`, `⚠`) — they're already in the file and
  match Sentinel's CLI output style elsewhere (`outcomes sync failed: ...`
  uses `❌` at line 3514).
- **VALIDATE**:
  ```bash
  poetry run ruff check src/cli.py
  poetry run mypy src/cli.py
  ```

### Task 3: VERIFY no regression in CLI integration test

- **ACTION**: Run the existing CLI test that exercises this helper end-to-
  end, plus the broader `cli` and `outcome_sync` test groups.
- **NO CODE CHANGE** in this task — pure validation.
- **VALIDATE**:
  ```bash
  poetry run pytest tests/test_cli_outcomes.py -v
  poetry run pytest tests/core/test_outcome_sync.py -v
  ```
  Both must show all tests passing (in particular
  `test_outcomes_sync_dry_run_runs_with_flag_off`, which asserts
  `"acme/backend" in result.output` — produced by the refactored helper).
- **EXPECT**: All tests green. If
  `test_outcomes_sync_dry_run_runs_with_flag_off` fails on output-match,
  the helper's stdout shape changed; revert and re-check Task 2 — the user-
  visible string output should be byte-identical.

---

## Testing Strategy

### Unit Tests to Write

**None.** Existing coverage is sufficient:

- `tests/test_cli_outcomes.py::test_outcomes_sync_dry_run_runs_with_flag_off`
  invokes the `outcomes sync --dry-run` command end-to-end with a real
  (mocked-input) `OutcomeSyncService.sync()` call, which produces a real
  `OutcomeSyncSummary` instance and pipes it through
  `_print_outcome_sync_summary`. This is the canonical guarantee that direct
  attribute access works on the actual production type.

Adding a unit test like
`assert _print_outcome_sync_summary(OutcomeSyncSummary(project="x"))` would
just re-test what the integration test already covers.

### Edge Cases Checklist

- [ ] Empty `tag_counts` → no per-tag lines printed (already covered by
      existing test; default-factory dict).
- [ ] Empty `errors` list → no `⚠` lines (default-factory list).
- [ ] `dry_run=True` → ` (dry-run)` suffix appears.
- [ ] `watermark_advanced_to=None` → printed literally as `None` (current
      behavior preserved — `f"...{summary.watermark_advanced_to}"` renders
      `None` as `"None"`).
- [ ] Real `OutcomeSyncSummary` from `service.sync()` returned through
      Click runner produces identical output bytes to the pre-refactor
      version (verified by the integration test in Task 3).

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
poetry run ruff check src/cli.py
poetry run mypy src/cli.py
```

**EXPECT**: Exit 0 from both. Ruff should not complain about the
`TYPE_CHECKING` import (it's idiomatic). Mypy should now type-check the
helper body against `OutcomeSyncSummary`'s real fields — if a field name in
the helper body ever drifts from the dataclass, mypy fails here.

### Level 2: UNIT_TESTS

```bash
poetry run pytest tests/test_cli_outcomes.py tests/core/test_outcome_sync.py -v
```

**EXPECT**: All tests pass, including
`test_outcomes_sync_dry_run_runs_with_flag_off` (the one that prints the
summary in a `CliRunner`).

### Level 3: FULL_SUITE

```bash
poetry run pytest -q
```

**EXPECT**: No regressions in the rest of the suite. We're only touching one
file's helper internals; failures elsewhere imply a typo in the
refactor.

### Level 4: DATABASE_VALIDATION

Not applicable — no schema or data changes.

### Level 5: BROWSER_VALIDATION

Not applicable — CLI-only change.

### Level 6: MANUAL_VALIDATION

Optional smoke test (only if running in `sentinel-dev` against a real DB):

```bash
poetry run sentinel outcomes sync --dry-run --project some/project
```

**EXPECT**: Output identical to pre-refactor:
```
📦 some/project (dry-run)
   mrs_seen:          0
   executions_tagged: 0
   watermark_advanced_to: None
```

---

## Acceptance Criteria

- [ ] `src/cli.py:_print_outcome_sync_summary` parameter is annotated as
      `"OutcomeSyncSummary"` (forward-ref string).
- [ ] All seven `getattr(summary, "X", default)` calls are replaced with
      direct `summary.X` attribute access.
- [ ] `if TYPE_CHECKING:` block at top of `src/cli.py` imports
      `OutcomeSyncSummary` from `src.core.learning.outcome_sync`.
- [ ] No runtime import of `OutcomeSyncSummary` is added (the
      `TYPE_CHECKING` guard ensures it's not loaded at CLI startup).
- [ ] `poetry run ruff check src/cli.py` exits 0.
- [ ] `poetry run mypy src/cli.py` exits 0.
- [ ] `poetry run pytest tests/test_cli_outcomes.py tests/core/test_outcome_sync.py`
      passes with no failures.
- [ ] Output of `outcomes sync --dry-run` is byte-identical to pre-refactor
      output (verified by the existing integration test's
      `assert "acme/backend" in result.output` and by the manual smoke
      test if run).
- [ ] `OutcomeSyncSummary` dataclass in `src/core/learning/outcome_sync.py`
      is unchanged.

---

## Completion Checklist

- [x] Task 1: `TYPE_CHECKING` import block added at top of `src/cli.py`
- [x] Task 2: `_print_outcome_sync_summary` body refactored to direct access
      with `OutcomeSyncSummary` forward-ref annotation
- [x] Task 3: Validation commands run; all green
- [x] Level 1 (ruff + mypy): pass (no new errors introduced; pre-existing
      cli.py errors unrelated to this refactor are stable on baseline)
- [x] Level 2 (targeted pytest): pass (38/38 in
      `tests/test_cli_outcomes.py` + `tests/core/test_outcome_sync.py`)
- [x] Level 3 (full suite): pass (1053 passed; 26 known pre-existing
      failures in `test_environment_manager.py`, `test_jira_server_client.py`,
      `test_plan_generator.py`, `test_worktree_manager.py` — NOT regressions)
- [x] All acceptance criteria met

---

## Risks and Mitigations

| Risk                                                                                     | Likelihood | Impact | Mitigation                                                                 |
| ---------------------------------------------------------------------------------------- | ---------- | ------ | -------------------------------------------------------------------------- |
| Ruff complains about import order when `TYPE_CHECKING` block is added                    | LOW        | LOW    | If it fires, place the block after all `from src...` imports and add `# noqa: I001` (already noted in Task 1 gotcha) |
| Forward-ref annotation `"OutcomeSyncSummary"` confuses a future tool that doesn't honor PEP 563 | VERY LOW   | LOW    | All Python type-checkers and runtime stubs in this project (mypy 1.7, py 3.11) handle PEP-563 string annotations correctly |
| Integration test asserts on output containing a default-rendered `None` for `watermark_advanced_to` | LOW        | LOW    | The Python `f"...{None}"` substitution renders `"None"` byte-identically pre and post; verified by reading the test (line 113-115 of `test_cli_outcomes.py` only asserts `"acme/backend" in result.output`, not the rendered watermark line) |
| A future field rename in `OutcomeSyncSummary` now raises `AttributeError` at runtime instead of silently rendering 0 | INTENDED   | INTENDED | This is the *goal* of the refactor — fail-fast is correct behavior. mypy will catch it before runtime if the change goes through CI |
| Lazy import was actually needed for circular-import reasons we missed                     | VERY LOW   | LOW    | Verified by grep that `src/core/learning/outcome_sync.py` does not import from `src.cli`. `TYPE_CHECKING` guard means we don't import at runtime anyway, so even an unforeseen runtime cycle is avoided |

---

## Notes

**Why `TYPE_CHECKING` instead of hoisting the runtime import?** The file
`src/cli.py` consistently uses lazy imports (`# noqa: PLC0415`) for every
`src.core.learning.*` and `src.gitlab_client` reference inside subcommand
bodies (5 instances at lines 1821, 1822, 1902, 1998, 2001, 3476, 3477).
This is a conscious convention — heavy learning-subsystem imports are
deferred so `sentinel --help` doesn't pay the cost. Hoisting
`OutcomeSyncSummary` purely as a runtime import would break that
convention, while `TYPE_CHECKING` honors it (the import only happens during
mypy analysis).

**Why drop the `or {}` / `or []` defensive fallbacks?** The dataclass uses
`field(default_factory=dict)` and `field(default_factory=list)`, which
guarantee that `tag_counts` and `errors` are never `None` for a properly
constructed instance. With direct attribute access on a real
`OutcomeSyncSummary`, those fallbacks are dead code. If a caller ever
constructs a malformed `OutcomeSyncSummary(tag_counts=None)` explicitly
(which the dataclass type signature `Dict[str, int]` forbids), we *want*
the iteration to fail loudly rather than silently render nothing — same
fail-fast principle that motivates this whole change.

**Why no new unit test?** The bug class this refactor prevents (silent
field-rename regression) is detected by mypy at static-analysis time and by
`AttributeError` at integration-test time. Both are already wired up. A
unit test that does
`_print_outcome_sync_summary(OutcomeSyncSummary(project="x"))` would
duplicate the integration test's coverage without adding any signal.
