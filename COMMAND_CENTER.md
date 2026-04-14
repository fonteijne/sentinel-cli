# Sentinel Command Center — Development Status

> Reference file for tracking Command Center dashboard features.
> Update this file when new CLI features are added or dashboard capabilities change.

---

## Dashboard Pages

### Overview
- [x] KPI stat cards wired to real backend (active projects, active tickets, security score, agent runs today)
- [x] Recent activity feed from real CLI execution history (`GET /api/activity`)
- [x] Agent runs per week bar chart
- [x] Plan confidence score line chart with threshold indicator
- [x] System health indicators (Jira, GitLab, LLM, SSH, Beads) from real `/api/status`
- [x] Quick action buttons navigate to actual pages (Tickets, Settings, Projects)
- [x] System Health refresh triggers real `sentinel validate` via `/api/status/validate`
- [x] Zero-state display when no data — no fake demo numbers

### Projects
- [x] Project grid with card layout (key, name, git URL, branch, stack type, worktree count)
- [x] Add new project form (writes to config.yaml via API)
- [x] Edit existing project form
- [x] Delete project with confirmation
- [x] Generate project profile action (triggers `sentinel projects profile`)
- [x] Search/filter projects
- [x] Active worktree count from real git data
- [x] Empty state when no projects configured

### Tickets (Kanban Pipeline)
- [x] Four-column kanban board: Plan → Execute → Review → Done
- [x] Ticket cards with ID, summary, priority badge, labels
- [x] Click-to-select ticket detail panel with project/branch/worktree info
- [x] Actions: Generate/Revise Plan, Execute Implementation, Run Functional Debrief (all pass project context)
- [x] Search/filter tickets
- [x] Per-column ticket counts
- [x] Worktree-based ticket sync — scans active git worktrees to derive live ticket data
- [x] Phase detection: plan file presence, memory/session artifacts, code diffs vs default branch
- [x] "Start New Ticket" modal — select project + ticket ID → creates worktree via API
- [x] "Reset Ticket" action — removes worktree + branch
- [x] "Remove Worktree" action — cleans up worktree only
- [x] Connected/Offline status badge
- [x] Sync button for manual worktree refresh
- [x] Project filter dropdown (multi-project support)
- [x] Loading skeleton during sync
- [x] Empty state when no active tickets (no fake mock data)

### Agents
- [x] Agent cards from real config.yaml (plan_generator, python_developer, security_review, confidence_evaluator, project_profiler)
- [x] Model name and temperature display per agent
- [x] Specialization tags from real config
- [x] Dynamic model usage breakdown computed from agent config
- [x] Run history with success/failure indicators
- [x] Success rate percentage
- [x] Click-to-select run history detail panel
- [x] Weekly runs bar chart
- [x] Model usage breakdown

### Security
- [x] Security score calculation (weighted by severity)
- [x] Severity breakdown stat cards (Critical, High, Medium, Low)
- [x] Findings list with severity filter tabs
- [x] Finding detail panel (ID, OWASP rule, ticket link, file location, description)
- [x] Mark Resolved / Ignore actions
- [x] Empty state when no findings — explains how to trigger security reviews
- [x] Info banner explaining security review workflow

### Settings
- [x] Service connection cards (Jira, GitLab, LLM, SSH, Beads) with real status from `/api/status`
- [x] Test connection triggers real `sentinel validate` via `/api/status/validate`
- [x] Validate All connections button
- [x] config.yaml viewer loaded from real file via `/api/config`
- [x] Edit + Save config (writes back to disk)
- [x] Environment variables reference table
- [x] Auth management section (LLM, Jira, GitLab)

### Logs
- [x] Real-time log stream via WebSocket (`ws://host/ws/logs`)
- [x] Log level filter buttons (ALL, INFO, WARNING, ERROR, DEBUG)
- [x] Text search/filter
- [x] Color-coded log levels
- [x] Timestamped entries with source module identification
- [x] "Waiting for sentinel commands..." when idle
- [x] Auto-scroll, pause/resume, and clear actions

---

## Backend API Endpoints

### Health & Status
- [x] `GET /api/health` — Health check
- [x] `GET /api/status` — System status with integration health and KPI stats
- [x] `GET /api/status/validate` — Validate all API connections

### Projects
- [x] `GET /api/projects` — List all projects
- [x] `POST /api/projects` — Add new project
- [x] `PUT /api/projects/{key}` — Update project
- [x] `DELETE /api/projects/{key}` — Remove project
- [x] `POST /api/projects/{key}/profile` — Generate project profile

### Tickets
- [x] `GET /api/tickets` — List all active tickets from git worktrees (with phase detection)
- [x] `GET /api/tickets/{ticket_id}/info` — Get ticket info from Jira
- [x] `POST /api/tickets/{ticket_id}/plan` — Generate/revise plan (accepts `?project=`)
- [x] `POST /api/tickets/{ticket_id}/execute` — Execute implementation (accepts `?project=`)
- [x] `POST /api/tickets/{ticket_id}/debrief` — Run functional debrief (accepts `?project=`)
- [x] `POST /api/tickets/{ticket_id}/worktree` — Create worktree for ticket (bare clone + branch)
- [x] `DELETE /api/tickets/{ticket_id}/worktree` — Remove worktree
- [x] `POST /api/tickets/{ticket_id}/reset` — Full reset (worktree + branch)

### Configuration
- [x] `GET /api/config` — Read config.yaml
- [x] `PUT /api/config` — Update config.yaml
- [x] `GET /api/agents` — Get agent configurations

### Activity
- [x] `GET /api/activity` — Recent activity log from CLI executions (in-memory buffer)

### Real-time
- [x] `WS /ws/logs` — WebSocket log stream from CLI process

---

## Infrastructure

### Docker
- [x] Multi-stage Dockerfile (Node build → Python runtime)
- [x] docker-compose.dashboard.yml with dashboard + sentinel services
- [x] Shared config volume between CLI and dashboard containers
- [x] Port 8080 (backend API + static frontend)

### Frontend Stack
- [x] React 18 + Vite 5 + Tailwind CSS v3
- [x] Recharts for data visualization
- [x] Axios for API communication
- [x] React Router for navigation
- [x] Lucide React for icons
- [x] clsx for conditional class names

### Design System
- [x] Dark theme: deep navy (#0a0e27), electric blue (#3b82f6), cyan (#06b6d4)
- [x] Glass morphism cards with subtle borders and backdrop blur
- [x] Inter font family
- [x] Custom SVG shield logo with gradient
- [x] Responsive sidebar navigation with collapse toggle
- [x] Status indicators with glow effects
- [x] Page enter animations

---

## CLI Commands Mapped to Dashboard

| CLI Command | Dashboard Location | Status |
|---|---|---|
| `sentinel plan <ticket>` | Tickets → Detail → Generate/Revise Plan | ✅ |
| `sentinel execute <ticket>` | Tickets → Detail → Execute Implementation | ✅ |
| `sentinel execute --revise` | Tickets → Detail → Execute Implementation | ✅ |
| `sentinel debrief <ticket>` | Tickets → Detail → Run Functional Debrief | ✅ |
| `sentinel info <ticket>` | Tickets → Ticket cards display info | ✅ |
| `sentinel status` | Overview → KPI cards + System Health | ✅ |
| `sentinel validate` | Settings → Validate All / Overview → Quick Actions | ✅ |
| `sentinel reset <ticket>` | Tickets → Reset Ticket / Remove Worktree | ✅ |
| `sentinel reset --all` | — | ⬜ Planned |
| `sentinel auth login` | — | ⬜ Planned |
| `sentinel auth logout` | — | ⬜ Planned |
| `sentinel auth status` | Settings → LLM connection status | ✅ |
| `sentinel auth configure` | — | ⬜ Planned |
| `sentinel projects list` | Projects page | ✅ |
| `sentinel projects add` | Projects → Add Project | ✅ |
| `sentinel projects edit` | Projects → Edit Project | ✅ |
| `sentinel projects remove` | Projects → Delete Project | ✅ |
| `sentinel projects profile` | Projects → Generate Profile | ✅ |

---

## Upcoming Features

- [ ] Auth management page (login/logout/configure LLM provider)
- [x] ~~Reset ticket action from Tickets detail panel~~ (implemented: Reset Ticket + Remove Worktree buttons)
- [ ] Reset all action from Settings
- [x] ~~Real-time execution progress~~ (implemented: WebSocket log stream from CLI process)
- [x] ~~Worktree management view per project~~ (implemented via Tickets worktree sync)
- [x] ~~Config editor with save/validate~~ (implemented in Settings page)
- [ ] MR status tracking from GitLab
- [ ] Debrief conversation thread viewer
- [ ] Multi-project dashboard switching
- [ ] Light theme option
- [ ] Auth management page (login/logout/configure LLM provider)
