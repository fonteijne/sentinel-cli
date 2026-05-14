# Feature: GitLab Pagination Safety Hatch (H6)

## Summary

Add a `max_pages` safety hatch to `GitLabClient.list_merged_mrs_since` so that a misbehaving reverse proxy or rate-limit injection layer cannot drive the CLI into an unbounded pagination loop. When the cap is hit, log a WARNING with full context (project, `updated_after`, last page seen, count of MRs collected so far) and return the partial result rather than raise — preserving forward progress for `OutcomeSyncService` while making the failure mode loud and operationally visible. The same parameter and logging convention are applied to `list_pipelines_for_commit` only if pagination is added there in the future; today it is a single-shot GET and needs no guard. No other paginated calls exist in `gitlab_client.py`.

## User Story

As a Sentinel operator running `sentinel outcomes-sync`
I want pagination loops in the GitLab client to terminate even when an upstream proxy strips the `X-Total-Pages` header AND replays full-sized pages
So that one bad day for a reverse proxy never wedges the CLI in an infinite loop, and when the safety hatch trips I see a loud WARNING in the logs telling me exactly which project + watermark hit the cap

## Problem Statement

`list_merged_mrs_since` (`src/gitlab_client.py:241-281`) terminates only when (a) `X-Total-Pages` header tells it the last page, OR (b) `len(batch) < per_page`. If a misbehaving proxy strips the header AND every page returns exactly `per_page` items (e.g. proxy is replaying or duplicating responses), neither guard trips and the loop runs forever. Real GitLab is well-behaved; the failure mode is operational, not protocol-level — but a single bad proxy day turns this into a CLI-wide hang.

This is testable: inject a `MagicMock` whose `.get` always returns a response with `headers={}` and a JSON body of exactly `per_page` rows. The current implementation will spin forever; the fixed implementation must terminate at `max_pages` and log a WARNING.

## Solution Statement

Add a keyword-only `max_pages: int = 1000` parameter to `list_merged_mrs_since`. Before incrementing `page`, check `if page >= max_pages: log + break`. The default of 1000 × default `per_page=100` = 100 000 MRs per project, which is two orders of magnitude above any realistic Drupal-shop project size, so the default behavior on healthy GitLab installations is byte-for-byte unchanged.

When the safety hatch trips, log at **WARNING** (not ERROR — the partial result is still useful and `OutcomeSyncService` will retry on the next sync cycle) with the structured context: `project`, `updated_after`, `page`, `len(results)`, `per_page`, `max_pages`. Return the partial `results` list rather than raising — `OutcomeSyncService._resolve_updated_after` already advances the watermark only past handled MRs (`outcome_sync.py:267-300`), so a partial fetch is safely idempotent on the next sync.

Audit confirms `list_pipelines_for_commit` (`src/gitlab_client.py:283-301`) is a single-shot GET with no `while`-loop — no guard needed. `list_merge_requests` (`src/gitlab_client.py:209-239`) is also single-shot. `get_merge_request_discussions` (`src/gitlab_client.py:373-469`) makes two single-shot GETs. **`list_merged_mrs_since` is the only paginated call in the file.**

Page-repetition detection (last-page MR `iid` equals previous-page MR `iid` as a secondary signal of proxy replay) is left as a NOT-BUILDING item: it would catch one specific proxy misbehavior but adds state and complicates the loop; the `max_pages` cap is sufficient at this severity.

## Metadata

| Field            | Value                                                         |
| ---------------- | ------------------------------------------------------------- |
| Type             | BUG_FIX                                                       |
| Complexity       | LOW                                                           |
| Systems Affected | `src/gitlab_client.py`, `tests/test_gitlab_client.py`         |
| Dependencies     | `requests` (already present), stdlib `logging` (already used) |
| Estimated Tasks  | 4                                                             |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                              BEFORE STATE                                     ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   Operator runs:                                                              ║
║     sentinel outcomes-sync                                                    ║
║                                                                               ║
║                       ┌────────────────────────┐                              ║
║                       │  OutcomeSyncService    │                              ║
║                       │     .sync(project)     │                              ║
║                       └───────────┬────────────┘                              ║
║                                   │                                           ║
║                                   ▼                                           ║
║                  ┌────────────────────────────────┐                           ║
║                  │ list_merged_mrs_since(...)     │                           ║
║                  │   while True:                  │                           ║
║                  │     GET ?page=N&per_page=100   │                           ║
║                  │     if X-Total-Pages: break    │  ← header missing         ║
║                  │     elif len(batch)<per_page:  │  ← always = 100           ║
║                  │       break                    │                           ║
║                  │     page += 1                  │  ← INFINITE               ║
║                  └────────────────────────────────┘                           ║
║                                                                               ║
║   Symptom: CLI hangs. No log line. No timeout. Operator must SIGKILL.         ║
║   Trigger: misbehaving reverse proxy strips X-Total-Pages and replays         ║
║            the same 100-row page repeatedly.                                  ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                               AFTER STATE                                     ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   Operator runs:                                                              ║
║     sentinel outcomes-sync                                                    ║
║                                                                               ║
║                       ┌────────────────────────┐                              ║
║                       │  OutcomeSyncService    │                              ║
║                       │     .sync(project)     │                              ║
║                       └───────────┬────────────┘                              ║
║                                   │                                           ║
║                                   ▼                                           ║
║                ┌──────────────────────────────────────┐                       ║
║                │ list_merged_mrs_since(               │                       ║
║                │   ..., max_pages=1000)               │                       ║
║                │                                      │                       ║
║                │   while True:                        │                       ║
║                │     GET ?page=N                      │                       ║
║                │     if X-Total-Pages: break          │                       ║
║                │     elif len(batch)<per_page: break  │                       ║
║                │     if page >= max_pages:            │  ← NEW GUARD          ║
║                │       logger.warning(                │                       ║
║                │         "pagination cap hit ...")    │                       ║
║                │       break                          │                       ║
║                │     page += 1                        │                       ║
║                └──────────────────────────────────────┘                       ║
║                                   │                                           ║
║                                   ▼                                           ║
║                       Returns partial results.                                ║
║                       OutcomeSyncService advances watermark                   ║
║                       only past MRs it actually handled, so                   ║
║                       next sync resumes cleanly.                              ║
║                                                                               ║
║   Symptom: WARNING log line with project + updated_after + last page,         ║
║            partial result returned, CLI completes, operator can investigate.  ║
║                                                                               ║
║   Default behavior on healthy GitLab: UNCHANGED (cap = 100k MRs).             ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                  | Before                                | After                                                       | User Impact                                                                |
| ----------------------------------------- | ------------------------------------- | ----------------------------------------------------------- | -------------------------------------------------------------------------- |
| `list_merged_mrs_since` signature         | `(project_id, *, updated_after, per_page=100)` | `(project_id, *, updated_after, per_page=100, max_pages=1000)` | New optional kwarg; default behavior unchanged for healthy GitLab.        |
| `list_merged_mrs_since` runtime           | Infinite loop on proxy misbehavior    | Bounded loop; WARNING + partial result on cap hit           | CLI no longer hangs; operator gets actionable log line.                    |
| `tests/test_gitlab_client.py`             | 4 tests for `list_merged_mrs_since`   | 4 existing + 2 new (cap hit, default-cap unchanged behavior) | Regression coverage for the safety hatch.                                  |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File                                                       | Lines     | Why Read This                                                                                     |
| -------- | ---------------------------------------------------------- | --------- | ------------------------------------------------------------------------------------------------- |
| P0       | `src/gitlab_client.py`                                     | 241-301   | The exact loop being modified + the sibling pipelines call (audit confirms no pagination there).  |
| P0       | `tests/test_gitlab_client.py`                              | 601-746   | Existing `TestListMergedMrsSince` class — new tests must mirror this style (fixtures, mocks).     |
| P1       | `src/core/learning/outcome_sync.py`                        | 240-300   | Caller — confirms watermark advances only past handled MRs, so partial-return is safe.            |
| P1       | `src/gitlab_client.py`                                     | 399-400, 500-501 | Existing in-method `import logging; logger = logging.getLogger(__name__)` pattern to mirror. |
| P2       | `pyproject.toml`                                           | all       | `mypy` strict (`disallow_untyped_defs=true`); ruff line-length 88; pytest in `tests/`.            |

**External Documentation:**

| Source                                                                                                        | Section                  | Why Needed                                                                                                                  |
| ------------------------------------------------------------------------------------------------------------- | ------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| [GitLab REST API — Pagination](https://docs.gitlab.com/ee/api/rest/index.html#pagination)                     | Offset-based pagination  | Confirms `X-Total-Pages` is the documented header; absence is non-conformant but possible behind misconfigured proxies.     |
| [Python `logging` HOWTO](https://docs.python.org/3.11/howto/logging.html#logging-basic-tutorial)              | Choosing a level         | WARNING ("indication that something unexpected happened … the software is still working as expected") matches our case.    |

---

## Patterns to Mirror

**IN-METHOD LOGGING IMPORT** (the only pattern used in this file):

```python
# SOURCE: src/gitlab_client.py:399-400 (inside get_merge_request_discussions)
# COPY THIS PATTERN:
import logging
logger = logging.getLogger(__name__)
```

Note the file does not have a module-level logger; both existing log-using methods (`get_merge_request_discussions`, `reply_to_discussion`) import inside the function. We follow that local convention rather than introducing a module-level logger.

**KEYWORD-ONLY OPTIONAL PARAMETERS**:

```python
# SOURCE: src/gitlab_client.py:241-247
# COPY THIS PATTERN — `*,` separator + typed default + docstring entry:
def list_merged_mrs_since(
    self,
    project_id: str,
    *,
    updated_after: str,
    per_page: int = 100,
) -> List[Dict[str, Any]]:
```

**EXISTING PAGINATION LOOP TO MODIFY**:

```python
# SOURCE: src/gitlab_client.py:259-281
# THIS IS THE LOOP TO ADD `max_pages` TO:
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

**TEST STRUCTURE** (mirror exactly — `Mock` with `.json` and `.headers`, `side_effect` list, `patch.object(gitlab_client.session, "get", ...)`):

```python
# SOURCE: tests/test_gitlab_client.py:609-631
# COPY THIS PATTERN FOR THE NEW max_pages TEST:
def test_paginates_via_x_total_pages_header(self, gitlab_client):
    """X-Total-Pages=2 → walk page=1 then page=2; results concatenated."""
    page1 = Mock()
    page1.json.return_value = [{"iid": 1, "updated_at": "2026-01-01T00:00:00Z"}]
    page1.headers = {"X-Total-Pages": "2"}

    page2 = Mock()
    page2.json.return_value = [{"iid": 2, "updated_at": "2026-01-02T00:00:00Z"}]
    page2.headers = {"X-Total-Pages": "2"}

    with patch.object(
        gitlab_client.session, "get", side_effect=[page1, page2]
    ) as mock_get:
        result = gitlab_client.list_merged_mrs_since(
            "acme/backend", updated_after="2026-01-01T00:00:00Z"
        )

    assert len(result) == 2
    assert [mr["iid"] for mr in result] == [1, 2]
    assert mock_get.call_count == 2
```

---

## Files to Change

| File                              | Action | Justification                                                            |
| --------------------------------- | ------ | ------------------------------------------------------------------------ |
| `src/gitlab_client.py`            | UPDATE | Add `max_pages` kwarg + WARNING log + cap-hit break to `list_merged_mrs_since`. |
| `tests/test_gitlab_client.py`     | UPDATE | Add 2 new tests in `TestListMergedMrsSince`: cap hit + default-1000 cap not interfering. |

No new files. No changes to `outcome_sync.py` (caller relies on the partial-result-is-safe invariant which already holds).

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **Streaming/iterator interface for paginated results** — would be a larger refactor; the `max_pages` safety hatch alone is enough for this severity. (Consistent with finding's "Out of scope".)
- **Page-repetition detection** (e.g. last-MR-id == previous-last-MR-id) — would catch one specific proxy replay pattern but adds state and complicates the loop. The `max_pages` cap is sufficient; if a real production proxy turns out to replay pages, this can be added later as a tighter guard.
- **Wall-clock time bound** (option 3 from the finding) — orthogonal axis with its own correctness questions (clock drift, partial-batch interaction). Reject in favor of page-count, which is deterministic and aligns with the existing pagination loop variable.
- **Guard on `list_pipelines_for_commit`** — verified by code audit (`src/gitlab_client.py:283-301`): single GET, no `while` loop. No other paginated methods exist in `gitlab_client.py`.
- **Raising on cap hit** — chose log-and-return-partial. `OutcomeSyncService` (`outcome_sync.py:267-300`) advances the watermark only past handled MRs, so partial returns are safely idempotent. Raising would convert a recoverable proxy hiccup into a hard CLI failure; logging preserves forward progress + makes the issue loud.
- **Module-level logger** — the file uses in-method `logging.getLogger(__name__)` calls; we follow the local convention rather than introducing a top-level `logger` to keep the diff minimal.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/gitlab_client.py` — add `max_pages` kwarg and cap-hit guard

- **ACTION**: Modify `list_merged_mrs_since` to accept `max_pages: int = 1000` and break with a WARNING log when the cap is hit.
- **IMPLEMENT**:
  1. Add `max_pages: int = 1000,` to the signature (after `per_page: int = 100,`, still inside the keyword-only block).
  2. Update the docstring: add a `max_pages` section describing the default, the WARNING-log + partial-return semantics, and the rationale (proxy-misbehavior safety hatch). Add a sentence to the existing pagination paragraph noting the cap.
  3. After the existing two break conditions and before `page += 1`, add:
     ```python
     if page >= max_pages:
         import logging
         logger = logging.getLogger(__name__)
         logger.warning(
             "list_merged_mrs_since hit max_pages safety hatch: "
             "project=%s updated_after=%s page=%d max_pages=%d "
             "results_so_far=%d per_page=%d — returning partial result. "
             "This usually indicates a misbehaving reverse proxy stripping "
             "X-Total-Pages and replaying full pages.",
             project_id, updated_after, page, max_pages,
             len(results), per_page,
         )
         break
     ```
- **MIRROR**: `src/gitlab_client.py:399-400` (in-method `import logging; logger = logging.getLogger(__name__)`).
- **IMPORTS**: `import logging` is added inside the cap-hit branch only (matches existing in-method pattern in this file). No top-level new imports.
- **GOTCHA #1**: The cap check must come AFTER `results.extend(batch)` so the partial result includes the last page fetched. Otherwise we throw away one page of data we already paid for.
- **GOTCHA #2**: Place the cap check AFTER the existing `X-Total-Pages` and short-page break conditions. If the API legitimately returns the last page exactly at `page == max_pages`, the existing breaks should fire first and the WARNING should NOT log. Order matters.
- **GOTCHA #3**: `page` is the page number just fetched, not the next page. `if page >= max_pages: break` after fetching page=1000 is correct: we've fetched 1000 pages × 100 rows = 100k MRs and are about to start page 1001.
- **GOTCHA #4**: Do not change the `per_page` default or the early-break behavior — the existing 4 tests in `TestListMergedMrsSince` exercise those paths and must continue to pass byte-for-byte.
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && poetry run mypy src/gitlab_client.py
  cd /workspace/sentinel && poetry run ruff check src/gitlab_client.py
  ```
  Expect: zero new errors. (Pre-existing repo errors documented in the PR review may persist; only delta matters.)

### Task 2: UPDATE `tests/test_gitlab_client.py` — add cap-hit safety-hatch test

- **ACTION**: Add a new test method `test_safety_hatch_caps_pagination_at_max_pages` to the `TestListMergedMrsSince` class (around line 696, after `test_raise_for_status_propagates`).
- **IMPLEMENT**:
  1. Build a `Mock` response that always returns exactly `per_page` rows and `headers={}` (no `X-Total-Pages`):
     ```python
     def make_full_page():
         m = Mock()
         m.json.return_value = [{"iid": i, "updated_at": "2026-01-01T00:00:00Z"} for i in range(2)]
         m.headers = {}
         return m
     ```
  2. Use `side_effect=lambda *a, **kw: make_full_page()` so every call returns a fresh full page indefinitely.
  3. Call with `max_pages=5, per_page=2`:
     ```python
     with patch.object(gitlab_client.session, "get", side_effect=lambda *a, **kw: make_full_page()) as mock_get:
         with caplog.at_level(logging.WARNING, logger="src.gitlab_client"):
             result = gitlab_client.list_merged_mrs_since(
                 "acme/backend",
                 updated_after="2026-01-01T00:00:00Z",
                 per_page=2,
                 max_pages=5,
             )
     ```
  4. Assert: `mock_get.call_count == 5` (cap stops at exactly 5 pages, not 6); `len(result) == 10` (5 pages × 2 rows = partial result returned, not raised); WARNING log line contains "max_pages safety hatch" and the project name.
  5. Add `caplog` to the test signature: `def test_safety_hatch_caps_pagination_at_max_pages(self, gitlab_client, caplog):`.
  6. Add `import logging` to the test file imports if not already present (top of file currently has no logging import).
- **MIRROR**: `tests/test_gitlab_client.py:633-655` (`test_falls_back_to_short_page_when_header_missing`) for the `Mock` + `headers={}` + `side_effect` shape.
- **IMPORTS**: `import logging` at top of test file (currently absent — verify before editing).
- **GOTCHA #1**: Use `side_effect=lambda ...` (callable) rather than `side_effect=[mock1, mock2, ...]` (list). A list would need to be at least `max_pages` long; a callable is unbounded so the test correctly exercises "what if pagination never terminates on its own".
- **GOTCHA #2**: `caplog` is a built-in pytest fixture — no extra config needed. The `logger="src.gitlab_client"` argument matches `__name__` inside the SUT (which resolves to `src.gitlab_client` when imported as `from src.gitlab_client import GitLabClient`).
- **GOTCHA #3**: Each `Mock` response must be a fresh instance (hence the `make_full_page()` factory). A single `Mock` reused across calls is fine for `.json` (returns same list) but bookkeeping for assertion of `call_count` works either way; the factory is clearer.
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && poetry run pytest tests/test_gitlab_client.py::TestListMergedMrsSince -v
  ```
  Expect: 5 passes (4 existing + 1 new). The original 4 tests must still pass — if any fail, Task 1 introduced a regression.

### Task 3: UPDATE `tests/test_gitlab_client.py` — add default-cap-unchanged test

- **ACTION**: Add a second new test `test_default_max_pages_does_not_interfere_with_short_page_termination` that verifies the default `max_pages=1000` does not change behavior when the API returns short pages or `X-Total-Pages` correctly.
- **IMPLEMENT**:
  1. Reuse the `test_falls_back_to_short_page_when_header_missing` shape but call without `max_pages` (defaulting to 1000) and assert the call still terminates after 2 pages, not 1000.
  2. Assertion: `mock_get.call_count == 2`; no WARNING log emitted (use `caplog` with `at_level(logging.WARNING)` and assert `len(caplog.records) == 0` or no record contains "safety hatch").
- **MIRROR**: `tests/test_gitlab_client.py:633-655`.
- **GOTCHA**: This test is functionally near-redundant with `test_falls_back_to_short_page_when_header_missing` but is included as **negative-space coverage**: it explicitly asserts the safety hatch does NOT fire on healthy responses. The PR review's strongest praise was for negative-space assertions; this matches the codebase culture.
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && poetry run pytest tests/test_gitlab_client.py::TestListMergedMrsSince -v
  ```
  Expect: 6 passes (4 existing + 2 new).

### Task 4: VALIDATE no regressions across the full file

- **ACTION**: Run the entire `test_gitlab_client.py` suite + the outcome_sync suites that depend on it.
- **IMPLEMENT**: No code changes. Verification only.
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && poetry run pytest tests/test_gitlab_client.py tests/core/test_outcome_sync.py tests/test_cli_outcomes.py tests/integration/test_phase3a_outcomes.py -v
  ```
  Expect: all pass. If `test_outcome_sync.py` fails, the partial-result behavior may need a thread to inspect — but per `outcome_sync.py:267-300` audit, partial results are already handled correctly (watermark advances per handled MR, not per fetch).

---

## Testing Strategy

### Unit Tests to Write

| Test                                                                                       | Validates                                                                                |
| ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| `test_safety_hatch_caps_pagination_at_max_pages`                                           | Cap trips at exactly `max_pages`; partial result returned; WARNING logged with context.  |
| `test_default_max_pages_does_not_interfere_with_short_page_termination`                    | Default `max_pages=1000` doesn't fire on healthy 2-page response (negative-space).       |

### Edge Cases Checklist

- [x] `max_pages=1000` default leaves healthy GitLab behavior unchanged (Task 3).
- [x] `max_pages` cap trips at exactly N, not N+1 or N-1 (Task 2 asserts `call_count == 5`).
- [x] Partial result returned, not raised (Task 2 asserts `len(result) == 10`).
- [x] WARNING log includes project + updated_after + page + max_pages + results_so_far (Task 2 asserts log content).
- [x] Cap check ordered AFTER existing breaks: legitimate short-page or X-Total-Pages termination at exactly page=`max_pages` does NOT log a false-positive WARNING (covered by Task 3 — header-fallback at page 2 with default cap=1000 does not log).
- [x] `outcome_sync.py` continues to function with partial results (Task 4 — pre-existing tests cover the watermark logic).

### Out of scope for testing

- HTTPError still propagates: existing `test_raise_for_status_propagates` covers this and is unchanged.
- URL encoding + canonical params: existing `test_url_encoding_and_required_params` covers this; new code does not touch param construction.

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
cd /workspace/sentinel && poetry run mypy src/gitlab_client.py && poetry run ruff check src/gitlab_client.py tests/test_gitlab_client.py
```

**EXPECT**: Zero new errors vs. the branch baseline. (PR review notes 26 pre-existing mypy errors and 18 ruff errors on the branch; this change must not increase either count.)

### Level 2: UNIT_TESTS (focused)

```bash
cd /workspace/sentinel && poetry run pytest tests/test_gitlab_client.py -v
```

**EXPECT**: All tests in `TestListMergedMrsSince` (4 existing + 2 new = 6) pass. No regressions in other test classes.

### Level 3: UNIT_TESTS (caller dependencies)

```bash
cd /workspace/sentinel && poetry run pytest tests/core/test_outcome_sync.py tests/test_cli_outcomes.py tests/integration/test_phase3a_outcomes.py -v
```

**EXPECT**: All pass. These exercise `OutcomeSyncService` which calls `list_merged_mrs_since` via a `MagicMock` — they should be fully insulated from this signature change because they pass no positional args to the method beyond `project` and use `updated_after=` kwarg.

### Level 4: FULL_SUITE (sanity)

```bash
cd /workspace/sentinel && poetry run pytest -q
```

**EXPECT**: No new failures vs. the branch baseline (per PR review: 937 passed, 26 pre-existing failures unrelated to this change).

### Level 5–6: not applicable

No DB schema changes, no UI, no manual test path required for a logic-only fix in a paginated HTTP loop.

---

## Acceptance Criteria

- [x] `list_merged_mrs_since` accepts `max_pages: int = 1000` keyword-only argument.
- [x] When the cap is hit, partial results are returned (not raised).
- [x] When the cap is hit, exactly one WARNING log line is emitted via `logging.getLogger("src.gitlab_client")` containing project, updated_after, page, max_pages, results_so_far, and per_page.
- [x] Default behavior (`max_pages=1000`) on healthy GitLab installations is byte-for-byte unchanged: existing 4 `TestListMergedMrsSince` tests pass without modification.
- [x] Two new tests cover the cap-hit path and the negative-space "default does not interfere" path.
- [x] No new mypy or ruff errors on `src/gitlab_client.py` vs. the branch baseline.
- [x] `outcome_sync.py` and its tests are unaffected — partial-result-is-safe invariant holds (verified by Task 4).
- [x] `list_pipelines_for_commit` is left unchanged: audit confirms it is single-shot, no pagination, no guard needed. This decision is documented in the docstring of `list_merged_mrs_since` and in this plan's NOT-BUILDING section.

---

## Completion Checklist

- [x] Task 1: signature + docstring + cap-hit guard added to `list_merged_mrs_since`.
- [x] Task 2: cap-hit safety-hatch test added.
- [x] Task 3: default-cap-unchanged negative-space test added.
- [x] Task 4: full regression sweep across `test_gitlab_client.py`, `test_outcome_sync.py`, `test_cli_outcomes.py`, `test_phase3a_outcomes.py` passes.
- [x] Level 1 mypy + ruff delta: 0.
- [x] Level 2 focused unit tests: 6/6 pass.
- [x] Level 3 caller-dependency tests: all pass.
- [x] Level 4 full suite: no new failures vs. branch baseline.

---

## Implementation Notes (post-execution)

**Deviation from plan**: The plan instructs in-method `import logging; logger = logging.getLogger(__name__)` for the new log site, but `src/gitlab_client.py` has gained both a module-level `logger` (line 10) and an existing `max_pages` safety hatch in the sibling `list_merge_requests` (lines 220+) since the plan was written. The existing module-level logger is used for consistency with the now-canonical pattern in this file. Behavior is identical — `caplog` captures via logger name `src.gitlab_client` either way and the new test asserts exactly that.

---

## Risks and Mitigations

| Risk                                                                                                 | Likelihood | Impact | Mitigation                                                                                                     |
| ---------------------------------------------------------------------------------------------------- | ---------- | ------ | -------------------------------------------------------------------------------------------------------------- |
| Operator with a *huge* (>100k merged MR) project hits the default cap on first sync                  | LOW        | LOW    | 100k MRs at `per_page=100` = 1000 pages — far above any realistic Drupal-shop project. Operator can pass `max_pages=10000` if needed. WARNING log makes the workaround discoverable. |
| Partial-result return interacts badly with `OutcomeSyncService` watermark logic                      | LOW        | MED    | `outcome_sync.py:267-300` advances watermark only past *handled* MRs (`max_updated_at_handled`). A short fetch is functionally identical to a slow GitLab returning fewer rows — the existing logic handles it. Task 4 regression sweep validates. |
| WARNING log line is too noisy if a proxy is chronically misbehaving                                  | LOW        | LOW    | Operator concern, not correctness concern. WARNING is the right level (still working as expected, but flagged). If chronic, operator should fix the proxy, not silence the log. |
| Future maintainer adds page-repetition detection without re-evaluating `max_pages` interaction       | LOW        | LOW    | NOT-BUILDING section documents the decision; both signals are independent (cap protects when repetition detection misses, repetition detection catches faster).                  |

---

## Notes

**Why log-and-return-partial instead of raise?**
`OutcomeSyncService.sync` (caller) already treats `list_merged_mrs_since` failure as "record an error, advance no watermark, retry next sync" — see `outcome_sync.py:254-260`. If we raise, we get that same behavior. If we return partial, the operator gets *some* outcomes recorded plus a WARNING they can act on, and the next sync resumes from the partial watermark. Partial is strictly more useful and adds zero risk because the watermark advances only past *handled* MRs, not past the fetch boundary.

**Why 1000?**
Default `per_page=100` × 1000 pages = 100 000 MRs. The largest Drupal monorepo in the wild (drupal.org itself, in MR-equivalent issue volume) is well below this. Sentinel's target customer is a Drupal shop with ≤ ~10 active projects, each with ≤ ~5000 lifetime merged MRs. 1000 pages is two orders of magnitude of headroom. If a real customer ever hits it, the WARNING log tells them exactly which knob to turn.

**Why not bump to a constant `_MAX_PAGES = 1000`?**
Configurability is essentially free here (one extra kwarg). Tests benefit (`max_pages=5` to exercise the cap quickly, instead of mocking 1000 calls). Operators benefit if they ever need to override. The hard-coded constant version (option 1 from the finding) saves four characters in the signature and loses both wins.

**Why not also instrument `list_pipelines_for_commit`?**
Audit of `src/gitlab_client.py` confirms it is the only file with a `while True` paginated loop. `list_pipelines_for_commit` (lines 283-301) is a single-shot GET; `list_merge_requests` (lines 209-239) is single-shot; `get_merge_request_discussions` (lines 373-469) is two single-shot GETs. No other paginated call exists. This is documented in NOT-BUILDING and reflected in the `list_merged_mrs_since` docstring update.

**Confidence Score: 9/10** for one-pass implementation.
- Pattern is small and well-bounded (one method, one new kwarg, one log line, two tests).
- Existing test infrastructure (`Mock`, `caplog`, `patch.object`) covers everything needed.
- No interaction with other systems (DB, env-manager, agent loop).
- Caller's partial-result tolerance is verified by code reading, not just hoped.
- One point of risk: confirming `caplog`'s logger-name match works correctly when the SUT does in-method `logging.getLogger(__name__)` — should resolve to `src.gitlab_client` and be capturable, but this is the one thing that can need a small adjustment in the test (e.g. drop the `logger=` filter and just match on message substring).
