# Command Center Backend — Plan Index

This is an index, not an executable plan. It captures shared architecture and sequencing across the five tracks that together produce a fully functional backend for a future Sentinel Command Center dashboard.

The dashboard/UI is **explicitly out of scope** for every plan in this directory.

---

## Tracks

| # | Plan | Scope | Depends on |
|---|------|-------|------------|
| 01 | [`01-foundation.plan.md`](01-foundation.plan.md) | `Execution` entity, SQLite persistence, structured event bus, CLI-as-client | — |
| 02 | [`02-http-read-api.plan.md`](02-http-read-api.plan.md) | FastAPI read-only endpoints (executions, events, agent results) | 01 |
| 03 | [`03-live-event-stream.plan.md`](03-live-event-stream.plan.md) | WebSocket DB-tail for cross-process event streaming | 01, 02 |
| 04 | [`04-commands-and-workers.plan.md`](04-commands-and-workers.plan.md) | Start/cancel/retry endpoints, out-of-process worker supervisor, heartbeat-based crash recovery, container cleanup | 01, 02 |
| 05 | [`05-auth-and-binding.plan.md`](05-auth-and-binding.plan.md) | Bearer-token auth + rate limits, network binding, CORS, audit logging. **Owns final `create_app()` composition.** | 02, 03, 04 |

```
          ┌──────────────┐
          │ 01 Foundation│
          └──────┬───────┘
        ┌────────┼───────────┐
        ▼        ▼           ▼
  ┌──────────┐ ┌──────────┐ ┌──────────┐
  │ 02 Read  │ │ 03 Stream│ │ 04 Cmd/W │
  └──────┬───┘ └────┬─────┘ └────┬─────┘
         └──────────┼────────────┘
                    ▼
             ┌──────────────┐
             │ 05 Auth/Bind │
             └──────────────┘
```

Tracks 02, 03, 04 can be worked in parallel worktrees once 01 is merged. 05 is the finishing seal — do not ship the service to anything but a trusted dev machine until 05 lands.

---

## Shared Architectural Decisions

Locked across all five plans so the tracks integrate cleanly.

| Decision | Choice | Rationale |
|---|---|---|
| Persistence | **SQLite** at `~/.sentinel/sentinel.db` (override via `SENTINEL_DB_PATH`), stdlib `sqlite3` | No new deps; schema-versioned so Postgres/Dolt remains a future swap |
| DB connection model | **Connection-per-caller via `connect()` factory**. No module-level singleton. WAL, `check_same_thread=False`, `timeout=30`, `PRAGMA busy_timeout=30000`. Writers use `BEGIN IMMEDIATE`. | Sharing a sqlite3 connection across threads/processes corrupts state; the FastAPI threadpool alone forces per-request connections |
| Migrations | Plain `.sql` files numbered `NNN_name.sql`, applied in order, tracked in `schema_migrations` | Boring; auditable; no Alembic overhead |
| Event bus | In-process pub/sub; every published event is persisted to `events` table BEFORE subscribers fire. Subscriber dispatch outside the lock. | Persistence is the source of truth; subscribers are best-effort local consumers |
| Cross-process streaming | **WebSocket reads from DB (polling), not from bus.** Subprocess workers (plan 04) have their own bus instance, invisible to the service. DB is the only cross-process truth. | Single code path for in-process and out-of-process executions; no replay/live race |
| HTTP framework | **FastAPI + uvicorn, single-process** (`uvicorn.run(create_app(), ...)`) | Supervisor state + SQLite connections are per-process by design |
| Lifespan | `@asynccontextmanager` lifespan (not deprecated `@app.on_event`) | Modern FastAPI idiom |
| Worker model | One subprocess per execution via `multiprocessing.get_context("spawn")` | `spawn` avoids fork-with-uvicorn hazards; DooD still works (inherits Docker socket env) |
| Worker identity | `workers` table with PID + `last_heartbeat_at`; adopt live workers on service restart | Preserves "runs survive service restart"; dead rows reconciled |
| Cancellation | SIGTERM → 20s → SIGINT → 10s → SIGKILL; **post-mortem cleanup always runs** (`docker compose down` for per-ticket stacks) | Prevents container leaks; best-effort mid-turn cancel is documented |
| Auth | Bearer token (shared secret, atomic `O_CREAT\|O_EXCL` creation, 0o600); per-token rate limits (3 concurrent, 30/min default) | Single-user today; multi-user is a later plan |
| Time | `datetime.now(timezone.utc)` — **never** naive `utcnow()` (deprecated in 3.12) | tz-aware across the system |
| Python | 3.11+ (matches existing constraint) | — |
| Config | Extend existing `ConfigLoader` (config_loader.py); new keys: `service.{bind_address,port,cors_origins,rate_limits.*}` | No new config system |
| Logging | `logger = logging.getLogger(__name__)` in every module. Worker re-initializes via `configure_logging()` since `spawn` doesn't inherit `basicConfig`. | Event bus is orthogonal to logs; both coexist |

---

## Target Module Layout

```
src/
├── core/                        # [01] new package
│   ├── persistence/
│   │   ├── db.py                # connect() factory + ensure_initialized()
│   │   └── migrations/
│   │       ├── 001_init.sql     # [01] executions, events, agent_results, schema_migrations
│   │       └── 002_workers.sql  # [04] workers heartbeat table
│   ├── events/
│   │   ├── bus.py               # persist-then-publish, subscriber dispatch outside lock
│   │   └── types.py             # tz-aware events, TERMINAL_EVENT_TYPES, ExecutionKind/Status
│   └── execution/
│       ├── models.py            # Execution, ExecutionStatus, ExecutionKind
│       ├── repository.py        # CRUD, iter_events, find_by_idempotency_key, list_agent_results
│       ├── orchestrator.py      # plan/execute/debrief flows
│       ├── worker.py            # [04] spawn entrypoint, heartbeat, signal handlers, cleanup
│       └── supervisor.py        # [04] spawn/cancel/reap/adopt_or_reconcile_on_startup
├── service/                     # [02] new package
│   ├── app.py                   # [05] owns final create_app() composition
│   ├── schemas.py               # pydantic response/request models, extra="forbid" on writes
│   ├── deps.py                  # get_db_conn (per-request), get_repo, get_supervisor, lifespan
│   ├── auth.py                  # [05] bearer token (atomic file), require_token, require_token_ws
│   ├── rate_limit.py            # [05] per-token concurrent + per-minute limits
│   └── routes/
│       ├── executions.py        # [02] GET
│       ├── stream.py            # [03] WS DB-poll tail
│       └── commands.py          # [04] POST start/cancel/retry
├── utils/
│   └── logging_config.py        # [04] configure_logging() — shared by CLI, service, worker
├── agents/                      # existing — [01] adds event emission
├── cli.py                       # existing — [01] refactors plan/execute/debrief; [02] adds `serve`; [04] adds --remote
└── …
```

---

## Branch Strategy

Per session rule: any commits go on `experimental/<topic>` branches.

Suggested branch names:
- `experimental/command-center-01-foundation`
- `experimental/command-center-02-read-api`
- `experimental/command-center-03-event-stream`
- `experimental/command-center-04-commands-workers`
- `experimental/command-center-05-auth`

---

## Explicitly NOT in any of these plans

- Dashboard UI (HTML/React/anything rendered)
- Prometheus/OpenTelemetry metrics export
- Aggregate metrics endpoint (executions/day, success rate, avg cost)
- Management endpoints (`/status`, `/projects`, `/validate`, `/reset`) — CLI-only for now
- Multi-tenant / multi-user authorization model (token is single-shared-secret in 05)
- Beads ↔ execution linkage (maintainers correlate manually via `ticket_id`)
- Replacing `~/.sentinel/sessions.json` as the Claude SDK session-id store — new DB supplements it
- Changing how agents are defined or how they talk to claude-agent-sdk — only how they *emit progress*
- Docker Compose integration (port expose, healthcheck, `SENTINEL_SERVICE_URL` to appserver) — follow-up plan 06
- Worker-log endpoint (GET `logs/workers/<id>.log`) — follow-up
- Retention / archival of `events` and `agent_results` — `idx_events_ts` is in place; sweep is follow-up
- Schema migration rollback — forward-only today
- Log rotation for `logs/workers/*.log` — ops concern

---

## Known operating caveats

These are not bugs; they are deliberate scope decisions.

- **`$HOME` ambiguity across containers.** `~/.sentinel/sentinel.db` resolves to the container's HOME inside `sentinel-dev` and to the host HOME outside. Running `sentinel` in both places creates two independent DBs. Set `SENTINEL_DB_PATH` in both environments to a shared path if sharing is required.
- **DooD test limits.** Worker paths that spawn `docker compose` cannot run from the Claude Code sandbox (no Docker CLI). Unit tests mock the subprocess; end-to-end validation happens in `sentinel-dev`.
- **Single-instance assumption.** No HA deploy story. Startup reconciliation will false-fail a peer's in-flight workers if two services target the same DB.
- **Session-completion rituals** — CLAUDE.md's "Landing the Plane" (quality gates, bd sync, push) applies to every track. Not re-specified in individual plans.
