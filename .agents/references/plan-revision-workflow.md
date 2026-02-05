# Plan Revision Workflow

**Date**: 2026-01-24
**Feature**: Plan revision based on MR feedback

## Overview

Sentinel can now iterate on implementation plans based on team feedback from GitLab merge requests. The agent analyzes unresolved discussions, decides between incremental updates or full rewrites, and responds to each discussion explaining what changed.

## Usage

```bash
# Initial plan generation
sentinel plan sentest-1

# After team leaves feedback in the MR...
# Revise the plan based on unresolved discussions
sentinel plan sentest-1 --revise
```

## Workflow Steps

### 1. Initial Plan Creation
```bash
$ sentinel plan sentest-1
```
- Creates worktree
- Generates initial plan
- Commits and pushes plan
- Creates draft MR
- Adds Jira comment with MR link

### 2. Team Review
- Team reviews the plan in the GitLab MR
- Leaves comments and discussions
- Some discussions remain unresolved (requiring changes)

### 3. Plan Revision
```bash
$ sentinel plan sentest-1 --revise
```

The agent:
1. **Fetches unresolved discussions** from the MR
2. **Analyzes feedback** using LLM
3. **Decides revision approach**:
   - **Incremental update**: Minor clarifications, section tweaks
   - **Full rewrite**: Fundamental approach change
4. **Revises the plan** based on feedback
5. **Commits and pushes** the updated plan
6. **Replies to each discussion** with:
   - What changes were made
   - Which sections were affected
   - Revision type (incremental/full rewrite)
7. **Posts summary comment** to MR with:
   - Revision type and rationale
   - Number of discussions addressed

## Example Output

```
🔄 Revising plan for: sentest-1
🏗️  Project: sentest

1️⃣  Fetching MR feedback...
   ✓ Found 3 unresolved discussion(s)

2️⃣  Revising plan based on feedback...
   ✓ Revision type: Incremental

3️⃣  Updating MR...
   ✓ Revised plan committed and pushed
   ✓ Posted 3 response(s) to discussions
   ✓ Added revision summary to MR

✅ Plan revision complete for sentest-1
   MR: https://gitlab.com/vpl-test2/todo/-/merge_requests/42
   Next: Review the updated plan and address any remaining feedback
```

## Technical Details

### GitLabClient New Methods

#### `get_merge_request_discussions()`
```python
discussions = gitlab.get_merge_request_discussions(
    project_id="vpl-test2/todo",
    mr_iid=42,
    unresolved_only=True,  # Only fetch unresolved discussions
)
```

Returns list of discussions with:
- `id`: Discussion ID
- `notes`: List of comments in the discussion
- `resolved`: Whether discussion is resolved

#### `reply_to_discussion()`
```python
gitlab.reply_to_discussion(
    project_id="vpl-test2/todo",
    mr_iid=42,
    discussion_id="abc123",
    body="**Plan Updated** 🤖\n\nAdded more details...",
    resolve=False,  # Don't auto-resolve, let reviewers confirm
)
```

### PlanGeneratorAgent New Methods

#### `revise_plan()`
Core revision logic that:
- Formats feedback for LLM
- Calls LLM with revision prompt
- Parses JSON response with:
  - `revision_type`: "incremental" or "full_rewrite"
  - `rationale`: Why this approach was chosen
  - `revised_plan`: Updated markdown content
  - `feedback_responses`: Per-discussion explanations
- Writes revised plan to file

#### `run_revision()`
Orchestrates the full revision workflow:
1. Read existing plan from file
2. Find the MR by source branch
3. Fetch unresolved discussions
4. Call `revise_plan()` with feedback
5. Commit and push updated plan
6. Reply to each discussion
7. Post summary comment to MR

### LLM Decision Making

The agent receives a prompt with:
- Current plan content
- All unresolved discussions
- Instructions to decide: incremental vs full rewrite

**Incremental update criteria**:
- Minor clarifications
- Adding missing details
- Fixing typos or formatting
- Updating specific sections

**Full rewrite criteria**:
- Fundamental approach challenged
- Major architecture change needed
- Multiple sections need restructuring

## Configuration

No additional configuration required. Uses existing:
- `config.yaml` - Project git URLs
- GitLab API token from environment
- Agent model settings (Claude Opus 4.5 for planning)

## Feedback Format

The agent sees feedback in this format:

```
**Feedback 1** (ID: abc123)
Author: John Doe
Comment: The database schema should use UUIDs instead of auto-increment IDs for better scalability.

Replies:
  - Jane Smith: Good point, UUIDs would help with distributed systems
```

## Response Format

Agent replies to discussions with:

```markdown
**Plan Updated** 🤖

Updated the database schema section to use UUID primary keys
instead of auto-increment integers. Added migration strategy and
noted impact on foreign key relationships.

**Section(s) affected:** Database Schema, Data Models, Migrations

**Revision type:** incremental
```

## Limitations & Future Enhancements

### Current Limitations
- Only processes unresolved discussions
- No auto-resolve (reviewers must manually resolve)
- Single revision pass (doesn't iterate until all resolved)

### Potential Enhancements
- Multi-iteration mode: keep revising until no unresolved discussions
- Allow filtering by reviewer or label
- Option to auto-resolve discussions when addressed
- Track revision history in plan file
- Generate diff summary of plan changes
- Support resolving specific discussions by ID

## Best Practices

1. **Leave clear feedback**: Be specific about what needs to change
2. **Mark discussions as unresolved**: Use GitLab's discussion resolution feature
3. **Review agent responses**: Verify the changes match your intent
4. **Iterate as needed**: Run `--revise` multiple times if needed
5. **Resolve manually**: Review changes before resolving discussions

## Related Files

- [`src/gitlab_client.py`](../../src/gitlab_client.py) - GitLab API client
- [`src/agents/plan_generator.py`](../../src/agents/plan_generator.py) - Plan generation and revision
- [`src/cli.py`](../../src/cli.py) - CLI command interface
