"""Post-execute side effects shared by CLI and Command Center worker.

The execute and revise workflows finish with a tail of side effects that
must be identical regardless of how the run was triggered:

* push the worktree branch to ``origin``
* mark the existing GitLab merge request as ready (remove draft status)
* post a decision-log MR comment
* (optionally) post the unresolved Drupal findings comment
* notify Jira that execution is complete with a link to the MR

This module owns the canonical implementation. Both ``src.cli.execute`` and
``src.core.execution.workflows.run_execute`` route through here so the two
paths stay in lock-step.

Design notes:

* All steps are wrapped with try/except so a failure in one (e.g. Jira
  unreachable) does not roll back the work that already succeeded
  (e.g. the git push). Each step returns its own success-or-failure
  marker; the caller adds those markers to its :class:`WorkflowResult`.
* The functions take pre-built clients/managers so tests can substitute
  fakes without monkey-patching imports.
* No ``click.echo`` here — presentation is the CLI's responsibility,
  observed via the event bus / returned markers.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Result objects
# --------------------------------------------------------------------------- #


@dataclass
class PostExecuteOutcome:
    """What the side-effect tail produced.

    ``artifacts`` mirror the ones used by ``WorkflowResult`` so callers can
    keep their no-op detection working without duplicating constants.
    """

    artifacts: List[str] = field(default_factory=list)
    pushed: bool = False
    push_branch: Optional[str] = None
    push_error: Optional[str] = None
    mr_iid: Optional[int] = None
    mr_web_url: Optional[str] = None
    mr_marked_ready: bool = False
    decision_log_posted: bool = False
    drupal_findings_posted: bool = False
    jira_notified: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def add(self, marker: str) -> None:
        if marker and marker not in self.artifacts:
            self.artifacts.append(marker)


# --------------------------------------------------------------------------- #
# Step 1 — git push
# --------------------------------------------------------------------------- #


def push_branch(worktree_path: Path, *, force: bool) -> Dict[str, Any]:
    """Push the worktree's current branch to ``origin``.

    Returns a dict with keys ``pushed`` (bool), ``branch`` (str), and on
    failure ``error`` (str) and ``rejected_diverged`` (bool). The caller
    decides whether to surface the error as fatal.
    """
    try:
        branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        return {
            "pushed": False,
            "branch": None,
            "error": f"failed to resolve branch: {exc.stderr or exc}",
            "rejected_diverged": False,
        }

    branch_name = branch_result.stdout.strip()
    push_cmd = ["git", "push", "-u", "origin", branch_name]
    if force:
        push_cmd.insert(2, "--force")

    push_result = subprocess.run(
        push_cmd,
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )

    if push_result.returncode == 0:
        return {"pushed": True, "branch": branch_name, "error": None,
                "rejected_diverged": False}

    err = push_result.stderr or ""
    diverged = "non-fast-forward" in err or "rejected" in err
    return {
        "pushed": False,
        "branch": branch_name,
        "error": err.strip(),
        "rejected_diverged": diverged,
    }


# --------------------------------------------------------------------------- #
# Step 2 — locate MR for branch
# --------------------------------------------------------------------------- #


def locate_merge_request(
    *,
    gitlab_client: Any,
    project_path: str,
    source_branch: str,
) -> Optional[Dict[str, Any]]:
    """Return the first MR for ``source_branch`` or ``None``."""
    mrs = gitlab_client.list_merge_requests(
        project_id=project_path,
        source_branch=source_branch,
    )
    if not mrs:
        return None
    return mrs[0]


# --------------------------------------------------------------------------- #
# Step 3 — decision log / drupal-findings comment formatters
# --------------------------------------------------------------------------- #


def format_decision_log(
    *, ticket_id: str, iteration: int, dev_result: Dict[str, Any],
    sec_result: Dict[str, Any]
) -> str:
    """Markdown decision log for an execute MR comment."""
    lines = [
        "## Sentinel Execution Summary",
        "",
        f"**Ticket:** `{ticket_id}`  ",
        f"**Iterations:** {iteration}  ",
        "**Status:** Approved",
        "",
        "### Implementation",
        f"- **Tasks completed:** {dev_result.get('tasks_completed', 0)}",
        f"- **Tasks failed:** {dev_result.get('tasks_failed', 0)}",
    ]

    test_results = dev_result.get("test_results")
    if test_results:
        if test_results.get("success"):
            lines.append("- **Tests:** passing")
        else:
            lines.append(
                f"- **Tests:** failing (exit code {test_results.get('return_code', '?')})"
            )

    lines.extend(["", "### Security Review"])
    findings = sec_result.get("findings") or []
    if findings:
        lines.append(f"- **Findings addressed:** {len(findings)}")
        for f in findings[:5]:
            lines.append(
                f"  - `{f.get('severity', '?')}` {f.get('category', '')}: "
                f"{f.get('description', '')[:80]}"
            )
        if len(findings) > 5:
            lines.append(f"  - ... and {len(findings) - 5} more")
    else:
        lines.append("- No issues found")

    lines.extend([
        "",
        "---",
        f"*Generated by Sentinel at "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
    ])
    return "\n".join(lines)


def format_revision_log(*, ticket_id: str, result: Dict[str, Any]) -> str:
    tasks_completed = result.get("tasks_completed", 0)
    tasks_failed = result.get("tasks_failed", 0)
    feedback_count = result.get("feedback_count", 0)
    questions_answered = result.get("questions_answered", 0)
    questions_failed = result.get("questions_failed", 0)
    acknowledged = result.get("acknowledged", 0)

    lines = [
        "## Sentinel Revision Complete",
        "",
        f"**Ticket:** `{ticket_id}`  ",
        f"**Discussions analyzed:** {feedback_count}  ",
        "",
        "### Tasks",
        f"- **Completed:** {tasks_completed}",
        f"- **Failed:** {tasks_failed}",
    ]
    if questions_answered or questions_failed:
        lines.extend([
            "",
            "### Questions",
            f"- **Answered:** {questions_answered}",
            f"- **Failed:** {questions_failed}",
        ])
    if acknowledged:
        lines.append(f"- **Acknowledged:** {acknowledged}")

    test_results = result.get("test_results", {})
    if test_results:
        if test_results.get("success"):
            lines.append("- **Tests:** passing")
        else:
            lines.append(
                f"- **Tests:** failing (exit code {test_results.get('return_code', '?')})"
            )
    config_validation = result.get("config_validation", {})
    if config_validation:
        if config_validation.get("success"):
            lines.append("- **Config validation:** passing")
        elif not config_validation.get("success", True):
            lines.append("- **Config validation:** failing")

    lines.extend([
        "",
        "---",
        f"*Generated by Sentinel at "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
    ])
    return "\n".join(lines)


def format_drupal_findings_comment(
    *, ticket_id: str, drupal_result: Dict[str, Any], attempts: int
) -> str:
    review_data = drupal_result.get("review_data", {})
    verdict = review_data.get("verdict", "REQUEST_CHANGES")
    findings = drupal_result.get("findings") or []

    lines = [
        "## ⚠️ Drupal Review — Unresolved Findings",
        "",
        f"**Ticket:** `{ticket_id}`  ",
        f"**Attempts:** {attempts}  ",
        f"**Verdict:** {verdict}",
        "",
        "### Findings requiring human review",
    ]
    severity_order = ["BLOCKER", "MAJOR", "MINOR", "NIT", "QUESTION"]
    for severity in severity_order:
        group = [f for f in findings if f.get("severity") == severity]
        if not group:
            continue
        lines.append(f"\n#### {severity} ({len(group)})")
        for finding in group:
            loc = finding.get("file", "unknown")
            if finding.get("line"):
                loc += f":{finding['line']}"
            fid = finding.get("id", "?")
            title = finding.get("title", "")
            lines.append(f"- **[{fid}]** {title} (`{loc}`)")
    lines.extend([
        "",
        "---",
        "*Unresolved findings from automated review. Please validate manually.*  ",
        f"*Generated by Sentinel at "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*",
    ])
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Compound flow used by run_execute / run_revise
# --------------------------------------------------------------------------- #


def run_post_execute_side_effects(
    *,
    ticket_id: str,
    project: str,
    worktree_path: Path,
    iteration: int,
    dev_result: Dict[str, Any],
    sec_result: Dict[str, Any],
    drupal_findings: Optional[Dict[str, Any]],
    drupal_attempts: Optional[int],
    force_push: bool,
    revision_result: Optional[Dict[str, Any]] = None,
    gitlab_factory: Optional[Any] = None,
    jira_factory: Optional[Any] = None,
    config_factory: Optional[Any] = None,
    branch_name_factory: Optional[Any] = None,
) -> PostExecuteOutcome:
    """Run push → MR-ready → decision log → drupal-findings → Jira notify.

    Each sub-step is best-effort: a failure logs and is recorded on the
    outcome, but does not prevent the next step from running. Push failure
    is the exception — if push fails we skip the MR/Jira steps because they
    only make sense when the new commits actually reached origin.

    ``revision_result`` is for the revise flow: when present we post the
    revision log instead of the execute decision log.
    """
    from src.config_loader import get_config
    from src.gitlab_client import GitLabClient
    from src.jira_factory import get_jira_client
    from src.worktree_manager import get_branch_name

    outcome = PostExecuteOutcome()

    # 1. push
    push_info = push_branch(worktree_path, force=force_push)
    outcome.push_branch = push_info["branch"]
    if push_info["pushed"]:
        outcome.pushed = True
        outcome.add("git.pushed")
    else:
        outcome.push_error = push_info["error"]
        outcome.extra["push_rejected_diverged"] = push_info["rejected_diverged"]
        outcome.add("git.push_failed")
        # Without a successful push the MR/Jira steps are meaningless.
        return outcome

    # 2. MR locate + mark ready + decision log + drupal findings comment
    try:
        gitlab = (gitlab_factory or GitLabClient)()
        config = (config_factory or get_config)()
        project_config = config.get_project_config(project)
        git_url = project_config.get("git_url", "")
        project_path = GitLabClient.extract_project_path(git_url)
        branch_resolver = branch_name_factory or get_branch_name
        source_branch = branch_resolver(ticket_id)
        mr = locate_merge_request(
            gitlab_client=gitlab, project_path=project_path,
            source_branch=source_branch,
        )
    except Exception as exc:
        logger.warning("post_execute: GitLab lookup failed: %s", exc)
        mr = None

    if mr:
        outcome.mr_iid = mr.get("iid")
        outcome.mr_web_url = mr.get("web_url")
        outcome.add("gitlab.mr_located")

        # Mark MR as ready (only meaningful for the execute flow, not revise)
        if revision_result is None:
            try:
                gitlab.mark_as_ready(
                    project_id=project_path, mr_iid=outcome.mr_iid
                )
                outcome.mr_marked_ready = True
                outcome.add("gitlab.mr_ready")
            except Exception as exc:
                logger.warning(
                    "post_execute: mark_as_ready failed: %s", exc
                )

        # Decision/revision log comment
        try:
            if revision_result is not None:
                body = format_revision_log(
                    ticket_id=ticket_id, result=revision_result
                )
            else:
                body = format_decision_log(
                    ticket_id=ticket_id, iteration=iteration,
                    dev_result=dev_result, sec_result=sec_result,
                )
            gitlab.add_merge_request_comment(
                project_id=project_path,
                mr_iid=outcome.mr_iid,
                body=body,
            )
            outcome.decision_log_posted = True
            outcome.add("gitlab.decision_log_posted")
        except Exception as exc:
            logger.warning(
                "post_execute: decision log comment failed: %s", exc
            )

        # Drupal findings (if any)
        if drupal_findings is not None:
            try:
                comment = format_drupal_findings_comment(
                    ticket_id=ticket_id,
                    drupal_result=drupal_findings,
                    attempts=drupal_attempts or 0,
                )
                gitlab.add_merge_request_comment(
                    project_id=project_path,
                    mr_iid=outcome.mr_iid,
                    body=comment,
                )
                outcome.drupal_findings_posted = True
                outcome.add("gitlab.drupal_findings_posted")
            except Exception as exc:
                logger.warning(
                    "post_execute: drupal findings comment failed: %s", exc
                )

    # 3. Jira completion notification — only on the execute flow.
    if revision_result is None:
        try:
            jira = (jira_factory or get_jira_client)()
            comment = (
                f"Sentinel has completed execution for {ticket_id}. "
                "Code is ready for developer review."
            )
            jira.add_comment(
                ticket_id,
                comment,
                link_text="View Merge Request" if outcome.mr_web_url else None,
                link_url=outcome.mr_web_url,
            )
            outcome.jira_notified = True
            outcome.add("jira.completion_comment_posted")
        except Exception as exc:
            logger.warning(
                "post_execute: jira completion comment failed: %s", exc
            )

    return outcome
