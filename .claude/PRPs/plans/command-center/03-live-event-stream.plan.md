# Feature: Command Center — Live Event Stream

## Summary

Add a WebSocket endpoint `/executions/{id}/stream` that tails events by polling the `events` table and streams them as JSON frames until the execution ends or the client disconnects.

## User Story

As a Command Center dashboard
I want a single long-lived connection per execution that delivers events in order
So that I can render live progress without HTTP polling from the browser.

## Problem Statement

After plans 01 + 02 a dashboard can fetch paginated events, but client-side HTTP polling is wasteful, lossy at the boundary, and pushes complexity onto every consumer.

## Solution Statement

**Server-side DB polling, client-side single stream.** The browser sees one WebSocket; the server translates that into a tight DB-poll loop.

Rationale for DB-polling over an in-memory bus subscription:

- Plan 04 runs executions in **subprocess workers**. Those workers own their own `EventBus` Python object — the service's bus cannot see their events via `subscribe()`. Persistence is the only cross-process truth.
- Single code path for the in-process CLI case and the out-of-process worker case. No "subscribe first, replay second" race to get wrong.
- SQLite WAL readers never block writers; a 200ms poll on an indexed `(execution_id, seq)` scan is cheap and predictable.

Route flow:

1. Client opens `ws://…/executions/{id}/stream?since_seq=N` (optional).
2. Server `accept()`s. If execution does not exist → close `4404` and return.
3. Loop:
   a. `SELECT ... WHERE execution_id = ? AND seq > ? ORDER BY seq LIMIT 500` via `repo.iter_events(...)`.
   b. For each row: `await ws.send_json({"kind": "event", ...})`.
   c. If the last row's `type` is in `TERMINAL_EVENT_TYPES`: send `{"kind":"end","execution_status":...}` and close.
   d. If no rows this tick: send `{"kind":"heartbeat","ts":...}` every ~30s of silence; else idle-sleep 200ms.
4. Client disconnect → `WebSocketDisconnect` → exit loop cleanly.

No in-memory queue, no subscriber thread, no race between replay and live feed. `since_seq` is just the initial `last_seq` in the loop.

## Metadata

| Field | Value |
|---|---|
| Type | NEW_CAPABILITY |
| Complexity | MEDIUM |
| Systems Affected | `src/service/routes/stream.py` (new), `src/service/app.py`, `src/service/deps.py` |
| Dependencies | None new |
| Estimated Tasks | 4 |
| Prerequisite | Plans 01 + 02 |

---

## Mandatory Reading

| Priority | File | Why |
|---|---|---|
| P0 | `.claude/PRPs/plans/command-center/01-foundation.plan.md` | `EventBus`, event types, `events` table schema |
| P0 | `.claude/PRPs/plans/command-center/02-http-read-api.plan.md` | FastAPI app factory + routes pattern |
| P0 | `src/core/events/bus.py` (from 01) | Subscriber interface |
| P1 | [FastAPI WebSockets docs](https://fastapi.tiangolo.com/advanced/websockets/) | Async WS handler idioms |
| P1 | [Starlette WebSocket](https://www.starlette.io/websockets/) | Underlying API |

---

## Files to Change

| File | Action |
|---|---|
| `src/service/routes/stream.py` | CREATE — WebSocket endpoint, DB-polling tail |
| `src/service/routes/__init__.py` | UPDATE — export the new router |
| `tests/service/test_stream.py` | CREATE |

**Not touched here** (plan 05 owns `create_app()` composition):
- `src/service/app.py` — 05 wires the stream router behind auth.
- `src/service/deps.py` — `get_repo` and `get_db_conn` already exist from plan 02; we reuse them.
- `src/core/events/bus.py` — unchanged. The bus stays in-process for local consumers (CLI log adapter); WS tail does not subscribe.

---

## Frame Format

Server → client, one JSON object per frame:

```json
{"kind":"event","seq":42,"ts":"...","type":"agent.message_sent","agent":"python_developer","payload":{...}}
{"kind":"event","seq":43,"ts":"...","type":"tool.called","agent":"python_developer","payload":{...}}
{"kind":"heartbeat","ts":"..."}
{"kind":"end","execution_status":"succeeded"}
```

Every event frame has the same shape (no replay/live distinction — there's one source). The envelope fields (`seq`, `ts`, `type`, `agent`) sit at the top level; the event-specific fields are nested under `payload` so the frame shape is stable across event types.

Terminal statuses derived from the terminal event type via an explicit `_END_STATUS` mapping dict (NOT `type.split(".")[-1]`):

| Event type | `execution_status` value |
|---|---|
| `execution.completed` | `succeeded` |
| `execution.failed` | `failed` |
| `execution.cancelled` | `cancelled` |

These values match the `ExecutionStatus` enum defined in plan 01.

---

## Tasks

### Task 1 — CREATE `src/service/routes/stream.py`

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from src.core.events.types import TERMINAL_EVENT_TYPES
from src.core.execution.repository import ExecutionRepository
from src.service.deps import get_repo

router = APIRouter()

POLL_INTERVAL_S = 0.2
HEARTBEAT_INTERVAL_S = 30.0
SEND_TIMEOUT_S = 30.0          # slow-client backpressure cutoff

# Terminal event type → dashboard-friendly status string.
# MUST match the ExecutionStatus enum values, not the raw type suffix —
# `execution.completed` maps to `succeeded`, NOT `completed`.
_END_STATUS = {
    "execution.completed": "succeeded",
    "execution.failed":    "failed",
    "execution.cancelled": "cancelled",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _frame_from_row(row: "EventRow") -> dict:
    return {
        "kind": "event",
        "seq": row["seq"],
        "ts": row["ts"],
        "type": row["type"],
        "agent": row["agent"],
        "payload": row["payload"],            # repo returns already-parsed dict
    }


@router.websocket("/executions/{execution_id}/stream")
async def stream(
    ws: WebSocket,
    repo: Annotated[ExecutionRepository, Depends(get_repo)],
    execution_id: str,
    since_seq: int = 0,
) -> None:
    # Note: param order: non-default (ws, repo via Depends, execution_id path) first,
    # defaulted (since_seq) last — Python SyntaxError otherwise.
    await ws.accept()
    if repo.get(execution_id) is None:
        await ws.close(code=4404)
        return

    last_seq = since_seq
    last_heartbeat = asyncio.get_running_loop().time()
    closed = False                          # guards against double-close after timeout

    async def _send(frame: dict) -> None:
        # Backpressure cutoff: if a slow client cannot absorb a frame in SEND_TIMEOUT_S,
        # close the socket with 1011 (Internal Error) and let the client reconnect with since_seq.
        await asyncio.wait_for(ws.send_json(frame), timeout=SEND_TIMEOUT_S)

    try:
        while True:
            rows = list(repo.iter_events(execution_id, since_seq=last_seq, limit=500))

            for row in rows:
                await _send(_frame_from_row(row))
                last_seq = row["seq"]
                if row["type"] in TERMINAL_EVENT_TYPES:
                    await _send({
                        "kind": "end",
                        "execution_status": _END_STATUS[row["type"]],
                    })
                    return

            now = asyncio.get_running_loop().time()
            if not rows and (now - last_heartbeat) >= HEARTBEAT_INTERVAL_S:
                await _send({"kind": "heartbeat", "ts": _now_iso()})
                last_heartbeat = now

            await asyncio.sleep(POLL_INTERVAL_S)
    except WebSocketDisconnect:
        closed = True
        return
    except asyncio.TimeoutError:
        # Slow client — close with 1011; client reconnects with since_seq.
        try: await ws.close(code=1011)
        except Exception: pass
        closed = True
        return
    finally:
        if not closed:
            try: await ws.close()
            except Exception: pass
```

**GOTCHA — no subscriber, no race.** The old "subscribe first, replay second" dedup is gone. There is exactly one source (the DB) and `seq` is monotonic; `since_seq` + `ORDER BY seq LIMIT 500` is lossless.

**GOTCHA — polling cost.** 200ms × one indexed SELECT per connection is cheap, but multiply by connected clients. Cap concurrent WS connections per execution in plan 05 if this becomes real; for now documented as follow-up.

**GOTCHA — WebSocketDisconnect import.** `from fastapi import WebSocket, WebSocketDisconnect` (re-exported from starlette).

**GOTCHA — stale connection detection.** Browsers don't always fire TCP RST on tab close. The periodic `send_json` (heartbeat or real event) is what surfaces a dead peer — it will raise `WebSocketDisconnect` or `ConnectionClosed` and the loop exits.

**GOTCHA — slow-client backpressure.** A client that opens a socket but never reads will have its server coroutine stalled on `send_json` forever, holding a threadpool slot (from the sync `get_repo` dep). `SEND_TIMEOUT_S = 30` + `asyncio.wait_for` turns that into a clean 1011 close. Events remain in the DB; the client reconnects with `since_seq` and resumes.

**GOTCHA — connection cap.** Every WS connection holds one threadpool thread (via `get_repo` → `get_db_conn` sync generator). With default threadpool size 40, 40 simultaneous WS connections exhaust it. For MVP this is acceptable (single-user dashboard). File as follow-up if/when a multi-tab dashboard lands.

**VALIDATE**: `pytest tests/service/test_stream.py`.

### Task 2 — UPDATE `src/service/routes/__init__.py`
Re-export the `stream.router` alongside the `executions.router` added in plan 02.

**VALIDATE**: `from src.service.routes import stream` works.

### Task 3 — Verify `repo.iter_events` + `TERMINAL_EVENT_TYPES` exist (from plan 01)
Both are mandated by plan 01 Task 6 and Task 3 respectively. This task is a checkpoint: if either is missing, fix in plan 01's scope, not here.

- `iter_events(execution_id, since_seq=0, limit=500) -> Iterator[RowLike]` — rows have `seq`, `ts`, `type`, `agent`, `payload` (dict, already parsed from `payload_json`).
- `TERMINAL_EVENT_TYPES` — importable from `src.core.events.types`.

**VALIDATE**: `python -c "from src.core.events.types import TERMINAL_EVENT_TYPES; from src.core.execution.repository import ExecutionRepository; assert hasattr(ExecutionRepository, 'iter_events')"`

### Task 4 — CREATE `tests/service/test_stream.py`
Use FastAPI's `TestClient.websocket_connect` (sync — no pytest-asyncio needed):

- **Replay-only, finished execution**: seed 3 events seq=1..3 with final `execution.completed`; connect → assert 3 `event` frames + `end` frame with `execution_status=succeeded`.
- **since_seq cursor**: same fixture, connect with `?since_seq=2` → assert 1 `event` frame (seq=3) + `end`.
- **Non-existent execution**: connect to unknown id → closed with 4404.
- **Live tail of an in-flight run**: seed execution without terminal event; connect; in parallel publish a new event via `bus.publish` (or direct INSERT into the test DB); assert frame arrives within 1s.
- **Live tail of a subprocess-only run (simulating plan 04)**: seed execution; INSERT events from a thread (simulating another process); assert they appear. **This test is the reason the WS reads from DB, not bus — if it passes here, it passes for subprocess workers.**
- **Heartbeat on silence**: monkeypatch `HEARTBEAT_INTERVAL_S = 0.5`; seed running execution, no events; assert a `heartbeat` frame arrives within 1s.
- **Client disconnect**: connect then close; assert the server coroutine exits cleanly (no zombie loop).
- **Terminal mapping**: seed terminal event `execution.cancelled`; assert `end` frame has `execution_status == "cancelled"`. Seed `execution.completed`; assert `"succeeded"`. (Guards against the `split(".")[-1]` regression.)
- **Slow-client backpressure**: monkeypatch `SEND_TIMEOUT_S = 0.1`; connect but don't read; assert the server closes with 1011 within 1s.

**VALIDATE**: `pytest tests/service/test_stream.py -v`.

---

## Validation Commands

```bash
poetry run pytest tests/service -v
poetry run pytest -x
# manual in sentinel-dev:
sentinel serve --port 8787 &
websocat ws://127.0.0.1:8787/executions/<id>/stream?since_seq=0
```

## Acceptance Criteria

- [ ] Events delivered in strict `seq` order with no gaps or duplicates
- [ ] `since_seq` cursor resumes cleanly from any point
- [ ] Works for in-process executions AND subprocess-worker executions (plan 04) without code change
- [ ] Heartbeat frame every ~30s of silence
- [ ] Socket closes with `{"kind":"end",...}` on terminal event
- [ ] Client disconnect exits the server coroutine cleanly (no leaked tasks)
- [ ] No change to Foundation tests

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Polling load scales linearly with connected clients | MED | LOW | 200ms × indexed SELECT is cheap; cap concurrent WS per execution in plan 05 if needed |
| SQLite reader starves under heavy write contention | LOW | MED | WAL readers don't block writers; `busy_timeout=30000` on the reader connection |
| Browser tab close doesn't fire disconnect | MED | LOW | Heartbeat send surfaces the dead peer within 30s |
| Dashboard reconnect storm after a server restart | LOW | LOW | Client uses `since_seq` + backoff — documented in dashboard contract |

## Notes

- Branch: `experimental/command-center-03-event-stream`.
- SSE was considered instead of WebSocket. Chose WS because (a) pydantic payloads serialize cleanly as JSON frames either way, (b) bidirectional leaves room for client-side filter subscriptions without a breaking change, (c) starlette WS is already in-tree via FastAPI.
- **Why DB-polling instead of bus subscription?** Plan 04 runs executions in a subprocess. A subprocess has its own `EventBus` object; the service process cannot `subscribe()` to it. The DB is the only cross-process source of truth, and SQLite WAL makes the poll cheap. This also eliminates the replay/live race that dogged the earlier design.
