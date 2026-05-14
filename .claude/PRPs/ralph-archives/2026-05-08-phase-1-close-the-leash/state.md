---
iteration: 1
max_iterations: 20
plan_path: ".claude/PRPs/plans/phase-1-close-the-leash.plan.md"
input_type: "plan"
started_at: "2026-05-08T00:00:00Z"
---

# PRP Ralph Loop State — Phase 1: Close the Leash

## Codebase Patterns
- Working dir is `/workspace/sentinel` (subrepo); branch `feat/sentinel-learning-system`.
- Toolchain: `poetry run pytest`, `poetry run ruff`, `poetry run mypy`. Python 3.11.
- `src/core/` dirs exist but contain only stale `__pycache__` from another branch — NO source files yet on this branch. Treat foundation as greenfield.
- 5 specialist agents present: `sentinel-{persistence,verifier-loop,learning-integrator,test-harness,learning-reviewer}-expert`.
- Pattern: container-exec via `env_manager.exec(ticket_id, service, command, workdir)` returns `ComposeResult(success, stdout, stderr, returncode)`.
- Pattern: `drupal_developer.py:150-213` (validate_config) is the mirror for `run_static_checks`.
- Run-tests current shape `{success, output, return_code}` lives at `base_developer.py:547-573`; callers at `:357, :696, :1051`.
- GitLab `mark_as_ready` at `gitlab_client.py:263-283` is the symmetry mirror for `mark_as_draft`.
- Feature-flag pattern: env-var read at `cli.py:1185`. New flag `DEV_VERIFIER_LOOP=1`.
- Migration numbering: `001_init`, `003_postmortems` (gap left for `002_workers` if interactive-cli lands).
- Pydantic v2 chosen for events (matches d75d276 commit shape; do NOT use dataclass).

## Current Task
Execute the 14-task plan. Use specialist subagents per ownership table. Validate at each level.

## Plan Reference
.claude/PRPs/plans/phase-1-close-the-leash.plan.md

## Dispatch Order
- Round 1 (parallel): Task 1+2 (persistence), Task 3 (events), Task 4 (parsers), Task 11 (prompt), Task 13 (fixtures)
- Round 2 (after Round 1): Task 5 (run_tests shape), Task 8 (mark_as_draft)
- Round 3: Task 6 (static_checks subclasses)
- Round 4: Task 7 (Loop A wrap)
- Round 5: Task 9 (post_execute subscriber), Task 10 (CLI wiring), Task 12 (test consolidation)
- Round 6: Task 14 (reviewer gate)

## Progress Log
(Append after each iteration.)

---
