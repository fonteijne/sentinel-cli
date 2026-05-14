# Implementation Report

**Plan**: `.claude/PRPs/plans/verifier-cross-iteration-feedback.plan.md`
**Branch**: `feat/sentinel-learning-system`
**Date**: 2026-05-12
**Status**: COMPLETE

---

## Summary

Carry verifier failures across iteration boundaries so iteration N+1 sees what failed in iteration N. Added a `RegressionContext` dataclass plus pure helpers (`_render_regression_section`, `_dedupe_structured_errors`), wired the context through `BaseDeveloperAgent.implement_feature` (both single-shot and Loop A) so its rendered markdown block prepends each task prompt, and updated the CLI iteration loop in `src/cli.py` to harvest per-iteration failures and inject them into the next iteration's developer prompts. Failures now carry structured errors via two custom exception types (`DeveloperCappedOutException`, new `DeveloperTaskFailedException`) so the iteration boundary in `developer.run()` can collect them — same plumbing the reviewer-feedback channel uses, just sourced from the verifier instead of the reviewer.

---

## Assessment vs Reality

| Metric     | Predicted          | Actual             | Reasoning |
| ---------- | ------------------ | ------------------ | --------- |
| Complexity | LOW-MEDIUM         | LOW-MEDIUM         | Matched. The pure helpers and prompt-prepend were trivial; capturing structured errors at the iteration boundary required attaching them to exceptions because failed tasks today raise instead of returning a result dict. |
| Confidence | High (named files) | High               | All named files (`base_developer.py`, `_structured_errors.py`, `cli.py`) existed; `StructuredError` is a TypedDict (not a class with `render_oneline()`), so the renderer formats fields directly — same shape as the existing `_build_refine_prompt`. |

**Deviations from the literal plan text:**

- Plan referred to `run_implementation_plan` as the iteration host. That method does not exist; the iteration loop lives in `src/cli.py:execute()` (around line 1066). Implemented there instead.
- Plan’s `RegressionContext.errors` typed as `list[StructuredError]`, plan example called `err.render_oneline()`. `StructuredError` is a TypedDict with no methods — render directly from `file/line/rule/message`, mirroring `_build_refine_prompt`’s bullet format for consistency.
- Plan referred to dedup keys as `(test_class, test_method, error_type, line)`. Those fields don’t exist on `StructuredError`. Used `(file, line, rule, message)` — the available analogues — and added a unit test pinning that contract.

---

## Tasks Completed

| # | Task | File | Status |
|---|------|------|--------|
| 1 | Add `RegressionContext`, `_dedupe_structured_errors`, `_render_regression_section`, plus failure-carrying exceptions (`DeveloperCappedOutException` keyword-args, new `DeveloperTaskFailedException`) | `src/agents/base_developer.py` | ✅ |
| 2 | Thread `regressions` kwarg through `implement_feature` → `_implement_feature_single_shot` and `_implement_feature_with_loop`; new `_prepend_regression_section` method that emits the markdown block before `_append_operator_prompt` | `src/agents/base_developer.py` | ✅ |
| 3 | Capture per-task structured errors in `BaseDeveloperAgent.run` (read from the exception attribute), expose deduped union as `regression_errors` on the result dict | `src/agents/base_developer.py` | ✅ |
| 4 | CLI iteration loop accumulates `regression_errors` into a `RegressionContext` between iterations and passes it into the next `developer.run` call | `src/cli.py` | ✅ |
| 5 | CLI prints `↺ Carrying N regression(s) from iteration M into developer prompts` whenever an iteration starts with non-empty regressions | `src/cli.py` | ✅ |
| 6 | Unit + integration tests covering helpers, prompt prepending (both code paths), exception payload, and the `run`-level harvest/thread | `tests/agents/test_base_developer_regressions.py` | ✅ |

---

## Validation Results

| Check | Result | Details |
|-------|--------|---------|
| AST parse — `base_developer.py` | ✅ | OK |
| AST parse — `cli.py` | ✅ | OK |
| Ruff — new test file | ✅ | All checks passed |
| Ruff — `base_developer.py` | ✅ | One pre-existing F541 (line 1206, blamed to 2026-04-17), no new lint errors introduced |
| Ruff — `cli.py` | ✅ | Pre-existing F541s only, none introduced |
| Unit tests — new file | ✅ | 11 / 11 passed |
| Unit tests — `tests/agents/` | ✅ | 91 / 91 passed |
| Unit tests — `tests/test_python_developer.py`, `tests/test_drupal_developer.py` | ✅ | 75 / 75 passed |
| Broader suite | ⚠ | 32 pre-existing failures in unrelated modules (`test_environment_manager`, `test_jira_server_client`, `test_plan_generator`, etc.) — confirmed by re-running with my changes stashed |

---

## Files Changed

| File | Action | Notes |
|------|--------|-------|
| `src/agents/base_developer.py` | UPDATE | Added `RegressionContext`, dedup + render helpers, `DeveloperTaskFailedException`, `_prepend_regression_section`; threaded `regressions` kwarg through `implement_feature`/`_implement_feature_*`; harvested failures in `run` |
| `src/cli.py` | UPDATE | Imported `RegressionContext`; added carry-forward variable + carry-count log line; passed `regressions=` into `developer.run` |
| `tests/agents/test_base_developer_regressions.py` | CREATE | 11 tests covering helpers, both prompt paths, exception payload, and run-level harvest/thread |

---

## Deviations from Plan

See *Assessment vs Reality* — all three deviations stem from the plan text referencing identifiers that don’t exist (`run_implementation_plan`, `render_oneline()`, `test_class/test_method` fields). Implementation maps the plan’s intent to the actual call graph + actual `StructuredError` schema.

---

## Issues Encountered

- Failed tasks in `BaseDeveloperAgent.run` raise rather than return; structured errors needed to flow back through that boundary. Solved by attaching `structured_errors` as an attribute on the relevant exceptions and reading them via `getattr(e, "structured_errors", [])` in the catch block — keeps the existing `RuntimeError`-shaped contract (callers that don't care still see a regular exception) but lets the iteration loop pick up the data.
- `RegressionContext.errors` field needed `field(default_factory=list)` because `list[StructuredError]` is mutable; otherwise dataclass refuses the default.

---

## Tests Written

| Test File | Test Cases |
|-----------|------------|
| `tests/agents/test_base_developer_regressions.py` | `test_render_regression_section_empty_returns_blank`, `test_render_regression_section_includes_header_count_and_errors`, `test_dedupe_collapses_identical_errors_preserving_order`, `test_dedupe_distinguishes_different_lines`, `test_single_shot_prepends_regression_block`, `test_single_shot_no_regressions_no_block`, `test_single_shot_failure_carries_structured_errors_on_exception`, `test_loop_path_prepends_regression_block`, `test_loop_capout_exception_carries_last_errors`, `test_run_collects_regression_errors_from_failed_tasks`, `test_run_threads_inbound_regressions_into_each_task` |

---

## Next Steps

- [ ] Review the diff (especially the `developer.run` signature change — it adds a kwarg, default-`None`, so callers without `regressions=` keep working)
- [ ] Run a multi-iteration ticket end-to-end (Task 6 — manual verification on DHLEXS_DHLEXC-311 or another reliably-failing case) and confirm:
  - iteration 2+ logs `↺ Carrying N regression(s) ...`
  - iteration 2's developer prompt visibly contains `## Prior Iteration Regressions` in agent-SDK debug logs
  - iteration 2 produces strictly fewer structured failures than iteration 1
- [ ] If smoke is good, create PR
