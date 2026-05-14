"""Phase 3A — exit-criterion fixture (PRD line 496-497).

The single, gating integration test for Phase 3A: a project with one merged
MR, one reverted MR, and one post-merge-pipeline-failure MR must produce the
correct three tags, advance the watermark, and be a no-op on a second sync.

This test goes through ``OutcomeSyncService`` directly (not through the
Click CLI) so the failure mode is service-level — Click's surface is
exercised by ``tests/test_cli_outcomes.py``.

Plan ref: phase-3a-outcome-ingestion.plan.md task 11.c.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import Mock

import pytest

from src.core.learning.outcome_sync import OutcomeSyncService
from src.core.persistence import read_sync_state
from src.gitlab_client import GitLabClient


def _seed_execution(
    conn: sqlite3.Connection,
    *,
    execution_id: str,
    ticket_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO executions (id, ticket_id, kind, status, created_at)
        VALUES (?, ?, 'execute', 'running', ?)
        """,
        (execution_id, ticket_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _make_mr(
    *,
    iid: int,
    ticket_id: str,
    updated_at: str,
    merge_commit_sha: str,
    title: str = "Fix the thing",
) -> Dict[str, Any]:
    return {
        "iid": iid,
        "source_branch": f"sentinel/feature/{ticket_id}",
        "target_branch": "main",
        "updated_at": updated_at,
        "merged_at": updated_at,
        "merge_commit_sha": merge_commit_sha,
        "state": "merged",
        "title": title,
    }


def test_phase3a_exit_criterion(sqlite_mem_conn, event_bus):
    """Three MRs (success, rolled_back, regressed) → tagged correctly,
    watermark advances, second sync.summary.mrs_seen == 0.

    PRD line 496-497: "on a fixture project with a known merged MR and a
    known reverted MR, ``sentinel outcomes sync`` correctly tags the matching
    ``execution_id``s; the watermark advances; a re-run does not re-paginate.
    A post-merge pipeline failure on ``main`` tags the originating
    ``execution_id`` as ``regressed``."
    """
    # --- seed three executions alongside the conftest-seeded TEST-1 row ---
    _seed_execution(sqlite_mem_conn, execution_id="exec-1", ticket_id="ACME-1")
    _seed_execution(sqlite_mem_conn, execution_id="exec-2", ticket_id="ACME-2")
    _seed_execution(sqlite_mem_conn, execution_id="exec-3", ticket_id="ACME-3")

    # --- build three MRs in updated_at-ascending order ---
    mr_success = _make_mr(
        iid=101,
        ticket_id="ACME-1",
        updated_at="2026-05-01T00:00:00Z",
        merge_commit_sha="aaa11111aaaaaaaa",
        title="Fix ACME-1",
    )
    mr_rolled_back = _make_mr(
        iid=102,
        ticket_id="ACME-2",
        updated_at="2026-05-02T00:00:00Z",
        merge_commit_sha="bbb22222bbbbbbbb",
        title="Fix ACME-2",
    )
    mr_regressed = _make_mr(
        iid=103,
        ticket_id="ACME-3",
        updated_at="2026-05-03T00:00:00Z",
        merge_commit_sha="ccc33333cccccccc",
        title="Fix ACME-3",
    )

    # --- mock GitLab client ---
    gl = Mock(spec=GitLabClient)
    gl.list_merged_mrs_since.return_value = [
        mr_success,
        mr_rolled_back,
        mr_regressed,
    ]

    # Pipelines: only ACME-3 has a failed pipeline.
    def pipelines_side_effect(
        project_id: str, *, sha: str, ref: str = "main"
    ) -> List[Dict[str, Any]]:
        if sha == "ccc33333cccccccc":
            return [{"id": 99, "status": "failed", "ref": "main"}]
        return []

    gl.list_pipelines_for_commit.side_effect = pipelines_side_effect

    # Revert MR detection: list_merge_requests returns one Revert pointing at
    # ACME-2's title; service filters by 'Revert "<original title>"'.
    revert_mr = {
        "iid": 200,
        "state": "merged",
        "title": 'Revert "Fix ACME-2"',
    }
    gl.list_merge_requests.return_value = [revert_mr]

    # --- run sync ---
    service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=event_bus)
    summary = service.sync(project="acme/backend")

    # --- assertions: tag counts ---
    assert summary.executions_tagged == 3, (
        f"expected all three ACME-* executions tagged, got "
        f"{summary.executions_tagged}; errors={summary.errors}"
    )
    assert summary.tag_counts.get("success", 0) == 1
    assert summary.tag_counts.get("rolled_back", 0) == 1
    assert summary.tag_counts.get("regressed", 0) == 1

    # --- per-execution outcomes ---
    rows = {
        r["id"]: r["outcome"]
        for r in sqlite_mem_conn.execute(
            "SELECT id, outcome FROM executions WHERE id LIKE 'exec-%'"
        ).fetchall()
    }
    assert rows["exec-1"] == "success"
    assert rows["exec-2"] == "rolled_back"
    assert rows["exec-3"] == "regressed"

    # --- exactly three OutcomeRecorded events ---
    n_events = sqlite_mem_conn.execute(
        "SELECT COUNT(*) FROM events WHERE type='OutcomeRecorded'"
    ).fetchone()[0]
    assert n_events == 3

    # --- watermark advanced to max updated_at ---
    state = read_sync_state(sqlite_mem_conn, "acme/backend")
    assert state is not None
    assert state["last_seen_updated_at"] == "2026-05-03T00:00:00Z"

    # --- second sync is a no-op (simulate watermark filter via mock) ---
    gl.list_merged_mrs_since.return_value = []
    summary2 = service.sync(project="acme/backend")
    assert summary2.mrs_seen == 0
    assert summary2.executions_tagged == 0

    # No new events on second sync.
    n_events_after = sqlite_mem_conn.execute(
        "SELECT COUNT(*) FROM events WHERE type='OutcomeRecorded'"
    ).fetchone()[0]
    assert n_events_after == 3
