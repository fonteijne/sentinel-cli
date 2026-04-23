"""Stand-alone execution worker — entrypoint for subprocess isolation.

Usage:
    python -m src.core.execution.worker --execution-id <id>

The worker:
    1. Calls ``configure_logging()`` *first* so spawn-reimport doesn't skip it.
    2. Opens its own SQLite connection + bus (no inheritance from parent).
    3. Starts a daemon heartbeat thread that updates ``workers.last_heartbeat_at``
       every 5s so the supervisor can tell "detached but alive" from "orphan".
    4. Installs SIGTERM/SIGINT handlers that set an internal cancel event —
       handlers are best-effort; actual work interrupt happens between agent turns.
    5. Constructs an Orchestrator and dispatches to ``plan``/``execute``/``debrief``
       based on the persisted ``ExecutionKind``. Options come from
       ``executions.metadata_json.options`` — NEVER argv — to keep the endpoint
       body small and escape-free.
"""

from __future__ import annotations

import argparse
import logging
import sys

HEARTBEAT_INTERVAL_S = 5.0


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="sentinel-worker",
        description="Command Center execution worker",
    )
    parser.add_argument(
        "--execution-id",
        required=True,
        help="UUID of the executions row to run",
    )
    return parser.parse_args()


def main() -> int:
    # ---- Logging FIRST. spawn re-imports; no inherited basicConfig. --------
    from src.utils.logging_config import configure_logging

    configure_logging()
    logger = logging.getLogger("src.core.execution.worker")

    # ---- Only NOW import orchestration + SDK-heavy modules. ---------------
    import signal
    import threading

    from src.core.events.bus import EventBus
    from src.core.execution.models import ExecutionStatus
    from src.core.execution.repository import ExecutionRepository
    from src.core.persistence.db import connect, ensure_initialized

    args = _parse_args()
    execution_id: str = args.execution_id

    logger.info("worker starting execution_id=%s pid=%d", execution_id, __import__("os").getpid())

    ensure_initialized()
    conn = connect()
    repo = ExecutionRepository(conn)
    bus = EventBus(conn)

    shutdown = threading.Event()

    def _heartbeat_loop() -> None:
        hb_conn = connect()
        try:
            while not shutdown.wait(HEARTBEAT_INTERVAL_S):
                try:
                    hb_conn.execute("BEGIN IMMEDIATE")
                    hb_conn.execute(
                        "UPDATE workers SET last_heartbeat_at=? WHERE execution_id=?",
                        (_now_iso(), execution_id),
                    )
                    hb_conn.execute("COMMIT")
                except Exception:
                    try:
                        hb_conn.execute("ROLLBACK")
                    except Exception:
                        pass
                    logger.exception("heartbeat write failed")
        finally:
            try:
                hb_conn.close()
            except Exception:
                pass

    hb_thread = threading.Thread(
        target=_heartbeat_loop, daemon=True, name="worker-heartbeat"
    )
    hb_thread.start()

    cancel = threading.Event()

    def _on_signal(signum, frame) -> None:  # type: ignore[no-untyped-def]
        logger.info("worker received signal %d, setting cancel flag", signum)
        cancel.set()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    exit_code = 1
    try:
        execution = repo.get(execution_id)
        if execution is None:
            logger.error("worker: execution %s not found", execution_id)
            return 2

        # Transition queued → running here so the HTTP endpoint can return
        # 202 with "queued" and observers see the "running" transition from
        # the worker's lifetime, not the HTTP thread.
        if execution.status == ExecutionStatus.QUEUED:
            repo.set_status(execution_id, ExecutionStatus.RUNNING)

        orchestrator = _build_orchestrator(repo, bus, cancel)
        method = _resolve_method(orchestrator, execution.kind)
        options = (execution.metadata or {}).get("options", {}) or {}

        if method is None:
            # Orchestrator doesn't yet expose plan/execute/debrief (landed in
            # a later track). For plan 04's scaffold path, run a minimal
            # begin→complete cycle so the row transitions and events fire.
            logger.warning(
                "orchestrator.%s not implemented; running scaffold lifecycle",
                execution.kind.value,
            )
            return _scaffold_run(orchestrator, execution, cancel)

        try:
            result = method(execution_id=execution_id, **options)
        except TypeError:
            # method may not accept execution_id kwarg — retry positionally.
            result = method(execution_id, **options)

        status = getattr(result, "status", None)
        if isinstance(status, ExecutionStatus):
            exit_code = 0 if status == ExecutionStatus.SUCCEEDED else 1
        else:
            refreshed = repo.get(execution_id)
            exit_code = (
                0
                if refreshed is not None
                and refreshed.status == ExecutionStatus.SUCCEEDED
                else 1
            )
        return exit_code
    except Exception as exc:
        logger.exception("worker failed for execution %s", execution_id)
        try:
            repo.record_ended(
                execution_id, ExecutionStatus.FAILED, error=str(exc) or type(exc).__name__
            )
        except Exception:
            logger.exception("worker: record_ended on failure path raised")
        return 1
    finally:
        shutdown.set()
        try:
            conn.close()
        except Exception:
            pass


def _build_orchestrator(repo, bus, cancel_flag):  # type: ignore[no-untyped-def]
    """Construct an Orchestrator. Indirection keeps tests patchable."""
    from src.core.execution.orchestrator import Orchestrator

    return Orchestrator(repo=repo, bus=bus, cancel_flag=cancel_flag)


def _resolve_method(orchestrator, kind):  # type: ignore[no-untyped-def]
    from src.core.execution.models import ExecutionKind

    mapping = {
        ExecutionKind.PLAN: "plan",
        ExecutionKind.EXECUTE: "execute",
        ExecutionKind.DEBRIEF: "debrief",
    }
    attr = mapping.get(kind)
    if attr is None:
        return None
    return getattr(orchestrator, attr, None)


def _scaffold_run(orchestrator, execution, cancel_flag) -> int:
    """Minimal begin/complete cycle when orchestrator verbs don't yet exist.

    Lets plan 04 land and be exercised (events written, row transitions) before
    the plan/execute/debrief extraction completes.
    """
    from src.core.execution.models import ExecutionStatus

    logger = logging.getLogger("src.core.execution.worker")
    if cancel_flag.is_set():
        orchestrator.repo.set_status(
            execution.id, ExecutionStatus.CANCELLING
        )
        orchestrator.repo.record_ended(
            execution.id, ExecutionStatus.CANCELLED
        )
        return 1
    try:
        orchestrator.complete(execution)
        return 0
    except Exception:
        logger.exception("scaffold_run: complete() failed")
        orchestrator.fail(execution, error="scaffold_complete_failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
