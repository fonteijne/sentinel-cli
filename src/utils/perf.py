"""Opt-in performance instrumentation for Sentinel.

Activated by setting the ``SENTINEL_PERF=1`` environment variable. When unset,
``timed()`` is a near-zero-overhead context manager (one cached boolean check)
and no log file is created.

Span records are appended as JSONL to ``logs/perf.jsonl`` (or ``/app/logs/perf.jsonl``
inside ``sentinel-dev``). Mirrors the path resolution in ``agent_sdk_wrapper.py``.

Schema per record::

    {
      "ts": "<utc iso>",
      "span": "<dotted name>",
      "elapsed_s": 0.123,
      "thread": <int>,
      "pid": <int>,
      "meta": {... user-supplied ...}
    }
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_ENABLED: bool | None = None
_LOG_PATH: Path | None = None


def is_enabled() -> bool:
    """Return True iff ``SENTINEL_PERF=1`` was set when first checked.

    Cached on first call; subsequent ``os.environ`` mutations are ignored
    until ``reset_for_tests()`` is called. This is intentional — we never
    want a hot-loop ``timed()`` to re-read ``os.environ``.
    """
    global _ENABLED
    if _ENABLED is None:
        _ENABLED = os.environ.get("SENTINEL_PERF") == "1"
    return _ENABLED


def perf_log_path() -> Path:
    """Resolve the perf-log file path.

    Prefers ``/app/logs/perf.jsonl`` (sentinel-dev bind-mount), falls back to
    ``<cwd>/logs/perf.jsonl``. Mirrors ``agent_sdk_wrapper._write_diagnostic``
    resolution.
    """
    global _LOG_PATH
    if _LOG_PATH is not None:
        return _LOG_PATH
    for base in ("/app/logs", "logs"):
        candidate = Path(base)
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            _LOG_PATH = candidate / "perf.jsonl"
            return _LOG_PATH
        except OSError:
            continue
    _LOG_PATH = Path.cwd() / "logs" / "perf.jsonl"
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _LOG_PATH


def reset_for_tests() -> None:
    """Clear the cached enabled flag and log path. Test-only."""
    global _ENABLED, _LOG_PATH
    _ENABLED = None
    _LOG_PATH = None


class _Span:
    """Light handle yielded by :func:`timed`. Allows post-hoc meta additions."""

    __slots__ = ("name", "start", "meta", "_recording")

    def __init__(self, name: str, meta: dict[str, Any] | None, recording: bool) -> None:
        self.name = name
        self.meta: dict[str, Any] = dict(meta) if meta else {}
        self.start = time.monotonic() if recording else 0.0
        self._recording = recording

    def add_meta(self, key: str, value: Any) -> None:
        """Attach a key/value to this span; written on context-exit."""
        if self._recording:
            self.meta[key] = value


class _NoopSpan(_Span):
    """Disabled-mode span: ``add_meta`` is a no-op."""

    __slots__ = ()

    def add_meta(self, key: str, value: Any) -> None:  # noqa: D401
        return


@contextmanager
def timed(span_name: str, *, meta: dict[str, Any] | None = None) -> Iterator[_Span]:
    """Context manager that records a span when ``SENTINEL_PERF=1``.

    When disabled, yields a no-op span and adds no measurable overhead beyond
    one cached boolean check.

    On exit (success or exception), an enabled span writes one JSONL record.
    Exceptions are recorded with ``meta["error"] = exc.__class__.__name__``
    and re-raised.
    """
    if not is_enabled():
        yield _NoopSpan(span_name, meta, recording=False)
        return

    span = _Span(span_name, meta, recording=True)
    try:
        yield span
    except BaseException as exc:
        span.meta["error"] = exc.__class__.__name__
        raise
    finally:
        elapsed = time.monotonic() - span.start
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "span": span.name,
            "elapsed_s": round(elapsed, 6),
            "thread": threading.get_ident(),
            "pid": os.getpid(),
            "meta": span.meta,
        }
        try:
            line = json.dumps(record, default=str) + "\n"
            with open(perf_log_path(), "a") as f:
                f.write(line)
        except OSError as write_err:
            logger.warning("perf: failed to write span %s: %s", span.name, write_err)
