# Feature: Interactive `sentinel` CLI Menu

## Summary

Wrap Sentinel's existing Click commands in a two-step interactive menu so that running bare `sentinel` shows a list of loaded tickets (one per active worktree across all configured projects, plus a `+ new…` entry) and, after selection, an action menu (`Debrief`, `Plan`, `Execute`, `Execute --revise`, `Reset`) that dispatches to the existing subcommand. No business logic changes — purely a UX layer on top of `cli.py`.

## User Story

As a Sentinel operator
I want to run `sentinel` with no arguments and pick a loaded ticket + action from arrow-key menus
So that I avoid typing project keys, ticket IDs, and command flags for routine work

## Problem Statement

Today, `sentinel` with no args prints Click's auto-generated help. To act on a ticket, the user must remember (a) which tickets have active worktrees, (b) the project key, and (c) the exact subcommand and flags. There is no in-CLI way to discover loaded tickets short of `sentinel status` followed by retyping the action.

## Solution Statement

Add `invoke_without_command=True` to the existing `cli()` group and route the no-subcommand case to a new internal `_run_menu()` helper. The helper uses `questionary` for arrow-key selection, builds the ticket list by iterating `ConfigLoader.get_all_projects()` × `WorktreeManager.list_worktrees(key)`, and dispatches to existing commands via `click.Context.invoke(...)`. All existing `sentinel <subcommand>` invocations continue to work unchanged.

## Metadata

| Field            | Value                                                      |
| ---------------- | ---------------------------------------------------------- |
| Type             | NEW_CAPABILITY                                             |
| Complexity       | LOW                                                        |
| Systems Affected | `src/cli.py`, `pyproject.toml`, `tests/`                   |
| Dependencies     | `questionary ^2.0.1` (new)                                 |
| Estimated Tasks  | 7                                                          |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                              BEFORE STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   $ sentinel                                                                  ║
║   ┌─────────────────────────────────────────────────────────────────────┐    ║
║   │ Usage: sentinel [OPTIONS] COMMAND [ARGS]...                         │    ║
║   │                                                                      │    ║
║   │ Sentinel - Autonomous agent orchestration for Jira tickets.         │    ║
║   │                                                                      │    ║
║   │ Commands:                                                            │    ║
║   │   debrief   Run a functional debrief for a Jira ticket.             │    ║
║   │   execute   ...                                                      │    ║
║   │   plan      ...                                                      │    ║
║   │   reset     ...                                                      │    ║
║   │   ...                                                                │    ║
║   └─────────────────────────────────────────────────────────────────────┘    ║
║                                                                               ║
║   USER_FLOW:                                                                  ║
║     1. Run `sentinel` → reads help                                            ║
║     2. Run `sentinel status` to see what's loaded                             ║
║     3. Type full command: `sentinel execute DHLEXC-311 --revise --project…`  ║
║                                                                               ║
║   PAIN_POINT: 3-step recall (loaded tickets, project key, flags) for         ║
║              every routine action. No in-CLI affordance for "what now?".     ║
║   DATA_FLOW: User memory → keystrokes → Click subcommand                     ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                               AFTER STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   $ sentinel                                                                  ║
║                                                                               ║
║   ? Select a ticket: (Use arrow keys)                                         ║
║   » DHLEXS_DHLEXC-311                                                         ║
║     DHLEXS_DHLEXC-123                                                         ║
║     JIRA_TESTAI-1234                                                          ║
║     ACME_ACME-142                                                             ║
║     ─────────────────                                                         ║
║     + new…                                                                    ║
║                                                                               ║
║                              ▼ (user selects)                                 ║
║                                                                               ║
║   ? Action for DHLEXS_DHLEXC-311:                                             ║
║   » Debrief                                                                   ║
║     Plan                                                                      ║
║     Execute                                                                   ║
║     Execute --revise                                                          ║
║     Reset                                                                     ║
║                                                                               ║
║                              ▼ (Reset only)                                   ║
║                                                                               ║
║   ? Reset DHLEXC-311? This removes the worktree and local branch. (y/N)      ║
║                                                                               ║
║                              ▼                                                ║
║                                                                               ║
║   ┌──────────────────────────────────────────────────────────┐               ║
║   │ ctx.invoke(<existing Click command>, ticket_id=…, …)      │              ║
║   └──────────────────────────────────────────────────────────┘               ║
║                                                                               ║
║   USER_FLOW:                                                                  ║
║     1. `sentinel` → arrow-key pick from loaded tickets                        ║
║     2. Arrow-key pick action                                                  ║
║     3. (Reset only) y/N confirm                                               ║
║     4. Existing subcommand runs; CLI exits when done                          ║
║                                                                               ║
║   VALUE_ADD: Zero recall — ticket list comes from worktrees, action          ║
║              names come from the menu, no flag typing.                        ║
║   DATA_FLOW: get_all_projects() × list_worktrees() → questionary →           ║
║              ctx.invoke(plan|debrief|execute|reset)                           ║
║                                                                               ║
║   `+ new` SUB-FLOW:                                                           ║
║     1. text prompt: "Ticket ID:"                                              ║
║     2. parse `PROJECT-NUMBER` → derive project key                            ║
║     3. if project unknown → ctx.invoke(projects_add) → reload config          ║
║     4. continue to action menu                                                ║
║                                                                               ║
║   EMPTY-STATE:                                                                ║
║     If no projects configured AND/OR no worktrees:                            ║
║     menu shows only `+ new…`. Selecting it routes to the same sub-flow.      ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                         | Before                    | After                                | User Impact                                           |
| -------------------------------- | ------------------------- | ------------------------------------ | ----------------------------------------------------- |
| `$ sentinel` (no args)           | Click help text           | Interactive ticket + action menu     | Zero-recall workflow for loaded tickets               |
| `$ sentinel <subcommand> …`      | Works                     | Works (unchanged)                    | None — non-interactive flows preserved                |
| `$ sentinel --help`              | Works                     | Works (unchanged)                    | None — Click's help still available                   |
| New ticket entry                 | `sentinel plan ACME-142`  | `sentinel` → `+ new` → `ACME-142`    | Discoverable; auto-prompts to add project if unknown  |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File                                  | Lines       | Why Read This                                                      |
| -------- | ------------------------------------- | ----------- | ------------------------------------------------------------------ |
| P0       | `src/cli.py`                          | 183–195     | The `cli()` group decorator — must add `invoke_without_command`   |
| P0       | `src/cli.py`                          | 218–260     | `plan` signature & options — to call via `ctx.invoke`             |
| P0       | `src/cli.py`                          | 334–360     | `debrief` signature                                                |
| P0       | `src/cli.py`                          | 615–660     | `execute` signature including `--revise`                           |
| P0       | `src/cli.py`                          | 1620–1660   | `reset` signature including `--yes`                                |
| P0       | `src/cli.py`                          | 3098–3175   | `projects` group + `projects_add` — invoke for unknown projects   |
| P1       | `src/cli.py`                          | 3145–3175   | Existing prompt/echo style to mirror (emoji, formatting)           |
| P1       | `src/config_loader.py`                | 283–290     | `get_all_projects()` shape (`Dict[str, Any]`)                      |
| P1       | `src/worktree_manager.py`             | 573–609     | `list_worktrees(project_key) -> list[str]`                         |
| P1       | `tests/test_cli_postmortems.py`       | 1–110       | Test pattern: `CliRunner` fixture + `runner.invoke(cli, [...])`    |
| P2       | `pyproject.toml`                      | 9–34        | Poetry deps section — where to add `questionary`                   |

**External Documentation:**

| Source | Section | Why Needed |
|--------|---------|------------|
| [questionary 2.0.1 — Question Types](https://questionary.readthedocs.io/en/stable/pages/types.html) | `select`, `text`, `confirm` | API reference for the three prompt types we use |
| [questionary GitHub](https://github.com/tmbo/questionary) | README usage examples | Cross-check `unsafe_ask()` vs `ask()` semantics for KeyboardInterrupt handling |
| [Click 8 — Context.invoke](https://click.palletsprojects.com/en/stable/api/#click.Context.invoke) | invoke / forward | Calling one Click command from another |

**KEY_INSIGHTS:**
- **questionary**: `Choice` objects let us decouple display label (`"DHLEXS_DHLEXC-311"`) from value (a tuple `("DHLEXS", "DHLEXC-311")`). Use `questionary.Separator()` for the `─────` divider above `+ new…`.
- **questionary**: `ask()` returns `None` if the user hits Ctrl-C; `unsafe_ask()` raises `KeyboardInterrupt`. We want `ask()` + early-return on `None` so Ctrl-C exits cleanly without a traceback.
- **Click**: `ctx.invoke(cmd, **kwargs)` calls the command's callback directly with kwargs (param names, not flag names — so `revise=True`, not `--revise`). Default values for un-passed kwargs are taken from the Click option defaults.
- **GOTCHA**: When `invoke_without_command=True` is set on a group, the group callback runs *before* any subcommand resolution. Guard with `if ctx.invoked_subcommand is None:` so that `sentinel plan ACME-142` does not also fire the menu.

---

## Patterns to Mirror

**GROUP_DEFINITION** (existing — extend, don't replace):

```python
# SOURCE: src/cli.py:183-191
# CURRENT:
@click.group()
@click.version_option(version=_get_version())
def cli() -> None:
    """Sentinel - Autonomous agent orchestration for Jira tickets.

    Sentinel automates the development workflow from Jira ticket to merge-ready code
    using specialized AI agents.
    """
    pass
```

**ECHO/PROMPT_STYLE** (mirror exactly):

```python
# SOURCE: src/cli.py:3145-3151
# COPY THIS PATTERN:
click.echo("➕ Add New Project\n")
project_key = click.prompt("JIRA project key").strip().upper()
git_url = click.prompt("Git origin URL (use HTTPS, not SSH)").strip()
default_branch = click.prompt("Default branch", default="main").strip()
```

```python
# SOURCE: src/cli.py:1654-1656 (reset error/confirm style)
# COPY THIS PATTERN:
click.echo(f"\n❌ Error: {e}", err=True)
```

**CLI_TEST_PATTERN:**

```python
# SOURCE: tests/test_cli_postmortems.py:40-42, 105-109
# COPY THIS PATTERN:
@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()

def test_postmortems_list_empty(runner: CliRunner, db_path: Path) -> None:
    """Empty postmortems table → "No postmortems matched." with exit code 0."""
    result = runner.invoke(cli, ["postmortems", "list"])

    assert result.exit_code == 0, result.output
```

**CTX_INVOKE_PATTERN** (no in-repo example — Click standard):

```python
# Click 8 standard
@click.pass_context
def cli(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is None:
        ctx.invoke(some_command, ticket_id="ACME-1", revise=True)
```

---

## Files to Change

| File                                | Action | Justification                                                    |
| ----------------------------------- | ------ | ---------------------------------------------------------------- |
| `pyproject.toml`                    | UPDATE | Add `questionary = "^2.0.1"` under `[tool.poetry.dependencies]` |
| `src/cli.py`                        | UPDATE | Add `invoke_without_command`, `_run_menu()`, helpers            |
| `tests/test_cli_menu.py`            | CREATE | Unit + CliRunner integration tests for the menu                  |

No other files touched. Existing commands (`plan`, `debrief`, `execute`, `reset`, `projects add`) are unmodified.

---

## NOT Building (Scope Limits)

- **No changes to `plan`/`debrief`/`execute`/`reset` business logic** — menu is a thin dispatch layer.
- **No `Plan --revise` option** — user explicitly dropped it; `plan` already auto-detects revision state per its docstring at `cli.py:218`.
- **No looping back to the menu after action completes** — exit after a single action (user-confirmed).
- **No multi-select / batch actions** — one ticket, one action per invocation.
- **No new project-removal flow from the menu** — `sentinel projects remove` stays as the path for that.
- **No remote-branch awareness in the ticket list** — list comes only from local worktrees, matching existing `sentinel status` semantics.
- **No new ticket fetching from Jira from the `+ new` flow** — the new ticket ID is just routed into the action menu; the action itself (plan/debrief) does the Jira fetch as it does today.
- **No autocomplete on the `+ new` text prompt** — plain text input. (Future-friendly: `questionary.autocomplete` could be wired in later if needed.)

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `pyproject.toml`

- **ACTION**: Add `questionary = "^2.0.1"` to `[tool.poetry.dependencies]`
- **MIRROR**: existing entries `pyyaml = "^6.0.1"`, `requests = "^2.31.0"` (line 9–34)
- **GOTCHA**: Do NOT regenerate `poetry.lock` from inside the Claude Code sandbox — note that `poetry lock && poetry install` must be run from the user's host or `sentinel-dev` container. State this explicitly in the task hand-off.
- **VALIDATE**: `grep -n questionary pyproject.toml` shows the new line; `python -c "import tomllib; tomllib.loads(open('pyproject.toml','rb').read().decode())"` parses without error.

### Task 2: UPDATE `src/cli.py` — add `invoke_without_command` to `cli()` group

- **ACTION**: Modify the `@click.group()` decorator at line 183 and add `@click.pass_context` + `ctx: click.Context` parameter; in the body, dispatch to `_run_menu(ctx)` when no subcommand was invoked.
- **MIRROR**: `src/cli.py:183-191` (current shape)
- **IMPLEMENT**:
  ```python
  @click.group(invoke_without_command=True)
  @click.version_option(version=_get_version())
  @click.pass_context
  def cli(ctx: click.Context) -> None:
      """Sentinel - Autonomous agent orchestration for Jira tickets.

      Run with no arguments for an interactive menu of loaded tickets.
      Use 'sentinel COMMAND --help' for non-interactive command details.
      """
      if ctx.invoked_subcommand is None:
          _run_menu(ctx)
  ```
- **GOTCHA**: The body MUST guard on `ctx.invoked_subcommand is None`, otherwise `sentinel plan ACME-1` will run BOTH the menu and `plan`.
- **VALIDATE**: `poetry run sentinel --help` still lists subcommands; `poetry run sentinel plan --help` still works (no menu side effect).

### Task 3: UPDATE `src/cli.py` — add `_run_menu` and helpers (private functions, near line 180)

- **ACTION**: Add four private helpers above `def cli(...)`:
  1. `_collect_loaded_tickets() -> list[tuple[str, str]]` — iterates `ConfigLoader.get_all_projects()` and `WorktreeManager.list_worktrees(key)`, returns `[(project_key, ticket_id), ...]` sorted alphabetically.
  2. `_prompt_ticket(tickets: list[tuple[str, str]]) -> tuple[str, str] | None` — questionary `select` of `Choice(title=f"{p}_{t}", value=(p, t))` followed by `Separator()` and `Choice("+ new…", value="__new__")`. Returns `None` on Ctrl-C.
  3. `_prompt_new_ticket(ctx: click.Context) -> tuple[str, str] | None` — questionary `text` prompt, parse `PREFIX-NUMBER`. If `PREFIX` is in `get_all_projects()`, return `(PREFIX, ticket_id)`. Otherwise echo the matching style and call `ctx.invoke(projects_add)` to add the project, then re-read config and return.
  4. `_prompt_action(label: str) -> str | None` — questionary `select` over `["Debrief", "Plan", "Execute", "Execute --revise", "Reset"]`. Returns the literal string or `None`.
- **IMPLEMENT** `_run_menu(ctx)`:
  ```python
  def _run_menu(ctx: click.Context) -> None:
      tickets = _collect_loaded_tickets()
      selection = _prompt_ticket(tickets)
      if selection is None:
          return  # Ctrl-C
      if selection == "__new__":
          selection = _prompt_new_ticket(ctx)
          if selection is None:
              return
      project_key, ticket_id = selection
      action = _prompt_action(f"{project_key}_{ticket_id}")
      if action is None:
          return
      _dispatch(ctx, project_key, ticket_id, action)
  ```
- **IMPLEMENT** `_dispatch(ctx, project_key, ticket_id, action)`:
  ```python
  if action == "Debrief":
      ctx.invoke(debrief, ticket_id=ticket_id, project=project_key)
  elif action == "Plan":
      ctx.invoke(plan, ticket_id=ticket_id, project=project_key)
  elif action == "Execute":
      ctx.invoke(execute, ticket_id=ticket_id, project=project_key)
  elif action == "Execute --revise":
      ctx.invoke(execute, ticket_id=ticket_id, project=project_key, revise=True)
  elif action == "Reset":
      if questionary.confirm(
          f"Reset {ticket_id}? This removes the worktree and local branch.",
          default=False,
      ).ask():
          ctx.invoke(reset, ticket_id=ticket_id, project=project_key, yes=True)
  ```
- **IMPORTS** to add at the top of `src/cli.py` (in the existing import block, alphabetical):
  ```python
  import questionary
  ```
- **MIRROR**: prompt/echo emoji style from `src/cli.py:3145-3151`.
- **GOTCHA 1**: `ctx.invoke` takes Python parameter names, not flag strings — `revise=True`, `yes=True`, `project=...` (matching the `def execute(...)` / `def reset(...)` signatures).
- **GOTCHA 2**: Use `questionary.<type>(...).ask()` (not `unsafe_ask()`) so Ctrl-C returns `None` and we can exit cleanly.
- **GOTCHA 3**: Empty `tickets` list — the `_prompt_ticket` choices should still include `+ new…`. If `tickets` is empty, the select prompt is shown with only the new entry.
- **GOTCHA 4**: `_prompt_new_ticket` parsing: `ticket_input.upper().split("-", 1)[0]` for project key derivation. Validate the input matches `^[A-Z][A-Z0-9_]+-\d+$` regex; on mismatch, echo `"❌ Invalid ticket ID format (expected PROJECT-NUMBER)"` and re-prompt or return `None`.
- **GOTCHA 5**: After `ctx.invoke(projects_add)` adds a new project, the existing `ConfigLoader` instance may be cached. Re-load via `get_config()` (whatever helper is used elsewhere) before checking project membership.
- **VALIDATE**: `poetry run python -c "from src.cli import _collect_loaded_tickets; print(_collect_loaded_tickets())"` runs without import errors.

### Task 4: UPDATE `src/cli.py` — verify imports

- **ACTION**: Confirm `questionary` import added (Task 3) and that `WorktreeManager`, `get_config`/`ConfigLoader`, and the four target commands (`plan`, `debrief`, `execute`, `reset`, `projects_add`) are accessible from the module scope. They already are — `cli.py` defines all of them.
- **VALIDATE**: `poetry run python -m compileall src/cli.py` exits 0.

### Task 5: CREATE `tests/test_cli_menu.py` — helper unit tests

- **ACTION**: Write unit tests for the four helpers using `monkeypatch` to fake `WorktreeManager.list_worktrees`, `ConfigLoader.get_all_projects`, and `questionary.select/text/confirm`.
- **MIRROR**: `tests/test_cli_postmortems.py` (CliRunner fixture pattern, lines 1–110).
- **TEST CASES**:
  - `_collect_loaded_tickets`: returns sorted `[(project, ticket), ...]` from mocked projects + worktrees.
  - `_collect_loaded_tickets`: returns `[]` when no projects configured.
  - `_prompt_new_ticket`: parses `ACME-142` into `("ACME", "ACME-142")` when project ACME exists.
  - `_prompt_new_ticket`: invokes `projects_add` when the parsed project is unknown.
  - `_prompt_new_ticket`: returns `None` for invalid input like `"garbage"`.
- **PATTERN**: monkeypatch `questionary.select` to return a stub object with `.ask()` returning a canned value:
  ```python
  class _Stub:
      def __init__(self, value): self._v = value
      def ask(self): return self._v
  monkeypatch.setattr("questionary.text", lambda *a, **k: _Stub("ACME-142"))
  ```
- **VALIDATE**: `poetry run pytest tests/test_cli_menu.py -v`

### Task 6: CREATE `tests/test_cli_menu.py` — CliRunner integration tests

- **ACTION**: Add tests in the same file that drive `runner.invoke(cli, [])` (bare invocation) end-to-end with all questionary prompts monkeypatched and `ctx.invoke` targets monkeypatched to record calls.
- **TEST CASES**:
  - Bare `sentinel` with no projects + user picks `+ new` + types `ACME-142` + picks `Plan` → `plan` callback called with `ticket_id="ACME-142"`, `project="ACME"`.
  - Bare `sentinel` with one ticket + user picks it + picks `Execute --revise` → `execute` called with `revise=True`.
  - Bare `sentinel` with one ticket + user picks `Reset` + confirm `False` → `reset` NOT called.
  - Bare `sentinel` with one ticket + user picks `Reset` + confirm `True` → `reset` called with `yes=True`.
  - `sentinel plan ACME-1` (with subcommand) does NOT fire the menu — verify by monkeypatching `_run_menu` to raise.
- **PATTERN**:
  ```python
  def test_menu_dispatches_plan(runner, monkeypatch):
      called = {}
      def fake_plan(ticket_id, project, **kw):
          called["plan"] = (ticket_id, project)
      monkeypatch.setattr("src.cli.plan.callback", fake_plan)
      # ... monkeypatch questionary.select, .text ...
      result = runner.invoke(cli, [])
      assert result.exit_code == 0
      assert called["plan"] == ("ACME-142", "ACME")
  ```
- **GOTCHA**: Click commands' actual function is `cmd.callback`, not `cmd` itself, when monkeypatching.
- **VALIDATE**: `poetry run pytest tests/test_cli_menu.py -v`

### Task 7: Manual smoke test (interactive)

- **ACTION**: Document the manual flow for the user to verify in `sentinel-dev`:
  1. `poetry install` (picks up `questionary`)
  2. `poetry run sentinel` → arrow-key pick a loaded ticket → pick `Plan` → confirm `plan` runs.
  3. `poetry run sentinel` → `+ new` → type a brand-new project's ticket ID → confirm `projects add` flow triggers, then action menu appears.
  4. `poetry run sentinel` → pick `Reset` → answer `n` at confirm → confirm nothing was reset.
  5. `poetry run sentinel plan --help` → confirm Click help still works (menu NOT triggered).
- **VALIDATE**: User reports each of the 5 steps works as described.

---

## Testing Strategy

### Unit Tests to Write

| Test File                  | Test Cases                                                                 | Validates                                  |
| -------------------------- | -------------------------------------------------------------------------- | ------------------------------------------ |
| `tests/test_cli_menu.py`   | `_collect_loaded_tickets`: empty, single, multi-project; sort order        | Ticket aggregation                         |
| `tests/test_cli_menu.py`   | `_prompt_new_ticket`: valid known project, valid unknown project, garbage  | Free-text parse + projects_add hand-off    |
| `tests/test_cli_menu.py`   | `_dispatch`: each of 5 actions routes to right Click command + kwargs      | Action wiring                              |
| `tests/test_cli_menu.py`   | Bare `cli` end-to-end via `CliRunner`: 4 happy paths + 1 negative          | `invoke_without_command` integration       |

### Edge Cases Checklist

- [ ] No projects configured → menu shows only `+ new…`
- [ ] Project configured but no worktrees → menu shows only `+ new…`
- [ ] Ctrl-C at ticket select → exit 0, no command invoked
- [ ] Ctrl-C at action select → exit 0, no command invoked
- [ ] Ctrl-C at Reset confirm → no reset performed
- [ ] User answers `n` to Reset confirm → no reset performed
- [ ] `+ new` with malformed ticket ID → graceful error, no crash
- [ ] `+ new` for unknown project → `projects add` fires; after add, action menu proceeds
- [ ] `sentinel plan ACME-1` (with subcommand) → menu NOT triggered (regression guard)
- [ ] `sentinel --help` → help still rendered (no menu)
- [ ] `sentinel --version` → version still printed (no menu)

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
poetry run python -m compileall src/cli.py
poetry run ruff check src/cli.py tests/test_cli_menu.py    # if ruff is in dev deps; otherwise skip
poetry run mypy src/cli.py                                  # if mypy is configured; otherwise skip
```

**EXPECT**: Exit 0.

### Level 2: UNIT_TESTS

```bash
poetry run pytest tests/test_cli_menu.py -v
```

**EXPECT**: All tests in this file pass.

### Level 3: FULL_SUITE

```bash
poetry run pytest tests/ -v
```

**EXPECT**: All previously-passing tests still pass — there should be zero regressions because no existing command was modified.

### Level 4: MANUAL_VALIDATION

(See Task 7.) Five manual smoke steps to be run by the user inside `sentinel-dev`.

---

## Acceptance Criteria

- [ ] Bare `sentinel` shows arrow-key ticket menu populated from loaded worktrees + `+ new…` entry
- [ ] Selecting a ticket opens an action menu with exactly: Debrief, Plan, Execute, Execute --revise, Reset
- [ ] Each action dispatches to the matching existing Click command with correct kwargs
- [ ] Reset prompts a y/N confirm before invoking; declining = no-op
- [ ] `+ new…` accepts free text, derives project key, and triggers `projects add` for unknown projects
- [ ] `sentinel <subcommand> …` invocations behave identically to before (no menu triggered)
- [ ] `sentinel --help` and `sentinel --version` behave identically to before
- [ ] Ctrl-C at any prompt exits cleanly with no traceback
- [ ] All tests in `tests/test_cli_menu.py` pass
- [ ] No regressions in the rest of `tests/`

---

## Completion Checklist

- [ ] Task 1 — `questionary` added to `pyproject.toml`
- [ ] Task 2 — `cli()` group has `invoke_without_command=True` and dispatches to `_run_menu`
- [ ] Task 3 — `_run_menu` + 4 helpers + `_dispatch` implemented
- [ ] Task 4 — Imports verified, file compiles
- [ ] Task 5 — Helper unit tests pass
- [ ] Task 6 — CliRunner integration tests pass
- [ ] Task 7 — Manual smoke validated by user
- [ ] User has run `poetry lock && poetry install` from host or sentinel-dev
- [ ] Level 1, 2, 3 validations pass
- [ ] Acceptance criteria met
- [ ] Plan file moved to `.claude/PRPs/plans/completed/` after merge

---

## Risks and Mitigations

| Risk                                                                                          | Likelihood | Impact | Mitigation                                                                                                                                       |
| --------------------------------------------------------------------------------------------- | ---------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `invoke_without_command=True` accidentally fires menu when a subcommand is given              | LOW        | HIGH   | Guard with `if ctx.invoked_subcommand is None` (Click's documented contract). Add explicit regression test (Task 6 last case).                   |
| `questionary` requires a TTY; tests would hang                                                | MEDIUM     | MEDIUM | Tests monkeypatch `questionary.<type>` entirely — never call the real prompt. Manual smoke covers the TTY path.                                  |
| `ctx.invoke(projects_add)` is itself interactive (uses `click.prompt`); nested prompts        | LOW        | LOW    | This is acceptable — it matches today's `sentinel projects add` UX. The nesting is one level deep and only on the unknown-project branch.        |
| Project-key parsing in `+ new` mis-derives for repos that name worktrees inconsistently       | LOW        | LOW    | Validate against `get_all_projects()`; on mismatch, route through `projects_add` rather than failing.                                            |
| Adding `questionary` increases install size                                                   | LOW        | LOW    | `questionary` depends only on `prompt_toolkit`, which is already a transitive dep of `click` in many environments. Acceptable.                   |
| `poetry.lock` regeneration cannot happen in this Claude Code sandbox                          | CERTAIN    | LOW    | Plan explicitly hands `poetry lock && poetry install` to the user (Task 1 gotcha + Task 7 step 1).                                              |

---

## Notes

- **Why `invoke_without_command=True` over a separate `sentinel menu` command**: bare `sentinel` is currently a dead-end (just help text). Routing it to the menu is the most discoverable UX. A `sentinel menu` command would still leave bare `sentinel` showing help. `--help` still works because Click's `--help` short-circuits before the group callback runs.
- **Why no looping**: user explicitly chose exit-after-action. This also keeps the menu "dumb" — it doesn't need to know about post-command state, output, or success/failure. The shell loop is the user's job.
- **Why we're not handling `Plan --revise`**: per `cli.py:218`'s docstring, `plan` already auto-detects revise state. Surfacing `--revise` would suggest two distinct behaviors when there's only one.
- **Future-friendly extension points** (NOT in this scope):
  - `questionary.autocomplete` over the loaded-ticket list (better than scrolling once there are >20 worktrees).
  - A `Status` action in the action menu that calls `ctx.invoke(status, project=...)`.
  - Breadcrumb / "back" navigation between menus (questionary supports this via separate prompts; not needed for v1).
