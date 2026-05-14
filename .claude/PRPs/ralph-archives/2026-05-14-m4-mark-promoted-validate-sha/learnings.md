# Implementation Report: M4 — Validate SHA Format in `mark_promoted`

**Plan**: `.claude/PRPs/plans/m4-mark-promoted-validate-sha.plan.md`
**Completed**: 2026-05-14
**Iterations**: 1

## Summary

Added defensive SHA-format validation at two layers — the `mark_promoted(...)` persistence helper and the `sentinel learning mark-merged --sha` Click option. Both layers reject anything that isn't 7-64 lowercase hex characters. Operator typos (e.g. `--sha abc`) now surface a clean Click `BadParameter` (exit 2) at parse time instead of silently writing garbage to the append-only feedback ledger.

## Tasks Completed

- Task 1: `src/core/persistence/feedback_rules.py` — added `import re`, module-level `_SHA_RE`, validation at the top of `mark_promoted`, extended module + function docstrings.
- Task 2: `src/cli.py` — added `import re`, `_LEARNING_SHA_RE`, `_validate_sha` callback near `_learning_seed_synthetic_execution`, attached `callback=_validate_sha` to the `--sha` option on `learning mark-merged`.
- Task 3: `tests/core/test_feedback_rules_helpers.py` — migrated 7 sentinel-string SHAs to valid 7-hex equivalents and added two parametrized tests (`test_mark_promoted_accepts_valid_sha` covering 7/40/64-char paths; `test_mark_promoted_rejects_invalid_sha` covering 10 typo classes including the no-mutation-on-rejection assertion).
- Task 4: `tests/test_cli_learning.py` — migrated `--sha def456` → `--sha def4567`, `--sha bbb` → `--sha bbb1234`, added `test_mark_merged_rejects_invalid_sha_at_cli` parametrized over 5 bad SHAs (dropped `""` per plan gotcha — Click required-option behavior makes empty unreliable; persistence-layer test still covers it).
- Task 5: `tests/integration/test_phase2c_supersede_chain.py` — `sha="aaa"` → `sha="aaa1234"` (3 occurrences).
- Task 6: `tests/integration/test_phase2c_promotion.py` — `def456` → `def4567` (3 occurrences).

## Validation Results

| Check | Result | Notes |
|-------|--------|-------|
| Level 1: ruff (touched files) | PASS | No new warnings on touched lines; pre-existing F541/F841/E741 in cli.py unrelated |
| Level 1: mypy (touched files) | PASS | `feedback_rules.py` clean; `cli.py` shows 2 pre-existing `assignment` errors at lines 684/1026, unrelated |
| Level 2: unit tests | PASS | 47/47 passing (`tests/core/test_feedback_rules_helpers.py` + `tests/test_cli_learning.py`) |
| Level 3: full suite | PASS (no regressions) | 1038 passed; 26 failed are all pre-existing per orchestrator note (`test_environment_manager.py`, `test_jira_server_client.py`, `test_plan_generator.py`, `test_worktree_manager.py`) |

## Codebase Patterns Discovered

- Python's `re.match` / `$` anchor still matches a trailing `\n`. For strict end-of-string anchoring on user input, use `re.fullmatch` (preserves the regex literal as-written without needing `\Z`).
- The project uses `_VALID_STATUS = frozenset({...})` style for module-private validation constants; new `_SHA_RE` follows that placement convention.
- `src/cli.py` had no prior art for Click `callback=` or `BadParameter` — this fix introduces the first usage. Helper placed adjacent to `_learning_seed_synthetic_execution` (the existing module-private helper for the learning command group).

## Deviations from Plan

1. **`re.match` → `re.fullmatch`** (both layers). The plan literally specified `re.match(r"^[0-9a-f]{7,64}$", ...)` but also enumerated `"abc1234\n"` as a must-reject case in Task 3. Those two are inconsistent in Python — `$` matches before a final newline. Switched to `re.fullmatch` so the regex literal stays exactly as the plan specified while honoring the rejection-list contract. Documented in plan's "Implementation Notes" section.

2. **Dropped `""` from CLI parametrize** (Task 4). Plan flagged this as a gotcha: Click rejects required-but-empty options before the callback fires, producing exit 2 but with a different message ("Missing argument" vs. "Invalid value"). The persistence-layer test in Task 3 still covers empty strings.

## Follow-up

None required. Pre-existing 26 test failures are out of scope (per orchestrator note) and tracked separately. No issue to file.
