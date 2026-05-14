---
pr: "feature/cli-tool (no PR open yet)"
title: "feat(cli): interactive ticket+action menu when sentinel is run with no args"
author: "(local working tree)"
reviewed: 2026-05-14T19:45:00Z
recommendation: approve-with-comments
---

# Review: feature/cli-tool — Interactive CLI Menu

**Branch**: `feature/cli-tool` → `main`
**Files Changed**: 4 code files (+240 / −7) plus 4 docs/archive files
**No PR open yet** — review scoped to the cumulative working-tree diff against `origin/main`.

---

## Summary

Wraps Sentinel's existing Click commands in a two-step interactive `questionary` menu fired when `sentinel` is invoked with no arguments: pick a loaded ticket (one per active worktree across configured projects, plus a `+ new…` entry), then pick an action (`Debrief` / `Plan` / `Execute` / `Execute --revise` / `Reset`). Dispatches to the existing subcommands via `ctx.invoke` — zero business-logic changes.

The branch went through three rounds: original Ralph implementation, then two follow-up bugfix rounds driven by user smoke testing in `sentinel-dev` (ESC=back navigation, label double-stamping). The final code is tighter and more correct than the v1 in the implementation report.

Overall assessment: **functionally correct, well-tested, follows project patterns**. Two medium-priority items below are worth addressing before commit; nothing blocks merge.

---

## Implementation Context

| Artifact | Path |
|---|---|
| Implementation Report | `.claude/PRPs/reports/interactive-cli-menu-report.md` |
| Original Plan | `.claude/PRPs/plans/completed/interactive-cli-menu.plan.md` |
| Ralph Archive | `.claude/PRPs/ralph-archives/2026-05-14-interactive-cli-menu/` |
| Documented Deviations | None at v1; **2 undocumented follow-up rounds applied since** |

**Note**: The implementation report describes v1 (single-shot menu, `.ask()`, strict `PROJECT-NUMBER` parser). The current code has two rounds of user feedback applied beyond that — the report should be amended (see Medium #2 below).

---

## Changes Overview

| File | Changes | Assessment |
|---|---|---|
| `pyproject.toml` | +1 / −0 | PASS — adds `questionary = "^2.0.1"` correctly |
| `poetry.lock` | +44 / −2 | PASS — mechanical regen via `poetry lock`, picks up `questionary 2.1.1` + `prompt_toolkit` chain |
| `src/cli.py` | +197 / −5 | PASS — see findings |
| `tests/test_cli_menu.py` | +600 / −0 (new) | PASS — 36 tests, all pass |
| `.claude/PRPs/plans/completed/interactive-cli-menu.plan.md` | new (moved) | OK — checklist marked, implementation notes appended |
| `.claude/PRPs/reports/interactive-cli-menu-report.md` | new | **Stale** — predates the 2 follow-up rounds (see Medium #2) |
| `.claude/PRPs/ralph-archives/2026-05-14-interactive-cli-menu/` | new (3 files) | OK — same staleness as report |

---

## Issues Found

### Critical
None.

### High Priority
None.

### Medium Priority

- **`src/cli.py:_parse_ticket_input` (lines ~218–243)** — Latent ambiguity when two configured project keys share an underscore prefix.
  - **Why**: `text.partition("_")` only splits on the *first* `_`. For input `DHLEXS_DHLEXC-384` with **both** `DHLEXS` and `DHLEXS_DHLEXC` configured, the function returns `("DHLEXS", "DHLEXC-384")` — but the user almost certainly meant the longer-prefix project. The current single-project case from feedback round 3 (only `DHLEXS_DHLEXC` configured, not `DHLEXS`) works correctly because the partition match falls through to bare-form. The bug is dormant until someone configures both prefixes simultaneously.
  - **Fix**: Replace the partition heuristic with a longest-prefix-wins scan over `configured`:
    ```python
    configured = sorted(get_config().get_all_projects().keys(), key=len, reverse=True)
    for project_key in configured:
        for sep in ("-", "_"):
            prefix = f"{project_key}{sep}"
            if text.startswith(prefix):
                rest = text[len(prefix):] if sep == "_" else text
                if _TICKET_ID_RE.match(rest if sep == "_" else text):
                    return (project_key, rest if sep == "_" else text)
    ```
    Plus add a regression test covering the two-projects-share-prefix case. Cost: ~15 minutes; eliminates a class of future "why did the menu route this to the wrong project" reports.

- **`.claude/PRPs/reports/interactive-cli-menu-report.md`** — Implementation report is stale.
  - **Why**: The report describes the v1 feature (single-shot menu, `.ask()`, strict `PROJECT-NUMBER` parser, no back-nav, no display-format awareness). Two rounds of user feedback have shipped since: (a) `unsafe_ask()` migration with ESC=back / Ctrl-C=exit semantics + outer/inner loop, (b) `_parse_ticket_input` extension to accept `PROJECT_TICKETPREFIX-NUM` shape, (c) display label collapsed to bare `ticket_id`. None of these are documented.
  - **Fix**: Amend the report with a "Post-merge follow-ups" section listing the three changes above and the test additions (parser tests, ESC back-nav tests, real Ctrl-C test). Or add a `interactive-cli-menu-report-followups.md` next to it. Cost: ~10 minutes; prevents future archeology.

### Suggestions (Low Priority)

- **`src/cli.py:_prompt_ticket` return type** is `tuple[str, str] | str | None` — three-way discriminated union with the magic literal `"__new__"`. A typed sentinel would tighten the contract:
  ```python
  _NEW_TICKET: Final = "__new__"
  ```
  Then `tuple[str, str] | Literal["__new__"] | None`. Removes the `# type: ignore[misc]` at `_run_menu`'s tuple-unpack line and makes the menu's third state explicit. Cosmetic, not blocking.

- **`src/cli.py:_dispatch` Reset declined path** silently exits. After `confirmed=False` the menu prints nothing and the process returns. A user pressing `n` might wonder if it worked. One line of feedback would help:
  ```python
  if confirmed is None:
      return False
  if not confirmed:
      click.echo("Reset cancelled.")
      return True
  ```
  Minor UX polish.

- **`src/cli.py:_dispatch` action→command mapping** is an if/elif chain. A `dict[str, Callable]` would be marginally cleaner but the chain is 5 lines and readable. Skip unless you're already touching `_dispatch` for something else.

- **Test file naming**: `test_prompt_new_ticket_ctrl_c` (line ~190) actually tests ESC under the new `unsafe_ask` semantics — Ctrl-C now raises KeyboardInterrupt and is tested separately in `test_ctrl_c_anywhere_exits_cleanly`. Rename for honesty: `test_prompt_new_ticket_esc_returns_none`.

---

## Validation Results

| Check | Status | Details |
|---|---|---|
| Type check (compileall) | **PASS** | `python3 -m compileall src/cli.py tests/test_cli_menu.py` exits 0 |
| Lint (ruff, changed files) | **PASS for changes** | 10 hits in `src/cli.py`, all pre-existing in lines 3007/3151/3153/3322 (E741, F541, F841 in unrelated regions); **zero** in added lines 183–344 or in `tests/test_cli_menu.py` |
| Unit tests — menu file | **PASS** | `tests/test_cli_menu.py`: 36/36 |
| Unit tests — full suite | **PASS for this change** | 1065 pass / 26 fail. The 26 failures are **pre-existing** — verified earlier via `git stash` against `origin/main`; same set on `main`. Affects `test_environment_manager`, `test_jira_server_client`, `test_plan_generator`, `test_worktree_manager` — environmental fixture issues unrelated to this change. |
| Build / import smoke | **PASS** | `from src.cli import cli` succeeds; `runner.invoke(cli, ["--help"])` and `runner.invoke(cli, ["plan", "--help"])` exit 0 with expected output |
| Manual smoke (TTY) | **N/A in sandbox** | Has been driven twice by the user in `sentinel-dev`; both bugs found there are fixed in the current code |

---

## Pattern Compliance

- [x] Follows `@click.group()` / `@cli.command()` decorator pattern from existing CLI surface
- [x] `invoke_without_command=True` correctly guarded by `if ctx.invoked_subcommand is None` — regression test in place
- [x] Echo style mirrors existing `cli.py:3145–3151` (`click.echo` + emoji prefixes `❌`, `ℹ️`, `✅`)
- [x] CLI test idiom mirrors `tests/test_cli_postmortems.py` (`CliRunner` fixture + `runner.invoke(cli, [...])`)
- [x] Imports grouped correctly (third-party `click`, `questionary` together; project imports below)
- [x] Type hints present on all public-ish helpers
- [x] No new files created beyond what the plan called for
- [x] No business-logic changes to `plan`, `debrief`, `execute`, `reset`, `projects add` — purely a dispatch layer (verified by reading the diff)

---

## Notable Behavioral Properties

These are properties worth knowing for future maintainers:

1. **ESC vs Ctrl-C semantics**: `_run_menu` uses `unsafe_ask()` everywhere. ESC returns `None` (treated as "back one step"). Ctrl-C raises `KeyboardInterrupt` (caught at the top of `_run_menu`, exits cleanly with a trailing newline). Two `_Stub`-based tests pin both behaviors.

2. **Action sub-loop**: ESC at the Reset confirm re-prompts the action menu; ESC at the action menu re-prompts the ticket select; ESC at the ticket select exits. Pinned by `test_esc_at_reset_confirm_re_prompts_action` and `test_esc_at_action_returns_to_ticket_select`.

3. **Display label is `ticket_id` verbatim** (not `f"{project}_{ticket}"`). The worktree dirname is the source of truth and prepending the project key would double-stamp prefixes for projects whose tickets already include the project key (the bug from feedback round 3). This is the right call but means the menu does NOT visually disambiguate two tickets with the same `ticket_id` across different projects — that case is rare in practice but worth noting.

4. **Dispatch invariants**: `(project_key, ticket_id)` selected from the menu is always passed unchanged to `ctx.invoke`. So whatever shape the worktree has on disk, the existing commands operate on that same shape — no mismatch risk.

5. **`+ new` round-trip**: A user can re-type a label they saw in the menu (e.g. `DHLEXS_DHLEXC-384`) and the parser resolves it correctly *as long as the project's prefix is configured*. If the project isn't configured, `projects_add` is invoked to add it; the parser then re-reads the config.

---

## What's Good

- **Test coverage is genuine.** 36 tests covering not just the happy path but the navigation graph (4 ESC scenarios), the parser's two input shapes, dispatch wiring per action (parametrized), and a regression test pinning the `invoke_without_command` guard. The `_Stub` queue model handles multi-prompt flows cleanly — earlier per-call rebuild was caught and fixed.
- **Iteration discipline.** Two rounds of real user feedback in `sentinel-dev` were turned into focused fixes plus regression tests, not workarounds. The display-format change in particular went through three iterations to land on the right rule (just `ticket_id` verbatim) instead of a clever-but-wrong "starts-with-project" heuristic.
- **Scope discipline.** Plan said "no business logic changes"; diff confirms it. No existing command's signature, behavior, or error handling moved.
- **`unsafe_ask()` migration was the right move.** Distinguishing ESC from Ctrl-C is the only way to support back-navigation cleanly, and the production code structure (try/except KI at the top + None-as-back inside) is clean.
- **Sentinel string for `+ new`** keeps the menu's value type small and testable — no need for a parallel "is this the new entry?" flag.
- **Plan archived correctly** to `.claude/PRPs/plans/completed/` with checklist updates and an "Implementation Notes" appendix.

---

## Recommendation

**APPROVE WITH COMMENTS**

The two medium-priority items are quality-of-life improvements, not blockers:
- The `_parse_ticket_input` ambiguity is dormant in current configurations and easy to fix later.
- The stale implementation report is documentation hygiene.

Suggest the author:
1. Apply the longest-prefix-wins parser fix (15 min) **before commit** if it's quick to do; otherwise file as a follow-up issue.
2. Append a "Post-merge follow-ups" note to the implementation report (10 min).
3. Optionally apply the cosmetic suggestions (sentinel constant, Reset-cancelled feedback line, test rename).
4. Commit the four files + the docs (`pyproject.toml`, `poetry.lock`, `src/cli.py`, `tests/test_cli_menu.py`, `.claude/PRPs/...`), open the PR from `sentinel-dev`/host (this sandbox can't push), and run the 5 manual smoke steps from plan Task 7 with `poetry install` first to pick up `questionary`.

No blockers. Ready to merge after the documentation amendment and (preferably) the parser tightening.

---

## Posting to GitHub

`gh` is installed but unauthenticated in this sandbox, and **no PR exists yet** for `feature/cli-tool` (only the prior `9fa3cd9 cli flow plan` commit is upstream). When you commit + push + open the PR from `sentinel-dev` or host, run:

```bash
gh pr comment <NUM> --body-file .claude/PRPs/reviews/feature-cli-tool-review.md
```

Or copy the relevant sections inline. Per CLAUDE.md, posting to GitHub from this sandbox is not possible.

---

*Reviewed by Claude (Opus 4.7, 1M context)*
*Report: `.claude/PRPs/reviews/feature-cli-tool-review.md`*
