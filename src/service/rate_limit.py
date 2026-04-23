"""Per-token rate limiter for Command Center write endpoints.

In-memory, single-process. This mirrors the single-process service model
(uvicorn without ``--workers``); a multi-worker deploy would need a shared
store but that's explicitly out of scope for the MVP.

Two limits apply:

* ``max_concurrent`` — number of currently in-flight requests for the same
  token. Prevents a single leaked token from exhausting the Supervisor's
  worker slots faster than they release.
* ``max_per_minute`` — sliding-window request count for the same token.
  Prevents long-tail spend blowing up via a high-frequency script.

Keys are ``token_prefix(token)`` (sha256-truncated), not the raw token —
we never want a raw secret on a dict key that might show up in a heap dump.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple


class TokenRateLimiter:
    """Per-token concurrent + sliding-window rate limit.

    Thread-safe: a single ``threading.Lock`` guards both the in-flight counter
    and the 60-second window deque. The fast path is a handful of arithmetic
    ops — contention is negligible at the rates this enforces.
    """

    def __init__(self, max_concurrent: int, max_per_minute: int) -> None:
        self._max_concurrent = int(max_concurrent)
        self._max_per_min = int(max_per_minute)
        self._in_flight: Dict[str, int] = defaultdict(int)
        self._window: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check_and_reserve(self, token_key: str) -> Tuple[bool, int]:
        """Reserve a slot for ``token_key`` if both limits allow.

        Returns ``(allowed, retry_after_seconds)``. When ``allowed`` is
        ``True`` the caller MUST pair the reservation with exactly one
        ``release`` call. When ``False`` no reservation was made and no
        release is needed; ``retry_after_seconds`` is a rough hint
        (minute-window remaining time, or 1s for concurrent-slot wait).
        """

        with self._lock:
            now = time.monotonic()

            # Prune stale entries from the 60-second window first — otherwise
            # a long-idle token could carry ancient entries that unfairly
            # count against a fresh burst.
            w = self._window[token_key]
            while w and now - w[0] > 60.0:
                w.popleft()

            if len(w) >= self._max_per_min:
                # Retry just after the oldest entry drops off the window.
                # +1s is a rounding cushion so the client's retry doesn't
                # trip the same bucket immediately.
                retry = int(60.0 - (now - w[0])) + 1
                return (False, retry)

            if self._in_flight[token_key] >= self._max_concurrent:
                # Concurrent slot frees on ``release`` — no deterministic
                # retry; 1s is a reasonable client nudge.
                return (False, 1)

            w.append(now)
            self._in_flight[token_key] += 1
            return (True, 0)

    def release(self, token_key: str) -> None:
        """Release a previously reserved concurrent slot for ``token_key``.

        ``max(0, …)`` guards against a defensive double-release leaking us
        into negative counts; such a leak would silently expand capacity.
        """

        with self._lock:
            self._in_flight[token_key] = max(
                0, self._in_flight[token_key] - 1
            )
