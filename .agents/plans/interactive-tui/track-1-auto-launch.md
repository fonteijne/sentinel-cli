# Track 1 — Service auto-launch + discovery

## Goal

`sentinel i` finds or spawns the Command Center service transparently. The user never runs `sentinel serve` by hand. The service survives TUI quit. A second `sentinel i` finds the running service rather than spawning a competitor.

## User-visible behaviour

- First `sentinel i` in a fresh environment: service is launched in the background; TUI connects; startup feels instant.
- Subsequent `sentinel i`: immediately connects to the existing service.
- If the service is unreachable (port taken, crashed), the TUI shows a clear error with a one-line recovery hint. No silent retries that hide the problem.

## Scope

1. A discovery file (`~/.config/sentinel/service.json` — or the existing state path) with `{pid, port, token, started_at, version}`.
2. A `sentinel serve` entry point that writes this file on boot and removes it on clean shutdown.
3. TUI startup logic: read file → try `GET /health` with token → on success connect; on failure (no file, stale pid, bad token, conn refused) spawn `sentinel serve` detached, wait for health, connect.
4. Single-instance guard: OS-level file lock on the discovery file so two simultaneous `sentinel i` boots can't both spawn.

## Non-goals

- Systemd units, launchd plists, any OS-level service manager. Lifecycle is "as long as the host container lives".
- Cross-host discovery. Service is loopback-only.
- Restart-on-crash. If the service dies, the next `sentinel i` respawns it — that's enough for v1.

## Files / owners

| File | Change | Owner |
|---|---|---|
| `src/cli.py` | `sentinel serve` command body (thin — delegates to service factory). `sentinel i` preamble calls the discovery helper. | `cc-cli-integration-expert` |
| `src/service/auth.py` | `load_or_create_token` already writes the token atomically; extend the written file to carry `pid` / `port` / `started_at`, or keep a separate discovery file alongside. | `cc-auth-expert` |
| `src/tui/app.py` | On mount: call the discovery helper, store the connection config, pass to the TUI's HTTP client. | `cc-cli-integration-expert` (discovery) + TUI owner |
| `src/service/app.py` | On startup write discovery file; on graceful shutdown remove it. Lifespan hook. | `cc-fastapi-expert` |
| `tests/integration/test_auto_launch.py` | Covers fresh boot, second-instance attach, stale-pid cleanup. | `cc-test-harness-expert` |

## Discovery file contract

Location: `$XDG_STATE_HOME/sentinel/service.json` (fallback `~/.local/state/sentinel/service.json`). Written with mode `0600`.

```json
{
  "pid": 12345,
  "port": 8765,
  "token": "st_live_…",
  "started_at": "2026-05-01T14:32:11Z",
  "version": "0.X.Y"
}
```

- **Atomic write**: write to `service.json.tmp`, fsync, rename. Same discipline as `load_or_create_token`.
- **Stale detection**: on discovery, if `pid` is not alive OR `GET /health` returns non-200 within a short deadline, the file is treated as stale and unlinked under the lock.

## Single-instance guard

- `fcntl.flock(LOCK_EX | LOCK_NB)` on a sibling `service.lock` file.
- TUI startup flow under the lock:
  1. Try to read `service.json`.
  2. If present and healthy → release the lock, use it.
  3. If absent or stale → spawn `sentinel serve` (double-fork, `setsid`), wait up to N seconds for the file to appear + health to respond, release the lock.
- A second TUI racing to the lock waits briefly; by the time it gets the lock the first has written the discovery file, so it goes down path (2).

## `sentinel serve` shape

- Thin click command; all real work happens inside the service factory (`src/service/app.py::create_app`).
- Binds `127.0.0.1:<port>`. `port` default is an ephemeral pick (bind to 0, read back); can be pinned via env var for tests.
- On startup: write discovery file.
- On shutdown (SIGTERM, SIGINT, lifespan exit): remove discovery file.
- Logs to the existing log sink; does not steal stdout from the TUI.

## Gotchas

- **Double-fork vs `subprocess.Popen(start_new_session=True)`.** The latter is enough on Linux. Don't over-engineer.
- **Port already in use.** Pick ephemeral; fail loudly with the offending port in the message if pinned.
- **Token rotation.** If the discovery file is deleted, the next spawn creates a new token. Old TUI instances holding the old token get 401 and re-run discovery. Acceptable.
- **Container restart.** Service dies, discovery file survives on a persistent volume → next TUI startup sees the stale file, detects the dead pid, cleans up, respawns.
- **TUI running outside `sentinel-dev`.** Not supported in v1. Document the assumption; don't code for it.

## Acceptance

- Fresh environment: `sentinel i` → service boots, TUI connects, `ps` shows a detached `sentinel serve`. Quitting the TUI leaves the service running.
- Second `sentinel i` while the first is still running → instant connect, no second `sentinel serve` process.
- `kill -9` the service process; stale discovery file remains. Next `sentinel i` detects the stale pid, respawns. No manual cleanup needed.
- `SIGTERM` the service: discovery file removed; next `sentinel i` respawns cleanly.

## Depends on

- Track 2's endpoints (`/health` already exists; start/cancel come from track 2 but aren't required for auto-launch itself).

## Does not depend on

- Track 3 or 4.
