"""Orchestrator — owns execution lifecycle, event emission, and agent wiring.

Scope note (plan 01 / foundation):
    The existing CLI flows (``sentinel plan``, ``sentinel execute``, ``sentinel
    debrief``) keep their incidental side-effects (git push, GitLab MR updates,
    Jira comments, container setup/teardown) inline in :mod:`src.cli`. The
    orchestrator's job on this plan is the *structural* piece: create the
    ``Execution`` row, wire :class:`EventBus` + ``execution_id`` into the
    agents, publish lifecycle events, record terminal state, and expose a
    small API the CLI commands can call.

    Later plans (02-05) can push more of the CLI flow into the orchestrator
    once the HTTP surface and out-of-process worker exist.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, Optional

from src.core.events import (
    EventBus,
    ExecutionCompleted,
    ExecutionFailed,
    ExecutionStarted,
    PhaseChanged,
    SentinelEvent,
)
from src.core.execution.models import Execution, ExecutionKind, ExecutionStatus
from src.core.execution.repository import ExecutionRepository

if TYPE_CHECKING:
    from src.config_loader import ConfigLoader
    from src.session_tracker import SessionTracker

logger = logging.getLogger(__name__)


class Orchestrator:
    """Coordinates an agent-driven run against a persistent ``Execution`` row.

    One orchestrator instance per run is the expected usage pattern. Instances
    are cheap to build — share the ``EventBus`` and ``ExecutionRepository``
    across calls if desired.

    Subscribers registered in :meth:`__init__`:
        * ``cost.accrued`` → :meth:`ExecutionRepository.add_cost`
    """

    def __init__(
        self,
        repo: ExecutionRepository,
        bus: EventBus,
        session_tracker: Optional["SessionTracker"] = None,
        config: Optional["ConfigLoader"] = None,
    ) -> None:
        self.repo = repo
        self.bus = bus
        self.session_tracker = session_tracker
        self.config = config

        # Mandatory subscriber: cost.accrued → executions.cost_cents
        self.bus.subscribe(self._cost_subscriber)

    # ---------------------------------------------------------- subscribers

    def _cost_subscriber(self, event: SentinelEvent) -> None:
        if event.type == "cost.accrued":
            cents = getattr(event, "cents", 0)
            if cents:
                try:
                    self.repo.add_cost(event.execution_id, cents)
                except Exception:
                    logger.exception(
                        "failed to increment cost for execution %s", event.execution_id
                    )

    # ------------------------------------------------------------ lifecycle

    def begin(
        self,
        *,
        ticket_id: str,
        project: str,
        kind: ExecutionKind,
        options: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        idempotency_token_prefix: Optional[str] = None,
    ) -> Execution:
        """Insert the execution row and publish ``ExecutionStarted``."""
        execution = self.repo.create(
            ticket_id=ticket_id,
            project=project,
            kind=kind,
            options=options,
            idempotency_key=idempotency_key,
            idempotency_token_prefix=idempotency_token_prefix,
        )
        self.bus.publish(
            ExecutionStarted(
                execution_id=execution.id,
                kind=kind.value,
                ticket_id=ticket_id,
                project=project,
            )
        )
        return execution

    def set_phase(self, execution_id: str, phase: str) -> None:
        self.repo.set_phase(execution_id, phase)
        self.bus.publish(PhaseChanged(execution_id=execution_id, phase=phase))

    def record_agent_result(
        self, execution_id: str, agent: str, result: Dict[str, Any]
    ) -> None:
        self.repo.record_agent_result(execution_id, agent, result)

    def complete(self, execution: Execution) -> Execution:
        """Mark the run as ``succeeded``, publish ``ExecutionCompleted``."""
        self.repo.record_ended(execution.id, ExecutionStatus.SUCCEEDED)
        refreshed = self.repo.get(execution.id) or execution
        self.bus.publish(
            ExecutionCompleted(
                execution_id=execution.id,
                status=ExecutionStatus.SUCCEEDED.value,
                cost_cents=refreshed.cost_cents,
            )
        )
        return refreshed

    def fail(self, execution: Execution, error: str) -> Execution:
        self.repo.record_ended(execution.id, ExecutionStatus.FAILED, error=error)
        refreshed = self.repo.get(execution.id) or execution
        self.bus.publish(
            ExecutionFailed(execution_id=execution.id, error=error)
        )
        return refreshed

    # --------------------------------------------------------------- context

    @contextmanager
    def run(
        self,
        *,
        ticket_id: str,
        project: str,
        kind: ExecutionKind,
        options: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Execution]:
        """Context manager that pairs :meth:`begin` with :meth:`complete`/:meth:`fail`.

        Usage in a CLI command::

            with orchestrator.run(ticket_id=..., project=..., kind=ExecutionKind.EXECUTE) as execution:
                # drive agents, etc.
                ...
        """
        execution = self.begin(
            ticket_id=ticket_id, project=project, kind=kind, options=options
        )
        try:
            yield execution
        except BaseException as exc:
            try:
                self.fail(execution, error=str(exc) or type(exc).__name__)
            except Exception:
                logger.exception(
                    "orchestrator could not record failure for %s", execution.id
                )
            raise
        else:
            try:
                self.complete(execution)
            except Exception:
                logger.exception(
                    "orchestrator could not record success for %s", execution.id
                )

    # ---------------------------------------------------------- agent wiring

    def agent_kwargs(self, execution: Execution) -> Dict[str, Any]:
        """Return the kwargs an agent needs to emit events for this execution."""
        return {"event_bus": self.bus, "execution_id": execution.id}

    # Convenience — CLI commands that want to hand the orchestrator a single
    # callable rather than compose begin/complete/fail manually.

    def wrap(
        self,
        *,
        ticket_id: str,
        project: str,
        kind: ExecutionKind,
        runner: Callable[[Execution], Any],
        options: Optional[Dict[str, Any]] = None,
    ) -> Execution:
        """Run ``runner(execution)`` inside a managed lifecycle.

        ``runner`` receives the fresh :class:`Execution` and returns whatever
        it likes — the return value is ignored here; the agent-driven
        side-effects (agent_results rows, events, status transitions) are the
        observable output.
        """
        with self.run(
            ticket_id=ticket_id, project=project, kind=kind, options=options
        ) as execution:
            runner(execution)
        return self.repo.get(execution.id) or execution
