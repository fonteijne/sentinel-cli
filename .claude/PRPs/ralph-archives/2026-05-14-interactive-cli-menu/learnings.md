# Implementation Report

**Plan**: .claude/PRPs/plans/interactive-cli-menu.plan.md
**Completed**: 2026-05-14T18:35:00Z
**Iterations**: 1

## Summary

Added an interactive two-step menu to bare `sentinel` invocations. `cli()` is
now `invoke_without_command=True`: when no subcommand is given, a
`questionary` ticket picker (populated from active worktrees across all
configured projects, plus a `+ new…` entry) is shown, followed by an action
menu (`Debrief` / `Plan` / `Execute` / `Execute --revise` / `Reset`) that
dispatches to the existing Click subcommands via `ctx.invoke`. All existing
`sentinel <subcommand>` invocations are unchanged. No business logic was
modified.

## Tasks Completed

- Task 1 — `questionary = "^2.0.1"` added to `pyproject.toml` `[tool.poetry.dependencies]`.
- Task 2 — `cli()` group switched to `invoke_without_command=True` with `@click.pass_context`; dispatches to `_run_menu(ctx)` only when `ctx.invoked_subcommand is None`.
- Task 3 — `src/cli.py` gained `_TICKET_ID_RE`, `_MENU_ACTIONS`, and the helpers `_collect_loaded_tickets`, `_prompt_ticket`, `_prompt_new_ticket`, `_prompt_action`, `_dispatch`, `_run_menu`. `import questionary` added to the import block.
- Task 4 — `python3 -m compileall src/cli.py` exits 0; all helpers verified present at module load.
- Task 5 — Helper unit tests in `tests/test_cli_menu.py` (sort order, empty config, regex parsing, unknown-project flow, Ctrl-C, dispatch wiring per action, reset confirm/decline).
- Task 6 — `CliRunner` integration tests covering plan dispatch, execute --revise, reset confirm/decline, subcommand regression (menu must NOT fire when a subcommand is given), --help (menu must NOT fire), and clean Ctrl-C exit.
- Task 7 — Manual smoke steps documented in plan (Step-by-Step Tasks → Task 7); user runs them inside `sentinel-dev` after `poetry install`.

## Validation Results

| Check                         | Result                                                                                                                                                |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Level 1 — `compileall`        | PASS                                                                                                                                                  |
| Level 1 — ruff (changed code) | PASS (10 pre-existing hits in unrelated regions of `cli.py`; zero in added lines or new test file)                                                    |
| Level 2 — `test_cli_menu.py`  | 22/22 PASS                                                                                                                                            |
| Level 3 — full suite          | 1051 pass / 26 pre-existing failures (reproduced on `git stash` — unrelated to this change: jira_server, worktree_manager, plan_generator, env_manager) |
| Level 4 — manual smoke        | PENDING — to be run by user in `sentinel-dev` after `poetry install`                                                                                  |

## Codebase Patterns Discovered

- Click `Context.invoke(cmd, **kw)` reads `cmd.callback` at call time, so monkeypatching `cmd.callback` in tests is the canonical way to spy on subcommand dispatch without reaching deeper into Click internals.
- `invoke_without_command=True` *must* pair with `@click.pass_context` and a `ctx.invoked_subcommand is None` guard; otherwise the group callback fires for *every* subcommand call (verified by regression test).
- `questionary.<type>(...).ask()` returns `None` on Ctrl-C — preferred over `unsafe_ask()` which raises `KeyboardInterrupt` and produces a traceback.
- For multi-prompt test flows, monkeypatch `questionary.<type>` with a closure-captured queue rather than rebuilding a stub from a list per call (a per-call rebuild always replays the first element).

## Learnings

- The sandbox has neither `questionary` nor `pytest-asyncio` from the dev environment; install with `pip install --user --break-system-packages` for local validation. The canonical install path is `poetry lock && poetry install` from `sentinel-dev` (see plan Task 1 gotcha).
- All 26 full-suite failures are environmental fixture failures pre-dating this branch — they reproduce identically on the parent commit. Filing a separate cleanup ticket would be appropriate but is out of scope for this PRP.
- `ConfigLoader` may cache project state; after `ctx.invoke(projects_add)` we re-fetch via `get_config()` before continuing the menu flow.

## Deviations from Plan

- None. Implementation follows the plan exactly. Task 7 (manual smoke) is documentation only; the steps are listed in the plan but cannot be executed from this sandbox (no TTY, no `poetry`).

## Files Changed

- `pyproject.toml` — added `questionary = "^2.0.1"` dependency
- `src/cli.py` — added `import questionary`, regex constant, action constant, six helper functions, modified `cli()` group decorator and signature
- `tests/test_cli_menu.py` — new test file (22 tests)
- `.claude/PRPs/plans/interactive-cli-menu.plan.md` — checked off completed tasks, added implementation notes section

## Next Steps for User

1. From the host or `sentinel-dev` container: `poetry lock && poetry install` to materialize the `questionary` lock entry.
2. Run the 5 manual smoke steps (plan → Step-by-Step Tasks → Task 7).
3. Move the plan file to `.claude/PRPs/plans/completed/` after merge.
