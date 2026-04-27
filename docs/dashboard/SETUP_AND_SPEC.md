# Sentinel Command Center Dashboard — Setup & Spec

**Status:** Reconciled (v0.3 — close-the-gap aligned) — 2026-04-27
**Author:** automated subagent on `v2/command-center-ui`
**Branches:** read-only against `v2/command-center-close-the-gap` (HEAD `4b742cc`); v0.2 was pinned to `v2/command-center` (HEAD `37a30b0`).
**Scope:** Single-page admin dashboard for the Sentinel Command Center FastAPI service. **No backend changes.**

> Best-practices source: `/home/user/workspace/admin-dashboard-best-practices.pplx.md` (the "PPLX research report"). All `[NEEDS PPLX]` placeholders from v0.1 have been resolved against that report in this revision; see §3, §6, §11, and §14 for the reconciled guidance. The Pass 6 validation entry in `VALIDATION.md` records the diff.

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

## 3. Admin-dashboard best practices applied

Reconciled against the PPLX research report (`/home/user/workspace/admin-dashboard-best-practices.pplx.md`). Each principle below cites the section of that report it derives from; specific external sources cited by the report are linked inline.

1. **Inverted-pyramid information density** — top row is 3–5 KPI cards (system health, active runs, error rate, cost/duration); middle band carries time-series; lower band is the executions/worktrees table. F-pattern places the most critical signal in the top-left quadrant. (PPLX §1.3, §3.1; [GOV.UK redesign](https://www.tempertemper.net/portfolio/efficient-simple-and-usable-govuk-dashboard-pages))
2. **One page, one decision** — `Overview` answers "is the Command Center healthy right now?"; `Worktrees` answers "what work is in flight?"; `Executions` answers "what changed and where?". Pages that cannot be stated as one of those questions get split. (PPLX §4.2; [OpenObserve](https://openobserve.ai/blog/observability-dashboards/))
3. **Golden signals at the top** — Overview KPIs map to request rate (executions started/min), error rate (failed %), latency (median run duration), and saturation (active executions vs. configured worker cap). (PPLX §4.1)
4. **Drawers (not modals) for detail; deep links everywhere** — every drawer has its own URL so operators can paste a link to a teammate. URL state encodes filters, time range, and selected execution. (PPLX §2.2, §12; [UX Pilot](https://uxpilot.ai/blogs/dashboard-design-principles))
5. **Real-time update channel chosen per direction** — read-only feeds (event stream, log tail) use SSE/WebSocket frames from the existing `WS /executions/{id}/stream`; bidirectional control (cancel/retry) goes through REST writes plus optimistic UI. List views poll every 5 s. WebSocket reconnect uses exponential backoff `1s → 2s → 4s → max 30s` per the report's recommendation. (PPLX §6.1, §9.2)
6. **Empty / loading / error states for every async surface** — initial load uses skeletons, background refresh uses a thin top progress bar over stale data, error states give a specific message + recovery action + error code. No blank panels. (PPLX §6.1–§6.3; [LogRocket](https://blog.logrocket.com/ui-design-best-practices-loading-error-empty-state-react/))
7. **Command palette (⌘K)** — fuzzy-search palette is the primary navigation aid: jump to worktree, jump to execution by id, run actions on the focused row. Number-key shortcuts (`1`–`5`) activate sidebar tabs. (PPLX §2.3, §2.4; [UX Patterns for Developers](https://uxpatterns.dev/patterns/advanced/command-palette))
8. **Friction on destructive actions, in proportion to risk** — `Cancel` (medium risk: terminates a run) is a modal with an explicit action label; `Retry` is medium risk and uses a modal that explains a *new* execution will be linked; any future `Reset / Cancel-all` belongs in a "Danger Zone" panel with type-to-confirm, separated spatially from safe controls. Cancel must never be the default focus. (PPLX §5.1–§5.3; [GitHub Danger Zone pattern](https://thecrunch.io/ai-agent-dashboard/), [UX Psychology](https://uxpsychology.substack.com/p/how-to-design-better-destructive))
9. **Status vocabulary uses color + icon + text** — a status pill is never color alone; lozenge pattern from Atlassian Design System is followed (Operational / Degraded / Partial / Major / Maintenance / Unknown). (PPLX §4.3, §8.2; [WCAG 1.4.1](https://webaim.org/standards/wcag/checklist), [Atlassian Lozenge](https://atlassian.design/components))
10. **Drill-down with preserved context** — Overview KPI card → filtered Executions table → Run drawer with live tail / phases / agent results. Time range and global filters carry through; breadcrumb is always visible. (PPLX §12; mirrors Grafana / Linear / Mixpanel patterns)
11. **Progressive disclosure, two levels max** — Run drawer summary → expandable log → trace/agent-result detail. "Advanced" config and any future "Danger Zone" sit behind explicit toggles, never on the primary surface. (PPLX §7; [NN/G](https://www.nngroup.com/articles/progressive-disclosure/))
12. **WCAG 2.2 AA baseline** — 4.5:1 contrast on body text, visible focus on every interactive element, 24×24 CSS-px minimum touch target, ARIA live regions for toast/async completion, focus trap on modal dialogs, full keyboard reachability for the command palette and run drawer. Charts must have a data-table alternative or text summary. (PPLX §8; [WebAIM](https://webaim.org/standards/wcag/checklist), [Accessible.org WCAG 2.2](https://accessible.org/wcag/))
13. **Performance budgets** — initial load < 2 s, view transitions < 300 ms, real-time updates < 100 ms perceived latency; debounce filter inputs 300–500 ms; throttle event-stream rendering to 60 fps and batch incoming events per animation frame. Tables ≥ 200 rows must virtualize (react-window). (PPLX §9.1–§9.2; [BootstrapDash](https://www.bootstrapdash.com/blog/react-dashboard-performance))
14. **Token never logged; auth on backend only** — bearer stored in `sessionStorage`, redacted from dev tooling, never persisted. RBAC is enforced server-side; the dashboard adapts visually (hides controls the role cannot exercise) but never trusts frontend role claims. Disabled controls explain *why* via tooltip. (PPLX §14)
15. **Idempotency and rate-limit handling on writes** — every `POST /executions` carries a generated `Idempotency-Key`; a 429 or `rate_limited` event triggers a backoff banner and pauses polling. (PPLX §9.2 *real-time architecture*, §11.1 *background job status pattern*)
16. **Audit-log surface, even minimal** — the executions table itself is the audit timeline today (timestamp + actor-as-token-owner + action kind + ticket); a future Activity Timeline page is reserved with the canonical entry format (timestamp, actor, action, resource, before/after diff, correlation id). (PPLX §13.1–§13.2; [Martin Fowler — Audit Log](https://martinfowler.com/eaaDev/AuditLog.html))

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
| **Running** | latest execution is `running` or `queued` (and within the at-risk thresholds) |
| **At risk** | latest is `running` and `started_at` > 5 min ago, **or** `queued` and `started_at` > 2 min ago |
| **Failed** | latest is `failed` |
| **Done** | latest is `succeeded` or `cancelled` |

These predicates are entirely client-side aggregations over `GET /executions?limit=200`.

---

## 6. Run drawer (execution detail)

Opens for `/executions/:id`. Implements the three-level drill-down architecture from PPLX §12 (Overview KPI → Executions list → Run drawer) with preserved time range / filter context and a deep-linkable URL per level.

Sections:

1. **Header** — ticket, project, kind, status pill (color + icon + label per PPLX §4.3), started/ended, cost. Breadcrumb back to source list is always visible (PPLX §2.2).
2. **Live tail** — last N events from WS, rendered with type-specific chips (`tool.called` → tool name + args summary; `phase.changed` → phase pill; `cost.accrued` → cost delta; `rate_limited` → warning banner). Rendering is throttled to ≤60 fps and batches incoming events per animation frame (PPLX §9.2). Long buffers (>1000 events in one run) trigger a virtualized list.
3. **Phases timeline** — derived from `phase.changed` events; styled as the Activity Timeline pattern (timestamp + actor + action + resource), so its conventions transfer 1:1 to the future Activity page (PPLX §13.2).
4. **Agent results** — collapsed cards from `GET /executions/{id}/agent-results`; expand discloses logs and tool I/O (progressive disclosure, two-level max per PPLX §7).
5. **Actions** — Cancel (medium-risk: modal with explicit action label `Cancel run #<id>`, Cancel button red, default focus on safer "Keep running"), Retry (modal explaining a *new* execution is linked and the original is preserved), Copy command (`sentinel <kind> <ticket_id>`). Buttons are visually separated from the read sections per the Danger Zone principle (PPLX §5.3).
6. **Empty / loading / error states** — every async block (live tail, agent results, phases) has its own skeleton/empty/error state per PPLX §6; "no events yet" differs visibly from "events failed to load".
7. **First-class result surfaces** — as of close-the-gap HEAD `4b742cc` the orchestrator emits `agent.started`, `agent.finished`, `test.result`, `finding.posted`, `debrief.turn`, and `revision.requested`. The drawer renders dedicated **Test results** (PASS/FAIL + return code) and **Findings** cards (severity-ranked, critical→info; unknown severities sort last but still render); the live timeline gives each new type its own tone + icon. Empty states still distinguish "not produced by this run yet" from "failed to load" per PPLX §6.2; the raw JSON fallback is kept as a `<details>` for any payload key the UI doesn't recognise (PPLX §6.3 — graceful unknown-shape handling).
8. **Queued state visibility** — `queued` runs render an explicit warning lozenge with the current queued duration (`fmtElapsed`), a dedicated banner inside the run drawer, and a queued-duration line on the worktree card. The kanban "At risk" predicate now also fires for `queued` runs older than 2 minutes (running runs keep the original 5-minute grace) — a long-stuck queue is a real saturation signal, not a healthy waiting state.
9. **WebSocket per-token cap** — when the service closes the live stream with `1008 ws_connections_per_token_exhausted` (cap config: `service.rate_limits.ws_concurrent_per_token`, default 10), the run drawer surfaces a dedicated banner explaining the cap and pointing at the polling fallback, and a global toast appears so the operator knows the live tail is degraded rather than failed.

Safety prompts (PPLX §5.2 risk matrix):
- **Cancel** is medium-risk → modal with explicit "Cancel run" label, color-distinguished destructive button, explanation that the run will stop at the next safe boundary, and a non-default focus (Cancel is never the focused button on open).
- **Retry** is medium-risk → modal explaining a *new* execution will be created and linked back to the original; the original is not modified.
- A future **Reset / Cancel-all** (see §11 item 10) is critical-risk → modal + type-to-confirm of the workspace name, isolated in a Danger Zone panel.

Accessibility: drawer traps focus on open, returns focus to invoking row on close, supports `Esc` to close, exposes status messages via `aria-live="polite"` (PPLX §8.1).

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

Each is rendered as a clearly-labelled placeholder that does **not** call any nonexistent endpoint. They are the product roadmap for the next dashboard waves. Priority annotations come from the PPLX cross-reference (§16 of the report — "Sentinel CLI Feature Checklist Cross-Reference").

1. **Worktree CRUD** — create / delete / reset worktrees from the UI (today: CLI only). *PPLX §16.2: must back with type-to-confirm on destroy.*
2. **Ticket inbox** — Jira / GitLab proxy showing assigned tickets and quick "Plan it". *PPLX §16.1: maps to the "list jobs / queue" surface.*
3. **Compose container view** — show child appserver containers per execution (read from supervisor metadata once exposed). *PPLX §16.4: resource utilization gauges.*
4. **Cost analytics** — daily/weekly cost-by-project chart with top-N tickets. *PPLX §3.4: line/bar selection per question type.*
5. **~~Findings & test results~~** — ✅ shipped in v0.3 against close-the-gap HEAD `4b742cc`. The run drawer now renders first-class **Test results** and **Findings** cards (severity-ranked) plus dedicated tone/icon entries for `test.result` and `finding.posted` in the live timeline. Future enhancement: cross-run findings index (filter findings by severity across executions) — kept as roadmap, not as a placeholder, to avoid duplicating the per-run surface.
6. **Multi-user & RBAC** — replace the shared bearer with per-user tokens, roles, and audit history. *PPLX §14: enforce server-side, hide controls the role cannot exercise, tooltip on disabled.*
7. **Saved searches & filters** — full-text execution search by phase, error message, ticket. *PPLX §1.1: filters must be persistent and shareable via URL.*
8. **Notifications & webhooks** — Slack/email when an execution finishes or rate-limits. *PPLX §6.3: toast pattern for background-completion.*
9. **Settings editor** — view + safely diff the YAML config from the UI. *PPLX §13.1: pair with a config-change history (field-level diff).*
10. **Cancel-all / drain mode** — operator panic button to halt every running execution and stop accepting new ones. *PPLX §5.2: critical-risk action — Danger Zone panel + type-to-confirm + spatially separated from safe controls.*

**PPLX-driven additions to the roadmap (recorded for future passes, not yet placeholder-rendered):**
- **Activity timeline page** — a first-class audit timeline with actor / action / resource / timestamp / correlation id, exportable to CSV/JSON (PPLX §13). The Run drawer's phases timeline is the v0.1 stand-in.
- **Accessibility audit pass** — keyboard-only walkthrough + NVDA/VoiceOver smoke before the dashboard is exposed beyond developer preview (PPLX §8.3).

These are surfaced as disabled cards or `ComingSoon.tsx` pages so the user *sees* the roadmap without confusing it with shipped functionality.

---

## 12. Validation log

Seven iteration passes were run against this spec and the implementation. Full command logs and per-pass detail live in `VALIDATION.md`; brief recaps follow.

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

### Pass 7 — Close-the-gap reconciliation

- Re-pinned to `v2/command-center-close-the-gap` HEAD `4b742cc`. The orchestrator now emits `agent.started`, `agent.finished`, `test.result`, `finding.posted`, `debrief.turn`, and `revision.requested`; the WebSocket route enforces a per-token connection cap (`service.rate_limits.ws_concurrent_per_token`, default 10) closing with code `1008` and reason `ws_connections_per_token_exhausted`.
- Added first-class **Test results** and **Findings** cards to the run drawer; findings sort by severity rank (`critical → high → medium → low → info`, unknown last) with a `<details>` JSON fallback for any unrecognised payload shape.
- Extended the live event timeline with dedicated tone + icon mappings for the six newly-emitted event types.
- Improved queued state UX: dedicated lozenge with elapsed time in the run drawer header, an explanatory banner inside the drawer, and a queued-duration line on the worktree card. The kanban "At risk" predicate now also fires for `queued` runs older than 2 minutes (running keeps the 5 min grace).
- Added a per-drawer warning banner and a global dismissable toast that fire when the WebSocket closes with `1008 ws_connections_per_token_exhausted`, so the silent polling fallback is no longer invisible.
- Reclassified the "Findings & test results" item in §11 from "coming soon" to "shipped"; replaced future-compat empty-state language for the affected event types.
- Reverified zero backend changes (`src/`, `tests/`, `docker-compose.yml`, `pyproject.toml`, `poetry.lock`, `Dockerfile`).
- **Result:** dashboard surfaces the now-emitted event types and the WS cap UX, docs re-pinned to the close-the-gap reference, and no backend file is touched. ✅

### Pass 6 — PPLX research-report reconciliation
- Read `/home/user/workspace/admin-dashboard-best-practices.pplx.md` (742 lines, 17 sections).
- Replaced every `[NEEDS PPLX]` placeholder in this spec with concrete principles and citations from that report (see §3, §6, §11, §14).
- Promoted spec status from **Draft v0.1** to **Reconciled v0.2**.
- Added two PPLX-driven roadmap items (Activity Timeline page; Accessibility audit pass) and four follow-up doc actions (a11y QA, activity timeline promotion, performance regression sweep, status-vocabulary lint).
- Re-verified no backend file (`src/`, `tests/`, `docker-compose.yml`, `pyproject.toml`, `poetry.lock`) was touched.
- **Result:** spec is fully reconciled with the canonical research source; no orphan `[NEEDS PPLX]` markers remain. See VALIDATION.md Pass 6 for full diff and the no-backend-drift verification.

---

## 12a. Docker Compose deployment

The dashboard ships with its own multi-stage `dashboard/Dockerfile` and is wired into the top-level `docker-compose.yml` as a profile-gated `dashboard` service. The image is a static SPA bundle served by `nginx:1.27-alpine`; it never proxies API calls — the browser talks straight to the FastAPI backend (the same model as `npm run preview` locally).

### Run it

```bash
# From the repo root.
docker compose --profile dashboard up -d dashboard

# Open the dashboard:
open http://localhost:5174

# To stop it:
docker compose --profile dashboard down
```

Run alongside the backend (typical local-on-host workflow):

```bash
# Terminal A — backend on 127.0.0.1:8787
docker compose --profile dev up sentinel-dev

# Terminal B — dashboard on 127.0.0.1:5174
docker compose --profile dashboard up -d dashboard
```

On the splash screen, leave the API base URL as `http://localhost:8787` and paste the bearer token. (Both fields persist in the browser exactly as in `npm run dev` — no new storage paths were added.)

### Ports & env

| Variable | Default | Purpose |
| --- | --- | --- |
| `SENTINEL_DASHBOARD_PORT` | `5174` | Host port for the dashboard, bound to `127.0.0.1` only (matches the loopback-only contract used by `sentinel-dev`). |
| `SENTINEL_DASHBOARD_HOSTNAME` | `dashboard.localhost` | Traefik `Host(...)` rule used **only** when an operator runs the `traefik` profile or attaches their own Traefik to `sentinel-edge`. Ignored otherwise. |

The container listens on port `80` internally; the compose mapping is `127.0.0.1:${SENTINEL_DASHBOARD_PORT:-5174}:80`. To expose the dashboard on a different host port, set `SENTINEL_DASHBOARD_PORT=NNNN` in `.env` or the shell — no compose edit required.

### Networks

`dashboard` joins both `default` and `sentinel-edge`. The latter is the Traefik-shared network used by `sentinel-serve`; this lets a BYO/bundled Traefik route to the dashboard alongside the backend without further config. The dashboard does not depend on the backend service for startup — they are independent and can be brought up in either order.

### Behavior of the image

- `Dockerfile` is multi-stage: `node:20-alpine` runs `npm ci && npm run build`; `nginx:1.27-alpine` serves the resulting `dist/`.
- `nginx.conf` does SPA fallback to `/index.html`, caches hashed `/assets/*` aggressively, never caches `index.html`, and exposes a `/healthz` endpoint used by the compose `healthcheck`.
- No backend files (`src/`, `pyproject.toml`, `poetry.lock`) and no dashboard source files were modified for this wiring — only `dashboard/Dockerfile`, `dashboard/nginx.conf`, `dashboard/.dockerignore`, and the new `dashboard:` block in `docker-compose.yml`.

### Local dev workflow is preserved

`npm run dev` / `npm run build` / `npm run preview` continue to work unchanged. Compose is purely additive: no rebuild of backend images is required, and the existing default `docker compose up` (no profile) still starts only the `sentinel` CLI-idle container.

---

## 13. Out of scope

- Backend changes of any kind.
- Storybook / visual regression infra (would explode the dependency tree).
- E2E tests against a running backend (no CI lane defined yet).
- Mobile breakpoints (admin tool — desktop first).
- Internationalization.

---

## 14. Future doc actions

The PPLX research report has been reconciled into §3, §6, and §11 (see Pass 6 in `VALIDATION.md`). Open follow-ups derived from that pass:

1. **Accessibility QA** — execute the manual checklist in PPLX §8.3 (unplug mouse, NVDA on Firefox / VoiceOver on Safari, 200% zoom, contrast checker) before the dashboard ships beyond developer preview.
2. **Activity timeline** — promote the audit timeline (PPLX §13) from "future addition" to a placeholder page once the backend exposes a structured activity stream.
3. **Performance regression sweep** — once a real workload exists, validate the budgets in §3 item 13 (initial < 2 s, view transitions < 300 ms, real-time < 100 ms perceived) and add table virtualization on any list that crosses 200 rows (PPLX §9.2).
4. **Status-vocabulary lint** — add a unit-level guard that every status pill renders color + icon + text together (PPLX §4.3, §8.2).

---

*End of spec — v0.3 (close-the-gap reconciled, HEAD `4b742cc`), 2026-04-27.*
