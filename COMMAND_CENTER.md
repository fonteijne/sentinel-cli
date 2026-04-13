# Sentinel Command Center — Development Status

> Reference file for tracking Command Center dashboard features.
> Update this file when new CLI features are added or dashboard capabilities change.

---

## Dashboard Pages

### Overview
- [x] KPI stat cards (active projects, active tickets, security score, agent runs today)
- [x] Trend indicators with percentage change
- [x] Recent activity feed with timeline entries
- [x] Agent runs per week bar chart
- [x] Plan confidence score line chart with threshold indicator
- [x] System health indicators (Jira, GitLab, LLM, SSH, Beads)
- [x] Quick action buttons (Plan Ticket, Execute Ticket, Validate Connections, View Projects)
- [x] Demo data fallback when Sentinel CLI is not connected

### Projects
- [x] Project grid with card layout (key, name, git URL, branch, stack type, worktree count)
- [x] Add new project form
- [x] Edit existing project form
- [x] Delete project with confirmation
- [x] Generate project profile action
- [x] Search/filter projects
- [x] Active worktree count display
- [x] Demo project data fallback

### Tickets (Kanban Pipeline)
- [x] Four-column kanban board: Plan → Execute → Review → Done
- [x] Ticket cards with ID, summary, priority badge, labels
- [x] Click-to-select ticket detail panel
- [x] Actions: Generate/Revise Plan, Execute Implementation, Run Functional Debrief
- [x] Search/filter tickets
- [x] Per-column ticket counts
- [x] Action commands trigger sentinel CLI via API

### Agents
- [x] Agent cards for all 7 agent types (plan_generator, python_developer, drupal_developer, security_review, functional_debrief, confidence_evaluator, project_profiler)
- [x] Model name and temperature display per agent
- [x] Specialization tags
- [x] Run history with success/failure indicators
- [x] Success rate percentage
- [x] Click-to-select run history detail panel
- [x] Weekly runs bar chart
- [x] Model usage breakdown

### Security
- [x] Security score calculation (weighted by severity)
- [x] Severity breakdown stat cards (Critical, High, Medium, Low)
- [x] Severity pie chart
- [x] Findings trend line chart (6-week history)
- [x] Findings list with severity filter tabs (ALL, Critical, High, Medium, Low)
- [x] Finding detail panel (ID, OWASP rule, ticket link, file location, description)
- [x] Mark Resolved / Ignore actions
- [x] Status badges (Open, Resolved)

### Settings
- [x] Service connection cards (Jira, GitLab, LLM, SSH, Beads) with status indicators
- [x] Test connection action per service
- [x] Validate All connections button
- [x] config.yaml viewer with syntax-highlighted YAML
- [x] Edit config action
- [x] Environment variables reference table

### Logs
- [x] Real-time log stream viewer (WebSocket-ready)
- [x] Log level filter buttons (ALL, INFO, WARNING, ERROR, DEBUG)
- [x] Text search/filter
- [x] Color-coded log levels
- [x] Timestamped entries with source module identification
- [x] Demo log data for offline/demo mode
- [x] Download, copy, and clear actions

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
- [x] `GET /api/tickets/{ticket_id}/info` — Get ticket info from Jira
- [x] `POST /api/tickets/{ticket_id}/plan` — Generate/revise plan
- [x] `POST /api/tickets/{ticket_id}/execute` — Execute implementation
- [x] `POST /api/tickets/{ticket_id}/debrief` — Run functional debrief

### Configuration
- [x] `GET /api/config` — Read config.yaml
- [x] `PUT /api/config` — Update config.yaml
- [x] `GET /api/agents` — Get agent configurations

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
| `sentinel reset <ticket>` | — | ⬜ Planned |
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
- [ ] Reset ticket action from Tickets detail panel
- [ ] Reset all action from Settings
- [ ] Real-time execution progress (streaming CLI output during plan/execute)
- [ ] Worktree management view per project
- [ ] MR status tracking from GitLab
- [ ] Debrief conversation thread viewer
- [ ] Config editor with save/validate
- [ ] Multi-project dashboard switching
- [ ] Light theme option
