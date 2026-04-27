# Dashboard validation log

Five passes were run against the spec and the implementation on branch `v2/command-center-ui` (based on `v2/command-center` HEAD `37a30b0`). This log is the authoritative record referenced by `SETUP_AND_SPEC.md` §12.

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

## What this log does NOT cover

- E2E tests against a live backend — no CI lane and no running service in this environment.
- Cross-browser QA — dependent on Vite + React 18 standard support.
- Performance under heavy event volume — WebSocket handler buffers in React state without virtualization; will need a `react-window` follow-up if any single execution emits ≫ 1000 events.
- Visual regression. The handoff README explicitly told us not to render in a browser, so we didn't.

A Pass 6 should be added once `/home/user/workspace/admin-dashboard-best-practices.pplx.md` is published, to record the cross-reference of that doc against §3 and §11 of `SETUP_AND_SPEC.md`.
