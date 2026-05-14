# Implementation Report — H6 GitLab Pagination Safety Hatch

**Plan**: `.claude/PRPs/plans/h6-gitlab-pagination-safety-hatch.plan.md`
**Completed**: 2026-05-14T11:36Z
**Iterations**: 1

## Summary

Added a `max_pages: int = 1000` keyword-only safety hatch to `GitLabClient.list_merged_mrs_since`. When the cap is hit, the method logs a `WARNING` (with project, updated_after, page, max_pages, results-so-far, per_page) and returns the partial result rather than raising. Default behaviour on healthy GitLab installations is byte-for-byte unchanged: 100 000 MRs of headroom (1000 pages × 100 rows) protects against any realistic Drupal-shop project size while bounding the loop in the proxy-misbehavior failure mode.

## Tasks Completed

- **Task 1**: Added `max_pages: int = 1000` kwarg + cap-hit guard + docstring update to `list_merged_mrs_since` in `src/gitlab_client.py`.
- **Task 2**: Added `test_safety_hatch_caps_pagination_at_max_pages` to `TestListMergedMrsSince`.
- **Task 3**: Added `test_default_max_pages_does_not_interfere_with_short_page_termination` (negative-space).
- **Task 4**: Full regression sweep across `test_gitlab_client.py`, `test_outcome_sync.py`, `test_cli_outcomes.py`, `test_phase3a_outcomes.py`.

## Validation Results

| Check | Result |
|-------|--------|
| mypy `src/gitlab_client.py` | PASS — 0 errors |
| ruff delta vs. baseline | 0 (pre-existing F841 at `tests/test_gitlab_client.py:625` — confirmed pre-existing by stashing the diff) |
| `TestListMergedMrsSince` | PASS — 6/6 (4 existing + 2 new) |
| `tests/test_gitlab_client.py` (full file) | PASS — 44/44 |
| `tests/core/test_outcome_sync.py` | PASS — 27/27 |
| `tests/test_cli_outcomes.py` | PASS — 4/4 |
| `tests/integration/test_phase3a_outcomes.py` | PASS — 1/1 |

## Codebase Patterns Discovered

- `src/gitlab_client.py` now has **both** a module-level `logger` (line 10) and historical in-method `logging.getLogger(__name__)` calls (lines 484, 585). New log sites should prefer the module-level logger for consistency with `list_merge_requests`'s recently-added safety hatch — the in-method imports are legacy.
- The `max_pages` kwarg + WARNING + partial-return pattern is now used by **two** sibling methods in this file (`list_merge_requests` lines ~220-280, `list_merged_mrs_since` lines ~290-360). Future paginated additions to this file should follow the same shape.
- pytest `caplog.at_level(level, logger="src.gitlab_client")` correctly captures the SUT's logger when the test imports the SUT as `from src.gitlab_client import GitLabClient` — the in-method `__name__` resolves to `src.gitlab_client` exactly. No adjustment needed.
- For unbounded mock streams (testing infinite-loop guards), use `side_effect=lambda *a, **kw: factory()` rather than a list of N pre-built mocks — the callable is unbounded so the test correctly exercises "what if pagination never terminates on its own".

## Deviations from Plan

The plan specified in-method `import logging; logger = logging.getLogger(__name__)` and explicitly forbade introducing a module-level logger. However, the codebase already has a module-level `logger` (line 10), present before this change. The new code uses the existing module-level logger for consistency with the sibling `list_merge_requests` safety hatch, which also uses it. Behavior is identical and the new test verifies log capture works correctly via the `src.gitlab_client` logger name.

The plan's "Why not a module-level logger?" rationale is now stale — the file gained one at some point after the plan was written. The deviation is documented in the plan file's post-execution notes.

## Risks Verified

- **Partial-result safety**: Confirmed via Level 3 sweep — `OutcomeSyncService` tests (27 cases) all pass, validating that the watermark-advances-only-past-handled-MRs invariant holds and partial returns are idempotent.
- **No regressions in existing 4 tests**: Confirmed — they pass byte-for-byte with no test-side changes.
