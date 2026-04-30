# Prioritized Backlog

## P0: Make Command Center execute real workflows

### Ticket 1: Replace scaffold execution with real workflow invocation

**Problem:** Command Center can complete a run without executing the CLI workflow.

**Scope:**

- Disable `_scaffold_run()` in production mode.
- Add worker path that invokes the shared execution workflow.
- Mark run failed if workflow invocation does not happen.
- Emit real lifecycle events.
- Persist basic artifacts.

**Acceptance criteria:**

- API/dashboard run invokes real workflow.
- No-op run cannot complete successfully.
- Regression test fails if worker completes without workflow invocation.

### Ticket 2: Extract shared workflow runner

**Problem:** CLI behavior must be callable by both CLI and service.

**Scope:**

- Create shared `WorkflowRunner` or equivalent application service.
- Move business behavior behind the runner.
- Keep Click commands as input parsing/presentation.
- Make worker call the same runner.

**Acceptance criteria:**

- Existing CLI commands still pass tests.
- Worker can invoke the same workflow.
- CLI and worker share option schema.

### Ticket 3: Normalize execution options

**Problem:** Remote execution drops important CLI options.

**Scope:**

- Define canonical execution option model.
- Map local CLI flags to the model.
- Map API/UI payloads to the model.
- Reject unsupported options instead of dropping them.

**Acceptance criteria:**

- `--force`, `--no-env`, `--max-iterations`, and `--prompt` are supported or explicitly rejected.
- `sentinel execute --remote` preserves supported local flags.
- API schema is versioned.

### Ticket 4: Add no-op detection

**Problem:** Command Center can report success without useful artifacts.

**Scope:**

- Track required artifact markers.
- Fail runs with no workflow invocation.
- Fail or warn when no worktree/agent/MR/external action is produced.

**Acceptance criteria:**

- Run with no real workflow cannot complete green.
- Failure reason is visible in API and dashboard.

## P0: Restore external side effects

### Ticket 5: Jira parity

**Problem:** Remote runs do not reproduce CLI Jira behavior.

**Scope:**

- Fetch ticket context.
- Include attachments.
- Post configured comments.
- Support status transitions.
- Surface self-hosted Jira and VPN diagnostics.
- Add idempotency keys for comments.

**Acceptance criteria:**

- Remote run fetches Jira ticket.
- Remote run posts expected Jira comments.
- Attachment ticket fixture passes.
- Failure path includes actionable Jira diagnostics.

### Ticket 6: Git/GitLab parity

**Problem:** Remote runs do not create branches, MRs, or MR comments.

**Scope:**

- Create isolated worktree.
- Create branch.
- Commit/push according to CLI behavior.
- Create/update GitLab MR.
- Post decision log and findings.
- Support SSH transport.

**Acceptance criteria:**

- Remote happy path creates branch/worktree.
- Remote happy path creates or updates MR.
- MR contains decision log.
- GitLab comments are posted.

## P0: Prove parity

### Ticket 7: Golden parity test harness

**Problem:** There is no durable proof that remote behavior equals CLI behavior.

**Scope:**

- Add fixtures for generic, Drupal, failure, follow-up, attachment, self-hosted Jira, and SSH workflows.
- Run each fixture locally and remotely.
- Compare semantic artifacts.

**Acceptance criteria:**

- Test fails if remote does not fetch Jira ticket.
- Test fails if remote does not create branch/MR when CLI does.
- Test fails if remote drops supported options.
- Test fails if no-op success occurs.

## P1: Complete workflow parity

### Ticket 8: Agent lifecycle events

**Scope:**

- Emit `agent.started`.
- Emit `agent.finished`.
- Emit `test.result`.
- Emit `finding.posted`.
- Emit `confidence.scored`.
- Emit `guardrail.triggered`.

**Acceptance criteria:**

- Dashboard timeline shows real workflow events.
- Events are persisted and streamable.

### Ticket 9: Review and self-fix parity

**Scope:**

- Run reviewer agent remotely.
- Trigger self-fix loop when configured.
- Persist findings and actions.
- Post relevant MR/Jira comments.

**Acceptance criteria:**

- Review-heavy fixture passes.
- Self-fix iterations are visible in timeline.

### Ticket 10: Debrief and revision workflow

**Scope:**

- Implement debrief API.
- Implement revision request endpoint.
- Continue a run from previous context.
- Persist debrief turns.

**Acceptance criteria:**

- Operator can request revision from UI/API.
- Revision continues from prior artifacts.
- Debrief turns are persisted and visible.

### Ticket 11: Drupal profile parity

**Scope:**

- Add remote profile selection.
- Invoke Drupal developer agent.
- Invoke Drupal reviewer agent.
- Add Drupal golden fixture.

**Acceptance criteria:**

- Drupal implementation fixture passes remotely.
- Drupal reviewer fixture passes remotely.

## P1: Dashboard replacement UX

### Ticket 12: Run detail page

**Scope:**

- Show timeline.
- Show logs.
- Show plan.
- Show artifacts.
- Show Jira/GitLab links.
- Show resolved options.

**Acceptance criteria:**

- Operator can understand run state without terminal.

### Ticket 13: New execution form

**Scope:**

- Ticket input.
- Repo input.
- Profile selection.
- Prompt field.
- Advanced flags.
- Dry-run/force controls.

**Acceptance criteria:**

- Operator can start normal workflows from UI.
- Risky options require confirmation.

### Ticket 14: Inbox

**Scope:**

- Show approvals.
- Show debrief prompts.
- Show failed runs requiring action.
- Show revision requests.

**Acceptance criteria:**

- Human-in-the-loop actions are visible and actionable.

### Ticket 15: Worktree management

**Scope:**

- List worktrees.
- Inspect worktree metadata.
- Reset worktree.
- Delete worktree.
- Audit destructive actions.

**Acceptance criteria:**

- Reset/delete work through backend, not only dialogs.

## P1: Security and operations

### Ticket 16: Robust event auth

**Scope:**

- Replace query-token-only behavior outside loopback.
- Support secure WebSocket/SSE auth.
- Add tests for non-loopback usage.

**Acceptance criteria:**

- Remote deployment can stream events securely.

### Ticket 17: Authorization and audit

**Scope:**

- Add role model.
- Protect force/delete/reset/admin actions.
- Record audit events.

**Acceptance criteria:**

- Sensitive actions require permission.
- Audit log shows who did what.

### Ticket 18: Worker reliability

**Scope:**

- Add cancellation.
- Add timeouts.
- Add crash recovery behavior.
- Add concurrency locks.

**Acceptance criteria:**

- Worker crash leaves run in recoverable or clear failed state.
- Concurrent runs do not corrupt shared repos/worktrees.

## P2: Operational maturity

### Ticket 19: Queue and persistence hardening

**Scope:**

- Evaluate SQLite limits.
- Add queue semantics if needed.
- Consider Postgres option for multi-worker usage.

**Acceptance criteria:**

- Deployment target has documented persistence and queueing model.

### Ticket 20: Insights dashboard

**Scope:**

- Success rate.
- Failure categories.
- Average duration.
- Review findings.
- Agent iteration counts.

**Acceptance criteria:**

- Insights are based on real persisted runs.

### Ticket 21: Deployment runbook

**Scope:**

- Install/start/upgrade.
- Backup/restore.
- Troubleshooting.
- Security model.
- Docker socket/DooD risks.

**Acceptance criteria:**

- Operator can deploy and recover Command Center using documented steps.

## Recommended first sprint

| Order | Ticket | Why |
|---|---|---|
| 1 | Replace scaffold execution | Unlocks all real behavior |
| 2 | Extract shared workflow runner | Prevents duplicate workflow implementation |
| 3 | Normalize execution options | Prevents remote/local divergence |
| 4 | Add no-op detection | Prevents false green runs |
| 5 | Golden parity test harness | Protects the replacement goal |

## Recommended first ticket text

### Title

Replace Command Center scaffold execution with real CLI workflow invocation

### Description

Command Center currently creates execution rows and can complete runs without invoking the real Sentinel CLI workflow. This prevents Jira comments, GitLab merge request creation, MR decision logs, worktree creation, review loops, debrief, and Drupal-specific agents from working remotely.

Replace the scaffold execution path with a worker path that invokes the same workflow behavior used by `sentinel execute`.

### Acceptance criteria

```text
Given a valid Jira ticket and repo
When I start a run from Command Center
Then Sentinel fetches the Jira ticket
And creates or uses a worktree
And runs the developer/reviewer workflow
And opens or updates a GitLab MR
And posts the expected Jira/GitLab comments
And streams real phase events to the dashboard
And marks the run completed only after real workflow completion
```

### Regression criteria

```text
Given a Command Center execution
When no real workflow is invoked
Then the run must fail
And the run must not be marked completed
And the dashboard must show the failure reason
```

