# Feature: Carry Verifier Failures Across Iteration Boundaries

## Summary

When iteration N of `run_implementation_plan` produces failed tasks, accumulate the structured test errors from those tasks and inject them at the top of every task prompt in iteration N+1 as additional acceptance criteria: *"During the previous iteration, these existing tests failed. Treat them as additional acceptance criteria — your work isn't done until they pass too."* Reuses the same prompt-augmentation channel that already carries reviewer feedback to the developer. Closes the verifier feedback loop across iteration boundaries — currently iteration N+1 starts blind to iteration N's regressions and reproduces the same failures verbatim.

## User Story

As a Sentinel operator running a multi-iteration plan
I want test failures from iteration N to be visible to iteration N+1's developer agent
So that the agent can fix prior-iteration regressions on top of its normal task work, instead of repeating identical failures every iteration

## Problem Statement

The verifier-retry loop within a single task (`max_attempts=3`) feeds test failures back to the same task's developer prompt. But when the developer agent gives up — either because retries exhausted or because the failing tests aren't fixable from within the current task's scope — the failure data is **lost at the iteration boundary**.

Observed today on DHLEXS_DHLEXC-311:

- Iteration 1 ends with 0/7 tasks succeeded, 20 specific test failures (wrong path in 5 tests, missing `protected static $modules` in functional tests, missing role config in kernel tests).
- Iteration 2 starts. Developer prompts contain the original plan, and nothing else. Same plan → same agent decisions → same test files written with same bugs.
- Tests fail again — same line numbers, same error messages, same 4 errors + 16 failures.
- This repeats up to `max_iterations=5`, producing zero progress while consuming ~30 minutes of agent time per iteration.

The reviewer-feedback channel already carries structured findings from the drupal/security reviewers into the developer's revision prompt. The verifier produces equivalently structured errors but those errors don't follow the same path.

## Solution Statement

In `run_implementation_plan`'s iteration loop, after each iteration that produces failed tasks, capture the union of structured test errors. When the next iteration starts, prepend a "Prior Iteration Regressions" section to every task's developer prompt (via the existing prompt-augmentation channel). The section lists the failing test classes/methods, error type, message, and source file/line — enough for the agent to identify and fix without rerunning everything.

Pairs cleanly with `verifier-changed-files-scope.plan.md` (plan A): A keeps each task's verifier honest within an iteration; B ensures cross-iteration regressions actually get paid down. Either can ship without the other; together they close the loop.

## Metadata

| Field            | Value                                                        |
| ---------------- | ------------------------------------------------------------ |
| Type             | ENHANCEMENT                                                  |
| Complexity       | LOW-MEDIUM                                                   |
| Systems Affected | base developer (iteration loop, prompt assembly), CLI iteration logging |
| Dependencies     | Pairs with plan A but ships independently                    |
| Estimated Tasks  | 5                                                            |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════╗
║                           BEFORE STATE                              ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                     ║
║   Iteration 1: 7 tasks → 4 errors + 16 failures, 0/7 succeed        ║
║     │                                                               ║
║     │ (failure data discarded at iteration boundary)                ║
║     ▼                                                               ║
║   Iteration 2: 7 tasks (same plan, blank slate)                     ║
║     → developer rewrites same broken tests                          ║
║     → SAME 4 errors + 16 failures, 0/7 succeed                      ║
║     │                                                               ║
║     │ (failure data discarded again)                                ║
║     ▼                                                               ║
║   Iteration 3: same outcome                                         ║
║   ...                                                               ║
║   Iteration 5: cap-out, postmortem written, 0 commits               ║
║                                                                     ║
║   Result: 5 iterations × ~30 min = 2.5 hr producing nothing         ║
║                                                                     ║
╚═══════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════╗
║                            AFTER STATE                              ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                     ║
║   Iteration 1: 7 tasks → 4 errors + 16 failures, 0/7 succeed        ║
║     │                                                               ║
║     │ Failures captured into RegressionContext                      ║
║     ▼                                                               ║
║   Iteration 2: every task prompt prepends:                          ║
║                                                                     ║
║     ## Prior Iteration Regressions                                  ║
║                                                                     ║
║     The previous iteration left 20 tests failing. Treat fixing      ║
║     them as additional acceptance criteria for your task — your     ║
║     work isn't done until your task passes AND these are green:     ║
║                                                                     ║
║     - [error] ResponsivePreviewWebmasterConfigTest::                ║
║       testWebmasterRoleConfigHasResponsivePreviewModuleDependency   ║
║       File "/app/web/config/sync/user.role.webmaster.yml"           ║
║       does not exist (Symfony\Yaml\ParseException)                  ║
║       at tests/src/Unit/ResponsivePreviewWebmasterConfigTest:102    ║
║     - [error] ResponsivePreviewToolbarTest::                        ║
║       testAnonymousUserCannotAccessResponsivePreview                ║
║       Status code 404, expected 200                                 ║
║       at tests/src/Functional/ResponsivePreviewToolbarTest:207      ║
║     - ...                                                           ║
║                                                                     ║
║     → developer reads the regressions, fixes path bugs, fixes       ║
║       missing module declarations, fixes role config setup          ║
║     → tests pass; tasks commit                                      ║
║                                                                     ║
║   Result: iteration 2 actually closes the loop instead of           ║
║   reproducing iteration 1's mistakes verbatim.                      ║
║                                                                     ║
╚═══════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location | Before | After | User Impact |
|----------|--------|-------|-------------|
| Task prompt in iter 2+ | Plan only | Plan + `## Prior Iteration Regressions` block | Agent has the data to act |
| Iteration loop | Discards failures at boundary | Accumulates → injects | Loop converges instead of looping |
| CLI output | "Iteration 2/5: 0/7 succeeded" repeating | Should see decreasing failure count across iterations | Visible progress signal |
| Postmortem on cap-out | Same shape | Same shape (regression context isn't persisted) | No change |

---

## Mandatory Reading

Before implementing, read these files end-to-end:

1. **`src/agents/base_developer.py`** — focus on `run_implementation_plan` (the iteration loop), `implement_feature` (per-task entry), and the path that delivers reviewer feedback into developer prompts on revision (search for `unresolved discussions` / `discussion_handler` / wherever reviewer findings flow back).
2. **`src/agents/_structured_errors.py`** — what a `StructuredError` looks like; this is the data we accumulate and render.
3. **`src/agents/drupal_developer.py`** — `_parse_test_output` produces the structured errors from JUnit XML; that's our data source.
4. **`src/core/execution/post_execute.py`** — where `DeveloperCappedOut` postmortems get written; the regression-context data should NOT collide with this (postmortems are persisted; regressions are ephemeral, per-execution).
5. **`tests/test_drupal_developer.py` + `tests/agents/test_drupal_static_checks.py`** — patterns for asserting on prompt content the developer agent sees.

## Patterns to Mirror

- **Reviewer-feedback into developer revision** — when the drupal_reviewer or security_reviewer produces blocker findings, those flow into the next developer call's prompt under a clearly delimited section. Find that exact code path; the new "Prior Iteration Regressions" section is an exact analogue, just with a different source.
- **Structured-error rendering** — `_structured_errors.py` already has helpers that turn `StructuredError` into human-readable strings. Reuse, don't reinvent.
- **Existing `## Operator Instruction` injection** (from `agent-prompt-injection.plan.md`) — same pattern: a clearly delimited markdown section prepended to the user prompt before the SDK call.

## Files to Change

| File | Change |
|------|--------|
| `src/agents/base_developer.py` | Track `RegressionContext` across the iteration loop in `run_implementation_plan`. Thread it into `implement_feature` → SDK call. |
| `src/agents/base_developer.py` (new helper) | `_render_regression_section(errors: list[StructuredError]) -> str` — pure function, easy to unit test. |
| `src/cli.py` | Optional: in iteration-summary output, print "carrying N regressions into iteration N+1" so operators can see the loop is using the data. |
| `tests/test_base_developer.py` (or new) | Unit test `_render_regression_section`. Integration test: simulate iter 1 with failures, assert iter 2's task prompt contains the regressions block. |

## NOT Building (Scope Limits)

- **Persisting regressions to the postmortems table** — regressions are ephemeral acceptance criteria, not lessons. Postmortems remain `DeveloperCappedOut`-only.
- **Cap on regression list size / token budget** — initial cut just dumps all errors; if it ever exceeds prompt budget we'll add truncation. (Mention the renderer can grow a `max_chars` arg later.)
- **Filtering "this regression is already fixed by my plan"** — out of scope. The agent decides what's still relevant; we just provide the data.
- **Cross-execution regressions** (carrying failures from a previous `sentinel execute` run into a later one) — out of scope. Per-execution only.

---

## Step-by-Step Tasks

### Task 1: ADD `RegressionContext` dataclass and renderer to `src/agents/base_developer.py`

```python
@dataclass
class RegressionContext:
    """Test failures that survived the prior iteration of an execution.
    Injected into every task prompt in the next iteration as additional
    acceptance criteria. Ephemeral — never persisted, never crosses
    execution boundaries.
    """
    iteration_n: int       # the iteration that produced these failures
    errors: list[StructuredError]

    def is_empty(self) -> bool:
        return not self.errors


def _render_regression_section(ctx: RegressionContext) -> str:
    """Render the regression context as a markdown block ready to
    prepend to a developer task prompt. Empty context returns ''."""
    if ctx.is_empty():
        return ""
    lines = [
        "## Prior Iteration Regressions",
        "",
        f"The previous iteration ({ctx.iteration_n}) left "
        f"{len(ctx.errors)} test(s) failing. Treat fixing them as "
        f"additional acceptance criteria for your task — your work "
        f"isn't done until your task passes **and** these are green:",
        "",
    ]
    for err in ctx.errors:
        # use existing structured-error rendering
        lines.append(f"- {err.render_oneline()}")  # adapt to actual API
    return "\n".join(lines) + "\n"
```

Pure function; easy to unit-test with synthetic `StructuredError` instances.

### Task 2: ACCUMULATE failures across the iteration loop in `run_implementation_plan`

The iteration loop already runs each iteration and produces a result with task pass/fail. After each iteration:

```python
regressions = RegressionContext(iteration_n=iteration, errors=[])
# ... existing iteration body that runs tasks ...

# Collect structured errors from any failed task results in this iteration
all_errors: list[StructuredError] = []
for task_result in iteration_results:
    if not task_result.get("success"):
        all_errors.extend(task_result.get("structured_errors", []))

# Dedupe by (test_class, test_method, error_type, line) — same test
# failing in three task runs shouldn't appear three times
deduped = _dedupe_structured_errors(all_errors)
regressions = RegressionContext(iteration_n=iteration, errors=deduped)

# Pass into next iteration via local var; loop naturally has access.
```

The next iteration's `implement_feature` call gets `regressions` as a kwarg.

### Task 3: PREPEND the regression section to each task's prompt in `implement_feature`

Find where the task prompt is assembled before the agent-SDK call. Prepend the rendered section:

```python
def implement_feature(self, task, ..., regressions: Optional[RegressionContext] = None):
    prompt_parts: list[str] = []
    if regressions and not regressions.is_empty():
        prompt_parts.append(_render_regression_section(regressions))
    prompt_parts.append(task_prompt_body)
    final_prompt = "\n\n".join(prompt_parts)
    # ... existing SDK call with final_prompt ...
```

This piggybacks on whatever existing pattern reviewer-feedback uses (cf. **Patterns to Mirror**); the right answer is to plug into the *same* assembly point, not a new one.

### Task 4: SURFACE the regression count in iteration-summary output (CLI)

In `src/cli.py`'s execute output, when starting iteration N>1 with non-empty regressions, log:

```
Iteration 2/5
   Carrying 20 regressions from iteration 1 into developer prompts
```

Operators can then see the loop is *using* the data, and falling counts across iterations are the convergence signal.

### Task 5: TESTS

Unit:
- `_render_regression_section` with empty context → ''
- `_render_regression_section` with N errors → contains header, count, all errors
- `_dedupe_structured_errors` collapses same (class, method, type, line)

Integration (mock-based):
- Simulate `run_implementation_plan` running 2 iterations
- Iteration 1 produces 3 failed tasks with overlapping errors
- Assert iteration 2's `implement_feature` gets called with a `RegressionContext` containing the deduped errors
- Assert the prompt seen by the SDK call in iteration 2 contains "## Prior Iteration Regressions"

### Task 6: Manual Verification

Run a multi-iteration ticket where iteration 1 reliably fails (e.g. force a path bug). Verify:
- Iteration 2's developer prompt visibly contains the regression block in agent-SDK debug logs
- Iteration 2 produces fewer failures than iteration 1 (the fix-loop is converging)
- Cap-out postmortem is unchanged in shape (regressions are ephemeral)

## Testing Strategy

### Verification Approach

- **Unit-level**: rendering and deduping are pure functions, mock-free.
- **Integration-level**: mock `_env_manager.exec` and the agent SDK to simulate two iterations; assert prompt content seen by the SDK call.
- **Smoke**: re-run DHLEXS_DHLEXC-311 (the case that motivated this plan) and observe iter 2 produces strictly fewer failures than iter 1.

### Edge Cases Checklist

- Iteration 1 has all tasks pass → no regressions → iteration 2 prompt is unchanged
- Iteration 1 has overlapping errors across tasks → dedup collapses them
- Iteration 1 errors exceed reasonable token budget → renderer should truncate (initial cut: dump all; add `max_chars` if it bites)
- A task in iteration 2 fixes some regressions, leaves others → iteration 3 inherits the *remaining* set, not the iteration-1 set
- All iterations fail → cap-out path triggers normally; postmortem written; regressions discarded with the execution
- Reviewer-feedback already in the prompt → both blocks coexist (regressions + reviewer findings); no collision

## Validation Commands

### Level 1: STATIC ANALYSIS

```bash
cd /workspace/sentinel
python3 -c "import ast; ast.parse(open('src/agents/base_developer.py').read())"
ruff check src/agents/base_developer.py src/cli.py
```

### Level 2: UNIT TESTS

```bash
cd /workspace/sentinel
pytest tests/test_base_developer.py -q  # or wherever the tests land
```

### Level 3: INTEGRATION SMOKE

Run the motivating ticket end-to-end. Inspect:

```bash
sentinel execute DHLEXS_DHLEXC-311 2>&1 | tee /tmp/execute.log
grep "Carrying.*regressions" /tmp/execute.log
grep "Prior Iteration Regressions" /app/logs/*  # if SDK debug logs are dumped
```

Expect: iter 2+ logs the carry-count; iter 2's failure count < iter 1's.

## Implementation Order

1. Task 1 (pure helpers — RegressionContext + renderer + dedup; TDD-friendly)
2. Task 5 (unit tests for the helpers — TDD)
3. Task 2 (accumulation in iteration loop)
4. Task 3 (prompt prepend — plugs into existing reviewer-feedback assembly point)
5. Task 4 (CLI surface — small, last)
6. Task 6 (manual verification on the motivating ticket)

## Composition with Plan A

If shipping alongside `verifier-changed-files-scope.plan.md`:
- A reduces *intra-iteration* failure noise (each task's verifier sees only its own code)
- B ensures *inter-iteration* regressions don't get silently dropped
- Together: the verifier-retry loop within a task fixes that task's own regressions; the cross-iteration channel fixes regressions a single task can't address. Both layers needed for true convergence.

If shipping standalone, B still helps: even with broad-scope verifier, the agent at least *sees* what failed last time and can act on it.
