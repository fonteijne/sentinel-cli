# Command Center — Gap Analysis

**Date:** 2026-04-24
**Scope:** Plans 01–06 (foundation → production-exposure), their reports, `bd-residuals.md`, and the code in `src/core/`, `src/service/`, `src/cli.py`, `src/agent_sdk_wrapper.py`.
**Method:** Cross-referenced plan acceptance criteria / validation sections against shipped code; grepped every code-level scope-cut marker (`scaffold`, `not yet`, `later track`, `TODO`); ran event-catalogue audit (declared vs. emitted).

Two gaps (BLOCKER #1, #2) are verified with code reads. The rest are sourced from the cc-plan-reviewer audit.

---

## How to read this doc

Each gap carries:
- **Location** — the file(s)/line(s) where it manifests, or the plan text where it's specified-but-unshipped.
- **Evidence** — a specific grep or quote that proves the gap.
- **Plan ref** — originating plan/report.
- **Severity** — BLOCKER / HIGH / MEDIUM / LOW.
- **Owner** — which `cc-*` subagent should take it.

Severity rubric:
- **BLOCKER** — an HTTP/worker surface is effectively non-functional or ships a silent data inconsistency.
- **HIGH** — a documented acceptance criterion from one of the plans is missing.
- **MEDIUM** — scope cut with a named workaround, or API-level ambiguity.
- **LOW** — polish, nits, cosmetic drift.

---

## BLOCKERs

### G-00. Orchestrator has no `plan()` / `execute()` / `debrief()` verbs — FastAPI route is a no-op

- **Location:** `src/core/execution/worker.py:129-137` (scaffold fallback); `src/core/execution/orchestrator.py:1-14` (module docstring admits the scope cut).
- **Evidence:** `_resolve_method()` returns `None` because Orchestrator exposes only `begin/complete/fail/set_phase/record_agent_result`. Worker falls back to `_scaffold_run()` which only emits lifecycle events and transitions the row to `succeeded` — no agents run. POST `/executions` therefore returns 202 with a "successful" execution that did zero work.
- **Plan ref:** Plan 01 Task 10; `command-center-01-foundation-report.md:24` (scope cut); `04-commands-and-workers-report.md:107` (listed as "Next Steps").
- **Severity:** BLOCKER.
- **Owner:** `cc-orchestrator-expert`.
- **Note:** No numbered plan file exists for the extraction; owner is defined but work is untracked.

### G-01. `register_compose_project` has zero callers — plan 04's container-cleanup chain is dead

- **Location:** `src/core/execution/repository.py:416-451` (defined); no callers anywhere in `src/` or `tests/`.
- **Evidence:** `grep -rn register_compose_project src/ tests/` returns only the definition. Supervisor's `post_mortem` reads `execution.metadata["compose_projects"]` (supervisor.py:393) to run `docker compose down -p <name>`, but the list is always empty because nothing registers a project.
- **Plan ref:** Plan 04 §"Child containers — pre-registration ordering"; acceptance criterion *"Per-ticket appserver-* containers are cleaned up on cancel/failure/orphan (no leaks)"*.
- **Severity:** BLOCKER. The 04-report claims the criterion is met via `post_mortem`-reads-metadata, but the read side never sees populated data.
- **Owner:** `cc-orchestrator-expert` (wire `repo.register_compose_project(...)` before `docker compose up` in the agent flow) + `cc-supervisor-expert` (assertion/log when post_mortem sees an empty list for a non-trivial run).

### G-02. `repo.create()` hard-codes `status=RUNNING` — contradicts plan 04 API contract and breaks Set-C reconciliation

- **Location:** `src/core/execution/repository.py:118, 135` (hard-codes `ExecutionStatus.RUNNING`); `src/core/execution/worker.py:122-123` (`if status == QUEUED: set_status(RUNNING)` — dead code); `tests/core/test_supervisor.py:172` papers over it: `# Coerce status to QUEUED since create() defaults to RUNNING`.
- **Evidence:** Verified via direct read. Row is inserted as `RUNNING`; endpoint returns `RUNNING` with HTTP 202; worker's queued→running transition is unreachable.
- **Plan ref:** Plan 04 endpoints table: *"POST /executions → 202 + ExecutionOut (status queued, worker transitions it to running)"*. Plan 04 §Startup reconciliation Set C: *"orphaned queued rows (service died between repo.create and supervisor.spawn)"* — set cannot populate.
- **Severity:** BLOCKER. Crashes between `create` and `spawn.start` leave a row in `RUNNING` with no worker, which Set A misclassifies as "running without worker row → failed with `orphaned_on_restart`" — wrong error label, wrong recovery path. Dashboards also see `running` immediately even before the worker is up.
- **Owner:** `cc-persistence-expert` (decide: default QUEUED and let the worker transition; or delete QUEUED everywhere and update plan text).

---

## HIGH

### G-03. `cancel_flag` stored on Orchestrator but never `.is_set()`-checked in production paths

- **Location:** `src/core/execution/orchestrator.py:57,66` (stored); only `src/core/execution/worker.py:204` (`_scaffold_run`) reads it.
- **Evidence:** `grep -n cancel_flag src/core/execution/orchestrator.py` — assigned, never read.
- **Plan ref:** Plan 04 §Worker Process Model: *"Cancel flag: threading.Event checked by Orchestrator between agent turns; orchestrator observes and bails cleanly."*
- **Severity:** HIGH. Cancellation today relies entirely on signal escalation (SIGTERM→SIGINT→SIGKILL, 30s total). No cooperative early exit.
- **Owner:** `cc-orchestrator-expert`. Tied to G-00 but distinct — even the thin orchestrator should expose a phase-boundary check.

### G-04. Six declared event types never emitted

- **Location:** `src/core/events/types.py` declares `AgentStarted`, `AgentFinished`, `TestResultRecorded`, `FindingPosted`, `DebriefTurn`, `RevisionRequested`. No instantiations anywhere in `src/` other than re-exports.
- **Evidence:** `grep -rn "AgentStarted\|AgentFinished\|TestResultRecorded\|FindingPosted\|DebriefTurn\|RevisionRequested" src/` returns only type-definition lines.
- **Plan ref:** Plan 01 §"Event Types"; Plan 01 Task 10 GOTCHA (re: DebriefTurn); `bd-residuals.md` item 10 presupposes emission.
- **Severity:** HIGH. `AgentStarted` / `AgentFinished` are the most basic per-agent timeline signals any dashboard will want.
- **Owner:** `cc-event-bus-expert` (catalogue) + `cc-orchestrator-expert` (wire emission in `BaseAgent` and `Orchestrator.set_phase`).

### G-05. `_publish_rate_limited` helper defined but never called

- **Location:** `src/agent_sdk_wrapper.py:244-258` (defined); zero call sites.
- **Evidence:** `grep -n _publish_rate_limited src/agent_sdk_wrapper.py` — only the definition. `execute_with_tools` stream loop has no 429/529 branch.
- **Plan ref:** Plan 01 Task 9: *"On 429/529 / rate-limit exception: publish RateLimited(retry_after_s) before re-raising or backing off."*
- **Severity:** HIGH. Dashboards will never render "Anthropic is throttling." Documented acceptance criterion unmet.
- **Owner:** `cc-orchestrator-expert` (SDK-wrapper owner).

### G-06. No WS connection cap — `get_repo` dep holds threadpool slot for entire connection lifetime

- **Location:** `src/service/routes/stream.py:55-62` (depends on `get_repo` → `get_db_conn` sync generator).
- **Evidence:** Sync generator holds SQLite connection until socket closes. Plan 03 GOTCHA flagged this as "40 simultaneous connections exhaust default threadpool."
- **Plan ref:** Plan 03 Risks (accepted for MVP); `bd-residuals.md` item 4 specifies `service.rate_limits.ws_concurrent_per_token` (default 10).
- **Severity:** HIGH. A misbehaving or leaked client brings the service to its knees.
- **Owner:** `cc-fastapi-expert` (semaphore keyed on `token_prefix` inside `require_token_ws`).

### G-07. Named test `test_entry_dict_jsonl_bus_parity` never written

- **Location:** `tests/test_agent_sdk_wrapper.py` has no `entry_dict` / `parity` test.
- **Evidence:** `grep -n "entry_dict\|parity" tests/test_agent_sdk_wrapper.py` returns nothing.
- **Plan ref:** Plan 01 Task 9 — test is named explicitly.
- **Severity:** HIGH. The test was the guard against silent drift between JSONL diagnostic output and `events.payload_json`.
- **Owner:** `cc-event-bus-expert`.

### G-08. `set_worker_heartbeat` defined but worker bypasses it with raw SQL

- **Location:** `src/core/execution/repository.py:390-400` (defined); `src/core/execution/worker.py:80-85` (runs raw `UPDATE workers SET last_heartbeat_at = ?`).
- **Evidence:** `grep -rn set_worker_heartbeat src/` returns only the definition.
- **Plan ref:** Plan 04 Task 4: *"Worker's heartbeat thread calls this instead of raw SQL; lets tests mock the method."*
- **Severity:** HIGH. Behaviour is correct today; testability is lost.
- **Owner:** `cc-supervisor-expert` / `cc-worker-runtime-expert`.

### G-09. `multiprocessing.Process` env handling is a data race on global `os.environ`

- **Location:** `src/core/execution/supervisor.py:136-146`.
- **Evidence:** 04-report Deviation #4: `multiprocessing.Process` has no `env=` kwarg, so the supervisor does `saved = os.environ.copy(); os.environ.clear(); os.environ.update(env); proc.start(); os.environ = saved`. Global `os.environ` is mutated while the HTTP threadpool may be reading it.
- **Plan ref:** Plan 04 §Worker Process Model (specified `env=` semantics).
- **Severity:** HIGH. Under concurrent starts, env leaks between spawns. Many libs read `os.environ` at call time (httpx proxies, logging TZ, etc.).
- **Owner:** `cc-supervisor-expert`. Options: serialize spawns under `_lock` and document the hold window; or switch to a `subprocess.Popen`-based spawn path.

---

## MEDIUM

### G-10. CLI `--remote --follow` uses only `?token=` — breaks on non-loopback service URLs

- **Location:** `src/cli.py:218-221`.
- **Evidence:** Builds `ws_base` + `?token=`; no `Authorization` header. Plan 05 WS auth accepts `?token=` only from loopback.
- **Plan ref:** Plan 04 Task 7.
- **Severity:** MEDIUM. Works against local service (loopback); fails for a remote `SENTINEL_SERVICE_URL`.
- **Owner:** `cc-cli-integration-expert`.

### G-11. Read endpoints + WS connections uncapped (documented debt)

- **Location:** `src/service/app.py:151-165` — rate-limit dep wraps only `commands.router`.
- **Plan ref:** `bd-residuals.md` item 4.
- **Severity:** MEDIUM (documented; not shipped).
- **Owner:** `cc-fastapi-expert` + `cc-auth-expert`.

### G-12. `retry_of` chain is not consolidated to the root original

- **Location:** `src/service/routes/commands.py:199-239`.
- **Evidence:** A retry-of-a-retry points at its direct parent, not the root.
- **Plan ref:** Plan 04 endpoint spec is silent on this; API-level ambiguity for dashboard consumers.
- **Severity:** MEDIUM. Worth spec-locking before dashboard work.
- **Owner:** `cc-fastapi-expert`.

### G-13. Idempotency lookup silently skipped when `token_prefix` is None (isolation tests)

- **Location:** `src/service/routes/commands.py:144`: `if idempotency_key and token_prefix is not None`.
- **Evidence:** With auth bypassed, the idempotency branch is skipped — two identical POSTs both land as new rows (the unique index is partial: `WHERE idempotency_key IS NOT NULL`, but `(NULL, key)` pairs aren't deduped across requests).
- **Plan ref:** Plan 04 §Idempotency semantics.
- **Severity:** MEDIUM. Footgun under plan-04-isolated tests; safe once plan 05 auth always provides a token_prefix.
- **Owner:** `cc-fastapi-expert`. Either raise 500 when auth has stripped token_prefix, or document the isolation-mode caveat.

### G-14. `follow_up_ticket` option validated + stored but never read

- **Location:** `src/service/routes/commands.py:76` (field); no reads anywhere.
- **Evidence:** `grep -rn follow_up_ticket src/` — only schema/validator.
- **Plan ref:** Plan 04 §Request schemas lists it as an allowed option; semantics unspec'd.
- **Severity:** MEDIUM. API accepts a field nobody consumes — dashboards will infer behaviour that doesn't happen.
- **Owner:** `cc-orchestrator-expert` (decide: remove or wire).

### G-15. `__main__` entrypoint convention — `python -m src.core.execution.worker --help` path

- **Location:** `src/core/execution/worker.py:221`.
- **Evidence:** No `src/core/execution/__main__.py`. The `if __name__ == "__main__"` in `worker.py` does fire for `-m`, but worth smoke-testing.
- **Plan ref:** Plan 04 Task 3 VALIDATE.
- **Severity:** MEDIUM pending verification (likely LOW).
- **Owner:** `cc-worker-runtime-expert`.

### G-16. `latest_event_seq` defined on repository but unused

- **Location:** `src/core/execution/repository.py:361-366`.
- **Evidence:** `grep -rn latest_event_seq src/` — only the definition.
- **Plan ref:** Plan 01 Task 6 repo methods.
- **Severity:** MEDIUM. Minor; WS reconnect could be more efficient.
- **Owner:** `cc-fastapi-expert`.

### G-17. `--remote` round-trip of options (revise / max_turns / follow_up_ticket) has no test

- **Location:** `src/cli.py:167-172` builds `options` via `{k: v for k, v in options.items() if v is not None}`.
- **Evidence:** No integration test asserts the full option set survives client→API→worker.
- **Severity:** MEDIUM (untested boundary).
- **Owner:** `cc-cli-integration-expert` + `cc-test-harness-expert`.

---

## LOW

### G-18. Oversized-event truncation fallback emits an envelope that cannot rehydrate via `AnyEventAdapter`

- **Location:** `src/core/events/bus.py:117-137`.
- **Evidence:** Fallback writes an envelope missing subtype-required fields. Current consumers only `json.loads` the payload, so no crash today.
- **Plan ref:** Plan 01 Task 4.
- **Severity:** LOW.
- **Owner:** `cc-event-bus-expert`.

### G-19. `RateLimited` event type wire string is `rate_limited` (underscore) — breaks dotted-namespace convention

- **Location:** `src/core/events/types.py:158`.
- **Evidence:** All other types use `execution.started`, `phase.changed`, `tool.called`, etc.
- **Plan ref:** Plan 01 Event Types.
- **Severity:** LOW. Forward-compatible to rename *before any real data lands*; after that, forward-only migration applies.
- **Owner:** `cc-event-bus-expert`.

### G-20. `audit_write` fires on pydantic-422 rejects

- **Location:** `src/service/auth.py:258-282`. Dep runs before body validation.
- **Plan ref:** 05-report Risks section.
- **Severity:** LOW. Correctness is intentional ("audit authorised attempts"); ops runbook should document.
- **Owner:** Docs.

### G-21. `Idempotency-Key` lacks charset/length validation

- **Location:** `src/service/routes/commands.py:141`.
- **Plan ref:** `bd-residuals.md` item 3 — specifies `pattern=r"^[A-Za-z0-9._-]{1,128}$"`.
- **Severity:** LOW.
- **Owner:** `cc-fastapi-expert`.

### G-22. `test_worker_logging.py:56` seeds `workers.pid` with the test process PID

- **Location:** `tests/core/test_worker_logging.py:56`.
- **Severity:** LOW. Confusing fixture; not a behaviour bug.
- **Owner:** `cc-test-harness-expert`.

### G-23. `.env.example` / compose default for `SENTINEL_SERVICE_TOKEN` leads to "new token every recreate" in dev

- **Location:** `docker-compose.yml` references `SENTINEL_SERVICE_TOKEN=${SENTINEL_SERVICE_TOKEN:-}`. Empty → auto-create inside container → lost on recreate unless volume-mapped.
- **Plan ref:** Plan 06 Task 5.
- **Severity:** LOW. Operator UX; documented but not in deploy.md troubleshooting.
- **Owner:** Docs.

### G-24. Dev compose now runs `sentinel serve` — silently changes long-standing dev container behaviour

- **Plan ref:** Plan 06 Task 1.
- **Severity:** LOW (documented in plan, not in dev onboarding).
- **Owner:** Docs.

### G-25. Nullable `idempotency_token_prefix` allows duplicate rows in isolation tests

- **Location:** `src/core/persistence/migrations/001_init.sql` partial unique index.
- **Severity:** LOW (same family as G-13).
- **Owner:** `cc-persistence-expert` — decide once G-13 is resolved.

### G-26. Plan 05 text still references `os.rename` for the atomic token write; shipped code uses `os.link`

- **Location:** `.claude/PRPs/plans/completed/05-auth-and-binding.plan.md` §Task 1.
- **Plan ref:** 05-report:140-142.
- **Severity:** LOW (archive cosmetic).
- **Owner:** Docs.

---

## Residuals tracked in `bd-residuals.md` (all 19 unchecked)

These are catalogued in `.claude/PRPs/plans/command-center/bd-residuals.md` and by definition are open gaps. Severity column below is the audit's assessment, not the file's.

| # | Item | Severity | Notes |
|---|---|---|---|
| R-1 | Harden `SENTINEL_DB_PATH` against TOCTOU | LOW | |
| R-2 | WAL-aware backup runbook + `sentinel db backup/restore` | MEDIUM | |
| R-3 | `Idempotency-Key` charset/length validation | LOW | = G-21 |
| R-4 | Rate-limit read endpoints + WS connection ceiling | MEDIUM | = G-6, G-11 |
| R-5 | Document `token_prefix` collision semantics | LOW | |
| R-6 | Schema migration rollback policy docs | LOW | |
| R-7 | Log rotation for `logs/workers/*.log` | MEDIUM | |
| R-8 | Daily / per-token run cap | MEDIUM | Cost control |
| R-9 | PID-reuse safety in `_pid_alive` via `/proc/<pid>/stat` start-time | LOW | |
| R-10 | Flesh out `DebriefTurn` + `RevisionRequested` payloads | LOW | But never emitted — see G-4 |
| R-11 | Reference `FunctionalDebriefAgent` / reviewer subclasses in overview | LOW | Docs |
| R-12 | Plan 06 — dashboard contract (management + metrics endpoints) | FUTURE | |
| R-13 | Plan 06b — Docker Compose integration | PARTIAL | Most shipped with plan 06 |
| R-14 | Retention/sweep policy for `events` + `agent_results` | MEDIUM | |
| R-15 | Forced post-mortem re-run endpoint | LOW | |
| R-16 | SessionTracker → DB subsumption | LOW | |
| R-17 | `CLAUDE_*` env allowlist verification across SDK modes | LOW | |
| R-18 | CORS — reject `http://*` at startup | LOW | |
| R-19 | (File header) None of R-1..R-18 are filed in any tracker | META | `bd` disabled in this worktree |

---

## Summary

| Severity | Count |
|---|---|
| BLOCKER | 3 (G-00, G-01, G-02) |
| HIGH | 7 (G-03 … G-09) |
| MEDIUM | 8 (G-10 … G-17) |
| LOW | 9 (G-18 … G-26) |
| Residuals | 19 (R-1 … R-19) |
| **Total** | **46** |

The three BLOCKERs together describe a consistent picture: **the HTTP command path (POST `/executions`) is end-to-end wired for lifecycle events and status transitions, but the agent-work step in the middle, the compose-cleanup bookkeeping, and the API's own declared status-transition contract are all missing.** G-00 is the single biggest — fixing it likely pulls G-01 and G-03 along with it (the orchestrator extraction is the natural place to register compose projects and to check `cancel_flag` between phases).

## Suggested sequencing

1. **Land the orchestrator extraction (G-00).** This is the keystone. While doing it:
   - Wire `repo.register_compose_project(...)` before every `docker compose up` (closes G-01).
   - Check `cancel_flag.is_set()` at phase boundaries in `Orchestrator.set_phase` (closes G-03).
   - Emit `AgentStarted` / `AgentFinished` in `BaseAgent` entry/exit (partial close of G-04).
   - Emit `DebriefTurn` / `RevisionRequested` in the debrief loop (rest of G-04).
2. **Fix G-02** (status default). Tiny change, but reshapes reconciliation tests — best done alongside G-00 because the worker's queued→running transition becomes live at the same time.
3. **Fix G-05 / G-07** (`_publish_rate_limited` + parity test). Self-contained; lands in the SDK wrapper.
4. **G-09** (supervisor env race). Self-contained in supervisor.py.
5. **G-06 + G-11 + G-21** (rate-limit + header validation). One router-config pass.
6. Remaining MEDIUM/LOW as polish backlog.

## Out of scope for this doc

- Any gap already fixed on a feature branch that hasn't merged — this audit reads the current working tree only.
- Dashboard / frontend gaps (plan 06b / R-12).
- SearXNG / Archon MCP disconnection (infra, not Command Center).
