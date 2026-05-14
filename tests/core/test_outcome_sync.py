"""Phase 3A — outcome ingestion service unit tests.

Three concerns under test:

  1. ``classify_outcome`` — pure function; severity order and evidence shape.
  2. ``OutcomeSyncService`` — end-to-end with in-memory SQLite + mocked
     GitLabClient; covers branch matching, append-once semantics, watermark
     advancement, idempotent re-run, dry-run silence, and per-MR error
     containment.
  3. ``sync_state`` helpers — append-once UPDATE invariant and upsert
     conflict-replace behavior.

Plan ref: phase-3a-outcome-ingestion.plan.md task 11.b.

Fixtures: re-uses ``sqlite_mem_conn`` and ``event_bus`` from
``tests/conftest.py``. Do NOT redefine — see the test rules at the top of
the plan.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

import pytest
import requests

from src.core.learning.outcome_sync import (
    OutcomeSyncService,
    OutcomeSyncSummary,
    classify_outcome,
)
from src.core.persistence import (
    read_sync_state,
    update_execution_outcome,
    upsert_sync_state,
)
from src.gitlab_client import GitLabClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_execution(
    conn: sqlite3.Connection,
    *,
    execution_id: str,
    ticket_id: str,
    created_at: Optional[str] = None,
    outcome: Optional[str] = None,
) -> None:
    """Insert one row into ``executions`` for tests that need extra runs."""
    conn.execute(
        """
        INSERT INTO executions (id, ticket_id, kind, status, created_at, outcome)
        VALUES (?, ?, 'execute', 'running', ?, ?)
        """,
        (
            execution_id,
            ticket_id,
            created_at or datetime.now(timezone.utc).isoformat(),
            outcome,
        ),
    )
    conn.commit()


def _make_mr(
    *,
    iid: int,
    ticket_id: str = "TEST-1",
    updated_at: str = "2026-05-01T00:00:00Z",
    merge_commit_sha: str = "deadbeefcafe1234",
    title: str = "Fix the thing",
    target_branch: str = "main",
) -> Dict[str, Any]:
    return {
        "iid": iid,
        "source_branch": f"sentinel/feature/{ticket_id}",
        "target_branch": target_branch,
        "updated_at": updated_at,
        "merged_at": updated_at,
        "merge_commit_sha": merge_commit_sha,
        "state": "merged",
        "title": title,
    }


def _make_gitlab_mock(
    *,
    merged_mrs: Optional[List[Dict[str, Any]]] = None,
    pipelines: Optional[List[Dict[str, Any]]] = None,
    merge_requests: Optional[List[Dict[str, Any]]] = None,
) -> Mock:
    """Build a ``Mock(spec=GitLabClient)`` with the three methods used by sync."""
    gl = Mock(spec=GitLabClient)
    gl.list_merged_mrs_since.return_value = merged_mrs or []
    gl.list_pipelines_for_commit.return_value = pipelines or []
    # `_find_revert_mr` calls list_merge_requests(state='merged').
    gl.list_merge_requests.return_value = merge_requests or []
    return gl


# ---------------------------------------------------------------------------
# 1. classify_outcome — pure function
# ---------------------------------------------------------------------------


class TestClassifyOutcome:
    """Severity table for classify_outcome. No DB, no GitLab client."""

    def test_clean_merge_is_success(self):
        mr = _make_mr(iid=1)
        outcome, evidence = classify_outcome(mr, pipelines=[], revert_mr=None)
        assert outcome == "success"
        assert evidence["outcome"] == "success"
        assert evidence["mr_iid"] == 1
        assert evidence["merge_commit_sha"] == "deadbeefcafe1234"
        # No pipeline_id or revert_mr_iid on success.
        assert "pipeline_id" not in evidence
        assert "revert_mr_iid" not in evidence

    def test_revert_mr_merged_is_rolled_back(self):
        mr = _make_mr(iid=10, title="Fix flaky test")
        revert = {
            "iid": 200,
            "state": "merged",
            "title": 'Revert "Fix flaky test"',
        }
        outcome, evidence = classify_outcome(mr, pipelines=[], revert_mr=revert)
        assert outcome == "rolled_back"
        assert evidence["revert_mr_iid"] == 200
        assert "pipeline_id" not in evidence

    def test_revert_mr_open_is_not_rolled_back(self):
        """A revert MR that hasn't merged yet is not yet ground truth."""
        mr = _make_mr(iid=11, title="Fix flaky test")
        revert = {
            "iid": 201,
            "state": "opened",  # NOT merged
            "title": 'Revert "Fix flaky test"',
        }
        outcome, _ = classify_outcome(mr, pipelines=[], revert_mr=revert)
        assert outcome == "success"

    def test_post_merge_pipeline_failed_is_regressed(self):
        mr = _make_mr(iid=20)
        pipelines = [{"id": 99, "status": "failed", "ref": "main"}]
        outcome, evidence = classify_outcome(mr, pipelines=pipelines, revert_mr=None)
        assert outcome == "regressed"
        assert evidence["pipeline_id"] == 99
        assert evidence["pipeline_status"] == "failed"

    def test_severity_order_regressed_beats_rolled_back(self):
        """Both signals present → regressed wins."""
        mr = _make_mr(iid=30)
        pipelines = [{"id": 100, "status": "failed", "ref": "main"}]
        revert = {"iid": 300, "state": "merged", "title": 'Revert "..."'}
        outcome, evidence = classify_outcome(mr, pipelines=pipelines, revert_mr=revert)
        assert outcome == "regressed"
        assert evidence["pipeline_id"] == 100
        # rolled_back evidence should not appear on a regressed result.
        assert "revert_mr_iid" not in evidence

    def test_pending_pipeline_does_not_mark_regressed(self):
        """status='running' is non-terminal — defer to next sync."""
        mr = _make_mr(iid=40)
        pipelines = [{"id": 50, "status": "running", "ref": "main"}]
        outcome, _ = classify_outcome(mr, pipelines=pipelines, revert_mr=None)
        assert outcome == "success"


# ---------------------------------------------------------------------------
# 2. OutcomeSyncService — DB + mocked GitLab end-to-end
# ---------------------------------------------------------------------------


class TestOutcomeSyncService:
    """Service-level tests using sqlite_mem_conn + Mock(spec=GitLabClient)."""

    def test_tags_executions_for_matching_branch(self, sqlite_mem_conn, event_bus):
        """One MR matching ticket_id=TEST-1 → execution.outcome='success'."""
        mr = _make_mr(iid=1, ticket_id="TEST-1")
        gl = _make_gitlab_mock(merged_mrs=[mr])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=event_bus)

        summary = service.sync(project="acme/backend")

        assert summary.executions_tagged == 1
        assert summary.tag_counts == {"success": 1}
        row = sqlite_mem_conn.execute(
            "SELECT outcome FROM executions WHERE id = 'test-exec-1'"
        ).fetchone()
        assert row["outcome"] == "success"

    def test_does_not_overwrite_existing_outcome(self, sqlite_mem_conn):
        """Pre-tagged execution stays unchanged (append-once)."""
        # Pre-set the outcome.
        sqlite_mem_conn.execute(
            "UPDATE executions SET outcome='success', "
            "outcome_evidence_json='{}', outcome_recorded_at=? "
            "WHERE id='test-exec-1'",
            (datetime.now(timezone.utc).isoformat(),),
        )
        sqlite_mem_conn.commit()

        mr = _make_mr(iid=1, ticket_id="TEST-1")
        gl = _make_gitlab_mock(merged_mrs=[mr])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        summary = service.sync(project="acme/backend")

        assert summary.executions_tagged == 0
        # Outcome was not overwritten.
        row = sqlite_mem_conn.execute(
            "SELECT outcome FROM executions WHERE id = 'test-exec-1'"
        ).fetchone()
        assert row["outcome"] == "success"

    def test_advances_watermark_to_last_seen_updated_at(self, sqlite_mem_conn):
        """Watermark is the max updated_at across all handled MRs."""
        # Three MRs, ascending updated_at.
        mrs = [
            _make_mr(iid=1, ticket_id="TEST-1", updated_at="2026-05-01T00:00:00Z"),
            _make_mr(iid=2, ticket_id="TEST-2", updated_at="2026-05-02T00:00:00Z"),
            _make_mr(iid=3, ticket_id="TEST-3", updated_at="2026-05-03T00:00:00Z"),
        ]
        gl = _make_gitlab_mock(merged_mrs=mrs)
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        summary = service.sync(project="acme/backend")

        assert summary.watermark_advanced_to == "2026-05-03T00:00:00Z"
        state = read_sync_state(sqlite_mem_conn, "acme/backend")
        assert state is not None
        assert state["last_seen_updated_at"] == "2026-05-03T00:00:00Z"

    def test_idempotent_rerun_does_not_reprocess(self, sqlite_mem_conn):
        """Second sync — when GitLab returns no MRs above the watermark — is a no-op."""
        mr = _make_mr(iid=1, ticket_id="TEST-1", updated_at="2026-05-01T00:00:00Z")
        gl = _make_gitlab_mock(merged_mrs=[mr])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        summary1 = service.sync(project="acme/backend")
        assert summary1.executions_tagged == 1

        # Now simulate "no new MRs since watermark".
        gl.list_merged_mrs_since.return_value = []
        summary2 = service.sync(project="acme/backend")

        assert summary2.mrs_seen == 0
        assert summary2.executions_tagged == 0
        assert summary2.tag_counts == {}

    def test_dry_run_writes_nothing_and_publishes_no_events(
        self, sqlite_mem_conn, event_bus
    ):
        """dry_run=True → no UPDATE, no event row, watermark untouched."""
        mr = _make_mr(iid=1, ticket_id="TEST-1")
        gl = _make_gitlab_mock(merged_mrs=[mr])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=event_bus)

        summary = service.sync(project="acme/backend", dry_run=True)

        assert summary.dry_run is True
        # Counters reflect would-be impact.
        assert summary.executions_tagged == 1
        # ...but the row was not actually updated.
        row = sqlite_mem_conn.execute(
            "SELECT outcome FROM executions WHERE id='test-exec-1'"
        ).fetchone()
        assert row["outcome"] is None
        # No events written.
        event_rows = sqlite_mem_conn.execute(
            "SELECT COUNT(*) FROM events WHERE type='OutcomeRecorded'"
        ).fetchone()
        assert event_rows[0] == 0
        # Watermark not advanced.
        assert read_sync_state(sqlite_mem_conn, "acme/backend") is None

    def test_branch_without_sentinel_prefix_is_skipped(self, sqlite_mem_conn):
        """An MR on hotfix/x is counted as seen but tags nothing."""
        mr = {
            "iid": 1,
            "source_branch": "hotfix/x",
            "target_branch": "main",
            "updated_at": "2026-05-01T00:00:00Z",
            "merged_at": "2026-05-01T00:00:00Z",
            "merge_commit_sha": "abc",
            "state": "merged",
            "title": "Hotfix",
        }
        gl = _make_gitlab_mock(merged_mrs=[mr])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        summary = service.sync(project="acme/backend")

        assert summary.mrs_seen == 1
        assert summary.executions_tagged == 0
        # The seeded TEST-1 row is untagged.
        row = sqlite_mem_conn.execute(
            "SELECT outcome FROM executions WHERE id='test-exec-1'"
        ).fetchone()
        assert row["outcome"] is None

    def test_pipeline_lookup_failure_does_not_abort_sync(self, sqlite_mem_conn):
        """A 5xx from list_pipelines_for_commit still tags the MR as success."""
        mr = _make_mr(iid=1, ticket_id="TEST-1")
        gl = _make_gitlab_mock(merged_mrs=[mr])
        gl.list_pipelines_for_commit.side_effect = requests.HTTPError("500")
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        summary = service.sync(project="acme/backend")

        assert summary.executions_tagged == 1
        assert summary.tag_counts == {"success": 1}
        # Error captured on summary.errors.
        assert any("list_pipelines_for_commit" in e for e in summary.errors)

    def test_revert_lookup_failure_does_not_abort_sync(self, sqlite_mem_conn):
        """A 5xx from list_merge_requests classifies as success + records error."""
        mr = _make_mr(iid=1, ticket_id="TEST-1")
        gl = _make_gitlab_mock(merged_mrs=[mr])
        gl.list_merge_requests.side_effect = requests.HTTPError("500")
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        summary = service.sync(project="acme/backend")

        assert summary.executions_tagged == 1
        assert summary.tag_counts == {"success": 1}
        assert any("revert lookup" in e for e in summary.errors)

    def test_publishes_outcome_recorded_per_tagged_execution(
        self, sqlite_mem_conn, event_bus
    ):
        """Two executions for same ticket → two OutcomeRecorded events."""
        # Add a second execution for TEST-1.
        _seed_execution(
            sqlite_mem_conn,
            execution_id="test-exec-2",
            ticket_id="TEST-1",
            created_at="2026-04-30T00:00:00Z",
        )
        mr = _make_mr(iid=1, ticket_id="TEST-1")
        gl = _make_gitlab_mock(merged_mrs=[mr])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=event_bus)

        summary = service.sync(project="acme/backend")

        assert summary.executions_tagged == 2
        rows = sqlite_mem_conn.execute(
            "SELECT execution_id, type, payload_json "
            "FROM events WHERE type='OutcomeRecorded' "
            "ORDER BY execution_id"
        ).fetchall()
        assert len(rows) == 2
        execution_ids = {r["execution_id"] for r in rows}
        assert execution_ids == {"test-exec-1", "test-exec-2"}
        # Spot-check payload shape.
        import json

        payload = json.loads(rows[0]["payload_json"])
        assert payload["mr_iid"] == 1
        assert payload["project"] == "acme/backend"
        assert payload["outcome"] == "success"


# ---------------------------------------------------------------------------
# 2b. Revert-lookup hoist optimization (H5)
# ---------------------------------------------------------------------------


class TestRevertLookupOptimization:
    """H5: ``list_merge_requests`` is called at most once per ``sync()``.

    Before H5: O(M) calls (one per Sentinel-owned MR processed).
    After  H5: O(1) call hoisted into ``sync()``, narrowed by ``created_after``.

    These tests pin the load-bearing optimization invariant + verify
    classification semantics are byte-identical pre/post-refactor.
    """

    def test_called_once_per_sync_with_one_mr(self, sqlite_mem_conn):
        mr = _make_mr(iid=1, ticket_id="TEST-1")
        gl = _make_gitlab_mock(merged_mrs=[mr])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        service.sync(project="acme/backend")

        assert gl.list_merge_requests.call_count == 1

    def test_called_once_per_sync_with_many_mrs(self, sqlite_mem_conn):
        """10 Sentinel-owned MRs → still exactly 1 list_merge_requests call."""
        # Seed extra executions so each ticket has untagged work.
        for i in range(2, 11):
            _seed_execution(
                sqlite_mem_conn,
                execution_id=f"test-exec-{i}",
                ticket_id=f"TEST-{i}",
                created_at=f"2026-05-{i:02d}T00:00:00Z",
            )
        mrs = [
            _make_mr(
                iid=i,
                ticket_id=f"TEST-{i}",
                updated_at=f"2026-05-{i:02d}T00:00:00Z",
                merge_commit_sha=f"sha{i:013d}",
            )
            for i in range(1, 11)
        ]
        gl = _make_gitlab_mock(merged_mrs=mrs)
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        summary = service.sync(project="acme/backend")

        assert gl.list_merge_requests.call_count == 1
        # Sanity: pipelines still scale per Sentinel-owned MR (existing behavior).
        assert gl.list_pipelines_for_commit.call_count == 10
        assert summary.executions_tagged == 10

    def test_not_called_when_no_sentinel_mrs(self, sqlite_mem_conn):
        """Only non-Sentinel branches → skip the revert-candidate fetch entirely."""
        mr = {
            "iid": 1,
            "source_branch": "hotfix/x",
            "target_branch": "main",
            "updated_at": "2026-05-01T00:00:00Z",
            "merged_at": "2026-05-01T00:00:00Z",
            "merge_commit_sha": "abc",
            "state": "merged",
            "title": "Hotfix",
        }
        gl = _make_gitlab_mock(merged_mrs=[mr])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        service.sync(project="acme/backend")

        assert gl.list_merge_requests.call_count == 0

    def test_not_called_when_no_mrs_at_all(self, sqlite_mem_conn):
        """Empty merged-MR list → skip the revert-candidate fetch."""
        gl = _make_gitlab_mock(merged_mrs=[])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        service.sync(project="acme/backend")

        assert gl.list_merge_requests.call_count == 0

    def test_passes_min_merged_at_as_created_after(self, sqlite_mem_conn):
        """``created_after`` = min(merged_at) across Sentinel-owned MRs."""
        _seed_execution(
            sqlite_mem_conn,
            execution_id="test-exec-2",
            ticket_id="TEST-2",
        )
        _seed_execution(
            sqlite_mem_conn,
            execution_id="test-exec-3",
            ticket_id="TEST-3",
        )
        mrs = [
            _make_mr(iid=1, ticket_id="TEST-1", updated_at="2026-05-03T00:00:00Z"),
            _make_mr(iid=2, ticket_id="TEST-2", updated_at="2026-05-01T00:00:00Z"),
            _make_mr(iid=3, ticket_id="TEST-3", updated_at="2026-05-02T00:00:00Z"),
        ]
        gl = _make_gitlab_mock(merged_mrs=mrs)
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        service.sync(project="acme/backend")

        assert gl.list_merge_requests.call_count == 1
        kwargs = gl.list_merge_requests.call_args.kwargs
        assert kwargs["state"] == "merged"
        assert kwargs["created_after"] == "2026-05-01T00:00:00Z"
        assert kwargs["project_id"] == "acme/backend"

    def test_min_merged_at_falls_back_to_updated_at_when_merged_at_missing(
        self, sqlite_mem_conn
    ):
        """Older MRs without ``merged_at`` fall back to ``updated_at``."""
        mr = _make_mr(iid=1, ticket_id="TEST-1")
        # Drop merged_at.
        del mr["merged_at"]
        mr["updated_at"] = "2026-04-15T00:00:00Z"
        gl = _make_gitlab_mock(merged_mrs=[mr])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        service.sync(project="acme/backend")

        kwargs = gl.list_merge_requests.call_args.kwargs
        assert kwargs["created_after"] == "2026-04-15T00:00:00Z"

    def test_min_merged_at_excludes_non_sentinel_branches(self, sqlite_mem_conn):
        """``created_after`` is computed only over Sentinel-owned MRs."""
        non_sentinel = {
            "iid": 99,
            "source_branch": "hotfix/x",
            "target_branch": "main",
            "updated_at": "2026-04-01T00:00:00Z",
            "merged_at": "2026-04-01T00:00:00Z",
            "merge_commit_sha": "deadbeef",
            "state": "merged",
            "title": "Hotfix",
        }
        sentinel_mr = _make_mr(
            iid=1, ticket_id="TEST-1", updated_at="2026-05-10T00:00:00Z"
        )
        gl = _make_gitlab_mock(merged_mrs=[non_sentinel, sentinel_mr])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        service.sync(project="acme/backend")

        kwargs = gl.list_merge_requests.call_args.kwargs
        # NOT 2026-04-01 (the hotfix's merged_at) — only Sentinel MRs count.
        assert kwargs["created_after"] == "2026-05-10T00:00:00Z"

    def test_revert_classification_unchanged_after_hoist(
        self, sqlite_mem_conn, event_bus
    ):
        """Revert detection still produces ``rolled_back`` with byte-identical
        evidence shape on the canonical fixture."""
        mr = _make_mr(
            iid=10,
            ticket_id="TEST-1",
            title="Fix flaky test",
            updated_at="2026-05-01T00:00:00Z",
        )
        revert = {
            "iid": 200,
            "state": "merged",
            "title": 'Revert "Fix flaky test"',
        }
        gl = _make_gitlab_mock(merged_mrs=[mr], merge_requests=[revert])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=event_bus)

        summary = service.sync(project="acme/backend")

        assert summary.tag_counts == {"rolled_back": 1}
        # Evidence dict is byte-identical to the pre-refactor classifier output.
        import json as _json

        row = sqlite_mem_conn.execute(
            "SELECT outcome, outcome_evidence_json FROM executions "
            "WHERE id='test-exec-1'"
        ).fetchone()
        assert row["outcome"] == "rolled_back"
        evidence = _json.loads(row["outcome_evidence_json"])
        assert evidence["outcome"] == "rolled_back"
        assert evidence["mr_iid"] == 10
        assert evidence["revert_mr_iid"] == 200
        assert evidence["merge_commit_sha"] == "deadbeefcafe1234"

    def test_revert_lookup_failure_records_one_error_for_many_mrs(
        self, sqlite_mem_conn
    ):
        """list_merge_requests raises → 1 error recorded (not M), all MRs success."""
        for i in range(2, 6):
            _seed_execution(
                sqlite_mem_conn,
                execution_id=f"test-exec-{i}",
                ticket_id=f"TEST-{i}",
                created_at=f"2026-05-{i:02d}T00:00:00Z",
            )
        mrs = [
            _make_mr(
                iid=i,
                ticket_id=f"TEST-{i}",
                updated_at=f"2026-05-{i:02d}T00:00:00Z",
                merge_commit_sha=f"sha{i:013d}",
            )
            for i in range(1, 6)
        ]
        gl = _make_gitlab_mock(merged_mrs=mrs)
        gl.list_merge_requests.side_effect = requests.HTTPError("500")
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        summary = service.sync(project="acme/backend")

        # All five MRs classified success (no revert match because empty list).
        assert summary.executions_tagged == 5
        assert summary.tag_counts == {"success": 5}
        # One revert-lookup error (not five).
        revert_errors = [e for e in summary.errors if "revert lookup" in e]
        assert len(revert_errors) == 1


# ---------------------------------------------------------------------------
# 3. sync_state helpers
# ---------------------------------------------------------------------------


class TestSyncStateHelpers:
    """update_execution_outcome + upsert_sync_state behaviors."""

    def test_update_execution_outcome_is_append_once(self, sqlite_mem_conn):
        """First call returns 1; second call on the same row returns 0."""
        recorded_at = datetime.now(timezone.utc).isoformat()

        first = update_execution_outcome(
            sqlite_mem_conn,
            execution_id="test-exec-1",
            outcome="success",
            evidence_json='{"k": 1}',
            recorded_at=recorded_at,
        )
        assert first == 1

        second = update_execution_outcome(
            sqlite_mem_conn,
            execution_id="test-exec-1",
            outcome="regressed",  # would-be overwrite
            evidence_json='{"k": 2}',
            recorded_at=recorded_at,
        )
        assert second == 0

        # Outcome unchanged.
        row = sqlite_mem_conn.execute(
            "SELECT outcome, outcome_evidence_json FROM executions "
            "WHERE id='test-exec-1'"
        ).fetchone()
        assert row["outcome"] == "success"
        assert row["outcome_evidence_json"] == '{"k": 1}'

    def test_update_execution_outcome_rejects_invalid_outcome(self, sqlite_mem_conn):
        """ValueError on unknown outcome label."""
        with pytest.raises(ValueError, match="outcome must be one of"):
            update_execution_outcome(
                sqlite_mem_conn,
                execution_id="test-exec-1",
                outcome="garbage",
                evidence_json="{}",
                recorded_at=datetime.now(timezone.utc).isoformat(),
            )

    def test_upsert_sync_state_replaces_on_conflict(self, sqlite_mem_conn):
        """Two upserts with same project key → one row, latest values."""
        upsert_sync_state(
            sqlite_mem_conn,
            project="acme/backend",
            last_synced_at="2026-05-01T00:00:00Z",
            last_seen_mr_iid=1,
            last_seen_updated_at="2026-04-30T00:00:00Z",
        )
        upsert_sync_state(
            sqlite_mem_conn,
            project="acme/backend",
            last_synced_at="2026-05-02T00:00:00Z",
            last_seen_mr_iid=2,
            last_seen_updated_at="2026-05-01T00:00:00Z",
        )

        count = sqlite_mem_conn.execute(
            "SELECT COUNT(*) FROM project_sync_state WHERE project='acme/backend'"
        ).fetchone()[0]
        assert count == 1
        row = read_sync_state(sqlite_mem_conn, "acme/backend")
        assert row is not None
        assert row["last_synced_at"] == "2026-05-02T00:00:00Z"
        assert row["last_seen_mr_iid"] == 2
        assert row["last_seen_updated_at"] == "2026-05-01T00:00:00Z"


# ---------------------------------------------------------------------------
# 4. M5 — sync(deadline=...) cooperative cancellation
# ---------------------------------------------------------------------------


class TestSyncDeadline:
    """M5: cooperative deadline plumbed into ``OutcomeSyncService.sync()``."""

    def test_deadline_in_past_short_circuits_before_first_mr(
        self, sqlite_mem_conn, caplog
    ):
        """deadline in the past => log + return without iterating MRs."""
        import time

        gl = _make_gitlab_mock(merged_mrs=[_make_mr(iid=1)])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)
        past = time.monotonic() - 1.0

        with caplog.at_level(logging.WARNING):
            summary = service.sync(project="acme/backend", deadline=past)

        # Loop entered but immediate break => mrs_seen stays at 0.
        assert summary.mrs_seen == 0
        # Watermark must NOT advance.
        assert summary.watermark_advanced_to is None
        # Loud WARNING from the deadline-check (either the revert-fetch
        # checkpoint for sentinel MRs or the per-MR loop for non-sentinel).
        assert any(
            "deadline reached" in rec.message for rec in caplog.records
        ), f"expected deadline WARNING; got {[r.message for r in caplog.records]}"

    def test_deadline_none_is_unbounded(self, sqlite_mem_conn):
        """deadline=None preserves pre-M5 behavior (default arg)."""
        gl = _make_gitlab_mock(merged_mrs=[_make_mr(iid=1)])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)
        # No deadline kwarg passed.
        summary = service.sync(project="acme/backend")
        assert summary.mrs_seen == 1

    def test_deadline_mid_loop_advances_only_past_handled_mrs(
        self, sqlite_mem_conn
    ):
        """Idempotency: watermark only advances past MRs processed before the cap.

        Two MRs returned by GitLab; the first sync runs with a deadline-in-
        the-past so zero MRs are handled and the watermark is not advanced.
        A second UNBOUNDED sync confirms both MRs are then processed and
        the watermark reflects the latest one.
        """
        import time

        mr1 = _make_mr(
            iid=1, ticket_id="TEST-1", updated_at="2026-05-01T00:00:00Z"
        )
        mr2 = _make_mr(
            iid=2, ticket_id="TEST-2", updated_at="2026-05-02T00:00:00Z"
        )
        # Seed an execution row for each ticket so the per-MR processing
        # has work to do (advances the watermark only past handled MRs).
        _seed_execution(
            sqlite_mem_conn,
            execution_id="e1",
            ticket_id="TEST-1",
            created_at="2026-04-01T00:00:00Z",
        )
        _seed_execution(
            sqlite_mem_conn,
            execution_id="e2",
            ticket_id="TEST-2",
            created_at="2026-04-02T00:00:00Z",
        )
        gl = _make_gitlab_mock(merged_mrs=[mr1, mr2])
        service = OutcomeSyncService(sqlite_mem_conn, gl, event_bus=None)

        # First call: deadline-in-past => 0 MRs handled, watermark stays None.
        summary1 = service.sync(
            project="acme/backend", deadline=time.monotonic() - 1.0
        )
        assert summary1.mrs_seen == 0
        assert summary1.watermark_advanced_to is None

        # Second call: unbounded => both MRs handled, watermark = max
        # updated_at across handled MRs.
        summary2 = service.sync(project="acme/backend")
        assert summary2.mrs_seen == 2
        assert summary2.watermark_advanced_to == "2026-05-02T00:00:00Z"
