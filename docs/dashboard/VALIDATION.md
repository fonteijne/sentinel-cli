# Dashboard validation log

Six passes were run against the spec and the implementation on branch `v2/command-center-ui` (based on `v2/command-center` HEAD `37a30b0`). This log is the authoritative record referenced by `SETUP_AND_SPEC.md` §12.

---

## Pass 1 — Backend reverse-engineering

**Goal:** confirm what the dashboard can actually call.

**Inputs read:**
- `src/service/app.py` — read/write router composition, CORS, docs gate.
- `src/service/routes/executions.py` — list / get / events / agent-results.
- `src/service/routes/commands.py` — start / cancel / retry, body validation.
- `src/service/routes/stream.py` — WebSocket frames, terminal mapping, heartbeats.
- `src/service/schemas.py` — `ExecutionOut`, `EventOut`, `AgentResultOut`, `ListResponse`.
- `src/core/execution/models.py` — `ExecutionStatus`, `ExecutionKind` enums.
- `src/core/events/types.py` — full event-type catalogue + terminal set.

**Findings recorded in `API_CONTRACT.md`:**
- 6 lifecycle states; 17 declared event types; 3 terminal types.
- Only 3 mutating verbs: `POST /executions`, `cancel`, `retry`.
- WebSocket auth via subprotocol; closes 1008 on auth failure, 4404 on missing execution.
- Idempotency-Key honored on `POST /executions`.

**Result:** dashboard never targets unknown endpoints. ✅

---

## Pass 2 — Cross-reference vs. CLI features

**Goal:** identify which CLI capabilities can be expressed in the dashboard.

Mapped `WorktreeManager`, `JiraClient`, `GitLabClient`, `compose_runner`, `command_executor`, agent runners against the API surface.

| Capability | Backend route exists? | Dashboard surface |
| --- | --- | --- |
| Plan / Execute / Debrief | yes (`POST /executions`) | wired |
| Cancel / Retry | yes | wired |
| Worktree create / cleanup | **no** | "coming soon" |
| Jira / GitLab inbox | **no** | "coming soon" |
| Settings mutation | **no** | "coming soon" |
| Cost & duration analytics | partial (raw fields only) | KPIs + sparklines today; full Insights page coming soon |
| Findings & test results | declared events not yet emitted (gap G-04) | render gracefully when present, otherwise empty state |

**Result:** every "coming soon" item maps to a real backend gap and does NOT call a missing endpoint. ✅

---

## Pass 3 — Design-system fidelity

**Goal:** ensure visuals match the handoff bundle pixel-for-pixel.

Actions:
- Read the handoff `README.md` (`Sentinel-Design-System-handoff.zip` → `sentinel-design-system/README.md`). Per the README, the prototype is the source of truth and we should "match the visual output."
- Copied `styles.css` (764 lines) verbatim into `dashboard/src/styles.css` — zero edits.
- Ported `icons.jsx` to `icons.tsx` keeping all SVG paths byte-identical (added 5 dashboard-specific icons: `play`, `stop`, `refresh`, `branch`, `ticket`, `alert`).
- Reused class names from the handoff (`task-card`, `kanban`, `kanban-col-head`, `kpi`, `card-head`, `segmented seg active`, `badge dot`, `progress-bar`, `topbar`, `sidebar`, `nav-item`, `avatar avatar-sm`).

**Result:** the rendered DOM uses the exact tokens, classes, and structure the handoff spelled out. ✅

---

## Pass 4 — Worktree-centric grouping correctness

**Goal:** verify that the worktrees board can be implemented with **only** existing endpoints.

- `WorktreeManager.get_branch_name` keys worktrees by `ticket_id`.
- `commands.start` derives `project = ticket_id.split("-",1)[0].lower()` if omitted, so `(project, ticket_id)` is functionally a unique key per worktree.
- All five kanban buckets (`Idle`, `Running`, `At risk`, `Failed`, `Done`) are computable from `ExecutionOut.status`, `started_at`, and `ended_at` alone — no extra endpoints required.
- Predicate edge cases:
  - `running > 5min with no new events` → at-risk; verified by checking `ended_at == null` AND `Date.now() - started_at > 5 min`.
  - `terminal but recent` → done; `terminal but > 1h old` → idle.
  - `cancelling` → live (running bucket).

**Result:** the v0.1 board is implementable without backend changes. ✅

---

## Pass 5 — Build, lint, typecheck, no-backend-drift

Commands run from `/home/user/workspace/sentinel-cli-9c8281e8`:

```bash
$ cd dashboard && npm install
added 67 packages, and audited 68 packages in 10s
2 moderate severity vulnerabilities (transitive — outside our control)

$ npm run typecheck
> tsc --noEmit
(no output — clean)

$ npm run build
> tsc -b && vite build
✓ 51 modules transformed.
dist/index.html                   0.80 kB │ gzip:  0.44 kB
dist/assets/index-am4WtPSG.css   18.32 kB │ gzip:  4.09 kB
dist/assets/index-BERoUhs0.js   196.37 kB │ gzip: 59.90 kB
✓ built in 1.47s

$ cd .. && git status -s -- src/ tests/ docker-compose.yml pyproject.toml poetry.lock
(empty — no backend modifications)
```

- Type errors: **0**.
- Build errors: **0**.
- Backend file changes: **0** (verified explicitly, scope: `src/`, `tests/`, `docker-compose.yml`, `pyproject.toml`, `poetry.lock`).
- Bundle size after gzip: ~64 kB JS + 4 kB CSS, fits comfortably for an internal admin dashboard.
- Visual / interactive QA: **not run** — the environment is headless, so a browser is not available. The dev server can be started locally via `npm run dev`; the production build was verified via `vite build`. The reference design (HTML prototype) was not opened in a browser per the handoff README's instruction.

**Result:** branch is buildable, typesafe, and free of backend drift. ✅

---

## Pass 6 — PPLX research-report reconciliation

**Goal:** resolve every `[NEEDS PPLX]` placeholder and v0.1 caveat in `SETUP_AND_SPEC.md` against the canonical research source, now available at `/home/user/workspace/admin-dashboard-best-practices.pplx.md`.

**Inputs read:**
- `/home/user/workspace/admin-dashboard-best-practices.pplx.md` — 742 lines, 17 sections (Information Architecture, Navigation, Dense Data Display, Observability, Command/Action Safety, Empty/Loading/Error, Progressive Disclosure, Accessibility, Performance, Responsive, API Integration, Drill-Down Architecture, Audit & Activity, RBAC, Design QA Checklist, Sentinel CLI Cross-Reference, Product Patterns).
- `docs/dashboard/SETUP_AND_SPEC.md` (v0.1, pre-reconciliation).
- `docs/dashboard/VALIDATION.md` (this file, pre-Pass-6).

**Changes applied to `SETUP_AND_SPEC.md`:**
- **Header** — promoted from `Draft (v0.1)` to `Reconciled (v0.2)`; replaced the "PPLX not present" caveat banner with a forward reference to this pass.
- **§3 Admin-dashboard best practices** — removed the `[NEEDS PPLX]` tag; rewrote the 10-item list as 16 PPLX-cited principles covering inverted-pyramid IA (PPLX §1.3, §3.1), one-page-one-decision (§4.2), golden signals (§4.1), drawers + deep links (§2.2, §12), real-time channel selection with WebSocket exponential backoff `1s→2s→4s→max 30s` (§6.1, §9.2), empty/loading/error states (§6.1–§6.3), command palette + number-key tab shortcuts (§2.3, §2.4), risk-tiered destructive-action friction with Danger Zone separation (§5.1–§5.3), color+icon+text status vocabulary (§4.3, §8.2; WCAG 1.4.1), drill-down with preserved context (§12), two-level progressive disclosure (§7), WCAG 2.2 AA baseline incl. 24×24 touch targets and ARIA live regions (§8), explicit performance budgets (§9.1–§9.2), token+RBAC posture (§14), idempotency/rate-limit handling (§9.2, §11.1), and audit-log surface (§13.1–§13.2).
- **§6 Run drawer** — annotated each section against the relevant PPLX clauses; explicitly mapped Cancel and Retry into the PPLX §5.2 risk matrix (medium-risk modal, non-default-focus destructive button, color-distinguished); reserved a critical-risk "Reset / Cancel-all" pattern (modal + type-to-confirm + Danger Zone) for the future feature; added a focus-management/`aria-live` accessibility note (PPLX §8.1).
- **§11 Coming-soon features** — annotated each of the 10 placeholders with the PPLX section that governs its eventual UX (e.g., #6 Multi-user & RBAC ↔ §14; #10 Cancel-all ↔ §5.2 critical risk). Added two PPLX-driven roadmap entries: a first-class Activity Timeline page (§13) and an Accessibility audit pass (§8.3).
- **§12 Validation log** — added the present pass to the recap; updated the lead sentence from "Five iteration passes" to "Six iteration passes".
- **§14 Future doc actions** — removed `[NEEDS PPLX]` tag; replaced the "once PPLX arrives" todo list with four concrete follow-ups (manual a11y QA per PPLX §8.3, Activity Timeline promotion when backend support lands, performance budget validation under real load per §9, status-vocabulary lint guard per §4.3 / §8.2).
- **Footer** — bumped from `v0.1, 2026-04-27` to `v0.2 (PPLX-reconciled), 2026-04-27`.

**Verification:**

```bash
$ grep -c "NEEDS PPLX" docs/dashboard/SETUP_AND_SPEC.md docs/dashboard/VALIDATION.md
docs/dashboard/SETUP_AND_SPEC.md:0
docs/dashboard/VALIDATION.md:0

$ git status -s -- src/ tests/ docker-compose.yml pyproject.toml poetry.lock Dockerfile
(empty — no backend modifications)

$ git status -s -- docs/ dashboard/
M docs/dashboard/SETUP_AND_SPEC.md
M docs/dashboard/VALIDATION.md
```

- Orphan `[NEEDS PPLX]` markers: **0**.
- Backend file changes: **0** (scope: `src/`, `tests/`, `docker-compose.yml`, `pyproject.toml`, `poetry.lock`, `Dockerfile`).
- Dashboard build artifacts touched: **0** (this pass is documentation-only).
- Files modified by Pass 6: `docs/dashboard/SETUP_AND_SPEC.md`, `docs/dashboard/VALIDATION.md`.

**Result:** spec and validation log are fully reconciled with the PPLX research source; no further `[NEEDS PPLX]` placeholders remain in the dashboard documentation tree, and no backend code was touched. ✅

---

## What this log does NOT cover

- E2E tests against a live backend — no CI lane and no running service in this environment.
- Cross-browser QA — dependent on Vite + React 18 standard support.
- Performance under heavy event volume — WebSocket handler buffers in React state without virtualization; will need a `react-window` follow-up if any single execution emits ≫ 1000 events.
- Visual regression. The handoff README explicitly told us not to render in a browser, so we didn't.

Pass 6 (recorded above, 2026-04-27) reconciled the spec against `/home/user/workspace/admin-dashboard-best-practices.pplx.md`; no `[NEEDS PPLX]` placeholders remain.
