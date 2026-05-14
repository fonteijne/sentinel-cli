---
iteration: 1
max_iterations: 20
plan_path: "sentinel/.claude/PRPs/plans/phase-2c-promotion-path.plan.md"
input_type: "plan"
started_at: "2026-05-09T00:00:00Z"
---

# PRP Ralph Loop State

## Codebase Patterns
(Populated from plan §Patterns to Mirror — keep this section updated with any new ones discovered.)

- Migrations are forward-only, `IF NOT EXISTS`, statement-by-statement (`src/core/persistence/db.py:148-158` adds `BEGIN IMMEDIATE` + per-statement exec — never `executescript()`).
- Persistence helpers: keyword-only args after `conn`, use `_VALID_STATUS` frozenset for guards, no `update_*`/`delete_*` exports for append-only tables.
- Pydantic events: `Literal["Name"]` discriminator, additive (Phase 1 + 2A tests assume this).
- Click CLI: nested groups, `try/except`, `connect() + apply_migrations()` boilerplate, heavy imports inside subcommand bodies.
- Feature flag pattern: read at call time via `os.getenv(NAME, "0") == "1"`, default off (mirrors `POSTMORTEM_INJECTION` at `src/prompt_loader.py:12-19`).
- Test fixtures: in-memory SQLite + `apply_migrations` + parent `executions` rows (`tests/core/test_postmortems.py:14-72`). FK pragma ON.
- Subprocess git ops: `cwd=repo_root, check=True, capture_output=True` (`src/agents/plan_generator.py:790-855`).
- D7 invariant: proposer MR always `draft=True`. Hard-coded.

## Current Task
Execute PRP plan `sentinel/.claude/PRPs/plans/phase-2c-promotion-path.plan.md` and iterate until all validations pass.

## Plan Reference
sentinel/.claude/PRPs/plans/phase-2c-promotion-path.plan.md

## Instructions
1. Read the plan file
2. Implement all incomplete tasks
3. Run ALL validation commands from the plan
4. If any validation fails: fix and re-validate
5. Update plan file: mark completed tasks, add notes
6. When ALL validations pass: output <promise>COMPLETE</promise>

## Progress Log
