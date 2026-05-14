---
iteration: 1
max_iterations: 20
plan_path: ".claude/PRPs/plans/h2-propose-overlays-restore-head.plan.md"
input_type: "plan"
started_at: "2026-05-14T00:00:00Z"
completed_at: "2026-05-14T00:00:00Z"
status: "complete"
---

# PRP Ralph Loop State

## Codebase Patterns

- **Subprocess git read pattern**: when a non-zero exit code is a SIGNAL (e.g. detached HEAD detection) rather than an error, use `check=False, capture_output=True, text=True` and inspect `result.returncode` + `result.stdout`. This is distinct from the existing `check=True` + `CalledProcessError` pattern used for git mutations.
- **finally-block restore pattern**: a `finally` that performs cleanup MUST use `check=False` and log on failure (never raise). Otherwise `CalledProcessError` from the finally will mask any in-flight exception from the `try`, producing a confusing chained traceback. The original error is the user-visible failure; cleanup degradation is WARNING-level.
- **Test fixture HEAD reads after restore**: when production code restores HEAD on exit, tests that read from the working tree or `git show HEAD:...` after the call will fail. Read commits via `git show <branch_name>:<path>` using the branch name returned in the result dataclass.
- **Don't use `git checkout -` for round-tripping**: the reflog `@{-1}` is implicitly mutated by intervening checkouts (especially in the dry-run path which itself calls `git checkout -`). Capture the starting ref by name/SHA at the start and pass it explicitly on restore.

## Current Task
COMPLETE — H2 propose_overlays HEAD restore implemented and validated.

## Plan Reference
.claude/PRPs/plans/h2-propose-overlays-restore-head.plan.md

## Progress Log

### Iteration 1 — 2026-05-14

#### Completed
- Task 1: `_capture_starting_ref(repo_root)` added after `_branch_name_for` in `src/core/learning/propose_overlay.py`. Uses `git symbolic-ref --short HEAD` (text=True, check=False) with `git rev-parse HEAD` fallback; raises `RuntimeError` if both fail.
- Task 2: `_restore_starting_ref(repo_root, ref)` added immediately after Task 1's helper. `check=False` + WARNING log on failure (never masks original exception).
- Task 3: `propose_overlays` rewired. Snapshot captured AFTER empty-rules early return, BEFORE `branch_name = _branch_name_for(scope)`. Existing `try/except Exception:raise` block converted to `try/finally`. Old `except` removed; replaced with state-contract comment block. `git checkout -b` block stays OUTSIDE the new `finally` (if checkout-b fails, HEAD never moved → nothing to restore).
- Task 4: `_current_ref(repo_root)` test helper added in `tests/core/test_propose_overlay.py` below `_list_branches`.
- Task 5: 4 new tests added: `test_real_run_restores_head_on_success`, `test_real_run_restores_head_on_failure`, `test_dry_run_restores_head_when_apply_overlay_raises_midflow`, `test_restores_to_detached_head_when_started_detached`.
- Updated existing `test_propose_writes_provenance_trailer` (and integration test `test_extract_propose_promote_revoke_full_workflow`) to read the committed overlay via `git show <promote_branch>:<path>` instead of `git show HEAD:<path>` / working-tree. Necessary because HEAD now restores.

#### Validation Status
- Level 1 (ruff): PASS — `ruff check src/core/learning/propose_overlay.py tests/core/test_propose_overlay.py` exits 0.
- Level 1 (mypy): PASS — "Success: no issues found in 1 source file".
- Level 2 (unit tests): PASS — 14/14 tests in `test_propose_overlay.py` green (10 existing + 4 new).
- Level 3 (full suite): PASS for affected scope — 956 passing, 26 pre-existing baseline failures verified unrelated by stashing H2 changes and re-running the same 26 failed (in `test_environment_manager`, `test_jira_server_client`, `test_plan_generator`, `test_worktree_manager` — all unrelated to learning/). Critical integration test `tests/integration/test_phase2c_promotion.py` PASSES.
- Level 6 (manual): DEFERRED (requires GitLab API access from sentinel-dev container).

#### Learnings
- `git checkout <sha>` produces "detached HEAD" warning to stderr, but `capture_output=True` swallows it harmlessly. Documented in helper docstring.
- Plan said "9 existing tests"; file actually had 10. Off-by-one in plan wording; not consequential.
- The HEAD-restoration invariant has a non-obvious downstream consequence: any test that read from `git show HEAD:...` after the call now reads the restored branch, not the promote branch. Two tests (one unit, one integration) had to be updated mechanically. Test intent preserved.

#### Next Steps
None — plan complete, ready to archive.

---
