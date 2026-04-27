# Sentinel Command Center — API Contract (v0.1)

**Source of truth:** the FastAPI service at `src/service/app.py` and routers in `src/service/routes/`. This document is reverse-engineered from the *current* shipped backend and is **read-only** input for the dashboard UI. The dashboard MUST NOT assume any endpoint not listed here.

Branch: `v2/command-center` (HEAD `37a30b0`).

> Backend changes are out of scope for this work. Anything missing on the backend is represented in the dashboard as a disabled control, an empty state, or a "coming soon" placeholder.

---

## 1. Transport

- Base URL: configurable. Local dev compose: `http://localhost:8787`.
- Auth: `Authorization: Bearer <token>` for every route except `GET /health`. The token is loaded/created by `src/service/auth.py:load_or_create_token` and stored in the service container.
- Content-Type: JSON.
- CORS: only the origins in `service.cors_origins` (config) are allowed. Browser dashboards must be served from one of those origins, or proxied. `allow_credentials=True` — wildcards forbidden.
- Rate limits (write only): `service.rate_limits.max_concurrent` (default 3) and `service.rate_limits.max_per_minute` (default 30) per token. Read endpoints are not rate limited (polling expected).
- Idempotency: `Idempotency-Key` header on `POST /executions`. The `(token_prefix, key)` tuple deduplicates inside the create window.
- Docs: `/docs`, `/redoc`, `/openapi.json` are gated by the `SENTINEL_ENABLE_DOCS` env var (or `service.enable_docs`). Off in prod by default. The dashboard does not depend on `/openapi.json`.

---

## 2. Endpoints

### 2.1 Health

| Method | Path | Auth | Notes |
| --- | --- | --- | --- |
| GET | `/health` | none | Deep probe — opens DB and runs `SELECT 1`. Returns `{"status":"ok","db":"ok"}`; 500 on DB failure. |

### 2.2 Executions — read

| Method | Path | Description |
| --- | --- | --- |
| GET | `/executions` | List executions, most-recent first. |
| GET | `/executions/{execution_id}` | Get one execution. |
| GET | `/executions/{execution_id}/events` | Paginated event log for an execution (since_seq cursor). |
| GET | `/executions/{execution_id}/agent-results` | Per-agent structured results. |

**`GET /executions` query parameters**

| Name | Type | Default | Notes |
| --- | --- | --- | --- |
| `project` | string | — | Compose project filter. |
| `ticket_id` | string | — | Ticket ID filter (e.g. `ACME-123` or `ACME_KEY-12`). |
| `status` | enum | — | `queued` \| `running` \| `cancelling` \| `succeeded` \| `failed` \| `cancelled`. |
| `kind` | enum | — | `plan` \| `execute` \| `debrief`. |
| `limit` | int | 50 | Server clamps to 200. |
| `before` | ISO-8601 datetime | — | Returns rows strictly older than this `started_at`. |

Response envelope: `{ items: ExecutionOut[], next_cursor: string | null }`. `next_cursor` is the ISO `started_at` of the oldest returned row when the page is full. Reuse it as `before` to fetch the next page.

**`GET /executions/{id}/events` query**

| Name | Type | Default | Notes |
| --- | --- | --- | --- |
| `since_seq` | int | 0 | Only events with `seq > since_seq`. |
| `limit` | int | 200 | Clamped to 1000. |

`next_cursor` for events is the **string of the last `seq`**.

### 2.3 Executions — write

| Method | Path | Status | Description |
| --- | --- | --- | --- |
| POST | `/executions` | 202 | Create + spawn an execution asynchronously. |
| POST | `/executions/{id}/cancel` | 202 | Async cancel; supervisor SIGTERM → SIGINT → SIGKILL escalation. |
| POST | `/executions/{id}/retry` | 202 | Create a *new* execution linked to original via `metadata.retry_of`. |

**`POST /executions` body** (extra fields forbidden):

```json
{
  "ticket_id": "ACME-123",            // required, pattern ^[A-Z][A-Z0-9_]+-\\d+$
  "project": "acme",                  // optional, lowercased prefix derived if omitted
  "kind": "plan|execute|debrief",     // required
  "options": {                        // optional
    "revise": false,
    "max_turns": 30,                  // 1..200
    "follow_up_ticket": "ACME-124"    // optional, same ticket pattern
  }
}
```

Optional header: `Idempotency-Key: <opaque-string>`.

Cancel/retry are guarded by status:
- Cancel rejects with **409** if execution is already terminal.
- Retry rejects with **409** if execution is still live (`queued|running|cancelling`).

### 2.4 WebSocket stream

`WS /executions/{id}/stream?since_seq=<int>` — bearer auth via the WebSocket dep (closes with code `1008` on auth failure, `4404` if execution not found).

Frame shapes:

```jsonc
{ "kind": "event", "seq": 42, "ts": "...", "type": "tool.called", "agent": "python_developer", "payload": { ... } }
{ "kind": "heartbeat", "ts": "..." }                       // every 30s of idle
{ "kind": "end", "execution_status": "succeeded|failed|cancelled" } // emitted then socket closed
```

Reconnect protocol: on disconnect, reconnect with `since_seq` = last received `seq`. Server-side resume is gap-free.

---

## 3. Domain shapes

### `ExecutionOut`

```ts
type ExecutionOut = {
  id: string;
  ticket_id: string;
  project: string;
  kind: "plan" | "execute" | "debrief";
  status: "queued" | "running" | "cancelling" | "succeeded" | "failed" | "cancelled";
  phase: string | null;          // free-form orchestrator phase label
  started_at: string;            // ISO-8601, tz-aware
  ended_at: string | null;
  cost_cents: number;            // accrued LLM cost in cents (integer)
  error: string | null;          // populated on failed/cancelled
  metadata: Record<string, any>; // includes options, retry_of, compose_projects
};
```

### `EventOut`

```ts
type EventOut = {
  seq: number;                   // monotonic per execution
  ts: string;                    // ISO-8601
  agent: string | null;
  type: string;                  // see event catalogue below
  payload: Record<string, any>;
};
```

### `AgentResultOut`

```ts
type AgentResultOut = {
  agent: string;
  result: Record<string, any>;   // free-form, agent-specific
  created_at: string;
};
```

### Event type catalogue (current, stable identifiers)

Lifecycle: `execution.started`, `execution.completed`, `execution.failed`, `execution.cancelling`, `execution.cancelled`, `phase.changed`.
Agent / tool: `agent.started`, `agent.finished`, `agent.message_sent`, `agent.response_received`, `tool.called`.
Results: `test.result`, `finding.posted`, `cost.accrued`.
Interactive: `debrief.turn`, `revision.requested`.
Error-class (observational only — does NOT transition status): `rate_limited`.

Terminal types (used for the WS `end` frame): `execution.completed`, `execution.failed`, `execution.cancelled`.

> **Known gap (from `GAP_ANALYSIS.md`):** `agent.started`, `agent.finished`, `test.result`, `finding.posted`, `debrief.turn`, `revision.requested` are declared but **not yet emitted** (G-04). The dashboard treats them as future-compatible — surfaces are built, but render gracefully when no rows exist.

---

## 4. What the backend does NOT expose (relevant gaps)

These features are required by an admin dashboard but are not in the current API. The dashboard must surface them as disabled, empty, mocked-static, or "coming soon":

- **Worktree directory** (`/worktrees`) — `WorktreeManager` is CLI-side only. No HTTP CRUD for worktrees, branches, or compose projects.
- **Tickets** (`/tickets`) — Jira/GitLab integrations are CLI-side; no proxied list endpoint.
- **Compose / container introspection** — supervisor cleans up containers via `docker compose down`, but does not expose container state.
- **Service-level metrics** — no `/metrics`, no aggregated cost/cost-by-day, no token-usage counters.
- **Auth / users** — single shared bearer token; no users, roles, or sessions endpoint.
- **Settings mutation** — config is YAML-on-disk + env. No `PUT /config`.
- **Search** — no full-text or cross-execution search endpoint.
- **Notifications / webhooks** — none.
- **Audit log** — written to logs (`audit_write` dep) but not queryable.

---

## 5. Polling & pagination guidance for the dashboard

- Read polling interval ≥ 2 s (the read router has no rate limit, but be polite).
- Use the WebSocket for the *single* currently-focused execution and HTTP polling for list views.
- Keep `since_seq` per execution in client state; on reconnect, replay from there.
- For long lists, page backward with `before=<next_cursor>` until `next_cursor === null`.

---

*End of contract — v0.1, 2026-04-27.*
