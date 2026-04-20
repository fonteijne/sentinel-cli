# PRD — Sentinel Service & Command Center

> **Status:** Draft v1.0
> **Owner:** fonteijne
> **Target repo:** `sentinel-cli`
> **Date:** 2026-04-17
> **Related docs:** [API.md](./API.md) · [CONFIGURATION.md](./CONFIGURATION.md) · [DEVELOPMENT.md](./DEVELOPMENT.md)

---

## 1. Summary

Sentinel-CLI is today a headless, single-user CLI that orchestrates Claude-Agent-SDK agents (plan → execute → security-review → debrief) against Jira tickets, driving changes through per-ticket git worktrees and GitLab merge requests. This PRD proposes evolving Sentinel into a **long-running FastAPI service with a React command-center UI**, while keeping the CLI as a first-class headless interface.

The service is **single-user, workstation-local** at MVP (same trust boundary as the CLI — it can read `.env` and shell out to `git`/`lando`/`docker`). It is not a multi-tenant SaaS.

### Goals

1. Collapse the tab-switching tax: one pane of glass for tickets, worktrees, running agents, and review comments.
2. Make Sentinel's lifecycle (plan / execute / revise / security / debrief) **legible in real time** — no more `tail -f` across sessions.
3. Give a low-friction "triage → dispatch → review → merge" loop for a developer running 3–8 tickets in parallel.
4. Reuse 100% of existing `src/` managers and clients — the service is an adapter, not a rewrite.

### Non-goals (MVP)

- Multi-user auth, SSO, RBAC.
- Cloud-hosted agent runner (Devin/Jules-style).
- Running Sentinel headlessly on a remote server (beyond single workstation).
- Replacing the CLI.
- Generic "bring your own agent" framework — Sentinel's opinionated pipeline stays.

---

## 2. Context & Inspiration

The "coding-agent command center" space has converged on a small set of primitives, validated by:

- **[Vibe Kanban](https://github.com/BloopAI/vibe-kanban)** — kanban-per-task, worktree-per-card, in-board diff review, live agent terminal output.
- **[Agent Conductor](https://www.reddit.com/r/ClaudeCode/comments/1rg4wdy/i_got_tired_of_tabswitching_between_10_claude/)** — session kanban by status (Active / Waiting / Needs Attention / Done), one-click focus into tmux pane, quick actions (approve/reject/abort), searchable prompt history.
- **[Conductor (Melty Labs)](https://conductor.build/)** — parallel worktrees, diff-first review, automated rebase + PR.
- **[OpenClaw Command Center / ClawPort](https://www.jontsai.com/2026/02/12/building-mission-control-for-my-ai-workforce-introducing-openclaw-command-center)** — org map, cron monitor, memory browser, cost intelligence.
- **[Devin Workspace](https://cognition.ai/blog/devin-annual-performance-review-2025)** — chat + Shell / Browser / Editor / Planner tabs, "Following" live-view.
- **[GitLab Duo Agent Platform](https://about.gitlab.com/gitlab-duo-agent-platform/)** — AI Catalog, native MR review feedback, issue→MR automation.

Sentinel already produces the exact primitives these tools render — tickets, worktrees, sessions, MRs, confidence scores. The dashboard is mostly *"render what already exists + add control endpoints"*.

---

## 3. Users & Workflow

**Primary persona:** Solo developer (the author) running Sentinel on a MacBook Pro against a corporate Jira Server + self-hosted GitLab, driving 3–8 parallel Drupal/Python tickets.

**Top user journeys (MVP):**

| # | Journey | Today (CLI) | With service |
|---|---|---|---|
| 1 | "What's in flight?" | `sentinel status` per project + 10 terminal tabs | Kanban board, one glance |
| 2 | "Start a new ticket" | `sentinel plan PROJ-123` → watch logs | Drag card on board, watch live panel |
| 3 | "Someone left review comments" | Check GitLab email → `sentinel execute PROJ-123 --revise` | Review Inbox shows comment, click "Revise" |
| 4 | "Why did the agent pick that file?" | Grep session JSON | Run viewer tool-call timeline |
| 5 | "Something went sideways" | `sentinel reset --force PROJ-123` | Worktree card → Reset button |

---

## 4. Architecture

### 4.1 Directory layout (chosen option: single repo, reuse `src/` as library)

```
sentinel-cli/
├── src/
│   ├── __init__.py
│   ├── cli.py                   # unchanged
│   ├── agents/                  # unchanged
│   ├── worktree_manager.py      # unchanged
│   ├── jira_client.py           # unchanged
│   ├── gitlab_client.py         # unchanged
│   ├── session_tracker.py       # unchanged — service subscribes to it
│   ├── beads_manager.py         # unchanged
│   ├── ...
│   └── api/                     # NEW — FastAPI service
│       ├── main.py              # app factory, uvicorn entry
│       ├── deps.py              # DI: config, managers, auth
│       ├── events.py            # in-process pub/sub + WebSocket hub
│       ├── routers/
│       │   ├── tickets.py
│       │   ├── worktrees.py
│       │   ├── runs.py
│       │   ├── reviews.py
│       │   ├── projects.py
│       │   ├── config.py
│       │   └── health.py
│       ├── schemas/             # Pydantic models
│       └── adapters/
│           ├── run_orchestrator.py   # launches sentinel workflows as async tasks
│           └── session_stream.py     # tails session_tracker writes → events
├── web/                         # NEW — React + Vite + shadcn/ui frontend
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── pages/
│   │   │   ├── Board.tsx
│   │   │   ├── Worktrees.tsx
│   │   │   ├── Run.tsx
│   │   │   ├── Inbox.tsx
│   │   │   └── Settings.tsx
│   │   ├── components/
│   │   ├── api/                 # generated openapi client
│   │   └── ws/
│   └── ...
├── docker/
│   └── sentinel-service/
│       └── Dockerfile
├── docker-compose.service.yml   # NEW — adds `sentinel-api` + static web
└── pyproject.toml               # +fastapi, uvicorn, websockets, sse-starlette
```

### 4.2 Runtime topology

```
┌──────────────────────────────────────┐      ┌────────────────────────────┐
│  Browser (localhost:3939/ui)         │◄────►│  FastAPI  (localhost:3939) │
│  React + Vite + shadcn + TanStack Q  │ WS   │  Uvicorn, single worker    │
└──────────────────────────────────────┘      │  ├─ routers/*              │
                                              │  ├─ run_orchestrator (bg)  │
                                              │  │   ├─ asyncio.Task/run   │
                                              │  │   └─ spawns CLI flow    │
                                              │  ├─ session_stream (bg)    │
                                              │  │   └─ inotify on         │
                                              │  │      ~/.sentinel/       │
                                              │  └─ events.py (pubsub)     │
                                              │                            │
                                              │  Uses existing src/*       │
                                              │  (managers, clients)       │
                                              └─────────────┬──────────────┘
                                                            │
                             ┌───────────────┬──────────────┼──────────────┬──────────────┐
                             ▼               ▼              ▼              ▼              ▼
                       Jira/GitLab     Git worktrees   Claude Agent   Lando/DooD     ~/.sentinel/
                       (existing       (existing       SDK           (existing)     sessions.json
                        clients)        manager)                                    beads.db
```

Key points:
- **Same process, same trust boundary as CLI.** No network auth at MVP — bound to `127.0.0.1`.
- **Workflows run in-process** as `asyncio` tasks; each run = one async task + a subprocess for `git`/`lando` where needed. No Celery/Redis at MVP.
- **Event bus is in-process** (`asyncio.Queue` fanout). Any write to `session_tracker` / `beads_manager` publishes to the bus; WebSocket clients subscribe.
- **State lives where it lives today** — `~/.sentinel/sessions.json`, beads `.db`, worktrees on disk. The service adds a thin **SQLite cache** for aggregate queries (ticket board state) but source-of-truth is unchanged.

### 4.3 Data model additions

```sql
-- sqlite: ~/.sentinel/service.db
CREATE TABLE runs (
  id               TEXT PRIMARY KEY,              -- uuid4
  ticket_key       TEXT NOT NULL,
  project          TEXT NOT NULL,
  workflow         TEXT NOT NULL,                 -- plan|execute|revise|debrief|security_review
  status           TEXT NOT NULL,                 -- queued|running|awaiting_review|succeeded|failed|cancelled
  started_at       TIMESTAMP,
  ended_at         TIMESTAMP,
  session_path     TEXT,                          -- path under ~/.sentinel/sessions/
  worktree_path    TEXT,
  mr_url           TEXT,
  confidence       REAL,
  token_usage      JSON,
  cost_usd         REAL,
  error            TEXT
);
CREATE INDEX idx_runs_ticket ON runs(ticket_key);
CREATE INDEX idx_runs_status ON runs(status);

CREATE TABLE review_inbox (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  source           TEXT NOT NULL,                 -- jira|gitlab
  ticket_key       TEXT NOT NULL,
  mr_iid           INTEGER,
  comment_id       TEXT NOT NULL,
  author           TEXT,
  body             TEXT,
  created_at       TIMESTAMP,
  state            TEXT NOT NULL,                 -- open|actioned|dismissed
  run_id           TEXT,                          -- fk runs(id) when actioned
  UNIQUE(source, comment_id)
);

CREATE TABLE board_prefs (
  key TEXT PRIMARY KEY, value JSON
);
```

`runs` is populated by the orchestrator; `review_inbox` by a lightweight poller hitting `gitlab_client.get_mr_discussions()` and `jira_client.get_comments()` every 60s per project with Sentinel-authored MRs.

---

## 5. MVP Scope (v1)

Four modules, all agreed with user:

### 5.1 Module A — Ticket Queue & Pipeline Board

**What:** A kanban board whose columns are Sentinel lifecycle states, not Jira statuses.

| Column | Derived from |
|---|---|
| Backlog (Sentinel-eligible) | `jira_client.search_tickets(filter="ready-for-sentinel")` |
| Planning | Active run with `workflow=plan` |
| Plan ready (MR open, no review) | Run `succeeded` + MR has draft plan, no discussions |
| Executing | Active run with `workflow=execute` |
| Awaiting review | MR open, has unresolved discussions |
| Revising | Active run with `workflow=revise` |
| Security review | Active run with `workflow=security_review` |
| Done | MR merged or ticket moved to done |

**Card shows:** ticket key + title, assignee avatar, confidence score (colored), last run age, MR link, quick actions `Plan` / `Execute` / `Revise` / `Debrief` / `Reset`.

**Interactions:**
- Drag card column → forbidden (columns are computed). Instead, dedicated action buttons map to CLI verbs.
- Click card → slide-over with ticket description (ADF-rendered via existing parser), attachments, run history, MR status.

**APIs:**
- `GET /api/tickets?project=&state=` → list + board grouping.
- `POST /api/tickets/{key}/plan|execute|revise|debrief|reset` → enqueue run.
- `WS /ws/board` → push state transitions.

**Reuses:** `jira_client`, `jira_server_client`, `adf_parser`, `beads_manager`, `session_tracker`, `config_loader`.

### 5.2 Module B — Worktree Manager UI

**What:** A companion view listing every worktree on disk with git signals and one-click ops.

**Shows per row:** project, ticket key, branch, path, `git status --porcelain` counts, ahead/behind vs main, last-run timestamp, linked MR, size on disk.

**Actions:** Open in VS Code / Cursor (`code --folder-uri`), Copy path, `Reset` (maps to `sentinel reset`), `Archive` (move under `worktrees/_archive/`), `Prune stale`.

**APIs:**
- `GET /api/worktrees` → list.
- `POST /api/worktrees/{key}/reset` `{force: bool, keep_branch: bool}`.
- `POST /api/worktrees/{key}/open-in-editor`.
- `DELETE /api/worktrees/{key}`.

**Reuses:** `worktree_manager` (all existing methods), plus new `.git_status()` helper that shells out to `git -C <path> status --porcelain=v2 --branch`.

### 5.3 Module C — Live Run / Session Viewer

**What:** The "Following" panel. For any active or historical run, show a live timeline of agent activity.

**Layout (three-pane):**
1. **Left:** phase timeline — Plan → Execute → Security → Debrief, with iteration counters, token usage per phase.
2. **Center:** tool-call stream (Read/Write/Grep/Bash/…), collapsed by default, expand for args + output preview. Filterable.
3. **Right:** attachments (screenshots, logs), linked MR diff (pulled via `gitlab_client`), confidence report.

**Transport:** WebSocket at `/ws/runs/{run_id}` streams events parsed from `session_tracker` writes via inotify/watchdog. Backfill via `GET /api/runs/{id}/events?since=…`.

**Controls:** `Pause`, `Resume`, `Abort` (SIGTERM to run task), `Send note` (appends to run's scratch file the agent reads between iterations — requires small hook in `agents/base`).

**APIs:**
- `GET /api/runs?ticket=&status=` → list.
- `GET /api/runs/{id}` → detail.
- `GET /api/runs/{id}/events` → paginated history.
- `WS /ws/runs/{id}` → live tail.
- `POST /api/runs/{id}/abort|pause|resume|note`.

**Reuses:** `session_tracker`, `attachment_manager`, `confidence_evaluator` output, `gitlab_client.get_mr_changes()`.

### 5.4 Module D — Jira + GitLab Review Inbox

**What:** A unified list of comments on Sentinel-authored MRs and their linked Jira tickets, with one-click "Revise".

**Shows:** source icon, ticket/MR, author, snippet, created_at, state.
**Bulk actions:** Dismiss, Mark read.
**Per-row actions:** `Revise with this feedback` (passes the comment body into `execute --revise` as an additional instruction), `Reply via agent` (uses `functional_debrief` or a short "acknowledge" prompt), `Open in GitLab/Jira`.

**Polling:** Background task every 60s, only for MRs touched in the last 30 days, using ETag/If-Modified-Since where possible. Configurable in `config.local.yaml`.

**APIs:**
- `GET /api/inbox?state=open` → list.
- `POST /api/inbox/{id}/revise` → kicks off `revise` run.
- `POST /api/inbox/{id}/dismiss`.
- `WS /ws/inbox` → push new items.

**Reuses:** `gitlab_client.get_mr_discussions()`, `jira_client.get_comments()`.

---

## 6. Phase 2 Scope (all user-approved for planning)

Designed so each module can ship independently behind a feature flag.

### 6.1 Agent & Prompt Studio
- Browse `prompts/` tree with role overlays (e.g. `drupal_developer` vs `python_developer`).
- Live diff between base and project overlay.
- Edit in browser (Monaco), save to repo (writes through `config_loader`, triggers hot-reload).
- Agent profiles UI à la Vibe Kanban: model, temperature, max iterations, iteration budget per phase. Backed by a new `~/.sentinel/agent_profiles.yaml`.

### 6.2 Observability & Cost Analytics
- Per-project and per-agent dashboards: runs over time, success rate per phase, mean iterations, mean confidence, token/cost breakdown by model.
- Alerts: "cost > $X/day", "confidence < threshold N times in a row".
- Export to CSV.

### 6.3 Project / Profile / Config Admin
- CRUD for `projects_config.yaml` (currently CLI-only).
- Run stack-profiler against a repo and inspect the result before saving a project profile.
- Credential health check: ping Jira / GitLab / Claude API, surface missing env keys (values never displayed).
- YAML diff + validator before save.

### 6.4 Scheduling / Jira auto-poll + Container Control
- Cron-style rules: "every 10 min, `plan` any Jira ticket tagged `sentinel-ready` and unassigned to a run".
- Container control: live list of `docker ps` entries from `compose_runner`, status, logs tail, stop/restart, per-ticket env inspection. Reuses `environment_manager` + `lando_translator`.

---

## 7. API Surface (MVP consolidated)

OpenAPI will be auto-generated by FastAPI; this is the contract summary.

```
# Board
GET    /api/tickets?project=&state=
GET    /api/tickets/{key}
POST   /api/tickets/{key}/plan          { notes? }
POST   /api/tickets/{key}/execute       { revise?: bool, notes? }
POST   /api/tickets/{key}/debrief
POST   /api/tickets/{key}/reset         { force?: bool }

# Worktrees
GET    /api/worktrees
POST   /api/worktrees/{key}/reset       { force, keep_branch }
POST   /api/worktrees/{key}/open-in-editor   { editor: vscode|cursor }
DELETE /api/worktrees/{key}

# Runs
GET    /api/runs?ticket=&status=&since=
GET    /api/runs/{id}
GET    /api/runs/{id}/events?since=&limit=
POST   /api/runs/{id}/abort|pause|resume
POST   /api/runs/{id}/note              { text }

# Inbox
GET    /api/inbox?state=
POST   /api/inbox/{id}/revise
POST   /api/inbox/{id}/dismiss

# Projects / Config
GET    /api/projects
GET    /api/config
GET    /api/health                      # jira/gitlab/claude/docker status

# Realtime
WS     /ws/board
WS     /ws/runs/{id}
WS     /ws/inbox
```

Authentication MVP: none, bound to `127.0.0.1`. Phase 2: single shared token from `SENTINEL_SERVICE_TOKEN` env or macOS keychain, required header `Authorization: Bearer`.

---

## 8. Frontend

**Stack:** React 18 + Vite + TypeScript + **[shadcn/ui](https://ui.shadcn.com/)** + Tailwind + **TanStack Query** + **TanStack Router** + `zustand` (client-only UI state) + `openapi-typescript` (generated client).

**Design language:** "Linear-dark" — compact, keyboard-first, no animations on data-heavy views.

**Pages (MVP):**
1. `/board` — Module A
2. `/worktrees` — Module B
3. `/runs/:id` — Module C (also a slide-over from other pages)
4. `/inbox` — Module D
5. `/settings` — basic read-only health view of `config.yaml` merged

**Keyboard shortcuts (first-class):**
- `g b` → board, `g w` → worktrees, `g i` → inbox
- `p` / `e` / `r` / `d` on a selected card → plan/execute/revise/debrief
- `/` → global search (ticket key, branch, MR iid)

**Build & serve:** `vite build` → `web/dist/`, FastAPI mounts at `/ui` via `StaticFiles`. Dev mode: Vite dev server on `5173` with API proxy to `3939`.

---

## 9. Packaging & Operations

### 9.1 How you run it

```fish
# dev
poetry install --with service
poetry run sentinel-service --reload
# then, in web/:
pnpm install && pnpm dev

# prod-ish (single workstation)
poetry run sentinel-service  # serves API + built UI at http://127.0.0.1:3939/ui
```

A new `sentinel-service` console script is added in `pyproject.toml`.

### 9.2 Docker

`docker-compose.service.yml` adds one service (`sentinel-api`) that mounts:
- `~/.sentinel/` (state)
- the workspace root where worktrees live
- the docker socket (for DooD parity with existing `compose_runner`)

Optional — useful if the user already runs Sentinel in a container. Default flow remains native.

### 9.3 Config

New keys in `config.yaml`:

```yaml
service:
  host: 127.0.0.1
  port: 3939
  ui_enabled: true
  poll:
    review_inbox_seconds: 60
    jira_board_seconds: 120
  db:
    path: ~/.sentinel/service.db
  editor:
    default: cursor       # or vscode
```

---

## 10. Non-functional requirements

| Concern | Target |
|---|---|
| **Perf: board load** | < 500 ms cold (cached), < 1.5 s with live Jira fetch for 100 tickets |
| **Perf: run event latency** | < 500 ms from `session_tracker.write` → UI render |
| **Reliability** | Service restart loses in-flight runs (documented). Completed runs survive via `runs` table. |
| **Concurrency** | Up to 10 concurrent active runs (matches current workstation reality). |
| **Logging** | Structured JSON logs at `~/.sentinel/logs/service.log`, rotated. Existing session logs untouched. |
| **Observability** | `/metrics` Prometheus endpoint (Phase 2). MVP: `/api/health` + per-run telemetry in `runs`. |
| **Security (MVP)** | Loopback-only, CORS locked to `http://localhost:5173` and `http://127.0.0.1:3939`. Rejects `Origin` mismatch. No secrets in responses. |

---

## 11. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| `session_tracker` writes are not atomic / partial JSON during live tailing | Use `watchdog` + debounced re-read of whole file; parse defensively. Consider adding `.write_event(dict)` that appends to a JSONL sidecar for cleaner streaming. |
| Long-running agent runs block event loop | Each run wrapped in `asyncio.to_thread` for sync calls; subprocesses use `asyncio.create_subprocess_exec`. |
| Jira/GitLab polling hammers the server | ETag + 60 s default + only active projects + backoff on 429. |
| Secrets exposed via API | `/api/config` redacts keys matching `.*(TOKEN\|KEY\|SECRET\|PASSWORD).*`. |
| Drift between CLI behavior and service calls | Every service action is implemented by calling the same manager method the CLI uses. Integration test suite runs both paths against a fixture repo. |
| Browser is a more tempting attack surface than CLI | Loopback-only binding; in Phase 2 add shared-token + CSRF; document that the service is not to be exposed to the LAN. |

---

## 12. Milestones & Rough Sequencing

Assume solo developer, evenings + weekends.

| Milestone | Scope | Done when |
|---|---|---|
| **M0 — Skeleton** | `src/api/` FastAPI app, `/api/health`, `web/` Vite scaffold, `sentinel-service` entrypoint, `service.db` migrations | App boots, health returns `{jira, gitlab, claude, docker: ok}` |
| **M1 — Worktree Manager (Module B)** | Worktrees list, git status, reset/open actions | `sentinel status` deprecated in favor of UI (CLI still works) |
| **M2 — Runs persistence + viewer (Module C, read-only)** | `runs` table populated by orchestrator, run list, event history (no live) | Can replay any past run |
| **M3 — Live run tailing** | WebSocket event stream, abort/note | Launching `sentinel execute` from CLI shows up live in UI |
| **M4 — Ticket Board (Module A)** | Board view, action buttons, slide-over | All four action verbs work from UI |
| **M5 — Review Inbox (Module D)** | Poller + inbox UI + revise flow | Commenting on a Sentinel MR surfaces in UI within 60s |
| **M6 — Hardening** | CORS/origin checks, redaction, logs, docker-compose, docs | Ready to dogfood exclusively via UI |
| **Phase 2** | Agent Studio / Observability / Config Admin / Scheduling | Separate PRDs |

---

## 13. Open Questions

1. Do we keep `beads_manager` as the run state store, or migrate to `runs` table? Proposal: leave beads untouched, `runs` is an index over beads + session files.
2. Should the service run `lando`/`compose` on behalf of the user from the UI at MVP, or stay CLI-only for container ops? Proposal: CLI-only at MVP; Phase 2 surfaces it.
3. Do we want a "headless agent approval" flow (Claude Code Router style, skip prompts) exposed from the UI, or keep the CLI's current prompts? Proposal: keep current behavior; approval via `POST /api/runs/{id}/note` for now.
4. VS Code / Cursor integration — is a URL scheme sufficient, or do we also want a companion VS Code extension (Agent Conductor / Agent Flow style)? Proposal: URL scheme MVP, extension considered in Phase 2.
5. Should the service also own Jira ticket *creation* (e.g. "File a bug from this run's failure")? Proposal: no at MVP, yes in Phase 2 once Inbox is battle-tested.

---

## 14. References

- [Vibe Kanban · BloopAI](https://github.com/BloopAI/vibe-kanban) — kanban + worktree pattern
- [Agent Conductor (Taitopia)](https://marketplace.visualstudio.com/items?itemName=Taitopia.agent-conductor) — VS Code real-time dashboard
- [Agent Conductor (Reddit post)](https://www.reddit.com/r/ClaudeCode/comments/1rg4wdy/i_got_tired_of_tabswitching_between_10_claude/) — session kanban, one-click focus, prompt history
- [Addy Osmani — The Code Agent Orchestra](https://addyosmani.com/blog/code-agent-orchestra/) — taxonomy of orchestrators
- [OpenClaw Command Center](https://www.jontsai.com/2026/02/12/building-mission-control-for-my-ai-workforce-introducing-openclaw-command-center) — org map, cron monitor, cost intelligence
- [Claude Code Agent Teams](https://code.claude.com/docs/en/agent-teams) — task list + mailbox model
- [Devin Annual Performance Review 2025](https://cognition.ai/blog/devin-annual-performance-review-2025) — Workspace tabs & "Following" UX
- [GitLab Duo Agent Platform](https://about.gitlab.com/gitlab-duo-agent-platform/) — AI catalog + MR-native review
- [FastAPI + MCP enterprise patterns](https://www.mintmcp.com/blog/build-enterprise-ai-agents) — async tool execution patterns
- [AI + GitLab MR review tooling](https://www.getpanto.ai/blog/ai-code-review-tools-gitlab-merge-requests) — comment-triggered revision flows

---

*End of PRD v1.0*
