# Interactive TUI — dashboard over the Command Center

## Goal (UX, not architecture)

Use `sentinel i` as a control surface for long-running work:

1. **Select project.**
2. **View worktrees / add new worktree** (user supplies the ticket id only; the project's Jira key is auto-prefixed).
3. **Select worktree → pick a command** from `debrief`, `plan`, `execute` (debrief is optional; plan must have succeeded before execute is usable).
4. **View / select active processes** (across all projects) and their outputs — live and historic.

Runs must **survive quitting the TUI**. Reopening the dashboard must show in-flight runs and recent history, and let the user attach to any of them to watch output.

## Why this plan exists

Today the TUI runs plan/execute/debrief **in its own process** via Click callbacks (`src/tui/actions.py → _run_cli_callback`). Quitting the TUI kills the work. The Command Center substrate exists (`src/service/`, `src/core/execution/`, `src/core/events/`, `src/core/persistence/`) but the TUI doesn't talk to it. This plan wires them together.

## Design decisions (locked)

| # | Decision | Chosen | Rationale / constraint |
|---|---|---|---|
| 1 | Service lifecycle | **Auto-launch** by `sentinel i` on first use. Detached; persists across TUI quits. | "It just works" UX. Loopback + token auth per the existing Command Center design. |
| 2 | Duplicate-command behaviour | **Attach** to the running instance instead of starting a second one. | Dashboard semantics: clicking a command reveals its state, doesn't duplicate work. |
| 3 | Gating between commands | **None.** The underlying `execute` command already fails loudly when no plan exists. | Don't re-implement the check in two places. Worktree existence is handled implicitly by `plan`/`debrief` (they create it). |
| 4 | Attach feedback | **Banner** rendered in the Output panel: `"Attached to run {short_id} started {rel} ago"`. Service composes the string. | Avoid silent attach confusing the user. |
| 5 | History scope | **Last 10 runs per (worktree, command)**. Pure client-side query against the existing `GET /executions` — no server work. | Caps noise without building a reaper. |
| 6 | Processes screen layout | **Two swimlanes**: (a) *current project* — active runs + last-10 history per worktree/command; (b) *other projects* — active runs only. | Focus on the project you're in; still surface stuff running elsewhere so nothing gets forgotten. |

## Out of scope for this plan

- Multi-host deployments (service is local, loopback-only).
- Web dashboard / non-TUI clients.
- Cross-project migration of runs, plan artifacts, etc.
- Any change to how `plan` / `execute` / `debrief` actually perform their work — those are owned by existing Command Center tracks and the CLI.

## Tracks

Implementation is split into four tracks. Order matters — 2 is the keystone the rest attaches to.

| Track | File | Keystone for | Rough size |
|---|---|---|---|
| 1 | [`track-1-auto-launch.md`](track-1-auto-launch.md) | TUI can reach the service | small-medium |
| 2 | [`track-2-attach-or-start.md`](track-2-attach-or-start.md) | TUI ↔ service contract | small |
| 3 | [`track-3-tui-rewire.md`](track-3-tui-rewire.md) | Runs survive TUI quit | medium |
| 4 | [`track-4-processes-screen.md`](track-4-processes-screen.md) | Dashboard UX delivered | medium |

**Recommended implementation order:** 2 → 1 → 3 → 4.

- Track 2 first because it shapes the contract; everything else calls it.
- Track 1 next because without auto-launch the TUI has nothing to call. (Can be developed against a manually-started `sentinel serve` until 1 lands.)
- Track 3 rewires the current action panel to use the contract.
- Track 4 is additive UI over the same contract.

## Cross-cutting concerns

- **Auth**: every TUI→service call carries the bearer token written by `load_or_create_token` (owned by `cc-auth-expert`). Loopback-only binding. Same discipline as `sentinel execute --remote`.
- **Identity of a run**: `(project, ticket_id, kind)`. The TUI's "worktree id" maps to `ticket_id` on the service. Kind is one of `plan`, `execute`, `debrief` (see `ExecutionKind`).
- **Persistence**: SQLite (`src/core/persistence/`). Service reads/writes the same DB the Supervisor writes to. No new tables; new queries only.
- **Container boundary**: service and TUI both live inside `sentinel-dev` (or the baked prod `sentinel` image). Detaching from the TUI is process-level; container restart kills all in-flight runs — Supervisor reconciliation flips them to `CRASHED` on next service boot.

## Gotchas the plan deliberately does not guard against

- **Stale running rows between crash and reap.** A worker dies, reaper hasn't swept yet, POST attaches to the dead row. The reaper sweeps within seconds and flips it to `CRASHED`; the TUI re-POST starts fresh. Worth surfacing in the attach banner if the last heartbeat is older than the stale-threshold; not worth extra server logic.
- **Two `sentinel i` launched simultaneously.** Auto-launch race — track 1 handles this with a PID file + connect-first-spawn-second.
- **Worktree not created for a bare `execute`.** The command itself fails loudly; TUI surfaces the failure via the normal event stream. No pre-flight check.

## Owners (subagent types)

- `cc-auth-expert` — token, rate limit, CORS (track 1 cross-cut).
- `cc-cli-integration-expert` — `sentinel serve` auto-launch, pidfile discipline (track 1).
- `cc-fastapi-expert` — routes, schemas (track 2).
- `cc-supervisor-expert` — spawn/cancel API consult (track 2).
- `cc-persistence-expert` — any new repo query (track 2).
- `cc-websocket-expert` — stream contract consult (tracks 3 & 4).
- `cc-test-harness-expert` — fixtures + write-endpoint tests (track 2); TUI harness (tracks 3 & 4).
- `cc-plan-reviewer` — sign-off on every track before merge.

## Acceptance — "done" for the whole plan

The following user story passes end-to-end:

> I start `sentinel i`, pick project *X*, pick worktree *BANV-1234*, click **Plan**. Output streams. I press `q`. I reopen `sentinel i` — *BANV-1234 / plan* shows as running under "Current project"; I click it and the output resumes streaming from where the service is. The plan finishes; it now shows as completed in the last-10 list. I click **Execute** — output streams again; I quit, reopen, it's still running and I can reattach. In another terminal I open `sentinel i` on project *Y*; *BANV-1234 / execute* shows up under "Other active processes".
