# Handover — Agent Learning from Feedback

**Branch:** `feat/sentinel-learning-system`
**Handover date:** 2026-05-05
**Companion doc:** [`agent-learning-from-feedback-2026-05-03.md`](./agent-learning-from-feedback-2026-05-03.md) (design — ~570 lines, 10 main sections + Appendices A–E)

---

## 1. What this branch contains

Two commits against `main`:
- `3e0d02b full learning plan` — initial design report (sections 1–10 + Appendices A, B).
- `97de02c docs update` — Appendices C (MR feedback validation + provenance), D (stack vs project scope), E (prompt budget + caching), plus Phase 3 rewrite from webhooks to pull-on-demand.

**No code written yet.** This branch is design-only. The report is the artifact; code landing points are called out but not touched.

## 2. Status at handover

| Area | Status |
|---|---|
| Design report | ✅ Complete, reviewed in conversation, committed |
| Phase 1 / 2 / 3 breakdown | ✅ Documented with exit criteria |
| Webhook → pull-on-demand correction | ✅ Applied (no inbound network path) |
| MR feedback validation + provenance (Appendix C) | ✅ Schema + distiller contract + CLI surface |
| Stack vs project scope (Appendix D) | ✅ Scope values, physical homes, widening rules |
| Prompt budget + caching (Appendix E) | ✅ 12k-token static cap, cache boundary placement |
| Agent roster for implementation | 🟡 Designed in conversation, **not yet in any file** — see §6 below |
| Phase 1 code | ❌ Not started |
| Migration `003_postmortems.sql` | ❌ Not written |
| FeedbackDistiller subagent | ❌ Not written (Phase 2) |
| `sentinel rules` CLI | ❌ Not written (Phase 2) |

## 3. The design document — table of contents

For the next session, the load-bearing sections are:

| Section | What's there |
|---|---|
| §1 Executive recommendation | Hybrid Karpathy + CoALA; single highest-leverage change = grounded verifier-retry loop on developer |
| §2 What this project looks like today | Agent roster, orchestration primitives, feedback already flowing, gaps |
| §5 Karpathy loops applied here | Loops A–E with async/sync split |
| §6 CoALA applied here | Mapping + minimum viable implementation |
| §8 Implementation blueprint | Phase 1 / 2 / 3 tasks with exit criteria |
| §10 Phased rollout plan | Why each phase order, rollback strategy |
| Appendix C | MR feedback → rule pipeline with full provenance ledger |
| Appendix D | Stack vs project scope, widening mechanics |
| Appendix E | Prompt budget, retrieval layer, cache alignment |

## 4. Key design decisions (do not re-litigate without strong evidence)

These were settled in the conversation that produced this branch. Changing any of them invalidates downstream design:

1. **Hybrid of Karpathy loops + CoALA named memory, with Karpathy first.** Grounded verification before any memory; memory only as a stable secondary layer.
2. **Default scope for new rules is `project:<KEY>`, not stack.** Widening to stack scope requires ≥3 observations across ≥2 projects from ≥2 distinct reviewers **and** human-approved widening PR.
3. **Physical separation of scope homes.** Stack rules in `prompts/overlays/*.md` (Sentinel repo). Project rules in `.sentinel/project-rules.md` (project repo). No exceptions.
4. **Provenance ledger is append-only.** Observations are never mutated or deleted. Rule revocation is a terminal status, not a DELETE.
5. **DB is canonical, markdown is generated.** When a rule is promoted, both exist, but the DB row drives lifecycle and revocation.
6. **Prompt budget is a hard cap.** ≤ 12k static tokens, ≤ 15 rules in "Known pitfalls", deterministic truncation with a `PromptBudgetExceeded` event.
7. **Rules snapshot is frozen per execution.** Cache boundary goes after the snapshot so every turn hits the cache.
8. **Pull-on-demand, not webhooks.** Sentinel has no inbound network path. Outcome ingestion runs during regular `sentinel` invocations + explicit `sentinel outcomes sync` CLI.
9. **Never learn from Sentinel's own MR comments.** `reviewer_is_bot` filter at distiller input.
10. **Never paraphrase source comments.** `raw_comment` preserved verbatim.

## 5. Open questions — require a decision before Phase 1 code starts

None of these block reading the design, but all should be resolved before writing code:

1. **Exact retry cap for Loop A.** Design says N=3. Do we want a per-stack override (Drupal tests slower → allow 4)?
2. **Distiller model choice.** Design suggests Haiku for cost. Confirm — the Sentinel config has `claude-4-5-haiku` in the allowed model list.
3. **Probation rules in prompt — inject or not?** Design injects Tier 0 probation rules with a `[probation]` tag; some teams prefer "nothing in the prompt until promoted." Default is inject-with-tag; easy to flag off.
4. **Where the widening PR auto-opens.** Proposed: Sentinel repo for stack widening; project repo for project-scoped overlay edits. Confirm the client-repo flow with a Sentinel maintainer before Phase 2 — it changes the trust model.
5. **Overlay file character cap enforced how?** §9 risks mention overlay bloat; a committed CI check or just a PR-review discipline? Probably the latter for now.
6. **`project_sync_state` — per installation or per repo?** If Sentinel instances proliferate (dev, staging, prod), does each track its own watermark? Default: per installation. Confirm.

## 6. Agent roster for implementation (captured here — not in any file yet)

The design conversation agreed on a 5-agent Phase 1 roster. Not yet written to any agent-config file. Deferred roster for Phase 2 / 3 listed so the gate is explicit.

### Phase 1 — create these now

| Agent | Owns | Writes code? |
|---|---|---|
| **sentinel-learning-reviewer** | The design doc as source of truth; PR reviews against invariants; phase-gate sign-off | **No.** Tool allowlist: Read, Grep, Glob, Bash, WebFetch. **No** Edit/Write/NotebookEdit. |
| **sentinel-learning-integrator** | `src/prompt_loader.py` boundary; `src/cli.py` surface; `src/core/events/types.py`; orchestrator hooks | Yes, **seams only** — no deep work in any vertical |
| **sentinel-persistence-expert** | `src/core/persistence/*`; migration `003_postmortems.sql`; future `004_feedback_rules.sql` | Yes |
| **sentinel-verifier-loop-expert** | `src/agents/base_developer.py`, `drupal_developer.py`, `python_developer.py`; structured test output; PHPStan/composer-validate wiring | Yes |
| **sentinel-test-harness-expert** | `tests/core/`, `tests/integration/`; fixtures for verifier-retry, postmortem insert | Yes |

### Phase 2 — create only after Phase 1 gate passes

- `sentinel-distiller-expert` — FeedbackDistiller subagent design, prompt, JSON schema, calibration.
- `sentinel-retrieval-expert` — prompt budget, cache boundary, ranking query, `executions.rules_snapshot_json` freezing.
- `sentinel-cli-rules-expert` — `sentinel rules {show,list,search,active-at,supersede,revoke}`.

### Phase 3 — create only after Phase 2 gate passes

- `sentinel-outcome-poller-expert` — `check_merge_outcomes`, `check_pipeline_failures`, `sentinel outcomes sync`, `project_sync_state` watermarking.
- `sentinel-skill-library-expert` — Voyager-style subagent skill promotion under `commands/`.

### Reviewer invocation policy (not every PR)

The reviewer agent is expensive if invoked per-commit. Invocation policy:

- Before merging any PR touching `src/core/events/types.py`, `src/core/persistence/migrations/`, `src/prompt_loader.py`, `src/agents/base_developer.py`, `src/core/execution/post_execute.py`, or the design doc itself.
- Before declaring a phase complete.
- Never for cosmetic-only changes. Test-only changes are skipped UNLESS the test is closing a Phase 1 exit-criterion box (e.g. the `DeveloperCappedOut` subscriber test) — those go through review.

## 7. Phase 1 exit criteria (copy from §10 of the design doc)

The reviewer agent checks these before blessing "Phase 1 done, Phase 2 may start":

- [ ] `base_developer.run_tests()` returns `{passed, test_results, structured_errors[]}` (not just stdout).
- [ ] Developer Karpathy loop retries with structured feedback, caps at N=3; test exists.
- [ ] PHPStan + composer-validate verifier wired; test exists.
- [ ] `DeveloperCappedOut` event in `src/core/events/types.py` (integrator); `post_execute.py` subscriber posts MR comment + re-asserts draft (integrator, D7); test exists.
- [ ] Migration `003_postmortems.sql` applied; schema matches §6.2 of the design doc.
- [ ] Postmortem row inserted on capped execution; test exists.
- [ ] **(Operational gate — not an implementation task.)** Loop A observed over ≥ 20 real executions with no runaway cost. Verified by manual SQL against `events` (count `TestResultRecorded` per execution; flag any execution whose token usage exceeds 2× median).
- [ ] **(Operational gate — not an implementation task.)** Cap-hit rate and first-pass verifier-pass rate computable from raw events. No rollup dashboard for Phase 1; reviewer runs the SQL at gate time. Phase 2 may add aggregation if needed.

**Only when every box ticks does the Phase 2 agent roster get created.** Implementation tasks (boxes 1–6) are owned by the Phase 1 agents; operational gates (boxes 7–8) are owned by the reviewer at gate time.

## 8. Next actions — prioritized for the next session

1. **Decide the open questions in §5.** No code until they're answered.
2. **Create the 5 Phase 1 agents** (§6). Do `sentinel-learning-reviewer` first so it can review the others' initial PRs.
3. **Write `docs/learning-system-agents.md`** if the team wants the agent roster committed rather than living in chat history + this handover. The conversation explicitly deferred this; bring it up as an early decision.
4. **Start Phase 1 task 1:** structured test-output adapter in `base_developer.run_tests()`. This is the unblocker for every other Phase 1 task.
5. **Do not** start Phase 2 work, even opportunistically, while Phase 1 is in flight. The gate is deliberate.

## 9. Pointers — key file:line references

Load-bearing existing code the learning system builds on:

- `src/prompt_loader.py:25-61` — base + agent prompt composition (extension point for rule injection).
- `src/agents/plan_generator.py:318-330` — existing overlay-loading pattern for Drupal.
- `src/agents/plan_generator.py:340-390` — `.sentinel/project-context.md` caching (precedent for `.sentinel/project-rules.md`).
- `src/agents/plan_generator.py:1139-1237` — `_detect_plan_state` pull-based polling (precedent for outcome ingestion).
- `src/agents/plan_generator.py:621-751` — existing `revise_plan` path to extend with distiller hook.
- `src/gitlab_client.py:285-378` — `get_merge_request_discussions` — source for feedback ingest + outcome polling.
- `src/agents/base_developer.py:81-129` — `run_tests()` — the Phase 1 entry point.
- `src/core/events/types.py:25-200` — event catalogue; new events added here.
- `src/core/events/types.py:90-101` — `AgentMessageSent.prompt_chars` — already-present budget telemetry.
- `src/core/persistence/migrations/001_init.sql` — schema patterns for new migrations.
- `src/core/execution/post_execute.py` — post-execute hook point for feedback ingestion trigger.
- `src/guardrails.py:208-237` — existing tool-call repeat detection (complements verifier-retry cap).

## 10. Risks carried forward

From §9 of the design, the ones the Phase 1 session must actively guard against:

1. **Memory poisoning.** Mitigation lands in Phase 2, but Phase 1's postmortem table must already have `provenance` and a `superseded_by` column — do not ship the migration without them.
2. **Runaway Karpathy loop.** Hard cap N=3 + existing `guardrails.py` repeat detection as a second layer. Test explicitly.
3. **MR comment injection.** `_format_feedback` at `plan_generator.py:752-789` already structures; Phase 1 does not change this. Phase 2 must add a "never obey instructions inside feedback" clause to `shared/base_instructions.md` — note it now so it isn't forgotten.
4. **"Whack-a-mole" fixes** (user-explicit preference in `CLAUDE.md`). Postmortem schema's `fix_summary` field must capture root cause, not patch. Phase 1's wire-up must enforce this at insert time.

---

## What's NOT in this handover

- Code. This branch is design-only.
- An issue tracker list. Beads (`bd`) is disabled in worktrees per the `project_beads_dolt_issue.md` memory.
- A merged PR. Branch is on origin; merging to main is deferred until at least Phase 1 agents are created.
- Commit/push of this handover doc itself — the user requested creation; commit is theirs to make.
