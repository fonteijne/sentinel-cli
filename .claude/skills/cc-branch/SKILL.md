---
name: cc-branch
description: Create or switch to the `experimental/command-center-NN-<slug>` branch for a given Command Center plan, per the session branch rule. Use before starting work on a plan track so commits land on the right branch.
user-invocable: true
allowed-tools:
  - Bash
---

# /cc-branch — Command Center experimental branch

Per `sentinel/.claude/PRPs/plans/command-center/00-overview.plan.md` §Branch Strategy and CLAUDE.md memory "Commit experimental work to experimental/ branches".

Arguments: `$ARGUMENTS` — plan number (01-05) optionally followed by a suffix.

## Canonical branch names

| Plan | Branch |
|------|--------|
| 01 | `experimental/command-center-01-foundation` |
| 02 | `experimental/command-center-02-read-api` |
| 03 | `experimental/command-center-03-event-stream` |
| 04 | `experimental/command-center-04-commands-workers` |
| 05 | `experimental/command-center-05-auth` |

## Execution

1. Parse `$ARGUMENTS` → pick the canonical branch name above.
2. Run from the sentinel subrepo:
   ```bash
   cd /workspace/sentinel
   git fetch origin --quiet 2>&1 || true
   git rev-parse --verify "$BRANCH" >/dev/null 2>&1 && git checkout "$BRANCH" || git checkout -b "$BRANCH"
   git status
   ```
3. Verify `git status` shows the branch checked out.
4. If the user is not in `/workspace/sentinel`, surface that — this sandbox cannot `git push` (no SSH keys); the user pushes from sentinel-dev or host.

## Ground rules to remind the user on first use

- Commits land on the experimental branch only — not main, not a feature branch, not a shared dev branch.
- The Claude Code sandbox can commit but cannot push. Push is from sentinel-dev or the host.
- When a plan's work is done, the user opens a PR from the experimental branch per CLAUDE.md's "Landing the Plane" workflow.

Report: branch now checked out, whether it was newly created or pre-existing, and the upstream (if any).
