# Command Center backend — residual issues

Not blockers for implementing plans 01–05. Each is either known debt, a follow-up plan scope marker, or a tightening item best done once code exists.

Ingest with `bd create -f bd-residuals.md` when the beads Dolt server is reachable, or cherry-pick into individual `bd create ...` commands.

All issues are labelled `command-center` plus a specific area tag; all link to the relevant plan file for context.

---

## Harden `SENTINEL_DB_PATH` validation against TOCTOU

**Labels:** command-center, persistence, security, debt
**Priority:** low
**Source:** 01-foundation.plan.md §Task 1 (round-4 review)

**Description:**
`get_db_path()` validates that `SENTINEL_DB_PATH` resolves to a regular file via `stat()`, then `connect()` opens it. A symlink can be swapped between check and open. Single-host single-user today = negligible risk; worth addressing if the service ever runs in a shared host.

**Acceptance criteria:**
- [ ] Replace the `stat()`-then-`connect()` pattern with `open(..., os.O_NOFOLLOW | os.O_RDWR)` (or `O_CREAT` for first-time init) and wrap the resulting fd with `sqlite3.connect` via `f"file:/proc/self/fd/{fd}?mode=rwc"`.
- [ ] Unit test: symlink `SENTINEL_DB_PATH` to a regular file after validation → `connect()` still refuses or succeeds deterministically.

---

## Document WAL-aware backup runbook and add a backup helper

**Labels:** command-center, persistence, ops, docs
**Priority:** medium
**Source:** 00-overview.plan.md §Backup (deferred tooling)

**Description:**
Overview documents the right commands (`sqlite3 .backup`, `PRAGMA wal_checkpoint(TRUNCATE)`) but there is no first-class helper. Operators who `cp sentinel.db backup.db` without the `-wal`/`-shm` files produce a corrupt restore — easy failure mode.

**Acceptance criteria:**
- [ ] `sentinel db backup <path>` Click command runs `PRAGMA wal_checkpoint(TRUNCATE)` then `sqlite3_backup` and writes a single consistent file.
- [ ] `sentinel db restore <path>` validates and atomically swaps in.
- [ ] README / ops runbook references these commands (not raw sqlite3 recipes).

---

## Add charset/length validation to `Idempotency-Key` header

**Labels:** command-center, api, hardening
**Priority:** low
**Source:** 04-commands-and-workers.plan.md §Task 5 (round-4 review, item #3)

**Description:**
`Idempotency-Key` is received as a free-form string and stored in the DB. Plan validates the column is TEXT, but not the header content. A malicious client could send multi-megabyte keys or non-printable bytes. Low impact (the key's sole job is dedup), but the fix is one line.

**Acceptance criteria:**
- [ ] Header type: `Annotated[str | None, Header(..., pattern=r"^[A-Za-z0-9._-]{1,128}$")]`.
- [ ] Test: header `"x" * 1000` → 422.
- [ ] Test: header with newline → 422.

---

## Rate-limit read endpoints + WebSocket connection ceiling

**Labels:** command-center, api, dos-resistance, debt
**Priority:** medium
**Source:** 05-auth-and-binding.plan.md §Task 1b (explicit "reads exempt by design")

**Description:**
Current design: writes rate-limited per-token (3 concurrent / 30 per minute). Reads and WS unlimited. A compromised token can still flood reads or open hundreds of WS connections (each holds a threadpool thread). For MVP single-dashboard use this is fine; when the dashboard becomes multi-tab or multi-user it isn't.

**Acceptance criteria:**
- [ ] Configurable `service.rate_limits.read_per_minute` (default 600) + `service.rate_limits.ws_concurrent_per_token` (default 10).
- [ ] Enforced in the read-router dep and WS connect dep.
- [ ] Tests covering 429 on reads, 1013 on WS connect past the cap.

---

## Document `token_prefix` (sha256[:8]) collision semantics

**Labels:** command-center, security, docs
**Priority:** low
**Source:** 05-auth-and-binding.plan.md §Task 4 (round-4 review)

**Description:**
Audit log keys off `sha256(token)[:8]` — 32 bits. Fine for <10⁶ tokens at single-tenant scale, but nowhere do the plans say "audit records are tagged by a short hash; under token rotation, two tokens may collide in audit correlation but not in auth." Add one paragraph to the security notes.

**Acceptance criteria:**
- [ ] Paragraph added to `docs/security.md` (new) or 05-auth-and-binding.plan.md §Notes.
- [ ] States collision domain, rotation behaviour, and that full hash is available on request via `sqlite3` query over audit logs (future: audit_log table).

---

## Schema migration rollback policy (document forward-only decision)

**Labels:** command-center, persistence, debt, docs
**Priority:** low
**Source:** 00-overview.plan.md (explicitly forward-only)

**Description:**
Forward-only migrations are the deliberate choice, but no document captures *why* or what to do if a migration corrupts data. Capture the policy and the recovery playbook (restore from backup; cherry-pick from git).

**Acceptance criteria:**
- [ ] `docs/migrations.md` exists, covers policy + emergency restore.
- [ ] README of `src/core/persistence/migrations/` links to it.

---

## Log rotation for `logs/workers/*.log`

**Labels:** command-center, ops, debt
**Priority:** medium
**Source:** 04-commands-and-workers.plan.md §Risks (acknowledged as debt)

**Description:**
Workers stream stdout/stderr to `logs/workers/<execution_id>.log`. No rotation, no cap. After enough runs the `logs/` directory fills the disk. Either compress on execution-complete, or cap size per run + rotate-count.

**Acceptance criteria:**
- [ ] On execution-complete, `reap()` (or a separate hook) gzip-compresses the log.
- [ ] Retention: compressed logs older than 30 days are deleted by a periodic sweep.
- [ ] Config keys: `service.worker_logs.compress_on_complete` (bool) + `service.worker_logs.retain_days` (int).

---

## Daily / per-token run cap (Anthropic spend ceiling)

**Labels:** command-center, security, cost-control, debt
**Priority:** medium
**Source:** 05-auth-and-binding.plan.md §Rate limits (30/min × 24h = 43k/day theoretical max)

**Description:**
Rate limit today prevents a burst but allows sustained 30/min indefinitely = ~43k runs/day per token = real Anthropic cost risk. Add a daily cap keyed by `token_prefix` (in-memory sliding window, not persistent).

**Acceptance criteria:**
- [ ] `service.rate_limits.max_per_day` config key (default 500).
- [ ] Enforced in `TokenRateLimiter.check_and_reserve`.
- [ ] 429 response includes `X-RateLimit-Daily-Remaining` header.
- [ ] Test: 501st run in a day → 429.

---

## PID reuse safety in `_pid_alive`

**Labels:** command-center, reliability, debt
**Priority:** low
**Source:** 04-commands-and-workers.plan.md §Startup reconciliation (round-4 review)

**Description:**
`_pid_alive(pid)` uses `os.kill(pid, 0)`. On long-running hosts the kernel can recycle PIDs; a killed worker's PID can be reassigned to an unrelated process, and reconciliation would incorrectly "adopt" it. Low probability on short restarts; worth hardening when the service becomes a long-running daemon.

**Acceptance criteria:**
- [ ] Extend `workers` table with `started_at_epoch INTEGER` (nanoseconds).
- [ ] `_pid_alive` compares `/proc/<pid>/stat` starttime (column 22) against `workers.started_at_epoch` with ±1s tolerance.
- [ ] Test: simulate PID reuse via monkey-patch; assert recycled PID is treated as dead.

---

## Flesh out `DebriefTurn` and `RevisionRequested` event payloads

**Labels:** command-center, events, dashboard
**Priority:** low
**Source:** 01-foundation.plan.md §Event Types (round-4 review)

**Description:**
Both events are in the catalogue with minimal fields. Payloads are thin compared to what a dashboard timeline would want:
- `DebriefTurn`: currently `{turn_index, prompt_chars, response_chars}`. Dashboard would benefit from `question_summary` (first N chars of the agent's question) for timeline rendering.
- `RevisionRequested`: currently `{revise_of_execution_id, reason}`. Add `source_mr_url`, `source_comment_id` for deep linking.

**Acceptance criteria:**
- [ ] Payload fields expanded; pydantic models updated.
- [ ] `bus.publish` consumers (orchestrator, CLI log adapter) emit the new fields.
- [ ] Size-cap test: oversized `question_summary` truncates correctly (inherits plan 01 truncation behavior).

---

## Reference `FunctionalDebriefAgent` + reviewer subclasses in overview

**Labels:** command-center, docs
**Priority:** low
**Source:** 00-overview.plan.md (round-4 gap review)

**Description:**
Overview's "NOT in scope" list mentions generic agent plumbing. Add one explicit line: "All `BaseAgent` subclasses (`FunctionalDebriefAgent`, `DrupalReviewerAgent`, etc.) inherit event emission without code changes — no per-agent entries are required."

**Acceptance criteria:**
- [ ] Line added under "Shared Architectural Decisions" in `00-overview.plan.md`.

---

## Plan 06 — Dashboard contract: management + metrics endpoints

**Labels:** command-center, plan-06, scope-marker
**Priority:** low (future plan)
**Source:** 00-overview.plan.md (explicitly deferred)

**Description:**
Follow-up plan combining:
- Aggregate metrics endpoints (`GET /metrics/executions-per-day`, `/metrics/success-rate`, `/metrics/avg-cost`).
- Worker-log GET (`GET /executions/{id}/worker-log?range=bytes`) with tail + pagination.
- Management endpoints (`/status`, `/projects`, `/validate`, `/reset`) — bring CLI-only commands into the API surface.
- `GET /running-now` summary view for the dashboard home screen.

**Acceptance criteria:**
- [ ] Plan file written at `.claude/PRPs/plans/command-center/06-management-and-metrics.plan.md`.
- [ ] Depends on 05 (auth).
- [ ] Scope each endpoint with response shape + rate-limit class (reads, not writes).

---

## Plan 06b — Docker Compose integration

**Labels:** command-center, plan-06, compose, scope-marker
**Priority:** low (future plan)
**Source:** 00-overview.plan.md (explicitly deferred)

**Description:**
Wire the service into the project's compose topology:
- Expose 8787 on `sentinel-dev` service (compose override or production config).
- `healthcheck:` using `/health`.
- Publish `SENTINEL_SERVICE_URL=http://sentinel-dev:8787` to `appserver-*` per-ticket stacks.
- Document in `docker-compose.yml` comments + `docs/topology.md`.

**Acceptance criteria:**
- [ ] `docker-compose.yml` changes shown in plan file.
- [ ] Healthcheck reports unhealthy when `/health` returns non-200.
- [ ] `appserver` containers can `curl $SENTINEL_SERVICE_URL/executions` (if we ever want project-side introspection).

---

## Retention / sweep policy for `events` + `agent_results`

**Labels:** command-center, persistence, debt
**Priority:** medium
**Source:** 00-overview.plan.md (explicitly deferred)

**Description:**
`idx_events_ts` is in place (plan 01) but there's no sweep. After months of use the `events` table grows without bound. Disk fills slowly; query latency creeps up on cross-execution filters.

**Acceptance criteria:**
- [ ] `sentinel db sweep` Click command: deletes `events` rows older than `service.retention.events_days` (default 90) for executions in terminal status.
- [ ] `agent_results` sweep uses same policy.
- [ ] `executions` rows themselves are retained forever (narrow table; low cost).
- [ ] Cron note in ops runbook: "run weekly via `sentinel db sweep`."

---

## Forced post-mortem re-run endpoint

**Labels:** command-center, api, ops, debt
**Priority:** low
**Source:** 00-overview.plan.md §Explicitly NOT (documented as manual sqlite3)

**Description:**
Operators who need to re-clean a terminal row today run raw SQL to clear the `post_mortem_complete` flag and restart the service. An explicit endpoint would be kinder: `POST /executions/{id}/recleanup`. Low-priority ops convenience.

**Acceptance criteria:**
- [ ] Endpoint exists; requires audit-tagged bearer auth.
- [ ] Clears `metadata_json.post_mortem_complete` and enqueues the execution for the reconciliation sweep.
- [ ] Test: seed a post-mortem-complete row; POST; assert flag cleared and reconciliation re-fires.

---

## Optional later: SessionTracker → DB subsumption

**Labels:** command-center, persistence, debt
**Priority:** low
**Source:** 00-overview.plan.md + 01-foundation.plan.md (explicitly deferred; coexistence only)

**Description:**
`~/.sentinel/sessions.json` still owns the Claude SDK session-id store. It coexists fine, but keeping two state stores is a smell. When a future plan touches session tracking, subsume into a `sessions` table with `(session_id, execution_id FK, project)`.

**Acceptance criteria:**
- [ ] Migration 003 adds `sessions` table.
- [ ] `SessionTracker` reads/writes via `ExecutionRepository`.
- [ ] One-shot import of existing `~/.sentinel/sessions.json` on first open.
- [ ] Legacy file renamed `sessions.json.migrated` (non-destructive).

---

## `CLAUDE_*` env allowlist behaviour across SDK modes

**Labels:** command-center, subprocess, verification
**Priority:** low
**Source:** 04-commands-and-workers.plan.md §Env allowlist (added in round 4)

**Description:**
Allowlist now includes `CLAUDE_*` prefix so subscription-mode auth cache works. But no test proves a subscription-mode worker actually authenticates end-to-end. Add a fixture-based test (or at least a manual smoke-test script) that exercises the subscription path in a worker subprocess.

**Acceptance criteria:**
- [ ] `tests/core/test_worker_env.py::test_subscription_mode_env_propagates` — spawns worker with stubbed `CLAUDE_CODE_OAUTH_TOKEN` (or whatever the cache key is), asserts child reads it.
- [ ] Smoke-test script in `scripts/smoke_subscription_worker.sh` documented in CONTRIBUTING.

---

## CORS origin glob-literal confusion

**Labels:** command-center, security, docs
**Priority:** low
**Source:** 05-auth-and-binding.plan.md §CORS (round-3 review)

**Description:**
`_validate_cors(["*"])` rejects the wildcard, but operators may configure `["http://*"]` or `["https://*.example.com"]` expecting glob behaviour. Starlette's `CORSMiddleware` treats these as literal strings (inert). Document that only exact `scheme://host[:port]` literals are accepted.

**Acceptance criteria:**
- [ ] Validation extended to reject any origin containing `*` with a clear error message.
- [ ] `config/config.yaml` comment updated.
- [ ] Test: `["http://*"]` → startup raises.
