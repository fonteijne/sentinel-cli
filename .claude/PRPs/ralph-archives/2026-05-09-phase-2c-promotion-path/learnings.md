# Implementation Report — Phase 2C "Promotion Path"

**Plan**: `.claude/PRPs/plans/phase-2c-promotion-path.plan.md`
**Completed**: 2026-05-09
**Iterations**: 1 (large plan, executed in waves of parallel subagents)
**Reviewer verdict**: APPROVE (sentinel-learning-reviewer)

## Summary

Closed the learning loop. Phase 1 writes postmortems on cap-out; Phase 2A injects them into the planner prompt; Phase 2B closes reactive loops. **Phase 2C is the pipeline that grows memory on its own** — a heuristic extraction job clusters recurring postmortems by `failure_signature`, increments confidence as evidence accumulates across executions and projects, and (when confidence ≥ 80 + ≥ 3 observations + ≥ 2 distinct projects) opens a draft GitLab MR against the **Sentinel repo** that proposes adding the rule to `prompts/overlays/drupal_*.md`. A human Sentinel maintainer is the gate. Revocation is append-only via `superseded_by`.

Both jobs are flag-gated (`EXTRACTION_ENABLED=0`, `OVERLAY_PROPOSER_ENABLED=0`) and ship disabled until the operator opts in. D4 (PR location/approver) was resolved as part of this phase.

## Tasks Completed (all 18)

1. ✅ Resolved D4 in `docs/agent-learning-from-feedback-DECISIONS.md` (Sentinel repo target, maintainer approver, always-draft, never-auto-merge).
2. ✅ Migration `004_feedback_rules.sql` — full schema with partial unique `idx_feedback_rules_dedup` and secondary `idx_feedback_rules_status`.
3. ✅ `tests/core/test_feedback_rules_schema.py` (5 tests).
4. ✅ `src/core/persistence/feedback_rules.py` — 7 helpers (`upsert_rule`, `query_promotable`, `list_rules`, `mark_proposed`, `mark_promoted`, `revoke_rule`, `mark_superseded`). Append-only — no `update_rule` / `delete_rule` exports.
5. ✅ `tests/core/test_feedback_rules_helpers.py` (15 tests).
6. ✅ `query_postmortem_clusters` added to `src/core/persistence/postmortems.py`.
7. ✅ `tests/core/test_postmortem_clusters.py` (5 tests).
8. ✅ Re-exports in `src/core/persistence/__init__.py`.
9. ✅ Three new events (`FeedbackRuleExtracted`, `FeedbackRulePromoted`, `FeedbackRuleRevoked`).
10. ✅ `src/core/learning/extract.py` — `extract_clusters`, `compute_confidence`, `is_pure_symptom`, `ExtractionResult`, `ExtractionSummary`. Confidence clamped [0, 95].
11. ✅ `tests/core/test_extract.py` (10 tests).
12. ✅ `src/core/learning/propose_overlay.py` — `propose_overlays`, `_render_rule_bullet`, `_apply_overlay_edit`, `push_overlay_branch`. **`draft=True` hard-coded**.
13. ✅ `tests/core/test_propose_overlay.py` (10 tests).
14. ✅ `sentinel learning` CLI group with `extract`, `propose`, `mark-merged`, `revoke`, `list`. Feature-flag gating; `--dry-run` on extract+propose; synthetic `executions` row seeded before any bus publish.
15. ✅ `tests/test_cli_learning.py` (14 tests).
16. ✅ Exit-criterion integration test `tests/integration/test_phase2c_promotion.py` (1 test, full pipeline end-to-end).
17. ✅ Supersede-chain integration test `tests/integration/test_phase2c_supersede_chain.py` (2 tests).
18. ✅ `get_sentinel_repo_project_path()` accessor on config_loader; `config/config.yaml` updated with commented example.

## Validation Results

| Check | Result |
|-------|--------|
| Phase 2C unit tests | **62 passed** |
| Phase 2C integration tests | **3 passed** |
| Append-only export contract | **OK** (no `update_rule` / `delete_rule`) |
| Database validation | **OK** (table + both indexes present, idempotent) |
| ruff (Phase 2C files) | **clean** |
| mypy (new modules) | **clean** |
| Full suite | 896 passed, 17 pre-existing failures (16 baseline + 1 latent bug in `src/agents/plan_generator.py:1674` — `evaluation["passed"]` KeyError; not Phase 2C-related, plan_generator.py untouched by this work) |

**Phase 2A loader** (`src/prompt_loader.py`) is unchanged — Phase 2C does NOT wire `feedback_rules` into the loader. Promoted rules reach the planner via the overlay-file edit (existing overlay-loading path).

## Codebase Patterns Discovered (kept narrow — most reused existing patterns)

- **`execution_id: str | None = None` parameter contract.** When a learning module needs to publish events with synthetic `execution_id`s, the cleanest seam is: module accepts an optional id param; CLI generates the id, seeds the `executions` row to satisfy bus FK, then passes the id to the module. Unit tests don't pass it (preserves test ergonomics). Used by `extract_clusters` and `propose_overlays`.
- **Partial unique index on `(scope, agent_target, signature) WHERE status IN ('probation', 'active')`** is the right shape for an append-only "canonical rule" table that supports `superseded_by` chains. Full unique would break the chain; non-unique would allow live duplicates. Reusable for any future canonical-rule table.
- **`BEGIN IMMEDIATE / COMMIT` for verify-then-update writes.** `mark_promoted` and `mark_superseded` both follow this pattern — the SELECT and the UPDATE happen in one transaction so a parallel `revoke_rule` cannot race the status check. The persistence-expert agent applied this consistently.
- **Test stubs over `MagicMock`.** A 3-line `_FakeEventBus` class with a `published: list` attribute produces clearer test failures than `MagicMock(spec=['publish'])`. Used in extract + propose tests.

## Architectural Decisions Made During Implementation

- **Single commit per proposer run (all overlay files staged together)** rather than one commit per agent_target. Plan recommended this; confirmed cleaner.
- **`ProposalResult.overlay_path` field added** beyond the plan's spec, because a single proposer run can land bullets across multiple agent_targets — per-rule overlay paths must be reportable.
- **Push neutralization in tests** uses a stubbed `push_overlay_branch` that does `git add` + `git commit` (without push) rather than a tmp bare-repo origin. Simpler fixture; commits land in the local repo so the test can read back the provenance trailer.
- **Revoking a superseded row is allowed** (helper accepts it). The supersede-chain integration test documents this contract explicitly. Re-revoking a `revoked` row is rejected with `ValueError`.
- **Local-branch cleanup on proposer real-run failure is NOT performed**. Operator may want to inspect the partial state. Phase 2C-acceptable; flagged for revisit if proposer is re-run frequently in production.

## Deviations from Plan

- `ProposalResult` carries `overlay_path` (plan listed only `rule_id`, `branch_name`, `mr_url`, `dry_run`).
- `extract_clusters` and `propose_overlays` accept `execution_id: str | None = None` — not in original plan signature, added to coordinate synthetic-id seeding with the bus FK to `executions`.
- `_FakeEventBus` Protocol used instead of typing `event_bus: EventBus | None` directly, so unit tests can pass duck-typed stubs without the heavy import.

## Reviewer Sign-Off Statements

- D4 resolution honors the resolution: **YES**
- Append-only invariant on `feedback_rules` (no UPDATE/DELETE; supersede preserves history): **YES**
- Whack-a-mole guardrail real (not symbolic): **YES**
- Phase 2A loader unchanged: **YES**
- Synthetic execution_id seeding satisfies bus FK: **YES**

Verdict: **APPROVE**.

## Handoff Notes for Next Session

- `git push` is the user's responsibility per `CLAUDE.md` (Claude Code sandbox has no SSH keys or git push capability).
- Working tree includes pre-existing modifications to 18 files unrelated to Phase 2C (plan_generator.py, base_agent.py, gitlab_client.py, etc.). Those are from prior sessions on this branch (`feat/sentinel-learning-system`); they are not introduced by Phase 2C.
- The 17th full-suite failure (`test_run_update_with_investigation`) is a latent bug in pre-existing `plan_generator.py:1674` (`evaluation["passed"]` KeyError) — separate ticket if not already filed.
- Future Phase 2D work: wire `feedback_rules` into `prompt_loader.py` so `[probation]`-tagged rules reach the planner before promotion (deliberately deferred; design rationale in plan §"Why we skip the `feedback_rules → prompt_loader` wiring in 2C").
- Future improvement: per-second branch naming (currently per-minute) so two rapid proposer runs don't collide. Or: detect existing branch and append a counter.
