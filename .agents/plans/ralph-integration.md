# Add Ralph-style iteration loop to Sentinel's execute flow

## Context

Sentinel's `execute` command has a dev→security review loop, but it has key gaps compared to PRP Ralph's autonomous iteration model:

1. **No cross-iteration memory** — Developer agent re-parses the plan from scratch every iteration. No knowledge of what succeeded/failed previously.
2. **No validation gates beyond tests** — Only `run_tests()` is called. Lint and type-check (described in the developer system prompt) are never executed by the orchestrator.
3. **Security findings never reach the developer** — Findings become beads tasks but are never injected into the developer's prompt context on the next iteration.
4. **Weak completion criteria** — `sec_result["approved"]` alone gates exit. Partially complete work can slip through.

**Why not use PRP Ralph directly?** Ralph is a Claude Code session plugin that uses hooks (stop hook checks for `<promise>COMPLETE</promise>`) and `.claude/` state files. Sentinel spawns Claude via Agent SDK as a subprocess — fundamentally different execution model. Ralph can't be plugged in; its concepts must be adapted.

## Approach: Adapt Ralph's concepts into Sentinel's existing architecture

No new execution models, no new agent types, no new dependencies. Four targeted changes.

## Changes

### 1. New module: `src/iteration_state.py`

Dataclass that accumulates state across iterations (replaces Ralph's file-based state with in-memory object — simpler, since execute is a single process).

```python
@dataclass
class ValidationResult:
    lint: CommandResult | None       # None = skipped (tool not available)
    typecheck: CommandResult | None
    tests: dict                      # existing test_results format
    all_passed: bool

@dataclass
class IterationEntry:
    iteration: int
    tasks_completed: list[str]
    tasks_failed: list[tuple[str, str]]  # (task, error)
    validation: ValidationResult
    security_findings: list[dict]

@dataclass
class IterationState:
    max_iterations: int
    entries: list[IterationEntry]

    def completed_tasks(self) -> set[str]:
        """Tasks that succeeded in any previous iteration."""

    def summarize_for_prompt(self) -> str:
        """Concise text summary for injection into developer prompt."""

    def is_complete(self, security_approved: bool) -> bool:
        """All tasks done + all validations pass + security approved."""
```

### 2. Modify `src/agents/base_developer.py`

**a) Add abstract methods for lint/typecheck:**
```python
@abstractmethod
def _get_lint_command(self) -> list[str]: ...

@abstractmethod
def _get_typecheck_command(self) -> list[str] | None: ...  # None = not applicable
```

**b) Add `run_validations()` method:**
Runs lint → typecheck → tests in sequence. If lint/typecheck command fails with `FileNotFoundError` or exit code 127 (not installed), log warning and treat as skipped (not failed). Returns `ValidationResult`.

**c) Modify `run()` to accept `iteration_state`:**
- `iteration_state: IterationState | None = None` (backward compatible)
- When present: skip tasks already in `iteration_state.completed_tasks()`
- Pass iteration context through the `context` dict to `_build_tdd_prompt()`

### 3. Modify stack-specific developer agents

**`src/agents/drupal_developer.py`:**
```python
def _get_lint_command(self) -> list[str]:
    return ["vendor/bin/phpcs", "--standard=Drupal,DrupalPractice", "--extensions=php,module,inc,install,theme"]

def _get_typecheck_command(self) -> list[str] | None:
    return None  # PHPStan optional in Drupal
```
Modify `_build_tdd_prompt()` to include "Previous Iteration Context" section when `context` is non-empty.

**`src/agents/python_developer.py`:**
```python
def _get_lint_command(self) -> list[str]:
    return ["ruff", "check", "."]

def _get_typecheck_command(self) -> list[str] | None:
    return ["mypy", "."]
```
Same `_build_tdd_prompt()` modification.

### 4. Modify execute loop in `src/cli.py` (lines 485-535)

```python
state = IterationState(max_iterations=max_iterations, entries=[])

for iteration in range(1, max_iterations + 1):
    # Developer implements (skipping previously completed tasks)
    dev_result = developer.run(plan_file=plan_file, worktree_path=worktree_path,
                               iteration_state=state)

    # Full validation gates
    validation = developer.run_validations(worktree_path)
    # Log: lint PASS/FAIL, typecheck PASS/FAIL/SKIP, tests PASS/FAIL

    # Security review
    sec_result = security.run(...)

    # Record iteration
    state.entries.append(IterationEntry(iteration=iteration, ...))

    # Explicit completion: all three must hold
    if state.is_complete(sec_result["approved"]):
        break
    elif sec_result["approved"] and not validation.all_passed:
        click.echo("Security approved but validations failing — continuing")
    elif not sec_result["approved"]:
        # existing beads task creation for findings (unchanged)
        ...
```

## Files to modify

| File | Action |
|------|--------|
| `src/iteration_state.py` | **New** — dataclasses for iteration state, validation results |
| `src/agents/base_developer.py` | Add `_get_lint_command()`, `_get_typecheck_command()` abstract methods, `run_validations()`, modify `run()` for iteration state |
| `src/agents/drupal_developer.py` | Implement lint/typecheck methods, add iteration context to TDD prompt |
| `src/agents/python_developer.py` | Implement lint/typecheck methods, add iteration context to TDD prompt |
| `src/cli.py` | Wire `IterationState` through execute loop, enforce explicit completion |
| `tests/test_iteration_state.py` | **New** — unit tests for state tracking, completion logic |
| `tests/test_base_developer.py` | Update for new abstract methods and `run_validations()` |

## What this deliberately excludes (YAGNI)

- **File-based state persistence** — Ralph needs it for cross-session continuity. Sentinel execute is a single process.
- **Archive system** — Ralph archives to `.claude/PRPs/`. Sentinel logs via Python logging + beads.
- **Hook-based continuation** — Ralph's stop hook is Claude Code specific. Sentinel uses a Python `for` loop.
- **Configurable validation commands per project** — Start with hardcoded per-stack. Add config later if needed.

## Implementation order

1. `src/iteration_state.py` + tests (zero dependencies)
2. `src/agents/base_developer.py` (add abstract methods + `run_validations()`)
3. `src/agents/drupal_developer.py` + `python_developer.py` (implement new methods)
4. `src/cli.py` (wire it all together)
5. Integration test

## Verification

1. `python -m pytest tests/test_iteration_state.py -v`
2. `python -m pytest tests/test_base_developer.py tests/test_drupal_developer.py tests/test_python_developer.py -v`
3. Manual: run `sentinel execute` on a ticket — verify lint/typecheck output appears, iteration context is passed, completion criteria enforced
