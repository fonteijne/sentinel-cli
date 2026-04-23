"""In-process event bus with persist-then-publish semantics.

Every ``publish()`` writes to the SQLite ``events`` table BEFORE firing
subscribers — persistence is the source of truth. If a subscriber raises,
the event is still durable.

Subscriber lists are process-local; cross-process consumers (e.g. plan 03's
WebSocket tail) read from the DB, not via :meth:`subscribe`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from typing import Callable, List

from src.core.events.types import SentinelEvent

logger = logging.getLogger(__name__)

Subscriber = Callable[[SentinelEvent], None]


class EventBus:
    """Persist events to SQLite, then dispatch to in-process subscribers.

    Use one bus instance per connection. Connections are not shared across
    threads/processes; if you need a bus in another thread/process, create
    a fresh connection + bus there.
    """

    # Hard cap per event payload. An oversize event is truncated (biggest
    # string field shrunk) rather than dropped — the DB must always record
    # *something* so downstream tail/reads don't stall.
    MAX_PAYLOAD_BYTES = 64 * 1024

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._subscribers: List[Subscriber] = []
        # Serializes seq allocation within this process. Cross-process ordering
        # is handled by SQLite's BEGIN IMMEDIATE.
        self._seq_lock = threading.Lock()

    # ------------------------------------------------------------------ publish

    def publish(self, event: SentinelEvent) -> None:
        """Persist ``event`` to the DB, then dispatch to subscribers.

        Subscriber exceptions are logged and swallowed — a misbehaving
        dashboard cannot be allowed to crash a run.
        """
        payload = self._encode_payload(event)

        with self._seq_lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM events WHERE execution_id = ?",
                    (event.execution_id,),
                ).fetchone()
                seq = row[0] + 1
                self._conn.execute(
                    "INSERT INTO events(execution_id, seq, ts, agent, type, payload_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event.execution_id,
                        seq,
                        event.ts.isoformat(),
                        event.agent,
                        event.type,
                        payload,
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

        # Dispatch is OUTSIDE the lock — slow/broken subscribers must not
        # serialize publishers.
        for sub in list(self._subscribers):
            try:
                sub(event)
            except Exception:
                logger.exception("event subscriber raised")

    # -------------------------------------------------------------- subscribers

    def subscribe(self, cb: Subscriber) -> Callable[[], None]:
        """Register ``cb`` to receive future events. Returns an unsubscribe fn."""
        self._subscribers.append(cb)

        def _unsub() -> None:
            try:
                self._subscribers.remove(cb)
            except ValueError:
                pass

        return _unsub

    # ------------------------------------------------------------------ helpers

    def _encode_payload(self, event: SentinelEvent) -> str:
        """Serialize the full event (including ``type``) as JSON.

        If the result exceeds :data:`MAX_PAYLOAD_BYTES`, shrink the biggest
        string fields and mark ``_truncated: true``. Never byte-slice raw
        JSON — that can split escapes or multibyte chars.
        """
        payload = event.model_dump_json()
        if len(payload.encode("utf-8")) <= self.MAX_PAYLOAD_BYTES:
            return payload

        original_size = len(payload.encode("utf-8"))
        truncated = {
            **event.model_dump(mode="json"),
            "_truncated": True,
            "_original_bytes": original_size,
        }
        for k, v in list(truncated.items()):
            if isinstance(v, str) and len(v) > 4096:
                truncated[k] = v[:4096] + "…"
        payload = json.dumps(truncated, ensure_ascii=False)

        if len(payload.encode("utf-8")) > self.MAX_PAYLOAD_BYTES:
            envelope_only = {
                "execution_id": event.execution_id,
                "type": event.type,
                "ts": event.ts.isoformat(),
                "agent": event.agent,
                "_truncated": True,
                "_reason": "oversize_after_shrink",
                "_original_bytes": original_size,
            }
            payload = json.dumps(envelope_only)

        return payload
