# Sentinel Command Center Replacement Plan

This documentation set describes how to evolve `v2/command-center-ui` into a fully functional remote frontend and eventual replacement for the current `main` CLI workflows.

The CLI on `main` is treated as the baseline. Command Center must reach feature parity with the CLI before it can be considered a replacement.

## Documents

| File | Purpose |
|---|---|
| `01-gap-analysis.md` | Current workflow gaps between the `main` CLI and `v2/command-center-ui` Command Center |
| `02-implementation-roadmap.md` | Phased plan to reach 100% remote frontend/replacement functionality |
| `03-acceptance-criteria.md` | Definition of done, parity tests, and release gates |
| `04-prioritized-backlog.md` | Implementation backlog ordered by dependency and impact |

## Executive summary

The Command Center branch currently has a strong service and dashboard shell: FastAPI routes, persistence, WebSocket/event infrastructure, auth, deployment scaffolding, and a React dashboard.

However, Command Center executions do not yet run the real CLI workflows. The worker currently falls back to a scaffold/no-op execution path, which can mark a run successful without creating a worktree, running agents, creating a GitLab merge request, or posting Jira comments.

The highest-priority fix is to make Command Center call the same shared workflow engine as the CLI.

## Target state

Command Center is considered a full remote frontend/replacement when:

- A dashboard/API run performs the same work as `sentinel execute`.
- Jira comments, status updates, attachments, and diagnostics match CLI behavior.
- GitLab branches, merge requests, comments, and decision logs match CLI behavior.
- Developer, reviewer, Drupal, triage, debrief, confidence, guardrail, and self-fix workflows work remotely.
- Remote/API/UI execution exposes all meaningful CLI options without silent drops.
- The dashboard timeline reflects real workflow events, not synthetic placeholders.
- Operators can start, follow, cancel, retry, revise, approve, and clean up runs without terminal access.
- Golden parity tests prove that local CLI execution and remote Command Center execution produce equivalent artifacts.

