"""Centralized logging setup for CLI, service, and spawned workers.

``spawn``-based workers re-import this module in the child; ``basicConfig`` at
module top-level in ``cli.py`` does NOT run there. Call ``configure_logging()``
as the first step of any process entrypoint so the child's stdout/stderr and
JSONL diagnostics are wired up identically to the parent.

The module itself must NOT emit any log records during import — doing so
forces the default root handler to install before ``configure_logging`` gets
a chance to attach the intended handlers.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from logging import Handler, LogRecord
from pathlib import Path
from typing import Optional


_CONFIGURED = False


def _logs_dir() -> Path:
    raw = os.environ.get("SENTINEL_LOGS_DIR")
    if raw:
        return Path(raw).expanduser()
    return Path.cwd() / "logs"


class _JsonlDiagnosticsHandler(Handler):
    """Append each record as a single JSON line to ``agent_diagnostics.jsonl``."""

    def __init__(self, path: Path) -> None:
        super().__init__(level=logging.INFO)
        self._path = path

    def emit(self, record: LogRecord) -> None:  # noqa: D401
        try:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                entry["exc"] = logging.Formatter().formatException(record.exc_info)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            self.handleError(record)


def configure_logging(
    level: int = logging.INFO,
    *,
    enable_jsonl: bool = True,
    log_file: Optional[Path] = None,
    jsonl_path: Optional[Path] = None,
) -> None:
    """Install stderr + rotating file + optional JSONL handlers on the root logger.

    Idempotent: a second call from the same process is a no-op.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger()
    root.setLevel(level)

    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(fmt)
    root.addHandler(stderr_handler)

    logs_dir = _logs_dir()
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_path = log_file if log_file is not None else logs_dir / "cli_stderr.log"
        file_handler = logging.FileHandler(str(file_path), encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception:
        pass

    if enable_jsonl:
        try:
            jsonl = (
                jsonl_path
                if jsonl_path is not None
                else logs_dir / "agent_diagnostics.jsonl"
            )
            root.addHandler(_JsonlDiagnosticsHandler(jsonl))
        except Exception:
            pass

    _CONFIGURED = True
