# Implementation Report

**Plan**: `.claude/PRPs/plans/h2-propose-overlays-restore-head.plan.md`
**Completed**: 2026-05-14
**Iterations**: 1

## Summary

Fixed HIGH-severity operator-UX bug in `propose_overlays`: the function was leaving the operator's working tree on the freshly-created `sentinel-learning/promote-...` branch on both success and failure paths, silently mutating shell state outside its declared output. Now snapshots the starting ref via `git symbolic-ref --short HEAD` (with `git rev-parse HEAD` detached-HEAD fallback) and restores it in a `try/finally` that runs regardless of outcome. The promote branch is still preserved on failure for operator inspection — only HEAD is restored.

## Tasks Completed

| # | Task | Status |
|---|------|--------|
| 1 | Add `_capture_starting_ref(repo_root) -> str` helper | DONE |
| 2 | Add `_restore_starting_ref(repo_root, ref) -> None` helper | DONE |
| 3 | Wire snapshot + `try/finally` restore into `propose_overlays` | DONE |
| 4 | Add `_current_ref(repo_root)` test helper | DONE |
| 5 | Add 4 HEAD-restoration tests | DONE |

## Files Modified

| File | LOC change |
|------|-----------|
| `src/core/learning/propose_overlay.py` | +95 |
| `tests/core/test_propose_overlay.py` | +160 |
| `tests/integration/test_phase2c_promotion.py` | +12/-7 |

Total: 262 insertions, 12 deletions.

## Validation Results

| Level | Command | Result |
|-------|---------|--------|
| 1a | `poetry run ruff check src/core/learning/propose_overlay.py tests/core/test_propose_overlay.py` | PASS (exit 0) |
| 1b | `poetry run mypy src/core/learning/propose_overlay.py` | PASS — no issues |
| 2 | `poetry run pytest tests/core/test_propose_overlay.py -v` | PASS — 14/14 green |
| 3 | `poetry run pytest tests/ -q --ignore=tests/integration` | 956 PASS, 26 baseline failures (verified unrelated by stash test) |
| 3 (critical) | `poetry run pytest tests/integration/test_phase2c_promotion.py -v` | PASS |
| 6 | Manual sentinel-dev validation | DEFERRED (needs GitLab API access) |

The 26 baseline failures are in `test_environment_manager`, `test_jira_server_client`, `test_plan_generator`, `test_worktree_manager` — all confirmed pre-existing by stashing H2 changes and re-running: identical 26 failures on bare `feat/sentinel-learning-system`.

## Codebase Patterns Discovered

1. **Subprocess git-read with `check=False`**: when a non-zero exit code is a SIGNAL (e.g. detached HEAD) rather than an error, use `check=False, capture_output=True, text=True` and inspect `returncode`. Distinct from the existing `check=True` + `CalledProcessError` pattern used for git mutations.
2. **`finally`-block cleanup must not raise**: a `CalledProcessError` from a `finally` will mask any in-flight exception from the `try`, producing a confusing chained traceback. Use `check=False` + WARNING log; the original error is the user-visible failure.
3. **Don't use `git checkout -` for round-tripping**: the reflog `@{-1}` is implicitly mutated by intervening checkouts. Capture the starting ref by name/SHA upfront and pass it explicitly on restore.
4. **Test fixture HEAD reads after restore**: when production code restores HEAD on exit, tests that read `git show HEAD:...` after the call will read from the wrong commit. Read via `git show <branch_name>:<path>` using the branch name returned in the result dataclass.

## Learnings

- The HEAD-restoration invariant had a non-obvious downstream consequence: two existing tests (one unit, one integration) read from `git show HEAD:...` post-call and had to be updated to read from the promote branch by name. The semantic intent was preserved; the change is mechanical.
- Plan said "9 existing tests"; the file actually had 10. Off-by-one in plan; not consequential.
- `git checkout <sha>` emits "detached HEAD" warning to stderr, harmlessly swallowed by `capture_output=True`. Documented in `_capture_starting_ref` docstring.

## Deviations from Plan

1. **One additional test file modified**: the plan listed only `propose_overlay.py` and `test_propose_overlay.py`. The integration test `tests/integration/test_phase2c_promotion.py` had to be updated for the same reason as the existing unit test in #2 below — the assertion read the working-tree file post-call, but with HEAD restored that file is no longer the modified version. Now reads via `git show <promote_branch>:<path>`.
2. **One existing test had to be modified**: `test_propose_writes_provenance_trailer` previously read the committed overlay via `git show HEAD:...`. With HEAD restored, the commit is no longer at HEAD — it's only on the promote branch. Updated to read via `git show {results[0].branch_name}:...`. Test intent preserved.

Both deviations are necessary consequences of the new invariant ("HEAD always restored after the call"). They surfaced during validation and were not foreseeable from the plan as written.

## Acceptance Criteria

- [x] `_capture_starting_ref` and `_restore_starting_ref` helpers added with docstrings.
- [x] `propose_overlays` calls `_capture_starting_ref` BEFORE creating the promote branch.
- [x] `propose_overlays` wraps the post-checkout flow in `try/finally` calling `_restore_starting_ref`.
- [x] Existing `except Exception: raise` block removed; replaced with contract comment block.
- [x] Promote branch is NOT deleted on failure (existing intentional behaviour preserved, explicitly tested).
- [x] All 4 new tests pass: success-restore, failure-restore, dry-run-mid-flow-restore, detached-HEAD round-trip.
- [x] Level 1–3 validation commands pass with exit 0 (ignoring pre-existing baseline failures unrelated to H2).
- [x] No regression in existing tests in `test_propose_overlay.py`.
- [x] Module docstring (lines 8–28) verified untouched (no design invariants needed updating).
