# Sentinel Command Center — Dashboard

React + TypeScript single-page app that drives the FastAPI service in `src/service/`. **Read-only against the codebase: no backend changes are required to run it.**

## Quick start

### Local Vite dev (talks straight to the backend)

```bash
cd dashboard
npm install
npm run dev          # http://localhost:5173
```

The first load asks for the bearer token and the API base URL. In Vite dev the URL field defaults to `http://localhost:8787`, which assumes the backend is reachable on that host port (e.g. `sentinel-dev` in compose, or `sentinel serve` running locally). Tokens live in `sessionStorage`.

### Docker compose (the supported way to demo the SPA)

From the repo root, bring up the prod backend and the dashboard together:

```bash
docker compose --profile serve --profile dashboard up -d --build
```

Then open <http://localhost:5174>. The API base URL field on the splash defaults to `/api` — leave it as is. The dashboard's nginx reverse-proxies both HTTP and the `/executions/{id}/stream` WebSocket on `/api/` to the backend over the compose network, so the browser only ever talks to one same-origin host (no CORS, no extra port to publish).

If you need to point at a different backend (for example the dev container, or an externally-hosted service), set `SENTINEL_API_UPSTREAM` in `.env` before `up`:

```bash
SENTINEL_API_UPSTREAM=my-backend.internal:8787 docker compose --profile dashboard up -d
```

> **Note:** `sentinel-dev` uses `network_mode: bridge`, so the compose dashboard service can't resolve it by name. Pair `--profile dashboard` with `--profile serve`, or run a host-published backend (e.g. `--profile dev`) and override `SENTINEL_API_UPSTREAM=host.docker.internal:8787` (Linux: pass `--add-host=host.docker.internal:host-gateway`).

## Build

```bash
npm run build        # tsc + vite build → dashboard/dist/
npm run preview      # serve the built bundle
```

The compiled output is a static SPA. The bundled `Dockerfile` + `nginx.conf.template` add the same-origin `/api/` proxy described above; if you serve `dist/` from another HTTP server, you must either reproduce that proxy or set the API base URL to the absolute backend origin and configure CORS accordingly.

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
