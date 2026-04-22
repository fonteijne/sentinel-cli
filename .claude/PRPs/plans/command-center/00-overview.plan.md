# Command Center Backend — Plan Index

This is an index, not an executable plan. It captures shared architecture and sequencing across the five tracks that together produce a fully functional backend for a future Sentinel Command Center dashboard.

The dashboard/UI is **explicitly out of scope** for every plan in this directory.

---

## Tracks

| # | Plan | Scope | Depends on |
|---|------|-------|------------|
| 01 | [`01-foundation.plan.md`](01-foundation.plan.md) | `Execution` entity, SQLite persistence, structured event bus, CLI-as-client | — |
| 02 | [`02-http-read-api.plan.md`](02-http-read-api.plan.md) | FastAPI read-only endpoints (executions, events, agent results) | 01 |
| 03 | [`03-live-event-stream.plan.md`](03-live-event-stream.plan.md) | WebSocket tail of the event bus with replay-from-persistence | 01, 02 |
| 04 | [`04-commands-and-workers.plan.md`](04-commands-and-workers.plan.md) | Start/cancel/retry endpoints, out-of-process worker supervisor, crash recovery | 01 |
| 05 | [`05-auth-and-binding.plan.md`](05-auth-and-binding.plan.md) | Bearer-token auth, network binding, CORS | 02, 03, 04 |

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
| Persistence | **SQLite** at `~/.sentinel/sentinel.db`, stdlib `sqlite3` | No new deps; single-writer is fine (one orchestrator process); schema-versioned so Postgres/Dolt remains a future swap |
| Migrations | Plain `.sql` files numbered `NNN_name.sql`, applied in order, tracked in `schema_migrations` table | Boring; auditable; no Alembic overhead |
| Event bus | In-process pub/sub, stdlib only; every published event is persisted to `events` table before subscribers fire | Persistence is the source of truth — subscribers/streams are best-effort replays |
| HTTP framework | **FastAPI + uvicorn** | Async-native, WebSocket support in core, pydantic already a dep |
| Worker model | One-subprocess-per-execution, spawned via `multiprocessing` or `subprocess` from Supervisor | Execution crash cannot take down the HTTP service; DooD still works (subprocess inherits Docker socket env) |
| Python | 3.11+ (matches existing constraint) | — |
| Config | Extend existing `ConfigLoader` (config_loader.py) — no new config system | Consistency |
| Logging | Keep `logger = logging.getLogger(__name__)` pattern; event bus is orthogonal, not a replacement | Event bus is for *execution* telemetry; logs stay for operational diagnostics |

---

## Target Module Layout

```
src/
├── core/                        # [01] new package
│   ├── persistence/
│   │   ├── db.py
│   │   └── migrations/
│   │       └── 001_init.sql
│   ├── events/
│   │   ├── bus.py
│   │   └── types.py
│   └── execution/
│       ├── models.py
│       ├── repository.py
│       ├── orchestrator.py
│       ├── worker.py            # [04]
│       └── supervisor.py        # [04]
├── service/                     # [02] new package
│   ├── app.py
│   ├── schemas.py
│   ├── auth.py                  # [05]
│   └── routes/
│       ├── executions.py        # [02]
│       ├── stream.py            # [03]
│       └── commands.py          # [04]
├── agents/                      # existing — [01] adds event emission
├── cli.py                       # existing — [01] refactors plan/execute/debrief; [02] adds `serve`
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
- Multi-tenant / multi-user authorization model (token is single-shared-secret in 05)
- Replacing Beads
- Replacing `~/.sentinel/sessions.json` as the Claude SDK session-id store — the new DB supplements it; a later plan can subsume it
- Changing how agents are defined or how they talk to claude-agent-sdk — only how they *emit progress*
