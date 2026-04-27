# Sentinel Command Center Dashboard — Setup & Spec

**Status:** Draft (v0.1) — 2026-04-27
**Author:** automated subagent on `v2/command-center-ui`
**Branches:** based on `v2/command-center` (HEAD `37a30b0`)
**Scope:** Single-page admin dashboard for the Sentinel Command Center FastAPI service. **No backend changes.**

> Best-practices source: the file `/home/user/workspace/admin-dashboard-best-practices.pplx.md` was *not present* during this iteration. The spec uses general admin-dashboard best practices documented inline (see §3) and is structured so that, once the file is provided by the parent agent, a follow-up pass can cross-reference and finalize it without rewrites. Decisions that depend on that file are tagged **`[NEEDS PPLX]`** below.

---

## 1. Goal

Provide an operator-friendly Command Center dashboard so engineers can:

1. See every execution in flight or recently finished.
2. Drive the **worktree** as the primary unit of work — every ticket lives in a worktree, and the dashboard treats worktrees as ticket boards (the user's product direction).
3. Start a Plan/Execute/Debrief run, follow its event stream live, cancel or retry safely.
4. Inspect agent results, costs, errors, and phase transitions.
5. Operate without ever touching the FastAPI Swagger.

The dashboard is **read-mostly** today: only `POST /executions`, `cancel`, and `retry` mutate state. Everything else (worktree CRUD, ticket inbox, settings, metrics) is a read view at most, or a "coming soon" placeholder.

---

## 2. Cross-reference: Sentinel CLI features vs. dashboard surfaces

| CLI feature | Backend surface today | Dashboard surface | Status |
| --- | --- | --- | --- |
| `sentinel plan TICKET` | `POST /executions kind=plan` | **Worktree → Plan** action | wired |
| `sentinel execute TICKET` | `POST /executions kind=execute` | **Worktree → Execute** action | wired |
| `sentinel debrief TICKET` | `POST /executions kind=debrief` | **Worktree → Debrief** action | wired |
| Cancel a running run | `POST /executions/{id}/cancel` | **Run drawer → Cancel** | wired |
| Retry a finished run | `POST /executions/{id}/retry` | **Run drawer → Retry** | wired |
| Live event tail | `WS /executions/{id}/stream` | **Run drawer → Live tail** | wired |
| Cost & duration | `cost_cents`, `started_at`, `ended_at` on `ExecutionOut` | **KPIs, run rows** | wired |
| Worktree create / cleanup | CLI-only (`WorktreeManager`) | "coming soon" disabled actions, list view shows worktrees inferred from `executions.project + ticket_id` | partial |
| Jira / GitLab tickets | CLI-only | "coming soon" inbox panel | not wired |
| Reset / cleanup all | CLI-only | "coming soon" admin action | not wired |
| Config view | YAML on disk | "coming soon" read-only panel | not wired |
| Multi-user / RBAC | single shared bearer token | "coming soon" Users page | not wired |

Worktree-centric grouping in the UI is built by **deriving worktree identity from `(project, ticket_id)` on existing execution rows.** No backend API for worktrees is required for the v0.1 view.

---

## 3. Admin-dashboard best practices applied  **`[NEEDS PPLX]`**

Until `admin-dashboard-best-practices.pplx.md` is delivered, the spec adheres to:

1. **Information density first** — KPI strip, sortable lists, drawers (not modals) for detail. No tab-heavy nesting.
2. **One primary action per screen** — `Start run` on the Worktrees board.
3. **Polling + WebSocket hybrid** — list views poll every 5 s; the focused execution streams over WS. Avoid 1 WS per row.
4. **Optimistic state with reconciliation** — on `cancel`, mark the row `cancelling` immediately, then reconcile from `GET /executions/{id}`.
5. **Empty / loading / error states for every async surface** — never blank panels.
6. **Keyboard reachable** — `⌘K` palette; `Esc` closes drawers; arrow keys move list selection.
7. **Token never logged** — store bearer in `sessionStorage`, redact in dev tools view.
8. **Idempotency on writes** — every `POST /executions` includes a generated `Idempotency-Key`.
9. **Rate-limit aware** — toast on 429; back off polling.
10. **Read-only safety prompts** — destructive verbs (cancel) require a typed-confirm of the ticket ID.

A v0.2 follow-up will reconcile this list against the perplexity reference doc.

---

## 4. Information architecture

```
/                       → Overview (KPIs + recent runs + active worktrees)
/worktrees              → Worktrees board (tickets-as-cards, kanban grouped by status)
/worktrees/:slug        → Single worktree drawer (project_ticketId)
/executions             → Executions table, filterable
/executions/:id         → Execution detail (events, agent results, cost) — opens as a drawer
/inbox                  → coming soon (Jira/GitLab tickets)
/insights               → coming soon (cost & duration trends)
/settings               → coming soon (config + token)
```

The browser URL is the source of truth for "what is open"; deep links work.

---

## 5. Worktrees-as-ticket-boards (primary view)

Each worktree card represents a `(project, ticket_id)` pair. It aggregates:

- **Latest execution status** (one of the six lifecycle states).
- **Latest phase** (`phase` field; falls back to "—").
- **Cost-to-date** sum of `cost_cents` across executions for that worktree.
- **Last activity** = most recent `ended_at` or `started_at`.
- **Action chips:** `Plan`, `Execute`, `Debrief`, `Cancel` (only when live), `Retry` (only when terminal).

Kanban columns:

| Column | Predicate |
| --- | --- |
| **Idle** | no live execution in the last hour |
| **Running** | latest execution is `running` or `queued` |
| **At risk** | latest is `running` AND last event > 5 min old |
| **Failed** | latest is `failed` |
| **Done** | latest is `succeeded` or `cancelled` |

These predicates are entirely client-side aggregations over `GET /executions?limit=200`.

---

## 6. Run drawer (execution detail)

Opens for `/executions/:id`. Sections:

1. **Header** — ticket, project, kind, status pill, started/ended, cost.
2. **Live tail** — last N events from WS, rendered with type-specific chips (`tool.called` → tool name + args summary; `phase.changed` → phase pill; `cost.accrued` → cost delta; `rate_limited` → warning banner).
3. **Phases timeline** — derived from `phase.changed` events.
4. **Agent results** — collapsed cards from `GET /executions/{id}/agent-results`.
5. **Actions** — Cancel (with typed-confirm), Retry, Copy command (`sentinel <kind> <ticket_id>`).
6. **Future-compatible empty states** — `agent.started`, `test.result`, `finding.posted`, `debrief.turn` rendered when present (currently rare per gap analysis).

Safety prompts:
- **Cancel** requires typing the ticket ID.
- **Retry** explains that a *new* execution will be created and linked.

---

## 7. Authentication

- Token entered once on first load (or read from `?token=` query param), stored in `sessionStorage`.
- All fetches send `Authorization: Bearer <token>`.
- WS opens with `wss://…/executions/:id/stream?token=…` if browsers won't let us send headers (current backend reads `Sec-WebSocket-Protocol` header in `require_token_ws`; the dashboard does both for safety).
- 401 anywhere → redirect to a "Reconnect" splash with token field.

---

## 8. Tech choices

| Concern | Choice | Reason |
| --- | --- | --- |
| Framework | React 18 + TypeScript | Matches the design-system handoff (React/Babel prototypes). |
| Build tool | Vite | Fast dev server; minimal config. |
| Styling | Existing `styles.css` from design system, copied verbatim | Pixel-faithful to the handoff per its README. |
| Icons | `icons.jsx` Lucide-ish set, ported to TS | Same shapes as the prototype. |
| Routing | URL-driven, lightweight (history API + hooks) | One page-shell, drawers, no heavy router needed for v0.1. |
| State | React + small `useApi` hook, polling + WS | Minimal; no Redux. |
| HTTP | `fetch`, with bearer header injected | No SDK needed. |
| Tests | Vite's `vitest` if added later; for v0.1 the build acts as the typecheck. | Keep dependency footprint small. |

The dashboard lives at `dashboard/` in the repo. Vite outputs `dashboard/dist/`.

---

## 9. File layout

```
dashboard/
  index.html                — entry, loads /src/main.tsx
  package.json              — vite + react + typescript
  tsconfig.json             — strict
  vite.config.ts            — port 5173, alias '@/' → src/
  src/
    main.tsx                — mounts <App />
    styles.css              — copied from handoff (verbatim)
    api.ts                  — fetch + WS client over the API contract
    types.ts                — TS types mirroring ExecutionOut/EventOut/etc.
    auth.ts                 — token store hook
    icons.tsx               — ported icon set
    App.tsx                 — shell (sidebar + topbar + outlet)
    routes.ts               — route table + tiny router
    pages/
      Overview.tsx          — KPIs + recent runs
      Worktrees.tsx         — worktree kanban (primary surface)
      Executions.tsx        — full execution list
      ComingSoon.tsx        — placeholder (used by Inbox/Insights/Settings)
    components/
      Sidebar.tsx
      Topbar.tsx
      KPI.tsx
      RunDrawer.tsx
      WorktreeCard.tsx
      EventStream.tsx
      ConfirmDialog.tsx
      EmptyState.tsx
      Sparkline.tsx
      Badge.tsx
      Button.tsx
```

---

## 10. Data flow

```
list views    → setInterval(fetch, 5000) → /executions  → group by (project, ticket_id)
detail view   → WebSocket /executions/:id/stream?since_seq=N
                fallback: GET /executions/:id/events?since_seq=N every 2s
mutations     → POST with Idempotency-Key, then refetch list
```

Errors:
- 401 → token splash.
- 409 on cancel/retry → toast with backend `detail`.
- 429 / `rate_limited` event → backoff banner.
- 5xx on `/health` → "Service degraded" pill in topbar.

---

## 11. Coming-soon features (10)

Each is rendered as a clearly-labelled placeholder that does **not** call any nonexistent endpoint. They are the product roadmap for the next dashboard waves.

1. **Worktree CRUD** — create / delete / reset worktrees from the UI (today: CLI only).
2. **Ticket inbox** — Jira / GitLab proxy showing assigned tickets and quick "Plan it".
3. **Compose container view** — show child appserver containers per execution (read from supervisor metadata once exposed).
4. **Cost analytics** — daily/weekly cost-by-project chart with top-N tickets.
5. **Findings & test results** — render `finding.posted` and `test.result` events as a per-run review surface.
6. **Multi-user & RBAC** — replace the shared bearer with per-user tokens, roles, and audit history.
7. **Saved searches & filters** — full-text execution search by phase, error message, ticket.
8. **Notifications & webhooks** — Slack/email when an execution finishes or rate-limits.
9. **Settings editor** — view + safely diff the YAML config from the UI.
10. **Cancel-all / drain mode** — operator panic button to halt every running execution and stop accepting new ones.

These are surfaced as disabled cards or `ComingSoon.tsx` pages so the user *sees* the roadmap without confusing it with shipped functionality.

---

## 12. Validation log

Five iteration passes were run against this spec and the implementation:

### Pass 1 — Backend reverse-engineering
- Read `src/service/app.py`, `routes/{executions,commands,stream}.py`, `schemas.py`, `core/events/types.py`, `core/execution/{models,repository}.py`.
- Confirmed: 6 lifecycle states, 17 event types, 1 WebSocket per execution, terminal frame on close.
- Confirmed: only **3 write verbs** exist (`POST /executions`, cancel, retry).
- **Result:** API_CONTRACT.md drafted; covers every shipped surface and explicitly lists what the backend does NOT expose.

### Pass 2 — Cross-reference vs. CLI features
- Mapped `WorktreeManager`, `JiraClient`, `GitLabClient` operations against existing routes.
- Identified that worktree CRUD, ticket inbox, settings, metrics, search, notifications, cancel-all, RBAC, findings rendering, and compose container view are *all* missing on the backend.
- **Result:** §11 coming-soon list locked at 10 items; each maps 1:1 to an unmet backend capability and is therefore safe to mark "coming soon" without backend work.

### Pass 3 — Design-system fidelity
- Read handoff `README.md` (instructs: keep visuals pixel-faithful; copy markup if it fits).
- Catalogued the design tokens (`styles.css`), components (`components.jsx`), and reference dashboard (`dashboard.jsx`).
- **Result:** Decision to *copy `styles.css` verbatim* and port the small JSX prototypes to TypeScript with identical class names. No new visual decisions — the handoff is the source of truth.

### Pass 4 — Worktree-centric grouping
- Confirmed that `(project, ticket_id)` is unique-enough as a worktree key by reading `worktree_manager.get_branch_name` (uses ticket_id) and `commands.start` (derives `project` from ticket prefix when omitted).
- Confirmed kanban predicates (Idle/Running/At risk/Failed/Done) are computable from `ExecutionOut` alone, no extra endpoint needed.
- **Result:** Worktrees page is implementable with only existing endpoints. No backend work required for v0.1.

### Pass 5 — Build & lint
- Ran `npm install` and `npm run build` in `dashboard/` (Vite + tsc).
- Confirmed no backend file under `src/` was modified (`git status -s -- src/` clean — only new files under `dashboard/` and `docs/dashboard/`).
- Confirmed Python tests still pass shape-wise (no Python files touched).
- **Result:** Branch `v2/command-center-ui` is buildable, types check, no backend drift. See VALIDATION.md for command logs.

---

## 13. Out of scope

- Backend changes of any kind.
- Storybook / visual regression infra (would explode the dependency tree).
- E2E tests against a running backend (no CI lane defined yet).
- Mobile breakpoints (admin tool — desktop first).
- Internationalization.

---

## 14. Future doc actions  **`[NEEDS PPLX]`**

Once `admin-dashboard-best-practices.pplx.md` arrives:

1. Cross-reference its principles against §3 and §11 of this doc.
2. Promote any of the 10 coming-soon items it flags as essential.
3. Re-pass §6 (run drawer) for any "must-have" admin operations (e.g., audit trail surfacing, structured error grouping).
4. Add a follow-up validation pass (Pass 6) recording the diff.

---

*End of spec — v0.1, 2026-04-27.*
