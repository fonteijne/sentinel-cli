# Feature: Fix H3 — Branch-Name Collision in `learning propose`

## Summary

Replace the minute-precision UTC timestamp suffix in `_branch_name_for(scope)` with a second-precision suffix (`%Y%m%d-%H%M%S`) so that two `sentinel learning propose` invocations within the same minute produce distinct branch names. This eliminates the `git checkout -b "branch already exists"` failure that operators hit on a routine workflow (failed real-run leaves a branch behind, then a retry collides). Add a unit test that asserts two consecutive `_branch_name_for` calls with realistically close clocks do not collide. Pure helper change; the branch name remains a valid git ref, the prefix stays greppable, and the public surface of `propose_overlays` is unchanged.

## User Story

As a Sentinel operator running `sentinel learning propose`
I want a retry within the same minute to succeed
So that a single transient push/MR failure (or a dry-run-then-real sequence) doesn't strand me until the wall clock ticks past the minute boundary

## Problem Statement

`src/core/learning/propose_overlay.py:91-98`:

```python
def _branch_name_for(scope: str) -> str:
    """``sentinel-learning/promote-<scope>-<YYYYMMDD-HHMM>`` (UTC)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return f"sentinel-learning/promote-{scope}-{stamp}"
```

The minute-precision stamp produces colliding branch names for any two invocations within the same UTC minute. Failure modes:

1. **Failed real-run, then retry within the minute.** Per `propose_overlays` line 560-564, a failed real-run intentionally does NOT clean up the local branch (the operator may want to inspect partial state — this is the H2 contract). The next call hits `git checkout -b <same-name>` at line 431-441 and dies with `RuntimeError: git checkout -b ... failed: fatal: a branch named '...' already exists`.
2. **Two real-runs after a typo correction within the minute.** Same as (1).
3. **(Already-handled, but adjacent.)** Dry-run + real-run within the minute: dry-run reverts and `git branch -D`s its branch (lines 467-480), so this *currently* works — but with the H2 fix applying to dry-run too (some H2 variants do not delete on failure), this becomes brittle. Second-precision removes the dependency on dry-run-cleanup.

The collision is not theoretical: a routine "MR creation failed because the GitLab token expired, fix the token, re-run" workflow trips it within seconds.

## Solution Statement

Change the strftime format from `%Y%m%d-%H%M` to `%Y%m%d-%H%M%S` in `_branch_name_for`. Two-character widening; remains a valid git ref (no slashes/double-dots/control chars introduced); remains greppable at the `sentinel-learning/promote-<scope>-` prefix; remains human-readable when an operator inspects `git branch --list 'sentinel-learning/promote-*'` after a failed run.

Update the existing branch-naming regex test to expect 6-digit time, and add a new test that calls `_branch_name_for` twice in succession (with `time.sleep(1.05)` to guarantee a UTC second tick) and asserts inequality. The mocked-clock approach is rejected — see "Approach Chosen" below.

## Metadata

| Field            | Value                                                                                  |
| ---------------- | -------------------------------------------------------------------------------------- |
| Type             | BUG_FIX                                                                                |
| Complexity       | LOW                                                                                    |
| Systems Affected | `src/core/learning/propose_overlay.py`, `tests/core/test_propose_overlay.py`           |
| Dependencies     | Standard library only (`datetime` already imported); no version-sensitive 3rd-party    |
| Estimated Tasks  | 3                                                                                      |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                              BEFORE STATE                                     ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   T = 12:34:10 UTC                                                            ║
║   $ sentinel learning propose --scope drupal                                  ║
║     → branch sentinel-learning/promote-drupal-20260514-1234                   ║
║     → git push fails (token expired)                                          ║
║     → ERROR returned, branch LEFT ON DISK (H2 invariant)                      ║
║                                                                               ║
║   T = 12:34:25 UTC  (operator fixes token, re-runs 15s later)                 ║
║   $ sentinel learning propose --scope drupal                                  ║
║     → _branch_name_for("drupal") → "...drupal-20260514-1234"  ← SAME NAME    ║
║     → subprocess: git checkout -b sentinel-learning/promote-drupal-20260514-1234 ║
║     → fatal: A branch named 'sentinel-learning/promote-drupal-20260514-1234' ║
║       already exists                                                          ║
║     → RuntimeError: git checkout -b ... failed                                ║
║                                                                               ║
║   PAIN_POINT: Operator must wait for minute rollover OR manually              ║
║               `git branch -D ...` the orphan. Both are friction on a routine  ║
║               retry.                                                          ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                               AFTER STATE                                     ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   T = 12:34:10 UTC                                                            ║
║   $ sentinel learning propose --scope drupal                                  ║
║     → branch sentinel-learning/promote-drupal-20260514-123410                 ║
║     → push fails, branch LEFT ON DISK                                         ║
║                                                                               ║
║   T = 12:34:25 UTC                                                            ║
║   $ sentinel learning propose --scope drupal                                  ║
║     → branch sentinel-learning/promote-drupal-20260514-123425  ← DIFFERENT   ║
║     → git checkout -b succeeds                                                ║
║     → MR opens                                                                ║
║                                                                               ║
║   VALUE_ADD: Retries-within-the-minute just work. Operator inspecting         ║
║              `git branch` post-failure sees a chronologically sortable name   ║
║              that still grep's cleanly off the                                ║
║              `sentinel-learning/promote-` prefix.                             ║
║                                                                               ║
║   DATA_FLOW: _branch_name_for(scope)                                          ║
║                ├─ datetime.now(timezone.utc) → 2026-05-14T12:34:10+00:00      ║
║                ├─ strftime("%Y%m%d-%H%M%S")  → "20260514-123410"              ║
║                └─ f-string → "sentinel-learning/promote-drupal-20260514-123410" ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                     | Before                                | After                                   | User Impact                                            |
| -------------------------------------------- | ------------------------------------- | --------------------------------------- | ------------------------------------------------------ |
| `_branch_name_for(scope)`                    | suffix `YYYYMMDD-HHMM` (12 chars)     | suffix `YYYYMMDD-HHMMSS` (14 chars)     | Two extra chars in branch name; no behavior change for happy path |
| Same-minute retry after failed real-run      | `RuntimeError: branch already exists` | Succeeds with a fresh distinct branch   | Operator no longer has to wait or hand-delete branches |
| `git branch --list 'sentinel-learning/promote-*'` | sorts by minute                  | sorts by second                         | More precise audit trail; same prefix grep works       |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File                                                  | Lines    | Why Read This                                                                              |
| -------- | ----------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------ |
| P0       | `src/core/learning/propose_overlay.py`                | 91-98    | The exact function being modified — preserve docstring shape and UTC contract              |
| P0       | `src/core/learning/propose_overlay.py`                | 425-441  | The single caller; verifies `_branch_name_for` output is consumed unchanged via `git checkout -b` |
| P0       | `src/core/learning/propose_overlay.py`                | 560-564  | The `try/except` that *deliberately* leaves the branch on disk after failure — this is what makes H3 routine and is the H2 contract that this fix coordinates with |
| P1       | `tests/core/test_propose_overlay.py`                  | 361-382  | The existing `test_propose_branch_naming` regex assertion that must be updated             |
| P1       | `tests/core/test_propose_overlay.py`                  | 50-84    | `tmp_repo` fixture — what an integration-flavor test has to spin up                        |
| P1       | `tests/core/test_propose_overlay.py`                  | 233-238  | The `_list_branches`/dry-run-no-stale-branch assertion (still must pass after format change) |
| P2       | `.claude/PRPs/reviews/feat-sentinel-learning-system-review.md` | 100-106  | H3 finding text + adjacent H2/H4 (orthogonal, do NOT touch in this PR)                     |
| P2       | `src/worktree_manager.py`                             | 13-33    | Existing branch-naming convention (`BRANCH_PREFIX = "sentinel/feature"`, `f"{BRANCH_PREFIX}/{ticket_id}"`) — confirms the project favors human-readable, prefix-stable branch names with NO random suffixes |

**External Documentation:**

| Source                                                                                                | Section                       | Why Needed                                                                                              |
| ----------------------------------------------------------------------------------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------- |
| [Python `datetime.strftime`](https://docs.python.org/3.11/library/datetime.html#strftime-and-strptime-format-codes) | Format codes table            | Confirm `%S` is zero-padded 2-digit seconds (00-59); UTC-correct (we already use `timezone.utc`)        |
| [git check-ref-format](https://git-scm.com/docs/git-check-ref-format)                                 | "rules for ref names"         | Verify the new suffix introduces no banned chars (no `..`, no leading `/`, no `@{`, no control chars). Adding digits is trivially safe. |
| (No 3rd-party libs needed — pure stdlib change.)                                                      |                               |                                                                                                         |

---

## Patterns to Mirror

**TIMESTAMP_FORMAT (already-established UTC strftime use in this codebase):**

```python
# SOURCE: src/cli.py:436 (and :492, :551 — used 3x)
# COPY THIS PATTERN (always UTC, always strftime, always single line):
f"*Generated by Sentinel at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*"
```

The H3 fix preserves this convention exactly: `datetime.now(timezone.utc).strftime("…")` with a literal format string. We are widening the format only.

**BRANCH_NAMING_CONVENTION (existing project-wide convention):**

```python
# SOURCE: src/worktree_manager.py:13-33
# REFERENCE PATTERN — branches use a stable prefix + variable suffix:
BRANCH_PREFIX = "sentinel/feature"

def get_branch_name(ticket_id: str) -> str:
    return f"{BRANCH_PREFIX}/{ticket_id}"
```

Existing branches in the codebase favor **human-readable, prefix-stable** names — no random hex suffixes anywhere in `src/`. `_branch_name_for` already follows this convention; the H3 fix preserves it (only adding 2 digits of seconds).

**TEST_STRUCTURE (pytest module-level functions, regex assertions, tmp_repo fixture):**

```python
# SOURCE: tests/core/test_propose_overlay.py:361-382
# COPY THIS PATTERN — module-level def, regex match against branch_name:
def test_propose_branch_naming(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        propose_module, "push_overlay_branch", _no_push_overlay_branch,
    )
    results = propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        repo_project_path="sentinel-team/sentinel",
        scope="drupal",
        min_confidence=80,
    )
    assert len(results) == 1
    assert re.match(
        r"^sentinel-learning/promote-drupal-\d{8}-\d{4}$",  # <-- updated to \d{6}
        results[0].branch_name,
    ), results[0].branch_name
```

**UNIT_TEST_OF_PRIVATE_HELPER (cleaner than full propose_overlays e2e for the new uniqueness assertion):**

```python
# SOURCE: tests/core/test_propose_overlay.py:21-38 (imports)
# Pattern: import the private helper directly via the module alias:
from src.core.learning import propose_overlay as propose_module
# Then call propose_module._branch_name_for("drupal") in the test body.
```

---

## Files to Change

| File                                       | Action | Justification                                                                              |
| ------------------------------------------ | ------ | ------------------------------------------------------------------------------------------ |
| `src/core/learning/propose_overlay.py`     | UPDATE | One-line strftime format change (line 97) + docstring update on line 92                    |
| `tests/core/test_propose_overlay.py`       | UPDATE | Update existing branch-naming regex (line 380) from `\d{4}` to `\d{6}` + add a new uniqueness test |

No new files. No new dependencies. No public-API change.

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep — H2/H3/H4 are orthogonal per the brief:

- **H2 (branch state restore on failed real-run).** The H3 fix does NOT change the failure-cleanup contract at `propose_overlay.py:560-564`. The branch is still deliberately left on disk after a failure for operator inspection. H3 just makes the next retry's branch name distinct.
- **H4 (dry-run overlay edit safety).** No changes to `_apply_overlay_edit` or to dry-run's working-tree behavior. Same function module, but a different concern.
- **Random hex suffix (Option 2 from brief).** Considered and rejected — see "Approach Chosen" below.
- **Mocked-clock test using `freezegun` or `monkeypatch.setattr(datetime, ...)`.** Considered but rejected: introducing a new test dependency or a fragile module-attribute monkeypatch for a 1-line helper is over-engineering. A `time.sleep(1.05)` between two real calls (each <1ms) is a ~1.1s test cost paid once and is what the brief's constraint "two consecutive calls produce different names" naturally maps to.
- **Branch-name length validation.** Adding 2 chars (12→14) keeps us nowhere near the 250-char filesystem limit on common refs.
- **Backfilling old branch names.** Operators with stranded `*-HHMM` branches from before this fix can `git branch -D` them manually; no migration needed.
- **Changing the `propose_overlays` exception path to clean up the branch on failure.** That's H2's call.

---

## Approach Chosen

**APPROACH_CHOSEN**: **Option 1 — Add seconds (`%Y%m%d-%H%M%S`).**

**RATIONALE**:

1. **Codebase convention.** Every existing branch-naming and timestamp pattern in `src/` is human-readable and prefix-stable:
   - `src/worktree_manager.py:13-33` → `sentinel/feature/<ticket-id>` (no random suffix)
   - `src/cli.py:436,492,551` → `strftime('%Y-%m-%d %H:%M UTC')` (strftime, not random)
   - `src/core/learning/propose_overlay.py:97` → `strftime('%Y%m%d-%H%M')` (strftime)
   
   No occurrence of `secrets.token_hex` anywhere in `src/`. Introducing random suffixes for a single helper would be an inconsistent precedent.

2. **Operator ergonomics on failed runs.** The brief explicitly highlights this: when an operator inspects `git branch --list 'sentinel-learning/promote-*'` after a failed real-run, a sortable timestamp (`drupal-20260514-123410`, `drupal-20260514-123425`) tells them *which run failed when* at a glance. A hex suffix (`drupal-20260514-1234-a3f9c2`) loses that ordering and forces the operator to cross-reference logs.

3. **Collision risk is well-bounded.** Sub-second-collision needs the same operator to fire two real-runs from the same shell within ~1 second. That's not a workflow we observe; if it ever becomes one, we revisit.

4. **Greppability preserved.** Existing tests grep on `sentinel-learning/promote-drupal-` prefix (e.g. `test_dry_run_creates_no_branch_no_mr` at line 236). Adding 2 chars to the suffix does not break this.

5. **Minimal diff = minimal review surface.** One literal-string change in `propose_overlay.py` and a regex update in the test. Easy to audit, easy to revert.

**ALTERNATIVES REJECTED**:

- **Option 2 (`secrets.token_hex(3)` random suffix).** Rejected because (a) inconsistent with all other branch-naming in `src/`, (b) loses chronological sortability when operator inspects orphaned branches after failure, (c) introduces an unseeded source of nondeterminism into a test that previously asserted exact-format match. It would solve a collision risk we don't have (sub-second simultaneous runs from the same machine).

- **Hybrid (`%Y%m%d-%H%M%S-{token_hex(2)}`).** Rejected for the same reason (1) — overkill, two changes for one bug, no observed need for sub-second collision protection.

- **Mock the clock in the new test.** Rejected because a real `time.sleep(1.05)` delivers what the brief asks for ("two consecutive calls produce different names") without coupling the test to `datetime` internals. The whole-suite cost is 1.1s once, hidden by parallelism if anything.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/core/learning/propose_overlay.py:91-98` — widen strftime to second precision

- **ACTION**: Change format string from `"%Y%m%d-%H%M"` to `"%Y%m%d-%H%M%S"`. Update docstring on line 92 to match (`<YYYYMMDD-HHMM>` → `<YYYYMMDD-HHMMSS>`).
- **IMPLEMENT**:
  ```python
  def _branch_name_for(scope: str) -> str:
      """``sentinel-learning/promote-<scope>-<YYYYMMDD-HHMMSS>`` (UTC).

      UTC is non-negotiable: branch names with local-time suffixes break
      deterministic ordering when the operator's TZ changes. Second precision
      (vs minute) ensures two retries within the same minute produce distinct
      branch names — the failure path at line ~560 deliberately leaves a
      branch on disk for operator inspection, so a colliding name would block
      the next attempt.
      """
      stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
      return f"sentinel-learning/promote-{scope}-{stamp}"
  ```
- **MIRROR**: Existing strftime convention at `src/cli.py:436` (always UTC, always strftime, always literal format string).
- **IMPORTS**: None added — `datetime` and `timezone` already imported at line 36.
- **GOTCHA**: Do NOT change `datetime.now(timezone.utc)` to `datetime.utcnow()` — `utcnow()` is deprecated in 3.12+ and the codebase deliberately uses `now(timezone.utc)`. The H3 fix only widens the *format*, not the clock call.
- **GOTCHA**: Keep the f-string structure exactly. Do NOT introduce a slash, dot, or `@{` in the suffix — git would reject the ref. Adding digits is safe per `git check-ref-format` rules.
- **VALIDATE**: `poetry run mypy src/core/learning/propose_overlay.py` — must produce no new errors.

### Task 2: UPDATE `tests/core/test_propose_overlay.py:361-382` — adjust existing branch-naming regex

- **ACTION**: Update the regex in `test_propose_branch_naming` from `\d{4}$` to `\d{6}$` to match the new 6-digit time-of-day component.
- **IMPLEMENT**: Change line 380 from
  ```python
  r"^sentinel-learning/promote-drupal-\d{8}-\d{4}$",
  ```
  to
  ```python
  r"^sentinel-learning/promote-drupal-\d{8}-\d{6}$",
  ```
- **MIRROR**: The function body and fixture wiring around line 361 stay identical — only the inner regex character class changes.
- **GOTCHA**: This is the only regex in the test file that targets the time component. The `startswith("sentinel-learning/promote-drupal-")` assertions at lines 236 and 294 do NOT need updating — they grep on prefix only.
- **VALIDATE**: `poetry run pytest tests/core/test_propose_overlay.py::test_propose_branch_naming -v` — must pass.

### Task 3: ADD `tests/core/test_propose_overlay.py` — new uniqueness test for `_branch_name_for`

- **ACTION**: Append a new test function `test_branch_name_unique_across_seconds` after `test_propose_branch_naming` (i.e. immediately before `test_propose_zero_rules_no_branch_creation` at line 385). Asserts the brief's constraint that "two consecutive calls produce different names".
- **IMPLEMENT**:
  ```python
  def test_branch_name_unique_across_seconds() -> None:
      """Two `_branch_name_for` calls separated by ~1s must yield distinct
      names. Regression guard for H3: minute-precision suffixes collide on a
      same-minute retry after a failed real-run (the failure path at
      propose_overlay.py:560-564 deliberately leaves the branch on disk).
      """
      first = propose_module._branch_name_for("drupal")
      time.sleep(1.05)
      second = propose_module._branch_name_for("drupal")
      assert first != second, (
          f"branch names collided across a 1s gap: {first!r} == {second!r} "
          "(_branch_name_for stamp precision regressed)"
      )
      # Both must still match the documented shape.
      pattern = r"^sentinel-learning/promote-drupal-\d{8}-\d{6}$"
      assert re.match(pattern, first), first
      assert re.match(pattern, second), second
  ```
- **IMPORTS**: Add `import time` at the top of the test file (next to `import re`, line 23). Existing imports already cover `propose_module`, `re`.
- **MIRROR**: Test naming/structure mirrors `test_propose_branch_naming` (module-level def, type annotations on params, `re.match` for shape assertion).
- **GOTCHA**: The `1.05` (not `1.0`) is deliberate — Python's `time.sleep` may return slightly early on some platforms; `1.05` ensures the UTC second has actually rolled over. Documented in the docstring rationale ("~1s") so a future maintainer doesn't tighten it back to `1.0` and reintroduce flakes.
- **GOTCHA**: Do NOT mock `datetime.now` to skip the sleep. Mocking module-level `datetime` in `propose_overlay` is fragile (the import is `from datetime import datetime, timezone` so attribute-patching the module attribute requires patching the imported reference, not `datetime.datetime`). The 1.05s sleep is the simpler, less-coupled solution.
- **GOTCHA**: This is a *unit* test of the helper — no `tmp_repo`, no `gitlab_client`, no DB fixture. Keep it that way; bringing in fixtures would muddy the assertion.
- **VALIDATE**: `poetry run pytest tests/core/test_propose_overlay.py::test_branch_name_unique_across_seconds -v` — must pass and complete in ~1.1s.

---

## Testing Strategy

### Unit Tests to Write

| Test File                              | Test Cases                                                                            | Validates                                            |
| -------------------------------------- | ------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `tests/core/test_propose_overlay.py`   | `test_propose_branch_naming` (UPDATED regex)                                          | Branch shape matches `\d{8}-\d{6}` (was `\d{4}`)     |
| `tests/core/test_propose_overlay.py`   | `test_branch_name_unique_across_seconds` (NEW)                                        | Two consecutive calls separated by ~1s yield distinct names; both match documented shape |

### Edge Cases Checklist

- [x] Same-minute retry after failed real-run → distinct names (covered by new uniqueness test conceptually; manually verifiable as `T+0` and `T+15s` mock)
- [x] Dry-run + real-run within the same minute → still works (existing `test_dry_run_creates_no_branch_no_mr` and `test_propose_branch_naming` both still green)
- [x] Branch name remains a valid git ref → adding digits cannot violate `git check-ref-format`
- [x] Greppability of `sentinel-learning/promote-` prefix preserved → existing `startswith` assertions at test lines 236, 294 unchanged
- [x] UTC contract preserved → still `datetime.now(timezone.utc)`; no TZ regression
- [x] No new module-level state, no new global, no new dep → diff is purely internal to `_branch_name_for`
- [ ] (NOT TESTED, OUT OF SCOPE) Sub-second collision from two simultaneous CLI invocations on the same machine — covered only if H2 also lands; stays a theoretical concern

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
poetry run ruff check src/core/learning/propose_overlay.py tests/core/test_propose_overlay.py
poetry run mypy src/core/learning/propose_overlay.py
```

**EXPECT**: Exit 0, no new warnings/errors. (Pre-existing project-wide mypy noise outside these two files is not this PR's concern.)

### Level 2: UNIT_TESTS (targeted)

```bash
poetry run pytest tests/core/test_propose_overlay.py -v
```

**EXPECT**:
- `test_propose_branch_naming` PASSES with updated regex.
- `test_branch_name_unique_across_seconds` PASSES, runs in ~1.1s.
- All other tests in the file (dry-run, draft=True, mark_proposed, idempotency, etc.) PASS unchanged — H3 must not regress them.

### Level 3: FULL_SUITE

```bash
poetry run pytest tests/ -x
```

**EXPECT**: All tests pass. Particular attention to integration tests that may grep on the branch-name format:

```bash
poetry run pytest tests/ -k "branch or learning or propose" -v
```

### Level 4: DATABASE_VALIDATION

Not applicable — no schema, persistence, or migration changes.

### Level 5: BROWSER_VALIDATION

Not applicable — no UI changes.

### Level 6: MANUAL_VALIDATION

In the `sentinel-dev` container against a tmp git repo (or staging Sentinel checkout):

1. Force a `propose_overlays` failure mid-run by pointing it at a GitLab project with a bogus token, OR by `monkeypatch`-ing `gitlab_client.create_merge_request` to raise.
2. Confirm a `sentinel-learning/promote-<scope>-<stamp>` branch is left on disk after the failure.
3. Within 30 seconds, run `sentinel learning propose --scope <same-scope>` again.
4. Confirm the second run creates a *different* branch (visible in `git branch --list 'sentinel-learning/promote-*'`) and reaches the `git checkout -b` step without `RuntimeError`.

(Optional — only if a real GitLab is reachable from the test environment.)

---

## Acceptance Criteria

- [ ] `_branch_name_for("drupal")` produces a name matching `^sentinel-learning/promote-drupal-\d{8}-\d{6}$`.
- [ ] Two consecutive `_branch_name_for(scope)` calls with a real ~1s gap produce different names.
- [ ] All pre-existing tests in `tests/core/test_propose_overlay.py` continue to pass.
- [ ] Branch prefix `sentinel-learning/promote-` remains stable and greppable (no test using `.startswith("sentinel-learning/promote-")` needs updating).
- [ ] `propose_overlays` public signature, exception contract, and dry-run cleanup behavior unchanged.
- [ ] No new dependencies added to `pyproject.toml`.
- [ ] Diff is < 30 lines net (1-line format change + docstring + 1 regex char + ~15-line new test).

---

## Completion Checklist

- [x] Task 1 done: `propose_overlay.py:97` strftime updated, docstring updated.
- [x] Task 2 done: existing regex test updated to `\d{6}`.
- [x] Task 3 done: new `test_branch_name_unique_across_seconds` added; `import time` added.
- [x] Level 1 static analysis passes (ruff + mypy on the two changed files).
- [x] Level 2 targeted tests pass (full `test_propose_overlay.py` green — 15/15).
- [x] Level 3 full pytest suite passes — no H3 regression (17 pre-existing failures verified unrelated: docker/network/LLM-cred sandbox limits).
- [ ] (Optional) Level 6 manual smoke test confirms same-minute retry works end-to-end.
- [ ] Beads issue (if filed for H3) updated to `review` once tests are green.

---

## Risks and Mitigations

| Risk                                                                                  | Likelihood | Impact | Mitigation                                                                                                |
| ------------------------------------------------------------------------------------- | ---------- | ------ | --------------------------------------------------------------------------------------------------------- |
| `time.sleep(1.05)` makes the new test occasionally flake on a heavily-loaded CI       | LOW        | LOW    | 50ms guard band over the 1s second-tick is comfortable; `pytest` has no per-test timeout we'd hit at 1.1s |
| An out-of-band test or integration script greps on `\d{4}$` (4-digit minute time)     | LOW        | MED    | Pre-flight `grep -rn '\\d{4}$' tests/` and `\\d{4}` in any branch-name-shape assertion before merging; only one such regex was found in the inventory above |
| H2 lands first and changes the failure-cleanup contract                               | LOW        | LOW    | H3 is orthogonal; it makes retries safer regardless of whether H2 deletes-on-failure or not. No coordination needed beyond not touching the same lines. |
| Sub-second collision (two CLI invocations from the same shell within <1s)             | VERY LOW   | LOW    | Not a workflow we observe; if it ever materialises we revisit with Option 2 (hex suffix). Documented in "NOT Building". |
| Branch-name length explodes downstream (e.g. CI ref-namespaces)                       | VERY LOW   | LOW    | 14-char suffix vs 12 — no realistic platform limit at risk                                                |

---

## Notes

- **Coordination with H2 / H4.** Per the brief, H3 is in the *same function module* as H2 (branch state restore on failure) and H4 (dry-run overlay edit safety) but the changes are orthogonal. Implementer should NOT touch lines 467-480 (dry-run revert) or lines 560-564 (failure-no-cleanup) — those belong to H2/H4. If H2 lands first and changes the failure cleanup, this fix is unaffected: same-minute retries still get distinct names either way.
- **Why not `secrets.token_hex(3)` (Option 2 from brief).** See "Approach Chosen" — codebase has zero precedent for random branch-name suffixes; existing branches favor sortable human-readable names; brief's own constraint (b) prioritises operator ergonomics on failed runs, which seconds delivers and hex suffixes regress.
- **Confidence**: Very high. The change is one literal-string update plus a 15-line test. Risk surface is essentially zero outside the `_branch_name_for` helper. Pre-existing tests (idempotency, dry-run, draft=True, provenance trailer, event publication) all pass through unchanged because none of them care about the time-suffix granularity.
- **Estimated implementation time**: 10-20 minutes including running the suite locally.

---

**Confidence Score**: 9/10 for one-pass implementation success.
- One-line behavior change with crystal-clear acceptance criteria.
- Existing test gives the exact regex shape to update.
- New test pattern is mirrored from the same file.
- Only -1 because the new test contains a real `time.sleep`, which is conventionally something maintainers question on review even when justified — slight risk of a "use freezegun instead" comment loop. The plan documents the rationale explicitly to short-circuit that.
