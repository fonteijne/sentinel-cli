"""Phase 3A outcome ingestion service.

Pulls merge / revert / post-merge-CI facts from GitLab and tags prior
``executions`` rows with one of {success, rolled_back, regressed}.
Feature-gated (``OUTCOME_SYNC_ENABLED``) at the CLI; the service itself runs
unconditionally so it is testable in isolation.

Matching key: ``mr['source_branch']`` matches
``^sentinel/feature/(?P<ticket_id>.+)$`` -> SELECT FROM executions WHERE
``ticket_id = ?`` AND ``outcome IS NULL`` -> tag every match.

Severity order (most severe wins): ``regressed`` > ``rolled_back`` > ``success``.

Append-once semantics live in ``update_execution_outcome``: a second call on
the same execution returns 0 rows; the service uses that to skip
``OutcomeRecorded`` publication on already-tagged rows.

NO imports from ``src.agents.*`` -- learning is a foundation layer; agents
depend on it, not vice versa (mirrors the rule asserted in
``src/core/events/types.py:18-19``).

NOT in scope (Phase 3A "NOT Building"): no reranker math, no
``recompute_confidence_for_rule``, no ``OutcomeRecorded`` subscriber, no
skill promotion, no webhook listener, no ``python-gitlab``.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.core.events import EventBus, OutcomeRecorded
from src.core.persistence import (
    list_executions_for_ticket_untagged,
    read_sync_state,
    update_execution_outcome,
    upsert_sync_state,
)
from src.gitlab_client import GitLabClient

logger = logging.getLogger(__name__)

_BRANCH_RE = re.compile(r"^sentinel/feature/(?P<ticket_id>.+)$")
"""Branch-name -> ticket_id matcher. Anchored both ends; ticket_id is the
remainder verbatim (no further normalization -- ``executions.ticket_id`` is
populated from this same convention by ``worktree_manager.get_branch_name``)."""

_DEFAULT_LOOKBACK = "1970-01-01T00:00:00+00:00"
"""Used as ``updated_after`` when ``full_backfill=True`` and no watermark exists,
or when neither an explicit ``since`` nor a stored watermark is available.
Effectively means "from the beginning of GitLab time" -- safe because
``update_execution_outcome`` is append-once (no duplicate tags) and
``list_merged_mrs_since`` paginates lazily."""

_TERMINAL_PIPELINE_STATUSES = frozenset({"failed", "canceled"})
"""Pipeline statuses that count as a post-merge regression. ``running`` and
``pending`` (and other non-terminal states like ``created``, ``manual``,
``scheduled``, ``waiting_for_resource``, ``preparing``) are skipped: a still-
running pipeline is not yet ground truth -- defer to next sync."""

_NON_TERMINAL_PIPELINE_STATUSES = frozenset(
    {"running", "pending", "created", "manual", "scheduled",
     "waiting_for_resource", "preparing"}
)


@dataclass
class OutcomeSyncSummary:
    """Per-project summary of one ``OutcomeSyncService.sync`` call.

    ``mrs_seen`` counts every MR returned by GitLab regardless of whether it
    matched the ``sentinel/feature/...`` prefix or had any executions to tag --
    it is the "API-call work" counter, not the "tags written" counter.

    ``executions_tagged`` is the number of UPDATE statements that returned 1
    row (the actual append-once count). On ``dry_run`` it is the *would-be*
    count: how many tags the service would have written had the flag been off.

    ``tag_counts`` is keyed by outcome label; missing labels mean zero.

    ``watermark_advanced_to`` is the ``updated_at`` ISO-8601 string the next
    sync will pass as ``updated_after``. None means the watermark did not
    advance (no MRs handled successfully, or ``dry_run``).

    ``errors`` is a free-form list of strings -- one per per-MR exception or
    per-pipeline-lookup failure. Non-fatal: the sync continues past each.
    """

    project: str
    mrs_seen: int = 0
    executions_tagged: int = 0
    tag_counts: Dict[str, int] = field(default_factory=dict)
    watermark_advanced_to: Optional[str] = None
    errors: List[str] = field(default_factory=list)
    dry_run: bool = False


def classify_outcome(
    mr: Dict[str, Any],
    pipelines: List[Dict[str, Any]],
    revert_mr: Optional[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    """Return ``(outcome_label, evidence_dict)`` for one merged MR.

    Pure function: no DB, no GitLab calls, no logging side effects.

    Severity order (most severe wins):
        1. ``regressed`` -- any *terminal* (non-running, non-pending) pipeline
           on the target branch with status in ``{failed, canceled}``. The
           **most recent** such pipeline (sorted by ``id`` descending --
           pipeline IDs are GitLab-monotonic) is the one whose status decides.
        2. ``rolled_back`` -- ``revert_mr`` is not None AND its ``state`` is
           exactly ``"merged"``. (An open or closed-but-not-merged revert is
           not yet ground truth.)
        3. ``success`` -- everything else, including the case where the only
           pipelines on ``main`` are still running.

    The evidence dict carries enough to reconstruct the decision later
    (audited via ``executions.outcome_evidence_json``). Keys vary by outcome
    so callers should treat it as opaque JSON.
    """
    mr_iid = mr.get("iid")
    merge_commit_sha = mr.get("merge_commit_sha")

    # Filter to terminal pipelines (we ignore running/pending -- they'll be
    # ground truth on a future sync).
    terminal_pipelines = [
        p for p in pipelines
        if p.get("status") not in _NON_TERMINAL_PIPELINE_STATUSES
    ]

    # 1. regressed: most-recent terminal pipeline failed/canceled.
    if terminal_pipelines:
        # Sort by id descending; fall back to 0 for missing id so the sort is
        # stable rather than blowing up.
        most_recent = sorted(
            terminal_pipelines,
            key=lambda p: p.get("id", 0),
            reverse=True,
        )[0]
        if most_recent.get("status") in _TERMINAL_PIPELINE_STATUSES:
            evidence = {
                "outcome": "regressed",
                "mr_iid": mr_iid,
                "merge_commit_sha": merge_commit_sha,
                "pipeline_id": most_recent.get("id"),
                "pipeline_status": most_recent.get("status"),
                "reason": (
                    f"post-merge pipeline {most_recent.get('id')} on "
                    f"{mr.get('target_branch', 'main')} "
                    f"status={most_recent.get('status')}"
                ),
            }
            return "regressed", evidence

    # 2. rolled_back: revert_mr exists and is itself merged.
    if revert_mr is not None and revert_mr.get("state") == "merged":
        evidence = {
            "outcome": "rolled_back",
            "mr_iid": mr_iid,
            "merge_commit_sha": merge_commit_sha,
            "revert_mr_iid": revert_mr.get("iid"),
            "reason": (
                f"revert MR !{revert_mr.get('iid')} "
                f"({revert_mr.get('title', '')[:80]}) merged"
            ),
        }
        return "rolled_back", evidence

    # 3. success: clean merge.
    evidence = {
        "outcome": "success",
        "mr_iid": mr_iid,
        "merge_commit_sha": merge_commit_sha,
        "reason": "merged with no terminal-failed pipeline and no merged revert",
    }
    return "success", evidence


class OutcomeSyncService:
    """Pull-on-demand outcome ingestion. One instance per ``(conn, gitlab)`` pair.

    The service is intentionally stateless beyond its constructor args: each
    ``sync()`` call reads the watermark, walks GitLab, writes outcomes, and
    advances the watermark. Re-running is safe because:

      - ``update_execution_outcome`` is append-once (WHERE outcome IS NULL).
      - The watermark only advances to the max ``updated_at`` of MRs that
        were *successfully handled* in this run, so a transient HTTP failure
        on MR N does not skip MR N+1 on the next run.

    Constructor args:
        conn: open SQLite connection, migrations applied.
        gitlab: ``GitLabClient`` instance (real or mocked).
        event_bus: optional ``EventBus``. When provided, an
            ``OutcomeRecorded`` event is published per tagged execution.
            ``None`` is valid (e.g. dry-run paths or tests that don't care
            about events) and silently skips publication.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        gitlab: GitLabClient,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._conn = conn
        self._gitlab = gitlab
        self._event_bus = event_bus

    # ---- public surface --------------------------------------------------

    def sync(
        self,
        *,
        project: str,
        since: Optional[str] = None,
        full_backfill: bool = False,
        dry_run: bool = False,
    ) -> OutcomeSyncSummary:
        """Sync outcomes for ``project``.

        Args:
            project: GitLab project path (e.g. ``"acme/backend"``).
            since: explicit ``updated_after`` ISO-8601 string. Overrides the
                stored watermark. Useful for ad-hoc backfill of a known range.
            full_backfill: ignore the stored watermark AND ``since``-as-default;
                start from ``_DEFAULT_LOOKBACK``. ``since`` still wins if both
                are provided.
            dry_run: do everything except UPDATE the DB and publish events.
                Counters still increment so the CLI can preview impact.

        Returns:
            ``OutcomeSyncSummary`` with per-project counts. Never raises on
            per-MR errors; errors are appended to ``summary.errors``.
        """
        summary = OutcomeSyncSummary(project=project, dry_run=dry_run)

        # --- determine updated_after ---
        updated_after = self._resolve_updated_after(
            project=project, since=since, full_backfill=full_backfill
        )

        # --- fetch merged MRs ---
        try:
            mrs = self._gitlab.list_merged_mrs_since(
                project, updated_after=updated_after
            )
        except Exception as exc:
            # Top-level fetch failure -- nothing to iterate. Record and bail
            # without advancing watermark.
            msg = f"list_merged_mrs_since failed for {project}: {exc}"
            logger.warning(msg)
            summary.errors.append(msg)
            return summary

        # --- per-MR processing ---
        # Track the max updated_at and max iid among MRs whose tagging
        # *succeeded* (or had no untagged executions to tag, which is a
        # successful no-op). A per-MR exception leaves both untouched, so
        # the watermark cannot skip past a failure.
        max_updated_at_handled: Optional[str] = None
        max_iid_handled: Optional[int] = None

        for mr in mrs:
            summary.mrs_seen += 1
            try:
                handled, mr_updated_at, mr_iid = self._process_mr(
                    project=project,
                    mr=mr,
                    summary=summary,
                    dry_run=dry_run,
                )
            except Exception as exc:
                msg = (
                    f"per-MR error project={project} "
                    f"mr_iid={mr.get('iid')}: {exc}"
                )
                logger.warning(msg, exc_info=True)
                summary.errors.append(msg)
                continue

            if handled:
                if mr_updated_at is not None and (
                    max_updated_at_handled is None
                    or mr_updated_at > max_updated_at_handled
                ):
                    max_updated_at_handled = mr_updated_at
                if mr_iid is not None and (
                    max_iid_handled is None or mr_iid > max_iid_handled
                ):
                    max_iid_handled = mr_iid

        # --- advance watermark ---
        if not dry_run and max_updated_at_handled is not None:
            prior = read_sync_state(self._conn, project)
            prior_iid: Optional[int] = (
                prior["last_seen_mr_iid"] if prior is not None else None
            )
            prior_updated: Optional[str] = (
                prior["last_seen_updated_at"] if prior is not None else None
            )

            # Watermark only moves forward.
            new_updated = max_updated_at_handled
            if prior_updated is not None and prior_updated > new_updated:
                new_updated = prior_updated

            new_iid: Optional[int]
            if max_iid_handled is None:
                new_iid = prior_iid
            elif prior_iid is None:
                new_iid = max_iid_handled
            else:
                new_iid = max(prior_iid, max_iid_handled)

            now_iso = datetime.now(timezone.utc).isoformat()
            upsert_sync_state(
                self._conn,
                project=project,
                last_synced_at=now_iso,
                last_seen_mr_iid=new_iid,
                last_seen_updated_at=new_updated,
            )
            summary.watermark_advanced_to = new_updated

        return summary

    # ---- internals -------------------------------------------------------

    def _resolve_updated_after(
        self,
        *,
        project: str,
        since: Optional[str],
        full_backfill: bool,
    ) -> str:
        """Resolve the ``updated_after`` value passed to GitLab.

        Precedence (highest first):
            1. explicit ``since`` argument
            2. stored watermark ``last_seen_updated_at`` (unless ``full_backfill``)
            3. ``_DEFAULT_LOOKBACK`` (epoch)

        ``full_backfill`` only suppresses #2; an explicit ``since`` still wins
        because the operator's intent is more specific than the flag.
        """
        if since is not None:
            return since
        if not full_backfill:
            row = read_sync_state(self._conn, project)
            if row is not None and row["last_seen_updated_at"]:
                last_seen: str = row["last_seen_updated_at"]
                return last_seen
        return _DEFAULT_LOOKBACK

    def _process_mr(
        self,
        *,
        project: str,
        mr: Dict[str, Any],
        summary: OutcomeSyncSummary,
        dry_run: bool,
    ) -> Tuple[bool, Optional[str], Optional[int]]:
        """Handle one MR. Returns ``(handled, updated_at, iid)``.

        ``handled=True`` means "this MR's processing finished cleanly enough
        that the watermark may advance past it" -- including the
        no-untagged-executions case (it's still an idempotent no-op).
        ``handled=False`` means "skip past for watermark purposes" (e.g. the
        branch did not match the ``sentinel/feature/...`` prefix).

        Caller wraps this in try/except; per-MR exceptions are recorded in
        ``summary.errors`` and treated as ``handled=False``.
        """
        source_branch = mr.get("source_branch", "")
        match = _BRANCH_RE.match(source_branch)
        if match is None:
            # Not a Sentinel-owned branch; do not advance watermark on these
            # either -- branch ownership is independent of watermark progress
            # for the purpose of resuming after a transient error mid-page,
            # but skipping them from advancement is harmless because they
            # cannot ever be tagged. (We choose: do count toward watermark
            # so non-Sentinel-heavy projects don't re-paginate every run.)
            return True, mr.get("updated_at"), mr.get("iid")

        ticket_id = match.group("ticket_id")
        rows = list_executions_for_ticket_untagged(self._conn, ticket_id)
        if not rows:
            logger.info(
                "no untagged executions for ticket_id=%s (mr_iid=%s)",
                ticket_id,
                mr.get("iid"),
            )
            # Still considered handled: nothing to do, but the next sync
            # should not re-fetch this MR.
            return True, mr.get("updated_at"), mr.get("iid")

        # --- pipelines: best-effort, never abort on lookup failure ---
        pipelines: List[Dict[str, Any]] = []
        merge_commit_sha = mr.get("merge_commit_sha")
        target_branch = mr.get("target_branch", "main")
        if merge_commit_sha:
            try:
                pipelines = self._gitlab.list_pipelines_for_commit(
                    project,
                    sha=merge_commit_sha,
                    ref=target_branch,
                )
            except Exception as exc:
                msg = (
                    f"list_pipelines_for_commit failed project={project} "
                    f"mr_iid={mr.get('iid')} sha={merge_commit_sha}: {exc}"
                )
                logger.warning(msg)
                summary.errors.append(msg)
                pipelines = []

        # --- revert detection: best-effort, never abort on lookup failure ---
        revert_mr = self._find_revert_mr(project=project, mr=mr, summary=summary)

        # --- classify ---
        outcome, evidence = classify_outcome(mr, pipelines, revert_mr)

        # --- tag every untagged execution ---
        evidence_json = json.dumps(evidence, sort_keys=True)
        recorded_at = datetime.now(timezone.utc).isoformat()
        evidence_summary = str(evidence.get("reason", outcome))[:200]

        for row in rows:
            execution_id = row["id"]
            if dry_run:
                # Preview: increment counters as if the UPDATE returned 1.
                summary.executions_tagged += 1
                summary.tag_counts[outcome] = summary.tag_counts.get(outcome, 0) + 1
                continue

            tagged = update_execution_outcome(
                self._conn,
                execution_id=execution_id,
                outcome=outcome,
                evidence_json=evidence_json,
                recorded_at=recorded_at,
            )
            if tagged != 1:
                # Already tagged (append-once) -- do not double-publish.
                continue

            summary.executions_tagged += 1
            summary.tag_counts[outcome] = summary.tag_counts.get(outcome, 0) + 1

            if self._event_bus is not None:
                event = OutcomeRecorded(
                    execution_id=execution_id,
                    type="OutcomeRecorded",
                    mr_iid=int(mr.get("iid", 0)),
                    project=project,
                    outcome=outcome,  # type: ignore[arg-type]
                    merged_at=mr.get("merged_at"),
                    reverted_by_mr_iid=(
                        revert_mr.get("iid") if revert_mr is not None else None
                    ),
                    regressed_pipeline_id=(
                        evidence.get("pipeline_id")
                        if outcome == "regressed"
                        else None
                    ),
                    evidence_summary=evidence_summary,
                )
                self._event_bus.publish(event)

        return True, mr.get("updated_at"), mr.get("iid")

    def _find_revert_mr(
        self,
        *,
        project: str,
        mr: Dict[str, Any],
        summary: OutcomeSyncSummary,
    ) -> Optional[Dict[str, Any]]:
        """Best-effort revert detection.

        Heuristic (intentionally simple per Phase 3A "NOT Building"):
        list the project's merged MRs and pick one whose title starts with
        ``Revert "`` AND references either the original MR title or the
        merge_commit_sha[:8].

        A failure here is non-fatal: log + record + return None so the MR
        gets classified as ``success``-or-better. Reverted-but-undetected is
        a known limitation; richer detection is future work (PRD §"Auto-revert
        detection beyond title prefix...").
        """
        try:
            candidates = self._gitlab.list_merge_requests(
                project_id=project, state="merged"
            )
        except Exception as exc:
            msg = (
                f"revert lookup (list_merge_requests) failed project={project} "
                f"mr_iid={mr.get('iid')}: {exc}"
            )
            logger.warning(msg)
            summary.errors.append(msg)
            return None

        original_title = mr.get("title", "") or ""
        merge_commit_sha = mr.get("merge_commit_sha", "") or ""
        sha_prefix = merge_commit_sha[:8] if merge_commit_sha else ""

        for cand in candidates:
            cand_title = cand.get("title", "") or ""
            if not cand_title.startswith('Revert "'):
                continue
            # Title-of-original is the GitLab default; the SHA prefix is the
            # git default. Match either.
            if original_title and original_title in cand_title:
                return cand
            if sha_prefix and sha_prefix in cand_title:
                return cand

        return None
