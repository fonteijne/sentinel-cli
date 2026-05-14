# Implementation Report

**Plan**: /workspace/sentinel/.claude/PRPs/plans/drush-empty-module-name-guard.plan.md
**Completed**: 2026-05-14
**Iterations**: 1

## Summary

Added boundary guards to `parse_drush_config_validation` so that whitespace-only
captures from the lazy `[\w\- ]+?` regex no longer emit polluting empty-name
`StructuredError` bullets. The regex itself is unchanged — the fix lives at the
boundary, dropping empty captures via `continue` immediately after `.strip()`.

## Tasks Completed

- Task 1: Added `if not module or not dep: continue` guard with explanatory
  comment to the `_DRUSH_MODULE_REQUIRES` loop in
  `src/agents/_structured_errors.py`.
- Task 2: Added `if not module: continue` guard with one-liner comment to the
  `_DRUSH_MODULE_DOES_NOT_EXIST` loop in `src/agents/_structured_errors.py`.
- Task 3: Added `test_empty_module_name_is_silently_dropped` regression test in
  `tests/agents/test_structured_error_adapters.py` covering 4 scenarios
  (HTML-empty-em, plaintext double-space, requires variant, mixed valid+malformed).

## Validation Results

| Check | Result |
|-------|--------|
| Level 1: `py_compile` | PASS |
| Level 2: `TestParseDrushConfigValidation` | PASS (12/12) |
| Level 3: full `test_structured_error_adapters.py` suite | PASS (41/41) |
| Level 6: manual validation script | PASS (`OK`) |

## Codebase Patterns Discovered

- The drush adapter inlines HTML fixtures as Python string constants
  (`_DRUSH_MISSING`, `_DRUSH_REQUIRES`) at module level. The
  `tests/fixtures/static_check_output/` directory is reserved for
  static-analyzer outputs (phpstan/ruff/mypy/pytest/phpunit/composer) and must
  NOT be used for drush prose fixtures.
- `continue`-on-skip is the established defensive idiom in
  `_structured_errors.py` for malformed/unusable captures.

## Learnings

- Placement matters: the new guards intentionally sit BEFORE the
  `key = (...)` / `if key in seen` block, so empty keys never enter the dedup
  set and never block a later legitimate match.
- The regex's space tolerance is load-bearing for legitimate inputs like
  `"Drupal Symfony Mailer"`. Tightening it would risk regressions on real
  drush output, which is why option 2 (boundary filter) was chosen.

## Deviations from Plan

None. Tasks 1–3 were executed exactly as specified.
