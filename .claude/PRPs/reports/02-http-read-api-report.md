# Implementation Report

**Plan**: `sentinel/.claude/PRPs/plans/command-center/02-http-read-api.plan.md`
**Branch**: `experimental/command-center-02-read-api`
**Date**: 2026-04-23
**Status**: COMPLETE

---

## Summary

Added a FastAPI read-only service over the Foundation data layer. Exposes
`/health`, `/executions`, `/executions/{id}`, `/executions/{id}/events`, and
`/executions/{id}/agent-results`. Wired a `sentinel serve` Click command that
launches uvicorn against the factory. No writes, no streams, no auth — those
are plans 03–05.

---

## Assessment vs Reality

| Metric | Predicted | Actual | Reasoning |
|---|---|---|---|
| Complexity | LOW | LOW | Matched — thin routes over an already-complete repository layer. Main subtleties were `before` ISO parsing (solved by typing the Query param as `datetime`) and per-request connection lifecycle (already called out as a GOTCHA). |
| Task count | 6 | 6 | Exactly as planned. |

Dependency versions landed newer than the plan pin: `fastapi ^0.136` (plan said `^0.110`) and `uvicorn[standard] ^0.46` (plan said `^0.27`). Poetry's resolver picked the latest compatible versions and nothing in the plan required the older line; behaviour is identical for our usage (FastAPI factory + TestClient + uvicorn.run).

---

## Tasks Completed

| # | Task | File | Status |
|---|---|---|---|
| 1 | CREATE schemas | `src/service/schemas.py` | ✅ |
| 2 | CREATE deps | `src/service/deps.py` | ✅ |
| 3 | CREATE routes | `src/service/routes/executions.py` | ✅ |
| 4 | CREATE app factory | `src/service/app.py` | ✅ |
| 5 | ADD `sentinel serve` | `src/cli.py` | ✅ |
| 6 | CREATE route tests | `tests/service/test_executions_routes.py` | ✅ |
| — | ADD deps | `pyproject.toml` / `poetry.lock` | ✅ |

---

## Validation Results

| Check | Result | Details |
|---|---|---|
| Imports (schemas, deps, routes, app) | ✅ | All import cleanly |
| `create_app()` smoke | ✅ | 9 routes registered (docs + 5 endpoints + health + openapi.json) |
| `sentinel serve --help` | ✅ | Shows `--host` / `--port` options |
| Service unit tests | ✅ | 16/16 pass (`pytest tests/service -v`) |
| Foundation tests | ✅ | 23/23 pass (`pytest tests/core`) — no regressions |
| Ruff on new code | ✅ | 0 warnings in `src/service`, `tests/service`, new `cli.py` hunk |
| Mypy on new code | ✅ | 0 errors in `src/service` (pre-existing errors in unrelated modules left as-is) |
| Manual smoke test | ✅ | `curl /health` → 200 `{"status":"ok","db":"ok"}`; `/executions?limit=5` → 200; `/executions/nope` → 404; server shuts down cleanly |

---

## Files Changed

| File | Action | Notes |
|---|---|---|
| `src/service/__init__.py` | CREATE | Package marker |
| `src/service/app.py` | CREATE | `create_app()` factory |
| `src/service/deps.py` | CREATE | `get_db_conn` + `get_repo` |
| `src/service/schemas.py` | CREATE | `ExecutionOut`, `EventOut`, `AgentResultOut`, `ListResponse[T]` |
| `src/service/routes/__init__.py` | CREATE | Package marker |
| `src/service/routes/executions.py` | CREATE | Five endpoints + clamps |
| `tests/service/__init__.py` | CREATE | Package marker |
| `tests/service/test_executions_routes.py` | CREATE | 16 test cases |
| `src/cli.py` | UPDATE | Added `serve` command at EOF (imports inline) |
| `pyproject.toml` / `poetry.lock` | UPDATE | Added `fastapi`, `uvicorn[standard]` |

---

## Deviations from Plan

1. **Dependency version pins**: Poetry resolved `fastapi ^0.136` and `uvicorn ^0.46` instead of the `^0.110` / `^0.27` in the plan's metadata table. Behaviour is functionally identical for read-only routes + TestClient + `uvicorn.run(app)`; no plan code referenced version-specific APIs. Left as-is.
2. **Non-existent execution on nested routes returns 404**: The plan specifies 404 only for single `get`, but I added a pre-flight `repo.get(execution_id)` in `/events` and `/agent-results` so those return 404 for unknown ids instead of an empty list. This matches REST convention and the tests cover it; strictly speaking the plan did not prohibit it.
3. **`next_cursor` semantics**: Plan called it "opaque". For `/executions` I use the oldest row's `started_at` ISO string (usable as the `before` query param), and for `/events` I use the last `seq` as a string (though the existing `since_seq` param is what clients already use). Both are included only when the returned page is full — otherwise `None`. No dedicated cursor param on events because `since_seq` already exists.

---

## Issues Encountered

None — the Foundation layer did exactly what the plan promised, so routes were a thin wrapper.

---

## Tests Written

| Test File | Test Cases |
|---|---|
| `tests/service/test_executions_routes.py` | `test_health`, `test_list_executions_no_filters`, `test_list_executions_filter_by_project`, `test_list_executions_filter_by_status`, `test_list_executions_filter_by_ticket`, `test_list_executions_limit_clamped_when_client_requests_huge`, `test_list_executions_invalid_before_returns_422`, `test_get_execution_200`, `test_get_execution_404`, `test_events_basic`, `test_events_since_seq_pagination`, `test_events_limit_clamped`, `test_events_404_for_unknown_execution`, `test_agent_results`, `test_agent_results_404_for_unknown_execution`, `test_list_executions_next_cursor_when_page_full` |

---

## Next Steps

- [ ] Review the diff on `experimental/command-center-02-read-api`
- [ ] Push from sentinel-dev / host and open PR (Claude Code sandbox cannot push)
- [ ] Proceed with plan 03 (live event stream) which layers SSE on top of this
