# Feature: H5 — Eliminate O(M*N) GitLab API Calls in Revert-MR Detection

## Summary

`OutcomeSyncService._find_revert_mr` currently fetches the project's *entire* merged-MR list from GitLab once per processed MR. With M Sentinel-tagged MRs to classify and N total merged MRs in the project, this is O(M*N) GitLab API calls (and bytes transferred) per `outcomes sync` invocation. The fix combines two complementary optimizations: (1) **hoist the merged-MR listing once per `sync()` call** into a per-call cache, and (2) **constrain the listing with `created_after = min(merged_at across handled MRs)`** so the request returns only MRs created after the earliest reverted candidate. The `(label, evidence)` output of `classify_outcome` stays byte-identical; existing append-once and watermark semantics are unchanged.

## User Story

As a Sentinel maintainer running `sentinel outcomes sync` against a GitLab project with hundreds of merged MRs
I want revert-detection to make at most one paginated `list_merge_requests` call per project per sync (instead of one per Sentinel-tagged MR)
So that sync time and GitLab rate-limit budget no longer scale with `M*N` and a stale watermark cannot cause a sync to wedge against rate limits.

## Problem Statement

Concrete, verifiable evidence in the current code (`src/core/learning/outcome_sync.py`):

- **Per-MR call site**: `_process_mr` (line 425) calls `self._find_revert_mr(project=project, mr=mr, summary=summary)` for *every* MR returned by `list_merged_mrs_since`, including MRs that don't match the `sentinel/feature/...` prefix? No — that path returns at line 390 before reaching the revert lookup. But for every Sentinel-owned MR with at least one untagged execution, `_find_revert_mr` is called.
- **Full-project fetch inside `_find_revert_mr`** (line 499-501): `self._gitlab.list_merge_requests(project_id=project, state="merged")` with no `created_after`, no `updated_after`, no `per_page`, no pagination cap. The implementation in `src/gitlab_client.py:209-239` makes a single un-paginated request and returns whatever GitLab puts on page 1 (default `per_page=20`). On a large project this is *also* an under-fetch bug (silently misses old reverts) but per the brief that's a separate concern.
- **API call scaling**: For M Sentinel-tagged MRs, the per-sync GitLab call count from this method is `M * 1` (or `M * pages` if pagination were added). Each call transfers up to N MRs' worth of metadata. Combined with the per-MR `list_pipelines_for_commit`, the dominant work term is `M * (1 + pipeline_call)`.
- **Title-only detection**: `grep -rn "related_merge_requests\|reverted_by\|revert_commit" /workspace/sentinel/src/` returns only the `reverted_by_mr_iid` *output* field on `OutcomeRecorded`. There is no MR-relationship-API path that we'd be duplicating; the title-regex scan in `_find_revert_mr` (line 515-525) is the only revert-detection mechanism.
- **Caching scope**: `src/cli.py:3497-3504` shows the CLI iterates `service.sync(project=proj, ...)` once per project. `sync()` is short-lived and single-flight (no internal threading; one connection, one client, one event bus per call). Per-`sync()` caching is therefore safe — there is no cross-call invalidation problem, and any new revert merged *during* the sync would not have been visible to the current run anyway (snapshot semantics matching the existing `list_merged_mrs_since` watermark contract).

Result: a project with 500 merged MRs and 50 stale-watermark Sentinel MRs costs 50 × `list_merge_requests` calls plus the same number of `list_pipelines_for_commit` calls — versus the achievable 1 + 50 split.

## Solution Statement

Two complementary optimizations, applied together:

1. **Hoist + cache the merged-MR listing once per `sync()` call.** At the top of `sync()` (after `_resolve_updated_after` and the `list_merged_mrs_since` fetch but before the per-MR loop), if there is at least one MR to process, fetch the project's merged-MR list once and pass it down to `_process_mr` → `_find_revert_mr`. The listing is paginated using the same pattern as `list_merged_mrs_since` (existing convention in `src/gitlab_client.py:241-281`), capped by a safety hatch to avoid unbounded loops.

2. **Constrain by `created_after`.** A revert MR must be created strictly after the source MR it reverts. Compute `min_merged_at` across the candidate Sentinel-owned MRs at the top of the per-MR loop and pass `created_after=min_merged_at` to the merged-MR listing. This narrows N to "merged reverts created after the earliest candidate", which on a project with a fresh watermark is a small constant.

3. **No new `gitlab_client.py` method** — extend the existing `list_merge_requests` with optional `created_after`, `per_page`, and `max_pages` keyword arguments while preserving the existing positional/`source_branch` signature used by 5 other callers (`src/cli.py`, `src/agents/plan_generator.py`, `src/agents/base_developer.py`, tests). Pagination is added as part of this change because the un-paginated current behavior is a latent under-fetch when the candidate set ever exceeds 20.

The `_find_revert_mr` signature changes from `(project, mr, summary)` to `(project, mr, summary, candidates)` where `candidates` is the pre-fetched list passed in by `_process_mr`. When `candidates is None` (e.g. legacy callers, defensive default), it falls back to fetching once — preserving the one-call-per-`sync()` invariant from a different angle.

**Classification semantics:** `classify_outcome(mr, pipelines, revert_mr)` is **not** modified. The (label, evidence) tuple it returns is byte-identical for any given input. The only behavioral change is *where* `revert_mr` came from — a hoisted list instead of M independent fetches — and the additional caveat that `created_after` may exclude reverts created before the earliest candidate's merge time (which is logically impossible for a true revert anyway).

## Metadata

| Field            | Value                                                                |
| ---------------- | -------------------------------------------------------------------- |
| Type             | REFACTOR (perf)                                                      |
| Complexity       | LOW–MEDIUM                                                           |
| Systems Affected | `src/core/learning/outcome_sync.py`, `src/gitlab_client.py`, tests   |
| Dependencies     | requests ^2.31.0 (already present); **no new dependencies**          |
| Estimated Tasks  | 7                                                                    |
| Hard order       | gitlab_client extension → outcome_sync hoist → tests                 |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════════════╗
║                BEFORE — O(M*N) GitLab API calls per outcomes sync                     ║
╠═══════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                       ║
║   ┌─────────────────────┐                                                             ║
║   │ sentinel outcomes   │                                                             ║
║   │ sync --project P    │                                                             ║
║   └──────────┬──────────┘                                                             ║
║              │                                                                        ║
║              ▼                                                                        ║
║   ┌─────────────────────────┐    1× ┌──────────────────────────────────┐              ║
║   │ OutcomeSyncService.sync │──────▶│ list_merged_mrs_since(updated_after) │           ║
║   └──────────┬──────────────┘       └──────────────────────────────────┘              ║
║              │                                                                        ║
║              │ for each MR (M total Sentinel-owned):                                  ║
║              ▼                                                                        ║
║   ┌─────────────────────────┐    M× ┌──────────────────────────────────┐              ║
║   │ _process_mr             │──────▶│ list_pipelines_for_commit(sha)   │              ║
║   │                         │       └──────────────────────────────────┘              ║
║   │                         │    M× ┌──────────────────────────────────────────┐      ║
║   │  _find_revert_mr        │──────▶│ list_merge_requests(state='merged')      │      ║
║   │                         │       │   ↳ returns up to N rows, EVERY TIME     │      ║
║   └─────────────────────────┘       └──────────────────────────────────────────┘      ║
║                                                                                       ║
║   USER_FLOW: operator runs `sentinel outcomes sync --project acme/backend`.           ║
║   PAIN_POINT: M=50 stale-watermark MRs × N=500 merged MRs = 50 redundant fetches      ║
║              of ~500-MR pages. GitLab rate limit (300 req/min default) eaten by one   ║
║              sync; sync time dominated by repeated identical HTTP round-trips.        ║
║   DATA_FLOW: same merged-MR list fetched 50× in O(seconds), then scanned with         ║
║              identical title regex 50× to find at most a handful of real reverts.     ║
║                                                                                       ║
╚═══════════════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════════════╗
║              AFTER — O(M+N) GitLab API calls, narrowed by created_after               ║
╠═══════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                       ║
║   ┌─────────────────────────┐    1× ┌──────────────────────────────────────────────┐  ║
║   │ OutcomeSyncService.sync │──────▶│ list_merged_mrs_since(updated_after)         │  ║
║   └──────────┬──────────────┘       └──────────────────────────────────────────────┘  ║
║              │                                                                        ║
║              │ compute min_merged_at across Sentinel-owned candidates                 ║
║              ▼                                                                        ║
║   ┌─────────────────────────┐    1× ┌──────────────────────────────────────────────┐  ║
║   │ _fetch_revert_candidates│──────▶│ list_merge_requests(state='merged',          │  ║
║   │   (per-sync cache)      │       │   created_after=min_merged_at, paginated)    │  ║
║   └──────────┬──────────────┘       └──────────────────────────────────────────────┘  ║
║              │                                                                        ║
║              │ for each MR (M total Sentinel-owned):                                  ║
║              ▼                                                                        ║
║   ┌─────────────────────────┐    M× ┌──────────────────────────────────────────────┐  ║
║   │ _process_mr             │──────▶│ list_pipelines_for_commit(sha)               │  ║
║   │                         │       └──────────────────────────────────────────────┘  ║
║   │  _find_revert_mr(...,   │    0× HTTP (uses pre-fetched candidates list)            ║
║   │       candidates)       │                                                         ║
║   └─────────────────────────┘                                                         ║
║                                                                                       ║
║   USER_FLOW: same operator command — no surface change.                               ║
║   VALUE_ADD: one sync against a 500-MR / 50-Sentinel-MR project drops from            ║
║              ~100 GitLab requests to ~51 (−50 calls). Latency cut roughly in half;    ║
║              rate-limit headroom restored. Compounds with backfill: full-history      ║
║              backfills no longer become a quadratic-cost emergency.                   ║
║   DATA_FLOW: revert-candidate list materialized once at sync start, scanned in        ║
║              memory M times (O(M*K) where K = #reverts created after min_merged_at,   ║
║              typically <10).                                                          ║
║                                                                                       ║
╚═══════════════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                    | Before                                                          | After                                                                                        | User Impact                                                                  |
| ------------------------------------------- | --------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `sentinel outcomes sync --project P`        | M× `GET /merge_requests?state=merged`                           | 1× paginated `GET /merge_requests?state=merged&created_after=...`                            | Sync completes faster; no GitLab 429s on backfill                            |
| `OutcomeSyncService.sync()` internals       | `_find_revert_mr` self-fetches each call                        | `sync()` pre-fetches once; passes `candidates` list down                                     | Internal API only; CLI signature unchanged                                   |
| `GitLabClient.list_merge_requests(...)`     | No date filter, no pagination, no max-pages safety              | Optional `created_after`, `per_page`, `max_pages` kwargs; backward-compatible default        | Existing 5 callers untouched (default `per_page` keeps page-1 semantics)     |

---

## Mandatory Reading

**Implementation agent MUST read these files before starting any task:**

| Priority | File                                                  | Lines     | Why Read This                                                        |
| -------- | ----------------------------------------------------- | --------- | -------------------------------------------------------------------- |
| P0       | `src/core/learning/outcome_sync.py`                   | 218-332   | `sync()` method — where the hoist call site lives                    |
| P0       | `src/core/learning/outcome_sync.py`                   | 362-477   | `_process_mr` — caller of `_find_revert_mr`, gets new `candidates` arg |
| P0       | `src/core/learning/outcome_sync.py`                   | 479-526   | `_find_revert_mr` — the function being optimized                     |
| P0       | `src/gitlab_client.py`                                | 209-239   | `list_merge_requests` — extending with kwargs                        |
| P1       | `src/gitlab_client.py`                                | 241-281   | `list_merged_mrs_since` — pagination pattern to MIRROR               |
| P1       | `tests/core/test_outcome_sync.py`                     | 93-105    | `_make_gitlab_mock` helper — mock-spec convention                    |
| P1       | `tests/core/test_outcome_sync.py`                     | 324-335   | revert-lookup-failure test — error-containment contract              |
| P0       | `tests/integration/test_phase3a_outcomes.py`          | all       | Exit-criterion test — must still pass byte-identically               |
| P2       | `tests/test_gitlab_client.py`                         | 365-430   | `list_merge_requests` test patterns — extend, don't break            |
| P2       | `src/cli.py`                                          | 3470-3506 | CLI orchestration — confirms one `sync()` per project per invocation |

**External Documentation:**

| Source                                                                                     | Section                                       | Why Needed                                                                                 |
| ------------------------------------------------------------------------------------------ | --------------------------------------------- | ------------------------------------------------------------------------------------------ |
| GitLab REST API: `GET /projects/:id/merge_requests`                                        | Query parameters table                        | Confirm `created_after` / `created_before` are CE+EE, ISO-8601 (e.g. `2026-05-01T00:00:00Z`); available since GitLab 11.x |
| GitLab REST API: pagination headers                                                        | `X-Total-Pages`, `X-Next-Page`                | Mirror existing `list_merged_mrs_since` pagination contract                                |

**GOTCHA**: GitLab's `created_after` filter is **inclusive on the second** — pass an ISO-8601 timestamp. The format already used by `list_merged_mrs_since` (`updated_after=...`) is correct. Use the source MR's `merged_at` (same string format as elsewhere in the codebase) and subtract no buffer; reverts created at the same exact second as the merge are vanishingly improbable and the title-regex still acts as a final filter.

**GOTCHA**: `mr.get("merged_at")` may be `None` for very old MRs lacking the field; fall back to `mr.get("updated_at")` (always present per the existing watermark code path at `outcome_sync.py:390`).

---

## Patterns to Mirror

**PAGINATION_LOOP** (paginate `list_merge_requests` like `list_merged_mrs_since`):

```python
# SOURCE: src/gitlab_client.py:259-281
# COPY THIS PATTERN:
results: List[Dict[str, Any]] = []
page = 1
while True:
    params: Dict[str, Any] = {
        "state": "merged",
        "order_by": "updated_at",
        "sort": "asc",
        "updated_after": updated_after,
        "per_page": per_page,
        "page": page,
    }
    response = self.session.get(url, params=params)
    response.raise_for_status()
    batch: List[Dict[str, Any]] = response.json()
    results.extend(batch)
    total_pages_hdr = response.headers.get("X-Total-Pages")
    if total_pages_hdr is not None:
        if page >= int(total_pages_hdr):
            break
    elif len(batch) < per_page:
        break
    page += 1
return results
```

**MAX_PAGES_SAFETY_HATCH** (also resolves H6 risk class for the new pagination — narrow scope: only inside the new code paths we add. Do **not** retrofit `list_merged_mrs_since` here; that's tracked separately as H6.):

```python
# NEW PATTERN — add to the new paginated path:
if page >= max_pages:
    logger.warning(
        "list_merge_requests: hit max_pages=%d safety hatch for project=%s",
        max_pages, project_id,
    )
    break
```

**ERROR_CONTAINMENT** (mirror existing best-effort lookup):

```python
# SOURCE: src/core/learning/outcome_sync.py:498-509
# COPY THIS PATTERN (for the hoist-fetch error path):
try:
    candidates = self._gitlab.list_merge_requests(...)
except Exception as exc:
    msg = (
        f"revert lookup (list_merge_requests) failed project={project}: {exc}"
    )
    logger.warning(msg)
    summary.errors.append(msg)
    candidates = []  # empty list → no revert match → success classification
```

**MOCK_SPEC** (test fixture pattern for GitLabClient):

```python
# SOURCE: tests/core/test_outcome_sync.py:93-105
# COPY THIS PATTERN:
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

**CALL_COUNT_ASSERTION** (new pattern — call counts via `Mock.call_count`):

```python
# COPY THIS PATTERN:
service.sync(project="acme/backend")
# After Optimization: list_merge_requests called once per sync, regardless of M.
assert gl.list_merge_requests.call_count == 1
# After Optimization: list_pipelines_for_commit still called per Sentinel-owned MR.
assert gl.list_pipelines_for_commit.call_count == M
```

---

## Files to Change

| File                                              | Action | Justification                                                                              |
| ------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------ |
| `src/gitlab_client.py`                            | UPDATE | Extend `list_merge_requests` with optional `created_after`, `per_page`, `max_pages` kwargs |
| `src/core/learning/outcome_sync.py`               | UPDATE | Hoist revert-candidate fetch into `sync()`; pass `candidates` down to `_find_revert_mr`    |
| `tests/test_gitlab_client.py`                     | UPDATE | Add tests for `created_after` parameter passthrough + pagination + `max_pages` safety hatch |
| `tests/core/test_outcome_sync.py`                 | UPDATE | Add `test_list_merge_requests_called_once_per_sync` and N-MR scaling assertion             |
| `tests/integration/test_phase3a_outcomes.py`      | NO CHANGE | Must continue to pass byte-identically as a regression guard                             |

**No new files.** No schema migration. No CLI surface change. No new external dependency.

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **Webhook-driven outcome ingestion** — Out of scope per brief; deferred per D8 in `docs/agent-learning-from-feedback-DECISIONS.md`.
- **Revert-detection accuracy improvements beyond title regex** — Out of scope per brief. The known gap (a manually-edited revert title that matches neither the original title nor the SHA prefix) is unchanged.
- **Retrofitting `max_pages` safety hatch onto `list_merged_mrs_since`** — Tracked as H6 in the same review; intentionally not bundled here to keep the diff focused on H5.
- **Cross-`sync()` (cross-project) revert cache** — Each `sync()` call processes one project; sharing across projects would require keying by project and is unwarranted given typical usage (one project per CLI invocation).
- **Switching `list_merge_requests` to a generator/iterator** — The existing 5 non-Sentinel callers expect a `List`. Don't break them.
- **Changing the public `OutcomeSyncService.sync` signature** — All optimizations are internal.
- **Modifying `classify_outcome`** — The brief is explicit: `(label, evidence)` byte-identical on all existing tests.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/gitlab_client.py:209-239` — extend `list_merge_requests`

- **ACTION**: Add optional kwargs `created_after: Optional[str] = None`, `per_page: int = 100`, `max_pages: int = 100` to `list_merge_requests`. Implement pagination only when `created_after` is provided OR the caller opts in via a new `paginate: bool = False` flag — *or* simpler: add pagination unconditionally with `per_page` default of 100 and `max_pages=100`. **Decide for the simpler path: add pagination unconditionally**, because the existing un-paginated call is a latent under-fetch bug and the 5 existing callers all return small result sets that fit comfortably under 100 (verify by reading their call sites).
- **IMPLEMENT**:
  - Keep positional/`source_branch` signature for backward compatibility.
  - Add params dict assembly: include `created_after` only when not None.
  - Add `order_by` / `sort` to ensure stable iteration when paginating: use `order_by="created_at", sort="asc"` (created_at is the natural ordering for revert detection — and matches the new `created_after` filter).
  - Loop with `X-Total-Pages` / `len(batch) < per_page` termination, mirror `list_merged_mrs_since`.
  - Add `max_pages` safety hatch with `logger.warning` (no exception — degrade gracefully).
- **MIRROR**: `src/gitlab_client.py:241-281` (`list_merged_mrs_since` pagination loop)
- **IMPORTS**: `import logging` (existing); `logger = logging.getLogger(__name__)` (existing pattern)
- **GOTCHA**: Five existing callers (`src/cli.py:694, 876, 1037, 1287`, `src/agents/plan_generator.py:976, 1378`, `src/agents/base_developer.py:1591`) pass `state="opened"` and rely on returning a `List`. They never pass `source_branch` + pagination together, but verify each caller still works with the new ordering. If any caller depends on default GitLab ordering (newest-first) for opened MRs, swap that caller to explicitly pass `order_by="updated_at", sort="desc"` *before* the signature change — but inspection suggests they all just iterate and don't rely on order.
- **VALIDATE**: `cd /workspace/sentinel && poetry run mypy src/gitlab_client.py && poetry run ruff check src/gitlab_client.py`

### Task 2: UPDATE `tests/test_gitlab_client.py` — add coverage for new kwargs

- **ACTION**: Add three test functions to `TestListMergeRequests`:
  1. `test_passes_created_after_when_provided` — assert the request URL includes `created_after=2026-05-01T00%3A00%3A00Z` (URL-encoded) when the kwarg is set.
  2. `test_paginates_until_x_total_pages` — mock `requests.Session.get` to return two pages with `X-Total-Pages: 2`, assert both pages are concatenated in result.
  3. `test_max_pages_safety_hatch_logs_and_breaks` — mock to return full pages indefinitely, no `X-Total-Pages` header; assert exactly `max_pages` calls were made and a warning was logged.
- **MIRROR**: `tests/test_gitlab_client.py:365-430` (existing TestListMergeRequests class)
- **IMPORTS**: `from unittest.mock import Mock, patch` (already present)
- **GOTCHA**: The existing tests use `requests_mock`/manual session mocking — match whichever style the file uses (read it first).
- **VALIDATE**: `cd /workspace/sentinel && poetry run pytest tests/test_gitlab_client.py -k "list_merge_requests" -v`

### Task 3: UPDATE `src/core/learning/outcome_sync.py` — hoist revert-candidate fetch into `sync()`

- **ACTION**:
  1. Add a private helper `_fetch_revert_candidates(project: str, *, created_after: Optional[str], summary: OutcomeSyncSummary) -> List[Dict[str, Any]]` that wraps `self._gitlab.list_merge_requests(project_id=project, state="merged", created_after=created_after)` in the existing try/except → log + `summary.errors.append` + return `[]` pattern (mirror lines 498-509 of the current `_find_revert_mr`).
  2. In `sync()`, after the `mrs = self._gitlab.list_merged_mrs_since(...)` block (line 251) and before the per-MR loop (line 270), compute `min_merged_at` across the *Sentinel-owned* MRs in `mrs` (those whose `source_branch` matches `_BRANCH_RE`). If at least one such MR exists, call `_fetch_revert_candidates` once and bind to a local `revert_candidates: List[Dict[str, Any]]`. If none exist, skip the fetch entirely and use `revert_candidates = []`.
  3. Pass `revert_candidates` into `_process_mr` as a new keyword arg.
  4. In `_process_mr` (line 425), replace `revert_mr = self._find_revert_mr(project=project, mr=mr, summary=summary)` with `revert_mr = self._find_revert_mr(mr=mr, candidates=revert_candidates)`.
  5. Refactor `_find_revert_mr` to take only `(mr, candidates)` and become a pure scan over the pre-fetched list. Remove the `try/except` HTTP block (now lives in `_fetch_revert_candidates`). Remove the `project` and `summary` params and the docstring lines that referenced them.
- **MIRROR**: Error-containment pattern at `src/core/learning/outcome_sync.py:498-509` (move into `_fetch_revert_candidates`).
- **IMPORTS**: No new imports — `Optional` and `List` already imported.
- **GOTCHA 1**: Compute `min_merged_at` from `mr.get("merged_at") or mr.get("updated_at")`. The existing `_make_mr` test helper sets `merged_at == updated_at`, so existing tests still work.
- **GOTCHA 2**: When the per-MR loop encounters a non-Sentinel-owned MR (line 383, `match is None`), the loop already early-returns. The hoisted fetch only needs to consider Sentinel-owned MRs — a tighter `min_merged_at` than the full `mrs` list.
- **GOTCHA 3**: If the original MR list is empty *after* filtering for Sentinel-owned, do **not** fetch revert candidates at all — saves 1 API call on quiet syncs.
- **GOTCHA 4**: The classification semantics rule from the brief means: for any input where the *current* `_find_revert_mr` would have returned a particular `revert_mr` dict, the new path must return the same dict. Verify by adding a test that mocks `list_merge_requests` to return the same canned list and asserts the `evidence` dict is byte-identical pre/post-refactor (golden-file or direct dict comparison).
- **VALIDATE**: `cd /workspace/sentinel && poetry run mypy src/core/learning/outcome_sync.py && poetry run ruff check src/core/learning/outcome_sync.py`

### Task 4: UPDATE `tests/core/test_outcome_sync.py` — call-count + scaling assertions

- **ACTION**: Add a new `TestRevertLookupOptimization` class with these tests:
  1. `test_list_merge_requests_called_once_per_sync_with_one_mr` — single Sentinel MR, no reverts: `gl.list_merge_requests.call_count == 1`.
  2. `test_list_merge_requests_called_once_per_sync_with_many_mrs` — 10 Sentinel MRs (different ticket_ids), no reverts: `gl.list_merge_requests.call_count == 1` (NOT 10).
  3. `test_list_merge_requests_not_called_when_no_sentinel_mrs` — only non-Sentinel MRs returned: `gl.list_merge_requests.call_count == 0`.
  4. `test_list_merge_requests_passes_min_merged_at_as_created_after` — three MRs with `merged_at` of 2026-05-01, 2026-05-02, 2026-05-03; assert the call kwargs include `created_after="2026-05-01T00:00:00Z"`.
  5. `test_revert_classification_unchanged_after_hoist` — same `_make_mr` + revert fixture as `test_revert_mr_merged_is_rolled_back` at line 127; assert outcome is `rolled_back` and `evidence` dict equals the pre-refactor expected dict (capture from current main first).
  6. `test_revert_lookup_failure_records_error_and_classifies_success` — make `gl.list_merge_requests.side_effect = HTTPError` and assert *all* M MRs are classified `success` and exactly **one** error is recorded in `summary.errors` (was M errors before).
- **MIRROR**: `tests/core/test_outcome_sync.py:93-105` (`_make_gitlab_mock`); `tests/core/test_outcome_sync.py:324-335` (existing `test_revert_lookup_failure_does_not_abort_sync`).
- **IMPORTS**: existing imports cover everything.
- **GOTCHA**: The existing `test_revert_lookup_failure_does_not_abort_sync` may need updating if it currently asserts `summary.errors` length — under the new behavior it will be 1 (one fetch failure) instead of M. Update the assertion to `assert len([e for e in summary.errors if "revert lookup" in e]) == 1`.
- **VALIDATE**: `cd /workspace/sentinel && poetry run pytest tests/core/test_outcome_sync.py -v`

### Task 5: VERIFY exit-criterion test passes byte-identically

- **ACTION**: Run `tests/integration/test_phase3a_outcomes.py` and confirm:
  - All assertions pass (it was already passing; this is a regression guard).
  - `gl.list_merge_requests.call_count == 1` (one project-level call now, was 3 before — one per Sentinel MR).
- **MIRROR**: N/A — this test is the contract.
- **GOTCHA**: This test does NOT currently assert `call_count == 1` — but its other assertions must still pass without modification. Do not edit this file unless an assertion legitimately breaks (none should).
- **VALIDATE**: `cd /workspace/sentinel && poetry run pytest tests/integration/test_phase3a_outcomes.py -v`

### Task 6: UPDATE the docstring of `_find_revert_mr`

- **ACTION**: Update the docstring on the refactored `_find_revert_mr` to reflect: (1) it no longer makes HTTP calls, (2) it operates on a pre-fetched `candidates` list passed by the caller, (3) the caller is responsible for narrowing `candidates` via `created_after` for performance, (4) the title-regex semantics are unchanged.
- **MIRROR**: Existing docstring style in the same file (lines 486-497).
- **VALIDATE**: `cd /workspace/sentinel && poetry run ruff check src/core/learning/outcome_sync.py`

### Task 7: Full test suite + acceptance check

- **ACTION**: Run the full unit + integration test suite for outcome_sync and gitlab_client.
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && \
    poetry run pytest tests/core/test_outcome_sync.py \
                      tests/integration/test_phase3a_outcomes.py \
                      tests/test_gitlab_client.py \
                      tests/test_cli_outcomes.py -v
  ```
- **EXPECT**: All tests green. No regressions in `test_cli_outcomes.py` (which uses `fake_gitlab.list_merge_requests.return_value = []` at line 101 — the call-count change should not affect it).

---

## Testing Strategy

### Unit Tests to Write

| Test File                                | New Test Cases                                                                                                                                                                          | Validates                                                                |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `tests/test_gitlab_client.py`            | `test_passes_created_after_when_provided`, `test_paginates_until_x_total_pages`, `test_max_pages_safety_hatch_logs_and_breaks`                                                          | New kwargs on `list_merge_requests`                                       |
| `tests/core/test_outcome_sync.py`        | `test_list_merge_requests_called_once_per_sync_with_one_mr`, `test_list_merge_requests_called_once_per_sync_with_many_mrs`, `test_list_merge_requests_not_called_when_no_sentinel_mrs`, `test_list_merge_requests_passes_min_merged_at_as_created_after`, `test_revert_classification_unchanged_after_hoist`, `test_revert_lookup_failure_records_error_and_classifies_success` | Call-count invariant + classification preservation                       |
| `tests/integration/test_phase3a_outcomes.py` | (no new tests — existing exit-criterion test acts as regression guard)                                                                                                              | `(label, evidence)` byte-identity on the canonical fixture               |

### Edge Cases Checklist

- [ ] Empty `mrs` list returned by `list_merged_mrs_since` → `list_merge_requests` not called at all (call_count == 0).
- [ ] All MRs in `mrs` are non-Sentinel branches → `list_merge_requests` not called.
- [ ] Mixed Sentinel + non-Sentinel MRs → `min_merged_at` computed only from Sentinel-owned subset.
- [ ] `mr.get("merged_at") is None` → falls back to `mr.get("updated_at")`.
- [ ] `list_merge_requests` raises (5xx, network error) → recorded once in `summary.errors`, all MRs classified `success`-or-`regressed` (never `rolled_back`).
- [ ] `list_merge_requests` returns paginated response (3 pages) → all candidates concatenated; revert detected on page 3 still matches.
- [ ] `list_merge_requests` hits `max_pages=100` ceiling → warning logged, partial result used (acceptable: a project with >10,000 reverts after a single watermark window is operator-actionable telemetry).
- [ ] Revert title contains the original MR's title verbatim → matched (existing semantics).
- [ ] Revert title contains only the SHA prefix → matched (existing semantics).
- [ ] Two MRs with the same `merged_at` → `min_merged_at` is correctly the smallest; revert candidates for both are in the same fetch.

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
cd /workspace/sentinel && \
  poetry run ruff check src/gitlab_client.py src/core/learning/outcome_sync.py && \
  poetry run mypy src/gitlab_client.py src/core/learning/outcome_sync.py
```

**EXPECT**: Exit 0, no errors or warnings.

### Level 2: UNIT_TESTS

```bash
cd /workspace/sentinel && \
  poetry run pytest tests/core/test_outcome_sync.py tests/test_gitlab_client.py -v
```

**EXPECT**: All tests pass. New `TestRevertLookupOptimization` class has all 6 tests green.

### Level 3: FULL_SUITE

```bash
cd /workspace/sentinel && \
  poetry run pytest tests/core/test_outcome_sync.py \
                    tests/integration/test_phase3a_outcomes.py \
                    tests/test_gitlab_client.py \
                    tests/test_cli_outcomes.py -v
```

**EXPECT**: All green. Exit-criterion fixture `test_phase3a_exit_criterion` passes without modification.

### Level 4: DATABASE_VALIDATION

N/A — no schema change.

### Level 5: BROWSER_VALIDATION

N/A — no UI surface.

### Level 6: MANUAL_VALIDATION

Hard to do without a live GitLab. Recommended manual smoke (deferred to follow-up if not run):

1. On a test project with ≥3 Sentinel-tagged merged MRs and ≥1 known revert, run `sentinel outcomes sync --project P --dry-run` with logging at DEBUG.
2. Confirm exactly one `GET /projects/.../merge_requests?state=merged&created_after=...` line appears in the request log.
3. Confirm `summary.errors` is empty and `summary.executions_tagged` matches the expected count.

---

## Acceptance Criteria

- [ ] `_find_revert_mr` no longer calls `self._gitlab.list_merge_requests`; the fetch is hoisted into `sync()` via `_fetch_revert_candidates`.
- [ ] `list_merge_requests` accepts optional `created_after`, `per_page`, `max_pages` kwargs; existing 5 callers continue to work without modification.
- [ ] `tests/integration/test_phase3a_outcomes.py::test_phase3a_exit_criterion` passes byte-identically (no test edits, all assertions green).
- [ ] New unit tests assert `gl.list_merge_requests.call_count == 1` for the multi-MR sync path (the load-bearing optimization invariant).
- [ ] `classify_outcome` is unchanged — verified by `test_revert_classification_unchanged_after_hoist` golden-file equality.
- [ ] Level 1 static analysis (ruff + mypy) passes with exit 0.
- [ ] Level 2 + 3 test suites pass with exit 0.
- [ ] No new dependencies added to `pyproject.toml`.
- [ ] No CLI surface change; `sentinel outcomes sync` flags and behavior unchanged from the operator's perspective.

---

## Completion Checklist

- [x] Task 1: `list_merge_requests` extended with new kwargs + pagination + max_pages.
- [x] Task 2: `tests/test_gitlab_client.py` covers the three new behaviors (plus 2 extra: no-`created_after` back-compat, short-page fallback termination).
- [x] Task 3: `outcome_sync.py` hoists fetch into `sync()`, `_find_revert_mr` is a pure scan.
- [x] Task 4: `test_outcome_sync.py` has the call-count invariant + classification-preservation tests (9 new tests in `TestRevertLookupOptimization`).
- [x] Task 5: Exit-criterion integration test passes without modification.
- [x] Task 6: `_find_revert_mr` docstring reflects the new contract.
- [x] Task 7: Full suite green; no regression in `test_cli_outcomes.py` (74/74 H5-relevant; baseline 17 pre-existing failures unchanged).
- [x] Verified via call-count assertions: API calls drop from M to 1 for `list_merge_requests`. Concrete: 10 Sentinel MRs → 1 call (was 10).

---

## Risks and Mitigations

| Risk                                                                                                                                              | Likelihood | Impact | Mitigation                                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Adding pagination + ordering to `list_merge_requests` breaks an existing caller that depends on default un-ordered, un-paginated GitLab response. | LOW        | MEDIUM | Read all 5 caller sites before changing signature. Default `per_page=100` keeps page-1 fit for small result sets. New ordering is `created_at asc` — verify each caller iterates without depending on order. If a caller relies on default ordering, override at the call site. |
| `min_merged_at` for one stale Sentinel MR (e.g. months old) widens `created_after` enough that the optimization shrinks to a marginal win.        | LOW–MED    | LOW    | Worst case is the same as today (one paginated fetch instead of M un-paginated fetches). Still a strict improvement.                                                                                                       |
| `created_after` is interpreted differently by self-hosted GitLab CE versions older than 11.x.                                                     | LOW        | LOW    | Sentinel already targets modern GitLab (uses `updated_after` on the same endpoint). Document the minimum CE version assumption in a top-of-file comment if not already present.                                            |
| Test fixture `test_revert_classification_unchanged_after_hoist` accidentally tests the wrong `evidence` shape because it's authored fresh.        | LOW        | LOW    | Capture the canonical expected `evidence` dict by running the *current* `_find_revert_mr` once before the refactor (e.g. checkpoint test or golden file).                                                                  |
| A revert MR is created at the *exact same second* as the source MR is merged → `created_after=merged_at` excludes it.                              | VERY LOW   | LOW    | Title-regex would still scan it if present in any included page. If absolutely paranoid, subtract 1 second from `min_merged_at` before passing to `created_after`. (Plan recommends NOT doing this — vanishingly improbable.) |

**Confidence Score**: 9/10 for one-pass implementation

The refactor is well-scoped: a single hoist of an MR-list fetch out of `_find_revert_mr` into `sync()`, plus three additive kwargs (`created_after`, `per_page`, `max_pages`) on `list_merge_requests`. The five existing callers are enumerated, the load-bearing invariant (`call_count == 1`) is directly assertable, and `classify_outcome` stays untouched (golden-file pinned). The held-back point is the `created_after` assumption: it relies on GitLab CE ≥11.x semantics for the merge_requests endpoint, which Sentinel already implicitly targets via `updated_after` elsewhere — but we have not pinned a minimum CE version in code, so an older self-hosted instance would silently ignore the filter and degrade to a wider (still correct) scan rather than fail loudly. That's a graceful degradation, not a correctness break, hence 9 rather than 10.

---

## Notes

- **API call reduction (concrete)**: For a project with M=50 stale Sentinel-watermark MRs and N=500 merged MRs, calls drop from 50 (`list_merge_requests`) + 50 (`list_pipelines_for_commit`) = 100, to ≤6 (paginated `list_merge_requests` for ≤500 MRs at `per_page=100`) + 50 (`list_pipelines_for_commit`) = ≤56. Roughly a **44% reduction** in API calls and an **88% reduction** specifically in the `list_merge_requests` traffic. Compounded with `created_after`, the actual paginated count is typically 1 (since reverts created after `min_merged_at` rarely exceed 100).
- **Order of optimizations matters for correctness**: the `created_after` filter must use the *minimum* `merged_at` across Sentinel candidates, not the maximum. A revert for the earliest candidate is the constraint.
- **Per-`sync()` cache invalidation is sound** because: (a) `sync()` is short-lived (seconds, not minutes); (b) it's single-flight (no internal threading); (c) any revert merged *during* this `sync()` would only be visible if it appeared in the source `list_merged_mrs_since` window, which it does on the *next* sync (not this one) by definition. There's no observable inconsistency.
- **H6 (unbounded pagination on `list_merged_mrs_since`)** is intentionally not bundled; the `max_pages` safety hatch we add to `list_merge_requests` does NOT retrofit onto `list_merged_mrs_since`. Keep the diff focused.
- **Future work (out of scope)**: webhook-driven model (D8), MR-relationship API for revert detection (richer than title regex), and a multi-project shared cache. None are blockers for landing H5.
