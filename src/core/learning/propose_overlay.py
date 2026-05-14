"""Phase 2C overlay-PR proposer — feedback_rules (probation) → draft GitLab MR.

Pure functions over a sqlite3 connection plus a thin GitLab-client surface and
a few subprocess calls against the Sentinel-repo working tree. The CLI command
is the only caller in production; tests pass a tmp git repo and a mock
GitLabClient.

Design invariants (plan task 12 / D4 / D7):

  - Always ``draft=True`` when calling ``GitLabClient.create_merge_request``.
    This is hard-coded at the call site, NOT computed from a flag. Reviewers
    test for it explicitly.
  - Never auto-merge. The proposer never calls
    ``gitlab_client.update_merge_request(state_event="merge")``.
  - ``repo_root`` is caller-supplied (the CLI resolves it). The module does not
    discover it via ``__file__`` heuristics — production trusts the caller.
  - On a successful run, ``mark_proposed`` is called per rule AFTER the MR is
    created. If push or MR-creation fails partway, the un-mark_proposed'd rules
    remain promotable for the next run; we deliberately do NOT swallow the
    exception.
  - Dry-run never publishes ``FeedbackRulePromoted`` and never persists state
    (no ``mark_proposed`` calls). The branch we created is reverted before
    return so a tmp test repo stays clean.

Append-only spirit: this module never DELETEs anything. Persistence-side
mutations are limited to ``mark_proposed`` (sets proposed_at + URL + path; does
NOT change ``status``).
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

from src.core.events.types import FeedbackRulePromoted
from src.core.persistence import mark_proposed, query_promotable

logger = logging.getLogger(__name__)


# Hard cap on the MR description payload (matches event-bus payload cap from
# src/core/events/bus.py). 64 KiB is the absolute ceiling — we truncate
# per-rule context_excerpts long before then so the description stays readable.
_MR_DESCRIPTION_MAX_BYTES = 64 * 1024
_CONTEXT_EXCERPT_MAX_CHARS = 200


class _EventBusLike(Protocol):
    """Minimal protocol the proposer needs from an event bus.

    The real ``src.core.events.bus.EventBus`` satisfies this; tests pass a
    ``_FakeEventBus`` with a ``.publish(event)`` method (or ``None`` to skip
    emission entirely). Stays narrow on purpose to keep import surface small.
    """

    def publish(self, event: object) -> None: ...


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ProposalResult:
    """One per rule processed in a proposer run.

    ``mr_url`` is the GitLab web URL on a real run, ``"(dry-run)"`` on dry-run.
    ``overlay_path`` is the relative path of the overlay file the rule's
    bullet was rendered into (multiple agent_targets can land in one run, so
    different rules in the same result list may have different overlay_paths).
    """

    rule_id: int
    branch_name: str
    mr_url: str
    dry_run: bool
    overlay_path: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _branch_name_for(scope: str) -> str:
    """``sentinel-learning/promote-<scope>-<YYYYMMDD-HHMMSS>`` (UTC).

    UTC is non-negotiable: branch names with local-time suffixes break
    deterministic ordering when the operator's TZ changes. Second precision
    (vs minute) ensures two retries within the same minute produce distinct
    branch names — the failure path at ``propose_overlays`` deliberately
    leaves a branch on disk for operator inspection, so a colliding name
    would block the next attempt.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"sentinel-learning/promote-{scope}-{stamp}"


def _capture_starting_ref(repo_root: Path) -> str:
    """Snapshot the operator's current git ref BEFORE we mutate HEAD.

    Returns either the current branch name (normal checkout) or the current
    HEAD SHA (detached HEAD). Both forms are restorable via ``git checkout
    <ref>``: a branch name re-checks-out the branch; a SHA re-detaches at
    that commit, which is the correct round-trip for an operator who started
    detached.

    Resolution order:
      1. ``git symbolic-ref --short HEAD`` — returns the branch name without
         the ``refs/heads/`` prefix; exits non-zero on detached HEAD.
      2. ``git rev-parse HEAD`` — returns the commit SHA; only fails when the
         repo has an unborn HEAD (no commits at all).

    Raises ``RuntimeError`` if BOTH commands fail. This is the idempotency
    guarantee: if we can't read the starting ref, we refuse to mutate HEAD
    at all (the caller must not call ``git checkout -b`` after this raises).
    """
    sym = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if sym.returncode == 0:
        return sym.stdout.strip()

    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if sha.returncode == 0 and sha.stdout.strip():
        return sha.stdout.strip()

    sym_err = (sym.stderr or "").strip()
    sha_err = (sha.stderr or "").strip()
    raise RuntimeError(
        f"could not capture starting git ref in {repo_root}: "
        f"symbolic-ref: {sym_err!r}; rev-parse: {sha_err!r}"
    )


def _restore_starting_ref(repo_root: Path, ref: str) -> None:
    """Best-effort restore of HEAD to the captured starting ref.

    Runs ``git checkout <ref>`` with ``check=False``. ``ref`` may be a branch
    name (normal re-checkout) or a SHA (re-detach at that commit) — both are
    accepted by ``git checkout`` and produce the correct round-trip.

    A failure here is logged at WARNING level and swallowed: a restore
    failure inside a ``finally`` must NEVER mask the original exception (if
    any) bubbling out of the ``try`` block. The operator gets a warning plus
    the original error.

    Note: we deliberately do NOT use ``git checkout -`` (the "previous
    branch" shortcut). It depends on git's reflog state, which is mutated by
    intervening operations and is therefore not deterministic.
    """
    result = subprocess.run(
        ["git", "checkout", ref],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode() if result.stderr else ""
        logger.warning(
            "propose_overlays: could not restore starting ref %s in %s: %s",
            ref,
            repo_root,
            stderr.strip(),
        )


def _overlay_relpath_for(scope: str, agent_target: str) -> Path:
    """``prompts/overlays/{scope}_{agent_target}.md`` (relative to repo root)."""
    return Path("prompts") / "overlays" / f"{scope}_{agent_target}.md"


def _render_rule_bullet(
    rule_row: sqlite3.Row,
    conn: sqlite3.Connection,
) -> str:
    """Render one Markdown bullet for the overlay file.

    Format (the trailing HTML comment is the provenance trailer the reviewer
    test asserts is present)::

        - **[rule:{id} conf:{conf} obs:{obs} proj:{projects}]** {failure_signature}
          Source: postmortem #{first} (first seen {iso}) → most recent {last}.
          <!-- rule:{id} origin:postmortem-{first} first_seen:{YYYY-MM-DD} -->

    ``first_seen_date`` is the first 10 chars of the first postmortem's
    ``created_at`` (ISO 8601 → ``YYYY-MM-DD``); ``first_seen_iso`` is the full
    timestamp.
    """
    rule_id = rule_row["id"]
    confidence = rule_row["confidence"]
    obs = rule_row["observation_count"]
    projects = rule_row["distinct_projects"]
    signature = rule_row["signature"]
    first_pm_id = rule_row["first_postmortem_id"]
    last_pm_id = rule_row["last_postmortem_id"]

    pm_row = conn.execute(
        "SELECT created_at FROM postmortems WHERE id = ?",
        (first_pm_id,),
    ).fetchone()
    first_seen_iso = pm_row["created_at"] if pm_row is not None else ""
    first_seen_date = first_seen_iso[:10] if first_seen_iso else ""

    return (
        f"- **[rule:{rule_id} conf:{confidence} obs:{obs} proj:{projects}]** "
        f"{signature}\n"
        f"  Source: postmortem #{first_pm_id} (first seen {first_seen_iso}) "
        f"→ most recent {last_pm_id}.\n"
        f"  <!-- rule:{rule_id} origin:postmortem-{first_pm_id} "
        f"first_seen:{first_seen_date} -->\n"
    )


def _apply_overlay_edit(
    repo_root: Path,
    overlay_relpath: Path,
    bullets: list[str],
) -> None:
    """Append bullets to the ``## Auto-promoted pitfalls`` H2 section.

    If the section exists, append at the END of that section (i.e. just before
    the next ``##`` header, or at end-of-file if it's the last section). If the
    section does not exist, create it at the end of the file with one leading
    blank line.

    Deterministic and idempotent in shape: re-running with the same bullet set
    against an unmodified file yields byte-identical output.
    """
    overlay_path = repo_root / overlay_relpath
    original = overlay_path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=False)

    section_header = "## Auto-promoted pitfalls"
    section_start: Optional[int] = None
    for idx, line in enumerate(lines):
        if line.strip() == section_header:
            section_start = idx
            break

    if section_start is not None:
        # Find the next H2 header (or EOF) — that's the section's end-exclusive
        # boundary. Append bullets there.
        section_end = len(lines)
        for idx in range(section_start + 1, len(lines)):
            stripped = lines[idx]
            if stripped.startswith("## ") and stripped.strip() != section_header:
                section_end = idx
                break
        # Trim trailing blank lines inside the section so our append produces a
        # single blank line gap.
        insert_at = section_end
        while insert_at > section_start + 1 and lines[insert_at - 1].strip() == "":
            insert_at -= 1
        new_lines = (
            lines[:insert_at]
            + ["".rstrip()]  # blank line separator before bullets
            + [b.rstrip("\n") for b in bullets]
            + [""]
            + lines[insert_at:]
        )
    else:
        # Create the section at end of file with a leading blank line.
        # Ensure the existing file ends with a newline before our new section.
        new_lines = list(lines)
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append(section_header)
        new_lines.append("")
        for b in bullets:
            new_lines.append(b.rstrip("\n"))
        new_lines.append("")

    new_text = "\n".join(new_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"

    logger.debug(
        "applying overlay edit: file=%s bullets=%d section_existed=%s",
        overlay_relpath,
        len(bullets),
        section_start is not None,
    )
    overlay_path.write_text(new_text, encoding="utf-8")


def push_overlay_branch(
    repo_root: Path,
    branch_name: str,
    paths: list[Path],
    commit_message: str,
) -> None:
    """Stage given overlay paths, assert non-empty staged diff, commit, push.

    Mirrors ``src/agents/plan_generator.py:790-855`` (capture_output=True,
    check=True, decode stderr on failure). The caller must already have
    created and switched to ``branch_name``.

    Raises ``RuntimeError`` if no overlay edits are staged (refusing to push
    an empty branch is intentional — an empty branch suggests an upstream
    bug and would create a noise MR).
    """
    for path in paths:
        subprocess.run(
            ["git", "add", str(path)],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )

    diff_result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_root,
        capture_output=True,
    )
    if diff_result.returncode == 0:
        raise RuntimeError(
            "No staged overlay changes — refusing to push empty branch."
        )

    subprocess.run(
        ["git", "commit", "-m", commit_message],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", branch_name],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )


def _build_mr_description(
    rules: list[sqlite3.Row],
    conn: sqlite3.Connection,
    scope: str,
) -> str:
    """Compose the MR description with per-rule evidence.

    Quotes each rule's metadata (signature, confidence, observation_count,
    distinct_projects) and the first/last postmortem rows' failure_signature,
    truncated context_excerpt, parent execution's ticket_id and created_at.

    Hard-capped at 64 KiB total. Per-rule context_excerpt is truncated to
    ``_CONTEXT_EXCERPT_MAX_CHARS`` aggressively to keep the description
    useful, not novel-length.
    """
    parts: list[str] = []
    parts.append(
        f"**Auto-promoted {scope} pitfalls — {len(rules)} rule"
        f"{'s' if len(rules) != 1 else ''}**\n"
    )
    parts.append(
        "This MR was opened by `sentinel learning propose`. Each bullet below "
        "promotes a postmortem cluster to the durable overlay; the source "
        "postmortem rows are quoted as audit trail.\n\n"
        "**Always opens as `draft=True` (Decision 4 / D7).** Merge is a "
        "human action.\n"
    )

    for rule in rules:
        rule_id = rule["id"]
        signature = rule["signature"]
        confidence = rule["confidence"]
        obs = rule["observation_count"]
        projects = rule["distinct_projects"]
        first_pm_id = rule["first_postmortem_id"]
        last_pm_id = rule["last_postmortem_id"]

        parts.append(
            f"\n---\n\n"
            f"### rule:{rule_id} — {signature}\n\n"
            f"- confidence: **{confidence}**\n"
            f"- observation_count: **{obs}**\n"
            f"- distinct_projects: **{projects}**\n"
            f"- first_postmortem_id: {first_pm_id}\n"
            f"- last_postmortem_id: {last_pm_id}\n"
        )

        for label, pm_id in (("First", first_pm_id), ("Most recent", last_pm_id)):
            if pm_id is None:
                continue
            pm_row = conn.execute(
                """
                SELECT p.failure_signature, p.context_excerpt, p.created_at,
                       e.ticket_id
                  FROM postmortems p
                  JOIN executions e ON e.id = p.execution_id
                 WHERE p.id = ?
                """,
                (pm_id,),
            ).fetchone()
            if pm_row is None:
                continue
            excerpt = (pm_row["context_excerpt"] or "").strip()
            if len(excerpt) > _CONTEXT_EXCERPT_MAX_CHARS:
                excerpt = excerpt[: _CONTEXT_EXCERPT_MAX_CHARS] + "…"
            parts.append(
                f"\n**{label} postmortem #{pm_id}**\n"
                f"- ticket: `{pm_row['ticket_id']}`\n"
                f"- created_at: `{pm_row['created_at']}`\n"
                f"- failure_signature: `{pm_row['failure_signature']}`\n"
                f"- context_excerpt: {excerpt or '_(none)_'}\n"
            )

    description = "".join(parts)
    if len(description.encode("utf-8")) > _MR_DESCRIPTION_MAX_BYTES:
        encoded = description.encode("utf-8")[: _MR_DESCRIPTION_MAX_BYTES - 200]
        description = encoded.decode("utf-8", errors="ignore") + (
            "\n\n_(description truncated at 64 KiB cap)_\n"
        )
    return description


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def propose_overlays(
    conn: sqlite3.Connection,
    *,
    gitlab_client: object,
    repo_root: Path,
    repo_project_path: str,
    scope: str,
    min_confidence: int = 80,
    dry_run: bool = False,
    target_branch: str = "main",
    event_bus: Optional[_EventBusLike] = None,
    execution_id: Optional[str] = None,
) -> list[ProposalResult]:
    """Open a draft MR against the Sentinel repo for promotable rules.

    ``execution_id`` is the synthetic id stamped on each ``FeedbackRulePromoted``
    event. When ``None`` (default — preserved for unit-test callers), the
    module generates ``"learning-propose-<UTC ISO>"`` internally. Production
    callers (the CLI) generate the id once, seed an ``executions`` row with
    that id (so the bus's FK to ``executions.id`` is satisfied), then pass the
    id verbatim — the module uses what it's given without modification.

    Steps (per plan task 12):
      1. ``query_promotable(conn, scope=scope, min_confidence=..., only_unproposed=True)``.
      2. If empty: log INFO and return ``[]``. Do NOT create a branch.
      3. Group rules by ``agent_target`` so a single proposer run can land
         bullets across multiple overlay files (e.g. ``drupal_developer.md``
         AND ``drupal_planner.md``).
      4. ``git checkout -b <branch>`` from ``repo_root``.
      5. For each agent_target group:
         - Compute ``overlay_relpath = prompts/overlays/{scope}_{agent_target}.md``.
         - Raise ``FileNotFoundError`` if the overlay file doesn't exist.
           (We do NOT create overlay files silently.)
         - Render bullets and apply the edit.
      6. If ``dry_run``: revert the branch (``git checkout -`` then
         ``git branch -D <branch>``), return one ``ProposalResult`` per rule
         with ``mr_url="(dry-run)"`` and ``dry_run=True``. **No event publish.**
      7. Else: stage all edited overlay files in one ``git add`` per file,
         commit + push via ``push_overlay_branch``; call
         ``gitlab_client.create_merge_request(..., draft=True)`` (hard-coded);
         ``mark_proposed`` per rule; publish ``FeedbackRulePromoted`` per rule
         when ``event_bus`` is provided.

    Constraints:
      - The dry-run branch-revert step uses ``git branch -D`` which fails if
        the working tree is dirty when we created the branch. Tests run on
        clean tmp repos so this is fine. Production callers should run on a
        clean Sentinel-repo working tree.
      - Push failures abort only this proposer run; un-mark_proposed'd rules
        remain promotable for the next run. The exception bubbles unchanged.
      - ``draft=True`` is hard-coded in the ``create_merge_request`` call.
        Never compute it from a flag.
    """
    rules = query_promotable(
        conn,
        scope=scope,
        min_confidence=min_confidence,
        only_unproposed=True,
    )

    if not rules:
        logger.info(
            "propose_overlays: no promotable rules for scope=%s "
            "(min_confidence=%d, only_unproposed=True); nothing to do",
            scope,
            min_confidence,
        )
        return []

    # Group rules by agent_target so we can edit one overlay per group.
    rules_by_agent: dict[str, list[sqlite3.Row]] = {}
    for rule in rules:
        rules_by_agent.setdefault(rule["agent_target"], []).append(rule)

    # Snapshot the operator's starting ref BEFORE any HEAD mutation. If this
    # raises, no checkout is attempted — the operator's tree is untouched.
    starting_ref = _capture_starting_ref(repo_root)

    branch_name = _branch_name_for(scope)
    try:
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=repo_root,
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else ""
        raise RuntimeError(
            f"git checkout -b {branch_name} failed: {stderr}"
        ) from e
    logger.info(
        "propose_overlays: created branch %s for scope=%s (%d rules across %d agents)",
        branch_name,
        scope,
        len(rules),
        len(rules_by_agent),
    )

    # Map rule_id -> overlay_relpath so we can stamp mark_proposed later.
    overlay_by_rule_id: dict[int, Path] = {}
    edited_overlay_paths: list[Path] = []

    # State contract: if any step below raises, we re-raise unchanged
    # (un-mark_proposed'd rules stay promotable, and we deliberately do NOT
    # delete the promote branch — the operator may want to inspect partial
    # state). The `finally` restores the operator's HEAD to where they
    # started regardless of success/failure.
    try:
        for agent_target, agent_rules in rules_by_agent.items():
            overlay_relpath = _overlay_relpath_for(scope, agent_target)
            if not (repo_root / overlay_relpath).exists():
                raise FileNotFoundError(
                    f"overlay {overlay_relpath} not found in repo {repo_root}"
                )
            bullets = [_render_rule_bullet(r, conn) for r in agent_rules]
            _apply_overlay_edit(repo_root, overlay_relpath, bullets)
            edited_overlay_paths.append(overlay_relpath)
            for r in agent_rules:
                overlay_by_rule_id[r["id"]] = overlay_relpath

        if dry_run:
            # Revert the branch — tests verify no stale branch survives.
            subprocess.run(
                ["git", "checkout", "-"],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=repo_root,
                check=True,
                capture_output=True,
            )
            return [
                ProposalResult(
                    rule_id=int(r["id"]),
                    branch_name=branch_name,
                    mr_url="(dry-run)",
                    dry_run=True,
                    overlay_path=str(overlay_by_rule_id[int(r["id"])]),
                )
                for r in rules
            ]

        # Real run: stage all edited overlays, commit once, push once.
        rule_id_csv = ", ".join(f"rule:{r['id']}" for r in rules)
        plural = "s" if len(rules) != 1 else ""
        commit_message = (
            f"Auto-promote {scope} pitfalls — "
            f"{len(rules)} rule{plural} ({rule_id_csv})"
        )
        push_overlay_branch(
            repo_root,
            branch_name,
            edited_overlay_paths,
            commit_message,
        )
        logger.info(
            "propose_overlays: pushed branch %s (%d overlay file(s) edited)",
            branch_name,
            len(edited_overlay_paths),
        )

        description = _build_mr_description(rules, conn, scope)
        mr = gitlab_client.create_merge_request(  # type: ignore[attr-defined]
            project_id=repo_project_path,
            title=(
                f"Auto-promote {scope} pitfalls — "
                f"{len(rules)} rule{'s' if len(rules) != 1 else ''}"
            ),
            source_branch=branch_name,
            target_branch=target_branch,
            description=description,
            draft=True,  # HARD-CODED — D7 invariant.
        )
        mr_url = mr["web_url"]

        results: list[ProposalResult] = []
        for rule in rules:
            rule_id = int(rule["id"])
            overlay_relpath = overlay_by_rule_id[rule_id]
            mark_proposed(
                conn,
                rule_id=rule_id,
                overlay_path=str(overlay_relpath),
                mr_url=mr_url,
            )
            if event_bus is not None:
                effective_execution_id = execution_id or (
                    "learning-propose-"
                    + datetime.now(timezone.utc).isoformat()
                )
                event_bus.publish(
                    FeedbackRulePromoted(
                        execution_id=effective_execution_id,
                        rule_id=rule_id,
                        scope=scope,
                        mr_url=mr_url,
                        branch_name=branch_name,
                    )
                )
            results.append(
                ProposalResult(
                    rule_id=rule_id,
                    branch_name=branch_name,
                    mr_url=mr_url,
                    dry_run=False,
                    overlay_path=str(overlay_relpath),
                )
            )
        return results

    finally:
        _restore_starting_ref(repo_root, starting_ref)
