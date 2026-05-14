"""Integration tests for the cap-out side-effect chain.

Covers Phase 1 plan Task 9 + portions of Task 12 — every assertion here ties
to a specific exit-criterion box in the handover §7 list:

  - postmortem row inserted with provenance='auto' and fix_summary=NULL
  - MR reverted to draft regardless of prior state (D7)
  - exactly one MR comment posted on cap-out (D8)
  - PostmortemRecorded re-emitted so listeners see the row landed
  - if no MR exists yet, the postmortem still lands (persistence first)
  - subscriber-internal exceptions don't propagate
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from src.core.events import DeveloperCappedOut, EventBus
from src.core.execution.post_execute import (
    TicketContext,
    register_post_execute_subscribers,
)
from src.core.persistence import apply_migrations, connect


@pytest.fixture
def conn():
    """In-memory SQLite with all migrations applied + a parent execution row."""
    c = connect(":memory:")
    apply_migrations(c)
    c.execute(
        "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("exec-1", "TICKET-1", "execute", "running", "2026-05-08T00:00:00+00:00"),
    )
    c.commit()
    yield c
    c.close()


@pytest.fixture
def gitlab_client():
    client = Mock()
    client.mark_as_draft = Mock()
    client.add_merge_request_comment = Mock()
    return client


@pytest.fixture
def cap_out_event():
    return DeveloperCappedOut(
        execution_id="exec-1",
        ts="",
        agent="drupal_developer",
        attempts=3,
        last_structured_errors=[
            {
                "file": "web/modules/custom/foo.module",
                "line": 42,
                "rule": "phpstan.notFound",
                "message": "undefined method",
            }
        ],
    )


def _ticket_context(mr_iid=7, project="group/repo"):
    return TicketContext(
        execution_id="exec-1",
        stack_type="drupal-10",
        gitlab_project=project,
        mr_iid=mr_iid,
    )


def test_cap_out_writes_postmortem_reverts_draft_posts_one_comment(
    conn, gitlab_client, cap_out_event
):
    bus = EventBus(conn)
    register_post_execute_subscribers(
        bus,
        conn=conn,
        gitlab_client=gitlab_client,
        ticket_context=_ticket_context(),
    )

    bus.publish(cap_out_event)

    rows = conn.execute(
        "SELECT execution_id, stack_type, agent, failure_signature, "
        "context_excerpt, fix_summary, provenance "
        "FROM postmortems WHERE execution_id = ?",
        ("exec-1",),
    ).fetchall()
    assert len(rows) == 1, "expected exactly one postmortem row"
    row = rows[0]
    assert row["execution_id"] == "exec-1"
    assert row["stack_type"] == "drupal-10"
    assert row["agent"] == "drupal_developer"
    assert row["provenance"] == "auto"
    assert row["fix_summary"] is None
    assert row["failure_signature"]  # non-empty
    excerpt = json.loads(row["context_excerpt"])
    assert excerpt[0]["rule"] == "phpstan.notFound"

    # D7: mark_as_draft called once, with the static MR IID.
    assert gitlab_client.mark_as_draft.call_count == 1
    kwargs = gitlab_client.mark_as_draft.call_args.kwargs
    assert kwargs["project_id"] == "group/repo"
    assert kwargs["mr_iid"] == 7

    # D8: exactly one MR comment per cap-out.
    assert gitlab_client.add_merge_request_comment.call_count == 1
    comment_kwargs = gitlab_client.add_merge_request_comment.call_args.kwargs
    assert comment_kwargs["project_id"] == "group/repo"
    assert comment_kwargs["mr_iid"] == 7
    body = comment_kwargs["body"]
    assert "Sentinel paused here" in body
    assert "drupal_developer" in body
    assert "phpstan.notFound" in body
    assert "Postmortem #" in body

    # Re-emitted PostmortemRecorded lands after the cap-out row.
    events = bus.get_events("exec-1")
    types = [e["type"] for e in events]
    assert types == ["DeveloperCappedOut", "PostmortemRecorded"]
    pm_payload = json.loads(events[1]["payload_json"])
    assert pm_payload["failure_signature"] == row["failure_signature"]


def test_cap_out_with_no_mr_context_still_records_postmortem(
    conn, gitlab_client, cap_out_event
):
    bus = EventBus(conn)
    register_post_execute_subscribers(
        bus,
        conn=conn,
        gitlab_client=gitlab_client,
        ticket_context=TicketContext(
            execution_id="exec-1",
            stack_type="drupal-10",
            gitlab_project=None,
            mr_iid=None,
        ),
    )

    bus.publish(cap_out_event)

    rows = conn.execute("SELECT id FROM postmortems").fetchall()
    assert len(rows) == 1
    gitlab_client.mark_as_draft.assert_not_called()
    gitlab_client.add_merge_request_comment.assert_not_called()


def test_cap_out_with_mr_iid_resolver_calls_resolver_at_cap_out(
    conn, gitlab_client, cap_out_event
):
    """The resolver path is what the CLI uses — MR IID is unknown when the
    bus is wired and only resolved at cap-out time.
    """
    resolver = Mock(return_value=42)
    bus = EventBus(conn)
    register_post_execute_subscribers(
        bus,
        conn=conn,
        gitlab_client=gitlab_client,
        ticket_context=TicketContext(
            execution_id="exec-1",
            stack_type="drupal-10",
            gitlab_project="group/repo",
            mr_iid=None,
            mr_iid_resolver=resolver,
        ),
    )

    bus.publish(cap_out_event)

    resolver.assert_called_once_with()
    assert gitlab_client.mark_as_draft.call_args.kwargs["mr_iid"] == 42
    assert gitlab_client.add_merge_request_comment.call_args.kwargs["mr_iid"] == 42


def test_subscriber_exception_does_not_propagate(conn, cap_out_event):
    """If GitLab raises, the postmortem still lands and publish returns."""
    failing_client = Mock()
    failing_client.mark_as_draft = Mock(side_effect=RuntimeError("gitlab 500"))
    failing_client.add_merge_request_comment = Mock()

    bus = EventBus(conn)
    register_post_execute_subscribers(
        bus,
        conn=conn,
        gitlab_client=failing_client,
        ticket_context=_ticket_context(),
    )

    # Must not raise.
    bus.publish(cap_out_event)

    # Postmortem still recorded.
    rows = conn.execute("SELECT id FROM postmortems").fetchall()
    assert len(rows) == 1
    # Comment still attempted even though the draft revert failed — they're
    # independent best-effort steps.
    assert failing_client.add_merge_request_comment.call_count == 1
