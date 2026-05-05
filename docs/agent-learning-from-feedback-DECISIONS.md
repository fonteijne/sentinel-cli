# Decisions — Agent Learning from Feedback

**Companions:**
- Design: [`agent-learning-from-feedback-2026-05-03.md`](./agent-learning-from-feedback-2026-05-03.md)
- Handover: [`agent-learning-from-feedback-HANDOVER.md`](./agent-learning-from-feedback-HANDOVER.md)

Append-only log. Each entry resolves an open question from the handover §5 or a later design question. Do not mutate past entries; supersede with a new one if a decision changes.

---

## D1 — Loop A retry cap: global N=3 for Phase 1

**Date:** 2026-05-05
**Resolves:** Handover §5 Q1
**Status:** Accepted

**Context.** Design §5.1 Loop A specifies a hard cap of N=3 retries inside the developer Karpathy loop. Question was whether to allow a per-stack override (Drupal tests are slower and sometimes flaky on a recoverable first pass).

**Decision.** Ship Phase 1 with a single global `N=3`. Revisit after cap-hit telemetry is available.

**Revisit condition.** If ≥20% of Drupal executions cap out at N=3 and postmortems show the 4th attempt would have passed on a meaningful fraction, add a per-stack override. Metric lives in the Phase 1 exit telemetry (handover §7 — cap-hit rate).

**Implementation note.** Keep the cap as a single named constant, not scattered. A future per-stack dict is a one-line change if needed.

---

## D2 — FeedbackDistiller model: `claude-4-5-haiku`

**Date:** 2026-05-05
**Resolves:** Handover §5 Q2
**Status:** Accepted

**Context.** Distiller (Appendix C.2) is invoked per unresolved MR comment. Strict JSON output, temperature=0. Design suggested Haiku for cost.

**Decision.** Use `claude-4-5-haiku` for the distiller. No Sonnet fallback in Phase 2.

**Rationale.** Distillation is a classification + slot-filling task with a tightly bounded output schema — Haiku's known comfort zone. Cost matters because the distiller runs on every MR comment, not every execution. Sonnet fallback adds runtime complexity that we haven't justified with data.

**Revisit condition.** If distiller-produced `signature_slug` collisions or scope misclassifications are caught in `sentinel rules` audits at a rate > 5%, consider the Sonnet fallback.

**Implementation note.** Model string is stored in `feedback_observations.distiller_model` so re-distillation with a different model is a supported audit operation (Appendix C.8).

---

## D3 — Probation rules injection: inject with `[probation]` tag, flag-gated

**Date:** 2026-05-05
**Resolves:** Handover §5 Q3
**Status:** Accepted

**Context.** Rules at `status='probation'` have crossed ingest but not promotion thresholds (Appendix C.4 / C.6). Question: inject them into agent prompts as tentative hints, or hold back entirely until promoted.

**Decision.** Inject probation rules into the "Known pitfalls" section with an explicit `[probation]` tag. Gate behind a feature flag (working name `PROBATION_INJECTION=true`, default on) so we can disable without a code change if drift shows up.

**Rationale.** Maximizes learning speed — new rules start influencing behavior immediately. The tag tells the agent the rule is tentative (via a `shared/base_instructions.md` clause: "`[probation]`-tagged rules are hints, not policy — apply judgment"). The flag is the kill switch if injecting probation rules turns out to encode bad habits.

**Revisit condition.** If postmortems trace bad behavior back to a probation rule that was later revoked, flip the default off and require explicit opt-in per stack.

**Implementation notes.**
- The `shared/base_instructions.md` addition must be written as part of Phase 2 (when retrieval injection lands), not Phase 1.
- Retrieval query (§D.4 of the design doc) already includes `status IN ('active', 'probation')` — no schema change needed.
- Flag is read at prompt-build time in `prompt_loader.load()`, not cached into the execution snapshot, so toggling takes effect on the next execution.

---

## D4 — Widening PR location/approver: deferred to Phase 2

**Date:** 2026-05-05
**Resolves:** Handover §5 Q4
**Status:** Deferred

**Context.** When a `project:<KEY>` rule earns its way to `<stack>` scope (Appendix D.3), an overlay PR opens somewhere and someone approves it. Design proposed Sentinel repo + Sentinel maintainer.

**Decision.** Do not decide now. Phase 1 only writes project-scoped observations; no widening happens. Resolve this right before the overlay-PR proposer is built in Phase 2.

**Revisit trigger.** Starting work on Phase 2 task "Overlay PR proposer" (handover §8 / design §8 task 11). The `sentinel-distiller-expert` and `sentinel-cli-rules-expert` agents block on this decision.

**What to decide then.**
- Which repo the widening PR targets (Sentinel vs the originating project).
- Who approves (Sentinel maintainer, dual approval with the flagging reviewer, or the originating project's team).
- Whether widening ever happens automatically in CI or always requires human click.

---

## D5 — Overlay character cap: PR-review discipline, no CI check

**Date:** 2026-05-05
**Resolves:** Handover §5 Q5
**Status:** Accepted for Phase 1/2

**Context.** Design §9 flagged overlay bloat as a real risk (e.g. `drupal_plan_generator.md` drifting from 137 → 600+ lines). Question: committed CI check with a hard cap, a soft warning, or just reviewer discipline.

**Decision.** Phase 1 and Phase 2: PR-review discipline only. No CI job. The `sentinel-learning-reviewer` agent (handover §6) explicitly checks overlay deltas as part of its pre-merge invocation policy.

**Rationale.** Prompt budget is already enforced at prompt-build time (Appendix E.8 — `PromptBudgetExceeded` event with deterministic truncation). A CI cap on the source file would be a second, weaker enforcement layer that adds tuning friction. Reviewer eyes catch bloat earlier, with context about whether growth is earned.

**Revisit condition.** If overlay files exceed their §E.8 token allocation on ≥2 occasions and truncation starts dropping rules the team wanted kept, introduce a CI soft-warning job (not a hard fail).

**Implementation note.** Add overlay-size scrutiny to the reviewer agent's prompt when that agent is written.

---

## D6 — `project_sync_state` watermark: per Sentinel installation

**Date:** 2026-05-05
**Resolves:** Handover §5 Q6
**Status:** Accepted

**Context.** Phase 3 introduces pull-on-demand outcome ingestion (design §10, task 14) with a new `project_sync_state(project, last_synced_at, last_seen_mr_iid)` table to avoid re-paginating GitLab on every run. Question: one watermark per Sentinel installation (dev/staging/prod each track their own) or one watermark per GitLab repo shared across installations.

**Decision.** Per Sentinel installation. Each instance's SQLite DB holds its own watermark row per project.

**Rationale.** Shared watermark would require cross-instance coordination to advance safely; if two instances both ingest the same project, one would race past the other. Per-installation avoids the coordination problem entirely at the cost of a small amount of duplicated GitLab API traffic if multiple instances track the same project — which is not the common case.

**Revisit condition.** If multiple production Sentinel instances end up tracking overlapping project sets and GitLab rate-limit pressure becomes real, consider a shared watermark behind a coordination primitive (advisory lock or `UPDATE ... WHERE last_seen_mr_iid < :new_iid`).

**Implementation notes.**
- The `sentinel outcomes sync [--project X] [--since DATE] [--all]` CLI (design §10 task 14) is already in scope — fresh installations use `--all` or `--since` to backfill rather than inheriting another instance's watermark.
- Schema stays as specified in the design: no `installation_id` column. The DB file itself identifies the installation.

---

## D7 — On cap-out, the MR stays (or reverts to) draft; never un-draft incomplete work

**Date:** 2026-05-05
**Resolves:** Ambiguity in design §5.1 Loop E ("un-un-draft so humans see it" — double negative, unclear intent).
**Status:** Accepted

**Context.** When Loop A caps out at N=3 (D1), the implementation is known-broken. The original design-doc wording for the escalation step was ambiguous about the MR's draft status. One reading said "un-draft" (mark ready for review), the other said "re-draft" (revert to draft). Shipping the wrong reading would page reviewers on every cap-out and train them to ignore the signal.

**Decision.** On cap-out, the MR is in draft state when the escalation finishes. Concretely:
- If the MR is already a draft, leave it alone.
- If a prior successful phase un-drafted it, revert it to draft before posting the escalation comment.
- Never un-draft on cap-out, even partially, even as a "hint."

The escalation surface is the "Sentinel paused here" comment plus assignee notification — not MR visibility state.

**Rationale.** Reviewer attention is a finite resource and a recurring theme in the risk register (handover §10). Un-drafting known-broken work would signal "ready for review" when the code doesn't work. Over time this erodes trust in Sentinel's draft-status signal — reviewers would learn to distrust it and either check every draft (wasted effort) or ignore all un-drafts (missed completions). Neither is acceptable. Keeping draft state truthful — *un-drafted only when the work is ready* — is the only stable equilibrium.

**Revisit condition.** If a future workflow intentionally uses un-drafted-but-known-incomplete MRs as a coordination signal (e.g., "assign to reviewer for design feedback before implementation is done"), revisit. That's a different UX than "cap-out" and deserves its own ADR, not a carve-out here.

**Implementation notes.**
- `src/core/execution/post_execute.py` already has an un-draft path on the happy flow (handover §9 pointer). The escalation path adds a symmetric "ensure-draft" step guarded on cap-out.
- The `sentinel-verifier-loop-expert` agent's spec already says "emit, do NOT burn more tokens." The `DeveloperCappedOut` subscriber owns the draft-reassertion logic, not the loop itself.
- Tests (owned by `sentinel-test-harness-expert`): on cap-out fixture, assert the MR's draft status is `true` after escalation regardless of its state before.
