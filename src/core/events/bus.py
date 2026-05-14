"""Persist-then-publish event bus.

Invariants (matches d75d276 commit-message contract; tests assert each):
  1. Persist FIRST: the row exists in ``events`` before any subscriber runs.
     A subscriber that queries the table for its own event must find it.
  2. ``seq`` is monotonic per ``execution_id`` (not global). Computed via
     ``MAX(seq) + 1`` *inside the INSERT statement itself* (single-statement
     atomic), so two writers cannot collide on the PK.
  3. Subscriber exceptions are caught and logged, never propagated. One bad
     subscriber must not crash a run, and subsequent subscribers must still
     fire for the same event.
  4. Oversized payloads (>64 KB serialized) are replaced with a small marker
     ``{"_truncated": true, "type": ..., "execution_id": ...}`` so the row
     stays valid JSON and can be inspected without dragging the bus into
     tracking blob storage.

No async. No HTTP surface. In-process, synchronous, sqlite3.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from src.core.events.types import BaseEvent

logger = logging.getLogger(__name__)

_MAX_PAYLOAD_BYTES = 64 * 1024


class EventBus:
    """Synchronous persist-then-publish bus bound to a single SQLite connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._subscribers: dict[type[BaseEvent], list[Callable[[BaseEvent], None]]] = (
            defaultdict(list)
        )

    def subscribe(
        self,
        event_type: type[BaseEvent],
        handler: Callable[[BaseEvent], None],
    ) -> None:
        """Register ``handler`` for events whose runtime type is ``event_type``.

        Subscriptions are by exact type, not by isinstance — subclassing
        BaseEvent does not implicitly subscribe parent handlers. Phase 1 has
        no need for it; revisit if Phase 2 wants generic listeners.
        """
        self._subscribers[event_type].append(handler)

    def publish(self, event: BaseEvent) -> None:
        """Persist the event, then fan out to subscribers.

        Steps:
          1. Fill ``ts`` if empty (UTC ISO-8601).
          2. Serialize payload; truncate marker if oversized.
          3. INSERT in one statement that derives the next per-execution ``seq``
             from ``MAX(seq)+1``, so two writer connections cannot race on the PK.
             COMMIT.
          4. Call subscribers; swallow + log exceptions individually.
        """
        if not event.ts:
            event.ts = datetime.now(timezone.utc).isoformat()

        payload_json = event.model_dump_json()
        if len(payload_json.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            payload_json = json.dumps(
                {
                    "_truncated": True,
                    "type": event.type,
                    "execution_id": event.execution_id,
                }
            )

        agent = getattr(event, "agent", None)

        self._conn.execute(
            "INSERT INTO events (execution_id, seq, ts, agent, type, payload_json) "
            "SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ?, ? "
            "FROM events WHERE execution_id = ?",
            (
                event.execution_id,
                event.ts,
                agent,
                event.type,
                payload_json,
                event.execution_id,
            ),
        )
        self._conn.commit()

        for handler in self._subscribers.get(type(event), []):
            try:
                handler(event)
            except Exception:
                # Persist-first means the row is already durable; a misbehaving
                # subscriber must not poison the run. Log and continue so the
                # next subscriber for this same event still gets a chance.
                logger.error("subscriber raised", exc_info=True)

    def get_events(self, execution_id: str) -> list[dict]:
        """Read helper used by tests. Returns rows as plain dicts in seq order."""
        rows = self._conn.execute(
            "SELECT execution_id, seq, ts, agent, type, payload_json "
            "FROM events WHERE execution_id = ? ORDER BY seq",
            (execution_id,),
        ).fetchall()
        return [dict(row) for row in rows]
