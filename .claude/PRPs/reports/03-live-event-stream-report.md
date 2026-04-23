# Implementation Report

**Plan**: `.claude/PRPs/plans/command-center/03-live-event-stream.plan.md`
**Branch**: `experimental/command-center-03-event-stream`
**Date**: 2026-04-23
**Status**: COMPLETE

---

## Summary

Implemented the Command Center live event stream: a WebSocket endpoint
`GET /executions/{id}/stream` that tails the `events` table via short-poll
DB reads and forwards rows as JSON frames. DB-polling (not bus subscription)
is the load-bearing design choice that lets plan 04 subprocess workers
become visible through the same route without code changes.

Bumped `sentinel` version to `0.3.3` per the user's request.

---

## Assessment vs Reality

| Metric     | Predicted (plan) | Actual | Reasoning |
|------------|-----------------:|-------:|-----------|
| Complexity | MEDIUM           | MEDIUM | Matched. The poll loop is ~50 LOC; most effort went into the 10-case test matrix. |
| Tasks      | 4                | 4      | As estimated. |

Deviation: The plan listed `src/service/app.py` under "Not touched here",
but the plan-04 tests need the WebSocket route wired somewhere or they
cannot connect. Added one `app.include_router(stream.router)` line with
a comment flagging it as a tactical wire-up that plan 05 will replace
with the auth-wrapped composed factory. Plan reviewer approved this as
acceptable.

---

## Tasks Completed

| # | Task | File | Status |
|---|------|------|--------|
| 1 | Bump version to 0.3.3 | `pyproject.toml` | Done |
| 2 | CREATE WebSocket route | `src/service/routes/stream.py` | Done |
| 3 | UPDATE routes package re-export | `src/service/routes/__init__.py` | Done |
| 4 | Tactical wire-up into app factory | `src/service/app.py` | Done |
| 5 | CREATE test file (10 cases) | `tests/service/test_stream.py` | Done |

---

## Validation Results

| Check | Result | Details |
|-------|--------|---------|
| `pytest tests/service/test_stream.py -v` | 10/10 | All scenarios from plan 03 Task 4 |
| `pytest tests/core tests/service -v` | 49/49 | CC-scoped suite; no plan 01/02 regressions |
| `ruff check src/service tests/service` | Clean | 0 errors |
| `mypy src/service/routes/stream.py` | Clean | 0 errors in new code (pre-existing errors in unrelated files unchanged) |
| `python -c "from src.service.routes import stream"` | Pass | Task 2 checkpoint |
| `python -c "from src.core.events.types import TERMINAL_EVENT_TYPES; ..."` | Pass | Task 3 checkpoint |

One pre-existing test failure in `tests/test_base_agent.py` is unrelated
(a mock signature mismatch in legacy code that already failed on a clean
checkout of the plan 02 merge). Not introduced by plan 03.

---

## Files Changed

| File | Action | Lines |
|------|--------|------:|
| `src/service/routes/stream.py` | CREATE | +114 |
| `tests/service/test_stream.py` | CREATE | +278 |
| `src/service/routes/__init__.py` | UPDATE | +3 / −0 |
| `src/service/app.py` | UPDATE | +4 / −1 |
| `pyproject.toml` | UPDATE | +1 / −1 |

Commit: `feat(service): Command Center live event stream (plan 03)` on
`experimental/command-center-03-event-stream`.

---

## Acceptance Criteria

- [x] Events delivered in strict `seq` order, no gaps or duplicates
- [x] `since_seq` cursor resumes cleanly from any point
- [x] Works for in-process AND subprocess-worker executions (load-bearing test: `test_live_tail_from_cross_process_writer`)
- [x] Heartbeat frame every ~30s of silence
- [x] Socket closes with `{"kind":"end",...}` on terminal event
- [x] Client disconnect exits the coroutine cleanly (verified by reconnecting on the same execution after disconnect)
- [x] No change to Foundation tests

---

## Key Design Points

- **`_END_STATUS` explicit dict**: maps `execution.completed → succeeded`,
  not a `type.split(".")[-1]` derivation. Test `test_terminal_mapping_completed_uses_succeeded` guards against regression.
- **Backpressure**: `asyncio.wait_for(ws.send_json, SEND_TIMEOUT_S=30)`
  turns a slow/silent client into a clean 1011 close instead of a stuck
  threadpool slot.
- **Stale-peer detection**: a periodic heartbeat send is what forces
  `WebSocketDisconnect` when the browser tab was closed without TCP RST.
- **No bus subscription**: persisted DB rows are the only source. This is
  the whole point — plan 04's subprocess workers own their own bus and
  are invisible to the service process.

---

## Residuals / Follow-ups (documented gotchas, not bugs)

- Connection cap per execution (doc'd GOTCHA; plan 05 if multi-tab dashboards land).
- Threadpool exhaustion at >40 concurrent WS connections (doc'd GOTCHA; MVP-acceptable).
- Plan 05 will replace `create_app()` with the composed, auth-wrapped factory.

---

## Next Steps

1. User pushes `experimental/command-center-03-event-stream` from host or sentinel-dev.
2. Open PR per CLAUDE.md "Landing the Plane" workflow.
3. Proceed to plan 04 (subprocess workers / commands) — the DB-polling
   design here unblocks that plan without a WS rewrite.
