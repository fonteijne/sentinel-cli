# Implementation Report — H5: Eliminate O(M*N) GitLab API Calls in Revert-MR Detection

**Plan**: `.claude/PRPs/plans/h5-revert-mr-detection-n-squared.plan.md`
**Completed**: 2026-05-14
**Iterations**: 1

## Summary

Hoisted the project-wide merged-MR listing out of the per-MR `_find_revert_mr`
hot path and into a single per-`sync()` fetch on `OutcomeSyncService`. The fetch
is now narrowed by `created_after = min(merged_at across Sentinel-owned MRs)`,
and `list_merge_requests` was extended with `created_after` / `per_page` /
`max_pages` kwargs plus pagination (mirroring `list_merged_mrs_since`).

Result: `list_merge_requests` is called **at most once per `sync()`** regardless
of how many Sentinel-tagged MRs are processed, and **zero times** when there are
no Sentinel-owned MRs. Classification semantics (`classify_outcome` output) are
byte-identical pre/post-refactor — pinned by the unchanged exit-criterion
integration test plus a new dedicated unit test.

## Tasks Completed

1. **`src/gitlab_client.py`** — `list_merge_requests` extended with `created_after`,
   `per_page`, `max_pages`; mirrors `list_merged_mrs_since` pagination
   (`X-Total-Pages` then short-batch fallback) with a new `max_pages` safety
   hatch + `logger.warning`. Added module-level `logger`.
2. **`tests/test_gitlab_client.py`** — added 5 new tests covering
   `created_after` passthrough, default-omission for back-compat, multi-page
   concatenation, short-page termination fallback, and `max_pages` safety
   hatch. Updated 4 existing tests to set `mock_response.headers = {}` for the
   new pagination loop.
3. **`src/core/learning/outcome_sync.py`** —
   - Added `_fetch_revert_candidates(project, created_after, summary)` with
     the existing best-effort error-containment pattern.
   - In `sync()`, filter `mrs` to Sentinel-owned via `_BRANCH_RE`, compute
     `min_merged_at = min(merged_at or updated_at)`, and call
     `_fetch_revert_candidates` once. Skip the fetch when there are no
     Sentinel-owned MRs.
   - Pass `revert_candidates` down to `_process_mr` → `_find_revert_mr`.
   - Refactored `_find_revert_mr` to `(mr, candidates) → Optional[MR]` — pure
     scan, no HTTP, with an updated docstring.
4. **`tests/core/test_outcome_sync.py`** — added `TestRevertLookupOptimization`
   class with 9 tests pinning the call-count invariant, the
   `created_after = min(merged_at)` selection, the
   non-Sentinel-branch / empty-MR-list short-circuits, the merged_at→updated_at
   fallback, classification byte-identity, and one-error-not-M for fetch
   failures.
5. **Exit criterion** — `tests/integration/test_phase3a_outcomes.py` passes
   without modification.
6. **Docstring** — updated as part of the `_find_revert_mr` refactor.
7. **Validation** — ruff + mypy clean; 74/74 H5-relevant tests pass; wider
   suite shows 0 new regressions (17 pre-existing failures unchanged, all
   environmental: missing Docker, plan_generator agent fixtures, jira_server).

## Validation Results

| Check                           | Result | Notes                                                    |
| ------------------------------- | ------ | -------------------------------------------------------- |
| ruff `src/gitlab_client.py`     | PASS   |                                                          |
| ruff `src/core/learning/outcome_sync.py` | PASS |                                                    |
| mypy both files                 | PASS   |                                                          |
| `tests/core/test_outcome_sync.py` | PASS | 27/27 (was 18; added 9 in `TestRevertLookupOptimization`) |
| `tests/test_gitlab_client.py`   | PASS   | 42/42 (was 37; added 5)                                  |
| `tests/integration/test_phase3a_outcomes.py` | PASS | byte-identical, no edits                  |
| `tests/test_cli_outcomes.py`    | PASS   | 4/4                                                      |

**API call reduction (verified by `call_count` assertions):**
- 1 Sentinel MR → 1 `list_merge_requests` call (unchanged from before)
- 10 Sentinel MRs → 1 call (was 10) — **10× reduction**
- N non-Sentinel MRs only → 0 calls (was 0; unchanged)
- Empty `mrs` → 0 calls (was 0; unchanged)
- Lookup failure with M MRs → 1 error in `summary.errors` (was M)

## Codebase Patterns Discovered

- **Per-`sync()` cache pattern**: a short-lived, single-flight service method
  is the right boundary for "fetch once, scan many". The hoisted fetch lives
  in `sync()`, never crosses sync boundaries, has no invalidation problem.
- **Backward-compatible kwarg additions**: keep positional/old kwargs unchanged,
  use a `*` separator to add keyword-only params, preserve sensible defaults
  so existing callers don't pass them. Five `list_merge_requests` callers in
  `cli.py` / `agents/` were untouched.
- **`mock_response.headers = {}` is required** when production code calls
  `.headers.get(...)` and the test uses bare `Mock()`. A bare `Mock`'s
  `.headers.get(...)` returns a `Mock`, not `None`, so `int(...)` blows up.
  Caught 4 existing tests when adding pagination.
- **One-error-not-M for hoisted lookups**: error containment must match the
  fetch granularity. Hoisting the fetch means hoisting the error too — append
  to `summary.errors` once, not once per consumer.

## Learnings

- The plan's recommendation to add pagination *unconditionally* (rather than
  opt-in per call) was correct. The 5 existing callers all pass a
  `source_branch` filter and consume only `mrs[0]`, so the new
  `created_at asc` ordering and `per_page=100` are no-op for them but fix the
  latent under-fetch bug if a branch ever has >20 MRs.
- The plan's GOTCHA about `mr.get("merged_at") or mr.get("updated_at")` was
  load-bearing — without the fallback, the new `min_merged_at` computation
  would silently miss old MRs lacking `merged_at`.
- The exit-criterion integration test stayed byte-identical because it sets
  `gl.list_merge_requests.return_value` (not `side_effect`); the same return
  list satisfies the new single-call path and the old per-MR path.

## Deviations from Plan

- Added a 5th gitlab_client test (`test_passes_no_created_after_when_omitted`)
  that the plan didn't list explicitly, to lock in default-omission for the
  back-compat invariant the plan does require. Same intent, finer-grained.
- Added `test_min_merged_at_excludes_non_sentinel_branches` and
  `test_min_merged_at_falls_back_to_updated_at_when_merged_at_missing` to the
  optimization test class — both cover edge cases the plan flagged in
  GOTCHAs but didn't enumerate as explicit tests.
- Updated 4 pre-existing `TestListMergeRequests` tests to set
  `mock_response.headers = {}` (mechanical fix forced by the new pagination
  code path; not a plan deviation, just necessary fallout).
