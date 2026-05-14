# Implementation Report: Phase 1 — Close the Leash

**Plan**: `.claude/PRPs/plans/phase-1-close-the-leash.plan.md`
**Completed**: 2026-05-08
**Branch**: `feat/sentinel-learning-system`
**Iterations**: 1 (single-pass orchestration via specialist subagents)
**Reviewer status**: **APPROVE** (sentinel-learning-reviewer, read-only gate)

## Summary

Sentinel's developer agent now runs a grounded, capped (N=3) verifier-retry loop (Karpathy Loop A) gated behind `DEV_VERIFIER_LOOP=1`. Test and static-check failures are parsed into structured errors and fed back to the same agent for up to 3 attempts; on cap-out the agent emits `DeveloperCappedOut`, persists a postmortem row (`provenance='auto'`, `fix_summary=NULL`), reverts the MR to draft, and posts exactly one "Sentinel paused here" comment. A minimal `src/core/{persistence,events,execution}/` foundation was created to support Loop A and the Phase 2 work that depends on it.

## Tasks Completed

| # | Task | Owner agent | Status |
|---|------|-------------|--------|
| 1 | `db.py` + `001_init.sql` (executions, events, agent_results, schema_migrations) | persistence-expert | ✅ |
| 2 | `003_postmortems.sql` + `postmortems.py` (insert-only helper, append-only) | persistence-expert | ✅ |
| 3 | `events/types.py` (pydantic v2) + `events/bus.py` (persist-first, per-execution seq) | learning-integrator | ✅ |
| 4 | `_structured_errors.py` (6 parsers + `normalize_failure_signature`) | verifier-loop-expert | ✅ |
| 5 | `run_tests()` new shape `{passed, test_results, structured_errors, return_code}` | verifier-loop-expert | ✅ |
| 6 | `run_static_checks()` for Drupal (PHPStan + composer) and Python (ruff + mypy) | verifier-loop-expert | ✅ |
| 7 | Loop A wrap of `implement_feature` with `MAX_ATTEMPTS=3`, refine prompt, event emission | verifier-loop-expert | ✅ |
| 8 | `gitlab_client.mark_as_draft` (idempotent, symmetrical to `mark_as_ready`) | learning-integrator | ✅ |
| 9 | `post_execute.py` subscriber: postmortem + draft revert + 1 MR comment + `PostmortemRecorded` re-emit | learning-integrator | ✅ |
| 10 | CLI wiring: `DEV_VERIFIER_LOOP`, SQLite open + migrate, EventBus, `set_event_bus`, `DeveloperCappedOutException` catch | learning-integrator | ✅ |
| 11 | Refine-prompt policy paragraph in `prompts/shared/base_instructions.md` | learning-integrator | ✅ |
| 12 | Shared fixtures (`tests/conftest.py`) + smoke tests | test-harness-expert | ✅ |
| 13 | 12 golden-file fixtures in `tests/fixtures/static_check_output/` | test-harness-expert | ✅ |
| 14 | Reviewer gate (read-only) | learning-reviewer | ✅ APPROVE |

## Validation Results

| Level | Check | Result |
|-------|-------|--------|
| 1 | ruff/mypy on Phase 1 code | PASS — 0 new ruff errors; mypy clean on `src/core/` and `src/agents/_structured_errors.py` |
| 2 | Unit tests (core + agents + gitlab_client) | PASS — 103 passed |
| 3 | Integration test `test_verifier_retry.py` | PASS — 4/4 |
| 4 | Full suite | PASS — 756 passed / 35 baseline-failing (same set; zero new regressions) |
| 5 | Smoke (live ticket) | DEFERRED — operational gate |
| 6 | ≥20 real-world runs | DEFERRED — operational gate |

The 35 baseline failures pre-date this work and are confined to: `test_base_agent`, `test_confidence_evaluator`, `test_environment_manager`, `test_jira_server_client`, `test_plan_generator`, `test_worktree_manager`. Verified pre/post identical via `git stash` diff.

## Codebase Patterns Discovered

- The `src/core/` directories existed but contained only stale `__pycache__` from another branch — treat foundation as greenfield on `feat/sentinel-learning-system`.
- `executescript()` silently commits — use per-statement `execute()` inside an explicit `BEGIN IMMEDIATE`/`COMMIT` for migration runners.
- Comment-aware SQL splitter is required if migration files contain `;` characters inside `--` line comments.
- WAL pragma is silently downgraded on `:memory:` SQLite databases — `:memory:` is fine for fixture connections, but tests asserting WAL must use a temp file.
- pydantic v2 events with `ts: str = ""` and a bus that fills empty `ts` is a clean pattern for unit-testability.
- `mr_iid_resolver` callable beats subscriber-ordering coupling for late-binding the MR IID inside the cap-out handler.
- Per-execution `seq` in `events` is computed via `SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE execution_id = ?` inside the same transaction as the INSERT.

## Architectural Decisions (settled, see plan)

- Pydantic v2 chosen over dataclass for events (matches d75d276 commit shape; avoids Phase 2 churn if `feat/interactive-cli` lands).
- Migration numbering `001_init`, `003_postmortems` (gap left for `002_workers` if interactive-cli's foundation lands later).
- Single global `MAX_ATTEMPTS = 3` constant — no per-stack overrides.
- `mark_as_draft` is idempotent and called regardless of prior MR state (D7).
- Zero MR comments on Loop A retries; exactly one cap-out comment (D8).
- Postmortems are append-only — no `UPDATE` or `DELETE` helper functions; revocation is `superseded_by`-based, deferred to Phase 2.
- Schema is applied even when `DEV_VERIFIER_LOOP=0` so the DB is ready when the flag flips.
- Refine prompt does not replay the previous diff (SDK session history has it) and does not inject postmortem rules (Phase 2 concern).

## Deviations from Plan

1. **Drupal `_parse_test_output` reads JUnit XML from host filesystem only.** When PHPUnit runs inside `appserver`, the JUnit file at `/tmp/phpunit-junit.xml` lives inside the container and is not host-accessible. The container path returns `[]` and the loop still terminates correctly because the cap is hard. This was authorized in the verifier-loop-expert brief.
2. **`provenance` validated at the helper layer** (constrained to `{'auto', 'human-edited'}`), not just at the schema. Cheap; converts a typo into a `ValueError`.
3. **`mr_iid_resolver` callable** chosen over wrapper-subscriber for late MR-IID binding. Rationale: avoids `bus.subscribe` registration-order coupling.
4. **`--log-junit=/tmp/phpunit-junit.xml`** added unconditionally to `_get_test_command()` (rather than gating on `_verifier_loop_enabled()`). Test commands must not differ depending on env state. Required updating 5 hard-coded phpunit-command assertions in existing tests.
5. **Pre-existing `add_merge_request_comment` call in `revise_implementation` summary path** (`base_developer.py:1591`) is unrelated to Loop A's D8 invariant — D8 covers Loop A retries, not Loop B revision summaries. Not a blocker.

## Operational Gates Remaining (before Phase 2)

- **Level 5 smoke**: Run `DEV_VERIFIER_LOOP=1 SENTINEL_DB_PATH=/tmp/sentinel-smoke.db sentinel execute TICKET-FIXTURE-FAIL` against a deliberately-failing ticket; verify `postmortems` row count = 1 and `events` table has expected counts (`TestResultRecorded=3`, `StaticCheckRecorded≥1`, `DeveloperCappedOut=1`, `PostmortemRecorded=1`).
- **Level 6 real-world**: ≥20 real `sentinel execute` runs with the flag on. Required telemetry (`first_pass`, `cap_outs`, `total_runs`, cost delta) attached to the PR description before Phase 2 specialist agents are created.

## Files Touched

**Modified (9):**
- `prompts/shared/base_instructions.md`
- `src/agents/base_developer.py`
- `src/agents/drupal_developer.py`
- `src/agents/python_developer.py`
- `src/cli.py`
- `src/gitlab_client.py`
- `tests/test_drupal_developer.py`
- `tests/test_gitlab_client.py`
- `tests/test_python_developer.py`

**Created (production):**
- `src/agents/_structured_errors.py`
- `src/core/__init__.py`
- `src/core/persistence/{__init__.py, db.py, postmortems.py}`
- `src/core/persistence/migrations/{001_init.sql, 003_postmortems.sql}`
- `src/core/events/{__init__.py, types.py, bus.py}`
- `src/core/execution/{__init__.py, post_execute.py}`

**Created (tests + fixtures):**
- `tests/conftest.py`
- `tests/core/{__init__.py, test_persistence.py, test_postmortems.py, test_event_bus.py}`
- `tests/agents/{__init__.py, test_base_developer_verifier_loop.py, test_drupal_static_checks.py, test_python_static_checks.py, test_structured_error_adapters.py, test_structured_error_adapters_golden.py, test_shared_fixtures_smoke.py}`
- `tests/integration/{__init__.py, test_verifier_retry.py}`
- `tests/fixtures/static_check_output/{phpstan,phpunit_junit,pytest_short,composer_validate,mypy,ruff}_{pass,fail}.{json,xml,txt}` (12 golden files)

## Diff Stats

```
9 files changed, 972 insertions(+), 69 deletions(-)
+ 30+ new files (foundation, tests, fixtures)
```

## Reviewer Findings (all NIT)

1. `post_execute.py:146` re-emits `PostmortemRecorded` with `ts=""` relying on bus to fill — fine within the bus, traps if the event ever bypasses the bus.
2. `events/types.py:36` defaults `ts=""` rather than using `Field(default_factory=...)` — deliberate for testability.
3. Document baseline 35-test-failure set in PR description so reviewers don't chase ghosts.
4. Add a one-line comment at `base_developer.py:1591` clarifying the `add_merge_request_comment` there is the Loop B revise-summary post, not a Loop A retry comment.
5. Levels 5 + 6 must be exercised before Phase 2 agent roster is created.

None require code changes for Phase 1 to be considered complete.
