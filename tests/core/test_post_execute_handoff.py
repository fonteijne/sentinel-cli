"""Tests for the ``ReviewerHandoffTriggered`` subscriber in ``post_execute.py``.

Phase 2B Loop C exit criterion: a reviewer handoff event must
  1. Write ``executions.phase = 'replan_needed'`` first (mandatory),
  2. Then revert the MR to draft (D7 — best-effort, idempotent),
  3. Then post **exactly one** MR comment matching the DECISIONS §168 template
     (D8 — comment-volume invariant),
  4. Swallow GitLab failures so they never lose the phase write,
  5. Skip GitLab side-effects entirely when no MR context is configured.

These tests mirror the cap-out subscriber tests in
``tests/integration/test_verifier_retry.py`` — same fixture style, same
assertion idiom — so the two subscribers are uniform under maintenance.
"""

from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from src.core.events import (
    DeveloperCappedOut,
    EventBus,
    ReviewerHandoffTriggered,
)
from src.core.execution.post_execute import (
    TicketContext,
    register_post_execute_subscribers,
)


# ---------------------------------------------------------------------------
# Local fixtures — mirrors tests/integration/test_verifier_retry.py style.
# ---------------------------------------------------------------------------


@pytest.fixture
def gitlab_client():
    """Recording mock with the two methods the subscriber calls."""
    client = Mock()
    client.mark_as_draft = Mock()
    client.add_merge_request_comment = Mock()
    return client


@pytest.fixture
def ticket_ctx():
    return TicketContext(
        execution_id="test-exec-1",
        stack_type="drupal",
        gitlab_project="acme/site",
        mr_iid=42,
    )


def _handoff_event(
    *,
    reviewer_agent: str = "drupal_reviewer",
    finding_class: str = "service-injection,missing-hook",
    blocker_count: int = 2,
) -> ReviewerHandoffTriggered:
    return ReviewerHandoffTriggered(
        execution_id="test-exec-1",
        ts="",
        reviewer_agent=reviewer_agent,
        finding_class=finding_class,
        blocker_count=blocker_count,
        next_actor="planner",
    )


def _phase_for(conn, exec_id: str) -> str | None:
    row = conn.execute(
        "SELECT phase FROM executions WHERE id = ?", (exec_id,)
    ).fetchone()
    return row["phase"] if row is not None else None


# ---------------------------------------------------------------------------
# 1. Happy path — phase + draft + exactly-one comment, body verbatim.
# ---------------------------------------------------------------------------


def test_handoff_writes_phase_and_one_comment(
    event_bus, sqlite_mem_conn, gitlab_client, ticket_ctx
):
    register_post_execute_subscribers(
        event_bus,
        conn=sqlite_mem_conn,
        gitlab_client=gitlab_client,
        ticket_context=ticket_ctx,
    )

    event_bus.publish(_handoff_event())

    assert _phase_for(sqlite_mem_conn, "test-exec-1") == "replan_needed"

    # Exactly one comment, body matches DECISIONS §168 template verbatim.
    assert gitlab_client.add_merge_request_comment.call_count == 1
    body = gitlab_client.add_merge_request_comment.call_args.kwargs["body"]
    assert body == (
        "Drupal Reviewer found 2 blockers "
        "(service-injection,missing-hook). Re-running Planner."
    )

    # mark_as_draft called exactly once with the static MR coordinates.
    assert gitlab_client.mark_as_draft.call_count == 1
    draft_kwargs = gitlab_client.mark_as_draft.call_args.kwargs
    assert draft_kwargs == {"project_id": "acme/site", "mr_iid": 42}


# ---------------------------------------------------------------------------
# 2. Singular blocker — security_reviewer + 1 blocker → no plural 's'.
# ---------------------------------------------------------------------------


def test_handoff_singular_blocker_phrasing(
    event_bus, sqlite_mem_conn, gitlab_client, ticket_ctx
):
    register_post_execute_subscribers(
        event_bus,
        conn=sqlite_mem_conn,
        gitlab_client=gitlab_client,
        ticket_context=ticket_ctx,
    )

    event_bus.publish(
        _handoff_event(
            reviewer_agent="security_reviewer",
            finding_class="xss",
            blocker_count=1,
        )
    )

    body = gitlab_client.add_merge_request_comment.call_args.kwargs["body"]
    assert body == (
        "Security Reviewer found 1 blocker "
        "(xss). Re-running Planner."
    )


# ---------------------------------------------------------------------------
# 3. No MR context — phase still written, zero GitLab calls, warning logged.
# ---------------------------------------------------------------------------


def test_handoff_no_mr_context_writes_phase_only(
    event_bus, sqlite_mem_conn, gitlab_client, caplog
):
    register_post_execute_subscribers(
        event_bus,
        conn=sqlite_mem_conn,
        gitlab_client=gitlab_client,
        ticket_context=TicketContext(
            execution_id="test-exec-1",
            stack_type="drupal",
            gitlab_project=None,
            mr_iid=None,
        ),
    )

    with caplog.at_level(logging.WARNING):
        event_bus.publish(_handoff_event())

    assert _phase_for(sqlite_mem_conn, "test-exec-1") == "replan_needed"
    gitlab_client.mark_as_draft.assert_not_called()
    gitlab_client.add_merge_request_comment.assert_not_called()
    # The warning text from the subscriber.
    assert any(
        "phase written but skipping draft+comment" in rec.message
        for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# 4. mark_as_draft raises — comment STILL posts (D7 best-effort).
# ---------------------------------------------------------------------------


def test_handoff_draft_failure_does_not_block_comment(
    event_bus, sqlite_mem_conn, ticket_ctx
):
    failing = Mock()
    failing.mark_as_draft = Mock(side_effect=RuntimeError("503"))
    failing.add_merge_request_comment = Mock()

    register_post_execute_subscribers(
        event_bus,
        conn=sqlite_mem_conn,
        gitlab_client=failing,
        ticket_context=ticket_ctx,
    )

    event_bus.publish(_handoff_event())

    assert _phase_for(sqlite_mem_conn, "test-exec-1") == "replan_needed"
    assert failing.add_merge_request_comment.call_count == 1


# ---------------------------------------------------------------------------
# 5. add_merge_request_comment raises — swallowed; phase still written;
#    the subscriber must NOT retry.
# ---------------------------------------------------------------------------


def test_handoff_comment_failure_is_swallowed(
    event_bus, sqlite_mem_conn, ticket_ctx, caplog
):
    failing = Mock()
    failing.mark_as_draft = Mock()
    failing.add_merge_request_comment = Mock(
        side_effect=RuntimeError("gitlab-down")
    )

    register_post_execute_subscribers(
        event_bus,
        conn=sqlite_mem_conn,
        gitlab_client=failing,
        ticket_context=ticket_ctx,
    )

    with caplog.at_level(logging.ERROR):
        event_bus.publish(_handoff_event())

    assert _phase_for(sqlite_mem_conn, "test-exec-1") == "replan_needed"
    # Exactly one attempt — no retry.
    assert failing.add_merge_request_comment.call_count == 1
    assert any(
        "handoff MR comment failed" in rec.message for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# 6. Phase write fails — outer try surfaces; comment NOT posted (ordering).
# ---------------------------------------------------------------------------


class _FailingExecuteConn:
    """Pass-through wrapper around a sqlite3.Connection whose ``execute`` raises
    on the ``UPDATE executions ...`` statement only. Used to simulate a DB lock
    on the phase write without touching read-only sqlite3.Connection slots.
    """

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, *args, **kwargs):
        if isinstance(sql, str) and sql.strip().upper().startswith("UPDATE EXECUTIONS"):
            raise RuntimeError("db-locked")
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        # Delegate everything else (commit, close, row_factory, …).
        return getattr(self._conn, name)


def test_handoff_phase_write_failure_skips_comment(
    event_bus, sqlite_mem_conn, gitlab_client, ticket_ctx, caplog
):
    """If the UPDATE raises, the subscriber's outer try logs and bails BEFORE
    any side-effects fire. The phase-then-side-effects ordering is what
    guarantees we never claim 'Re-running Planner' on an unmarked execution.
    """
    failing_conn = _FailingExecuteConn(sqlite_mem_conn)

    register_post_execute_subscribers(
        event_bus,
        conn=failing_conn,  # type: ignore[arg-type]
        gitlab_client=gitlab_client,
        ticket_context=ticket_ctx,
    )

    with caplog.at_level(logging.ERROR):
        event_bus.publish(_handoff_event())

    # Phase wasn't actually written — read directly through the real conn.
    assert _phase_for(sqlite_mem_conn, "test-exec-1") in (None, "")
    # No GitLab side-effects.
    gitlab_client.mark_as_draft.assert_not_called()
    gitlab_client.add_merge_request_comment.assert_not_called()
    # Outer try logged.
    assert any(
        "post_execute handoff handler crashed" in rec.message
        for rec in caplog.records
    )


# ---------------------------------------------------------------------------
# 7. Loop A vs Loop C — comment-volume invariant (DECISIONS §180).
# ---------------------------------------------------------------------------


def test_loop_a_only_publishes_zero_handoff_comments(
    event_bus, sqlite_mem_conn, gitlab_client, ticket_ctx
):
    """A pure cap-out (Loop A) event must NOT produce a 'Re-running Planner'
    comment. Cap-out has its own one comment ("Sentinel paused here…"); the
    handoff template never appears unless ReviewerHandoffTriggered fires.
    """
    register_post_execute_subscribers(
        event_bus,
        conn=sqlite_mem_conn,
        gitlab_client=gitlab_client,
        ticket_context=ticket_ctx,
    )

    event_bus.publish(
        DeveloperCappedOut(
            execution_id="test-exec-1",
            ts="",
            agent="drupal_developer",
            attempts=3,
            last_structured_errors=[
                {
                    "file": "x.module",
                    "line": 1,
                    "rule": "phpstan.notFound",
                    "message": "boom",
                }
            ],
        )
    )

    bodies = [
        c.kwargs["body"] for c in gitlab_client.add_merge_request_comment.call_args_list
    ]
    assert all("Re-running Planner" not in b for b in bodies)


def test_loop_c_only_publishes_exactly_one_handoff_comment(
    event_bus, sqlite_mem_conn, gitlab_client, ticket_ctx
):
    register_post_execute_subscribers(
        event_bus,
        conn=sqlite_mem_conn,
        gitlab_client=gitlab_client,
        ticket_context=ticket_ctx,
    )

    event_bus.publish(_handoff_event())

    bodies = [
        c.kwargs["body"] for c in gitlab_client.add_merge_request_comment.call_args_list
    ]
    handoff_bodies = [b for b in bodies if "Re-running Planner" in b]
    assert len(handoff_bodies) == 1
