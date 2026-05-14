---
iteration: 1
max_iterations: 20
plan_path: ".claude/PRPs/plans/phase-2b-closed-loops.plan.md"
input_type: "plan"
started_at: "2026-05-08T00:00:00Z"
---

# PRP Ralph Loop State

## Codebase Patterns
(Consolidate reusable patterns here - future iterations read this first)

- **Subagent boundaries (CLAUDE.md + sentinel-learning-integrator.md):**
  - `sentinel-learning-integrator` owns: `src/core/events/types.py`, `src/core/execution/post_execute.py`, `src/cli.py` env-flag plumbing, `src/prompt_loader.py`, orchestrator hooks. Does NOT do business logic.
  - `sentinel-test-harness-expert` owns: tests under `tests/` — fixtures, unit tests, integration tests.
  - `sentinel-persistence-expert` owns: SQLite migrations & `core/persistence/` helpers (NOT needed in this plan — `executions.phase` already exists).
  - `sentinel-verifier-loop-expert` owns: developer verifier-retry loop (Phase 1, NOT touched by this plan).
  - `sentinel-learning-reviewer` is **read-only** — invoke before declaring phase complete.
  - **Planner-side internals** (`src/agents/plan_generator.py`, `_investigate_confidence_questions`) are NOT integrator territory — treat as direct edits or general-purpose agent work.
  - **Cancellation seam** (`src/agent_sdk_wrapper.py` + `BaseAgent._safe_reset_session`) sits in seam territory; can be done as direct edits with care, or via integrator if pure plumbing.

- **Working directory**: all sentinel commands run from `/workspace/sentinel/`. The Claude sandbox edits are bind-mounted into `sentinel-dev` at `/app/`. No rebuild needed; `git push` must be done from the host.

## Current Task
Execute `/workspace/sentinel/.claude/PRPs/plans/phase-2b-closed-loops.plan.md` — Phase 2B closed loops (Loop C reviewer→planner escalation + confidence-miss auto-investigation + cancellation seam).

## Plan Reference
.claude/PRPs/plans/phase-2b-closed-loops.plan.md (relative to /workspace/sentinel)

## Instructions
1. Read the plan file before each iteration — it has 11 tasks in dependency order.
2. Delegate work to the right subagent (integrator / test-harness / persistence / verifier-loop). Do NOT do worker tasks myself when a specialist exists.
3. Run ALL validation commands (Levels 1–4) from the plan.
4. If any validation fails: identify root cause, fix, re-validate.
5. Update plan file: mark completed tasks, add notes.
6. When ALL validations pass: invoke `sentinel-learning-reviewer` BEFORE outputting `<promise>COMPLETE</promise>`.
7. Do NOT lie to exit. The loop continues until genuinely complete.

## Task dependency order (from plan §Step-by-Step Tasks)
1. ADD `ReviewerHandoffTriggered` event (integrator) — `src/core/events/types.py` + `__init__.py`
2. ADD env-flag helpers in `src/cli.py` (integrator) — `_loop_c_enabled`, `_auto_investigate_enabled`, `_loop_c_blocker_threshold`
3. CREATE `_investigate_confidence_questions` in PlanGeneratorAgent (planner internals — direct edit)
4. WIRE auto-investigation into `PlanGeneratorAgent.run()` (planner internals — direct edit)
5. PUBLISH `ReviewerHandoffTriggered` from execute workflow (integrator) — locate the review-revise loop exit
6. REGISTER second subscriber in `post_execute.py` (integrator)
7. ADD `format_handoff_comment` helper in `post_execute.py` (integrator)
8. ADD cancellation seam in `agent_sdk_wrapper.py` + `_safe_reset_session` in `base_agent.py` (seam — direct edit, mostly plumbing)
9. TESTS — confidence-miss auto-investigation (test-harness)
10. TESTS — Loop C end-to-end (test-harness)
11. TESTS — cancellation seam (test-harness)

## Progress Log

---
