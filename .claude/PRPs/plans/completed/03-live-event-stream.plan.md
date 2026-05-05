# Feature: Command Center — Live Event Stream

## Summary

Add a WebSocket endpoint `/executions/{id}/stream` that replays events from the `events` table (catch-up) and then tails the in-process `EventBus` for new events until the execution ends or the client disconnects.

## User Story

As a Command Center dashboard
I want a single long-lived connection per execution that delivers events in order
So that I can render live progress without polling.

## Problem Statement

After plans 01 + 02 a dashboard can fetch paginated events, but polling is wasteful, lossy at the boundary, and adds dashboard-side complexity. A tail-of-the-bus stream is the natural surface.

## Solution Statement

Single WebSocket route:

1. Client opens `ws://…/executions/{id}/stream?since_seq=N` (optional).
2. Server reads events from DB where `execution_id=? AND seq>?` in batches, sends as JSON frames.
3. Server then subscribes to `EventBus` for this process; pushes new matching events until:
   - Execution's final event (`execution.completed` / `execution.failed` / `execution.cancelled`) is sent → close with code 1000.
   - Client disconnects → unsubscribe, close.
   - Idle > 30s → send heartbeat ping frame; disconnect on timeout.

An `asyncio.Queue` bridges the sync `EventBus.publish` into the route's async loop; the subscriber callback is a one-line `loop.call_soon_threadsafe(queue.put_nowait, event)`.

## Metadata

| Field | Value |
|---|---|
| Type | NEW_CAPABILITY |
| Complexity | MEDIUM |
| Systems Affected | `src/service/routes/stream.py` (new), `src/service/app.py`, `src/core/events/bus.py` (minor) |
| Dependencies | None new |
| Estimated Tasks | 5 |
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
| `src/service/routes/stream.py` | CREATE |
| `src/service/app.py` | UPDATE — `include_router(stream.router)` |
| `src/core/events/bus.py` | UPDATE — expose `subscribe()` return value (unsubscribe callable) if not already; ensure thread-safety of the subscriber list (already locked in plan 01) |
| `tests/service/test_stream.py` | CREATE |

---

## Frame Format

Server → client, one JSON object per frame:

```json
{"kind":"replay","seq":42,"ts":"...","type":"agent.message_sent","agent":"python_developer","payload":{...}}
{"kind":"live","seq":43,"ts":"...","type":"tool.called","agent":"python_developer","payload":{...}}
{"kind":"heartbeat","ts":"..."}
{"kind":"end","execution_status":"succeeded"}
```

Client need not distinguish `replay`/`live` for rendering, but debugging and reconnection logic benefit from the hint.

---

## Tasks

### Task 1 — UPDATE `src/core/events/bus.py`
Ensure `subscribe(cb) -> Callable[[], None]` returns an unsubscribe fn (plan 01 already specifies this — verify in code, add if missing). Add a `topic` filter param (optional `execution_id`) so the stream route doesn't receive events for other runs it would then drop. If kept simple, filter on the caller side — acceptable for now.

**GOTCHA**: Subscriber is invoked on the thread that published. For the stream route, the subscriber must *not* do async work — only `queue.put_nowait` via `loop.call_soon_threadsafe`.

**VALIDATE**: `pytest tests/core/test_event_bus.py` still passes.

### Task 2 — CREATE `src/service/routes/stream.py`
```python
@router.websocket("/executions/{execution_id}/stream")
async def stream(ws: WebSocket, execution_id: str, since_seq: int = 0,
                 repo: ExecutionRepository = Depends(get_repo),
                 bus: EventBus = Depends(get_bus)):
    await ws.accept()
    execution = repo.get(execution_id)
    if execution is None:
        await ws.close(code=4404); return

    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    loop = asyncio.get_running_loop()
    unsubscribe = bus.subscribe(
        lambda ev: ev.execution_id == execution_id
            and loop.call_soon_threadsafe(queue.put_nowait, ev)
    )

    try:
        # 1. Replay
        last_seq = since_seq
        for row in repo.iter_events(execution_id, since_seq=since_seq, batch=500):
            await ws.send_json({"kind":"replay", **_serialize(row)})
            last_seq = row.seq

        # 2. Live tail
        while True:
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=30)
            except asyncio.TimeoutError:
                await ws.send_json({"kind":"heartbeat","ts":_now()})
                continue
            if ev.seq <= last_seq:
                continue                                # already sent via replay
            await ws.send_json({"kind":"live", **_serialize(ev)})
            last_seq = ev.seq
            if ev.type in _TERMINAL_TYPES:
                await ws.send_json({"kind":"end","execution_status":ev.type.split(".")[-1]})
                break
    except WebSocketDisconnect:
        pass
    finally:
        unsubscribe()
        with suppress(Exception):
            await ws.close()
```

**GOTCHA — dedup**: events emitted between the replay query and subscription must not be missed. Subscribe FIRST into the queue, then run the replay, then drain the queue skipping anything with `seq <= last_seq`. The snippet above subscribes first — **follow that order**.

**GOTCHA — queue bound**: `maxsize=1000` prevents memory blowout if a client reads slowly. If full, drop oldest with a `dropped_events` counter sent as a frame. Alternative: close the socket with `1013 (Try Again Later)` — prefer this, simpler and honest.

**GOTCHA — execution already finished**: If `execution.ended_at is not None`, replay then close immediately with `{"kind":"end",...}` — no subscribe needed.

**VALIDATE**: `pytest tests/service/test_stream.py`.

### Task 3 — UPDATE `src/service/app.py`
`app.include_router(stream.router)`. Ensure `get_bus` dependency is wired (new addition: app holds the EventBus singleton created alongside the DB on startup).

**VALIDATE**: `create_app().routes` includes the ws route.

### Task 4 — CREATE `repo.iter_events`
Add a generator method `iter_events(execution_id, since_seq, batch)` on `ExecutionRepository` if not present from plan 01.

**VALIDATE**: Unit test iterating a seeded fixture.

### Task 5 — CREATE `tests/service/test_stream.py`
Use FastAPI's `TestClient.websocket_connect`:
- Seed DB with an execution and 3 events (seq 1..3), all final.
- Connect, assert 3 replay frames + `end` frame.
- Connect with `since_seq=2`, assert 1 replay frame (seq=3) + `end`.
- Connect to non-existent execution → closed with 4404.
- For live behaviour: publish a 4th event *after* connect (use a bus fixture), assert a `live` frame arrives before `end`.

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

- [ ] Replay delivers all historical events in seq order
- [ ] Live events arrive without gaps vs. replay (no duplicates, no drops)
- [ ] Heartbeat frame every ~30s of silence
- [ ] Socket closes cleanly with `{"kind":"end",...}` on terminal events
- [ ] Dead-client disconnect doesn't leak the subscriber
- [ ] No change to Foundation tests

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Replay/live race produces duplicate or dropped events at the boundary | HIGH | HIGH | Subscribe FIRST, replay SECOND, dedup by `seq`. Covered by a specific test. |
| Slow client backs up the queue, blocks publisher | MED | MED | Bounded queue (1000); on full, close socket with 1013 |
| Publisher thread accidentally does async work in subscriber | LOW | HIGH | The one-line `call_soon_threadsafe(put_nowait)` is the *only* work allowed in the subscriber |
| Dashboard reconnect storm after a server restart | LOW | LOW | Dashboard concern; document reconnect-with-backoff expectation |

## Notes

- Branch: `experimental/command-center-03-event-stream`.
- SSE was considered instead of WebSocket. Chose WS because (a) pydantic payloads serialize cleanly as JSON frames either way, (b) bidirectional leaves room for client-side filter subscriptions without a breaking change, (c) starlette WS is already in-tree via FastAPI.
