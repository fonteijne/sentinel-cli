# Implementation Report

**Plan**: /workspace/sentinel/.claude/PRPs/plans/m7-phpunit-junit-from-container.plan.md
**Completed**: 2026-05-14
**Iterations**: 1

## Summary

Surfaced PHPUnit JUnit test results from the per-ticket appserver
container. Previously `_parse_test_output` checked the host filesystem
for `/tmp/phpunit-junit.xml` — a path that never existed on the host
because phpunit ran inside the container. The function now dispatches:
when an env is attached it execs `cat` over DooD; when no env is
attached it preserves the original host-path read. The verifier loop's
refine prompt on the Drupal stack now contains structured per-test
failures alongside PHPStan / composer-validate signal.

## Tasks Completed

- Task 1: `src/agents/drupal_developer.py::_parse_test_output` — env
  branch added, docstring rewritten, host-fallback preserved.
- Task 2: `tests/test_drupal_developer.py::TestContainerAwareTests` —
  three new tests (container hit, container miss, container raise).
- Task 3: Host-fallback regression test added (no env, monkeypatched
  constant, fixture round-trip).
- Task 4: Broader regression suite green (drupal_developer +
  base_developer_verifier_loop + structured-error adapters + golden).

## Validation Results

| Check                                | Result | Detail                                      |
|--------------------------------------|--------|---------------------------------------------|
| Level 1 static analysis (ruff)       | PASS   | 0 findings on changed files                 |
| Level 2 unit tests (TestContainerAwareTests) | PASS | 10/10 (6 prior + 4 new)            |
| Level 3 targeted regression          | PASS   | 110/110                                     |
| Level 4 full suite                   | PASS\* | 1012 passed (1008 prior + 4 new). 26 unrelated pre-existing failures verified by re-running on unmodified tree. |

\* Acceptance criterion ("prior pass count + 4 new tests, no
regressions") satisfied.

## Codebase Patterns Discovered

- `StructuredError` is a `TypedDict` (not a dataclass) — tests must use
  `entry["file"]`, not `entry.file`. The plan's "assert each entry has
  keys file, line, rule, message populated" was correct guidance; the
  attribute-style assertion suggested by the surface mirror needs
  translation when the type is a TypedDict.
- `parse_phpunit_junit` accepts `str` directly. `EnvironmentManager.exec`
  returns `ComposeResult` whose `.stdout` is already `str` — no decode
  needed.
- The existing host-path fallback in `_parse_test_output` was retained
  verbatim. This matches the env-guard pattern used in
  `validate_config`, `run_static_checks`, and `_diagnose_failed_patches`
  elsewhere in the file.

## Learnings

- Iteration 1: implementation landed correctly; only one tweak needed
  (TypedDict vs attribute access in tests).
- The plan's "MIRROR" references were exact line numbers, which made
  Task 1 essentially a transcription job — high confidence, low risk.
- The pre-existing 26-test failure surface is unrelated to M7 and was
  verified by stash-and-rerun; recommended to track as a separate
  hygiene issue (out of scope for this PRP).

## Deviations from Plan

- None functionally. Test code uses `entry["file"]` instead of
  `entry.file` because `StructuredError` is a `TypedDict`. The plan
  said "mirroring `test_structured_error_adapters_golden.py`", which
  also uses dict subscript access — so this is consistent with the
  intent.

## Files Modified

- `/workspace/sentinel/src/agents/drupal_developer.py` — refactored
  `_parse_test_output` (env-branch + host fallback).
- `/workspace/sentinel/tests/test_drupal_developer.py` — added 4 tests
  in `TestContainerAwareTests`.

No fixture, compose, env-manager, or `_get_test_command` changes (per
plan scope).
