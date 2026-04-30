# Implementation Roadmap

## Goal

Make `v2/command-center-ui` a fully functional remote frontend and eventual replacement for the `main` CLI workflows.

## Guiding principle

Do not build Command Center as a separate workflow engine.

Build it as a remote control plane over the same workflow engine used by the CLI.

The CLI and Command Center should differ in presentation and control surface, not in business behavior.

## Target architecture

```text
CLI command
   |
   v
Shared workflow application layer
   |
   +--> Jira adapter
   +--> Git adapter
   +--> GitLab adapter
   +--> Agent runtime
   +--> Test/container runner
   +--> Debrief/revision loop
   +--> Event bus
   +--> Artifact store
   |
   v
Command Center worker
   |
   +--> API
   +--> Persistence
   +--> WebSocket/SSE stream
   +--> React dashboard
```

## Phase 0: Lock the baseline

### Objective

Define exactly what “100% functional replacement” means.

### Deliverables

| Deliverable | Description |
|---|---|
| CLI workflow inventory | List all commands, flags, side effects, configuration, and artifacts |
| Parity contract | Machine-checkable spec for expected behavior |
| Golden fixtures | Representative Jira tickets/repos for happy paths and failures |
| Gap tracker | Issue list mapped to parity features |

### Acceptance criteria

- Every CLI workflow has documented inputs, outputs, side effects, artifacts, and error modes.
- Every external integration is mapped.
- Every CLI flag is marked supported, deprecated, unsafe, or intentionally unsupported remotely.
- Golden test fixtures exist for generic, Drupal, review, failure, and follow-up workflows.

## Phase 1: Replace scaffold execution

### Objective

Remove the fake execution path and make Command Center call the real workflow.

### Work items

| Work item | Description |
|---|---|
| Quarantine scaffold execution | `_scaffold_run()` must not be reachable in production mode |
| Add real workflow invocation | Worker calls the same execution path as `sentinel execute` |
| Add no-op failure guard | Runs fail if no real workflow artifact is produced |
| Add correlation ID | One ID across logs, events, Jira, GitLab, and artifacts |
| Add lifecycle states | `queued`, `starting`, `planning`, `executing`, `reviewing`, `debriefing`, `completed`, `failed`, `cancelled` |

### Acceptance criteria

- API/dashboard execution invokes the real workflow.
- No-op runs cannot complete successfully.
- Tests fail if the worker completes without invoking the workflow.
- A remote run produces at least one real workflow artifact.

## Phase 2: Extract shared workflow engine

### Objective

Refactor CLI workflow behavior into a reusable application layer.

### Work items

| Work item | Description |
|---|---|
| Create workflow runner | Shared service used by both CLI and Command Center |
| Normalize execution options | One schema for CLI, remote CLI, API, worker, and UI |
| Split presentation from behavior | Click commands parse inputs, then call workflow runner |
| Adapterize side effects | Jira, GitLab, git, SSH, env, and containers behind interfaces |
| Add event bus | Workflows emit structured events |
| Add artifact model | Persist logs, plans, diffs, MR URLs, comments, and test results |

### Acceptance criteria

- Existing CLI behavior still works.
- Command Center worker uses the shared runner.
- CLI and service receive equivalent event streams.
- Side-effect adapters can be mocked in tests.

## Phase 3: Remote option parity

### Objective

Make remote execution preserve local CLI semantics.

### Required parity

| CLI capability | Remote/API/UI requirement |
|---|---|
| `--force` | Supported with explicit confirmation/permission |
| `--no-env` | Supported |
| `--max-iterations` | Supported |
| `--prompt` | Supported |
| `revise` | First-class revision flow |
| `max_turns` | Supported where relevant |
| `follow_up_ticket` | Linked to Jira/debrief workflow |
| Repo target | Validated and persisted |
| Branch naming | Configurable or deterministic |
| Agent/profile selection | Generic and Drupal workflows supported |
| Dry run | Supported |
| Review-only/triage-only/debrief | Supported where CLI supports them |

### Acceptance criteria

- No option is silently dropped.
- Unsupported options fail validation.
- `sentinel execute --remote` preserves meaningful local flags.
- UI exposes normal and advanced execution options.
- API schema is versioned.

## Phase 4: Jira parity

### Objective

Reproduce CLI Jira behavior from Command Center.

### Work items

| Feature | Requirement |
|---|---|
| Ticket fetch | Fetch summary, description, fields, comments, links, status, assignee |
| Attachments | Download and parse supported attachments |
| Self-hosted Jira | Support custom base URLs and auth modes |
| Jira comments | Post run started, plan, MR created, review findings, completion, failure |
| Status transitions | Optional configured transitions |
| Follow-up tickets | Create/link when requested |
| Failure diagnostics | Expose and optionally post useful failure context |
| VPN hints | Surface connectivity hints |
| Idempotency | Avoid duplicate comments on retries |

### Acceptance criteria

- Remote run can fetch Jira context.
- Remote run posts equivalent Jira comments.
- Attachments are included in execution context.
- Failure comments are clear and actionable.
- Self-hosted Jira is covered by integration or contract tests.

## Phase 5: Git and GitLab parity

### Objective

Remote runs must produce the same Git/GitLab artifacts as local CLI runs.

### Work items

| Feature | Requirement |
|---|---|
| Worktree creation | Isolated working area per run |
| Branch creation | Deterministic branch with collision handling |
| Commit/push | Match CLI behavior |
| MR creation | Create/update GitLab MR |
| MR comments | Post review findings and decision logs |
| Existing MR handling | Reuse/update where appropriate |
| Cleanup | Reset/delete worktree from API/UI |
| SSH transport | Preserve CLI SSH behavior |

### Acceptance criteria

- Happy path remote run creates branch/worktree.
- Happy path remote run opens or updates MR.
- MR contains decision log.
- GitLab comments are posted correctly.
- Worktree reset/delete works through backend actions.

## Phase 6: Agent workflow parity

### Objective

All CLI agent workflows must work remotely.

### Required workflows

| Workflow | Requirement |
|---|---|
| Plan | Persist and stream plan |
| Execute | Run developer agent |
| Review | Run reviewer agent |
| Self-fix | Iterate on findings/failures |
| Confidence | Persist score and reasoning |
| Guardrails | Apply same checks as CLI |
| Triage | Support ticket classification/routing |
| Debrief | Support post-run discussion |
| Revision | Continue from prior run with new instructions |
| Drupal developer | Support Drupal-specific implementation |
| Drupal reviewer | Support Drupal-specific review |

### Acceptance criteria

- Generic workflow completes remotely.
- Drupal workflow completes remotely.
- Review/self-fix loop works remotely.
- Debrief and revision work from UI/API.
- Artifacts are persisted and visible.

## Phase 7: Dashboard replacement UX

### Objective

Make the dashboard sufficient for normal operation without terminal access.

### Required pages

| Page | Features |
|---|---|
| Runs | List, filter, search, statuses, owner, repo, ticket |
| Run detail | Timeline, logs, artifacts, options, Jira/GitLab links |
| New run | Ticket/repo input, profile, flags, prompt, dry-run, force |
| Inbox | Approval, debrief, revision, and failure tasks |
| Worktrees | List, inspect, reset, delete |
| Settings | Jira, GitLab, repo defaults, profiles, auth, deployment |
| Insights | Success rate, failures, duration, review findings |
| Audit log | Who did what and when |

### Required controls

- Start run.
- Cancel run.
- Retry run.
- Request revision.
- Approve risky action.
- Reset/delete worktree.
- Open Jira.
- Open GitLab MR.

### Acceptance criteria

- Operator can go from Jira ticket to GitLab MR in the UI.
- UI reflects real state.
- Human-required actions appear in Inbox.
- Artifacts are inspectable.
- Errors are actionable.

## Phase 8: API hardening

### Objective

Make Command Center a stable remote execution platform.

### Required API capabilities

| API area | Requirement |
|---|---|
| Execution create | Versioned schema with full option parity |
| Execution read | Status, options, phases, artifacts |
| Execution control | Cancel, retry, revise, approve |
| Event stream | WebSocket or SSE with robust auth |
| Artifact access | Logs, plans, diffs, comments, test results |
| Worktree CRUD | List, reset, delete |
| Settings/config | Read/update safe settings |
| Health/readiness | Service and worker health |
| Idempotency | Safe retries |

### Security requirements

- Strong auth outside loopback.
- Role model for view/start/force/admin.
- Audit log for sensitive actions.
- Secret redaction in logs/events/UI.
- Active rate limits.
- Safe CORS/CSRF behavior.
- Deployment boundary documented and tested.

## Phase 9: Reliability and operations

### Objective

Make remote execution trustworthy for real use.

### Work items

| Area | Work |
|---|---|
| Persistence | Evaluate SQLite vs Postgres for deployment target |
| Queueing | Add proper job semantics if multiple workers/runs are needed |
| Concurrency | Lock worktrees/repos/tickets |
| Cancellation | Graceful agent/subprocess cancellation |
| Timeouts | Phase-level and run-level limits |
| Recovery | Resume or fail clearly after worker crash |
| Observability | Structured logs, metrics, trace IDs |
| Backups | Persist execution artifacts safely |
| Cleanup | Scheduled cleanup of old worktrees/artifacts |
| Deployment | Harden Docker socket/DooD risks |

## Phase 10: Migration

### Objective

Move from CLI-first to Command-Center-first without breaking current usage.

### Stages

| Stage | Behavior |
|---|---|
| 1 | CLI remains default; Command Center beta available |
| 2 | CLI can delegate to Command Center with `--remote` |
| 3 | Remote becomes recommended for normal workflows |
| 4 | CLI becomes thin client/admin fallback |
| 5 | Deprecated local-only paths are removed or frozen |

## Now / Next / Later

### Now

- Remove scaffold success path.
- Wire worker to real CLI workflow.
- Add no-op detection.
- Normalize execution options.
- Add golden test for remote execution artifacts.
- Pull useful work from `v2/command-center-close-the-gap`.
- Emit real lifecycle events.

### Next

- Jira parity.
- GitLab/MR parity.
- Git/worktree lifecycle parity.
- Review/self-fix/debrief parity.
- Drupal agent parity.
- Remote flag parity.
- Dashboard run detail and actions.

### Later

- Role-based auth.
- Audit log.
- Queue/concurrency hardening.
- Crash recovery.
- Insights dashboard.
- Multi-worker support.
- Postgres option.
- Advanced approval workflows.

