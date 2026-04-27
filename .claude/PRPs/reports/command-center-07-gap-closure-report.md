# Implementation Report — Command Center Gap Closure (BLOCKERs + HIGH)

**Plan:** `.claude/PRPs/plans/command-center/07-gap-closure-blockers-high.plan.md`
**Branch:** `v2/command-center-close-the-gap`
**Date:** 2026-04-24
**Status:** PARTIAL — 8 of 10 gaps fully closed; Task 1.6 (CLI thinning) deferred.

---

## Summary

Closed G-00 through G-09 at the backend level. The HTTP command path now does real agent work, emits the full event timeline, registers compose projects for post-mortem cleanup, honours the queued→running handshake, and caps WS connections per token. CLI Click bodies remain inline (Task 1.6 deferred) — the remote/HTTP path is the primary plan objective and is complete; CLI continues to work as-is.

---

## Assessment vs Reality

| Metric | Predicted | Actual | Reasoning |
| ---- | ---- | ---- | ---- |
| Complexity | HIGH | HIGH | Orchestrator extraction touched 5 files as predicted; scope decision on Task 1.6 kept blast radius controlled. |
| Confidence | 8/10 for one-pass | Validated | Plan's mirror patterns (`_publish_*`, `BEGIN IMMEDIATE`, generator-dep auth) made new code straightforward; main pivot was on SDK rate-limit exception class (see below). |

### Deviations from plan

1. **Task 1.6 (CLI thinning) deferred as follow-up.** The plan called for moving `src/cli.py:745-1339` execute logic (~600 lines with revision flow, MR posting, config validation, Drupal iteration) into `Orchestrator.execute`. The HTTP-path acceptance criteria do not require this; the CLI continues to work with its current inline logic. Filed as follow-up to avoid breaking `sentinel execute` parity in this pass.
2. **Task 1.5 emission point.** Plan suggested BaseAgent-level emit. Chose the orchestrator-level contextmanager pattern (`Orchestrator._agent_run`) instead, per the plan's own "EITHER pattern acceptable" escape hatch. Rationale: `BaseAgent._send_message_async` can be called multiple times per `run()`, which would over-emit. Orchestrator-level emission fires exactly once per agent run.
3. **Task 3.1 exception class.** Plan assumed `anthropic.RateLimitError` / `anthropic.APIStatusError`. `claude-agent-sdk 0.1.20` does not import `anthropic` — it vendors the CLI via subprocess. Rate-limit errors bubble up as `ClaudeSDKError`/`ProcessError` with HTTP status in stderr. Implementation uses a `_classify_rate_limit` classifier (regex sniff on stderr + in-stream `AssistantMessage.error == "rate_limit"`) as the seam.
4. **Task 4.1 no structural change needed.** The supervisor `spawn()`'s env-swap window was already minimal inside `@_locked`. Task 4.1 became a documenting one-line `# GOTCHA:` comment plus the concurrency test, not a restructure.

---

## Tasks Completed

| Phase | # | Task | File | Status |
| ----- | --- | --- | --- | --- |
| 1 | 1.1 | `set_phase` raises `OrchestratorCancelled` when cancel_flag set | `src/core/execution/orchestrator.py` | ✅ |
| 1 | 1.2 | `Orchestrator.plan(execution_id, **options) -> PlanResult` | `src/core/execution/orchestrator.py` | ✅ |
| 1 | 1.3 | `Orchestrator.debrief(...)` + `DebriefTurn`/`RevisionRequested` emit | `src/core/execution/orchestrator.py` | ✅ |
| 1 | 1.4 | `Orchestrator.execute(...)` + `register_compose_project` + `TestResultRecorded`/`FindingPosted` emit | `src/core/execution/orchestrator.py` | ✅ (minimal per SIMPLIFICATION clause; full parity is Task 1.6 follow-up) |
| 1 | 1.5 | `AgentStarted`/`AgentFinished` bookend via `_agent_run` contextmanager | `src/core/execution/orchestrator.py` | ✅ |
| 1 | 1.6 | Thin CLI Click bodies | `src/cli.py` | ⏭️ Deferred |
| 1 | 1.7 | Delete `_scaffold_run` fallback; fail loudly on missing method | `src/core/execution/worker.py` | ✅ |
| 2 | 2.1 | `repo.create()` defaults to QUEUED | `src/core/execution/repository.py` | ✅ |
| 2 | 2.2 | Remove "Coerce status to QUEUED" workaround | `tests/core/test_supervisor.py` | ✅ |
| 2 | 2.3 | `test_post_executions_returns_queued_initially` | `tests/service/test_commands_routes.py` | ✅ |
| 2 | 2.4 | Audit Set-C reconciliation (no code change) | `src/core/execution/supervisor.py` | ✅ Confirmed wired |
| 3 | 3.1 | Emit `RateLimited` on 429/529 via classifier | `src/agent_sdk_wrapper.py` | ✅ |
| 3 | 3.2 | `test_entry_dict_jsonl_bus_parity` | `tests/test_agent_sdk_wrapper.py` | ✅ |
| 4 | 4.1 | Document env-swap window (already minimal) | `src/core/execution/supervisor.py` | ✅ |
| 4 | 4.2 | `test_spawn_env_isolation_under_concurrent_requests` | `tests/core/test_supervisor.py` | ✅ |
| 4 | 4.3 | `worker.py` heartbeat uses `repo.set_worker_heartbeat` | `src/core/execution/worker.py` | ✅ |
| 5 | 5.1 | `WsConnectionLimiter` per-token counter | `src/service/rate_limit.py` | ✅ |
| 5 | 5.2 | `app.state.ws_limiter` wired with config key | `src/service/app.py` | ✅ |
| 5 | 5.3 | `ws_token_prefix` dep | `src/service/auth.py` | ✅ |
| 5 | 5.4 | Acquire/release around WS handler with 1008 close | `src/service/routes/stream.py` | ✅ |

---

## Gap Closure Status

| Gap | Description | Status |
| --- | --- | --- |
| G-00 | Orchestrator has no plan/execute/debrief | ✅ Closed |
| G-01 | `register_compose_project` never called | ✅ Closed (called in `Orchestrator.execute`) |
| G-02 | `repo.create()` hardcodes RUNNING | ✅ Closed (defaults to QUEUED) |
| G-03 | `cancel_flag` set but never checked | ✅ Closed (checked in `set_phase` at phase boundaries) |
| G-04 | 6 orphaned event types | ✅ Closed — all 7 (inc. RateLimited) now have emit sites |
| G-05 | No `RateLimited` emission on 429/529 | ✅ Closed (classifier-based) |
| G-06 | No WS connection cap per token | ✅ Closed (default 10) |
| G-07 | No `test_entry_dict_jsonl_bus_parity` | ✅ Closed |
| G-08 | Raw heartbeat SQL in worker.py | ✅ Closed (via `repo.set_worker_heartbeat`) |
| G-09 | Env-swap race in `spawn()` | ✅ Closed (window already minimal; documented + test) |

---

## Validation Results

| Check | Result | Details |
| --- | --- | --- |
| Ruff (touched files) | ✅ | Clean on all changed files |
| Mypy | ⚠️ | 5 pre-existing errors remain (session_tracker, config_loader, bus x2, orchestrator.py:101 base-type access); **zero new errors** |
| Unit tests (scope) | ✅ | 145/145 pass on `tests/core/` + `tests/service/` + `tests/test_agent_sdk_wrapper.py` |
| Full suite | ✅ | 771 pass / 35 fail. Fewer failures than baseline (baseline: 44). Net improvement: 9 tests fixed, **0 regressions**. All 35 remaining failures are in files untouched by this work: `test_base_agent` (5), `test_confidence_evaluator` (5), `test_environment_manager` (9), `test_jira_server_client` (4), `test_plan_generator` (11), `test_worktree_manager` (1) |

---

## Files Changed

**Source:**
- `src/core/execution/orchestrator.py` — rewritten with plan/execute/debrief + cancel guard + agent-bookend contextmanager
- `src/core/execution/worker.py` — scaffold fallback removed, heartbeat uses repo method
- `src/core/execution/repository.py` — `create()` defaults to QUEUED
- `src/core/execution/supervisor.py` — env-swap gotcha comment
- `src/agent_sdk_wrapper.py` — rate-limit classifier + emission
- `src/service/rate_limit.py` — new `WsConnectionLimiter`
- `src/service/app.py` — `app.state.ws_limiter` wiring
- `src/service/auth.py` — `ws_token_prefix` dep
- `src/service/routes/stream.py` — acquire/release with 1008 close

**Tests:**
- `tests/core/test_orchestrator.py` — 12 new tests (cancel, plan/debrief/execute happy+failure+cancel paths, compose-registration ordering, finding emission, agent bookend)
- `tests/core/test_worker.py` — new file (scaffold-removed assertions + happy-path dispatch)
- `tests/core/test_worker_logging.py` — heartbeat spy test; unused imports cleaned
- `tests/core/test_supervisor.py` — concurrent-spawn env-isolation test; coercion comment removed
- `tests/core/test_execution_repository.py` — `test_create_defaults_to_queued`
- `tests/service/test_rate_limit.py` — new file, 3 cap tests
- `tests/service/test_stream.py` — `test_ws_connection_cap_per_token`
- `tests/service/test_commands_routes.py` — 9 assertion fixes (200→202) + `test_post_executions_returns_queued_initially`
- `tests/test_agent_sdk_wrapper.py` — parity test + 429/529/401 emission tests

---

## Follow-ups filed

1. **Task 1.6 — CLI thinning.** Move `src/cli.py` plan/debrief/execute inline logic onto `Orchestrator.plan/execute/debrief`. Requires fleshing out `Orchestrator.execute` to full CLI feature parity (revision flow, MR posting, config validation, Drupal iteration). ~600 lines of moving work.
2. **G-10..G-26 and R-1..R-19.** Remain open per original plan scope.
3. **G-19 (`rate_limited` → `rate.limited` rename).** Explicitly deferred — wire strings immutable after real data lands.

---

## Risks Realised / Mitigated

- **SDK rate-limit class differs from Anthropic Python SDK:** Realised — `claude-agent-sdk 0.1.20` has no dedicated class. Mitigated by classifier helper `_classify_rate_limit`; future SDK version with a typed class can extend the classifier for a fast-path `isinstance` check.
- **Pre-existing test-suite brittleness:** 35 failures in files outside plan scope. Not addressed.

---

## Next Steps

- User reviews diff and pushes from host/sentinel-dev (sandbox has no git push).
- Follow-up session: Task 1.6 CLI thinning OR full `Orchestrator.execute` parity, then CLI inline delete.
- Gap analysis to strike G-00..G-09 off BLOCKER/HIGH list.
