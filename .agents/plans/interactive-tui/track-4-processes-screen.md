# Track 4 — "Processes" screen with two swimlanes

## Goal

A new TUI screen — reachable from a keybinding (e.g., `P`) — that shows what's running and what recently ran, across all projects. Clicking a row attaches the Output panel to that run's stream. Cancelling is one keystroke away.

## Layout

```
┌─ Processes ──────────────────────────────────────────────────────────┐
│                                                                      │
│ Current project — banv                                               │
│ ─────────────────────────────────────────────────────────────────    │
│   BANV-1234  plan      ● running   3m ago    cost $0.42   [attach]   │
│   BANV-1234  execute   ✓ succeeded 1h ago    cost $1.87              │
│   BANV-1234  execute   ✗ failed    2h ago                            │
│   BANV-1234  debrief   ✓ succeeded 2h ago    cost $0.11              │
│   BANV-1200  plan      ✓ succeeded 1d ago    cost $0.40              │
│   …last 10 per (worktree, command)…                                  │
│                                                                      │
│ Other active processes                                               │
│ ─────────────────────────────────────────────────────────────────    │
│   [proj-y]  PY-42     execute  ● running   12m ago   cost $2.10      │
│   [proj-z]  PZ-7      plan     ● running    4m ago   cost $0.08      │
│                                                                      │
│ [↑/↓] move   [enter] attach   [c] cancel   [r] refresh   [esc] back  │
└──────────────────────────────────────────────────────────────────────┘
```

## Data sources

All queries hit `GET /executions` with different params — no new server endpoints needed.

- **Current-project lane**:
  - One query per (worktree, command) triple? Too many. Simpler: one query `?project=X&limit=200` grouped client-side by `(ticket_id, kind)`, truncated to the newest 10 per group after sort.
  - Alternative: `?project=X&status=running` + `?project=X&limit=200` → merge. Pick whichever reads cleanly; document the choice.
- **Other-projects lane**: `?status=running` with the current project filtered out client-side. Small result set by nature.
- Refresh cadence: poll every 3s while the screen is open. Cancel the poll on screen exit. No WS subscription at the screen level — the Output panel subscribes only when a row is selected.

## Interactions

- **Enter / attach**: switches the Output panel to tail the selected run (uses the same `tail_execution` method from track 3). Banner shown if the run was already in-flight from a previous session.
- **`c` / cancel**: only enabled on rows with `status=running` or `status=pending`. Calls `POST /executions/{id}/cancel`. Optimistically updates the row to `cancelling` until the next poll confirms.
- **`r` / refresh**: immediate re-fetch.
- **Escape**: back to the home screen.

## Row rendering

- Columns: ticket id, kind, status (colored dot), relative age, cost, action hint.
- For the "other projects" lane, prefix the project name in brackets.
- Status colors: running = yellow, succeeded = green, failed = red, cancelled = dim, crashed = red-dim.
- Relative age uses the service's `started_at` / `ended_at`; TUI formats.

## Deliverables

| File | Change |
|---|---|
| `src/tui/screens/processes.py` | New screen class. |
| `src/tui/app.py` | Register screen + keybinding. |
| `src/tui/service_client.py` | Add `list_executions(...)` helper (wraps `GET /executions` with the query params). |
| `src/tui/widgets/process_row.py` | Small widget for one row. Optional — could be a list item. |
| `tests/tui/test_processes_screen.py` | Mock service; cover rendering, polling cadence, attach, cancel. |

## Explicitly out of scope

- Filtering / searching inside the processes list. v1 is a static list grouped by lane.
- Historical runs for "other projects" — only active runs shown.
- Cost aggregates, charts, anything beyond the per-row display.
- Deleting / hiding runs from the list.

## Gotchas

- **Polling cost**: every 3s against SQLite is cheap, but the HTTP round trip adds up across open TUIs. Cap to a single in-flight poll at a time; if the previous response hasn't landed, skip this tick.
- **Cross-session consistency**: two TUIs open on the same host see the same list — good. If TUI A cancels a run, TUI B's next poll reflects it. Don't try to push updates peer-to-peer; rely on the poll.
- **"Last 10 per (worktree, command)"** is a client-side truncation. With 500 active worktrees this query could grow; cap the overall fetch at 200 rows and be honest that very-old runs past the cap won't show. If that ever matters, revisit with a server-side per-group limit.
- **Stale "running" rows** (worker crashed, reaper hasn't swept): show them as `running` until the reaper flips to `crashed`. The user may see a brief flicker. Not worth special-casing in the TUI; Supervisor owns the truth.
- **Project config discovery**: the "current project" requires knowing what project the TUI is on — already tracked by the existing project picker state. Other-projects lane just needs the project names that appear in the active-runs result.

## Acceptance

- Start a plan run on project A, switch to the Processes screen — it appears in "Current project" as running.
- Cancel it from the screen — status flips to `cancelled` within one poll; output panel (if attached) shows the cancellation event.
- On project B's TUI, that run appears under "Other active processes" until it terminates, then disappears from the list (not historical for other projects).
- Reopening the TUI after a quit shows any still-running work in the correct lane with correct age.

## Depends on

- Track 2 (list endpoint already exists — this track just consumes it).
- Track 3 (reuse `tail_execution` for the attach action).

## Does not depend on

- Track 1 for development (manual `sentinel serve` works).
