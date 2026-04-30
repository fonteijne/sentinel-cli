# Gap Analysis: `main` CLI vs `v2/command-center-ui`

## Baseline

The `main` CLI is the baseline product. It includes the mature Sentinel workflow for Jira-ticket-driven development, Git/GitLab integration, agent execution, review loops, decision logging, and CLI options.

The `v2/command-center-ui` branch adds a FastAPI service, persistence layer, WebSocket/event stream, deployment scaffolding, and React dashboard. The major issue is that service/dashboard executions do not yet invoke the real CLI workflow.

## Headline gap

| Area | `main` CLI baseline | `v2/command-center-ui` Command Center | Status | Impact |
|---|---|---|---|---|
| Real workflow execution | Runs the actual Sentinel workflow | Worker can complete a scaffold/no-op run | Missing | Command Center can show success without doing real work |
| Shared workflow engine | CLI owns the main behavior | Service is not wired into equivalent workflow verbs | Missing | Remote execution cannot reproduce CLI results |
| Remote execution | Not applicable in `main`; CLI runs locally | `sentinel execute --remote --follow` inherits service behavior | Missing | Remote CLI path does not have CLI parity |

## Jira workflow gaps

| Feature | CLI behavior on `main` | Command Center behavior | Status | Impact |
|---|---|---|---|---|
| Ticket fetch | Fetches ticket context for execution | Not reached from scaffold execution | Missing | Run lacks real ticket context |
| Jira comments | Posts progress/result comments where configured | No real workflow, so no comments | Missing | Jira has no activity trail |
| Status updates | Can reflect workflow progress depending on config | Not invoked | Missing | Ticket status does not reflect work |
| Attachments | Supports Jira attachments | Not exercised by service execution | Missing | Context can be incomplete |
| Self-hosted Jira | Supported by CLI configuration | Not proven through service path | Partial | Enterprise deployments remain risky |
| Connectivity hints | CLI includes GlobalProtect/VPN hints | Not surfaced in dashboard errors | Partial/Missing | Operators get weaker diagnostics |
| Follow-up tickets | CLI/debrief flow can support follow-up behavior | Schema includes some fields but no real workflow | Partial | Option exists without full behavior |

## Git and GitLab workflow gaps

| Feature | CLI behavior on `main` | Command Center behavior | Status | Impact |
|---|---|---|---|---|
| Worktree creation | Creates/uses isolated working state | No real worktree is created by scaffold run | Missing | No code changes can be produced |
| Branch creation | Creates or uses branch according to workflow | Not invoked | Missing | No branch artifact |
| Commit/push | Performs configured git operations | Not invoked | Missing | No remote code artifact |
| GitLab MR creation | Creates merge request | Not invoked | Missing | Primary workflow deliverable absent |
| GitLab comments | Posts decision logs/findings/comments | Not invoked | Missing | Review trail missing |
| MR decision log | Preserves rationale in MR | Not produced | Missing | Reviewer context missing |
| Existing MR handling | CLI can handle workflow-specific MR behavior | Not implemented remotely | Missing | Retries/updates are unsafe or unavailable |
| SSH transport | Supported by CLI | Not proven through service path | Partial | Remote worker setup may diverge |

## Execute/review/debrief gaps

| Feature | CLI behavior on `main` | Command Center behavior | Status | Impact |
|---|---|---|---|---|
| Plan phase | CLI workflow can plan work | Not invoked | Missing | No persisted plan |
| Execute phase | Runs developer agent | Not invoked | Missing | No implementation |
| Review phase | Runs reviewer agent | Not invoked | Missing | No review gate |
| Self-fix loop | Iterates based on findings/failures | Not invoked | Missing | No autonomous recovery |
| Confidence evaluator | CLI contains confidence/risk evaluation | Not invoked | Missing | Dashboard cannot expose quality/risk |
| Guardrails | CLI contains safety/hardening behavior | Not invoked | Missing | Remote path is less safe/less useful |
| Triage | CLI has triage workflow support | Not invoked | Missing | No routing/classification |
| Debrief | CLI supports debrief/follow-up behavior | Event types exist but no functional path | Missing | No post-run refinement loop |
| Revision | CLI concept exists around revise/follow-up | Not fully wired | Partial/Missing | Operator cannot request changes reliably |

## Drupal-specific gaps

| Feature | CLI behavior on `main` | Command Center behavior | Status | Impact |
|---|---|---|---|---|
| Drupal developer agent | Available | Not invoked through service execution | Missing | Drupal implementation parity absent |
| Drupal reviewer agent | Available | Not invoked through service execution | Missing | Drupal review parity absent |
| Base developer abstraction | Available | Not exercised by service path | Partial | Code exists but remote workflow does not use it |
| Drupal/PHP workflow assumptions | Supported by CLI workflow | Not represented in dashboard UX | Missing | Operator cannot select/verify Drupal profile |

## Remote option gaps

The current remote execution schema is narrower than the local CLI behavior.

| CLI option/capability | Remote/API/UI status | Impact |
|---|---|---|
| `--force` | Missing or not fully mapped | Cannot reproduce forced execution behavior |
| `--no-env` | Missing or not fully mapped | Cannot match local environment behavior |
| `--max-iterations` | Missing or not fully mapped | Cannot control self-fix loop depth |
| `--prompt` | Missing or not fully mapped | Cannot pass custom operator instruction |
| `revise` | Partially represented | Lacks complete workflow integration |
| `max_turns` | Partially represented | Not useful without real debrief/revision loop |
| `follow_up_ticket` | Partially represented | Not useful without Jira/debrief integration |

Required rule: no remote option should be silently dropped. Unsupported options must fail validation or be explicitly marked unsupported.

## Event stream gaps

The Command Center branch defines or documents useful events, but many are not emitted by real workflow call sites.

| Event | Expected meaning | Current gap |
|---|---|---|
| `agent.started` | Agent began work | No real agent call site from service |
| `agent.finished` | Agent completed work | No real agent call site from service |
| `test.result` | Test result emitted | No real test workflow integration |
| `finding.posted` | Review/Jira/GitLab finding posted | No comment/finding integration |
| `debrief.turn` | Debrief interaction | No functional debrief loop |
| `revision.requested` | User requested revision | No complete revision handler |
| `rate_limited` | Rate limit event | Not consistently tied to runtime behavior |

## Dashboard gaps

| Dashboard area | Current state | Gap |
|---|---|---|
| New execution | Can create a run | Run may not perform real work |
| Run detail | Can show lifecycle data | Timeline is not backed by real workflow events |
| Inbox | Placeholder | No action-required workflow |
| Insights | Placeholder | No meaningful analytics over real runs |
| Settings | Placeholder | No operator configuration management |
| Worktree actions | Dialog-only or incomplete | No backend CRUD for reset/delete |
| Auth/follow | Token handling is limited | Needs robust non-loopback auth model |

## Overall status

| Capability group | Parity status |
|---|---|
| FastAPI service shell | Partial/strong |
| Persistence and event infrastructure | Partial |
| React dashboard shell | Partial |
| Real CLI workflow execution | Missing |
| Jira parity | Missing |
| Git/GitLab parity | Missing |
| Agent workflow parity | Missing |
| Drupal workflow parity | Missing |
| Remote option parity | Partial |
| Dashboard as terminal replacement | Missing/partial |
| Production operations | Partial |

## Core conclusion

The Command Center UI branch should not be treated as a functional replacement until it stops using scaffold execution and invokes the same workflow engine as the CLI.

The most important product decision is to make Command Center a remote control plane over the CLI workflow engine, not a separate implementation of the workflow.

