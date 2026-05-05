---
name: cc-validate
description: Run the Level 1 (static analysis), Level 2 (per-plan unit tests), and Level 3 (full suite) validation commands defined in the Command Center plans. Use when you've finished a task or track and need to verify against the plan's stated validation gates. Accepts a plan number (01-05) or "all".
user-invocable: true
allowed-tools:
  - Bash
  - Read
---

# /cc-validate — Run Command Center plan validations

Runs the validation commands specified in `sentinel/.claude/PRPs/plans/command-center/NN-*.plan.md` §Validation Commands.

Arguments: `$ARGUMENTS` — one of `01`, `02`, `03`, `04`, `05`, `all`, or empty (prompt which).

## Level 1 — static analysis (all plans)

```bash
cd /workspace/sentinel
poetry run ruff check src/core src/service tests/core tests/service 2>&1 || poetry run flake8 src/core src/service tests/core tests/service
poetry run mypy src/core src/service 2>&1 || true
```

## Level 2 — per-plan unit tests

| Plan | Command |
|------|---------|
| 01 | `poetry run pytest tests/core -v && poetry run pytest tests/test_base_agent.py tests/test_session_tracker.py -v` |
| 02 | `poetry run pytest tests/service/test_executions_routes.py -v` |
| 03 | `poetry run pytest tests/service/test_stream.py -v` |
| 04 | `poetry run pytest tests/core/test_supervisor.py tests/core/test_worker_logging.py tests/service/test_commands_routes.py tests/integration/test_end_to_end.py -v` |
| 05 | `poetry run pytest tests/service/test_auth.py -v && poetry run pytest tests/service -v` |

## Level 3 — full regression

```bash
cd /workspace/sentinel
poetry run pytest -x
```

## Level 4 — manual smoke (sentinel-dev only, not this sandbox)

Listed in each plan's §Validation Commands. Print the commands; do not attempt to run them from here (no Docker CLI in sandbox).

## Execution

1. Parse `$ARGUMENTS`. If empty, ask the user which plan to validate.
2. Run Level 1 first; on failure, stop and report.
3. Run Level 2 for the selected plan(s).
4. If `all` or the user asks for regression, run Level 3.
5. Print Level 4 commands as a reminder — do not execute.

Report back: for each level, PASS/FAIL and which tests failed.
