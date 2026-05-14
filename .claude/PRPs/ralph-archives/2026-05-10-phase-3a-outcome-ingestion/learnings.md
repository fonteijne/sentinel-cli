# Implementation Report — Phase 3A Outcome Ingestion (Pull Path)

**Plan**: `.claude/PRPs/plans/phase-3a-outcome-ingestion.plan.md`
**Completed**: 2026-05-10
**Iterations**: 1
**Branch**: `feat/sentinel-learning-system`

## Summary

Phase 3A introduces the ground-truth signal for the learning system: `executions.outcome ∈ {success, rolled_back, regressed}` populated by a pull-on-demand sync from GitLab, gated on `OUTCOME_SYNC_ENABLED=1`. State is durable via `project_sync_state` per D6 (no `installation_id`). Phase 3B (reranker) and 3C (skill promotion) are explicitly deferred.

Implementation completed in a single Ralph iteration via a four-wave orchestration:

- **Wave 1 (parallel):** `sentinel-persistence-expert` (Tasks 1-3 — migration + sync_state.py + persistence `__init__`), `sentinel-learning-integrator` (Tasks 4-5 — `OutcomeRecorded` event), general-purpose worker (Tasks 6-7 — GitLab client methods).
- **Wave 2:** general-purpose worker (Tasks 8-9 — `OutcomeSyncService` + classifier + summary dataclass + learning `__init__`).
- **Wave 3:** `sentinel-learning-integrator` (Task 10 — CLI `outcomes` group + `outcomes sync` subcommand + preflight hooks in `plan`/`execute`).
- **Wave 4:** `sentinel-test-harness-expert` (Task 11 — full unit + integration + CLI surface).

## Tasks Completed

| # | Surface | File(s) |
|---|---|---|
| 1 | Migration | `src/core/persistence/migrations/005_outcome_ingestion.sql` |
| 2 | Persistence helpers | `src/core/persistence/sync_state.py` |
| 3 | Persistence re-export | `src/core/persistence/__init__.py` |
| 4 | Event class | `src/core/events/types.py` (+`OutcomeRecorded`) |
| 5 | Events re-export | `src/core/events/__init__.py` |
| 6 | GitLab MR list (paginated) | `src/gitlab_client.py:list_merged_mrs_since` |
| 7 | GitLab pipelines list | `src/gitlab_client.py:list_pipelines_for_commit` |
| 8 | Sync service + classifier | `src/core/learning/outcome_sync.py` |
| 9 | Learning re-export | `src/core/learning/__init__.py` |
| 10 | CLI seam + preflight | `src/cli.py` (+ `_outcome_sync_enabled`, `outcomes` group, `_run_outcome_sync_preflight` in `plan`/`execute`) |
| 11 | Tests | `tests/test_gitlab_client.py` (+6), `tests/core/test_outcome_sync.py` (NEW, 18 tests), `tests/integration/test_phase3a_outcomes.py` (NEW, exit-criterion fixture), `tests/test_cli_outcomes.py` (NEW, 4 tests) |

## Validation Results

| Level | Check | Result |
|---|---|---|
| 1 | `mypy src/core/learning/outcome_sync.py src/core/persistence/sync_state.py src/gitlab_client.py` | PASS — `Success: no issues found in 3 source files` |
| 1 | `ruff check src/core/learning/outcome_sync.py src/core/persistence/sync_state.py` | PASS — clean (Phase 3A surfaces) |
| 1 | `ruff check src/cli.py` | 10 pre-existing warnings on lines outside Task 10 edits (405, 959, 960, 2292, 2589, 2613, 2686, 2830, 2832, 3001) — none introduced by this phase |
| 2 | `pytest tests/test_gitlab_client.py` | 37 passed |
| 2 | `pytest tests/core/test_outcome_sync.py` | 18 passed |
| 2 | `pytest tests/test_cli_outcomes.py` | 4 passed |
| 3 | `pytest tests/integration/test_phase3a_outcomes.py` | 1 passed (exit-criterion fixture per PRD line 496-497) |
| 4 | `pytest -q` (full suite) | 937 passed, 26 failed (vs. baseline 670 passed, 35 failed → net +267 passing, -9 failures) |
| 5 | Migration safety (idempotent re-run, all three new columns + table queryable) | PASS — prints `migration ok` |
| C | `git grep -n "python-gitlab"` | 0 matches |
| C | `git grep -nE "outcome_weight\|outcome_weight_recompute\|propose_skills"` | 0 matches |
| C | `git grep -nE "OutcomeRecorded\|outcome_sync\|project_sync_state"` in `src/` | All matches in expected files (cli.py, persistence, learning, events) |

### Pre-existing full-suite failures (unrelated to Phase 3A)

Confirmed against pre-Phase-3A baseline. All in modules untouched by this phase:
- `tests/test_environment_manager.py` — 9 tests (subprocess mock semantics)
- `tests/test_jira_server_client.py::TestAddComment` — 4 tests (Jira client drift)
- `tests/test_plan_generator.py` — 12 tests (`PlanGeneratorAgent` constructor signature drift)
- `tests/test_worktree_manager.py::TestEnsureBareClone::test_ensure_bare_clone_creates_new` — 1 test (`subprocess.run` call count drift)

Notable observation filed by test-harness-expert (NOT a Phase 3A issue, deferred to Phase 2B owners): `tests/test_plan_generator.py::TestUnifiedPlanFlow::test_run_update_with_investigation` fails with `KeyError: 'passed'` at `src/agents/plan_generator.py:1674` — the Phase 2B confidence-miss auto-investigation guard assumes `evaluation['passed']` always exists; should use `.get("passed", True)` or normalize upstream.

## Acceptance Criteria — All Met

- [x] Migration `005_outcome_ingestion.sql` lands; `executions.outcome` column + `project_sync_state` table present in fresh DB.
- [x] `GitLabClient.list_merged_mrs_since` and `list_pipelines_for_commit` exist; pagination tested via both `X-Total-Pages` and short-page fallback.
- [x] `OutcomeRecorded` event re-exported from `src.core.events`.
- [x] `OutcomeSyncService.sync()` correctly tags all three outcome categories on integration fixture.
- [x] Watermark advances; re-run reports zero new MRs (`summary.mrs_seen == 0`).
- [x] `executions.outcome` is append-once (second UPDATE leaves the original row intact; verified by `TestSyncStateHelpers::test_update_execution_outcome_is_append_once`).
- [x] CLI `outcomes sync` honors `--project`, `--since`, `--all`, `--dry-run`, gates on `OUTCOME_SYNC_ENABLED`.
- [x] Pre-flight in `plan` and `execute` is a no-op with flag off; with flag on, sync exceptions are logged and swallowed.
- [x] Levels 1-4 green.
- [x] No new dependencies in `pyproject.toml`.
- [x] No write to `postmortems`, `feedback_rules`, `prompts/`, or `commands/` from any code added in this phase.

## Codebase Patterns (consolidated from this run)

- **Append-once via WHERE clause:** `UPDATE executions SET outcome=?, ... WHERE id=? AND outcome IS NULL` enforces single-write semantics without a CHECK constraint or trigger. The helper returns `cursor.rowcount` so callers gate their event-publish on `== 1`. Tests assert second-call returns 0 and original row unchanged.
- **CHECK with explicit NULL guard:** SQLite would accept NULL under bare `IN(...)` but `CHECK (outcome IS NULL OR outcome IN (...))` makes the optionality intent loud for future readers.
- **Pure-function classifier + thin service class:** `classify_outcome(mr, pipelines, revert_mr) -> (label, evidence)` is fully unit-testable without DB or HTTP. The service composes it with persistence + event-bus side-effects. Mirrors `extract.py`'s separation.
- **Best-effort lookups inside a sync loop:** revert-detection and pipelines lookup wrapped in per-MR try/except. Watermark advances only past MRs whose `_process_mr` returned `handled=True` so transient HTTP failures resume cleanly next run.
- **Heavy imports inside Click function bodies (`# noqa: PLC0415`):** keeps `import src.cli` cheap for `--help` invocations. Mirrors `learning extract` discipline.
- **`--all` Click flag mapping to `all_history` Python identifier:** avoids shadowing the builtin while keeping the user-facing flag readable.

## Deviations from Plan

- **`plan`/`execute` preflight passes `project=None`** (not the local `project` variable). The plan suggested using whatever project variable was in scope; the integrator correctly identified that `project` in those entrypoints is the Jira project key, not the GitLab project path, and would cause spurious 404s. The helper falls back to `_discover_known_projects(conn)` which is the correct behavior. Documented in code comment.
- **`_print_outcome_sync_summary` uses `getattr(summary, ...)`** to stay decoupled from the dataclass import path. Functionally identical; mildly more defensive.
- **Synthetic execution row seeded but `execution_id` variable unused at the call site.** `OutcomeSyncService.sync()` does not accept an `execution_id` arg — the seeded row exists purely to satisfy the FK on bus publish. Comment in cli.py documents this.

## Notes for Phase 3B / 3C

- `executions.outcome` and the `OutcomeRecorded` event stream are the join keys. Phase 3B's reranker can subscribe to `OutcomeRecorded` and recompute `feedback_rules.confidence` per the design's Appendix C.6 curve; this phase deliberately did not.
- The classifier's severity order (`regressed > rolled_back > success`) is permanent — downstream consumers can rely on a single label per execution.
- `executions.outcome_evidence_json` is verbatim — Phase 3B can re-key off it for deeper analysis without re-pulling from GitLab.
- The flag `OUTCOME_SYNC_ENABLED` defaults to `0`. The exit-criterion fixture (now passing) is the gate to flip it on per-installation.
