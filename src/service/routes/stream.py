"""WebSocket live-event stream for executions (plan 03).

One long-lived connection per execution. The server tails the ``events``
table via short polls and forwards rows as JSON frames. This reads from
the DB (not the in-process ``EventBus``) because plan 04 will run
executions in subprocess workers whose bus is invisible to the service.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from src.core.events.types import TERMINAL_EVENT_TYPES
from src.core.execution.repository import EventRow, ExecutionRepository
from src.service.deps import get_repo

logger = logging.getLogger(__name__)

router = APIRouter()

POLL_INTERVAL_S = 0.2
HEARTBEAT_INTERVAL_S = 30.0
SEND_TIMEOUT_S = 30.0

# Terminal event type → dashboard-friendly status string.
# MUST match the ExecutionStatus enum values, not the raw type suffix —
# `execution.completed` maps to `succeeded`, NOT `completed`.
_END_STATUS = {
    "execution.completed": "succeeded",
    "execution.failed": "failed",
    "execution.cancelled": "cancelled",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _frame_from_row(row: EventRow) -> dict:
    return {
        "kind": "event",
        "seq": row["seq"],
        "ts": row["ts"],
        "type": row["type"],
        "agent": row["agent"],
        "payload": row["payload"],
    }


@router.websocket("/executions/{execution_id}/stream")
async def stream(
    ws: WebSocket,
    repo: Annotated[ExecutionRepository, Depends(get_repo)],
    execution_id: str,
    since_seq: int = 0,
) -> None:
    await ws.accept()
    if repo.get(execution_id) is None:
        await ws.close(code=4404)
        return

    last_seq = since_seq
    last_heartbeat = asyncio.get_running_loop().time()
    closed = False

    async def _send(frame: dict) -> None:
        # Backpressure cutoff: if a slow client cannot absorb a frame in
        # SEND_TIMEOUT_S, close with 1011 and let the client reconnect
        # with since_seq.
        await asyncio.wait_for(ws.send_json(frame), timeout=SEND_TIMEOUT_S)

    try:
        while True:
            rows = list(
                repo.iter_events(execution_id, since_seq=last_seq, limit=500)
            )

            for row in rows:
                await _send(_frame_from_row(row))
                last_seq = row["seq"]
                if row["type"] in TERMINAL_EVENT_TYPES:
                    await _send(
                        {
                            "kind": "end",
                            "execution_status": _END_STATUS[row["type"]],
                        }
                    )
                    await ws.close()
                    closed = True
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
        try:
            await ws.close(code=1011)
        except Exception:
            pass
        closed = True
        return
    finally:
        if not closed:
            try:
                await ws.close()
            except Exception:
                pass
