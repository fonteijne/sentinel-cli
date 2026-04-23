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
| 06 | [`06-production-exposure.plan.md`](06-production-exposure.plan.md) | Dev host-port publish, `sentinel-serve` service, bundled Traefik (profile `traefik`) with Let's Encrypt HTTP-01, `/docs` prod gate, deploy runbook | 02, 05 |

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
             └──────┬───────┘
                    ▼
             ┌──────────────┐
             │ 06 Exposure  │
             └──────────────┘
```

Tracks 02, 03, 04 can be worked in parallel worktrees once 01 is merged. 05 is the finishing seal for the backend itself. 06 lands the compose-level exposure (dev port + Traefik) so the service is browser-reachable — do not ship to anything but a trusted dev machine until 05 lands, and do not expose to the internet until 06 lands.

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
- `experimental/command-center-06-production-exposure`

---

## Environment variables

Single index of env vars read by the stack; each is documented in its owning plan.

| Variable | Read by | Owning plan | Purpose |
|---|---|---|---|
| `SENTINEL_DB_PATH` | CLI, service, worker | 01 | Override default `~/.sentinel/sentinel.db`; validated non-regular rejected |
| `SENTINEL_SERVICE_TOKEN` | service startup | 05 | Overrides the on-disk service token |
| `SENTINEL_SERVICE_URL` | CLI `--remote` | 04 | Default base URL for `sentinel execute --remote` (fallback: `http://127.0.0.1:8787`) |
| `SENTINEL_ENABLE_DOCS` | service startup | 06 | When `true`, exposes FastAPI `/docs`, `/redoc`, `/openapi.json`; default off (404) |
| `SENTINEL_HOSTNAME` | compose (`sentinel-serve` labels) | 06 | Public hostname Traefik routes; compose hard-fails if unset |
| `LETSENCRYPT_EMAIL` | compose (bundled `traefik`) | 06 | ACME account email for Let's Encrypt HTTP-01; compose hard-fails if unset |

All YAML config keys live under `service.*` in `config/config.yaml`:
`service.bind_address`, `service.port`, `service.cors_origins`, `service.rate_limits.max_concurrent`, `service.rate_limits.max_per_minute`.

## Migration numbering

Migrations live in `src/core/persistence/migrations/NNN_name.sql`, applied in ascending order, recorded in `schema_migrations`. Never reuse a version number.

| Plan | Migration | Introduces |
|---|---|---|
| 01 | `001_init.sql` | `schema_migrations`, `executions`, `events`, `agent_results` |
| 04 | `002_workers.sql` | `workers` (heartbeat/PID/compose_projects) |
| 06 | — | (no DB migration; compose/deploy changes only) |
| later | `003_*.sql` onward | future plans claim in PR-merge order |

Forward-only. No rollback mechanism today — documented as debt.

## Backup

`~/.sentinel/sentinel.db` is WAL-enabled: the file is accompanied by `sentinel.db-wal` and `sentinel.db-shm`. A correct backup is any one of:

- `sqlite3 /path/to/sentinel.db ".backup '/path/to/backup.db'"` (online-safe; preferred)
- `PRAGMA wal_checkpoint(TRUNCATE);` on the live DB, then `cp sentinel.db backup.db` (brief pause acceptable)
- Copy all three files atomically (filesystem snapshot)

A `cp sentinel.db backup.db` without any of the above produces a **corrupt** restore. Document this in ops runbooks.

## Explicitly NOT in any of these plans

- Dashboard UI (HTML/React/anything rendered)
- Prometheus/OpenTelemetry metrics export
- Aggregate metrics endpoint (executions/day, success rate, avg cost)
- Management endpoints (`/status`, `/projects`, `/validate`, `/reset`) — CLI-only for now
- Multi-tenant / multi-user authorization model (token is single-shared-secret in 05)
- Beads ↔ execution linkage (maintainers correlate manually via `ticket_id`)
- Replacing `~/.sentinel/sessions.json` as the Claude SDK session-id store — new DB supplements it
- Changing how agents are defined or how they talk to claude-agent-sdk — only how they *emit progress*
- `SENTINEL_SERVICE_URL` wired into the per-ticket `appserver` stacks — still follow-up (plan 06 wires the service itself into the compose network, not the appserver children)
- Worker-log endpoint (GET `logs/workers/<id>.log`) — follow-up
- Retention / archival of `events` and `agent_results` — `idx_events_ts` is in place; sweep is follow-up
- Schema migration rollback — forward-only today
- Log rotation for `logs/workers/*.log` — ops concern
- `succeeded_with_warnings` status — binary success/failure today; post-implementation warnings (e.g. GitLab push failure after agents succeeded) surface as `failed` with error + `agent_results` content; operators read via plan 02 GET
- `RateLimited` does not transition status — it's observational; runs stay `running` and the orchestrator handles backoff
- Forced re-run of a completed post-mortem — no endpoint; operators use `sqlite3` to clear the `post_mortem_complete` metadata flag and restart the service
- Windows deployment — POSIX-only target (`O_EXCL | 0o600` semantics, Docker, SIGTERM/SIGKILL behavior)

---

## Known operating caveats

These are not bugs; they are deliberate scope decisions.

- **`$HOME` ambiguity across containers.** `~/.sentinel/sentinel.db` resolves to the container's HOME inside `sentinel-dev` and to the host HOME outside. Running `sentinel` in both places creates two independent DBs. Set `SENTINEL_DB_PATH` in both environments to a shared path if sharing is required.
- **DooD test limits.** Worker paths that spawn `docker compose` cannot run from the Claude Code sandbox (no Docker CLI). Unit tests mock the subprocess; end-to-end validation happens in `sentinel-dev`.
- **Single-instance assumption.** No HA deploy story. Startup reconciliation will false-fail a peer's in-flight workers if two services target the same DB.
- **Session-completion rituals** — CLAUDE.md's "Landing the Plane" (quality gates, bd sync, push) applies to every track. Not re-specified in individual plans.
- **SQLite JSON1 required.** `list_post_mortem_incomplete`, `register_compose_project`, and metadata merges use `json_extract`/`json_set`/`json_insert`. JSON1 is compiled into CPython's `sqlite3` module on official builds for Linux/macOS since 3.9; `ensure_initialized()` asserts it on startup.
- **FastAPI ≥ 0.110** (pinned). Router-level `dependencies=[Depends(...)]` on WebSocket routes is relied on (plan 03, 05); behavior before 0.100 was inconsistent.
