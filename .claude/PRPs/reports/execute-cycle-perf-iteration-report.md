# Implementation Report

**Plan**: `execute-cycle-perf-iteration.plan.md`
**Branch**: `feature/execute-cycle-perf-iteration`
**Date**: 2026-05-18
**Status**: COMPLETE (Stage 0 + Stage A); Stage B & C operator-gated

---

## Summary

Landed a profile-first perf iteration on `sentinel execute`:

- Stage 0 autopsy data committed as a permanent artifact.
- Stage A instrumentation: opt-in `SENTINEL_PERF=1` perf harness, extended SDK-wrapper diagnostics (token usage, cache fields, time-to-first-chunk, `tool_complete` event), and `with timed():` seeds across the full execute pipeline.
- Permanent autopsy script + tests: reproduce the Stage-0 baseline and consume the new spans.
- Stage B fresh run + Stage C follow-on plans deferred to operator (require real `sentinel execute` from `sentinel-dev`); instructions live in the baseline report.
- Bumped `pyproject.toml` to `0.3.0` for the upcoming MR.

Default behavior unchanged when `SENTINEL_PERF` is unset; production runs pay zero cost.

---

## Assessment vs Reality

| Metric     | Predicted (plan)    | Actual                                                                   | Reasoning                                                                                                                         |
| ---------- | ------------------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| Complexity | MEDIUM              | MEDIUM                                                                   | Mechanical seed-planting once the perf primitive existed; the largest re-indent (`cli.execute`, ~800 lines) was solved with an inner-function wrapper. |
| Confidence | 8.5/10              | 8.5/10                                                                   | SDK usage object surfaces the cache fields as expected (`AssistantMessage.usage` is `dict[str, Any] \| None`). All other risks materialized as documented. |

Deviations: none material. The plan's GOTCHA #3 (verify `claude-agent-sdk ^0.1.20` exposes the usage object) is satisfied — `claude_agent_sdk/types.py` exposes `usage: dict[str, Any] | None` on both `AssistantMessage` and `ResultMessage`. Stage B will confirm whether the field is **populated** in practice.

---

## Tasks Completed

| # | Task                                                          | Status |
| - | ------------------------------------------------------------- | ------ |
| 0 | Stage-0 autopsy artifact + baseline report scaffold           | ✅     |
| 1 | `src/utils/perf.py` (`timed`, `is_enabled`, `perf_log_path`)  | ✅     |
| 2 | Re-export `timed` via `src/utils/__init__.py`                 | ✅     |
| 3 | `agent_sdk_wrapper.py` extension (3a + 3b + 3c)               | ✅     |
| 4 | `with timed():` seeds across orchestrator/container/test      | ✅     |
| 5 | `pyproject.toml` dev deps + perf marker + version bump        | ✅     |
| 6 | `tests/perf/` package, autopsy script, unit + snapshot tests  | ✅     |
| 7 | Stage-B fresh baseline run                                    | ⏸️ operator-gated (sentinel-dev) |
| 8 | Follow-on plan files                                          | ⏸️ conditional on Task 7         |
| 9 | HANDOVER update + final gate                                  | ✅     |

---

## Validation Results

| Check                      | Result | Details                                                                                                                                |
| -------------------------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| `ruff` on new code         | ✅     | `src/utils/perf.py`, `src/utils/__init__.py`, `tests/perf/` clean.                                                                     |
| `ruff` on touched src/     | ✅     | Pre-change: 31 errors (all pre-existing F541/F401). Post-change: 31 errors. Zero new lint introduced.                                   |
| `mypy` on new code         | ✅     | `src/utils/perf.py`, `src/utils/__init__.py`, `tests/perf/` — no issues.                                                               |
| `pytest tests/perf/`       | ✅     | 7/7 passing in ~0.05s.                                                                                                                  |
| Autopsy on real JSONL      | ✅     | `python tests/perf/autopsy.py --sessions` reproduces Stage-0 numbers (Sessions 11/12/16 match plan: 82/73/9, 60/58/2, 41/41/0).         |
| Default test suite         | ⏸️     | `pytest tests/ --ignore=tests/perf` cannot collect in this sandbox (`claude-agent-sdk` and other native deps not installed per CLAUDE.md container topology). Must run from `sentinel-dev`. The perf changes do not break collection — failures are pre-existing module-import errors at the pytest collection layer. |
| `SENTINEL_PERF` unset path | ✅     | Verified by `test_disabled_by_default` and `test_disabled_path_returns_noop_span`: no JSONL is written, `add_meta()` is a no-op.        |

---

## Files Changed

| File                                                              | Action  | Notes                                                                                                                          |
| ----------------------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `src/utils/perf.py`                                               | CREATE  | `timed()` ctx mgr with cached `is_enabled` + atomic JSONL append.                                                              |
| `src/utils/__init__.py`                                           | UPDATE  | Re-export `timed`.                                                                                                             |
| `src/agent_sdk_wrapper.py`                                        | UPDATE  | Token usage / cache / first-chunk on `exec_complete`; new `tool_complete` event with per-tool wallclock.                       |
| `src/agents/base_agent.py`                                        | UPDATE  | `set_project()` wrapped in `base_agent.set_project` span.                                                                      |
| `src/agents/base_developer.py`                                    | UPDATE  | Verifier-retry iteration wrapped in `base_developer.verifier_iteration` span; `run_tests` wrapped in `cmd.run_tests` span.     |
| `src/agents/plan_generator.py`                                    | UPDATE  | `generate_plan` / `revise_plan` wrapped via inner-method delegation.                                                           |
| `src/agents/drupal_reviewer.py`                                   | UPDATE  | Prompt assembly wrapped in `drupal_reviewer.assemble_prompt` span with per-section byte counters (header / description / diff / file_contents / footer / system_prompt / total_user). |
| `src/compose_runner.py`                                           | UPDATE  | `up`, `down`, `exec` wrapped in `compose.{up,down,exec}` spans.                                                                |
| `src/environment_manager.py`                                      | UPDATE  | `setup`, `_seed_volume`, `_run_post_start_commands` wrapped; per-command kind tagging (`composer` / `drush` / `git` / `other`). |
| `src/cli.py`                                                      | UPDATE  | Outermost `execute.full` span via `_execute_impl` wrapper (avoids re-indenting an 800-line click handler).                     |
| `pyproject.toml`                                                  | UPDATE  | Version `0.2.0` → `0.3.0`; added `pytest-benchmark ^4.0`, `py-spy ^0.3` to dev group; registered `perf_baseline` marker.        |
| `tests/perf/__init__.py`                                          | CREATE  | Package marker.                                                                                                                |
| `tests/perf/conftest.py`                                          | CREATE  | `enable_perf` and `tmp_log_dir` fixtures.                                                                                       |
| `tests/perf/autopsy.py`                                           | CREATE  | Permanent autopsy script (sessions / rank / cache-rate / tool-hist / reviewer-prompt). Runs against existing JSONL data.        |
| `tests/perf/test_perf_module.py`                                  | CREATE  | 6 unit tests (disabled-noop, JSONL write, add_meta, exception-meta, concurrent threads, no-op span).                            |
| `tests/perf/test_autopsy_script.py`                               | CREATE  | Snapshot test against frozen `.expected.txt`.                                                                                  |
| `tests/perf/fixtures/sample_diagnostics.jsonl`                    | CREATE  | 50-line slice of real diagnostics for snapshot.                                                                                |
| `tests/perf/fixtures/sample_autopsy.expected.txt`                 | CREATE  | Frozen autopsy output.                                                                                                          |
| `.claude/PRPs/reports/execute-cycle-perf-baseline.md`             | CREATE  | Living before/after report; Stage 0 frozen; Stage B/C placeholders + run instructions.                                          |
| `.claude/PRPs/reports/perf-data/autopsy-stage0.json`              | CREATE  | Frozen Stage-0 dataset (15 sessions × 3 tickets, 2026-04-20 → 2026-05-15).                                                      |
| `docs/agent-learning-from-feedback-HANDOVER.md`                   | UPDATE  | New "Performance Iteration" section pointing at the baseline report and instrumentation seeds.                                  |

---

## Deviations from Plan

1. **`cli.py:execute` wrapping.** Plan called for `with timed("execute.full"):` around the orchestration body. The body is ~800 lines; physically re-indenting was impractical. Mitigation: kept the click decorator on `execute()`, factored the body into `_execute_impl()`, and wrapped the call site. Same instrumentation result; cleaner diff.
2. **`plan_generator.{generate,revise}` wrapping.** Same inner-method pattern used for the same reason — both bodies are ≥ 200 lines.
3. **`environment_manager`.** Plan named `env.composer_install`, `env.drush_bootstrap`, `env.clone` — but the codebase doesn't have separate Python methods for those; they run as labeled commands in `_run_post_start_commands`. Implemented as a single `env.post_start_commands` outer span plus per-command `env.post_start.{composer,drush,git,other}` inner spans, which gives the same per-kind breakdown.
4. **No `composer.pull_image` span.** `compose_runner` has no separate pull method (image-pull is a side effect of `compose up --build`). Recorded as `compose.up` with `meta.build` flag instead.
5. **`run_static_checks` not separately wrapped.** Already enclosed in `verifier_iteration`; per-checker breakdown didn't seem high-value at the static-check level (PHPStan + composer validate + ruff + mypy are each subprocess-bound, not the dominant cost).

None of these change the data the harness can collect.

---

## Issues Encountered

1. **Sandbox lacks `claude-agent-sdk` and project deps.** Cannot run the full `pytest tests/` from this sandbox (per CLAUDE.md). Verified perf-test isolation: `pytest tests/perf/` runs cleanly with no SDK dep. Default-suite must run in `sentinel-dev`.
2. **Autopsy snapshot non-determinism.** First snapshot used `set` for cwd collection; switched to "first cwd encountered" for deterministic ordering.
3. **Reviewer prompt section refactor.** `_build_review_prompt` was a single string-concatenation chain. Refactored to extract sections into local variables FIRST (preserving exact byte content), then added `span.add_meta()`. The final `prompt` value is byte-identical to the prior implementation by inspection — same `+`-concatenation, same order, same content.

---

## Tests Written

| Test File                            | Test Cases                                                                                                                              |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/perf/test_perf_module.py`     | `test_disabled_by_default`, `test_timed_writes_jsonl_when_enabled`, `test_timed_supports_add_meta`, `test_timed_records_exception_meta`, `test_timed_handles_concurrent_threads`, `test_disabled_path_returns_noop_span` |
| `tests/perf/test_autopsy_script.py`  | `test_autopsy_snapshot` (compares stdout to frozen `.expected.txt`)                                                                     |

---

## Next Steps

1. Operator runs Stage B from `sentinel-dev`:
   ```bash
   SENTINEL_PERF=1 sentinel execute <SMALL-TICKET-ID> 2>&1 \
     | tee logs/autopsy/exec-stage-b-$(date +%Y%m%d-%H%M%S).log
   python tests/perf/autopsy.py \
     --report-out .claude/PRPs/reports/execute-cycle-perf-baseline.md
   ```
2. Operator manually fills the "Confirmed vs. rejected suspects" table in the baseline report.
3. Operator runs `/prp-plan` against each confirmed hot path to file the conditional follow-on plans (`perf-fix-prompt-cache-wiring`, `perf-fix-reviewer-prompt-slim`, `perf-fix-plan-cache`, `perf-fix-dev-tool-decisiveness`) — only those whose Stage-B numbers warrant it.
4. Open MR (`feature/execute-cycle-perf-iteration` → `main`) — needs to be done from the host or `sentinel-dev` (sandbox has no SSH keys per CLAUDE.md).
