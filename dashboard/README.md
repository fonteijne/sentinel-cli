# Sentinel Command Center — Dashboard

React + TypeScript single-page app that drives the FastAPI service in `src/service/`. **Read-only against the codebase: no backend changes are required to run it.**

## Quick start

```bash
cd dashboard
npm install
npm run dev          # http://localhost:5173
```

The first load asks for the bearer token (and the API base URL — defaults to `http://localhost:8787`). Tokens live in `sessionStorage` only.

## Build

```bash
npm run build        # tsc + vite build → dashboard/dist/
npm run preview      # serve the built bundle
```

The compiled output is a static SPA. Serve it from any HTTP server (or behind the same Cloudflare Access tunnel as the API).

## Design system

Styles are copied verbatim from the design-system handoff (`/home/user/workspace/Sentinel-Design-System-handoff.zip`) into `src/styles.css`. Component markup mirrors the prototypes in `dashboard.jsx` / `components.jsx`. See `docs/dashboard/SETUP_AND_SPEC.md` in the repo root for the full spec, validation log, and roadmap.

## What's wired

- Overview KPIs + recent runs (poll `GET /executions`).
- Worktrees board (kanban grouped by status, derived from `(project, ticket_id)`).
- Executions table with filters.
- Execution drawer with **live WebSocket stream** (`WS /executions/{id}/stream`), polling fallback, agent results, cancel + retry with safety prompts.
- Idempotency-Key on every `POST /executions`.

## What's "coming soon"

Inbox, Insights, Settings, plus seven other roadmap items called out in `docs/dashboard/SETUP_AND_SPEC.md` §11. They render as clearly-labelled placeholders and never call non-existent endpoints.
