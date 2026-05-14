# Implementation Report

**Plan**: /workspace/sentinel/.claude/PRPs/plans/m3-overlay-relpath-validation.plan.md
**Completed**: 2026-05-14
**Iterations**: 1

## Summary

Added defense-in-depth validation to `_overlay_relpath_for(scope, agent_target)`
in `src/core/learning/propose_overlay.py` so neither argument can contribute
path-traversal characters to the overlay file path. Validation lives at the
leaf of the helper, transitively protecting all callers in the proposer.

## Tasks Completed

- Task 1: Added `import re`, added `_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")`
  module constant alongside existing constants, added validation guards inside
  `_overlay_relpath_for` that raise `ValueError` with the offending value via
  `!r` and the regex pattern in the message.
- Task 2: Appended 4 new test functions to `tests/core/test_propose_overlay.py`:
  happy-path, traversal-in-agent_target, traversal-in-scope, and a
  parametrized edge-case battery (empty, uppercase, leading digit, slash,
  backslash, dot, space, NUL byte). 14 individual test cases total.

## Validation Results

| Check | Result |
|-------|--------|
| Static analysis (ruff) | PASS (pyflakes not installed; ruff is the project default) |
| Targeted M3 tests | PASS (14/14) |
| Full propose_overlay test file | PASS (33/33) |
| Integration test_phase2c_promotion | PASS (1/1) |
| Full suite | PASS (no new failures; pre-existing failures on `main` unaffected) |
| Manual smoke (real stack_type x agent matrix) | PASS |

## Codebase Patterns Discovered

- Module constants in `propose_overlay.py` live in a single block near top
  (lines 46-50), each with an explanatory leading comment. New constants
  should follow that convention.
- `pytest.raises(match=...)` uses `re.search` semantics, so unanchored
  substring matches are sufficient and more forgiving to wording tweaks.
- Tests in `test_propose_overlay.py` import private helpers locally per-test
  (rather than at module top) — a deliberate convention to keep the public
  test surface minimal.

## Deviations from Plan

- **pyflakes substituted with ruff**: The dev container does not have
  `pyflakes` installed (`No module named pyflakes`). Ran `ruff check` instead,
  which the plan's Level 1 explicitly mentions as an acceptable alternative.
  Result: `All checks passed!`
- **Full suite (Level 5)**: The plan suggests `pytest tests/ -x -q`. Running
  with `-x` would fail-fast on the 26+ pre-existing failures (in
  `test_environment_manager`, `test_jira_server_client`, `test_plan_generator`,
  `test_worktree_manager`, plus `test_agent_integration`, `test_agent_sdk_*`).
  These are documented as pre-existing on `main` — not regressions. Confirmed
  by stashing the change and re-running: same failures persist on a clean
  tree. No new failures were introduced.

## Follow-up Work

None. The plan's "Future work" notes (DB-boundary validation, hypothesis
property test) are explicitly scoped out and can be filed as separate issues
if desired.
