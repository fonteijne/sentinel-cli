# Implementation Report: H7 — Pretask SHA Per Loop A Retry Attempt

**Plan**: `.claude/PRPs/plans/h7-pretask-sha-per-attempt.plan.md`
**Completed**: 2026-05-14T11:41:47Z
**Iterations**: 1

## Summary

Fixed a verifier-scope correctness bug in Loop A: `pretask_sha` was captured once at the start of `implement_feature()` and reused across all retry attempts. If the developer SDK created a git commit between attempts (Bash is allowed), the per-attempt diff base went stale and `_derive_changed_test_paths` either ballooned scope or silently missed the new attempt's changes.

Adopted **Option 1**: re-capture `pretask_sha` at the start of every Loop A attempt, anchoring each attempt's diff to "wherever HEAD was when *this* attempt started." Layered an Option-2 advisory drift warning (`_warn_if_sha_drifted`) on top so unexpected mid-loop commits are observable in logs without failing the run.

## Tasks Completed

- **Task 1**: Added `_warn_if_sha_drifted` helper on `BaseDeveloperAgent` immediately after `_capture_pretask_sha`. No-ops when either SHA is `None`. Emits a single `logger.warning` with attempt number + both short SHAs when drift is detected.
- **Task 2**: Modified `_implement_feature_with_loop` — added `attempt_sha = self._capture_pretask_sha(worktree_path)` + `_warn_if_sha_drifted(...)` before the SDK call, and changed `run_tests(pretask_sha=pretask_sha)` to `run_tests(pretask_sha=attempt_sha)`. Outer `pretask_sha` retained as drift sentinel only.
- **Task 3**: Updated the comment block above the loop-entry capture in `implement_feature()` to describe loop-entry-vs-per-attempt semantics.
- **Task 4**: Added `test_loop_recaptures_pretask_sha_per_attempt` (asserts per-attempt SHA threading) + `test_loop_warns_on_mid_loop_sha_drift` (asserts single WARNING with both SHAs and attempt number).
- **Task 5**: Ran the full regression surface — all green.

## Validation Results

| Check | Result |
|-------|--------|
| Static analysis (`py_compile` + import) | PASS |
| Unit tests (`tests/agents/test_base_developer_verifier_loop.py`) | PASS — 9/9 (2 new) |
| Full suite (`tests/agents/`, `tests/integration/test_verifier_retry.py`, `tests/test_python_developer.py`, `tests/test_drupal_developer.py`) | PASS — 174/174 |

## Codebase Patterns Discovered

- **Logging convention**: `%s`/`%d` placeholders, never f-strings (mirrors `base_developer.py:789-792`).
- **Loop A test scaffolding**: `monkeypatch.setenv("DEV_VERIFIER_LOOP", "1")` + replace `agent.agent_sdk.execute_with_tools` with an async function + patch `run_tests`/`run_static_checks`/`execute_command`.
- **Subprocess patching pattern**: `patch("src.agents.base_developer.subprocess.run")` with `side_effect=[Mock(returncode=0, stdout=..., stderr=""), ...]`. When patching `agent.run_tests` outright, only `_capture_pretask_sha`'s `git rev-parse HEAD` calls hit the mock — `_derive_changed_test_paths` short-circuits.
- **`_capture_pretask_sha`**: returns `Optional[str]`; `None` means "no diff base, fall back to broad scope" — drift helper must no-op on `None` to avoid spurious warnings on transient git failures.

## Learnings

- Adding the assertion `mock_run.call_count == 3` to the recapture test made the test self-documenting — any future refactor that adds another `subprocess.run` callsite within Loop A will fail loudly with a clear error message instead of producing confusing SHA-mismatch failures downstream.
- `caplog.set_level(logging.WARNING, logger="src.agents.base_developer")` is required at the top of `caplog`-using tests in this project; relying on the default propagation misses records when test config sets higher thresholds.

## Deviations from Plan

None. Implementation followed the plan task-for-task. Shipped in a single Ralph iteration.
