---
iteration: 1
max_iterations: 20
plan_path: ".claude/PRPs/plans/phase-2a-pitfalls-visible.plan.md"
input_type: "plan"
started_at: "2026-05-08T21:46:15+02:00"
---

# PRP Ralph Loop State — Phase 2A "Pitfalls Visible"

## Codebase Patterns
(Consolidate reusable patterns here — future iterations read this first.)

- **Repo location**: All work happens in `/workspace/sentinel/` (subrepo). The Claude Code sandbox edits files here; the bind-mount makes them live in the `sentinel-dev` container at `/app`.
- **Persistence module convention**: Read/write helpers for a table live in `src/core/persistence/<table>.py`; package re-exports via `src/core/persistence/__init__.py`. Append-only invariant — no UPDATE/DELETE in helpers.
- **Event surface**: New event classes go in `src/core/events/types.py` (Pydantic, `Literal["..."]` type discriminator). Re-export from `src/core/events/__init__.py`. The bus persists-then-publishes (`src/core/events/bus.py:44-104`).
- **Subscriber registration pattern**: `register_<name>(bus, deps...)` factory at module level; closure-based handler with `isinstance` guard inside (`src/core/execution/post_execute.py:60-156`).
- **Prompt loader cache contract**: Currently `Dict[str, str]` keyed on `agent_name`. Phase 2A widens to `Dict[tuple[str, str], str]` keyed on `(agent_name, stack_type or "")`.
- **Feature flag pattern**: Read env var at call time (`os.getenv("FOO", "0") == "1"`), no caching. See Phase 1's `DEV_VERIFIER_LOOP` at `src/cli.py:41-47`.
- **Test fixture pattern for SQLite**: in-memory `sqlite3.connect(":memory:")` with `row_factory=Row`, `PRAGMA foreign_keys=ON`, `apply_migrations(c)`, then insert a parent `executions` row (mirror `tests/core/test_postmortems.py:14-42`).
- **Tooling**: `poetry run pytest`, `poetry run ruff check`, `poetry run mypy` are the canonical commands. The plan's validation level commands are authoritative.

## Current Task

Execute the Phase 2A plan end-to-end:
1. Add SELECT helpers to `src/core/persistence/postmortems.py`
2. Build pitfalls renderer (`src/core/learning/pitfalls.py`)
3. Add `PromptBudgetExceeded` event
4. Extend `PromptLoader.load()` with `stack_type` + `conn` kwargs (tuple cache, feature-flagged)
5. Cache invalidator subscriber (`src/core/learning/cache_invalidator.py`)
6. `BaseAgent.set_project()` re-loads prompt with stack
7. CLI: `sentinel postmortems list` group + cache-invalidator wiring
8. `prompts/shared/base_instructions.md` PROMPT-INJECTION SAFETY clause
9. All accompanying tests (unit + integration exit criterion)

Until all validation levels (Levels 1–6 in plan) pass.

## Plan Reference

`.claude/PRPs/plans/phase-2a-pitfalls-visible.plan.md` (13 tasks, 841 lines).

## Instructions

1. Read the plan file
2. Implement all incomplete tasks
3. Run ALL validation commands from the plan
4. If any validation fails: fix and re-validate
5. Update plan file: mark completed tasks, add notes
6. When ALL validations pass: output the completion promise

## Progress Log

(Append learnings after each iteration.)

---
