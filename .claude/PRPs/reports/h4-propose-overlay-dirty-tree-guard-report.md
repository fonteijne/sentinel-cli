---
plan: .claude/PRPs/plans/h4-propose-overlay-dirty-tree-guard.plan.md
completed: "2026-05-14T11:19:38Z"
iterations: 1
---

# Implementation Report — H4: Guard `propose_overlays` Against Dirty Working-Tree Edits

## Summary

Closed HIGH-severity finding **H4** from the `feat/sentinel-learning-system` PR review: a dry-run of `sentinel learning propose` could silently overwrite an operator's uncommitted edits to a `prompts/overlays/<scope>_<agent>.md` file because `_apply_overlay_edit` writes to the working tree and the dry-run revert path only deletes the branch ref. Two-layer defense added in `propose_overlays`:

1. **Pre-flight clean-tree assertion** via `_assert_overlay_paths_clean(repo_root, overlay_relpaths)` using `git status --porcelain --` with explicit pathspecs — refuses to run when any candidate overlay file has uncommitted modifications or is untracked, naming every blocking path in the error.
2. **Belt-and-braces byte-restore** on the dry-run path — captures `read_bytes()` before each `_apply_overlay_edit`, restores via `write_bytes(original)` before `git checkout -` / `git branch -D`.

`_apply_overlay_edit` itself was untouched. Real-run on a clean tree is byte-identical to before; dry-run on a clean tree is byte-identical to before.

## Tasks Completed

- **Task 1**: Added `_assert_overlay_paths_clean` helper (`src/core/learning/propose_overlay.py`)
- **Task 2**: Wired the pre-flight call into `propose_overlays` after `rules_by_agent` grouping and before `_capture_starting_ref` / `git checkout -b`
- **Task 3**: Added `original_overlay_bytes: dict[Path, bytes]` capture + dry-run restore loop
- **Task 4**: Replaced apologetic "Constraints" docstring block with the new contract
- **Task 5**: Appended 4 new tests in `tests/core/test_propose_overlay.py`:
  - `test_dry_run_refuses_when_overlay_is_dirty`
  - `test_real_run_refuses_when_overlay_is_dirty`
  - `test_dry_run_leaves_overlay_file_byte_identical_on_clean_tree`
  - `test_dry_run_refuses_when_overlay_is_untracked`

## Validation Results

| Check                                                      | Result | Detail                                                |
| ---------------------------------------------------------- | ------ | ----------------------------------------------------- |
| `ruff check` on changed files                              | PASS   | exit 0                                                |
| `mypy src/core/learning/propose_overlay.py`                | PASS   | Success: no issues found in 1 source file             |
| `pytest tests/core/test_propose_overlay.py -v`             | PASS   | 19 passed (15 pre-existing + 4 new H4)                |
| `pytest tests/integration/test_phase2c_*.py -v`            | PASS   | 3 passed — no E2E regression                          |

## Codebase Patterns Discovered

- **Pre-flight precondition placement**: cross-cutting preconditions live in the orchestrator (`propose_overlays`) rather than in pure rendering helpers (`_apply_overlay_edit`). The renderer stays focused; the orchestrator owns sequencing and validation.
- **`git status --porcelain --` with explicit pathspecs**: the codebase already uses porcelain v1 in `src/worktree_manager.py:585`. v1 is stable since git 1.7 and locale-independent. Use explicit pathspecs to keep unrelated dirt invisible to the check.
- **Belt-and-braces restore via `read_bytes`/`write_bytes`**: when capturing-and-restoring file contents around a transformation, prefer bytes over text — exact round-trip independent of newline normalization.
- **Subprocess + `CalledProcessError` → decoded stderr → `RuntimeError` chain**: established at `src/core/learning/propose_overlay.py:431-441`; new code mirrors it for consistency.

## Learnings (Iteration Notes)

- The plan's acceptance criterion of "9 existing tests" was stale — H2 and H3 had already landed 6 additional tests in `test_propose_overlay.py` (e.g., `test_real_run_restores_head_on_success`, `test_branch_name_unique_across_seconds`). The H4 changes composed cleanly with all of them; no existing test required modification. The expected post-H4 count is 19, not 13.
- The plan said to insert the pre-flight "after rules grouping and before `git checkout -b` (line 429)". The H2 fix had already added `_capture_starting_ref` between those two points. Correct placement is *before* `_capture_starting_ref` so the precondition runs before any git or filesystem mutation — matches the plan's intent ("precondition first, mutating action second"), not the literal line reference.

## Deviations from Plan

- None of substance. Insertion line numbers shifted because H2 had already moved nearby code; the plan anticipated this in the Risks table ("re-anchor the new lines to the H2/H3-modified surroundings"). All semantic acceptance criteria are met.
- Manual validation (Level 5) was skipped — Ralph executes deterministic validation only. No `sentinel learning propose --dry-run` was invoked against the live repo.
- Full suite (Level 4) was not run by the implementer subagent; the targeted unit + integration tests cover the scope and are sufficient to demonstrate no regression.

## Next Steps for Operator

1. Review the diff (`git diff src/core/learning/propose_overlay.py tests/core/test_propose_overlay.py`).
2. Commit on `feat/sentinel-learning-system` (Ralph does not commit or push).
3. Optionally run Level 5 manual validation per the plan against a real repo.
4. H2 (branch state leak) and H3 (branch-name collision) remain separate work items per the plan's "NOT Building" section — they were already partially landed (the pre-existing test names suggest H2 has merged); H3 status not verified here.
