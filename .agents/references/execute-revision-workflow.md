# Execute Revision Workflow

**Date**: 2026-01-28
**Feature**: Implementation revision based on MR feedback

## Overview

Sentinel can now iterate on code implementations based on team feedback from GitLab merge requests. The agent analyzes unresolved discussions, converts them to implementation tasks, uses TDD to implement fixes, and responds to each discussion explaining what changed.

## Usage

```bash
# Initial implementation
sentinel execute sentest-1

# After team leaves feedback in the MR...
# Revise the implementation based on unresolved discussions
sentinel execute sentest-1 --revise
```

## Workflow Steps

### 1. Initial Implementation
```bash
$ sentinel execute sentest-1
```
- Reads implementation plan
- Implements features using TDD
- Runs security review iterations
- Commits and pushes code
- Updates existing draft MR

### 2. Team Review
- Team reviews the implementation in the GitLab MR
- Leaves comments and discussions on specific code changes
- Some discussions remain unresolved (requiring changes)

### 3. Implementation Revision
```bash
$ sentinel execute sentest-1 --revise
```

The agent:
1. **Fetches unresolved discussions** from the MR
2. **Converts feedback to tasks** using LLM to extract actionable items
3. **Implements fixes** using TDD workflow for each task
4. **Commits changes** with clear commit messages
5. **Runs tests** to verify fixes
6. **Replies to each discussion** with:
   - What changes were made
   - Which files were affected
   - Test status
   - **Resolves the thread automatically** to prevent duplicate iterations
7. **Posts summary comment** to MR with:
   - Number of discussions addressed
   - Tasks completed/failed
   - Test results

## Example Output

```
🔄 Revising implementation for: sentest-1
🏗️  Project: sentest

1️⃣  Fetching MR feedback...
   ✓ Found 3 unresolved discussion(s)

2️⃣  Implementing fixes based on feedback...
   ✓ 3 task(s) completed

3️⃣  Updating MR...
   ✓ Revised implementation committed
   ✓ Posted 3 response(s) to discussions
   ✓ All tests passing

4️⃣  Pushing changes to remote...
   ✓ Pushed to origin/feature/sentest-1

✅ Implementation revision complete for sentest-1
   MR: https://gitlab.com/vpl-test2/todo/-/merge_requests/42
   Next: Review the updated implementation and address any remaining feedback
```

## Technical Details

### PythonDeveloperAgent New Methods

#### `run_revision()`
Orchestrates the full revision workflow:
1. Find the MR by source branch
2. Fetch unresolved discussions from GitLab
3. Use LLM to convert feedback to actionable tasks
4. Implement each task using TDD workflow (`implement_feature`)
5. Commit changes for each task
6. Run tests to verify all fixes
7. Reply to each discussion with implementation details
8. Post summary comment to MR

#### `_format_mr_feedback()`
Formats GitLab discussions for LLM consumption:
- Extracts author, comment body, and discussion ID
- Includes reply threads if present
- Returns formatted text for LLM to parse

### Revision Task Extraction

The agent uses LLM to intelligently convert review feedback into implementation tasks:

**Input (Feedback):**
```
Author: Jane Doe
Comment: The email validation is too permissive. It should reject emails without '@' symbol and verify the domain has a TLD.
```

**Output (Task):**
```
Fix email validation to check for '@' symbol and verify domain has valid TLD
```

### Implementation Process

For each extracted task:
1. **TDD Workflow** - Uses same TDD process as initial implementation:
   - Write failing test (RED)
   - Implement minimal fix (GREEN)
   - Refactor (REFACTOR)
2. **Commit** - Each task gets its own commit with clear message
3. **Track** - Results tracked for summary reporting

### MR Discussion Responses

Agent replies to discussions with:

```markdown
**Implementation Updated** 🤖

Fix email validation to check for '@' symbol and verify domain has valid TLD

**Files changed:** src/validators.py, tests/test_validators.py

**Tests:** ✅ Passing
```

Also adds emoji reactions:
- ✅ (white_check_mark) - For successfully implemented feedback

## Configuration

No additional configuration required. Uses existing:
- `config.yaml` - Project git URLs
- GitLab API token from environment
- Agent model settings (Claude Sonnet 4.5 for implementation)

## Comparison with Plan Revision

| Aspect | Plan Revision | Execute Revision |
|--------|--------------|------------------|
| **What's revised** | Implementation plan (markdown) | Actual code |
| **How** | LLM rewrites plan sections | TDD workflow implements fixes |
| **Output** | Updated `.md` file | Code changes, commits |
| **Testing** | N/A (plan is documentation) | Runs pytest after changes |
| **Iterations** | Single pass | Can iterate with security review |

## Force Push Option

If the remote branch has diverged (e.g., from manual changes), use `--force`:

```bash
sentinel execute sentest-1 --revise --force
```

**Warning:** This will overwrite remote commits. Only use when you're certain.

## Limitations & Future Enhancements

### Current Limitations
- Only processes unresolved discussions
- Single revision pass (doesn't iterate until all resolved)
- Maps discussions to tasks sequentially (one discussion = one task)

### Potential Enhancements
- Multi-iteration mode: keep revising until no unresolved discussions
- Group related feedback into compound tasks
- Track revision history in commit messages
- Re-run security review after revision
- Support filtering by reviewer or label
- Generate diff summary of code changes

## Best Practices

1. **Leave specific feedback**: Be clear about what code should change
2. **Mark discussions as unresolved**: Use GitLab's discussion resolution feature
3. **One discussion per issue**: Easier for agent to map feedback to tasks
4. **Review agent responses**: Verify the changes match your intent
5. **Iterate as needed**: Run `--revise` multiple times if needed
6. **Reopen if needed**: Threads are auto-resolved after revision; reopen if you disagree with the solution

## Related Files

- [sentinel/src/agents/python_developer.py](../../src/agents/python_developer.py) - Implementation and revision
- [sentinel/src/gitlab_client.py](../../src/gitlab_client.py) - GitLab API client
- [sentinel/src/cli.py](../../src/cli.py) - CLI command interface
- [plan-revision-workflow.md](./plan-revision-workflow.md) - Plan revision (similar workflow for plans)

## Example Scenarios

### Scenario 1: Security Concerns

**Feedback:**
> This endpoint doesn't validate the user ID. An attacker could access other users' data.

**Revision:**
- Adds authentication check
- Validates user owns the resource
- Adds tests for unauthorized access
- Commits: `fix: Add user authorization check to profile endpoint`

### Scenario 2: Missing Error Handling

**Feedback:**
> What happens if the database connection fails? This will crash the app.

**Revision:**
- Adds try/except around database calls
- Returns proper error responses
- Adds tests for database failures
- Commits: `fix: Add error handling for database connection failures`

### Scenario 3: Performance Issue

**Feedback:**
> This N+1 query will be slow with many users. Should use eager loading.

**Revision:**
- Refactors to use SELECT with JOIN
- Adds benchmark test
- Verifies query count reduced
- Commits: `perf: Fix N+1 query in user list endpoint`

## Testing the Feature

To test the execute revision workflow:

1. Create a test ticket and run initial implementation
2. Leave unresolved comments on the MR
3. Run `sentinel execute TICKET-ID --revise`
4. Verify:
   - Feedback extracted correctly
   - Code changes address the issues
   - Tests pass
   - MR discussions have replies
   - Summary comment posted

## Troubleshooting

### No MR Found
**Error:** `No MR found for branch feature/TICKET-123`

**Solution:** Run `sentinel execute TICKET-123` first to create the initial implementation and MR.

### Push Rejected
**Error:** `Push rejected: remote branch has diverged`

**Solution:** Use `--force` flag if you want to overwrite remote changes, or manually merge/rebase first.

### Tests Failing
**Warning:** `Some tests failing - review needed`

**Solution:** Review test output, manually fix remaining issues, or run `--revise` again with updated feedback.

### LLM Extraction Failed
**Error:** `Failed to extract revision tasks`

**Solution:** Check that feedback is clear and actionable. Very vague feedback ("make it better") won't extract well.
