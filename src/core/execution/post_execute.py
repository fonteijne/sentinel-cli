"""Subscriber that handles ``DeveloperCappedOut``: postmortem + draft + 1 MR comment.

Side-effect order is load-bearing:

  1. Persist the postmortem row first. Persistence is mandatory; the row is
     what Phase 2's extraction job consumes. If it doesn't land, the event is
     wasted.
  2. Best-effort GitLab side-effects: revert the MR to draft (D7), then post
     exactly one cap-out comment (D8 — no per-retry comments). Failures here
     are logged and swallowed; a flaky GitLab API must not lose the postmortem.
  3. Re-emit ``PostmortemRecorded`` so listeners (and tests) can assert that the
     row landed without re-querying SQLite.

The subscriber catches its own top-level exceptions as a last line of defense:
``EventBus`` already swallows handler exceptions, but logging here gives a
diagnostic trail when the inner ``try`` blocks miss something unexpected.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional, cast

from src.agents._structured_errors import StructuredError, normalize_failure_signature
from src.core.events import (
    BaseEvent,
    DeveloperCappedOut,
    EventBus,
    PostmortemRecorded,
    ReviewerHandoffTriggered,
)
from src.core.persistence import insert_postmortem

logger = logging.getLogger(__name__)


REVIEWER_PRETTY: dict[str, str] = {
    "drupal_reviewer": "Drupal Reviewer",
    "security_reviewer": "Security Reviewer",
}


def format_handoff_comment(event: ReviewerHandoffTriggered) -> str:
    """Render a DECISIONS §168-compliant single-line MR comment.

    Never paraphrases reviewer text — ``finding_class`` is computed from
    machine-readable fields at the workflow boundary.
    """
    pretty = REVIEWER_PRETTY.get(event.reviewer_agent, event.reviewer_agent)
    plural = "s" if event.blocker_count != 1 else ""
    return (
        f"{pretty} found {event.blocker_count} blocker{plural} "
        f"({event.finding_class}). Re-running Planner."
    )


@dataclass
class TicketContext:
    """Context the cap-out subscriber needs to apply side-effects.

    ``mr_iid_resolver`` is preferred over ``mr_iid``: the MR may not exist when
    the bus is wired up (cap-out happens at end of execution, MR creation in
    the middle), so the CLI hands us a callable that does the lookup at
    cap-out time. ``mr_iid`` is kept as a static fallback for tests and for
    the revision flow where the MR is known up front.

    Resolver wins when both are set. Resolver returning ``None`` means "no MR
    yet" — the subscriber records the postmortem and skips GitLab side-effects.
    """

    execution_id: str
    stack_type: str
    gitlab_project: Optional[str]
    mr_iid: Optional[int] = None
    mr_iid_resolver: Optional[Callable[[], Optional[int]]] = None


def register_post_execute_subscribers(
    bus: EventBus,
    *,
    conn: sqlite3.Connection,
    gitlab_client: object,
    ticket_context: TicketContext,
) -> None:
    """Wire post-execution handlers (DeveloperCappedOut, ReviewerHandoffTriggered).

    The closure captures ``bus`` so the handler can re-emit
    ``PostmortemRecorded`` without the caller threading the bus a second time.
    """

    def _resolve_mr_iid() -> Optional[int]:
        if ticket_context.mr_iid_resolver is not None:
            try:
                return ticket_context.mr_iid_resolver()
            except Exception as exc:
                logger.error("mr_iid_resolver raised: %s", exc, exc_info=True)
                return ticket_context.mr_iid
        return ticket_context.mr_iid

    def _handle(event: BaseEvent) -> None:
        if not isinstance(event, DeveloperCappedOut):  # defensive — bus already filters by type
            return
        try:
            errors = event.last_structured_errors or []
            signature = normalize_failure_signature(cast(list[StructuredError], errors))
            excerpt = json.dumps(errors)[:4096]

            pid = insert_postmortem(
                conn,
                execution_id=ticket_context.execution_id,
                stack_type=ticket_context.stack_type,
                agent=event.agent,
                failure_signature=signature,
                context_excerpt=excerpt,
                fix_summary=None,
                provenance="auto",
            )
            logger.info(
                "Postmortem #%d recorded for execution %s",
                pid,
                ticket_context.execution_id,
            )

            mr_iid = _resolve_mr_iid()
            if ticket_context.gitlab_project and mr_iid:
                # D7: revert to draft regardless of prior state — the helper is
                # idempotent, so it's fine to call without checking first.
                try:
                    gitlab_client.mark_as_draft(  # type: ignore[attr-defined]
                        project_id=ticket_context.gitlab_project,
                        mr_iid=mr_iid,
                    )
                except Exception as exc:
                    logger.error("mark_as_draft failed: %s", exc, exc_info=True)

                # D8: exactly one comment. This is the only call into
                # add_merge_request_comment in the cap-out path.
                try:
                    first_rule = errors[0].get("rule", "unknown") if errors else "unknown"
                    body = (
                        f"**Sentinel paused here** — developer agent (`{event.agent}`) "
                        f"capped at {event.attempts} attempts on this task. "
                        f"First error: `{first_rule}`. Postmortem #{pid} recorded."
                    )
                    gitlab_client.add_merge_request_comment(  # type: ignore[attr-defined]
                        project_id=ticket_context.gitlab_project,
                        mr_iid=mr_iid,
                        body=body,
                    )
                except Exception as exc:
                    logger.error("cap-out MR comment failed: %s", exc, exc_info=True)
            else:
                logger.warning(
                    "No MR context — skipping draft revert and cap-out comment "
                    "(execution=%s)",
                    ticket_context.execution_id,
                )

            # Re-emit so observers (and integration tests) can assert the row
            # landed without re-querying SQLite.
            bus.publish(
                PostmortemRecorded(
                    execution_id=ticket_context.execution_id,
                    ts="",
                    postmortem_id=pid,
                    failure_signature=signature,
                )
            )
        except Exception:
            # The bus already swallows handler exceptions; this is the last
            # diagnostic surface before the trace is gone.
            logger.error("post_execute handler crashed", exc_info=True)

    bus.subscribe(DeveloperCappedOut, _handle)

    def _handle_handoff(event: BaseEvent) -> None:
        if not isinstance(event, ReviewerHandoffTriggered):
            return
        try:
            # 1. MANDATORY: write phase before any side-effects so a comment
            # claiming "re-running Planner" is never posted without the row
            # actually marked replan_needed.
            conn.execute(
                "UPDATE executions SET phase = ? WHERE id = ?",
                ("replan_needed", ticket_context.execution_id),
            )
            conn.commit()
            logger.info(
                "Execution %s: phase=replan_needed (reviewer=%s, blockers=%d)",
                ticket_context.execution_id,
                event.reviewer_agent,
                event.blocker_count,
            )

            mr_iid = _resolve_mr_iid()
            if ticket_context.gitlab_project and mr_iid:
                # D7: idempotent draft revert
                try:
                    gitlab_client.mark_as_draft(  # type: ignore[attr-defined]
                        project_id=ticket_context.gitlab_project,
                        mr_iid=mr_iid,
                    )
                except Exception as exc:
                    logger.error("mark_as_draft failed (handoff): %s", exc, exc_info=True)
                # D8: exactly one comment per handoff event
                try:
                    body = format_handoff_comment(event)
                    gitlab_client.add_merge_request_comment(  # type: ignore[attr-defined]
                        project_id=ticket_context.gitlab_project,
                        mr_iid=mr_iid,
                        body=body,
                    )
                except Exception as exc:
                    logger.error("handoff MR comment failed: %s", exc, exc_info=True)
            else:
                logger.warning(
                    "No MR context — phase written but skipping draft+comment "
                    "(execution=%s)",
                    ticket_context.execution_id,
                )
        except Exception:
            logger.error("post_execute handoff handler crashed", exc_info=True)

    bus.subscribe(ReviewerHandoffTriggered, _handle_handoff)
