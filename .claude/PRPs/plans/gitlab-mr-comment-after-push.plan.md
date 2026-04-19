# Feature: GitLab MR Comment After Push in Execute Flow

## Summary

Add a GitLab MR comment after pushing new commits in the **revision flow** (`sentinel execute --revise`) to report what was done. The normal execute flow already posts a decision log comment after push, but the revision flow pushes commits without any post-push MR notification. This creates a gap: the revision flow's summary comment is posted *before* the push, meaning it references changes not yet visible on the MR.

## User Story

As a developer reviewing MRs
I want to see a comment on the MR after Sentinel pushes revision commits
So that I know what changes were made and can review them in context

## Problem Statement

The `sentinel execute --revise` flow pushes commits to the MR but does not post a comment afterwards. The only comment (`Implementation Revision Summary`) is posted by `run_revision()` *before* the push. The normal execute flow correctly posts a decision log *after* push (cli.py:675-692), but the revision flow (cli.py:448-491) lacks this entirely — no MR comment, no mark-as-ready call, no Jira notification.

## Solution Statement

Add post-push GitLab MR operations to the revision flow, mirroring the pattern already established in the normal execute flow (cli.py:650-693). Create a `_format_revision_log()` helper and wire it into the revision flow's post-push success block.

## Metadata

| Field            | Value                                   |
| ---------------- | --------------------------------------- |
| Type             | ENHANCEMENT                             |
| Complexity       | LOW                                     |
| Systems Affected | cli.py (execute command)                |
| Dependencies     | None (uses existing GitLabClient)       |
| Estimated Tasks  | 2                                       |

---

## UX Design

### Before State

```
┌─────────────────────┐       ┌──────────────────┐       ┌────────────────────┐
│ sentinel execute    │       │ run_revision()   │       │ git push           │
│ --revise TICKET-123 │──────►│ - fetch feedback │──────►│ push to origin     │
│                     │       │ - implement fixes│       │                    │
│                     │       │ - post summary   │       │ (no MR comment)    │
│                     │       │   comment to MR  │       │ (no mark-as-ready) │
│                     │       │ - commit changes │       │ (no Jira notify)   │
└─────────────────────┘       └──────────────────┘       └────────────────────┘

USER EXPERIENCE: Developer sees a summary comment on the MR *before* the
commits arrive. After push, there's no notification that revision is complete.
The MR stays in "Draft" status even though revision is done.
```

### After State

```
┌─────────────────────┐       ┌──────────────────┐       ┌────────────────────┐
│ sentinel execute    │       │ run_revision()   │       │ git push           │
│ --revise TICKET-123 │──────►│ - fetch feedback │──────►│ push to origin     │
│                     │       │ - implement fixes│       │        │           │
│                     │       │ - post summary   │       │        ▼           │
│                     │       │   comment to MR  │       │ ┌──────────────┐   │
│                     │       │ - commit changes │       │ │ Post revision│   │
│                     │       │                  │       │ │ log comment  │   │
│                     │       │                  │       │ │ to MR        │   │
│                     │       │                  │       │ └──────────────┘   │
└─────────────────────┘       └──────────────────┘       └────────────────────┘

USER EXPERIENCE: Developer sees summary comment during revision, then a
"Revision Complete" comment after commits are pushed. MR is updated with
status of tasks, tests, and config validation.
```

### Interaction Changes

| Location | Before | After | User Impact |
|----------|--------|-------|-------------|
| MR comments (revision flow) | No comment after push | Revision log comment posted after push | Developer sees what changed when commits appear |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File | Lines | Why Read This |
|----------|------|-------|---------------|
| P0 | `src/cli.py` | 269-311 | `_format_decision_log()` — pattern to MIRROR for revision log |
| P0 | `src/cli.py` | 448-491 | Revision flow push block — WHERE to add the new code |
| P0 | `src/cli.py` | 650-693 | Normal execute post-push block — pattern to MIRROR exactly |
| P1 | `src/gitlab_client.py` | 179-207 | `add_merge_request_comment()` — API method to use |
| P1 | `src/gitlab_client.py` | 209-239 | `list_merge_requests()` — how to find MR for branch |
| P2 | `src/gitlab_client.py` | 263-283 | `mark_as_ready()` — optional: also mark revision MR as ready |

**External Documentation:**

| Source | Section | Why Needed |
|--------|---------|------------|
| [GitLab Notes API](https://docs.gitlab.com/api/notes.html#create-new-merge-request-note) | Create MR note | Existing client already wraps this — no new API work needed |

---

## Patterns to Mirror

**DECISION_LOG_FORMAT:**
```python
# SOURCE: src/cli.py:269-311
# COPY THIS PATTERN for the revision log formatter:
def _format_decision_log(ticket_id: str, iteration: int, dev_result: dict, sec_result: dict) -> str:
    """Format a concise decision log for the GitLab MR comment."""
    from datetime import datetime, timezone

    lines = [
        "## Sentinel Execution Summary",
        "",
        f"**Ticket:** `{ticket_id}`  ",
        f"**Iterations:** {iteration}  ",
        f"**Status:** Approved",
        ...
    ]
    return "\n".join(lines)
```

**POST_PUSH_MR_OPERATIONS:**
```python
# SOURCE: src/cli.py:650-693
# COPY THIS PATTERN for the revision flow:
try:
    from src.gitlab_client import GitLabClient

    gitlab = GitLabClient()
    config = get_config()
    project_config = config.get_project_config(project)
    git_url = project_config.get("git_url", "")
    project_path = GitLabClient.extract_project_path(git_url)
    source_branch = get_branch_name(ticket_id)
    mrs = gitlab.list_merge_requests(
        project_id=project_path,
        source_branch=source_branch,
    )

    if mrs:
        mr_iid = mrs[0]["iid"]
        # ... post comment ...
        gitlab.add_merge_request_comment(
            project_id=project_path,
            mr_iid=mr_iid,
            body=comment_body,
        )

except Exception as e:
    logger.warning(f"Failed to ...: {e}")
```

**ERROR_HANDLING:**
```python
# SOURCE: src/cli.py:690-692
# Non-fatal pattern — comment failures don't abort execution:
except Exception as e:
    logger.warning(f"Failed to post decision log comment: {e}")
    # Non-fatal error - execution continues
```

---

## Files to Change

| File | Action | Justification |
|------|--------|---------------|
| `src/cli.py` | UPDATE | Add `_format_revision_log()` helper and post-push MR comment block in revision flow |

---

## NOT Building (Scope Limits)

- **No per-iteration comments in normal execute flow** — the existing decision log already summarizes all iterations after push; splitting this per-iteration would require pushing per-iteration, which is a larger architectural change
- **No Jira notification in revision flow** — out of scope for this task, though it's another gap
- **No mark-as-ready in revision flow** — the MR may need to stay in review status during revisions; marking as ready could be a separate enhancement
- **No changes to `run_revision()` in base_developer.py** — the pre-push summary comment it posts is separate and should remain

---

## Step-by-Step Tasks

### Task 1: ADD `_format_revision_log()` helper function to `src/cli.py`

- **ACTION**: ADD a new function after `_format_decision_log()` (after line 311)
- **IMPLEMENT**: Create `_format_revision_log(ticket_id: str, result: dict) -> str` that formats:
  - Header: "## Sentinel Revision Complete"
  - Ticket ID
  - Discussions analyzed count
  - Tasks completed/failed
  - Questions answered
  - Acknowledged items
  - Test status (passing/failing)
  - Config validation status
  - Timestamp footer
- **MIRROR**: `src/cli.py:269-311` — follow `_format_decision_log()` structure exactly
- **SIGNATURE**: `def _format_revision_log(ticket_id: str, result: dict) -> str:`
- **RESULT KEYS** (from `base_developer.py:1253-1267`): `feedback_count`, `tasks_completed`, `tasks_failed`, `questions`, `questions_answered`, `questions_failed`, `acknowledged`, `test_results`, `config_validation`, `changes_committed`, `responses_posted`
- **GOTCHA**: Use `.get()` with defaults for all dict access since some keys may be missing
- **VALIDATE**: `python -c "from src.cli import _format_revision_log"` (import check)

### Task 2: ADD post-push MR comment block to revision flow in `src/cli.py`

- **ACTION**: ADD GitLab MR operations after successful push in the revision flow (after line 478)
- **IMPLEMENT**: After `click.echo(f"   ✓ Pushed to origin/{branch_name}")`, add a try/except block that:
  1. Imports and instantiates `GitLabClient`
  2. Gets project config and extracts git URL
  3. Extracts project path from git URL
  4. Finds MR for the source branch
  5. Formats revision log using `_format_revision_log(ticket_id, result)`
  6. Posts comment via `gitlab.add_merge_request_comment()`
  7. Prints success/failure to CLI
- **MIRROR**: `src/cli.py:650-693` — follow the normal execute post-push pattern exactly
- **GOTCHA**: The `result` variable is already in scope from line 404 (`result = developer.run_revision(...)`)
- **GOTCHA**: The `project` variable is available from the function parameter
- **GOTCHA**: Use non-fatal error handling — comment failure should not abort the revision workflow
- **VALIDATE**: Read the file after editing to verify the indentation matches the surrounding try/except blocks

---

## Testing Strategy

### Manual Testing

1. Run `sentinel execute TICKET-123 --revise` on a ticket with MR feedback
2. Verify that after push, a "Sentinel Revision Complete" comment appears on the MR
3. Verify the comment contains correct task/question/test counts
4. Verify that if GitLab is unreachable, the revision still completes without error

### Edge Cases Checklist

- [ ] `result` dict has missing keys (e.g., no `test_results`) — `.get()` defaults handle this
- [ ] No MR found for branch — should log warning and continue (same as normal flow)
- [ ] GitLab API returns error — should be caught as non-fatal
- [ ] `feedback_count` is 0 — revision flow returns early at line 406-409, so this block never runs
- [ ] Empty `git_url` in project config — `extract_project_path("")` should be handled

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
cd /workspace/sentinel && python -m py_compile src/cli.py
```

**EXPECT**: Exit 0, no syntax errors

### Level 2: IMPORT_CHECK

```bash
cd /workspace/sentinel && python -c "from src.cli import _format_revision_log; print('OK')"
```

**EXPECT**: Prints "OK"

### Level 3: FORMAT_CHECK

```bash
cd /workspace/sentinel && python -c "
from src.cli import _format_revision_log
result = {
    'feedback_count': 3,
    'tasks_completed': 2,
    'tasks_failed': 1,
    'questions': 1,
    'questions_answered': 1,
    'questions_failed': 0,
    'acknowledged': 1,
    'test_results': {'success': True},
    'config_validation': {'success': True},
    'changes_committed': True,
    'responses_posted': 3,
}
log = _format_revision_log('TICKET-123', result)
assert '## Sentinel Revision Complete' in log
assert 'TICKET-123' in log
assert '2' in log  # tasks_completed
print(log)
print('--- PASS ---')
"
```

**EXPECT**: Formatted revision log printed, "--- PASS ---" at end

### Level 4: FULL_SUITE

```bash
cd /workspace/sentinel && python -m pytest tests/ -x --timeout=30 2>/dev/null || echo "No tests or test failures"
```

**EXPECT**: No regressions in existing tests

---

## Acceptance Criteria

- [ ] `_format_revision_log()` produces a clean markdown summary with ticket ID, task counts, test status, and timestamp
- [ ] Revision flow posts MR comment after successful push
- [ ] Comment failures are non-fatal (logged as warning, execution continues)
- [ ] CLI prints "✓ Revision log posted to MR" on success
- [ ] No changes to the normal execute flow (decision log remains as-is)
- [ ] Code mirrors existing patterns exactly (imports, error handling, logging)

---

## Completion Checklist

- [ ] Task 1: `_format_revision_log()` created and import-verified
- [ ] Task 2: Post-push MR comment block added to revision flow
- [ ] Level 1: py_compile passes
- [ ] Level 2: Import check passes
- [ ] Level 3: Format output is correct
- [ ] Level 4: No test regressions

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| GitLab API rate limiting | LOW | LOW | Non-fatal error handling; only one API call added |
| Missing result dict keys | MED | LOW | Use `.get()` with defaults for all dict access |
| Duplicate comments (pre-push summary + post-push log) | MED | LOW | Different headers ("Implementation Revision Summary" vs "Sentinel Revision Complete") make them distinguishable |

---

## Notes

- The `run_revision()` method in `base_developer.py` already posts a pre-push summary comment. The new post-push comment will appear after the commits are visible on the MR, providing a clear "revision complete" signal.
- The revision flow also lacks Jira notification and mark-as-ready calls (both present in normal execute). These could be added as follow-up enhancements but are out of scope here.
- The `result` variable from `developer.run_revision()` (line 404) is already in scope at the push block (line 448+), so no additional data threading is needed.
