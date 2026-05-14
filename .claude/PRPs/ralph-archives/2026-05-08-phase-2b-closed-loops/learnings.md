# Implementation Report: Phase 2B — Closed Loops

**Plan**: `.claude/PRPs/plans/phase-2b-closed-loops.plan.md`
**Completed**: 2026-05-08
**Iterations**: 1 (orchestrator-driven; 5 specialist sub-agent waves)
**Reviewer**: `sentinel-learning-reviewer` — APPROVED (17 binding checks, 0 blockers)

## Summary

Phase 2B closes two reactive feedback paths in Sentinel:

1. **Loop C** — Reviewer→Planner escalation. When the execute-stage review-revise loop exhausts with persisting blockers, a new `ReviewerHandoffTriggered` event is published; a new subscriber in `post_execute.py` writes `executions.phase='replan_needed'`, idempotently reverts the MR to draft (D7), and posts exactly one DECISIONS §168-compliant MR comment (D8).
2. **Confidence-miss auto-investigation** — When `ConfidenceEvaluatorAgent` returns below threshold with non-empty `questions[]`, `PlanGeneratorAgent.run()` now invokes a new `_investigate_confidence_questions()` against the worktree, regenerates the plan with the investigation findings, and re-evaluates exactly once before posting the report.
3. **Cancellation seam** — `AgentSDKWrapper` gained `request_cancel()` / `wait_for_idle()` / `_stream_active`. `BaseAgent._safe_reset_session()` drains a live stream before zeroing `session_id` / `messages`, eliminating a synchronous-reset-vs-async-stream race.

Both features ship behind feature flags (`LOOP_C_ENABLED`, `AUTO_INVESTIGATE_ENABLED`), default off.

## Tasks Completed (all 11 from the plan)

| # | Task | File(s) | Status |
|---|------|---------|--------|
| 1 | `ReviewerHandoffTriggered` event class | `src/core/events/types.py:75-80`, `__init__.py` re-export | ✅ |
| 2 | env-flag helpers in `cli.py` | `src/cli.py:52-65` | ✅ |
| 3 | `_investigate_confidence_questions` | `src/agents/plan_generator.py:1099-1206` | ✅ |
| 4 | Step 3.5 auto-investigation hook in `run()` | `src/agents/plan_generator.py:1668-1705` | ✅ |
| 5 | Publish `ReviewerHandoffTriggered` from execute workflow | `src/cli.py` lines ~775, ~1102, ~1128 + helpers `_extract_blockers` / `_format_finding_class` at 67-103 | ✅ |
| 6 | Register second subscriber in `post_execute.py` | `src/core/execution/post_execute.py:158-209` | ✅ |
| 7 | `format_handoff_comment` helper | `src/core/execution/post_execute.py:39-58` | ✅ |
| 8 | Cancellation seam + `_safe_reset_session` | `src/agent_sdk_wrapper.py:47-69, 376-433`; `src/agents/base_agent.py:254-283`; bare-reset replacements at `plan_generator.py:428, 1023, 1130` | ✅ |
| 9 | Tests: confidence-miss auto-investigation | `tests/agents/test_plan_generator_auto_investigate.py` (3 tests) | ✅ |
| 10 | Tests: Loop C E2E + subscriber | `tests/core/test_post_execute_handoff.py` (8 tests), `tests/integration/test_loop_c_e2e.py` (15 tests) | ✅ |
| 11 | Tests: cancellation seam | `tests/test_agent_sdk_cancellation.py` (7 tests) | ✅ |

**Test totals**: 33/33 new tests pass across all flag-matrix permutations (LOOP_C only / AUTO_INVESTIGATE only / both on / both off).

## Validation Results

| Check | Result | Notes |
|-------|--------|-------|
| Level 1 — ruff | PASS for new lines | 27 errors are 100% pre-existing (F541, F401, F841, E741) — Wave 1/2/3 confirmed via diff |
| Level 1 — mypy | PASS for new lines | 4 baseline errors in `session_tracker.py`, `config_loader.py`, `guardrails.py`, `agent_sdk_wrapper.py:316` (TypedDict) all pre-existing |
| Level 2 — new unit tests | 33/33 PASS | |
| Level 3 — full suite | PASS for new code | 26 pre-existing failures in `test_plan_generator.py`, `test_environment_manager.py`, `test_jira_server_client.py`, `test_worktree_manager.py` — root cause is mocked LLM not writing files; unrelated to Phase 2B (verified by failure-mode inspection) |
| Level 4 — flag matrix | PASS | All four combinations green |
| Level 5 — manual on sentinel-dev | DEFERRED | Sandbox has no Docker/SSH; user must run with deliberate-veto fixture |

## Codebase Patterns Discovered (added to state file during run)

1. **Subagent boundaries are real and enforced**: `sentinel-learning-integrator.md` lists exact files; planner-internal logic in `plan_generator.py` was correctly NOT integrator territory and was delegated to a general-purpose agent.
2. **Function-local imports avoid circular deps**: `cli.py` imports `PlanGeneratorAgent` at module top; `plan_generator.py`'s Step 3.5 had to do `from src.cli import _auto_investigate_enabled` function-locally to avoid the cycle. The plan flagged this as a "GOTCHA: verify first" — and it was real.
3. **SDK wrapper attribute on BaseAgent is `agent_sdk`**, not the plan boilerplate's `_sdk_wrapper`. Anyone adding cancellation/streaming helpers must `getattr(self, "agent_sdk", None)`.
4. **Bus-init guard broadening was a real gap**: cli.py only created the EventBus when `_verifier_loop_enabled()` was true. Loop C alone (without Loop A) needed a `or _loop_c_enabled()` so `bus.publish(...)` doesn't NPE on a None bus. Wave 2a flagged this as a deviation; reviewer signed off as correct.
5. **DECISIONS §168 vs §180**: §168 is the comment template (binding); §180 is the comment-volume invariant (Loop A 0 / Loop C ≤ 1 per handoff). Both are exactly testable; tests assert the verbatim body for both singular and plural forms.

## Learnings

- **Specialist routing pays off** — three parallel sub-agents in Wave 1 finished in <2 min; sequential would have been ~6 min. Identifying which tasks shared boundaries (Tasks 1+2 in integrator, Tasks 6+7 alongside Task 5 in integrator) cut handoff overhead.
- **Read-only reviewer caught nothing** because the specialists' briefs included the binding constraints upfront. Briefs included exact line ranges, the DECISIONS sections to mirror, GOTCHAs from the plan, and "DO NOT touch" lists. The reviewer pass was confirmation, not discovery.
- **`finding_class` is the only injection-ish surface** in Phase 2B. The plan's MR-comment-injection risk (LOW × HIGH) is mitigated by: (a) inputs are machine-readable fields only (`category` / `id` / `title` token), (b) hard 80-char cap with `…` suffix, (c) Phase 2A owns the prompt-side hardening of `base_instructions.md`. Reviewer confirmed no Phase 2B production change weakens any of these.
- **`--force` belt-and-suspenders**: Step 3.5 in `run()` checks BOTH `evaluation` truthiness AND `not force`. Either alone would suffice (eval is None when force=True), but the explicit `not force` is correct defense-in-depth and was kept.

## Deviations from Plan

1. **Bus-init guard broadening in cli.py** (lines ~624, ~967): the plan implied "ensure publish only happens when bus exists" but did not spell out broadening the bus-creation gate. Wave 2a took the correct interpretation. Reviewer signed off.
2. **One extra bare-reset replacement** in `plan_generator.py:428` (`generate_plan`). The plan cited two sites (lines 1022-1024 and 1515-1516); Wave 2b found and replaced a third instance of the same idiom. Correct cleanup, no regression.
3. **`agent_sdk` (not `_sdk_wrapper`) attribute name**: the plan boilerplate at `plan.md:661` suggested `_sdk_wrapper`; actual attribute is `self.agent_sdk` (set in `BaseAgent.__init__`). Wave 1b documented this in the helper docstring and used `getattr(self, "agent_sdk", None)`.
4. **Function-local import** of `_auto_investigate_enabled` in `plan_generator.py` instead of top-level — required to break the circular import (`cli.py:22` imports `PlanGeneratorAgent` at module top). The plan flagged this as a possible GOTCHA; the import path was confirmed circular and the function-local form was used.

## Follow-ups (NOT done in this loop)

- **Manual sentinel-dev validation (Level 5)**: requires deliberate-blocker fixture ticket. User must run end-to-end:
  ```
  LOOP_C_ENABLED=1 LOOP_C_BLOCKER_THRESHOLD=1 sentinel execute TEST-BLOCKER-1
  AUTO_INVESTIGATE_ENABLED=1 sentinel plan TEST-LOWCONF-1
  ```
- **`git push` and `bd sync`**: Claude sandbox cannot push. User must push from host or sentinel-dev container.
- **Pre-existing test failures** (26): `test_plan_generator.py`, `test_environment_manager.py`, `test_jira_server_client.py`, `test_worktree_manager.py` all fail due to mocking patterns that pre-date Phase 2B. Recommend a separate `bd` issue to triage these — out of scope for Phase 2B.
- **`bd ready` follow-up review**: skipped per current loop scope; user can run `bd ready` against this branch on next session.

## Files Modified

- `src/core/events/types.py`, `src/core/events/__init__.py`
- `src/core/execution/post_execute.py`
- `src/agent_sdk_wrapper.py`
- `src/agents/base_agent.py`
- `src/agents/plan_generator.py`
- `src/cli.py`

## Files Created

- `tests/core/test_post_execute_handoff.py`
- `tests/agents/test_plan_generator_auto_investigate.py`
- `tests/integration/test_loop_c_e2e.py`
- `tests/test_agent_sdk_cancellation.py`
- `.claude/PRPs/reports/phase-2b-closed-loops-report.md` (this file)

## Files Explicitly Not Touched

Per integrator boundaries and `NOT Building` scope: `src/agents/security_reviewer.py`, `src/agents/drupal_reviewer.py`, `src/agents/base_developer.py`, `src/core/persistence/migrations/`, `prompts/shared/base_instructions.md`, `src/core/persistence/postmortems.py`. No new SQL migrations.
