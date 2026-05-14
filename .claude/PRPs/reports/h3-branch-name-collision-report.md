# Implementation Report: H3 — Branch-Name Collision in `learning propose`

**Plan**: `.claude/PRPs/plans/h3-branch-name-collision.plan.md`
**Completed**: 2026-05-14
**Iterations**: 1

## Summary

Widened the timestamp suffix in `_branch_name_for(scope)` from minute precision (`%Y%m%d-%H%M`) to second precision (`%Y%m%d-%H%M%S`) in `src/core/learning/propose_overlay.py`. This eliminates the same-minute branch-name collision that operators hit on a routine `sentinel learning propose` retry (failed real-run leaves a branch on disk per the H2 contract; a retry within the minute used to die with `git checkout -b ... already exists`). Updated the existing branch-naming regex test and added a new uniqueness test asserting two consecutive calls separated by ~1s yield distinct names.

## Tasks Completed

- **Task 1**: `src/core/learning/propose_overlay.py:91-103` — strftime widened to `%Y%m%d-%H%M%S`; docstring updated to document second precision and the H2 coordination rationale.
- **Task 2**: `tests/core/test_propose_overlay.py` — existing `test_propose_branch_naming` regex updated from `\d{8}-\d{4}` to `\d{8}-\d{6}`.
- **Task 3**: `tests/core/test_propose_overlay.py` — added `test_branch_name_unique_across_seconds` (real `time.sleep(1.05)`, no clock mocking) + `import time`.

## Validation Results

| Check                              | Result | Notes                                                      |
| ---------------------------------- | ------ | ---------------------------------------------------------- |
| Ruff (changed files)               | PASS   | No issues                                                  |
| Mypy (`propose_overlay.py`)        | PASS   | No issues found in 1 source file                           |
| Pytest `test_propose_overlay.py`   | PASS   | 15/15, 1.28s (incl. new uniqueness test ~1.1s)             |
| Pytest `-k branch or learning or propose` | PASS | 47/47, 1.85s                                          |
| Pytest full suite                  | PASS*  | 974 passed, 17 pre-existing sandbox-limited failures (docker/network/LLM-cred) verified to fail identically pre-change via `git stash` round-trip |

\* No H3-introduced regressions. The +1 vs baseline (974 vs 973) is the new uniqueness test.

## Acceptance Criteria

- [x] `_branch_name_for("drupal")` matches `^sentinel-learning/promote-drupal-\d{8}-\d{6}$`
- [x] Two consecutive calls with ~1s gap → distinct names (asserted by new test)
- [x] All pre-existing `test_propose_overlay.py` tests still pass
- [x] `sentinel-learning/promote-` prefix stable; `startswith` assertions untouched
- [x] `propose_overlays` public signature, exception contract, dry-run cleanup unchanged
- [x] No new dependencies in `pyproject.toml`
- [x] Net diff well under 30 lines

## Codebase Patterns Discovered

(No new permanent project-level patterns surfaced — H3 reinforces existing conventions rather than introducing new ones.)

Reinforced patterns (already documented in `Patterns to Mirror`):
- UTC timestamps in branch names use `datetime.now(timezone.utc).strftime(...)` with literal format strings — no `utcnow()`, no random suffixes anywhere in `src/`.
- Tests for private helpers import the module via alias `from src.core.learning import propose_overlay as propose_module` and call `propose_module._branch_name_for(...)` directly.

## Learnings

- **Real-clock test was the right call**, not freezegun. The 1.05s `time.sleep` runs once and adds 1.1s to the suite vs introducing a dependency or fragile module-attribute monkeypatch on `from datetime import datetime` (which would have to patch the imported reference, not `datetime.datetime`).
- **Sandbox baseline matters when reading "full suite" results.** This sandbox has no `docker` CLI and no network/LLM creds, so 17 tests in `test_environment_manager.py`, `test_jira_server_client.py`, `test_plan_generator.py`, and `test_worktree_manager.py::test_ensure_bare_clone_creates_new` fail by environment, not by code. Always do a `git stash` round-trip before claiming a "regression" against this baseline.
- **H2 contract was load-bearing for H3's framing.** The plan's repeated emphasis on the failure path at `propose_overlay.py:560-564` (which deliberately leaves the branch on disk) is what makes same-minute collision a routine bug, not a theoretical one. Worth keeping the H2/H3/H4 boundaries clean in coordinated PRs.

## Deviations from Plan

None. The plan was followed exactly:
- One-line strftime change + docstring
- One-character regex change in existing test
- New uniqueness test (real sleep, mirrored structure)
- `import time` added next to `import re`

The plan referenced "line 380" and "line 97" as approximate anchors — the actual lines drifted slightly due to prior commits (regex was on line 401, helper on line 91-98), but the targets were unambiguous.
