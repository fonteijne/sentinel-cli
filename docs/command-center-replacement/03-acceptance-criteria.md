# Acceptance Criteria and Definition of Done

## Definition of done

Command Center is a 100% functional remote frontend/replacement when all of the following are true.

| Area | Done when |
|---|---|
| Execution | A dashboard/API run performs the same work as `sentinel execute` |
| Jira | Comments, status updates, attachments, and diagnostics match CLI behavior |
| GitLab | Branches, merge requests, comments, and decision logs match CLI behavior |
| Agents | Developer, reviewer, Drupal, triage, debrief, and self-fix flows work remotely |
| Options | Remote/API/UI expose all meaningful CLI options without silent drops |
| Events | UI timeline reflects real workflow events |
| Artifacts | Plans, logs, diffs, tests, comments, and MR links are persisted |
| Control | Operator can cancel, retry, revise, approve, and clean up |
| Security | Auth, permissions, secrets, and audit are production-safe |
| Reliability | Runs survive common failures or fail cleanly |
| Tests | Golden parity tests prove local CLI and remote behavior match |

## Milestone 1: Real remote execution

### Goal

Command Center starts the real workflow and cannot succeed as a no-op.

### Acceptance criteria

| Test | Expected result |
|---|---|
| Start run from API | Real workflow is invoked |
| Start run from dashboard | Real workflow is invoked |
| Start run from `sentinel execute --remote` | Real workflow is invoked |
| Run has invalid ticket/repo | Run fails clearly |
| Worker produces no artifact | Run fails, never completes green |
| Event stream is observed | Real workflow phases are emitted |

## Milestone 2: Artifact parity

### Goal

Remote execution creates the same primary artifacts as CLI execution.

### Acceptance criteria

| Artifact | Expected result |
|---|---|
| Worktree | Created or intentionally reused |
| Branch | Created with deterministic naming |
| Plan | Persisted and visible |
| Logs | Persisted and streamable |
| Jira comment | Posted or explicitly disabled by config |
| GitLab MR | Created or updated |
| MR decision log | Posted |
| Test result | Persisted and visible |
| Final summary | Persisted and visible |

## Milestone 3: Workflow parity

### Goal

Remote execution supports the full CLI agent loop.

### Acceptance criteria

| Workflow | Expected result |
|---|---|
| Execute | Runs implementation agent |
| Review | Runs review agent |
| Self-fix | Iterates when configured |
| Confidence | Produces score/reasoning |
| Guardrails | Trigger and report correctly |
| Debrief | Allows post-run interaction |
| Revision | Continues from prior run |
| Triage | Classifies/routes tickets |
| Drupal profile | Uses Drupal-specific agents |
| Failure handling | Reports equivalent CLI failure |

## Milestone 4: Operator replacement

### Goal

An operator can use the dashboard instead of the terminal for normal workflows.

### Acceptance criteria

| UX flow | Expected result |
|---|---|
| Create run | Supported in UI |
| Follow run | Live timeline/logs visible |
| Inspect plan | Plan visible |
| Inspect logs | Logs visible |
| Inspect diff | Diff/patch visible |
| Open MR | Link visible and valid |
| Open Jira | Link visible and valid |
| Request revision | Supported |
| Cancel run | Supported |
| Retry run | Supported |
| Clean up worktree | Supported |
| Configure defaults | Supported |

## Milestone 5: Production-ready platform

### Goal

Command Center is safe and reliable for real usage.

### Acceptance criteria

| Area | Expected result |
|---|---|
| Auth | Strong enough for deployment beyond loopback |
| Authorization | Roles/permissions enforced |
| Audit | Sensitive actions recorded |
| Rate limiting | Active and tested |
| Secret handling | Secrets redacted from logs/events/UI |
| Concurrency | Safe for configured run limits |
| Crash recovery | Runs recover or fail clearly |
| Deployment | Runbook exists |
| Monitoring | Logs/metrics available |
| Backup/restore | Documented |

## Golden parity tests

Each golden test should be run locally through the CLI and remotely through Command Center.

The outputs do not need to be byte-for-byte identical, but they must be semantically equivalent.

### Required fixtures

| Fixture | Purpose |
|---|---|
| Generic implementation ticket | Happy path for normal development |
| Drupal implementation ticket | Drupal-specific developer/reviewer flow |
| Review-heavy ticket | Exercises reviewer and self-fix loop |
| Failure ticket | Verifies error handling and diagnostics |
| Follow-up/debrief ticket | Verifies debrief and revision behavior |
| Attachment ticket | Verifies Jira attachment context |
| Self-hosted Jira fixture | Verifies enterprise Jira config |
| SSH GitLab fixture | Verifies SSH transport |

### Required assertions

| Assertion | Local CLI | Remote Command Center |
|---|---|---|
| Fetches Jira ticket | Yes | Yes |
| Reads attachments | Yes | Yes |
| Creates worktree | Yes | Yes |
| Creates branch | Yes | Yes |
| Runs developer agent | Yes | Yes |
| Runs reviewer agent | Yes | Yes |
| Runs self-fix loop if needed | Yes | Yes |
| Creates/updates MR | Yes | Yes |
| Posts MR decision log | Yes | Yes |
| Posts Jira comment | Yes | Yes |
| Persists plan | Optional/local artifact | Required remote artifact |
| Persists logs | Optional/local artifact | Required remote artifact |
| Emits phase events | Optional/log output | Required event stream |
| Handles failure clearly | Yes | Yes |
| Respects flags | Yes | Yes |

## Release gates

### Alpha gate

Command Center can run a real workflow for one happy-path fixture.

Required:

- Scaffold/no-op success path is removed or disabled.
- Remote run invokes real workflow.
- Worktree is created.
- Logs are persisted.
- Run status reflects real outcome.

### Beta gate

Command Center supports the main happy path end to end.

Required:

- Jira ticket context is fetched.
- Git branch/worktree is created.
- GitLab MR is created or updated.
- Jira and GitLab comments are posted.
- Dashboard shows real timeline and links.
- Remote options cover the most common CLI flags.

### Release candidate gate

Command Center supports critical workflows with parity tests.

Required:

- Generic and Drupal workflows pass.
- Review and self-fix workflows pass.
- Debrief/revision workflow passes.
- Failure workflow passes.
- Remote option parity is complete for supported flags.
- Security/auth model is production-safe.

### Replacement gate

Command Center can be recommended over terminal usage for normal operation.

Required:

- All golden parity tests pass.
- UI supports start/follow/retry/cancel/revise/cleanup.
- Jira/GitLab side effects match CLI behavior.
- No silent option drops.
- Operational runbook exists.
- Rollback path to local CLI is documented.

## Non-negotiable checks

- A run that did not invoke the real workflow must never complete successfully.
- A run that produced no worktree, no agent activity, no MR, and no external action must be marked failed or incomplete.
- Remote execution must not silently drop CLI options.
- Dashboard state must reflect persisted backend state, not optimistic placeholders.
- Jira/GitLab side effects must be idempotent to avoid comment spam on retries.
- Secrets must never appear in events, logs, screenshots, artifacts, or UI.

