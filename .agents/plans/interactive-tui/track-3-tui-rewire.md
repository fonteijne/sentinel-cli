# Track 3 — TUI action panel rewire

## Goal

Make the existing action panel talk to the Command Center service instead of running plan / execute / debrief in-process. Quitting the TUI must leave the work running. Reopening the TUI on the same worktree must offer to attach to the in-flight run rather than start a new one — driven by the service's attach-or-start response, not by TUI logic.

## Current state

`src/tui/actions.py::_run_cli_callback` invokes the Click command bodies from inside the TUI process. stdout and logging are captured at the FD level (see commits `ba57e09`, `ce2f178`) and piped to the Output panel. When the TUI quits, the Click callback's async work dies with it.

## Deliverables

### 1. A thin service client

New module, probably `src/tui/service_client.py` (or colocated under `src/tui/widgets/`):

- Constructor takes `(base_url, token)` from the discovery helper (track 1).
- Methods:
  - `start(project, ticket_id, kind, options=None) -> StartResult` — POSTs `/executions`, returns a small dataclass `{execution_id, attached: bool, banner: Optional[str]}`.
  - `cancel(execution_id) -> None`.
  - `tail(execution_id) -> AsyncIterator[str]` — opens the WebSocket at `/executions/{id}/stream?token=...` and yields frames as rendered text. Owned jointly with `cc-websocket-expert` for frame format.
- Uses `httpx` for HTTP, `websockets` or `httpx-ws` for the stream — whichever the existing service code uses. Don't introduce a new dependency.
- **All I/O is async.** The TUI is Textual; sync calls will block the reactor.

### 2. Rewire `src/tui/actions.py`

`run_plan`, `run_execute`, `run_debrief` currently call `_run_cli_callback`. Change them so:

1. They resolve `project` and `ticket_id` from TUI state.
2. Call `service_client.start(project, ticket_id, kind)`.
3. Switch the Output panel to tail `service_client.tail(execution_id)`.
4. If `attached=true`, render `banner` as the first line in the Output panel (track 2 sends a formatted string; TUI renders verbatim).
5. Mark the action panel's "running action" label with the kind + short id, as it does today.

`run_validate`, `run_status`, `run_drain` are local / read-only and **keep their existing in-process behaviour** for v1. They're fast and don't need the service round-trip. Revisit only if the user asks.

### 3. Output widget — dual-source tail

The Output panel today receives captured stdout/log from the in-process run. It needs a second mode: a stream from the service.

- Add a method `tail_execution(execution_id)` that:
  - Opens the WS via the service client.
  - Appends each frame to the widget's buffer.
  - Detects terminal frames (`ExecutionCompleted` / `ExecutionFailed` / `ExecutionCancelled`) and transitions the "running action" label accordingly.
  - On disconnect, tries a single reconnect (tail from `since_seq` in the last frame's seq). If the second attempt fails, render an error line and stop.
- Keep the in-process capture path for local commands (validate / status / drain). One widget, two modes.

### 4. "Start new instead" affordance

Deferred sub-feature of attach banner. When `attached=true`:

- Render the banner plus a footer line: *"[n] Start new run (disabled until this one ends)"*.
- Bind `n` to no-op while the attached run is active.
- When the run reaches a terminal state, enable `n` — pressing it calls `service_client.start(...)` again (service will now not find an active run and start fresh).
- Low-risk, low-code; still worth gating behind explicit user ask if it balloons.

### 5. Quit-safety

- Quitting the TUI must **not** cancel the run. Today the Click callback is tied to the TUI event loop; severing that tie is the point.
- The WS tail's cleanup on quit should be a clean close, not a cancel of the execution.
- Confirm that `Supervisor.spawn` truly detaches from the TUI process — if the TUI is the parent of the worker, ensure double-fork / setsid / equivalent already lives in the Supervisor (this is its plan, not ours).

## Files touched

| File | Change |
|---|---|
| `src/tui/service_client.py` | New module. |
| `src/tui/actions.py` | Rewire `run_plan` / `run_execute` / `run_debrief`. |
| `src/tui/widgets/output.py` (or wherever the Output panel lives) | Add `tail_execution`. |
| `src/tui/app.py` | Pass discovery config into the client at mount time. |
| `tests/tui/test_service_client.py` | Mock the service; cover start / attach / cancel / tail. |
| `tests/tui/test_actions.py` | Update existing tests for the new async flow. |

## Explicitly out of scope

- "Processes" screen (track 4).
- Auto-launch (track 1) — can develop against a manually-started `sentinel serve`.
- Changing the behaviour of validate / status / drain.
- Reworking the project / worktree picker UX.

## Gotchas

- **Async in Textual**: use Textual's worker/task API, not bare `asyncio.create_task`, so cancellation is routed through the TUI runtime.
- **WS reconnect loop**: one retry max. Infinite reconnect hides real service problems.
- **Token rotation**: if track 1's service restarts and the token changes, the TUI's open WS will 401. Handle by prompting the user to reopen the dashboard; don't silently rediscover mid-stream.
- **Log correlation**: events carry `execution_id`; the TUI's local log (for validate/status/drain) doesn't. Make sure the Output panel doesn't interleave them ambiguously — either clear the panel on action change, or label the mode.
- **stdout capture path regressions**: the FD-level capture from `ba57e09` exists for in-process runs. Don't remove it; validate/status/drain still need it.

## Acceptance

- Click **Plan** on a fresh worktree → output streams; quit the TUI; `ps` shows the worker still running; reopen the TUI, pick the same worktree, click **Plan** → Output panel shows the attach banner and resumes streaming from current frames.
- Click **Execute** while a prior execute is running on the same worktree → attach banner, same stream.
- Cancel (via the cancel keybinding, if one exists — or the future Processes screen) cleanly terminates.
- Validate / status / drain still work as local in-process commands.
- TUI tests pass against a mocked service.

## Depends on

- Track 2 (endpoints must exist).
- Track 1 for ergonomic development; can develop against manual `sentinel serve` without it.

## Does not depend on

- Track 4.
