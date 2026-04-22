# Feature: Refactor `sentinel execute` initial flow to "run once, then review-revise loop"

## Summary

The `sentinel execute` initial flow (no `--revise`) currently calls `developer.run(plan_file=…)` at the top of every outer iteration. When security or Drupal review vetoes, the Bucket-C patch calls `developer.run_revision(user_prompt=fix_prompt)` and `continue`s — but this has two bugs that compound: **(a)** the next iteration re-runs `developer.run(plan_file=…)` which re-implements the plan from scratch and can overwrite the targeted fix, and **(b)** `run_revision()` early-exits when there is no MR yet (there isn't one at this point — the MR is created *after* review approves), so the `user_prompt` is silently discarded. Result: on Drupal/security veto the "fix" loop is effectively a no-op that just burns max_iterations.

The fix is to reshape the initial flow to match the `--revise` flow's structure: call `developer.run(plan_file=…)` **once** before the review loop, then let the loop apply targeted fixes via a new thin primitive `developer.apply_feedback(feedback, worktree_path)` that wraps `implement_feature` — no plan re-parsing, no MR dependency. Out of scope: changing `developer.run()` or `developer.run_revision()` semantics; changing the `--revise` flow.

## User Story

As a Sentinel operator running `sentinel execute <ticket>` on a Drupal ticket
I want security/Drupal findings to actually feed back into the next developer iteration
So that veto-and-retry converges in practice instead of silently repeating the same implementation until max_iterations is exhausted.

## Problem Statement

Concretely testable statement: given an initial `execute` run where the security or Drupal reviewer emits `approved=False` with N findings, the next developer iteration must produce a diff that addresses at least one of those findings. On HEAD today, the diff is either (a) identical to the previous iteration because `run_revision()` no-opped and `developer.run(plan_file)` re-implemented from the unchanged plan, or (b) a regression that undoes `run_revision()`'s targeted changes.

## Solution Statement

1. Call `developer.run(plan_file=…, user_prompt=prompt)` **once** before the review loop (mirrors revise flow's `run_revision()` call site).
2. Add `BaseDeveloperAgent.apply_feedback(feedback, worktree_path, *, commit_prefix="fix")` — a thin wrapper that calls `implement_feature(task=<short summary>, context={}, worktree_path=…, commit_prefix="fix", user_prompt=<full feedback>)` and `commit_changes(…)` on the resulting files. No MR fetch, no discussion classification, no plan re-parse.
3. Replace the outer "N iterations, re-run from plan each time" loop with a review-revise loop: security → Drupal → if either vetoes, call `apply_feedback` with the concatenated findings, re-review.
4. Remove the duplicated config-retry-at-CLI-level; `developer.run()` already has an internal retry (`base_developer.py:699-742`).

## Metadata

| Field            | Value                                                                                                 |
| ---------------- | ----------------------------------------------------------------------------------------------------- |
| Type             | REFACTOR                                                                                              |
| Complexity       | MEDIUM                                                                                                |
| Systems Affected | `src/cli.py` (execute command), `src/agents/base_developer.py` (new primitive), `tests/test_base_developer.py` or new `test_cli_execute.py` |
| Dependencies     | Existing: `BaseDeveloperAgent.implement_feature`, `commit_changes`, `SecurityReviewerAgent.run`, `DrupalReviewerAgent.run`. No new third-party libs. |
| Estimated Tasks  | 6                                                                                                     |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║  OUTER LOOP: for iteration in range(1, max_iterations + 1)                    ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   ┌──────────────────────────────────────────────────────────────────────┐    ║
║   │ Iteration 1                                                          │    ║
║   │  ┌────────────────┐  ┌───────────┐  ┌──────────────┐                 │    ║
║   │  │ developer.run  │→ │ security  │→ │ drupal_review │ VETO           │    ║
║   │  │ (plan_file,    │  │ PASS      │  │ findings=3    │    │           │    ║
║   │  │  prompt)       │  └───────────┘  └──────────────┘    ▼           │    ║
║   │  └────────────────┘                              ┌─────────────┐    │    ║
║   │  (full impl from                                 │ run_revision │    │    ║
║   │   scratch — ~5-10min)                            │ (no-op: no   │    │    ║
║   │                                                  │ MR exists)   │    │    ║
║   │                                                  └─────────────┘    │    ║
║   │                                                         │            │    ║
║   │                                                         ▼            │    ║
║   │                                                     continue         │    ║
║   └──────────────────────────────────────────────────────────────────────┘    ║
║                                                                               ║
║   ┌──────────────────────────────────────────────────────────────────────┐    ║
║   │ Iteration 2                                                          │    ║
║   │  ┌────────────────┐  ┌───────────┐  ┌──────────────┐                 │    ║
║   │  │ developer.run  │→ │ security  │→ │ drupal_review │ SAME VETO      │    ║
║   │  │ (plan_file,    │  │ PASS      │  │ findings=3    │                │    ║
║   │  │  prompt)       │  └───────────┘  └──────────────┘                 │    ║
║   │  │ ← re-does the  │                                                  │    ║
║   │  │   whole plan;  │                                                  │    ║
║   │  │   no knowledge │                                                  │    ║
║   │  │   of findings  │                                                  │    ║
║   │  └────────────────┘                                                  │    ║
║   └──────────────────────────────────────────────────────────────────────┘    ║
║   ... iterations 3..N, each a re-implementation; same findings every time    ║
║                                                                               ║
║   USER_FLOW:  run → review → veto → full-re-impl → review → same veto → …    ║
║   PAIN_POINT: Findings never actually feed back. Converges iff LLM sampling  ║
║               happens to fix the issue; otherwise hits max_iterations.       ║
║   DATA_FLOW:  plan.md → developer.run() → code. Findings → /dev/null.        ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║  SINGLE developer.run() UP FRONT, THEN REVIEW-REVISE LOOP                    ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   ┌────────────────────────────────────────────────────────────────────┐     ║
║   │  developer.run(plan_file, user_prompt=prompt)   ← once              │     ║
║   │  (full plan impl; internal config retry still applies)              │     ║
║   └────────────────────────────────────────────────────────────────────┘     ║
║                                    │                                          ║
║                                    ▼                                          ║
║   ┌────────────────────────────────────────────────────────────────────┐     ║
║   │  REVIEW-REVISE LOOP: for attempt in range(1, max_iterations + 1)   │     ║
║   │                                                                    │     ║
║   │   ┌───────────┐  ┌──────────────┐                                  │     ║
║   │   │ security  │→ │ drupal_review │ ─── both APPROVE ───► break     │     ║
║   │   │           │  │ (if drupal)   │                                 │     ║
║   │   └───────────┘  └──────────────┘                                  │     ║
║   │        │                │                                          │     ║
║   │        └──────┬─────────┘                                          │     ║
║   │               ▼  (at least one VETO)                               │     ║
║   │        ┌──────────────────────────────────┐                        │     ║
║   │        │ concat findings → fix_prompt      │                        │     ║
║   │        │ developer.apply_feedback(         │                        │     ║
║   │        │   feedback=fix_prompt,            │                        │     ║
║   │        │   worktree_path=…)                │                        │     ║
║   │        │ ← targeted fix via implement_     │                        │     ║
║   │        │    feature; tests run; commits    │                        │     ║
║   │        └──────────────────────────────────┘                        │     ║
║   │                         │                                          │     ║
║   │                         └── next attempt ◄──                       │     ║
║   │                                                                    │     ║
║   └────────────────────────────────────────────────────────────────────┘     ║
║                                                                               ║
║   USER_FLOW:  run-plan-once → review → veto → targeted-fix → review → pass   ║
║   VALUE_ADD:  Each veto produces a real diff addressing the findings.         ║
║               No duplicate plan-implementation work between iterations.       ║
║   DATA_FLOW:  plan.md → run() → code. Findings → apply_feedback() → code.    ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                | Before                                                                            | After                                                                                                     | User Impact                                                                                             |
| --------------------------------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `src/cli.py:785` (`developer.run` in loop) | Called up to `max_iterations` times; re-implements plan each time (~5–10 min each) | Called **once** before the loop                                                                          | Wall-clock time drops by O(N−1) × implementation-time when ≥1 veto occurs                                |
| `src/cli.py:840-850` (Drupal veto)      | Calls `run_revision` (no-op before MR exists); `continue` to next full re-impl     | Calls `apply_feedback(feedback, worktree)`; loop re-reviews                                               | Drupal findings actually get addressed on first retry instead of being silently dropped                  |
| `src/cli.py:849-857` (security veto)    | Logs findings; `continue`; no feedback passed to developer                        | Calls `apply_feedback(feedback, worktree)`; loop re-reviews                                               | Security findings now influence the fix, not just the iteration counter                                  |
| CLI output                              | `Iteration N/M` banner with full "Developer: Implementing features…" each time   | `Initial implementation` banner once; then `Review N/M` with targeted `Developer: Addressing findings…`  | Operator sees what's actually happening; distinct phases                                                |
| Outer-loop config-retry at `cli.py:806-808` | CLI-level `continue` re-calls `developer.run` for config failure                  | Removed — `developer.run()` already retries config fix internally (`base_developer.py:699-742`)           | One retry path instead of two competing ones                                                            |

---

## Mandatory Reading

| Priority | File                                           | Lines      | Why Read This                                                                                                   |
| -------- | ---------------------------------------------- | ---------- | --------------------------------------------------------------------------------------------------------------- |
| P0       | `src/cli.py`                                   | 462–867    | Entire `execute` command incl. both flows — the refactor target                                                 |
| P0       | `src/cli.py`                                   | 508–720    | `--revise` flow — the pattern we are mirroring (`run_revision` once, then Drupal sub-loop at 587–614)          |
| P0       | `src/agents/base_developer.py`                 | 286–389    | `implement_feature()` — the primitive `apply_feedback()` will wrap                                              |
| P0       | `src/agents/base_developer.py`                 | 631–750    | `run()` — to confirm that internal config-retry makes the CLI-level retry redundant                             |
| P0       | `src/agents/base_developer.py`                 | 752–818    | `run_revision()` early-exit path at 810–818 — confirms the Bucket-C bug and why `apply_feedback` is needed      |
| P0       | `src/agents/base_developer.py`                 | 907–935    | How `run_revision` calls `implement_feature(task, context={}, commit_prefix="fix", user_prompt=...)` — the exact shape to reuse |
| P1       | `src/agents/base_developer.py`                 | ~(commit_changes signature; grep) | Existing commit helper used by `run_revision`; `apply_feedback` should call it too                                |
| P1       | `src/agents/security_reviewer.py`              | all        | Understand `sec_result` shape: `approved`, `findings`, `feedback`                                                |
| P1       | `src/agents/drupal_reviewer.py`                | 425–507    | `DrupalReviewerAgent.run()` return shape: `approved`, `findings`, `feedback`, `review_data`                      |
| P2       | `tests/test_python_developer.py`               | fixtures   | Mock fixtures (`mock_config`, `mock_agent_sdk`, `mock_prompt`) used by other developer tests — same pattern     |
| P2       | `src/cli.py`                                   | 587–614    | Revise-flow Drupal sub-loop — the exact pattern to port into initial flow                                       |

No new external documentation needed; this is a pure refactor against existing internal APIs.

---

## Patterns to Mirror

**SERVICE_WRAPPER_OVER_IMPLEMENT_FEATURE** (how `run_revision` calls `implement_feature`):

```python
# SOURCE: src/agents/base_developer.py:910-932
# COPY THIS PATTERN for apply_feedback:
impl_result = self.implement_feature(
    task=task,
    context={},
    worktree_path=worktree_path,
    commit_prefix="fix",
    user_prompt=user_prompt,
)

if impl_result.get("success"):
    changed_files = (
        impl_result.get("files_created", []) +
        impl_result.get("files_modified", [])
    )

    if changed_files:
        self.commit_changes(
            message=impl_result.get("commit_message", f"fix: {task[:72]}"),
            files=changed_files,
            worktree_path=worktree_path,
        )
```

**REVIEW_SUB_LOOP** (the revise-flow pattern we are mirroring into the initial flow):

```python
# SOURCE: src/cli.py:587-614 (inside the --revise flow)
# PORT THIS SHAPE into the initial flow as the review-revise loop:
for drupal_attempt in range(1, max_iterations + 1):
    click.echo(f"\n   🔍 Drupal: Reviewing revised code (attempt {drupal_attempt}/{max_iterations})...")
    drupal_reviewer = DrupalReviewerAgent()
    drupal_result = drupal_reviewer.run(
        worktree_path=worktree_path,
        ticket_id=ticket_id,
        ticket_description=ticket_description,
    )

    if drupal_result["approved"]:
        click.echo("      ✅ Drupal review PASSED")
        break
    else:
        issues_count = len(drupal_result.get("findings", []))
        click.echo(f"      ⚠️  Found {issues_count} Drupal issues")
        for line in drupal_result.get("feedback", []):
            click.echo(f"      {line}")

        if drupal_attempt < max_iterations:
            click.echo("      ↻  Developer will address Drupal findings...")
            fix_prompt = "Fix the following Drupal review findings:\n" + "\n".join(
                drupal_result.get("feedback", [])
            )
            # CURRENT: developer.run_revision(...) — swap to apply_feedback here too,
            # since the revise flow's MR already has discussions; run_revision would
            # then only process NEW MR comments, not the Drupal findings.
            # Defer this change; out of scope for this plan.
        else:
            click.echo("\n⚠️  Drupal review has unresolved findings — will post to MR for human review")
            drupal_findings_to_post = drupal_result
            break
```

**EARLY_EXIT_GUARD_IN_RUN_REVISION** (why we can't reuse `run_revision` for this):

```python
# SOURCE: src/agents/base_developer.py:810-818
# This is the trap we are avoiding — no MR yet in the initial flow,
# so run_revision returns early and user_prompt is silently dropped:
if not discussions:
    logger.info("No unresolved discussions found - nothing to revise")
    return {
        "mr_url": mr_url,
        "feedback_count": 0,
        "changes_committed": False,
        "responses_posted": 0,
        "message": "No unresolved discussions to address",
    }
```

**CLICK_PROGRESS_LOGGING** (exact echo style to preserve):

```python
# SOURCE: src/cli.py:780-784
# Existing style — keep when restructuring:
for iteration in range(1, max_iterations + 1):
    click.echo(f"\n   Iteration {iteration}/{max_iterations}")

    # Developer implements features
    click.echo("   🔨 Developer: Implementing features...")
    dev_result = developer.run(plan_file=plan_file, worktree_path=worktree_path, user_prompt=prompt)
```

---

## Files to Change

| File                                   | Action | Justification                                                                                      |
| -------------------------------------- | ------ | -------------------------------------------------------------------------------------------------- |
| `src/agents/base_developer.py`         | UPDATE | Add `apply_feedback()` method (new primitive)                                                     |
| `src/cli.py`                           | UPDATE | Restructure `execute` initial flow into "run once + review-revise loop"; remove duplicated config retry |
| `tests/test_base_developer.py` **or** new `tests/test_base_developer_apply_feedback.py` | CREATE | Unit tests for `apply_feedback`. Check existing path first — grep suggests no `test_base_developer.py` exists; if absent, follow mock fixtures pattern from `tests/test_python_developer.py` |
| (Optional) `tests/test_cli_execute.py` | CREATE | End-to-end-style test that mocks developer + reviewers and verifies the new call shape (`run` once, `apply_feedback` on veto). Can be deferred if project has no prior CLI-level tests — see Task 6 |

---

## NOT Building (Scope Limits)

- **Not changing `developer.run()` or `developer.run_revision()` behavior.** Both stay as-is. We only add `apply_feedback()` as a sibling primitive and change the CLI call pattern.
- **Not touching the `--revise` flow.** Its Drupal sub-loop already works (the MR has discussions, so `run_revision` doesn't early-exit). Porting `apply_feedback` there is a candidate follow-up, not part of this plan.
- **Not implementing task-level idempotency in `run()`.** No "skip already-done tasks" logic. The whole point of this refactor is that `run()` is called only once, so idempotency doesn't matter.
- **Not persisting review-loop state across `sentinel execute` invocations.** Each invocation starts fresh.
- **Not changing CLI flags, return codes, or output format beyond adding a short "Initial implementation" banner.** `--max-iterations`, `--force`, `--no-env`, `--prompt` all keep current semantics.
- **Not adding new review agents.** Security + Drupal only, same as today.
- **Not changing how unresolved findings get posted to the MR after max_iterations.** `_format_drupal_findings_comment` path stays as-is.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/agents/base_developer.py` — add `apply_feedback()`

- **ACTION**: Add a new method `apply_feedback(feedback: str, worktree_path: Path, *, commit_prefix: str = "fix") -> Dict[str, Any]` on `BaseDeveloperAgent`.
- **IMPLEMENT**:
  1. Derive a short task summary from the first non-empty line of `feedback` (truncate to 72 chars).
  2. Call `self.implement_feature(task=<summary>, context={}, worktree_path=worktree_path, commit_prefix=commit_prefix, user_prompt=feedback)`.
  3. If `impl_result["success"]` is True and `files_created + files_modified` is non-empty, call `self.commit_changes(message=impl_result.get("commit_message", f"{commit_prefix}: {summary}"), files=changed_files, worktree_path=worktree_path)`.
  4. Return a dict: `{"success": bool, "files_modified": [...], "commit_message": str, "message": str}`.
- **MIRROR**: `src/agents/base_developer.py:907-932` (the loop inside `run_revision` that calls `implement_feature` + `commit_changes`). Same shape, one iteration.
- **IMPORTS**: All already imported (`logger`, `implement_feature`, `commit_changes`).
- **GOTCHA 1**: `implement_feature` raises if post-implementation tests fail (`base_developer.py:361-363`). Catch `RuntimeError` at the call site and return `{"success": False, "error": str(e), ...}` so the CLI can decide to break the review loop vs. log-and-continue.
- **GOTCHA 2**: `feedback` may be hundreds of lines (concatenated security + Drupal findings). Pass it as `user_prompt` (which is appended to the TDD prompt verbatim) — do NOT squash it into the `task` slot.
- **GOTCHA 3**: Do **not** re-implement `run_revision`'s MR-discussion handling here. `apply_feedback` is free-form and MR-unaware by design.
- **VALIDATE**: `poetry run mypy src/agents/base_developer.py` passes (no new errors); `poetry run python -c "from src.agents.base_developer import BaseDeveloperAgent; assert hasattr(BaseDeveloperAgent, 'apply_feedback')"` succeeds.

### Task 2: CREATE unit tests for `apply_feedback`

- **ACTION**: Add tests in `tests/test_base_developer.py` (create if missing) covering:
  1. `apply_feedback` with a single-line feedback string → calls `implement_feature` with `task=<feedback snippet>`, `user_prompt=<full feedback>`, `commit_prefix="fix"`; calls `commit_changes` when `files_modified` is non-empty.
  2. `apply_feedback` with an empty `files_modified` result → does NOT call `commit_changes`; returns `success=True, files_modified=[]`.
  3. `apply_feedback` when `implement_feature` raises `RuntimeError` (tests failed) → returns `{"success": False, "error": "..."}` and does NOT call `commit_changes`.
  4. Multi-line feedback (100+ lines) is passed verbatim as `user_prompt`; task summary is truncated to ≤ 72 chars.
- **MIRROR**: `tests/test_python_developer.py` fixtures (`mock_config`, `mock_agent_sdk`, `mock_prompt`) and the `with patch("...")` pattern there. Replace mocks of `implement_feature` / `commit_changes` with `Mock(...)` spies.
- **IMPORTS**: `from unittest.mock import Mock, patch, ANY`; `from src.agents.base_developer import BaseDeveloperAgent`.
- **GOTCHA**: `BaseDeveloperAgent` is abstract — instantiate a concrete subclass (`PythonDeveloperAgent` or `DrupalDeveloperAgent`, whichever has simpler init) or use a test-local subclass that no-ops `_build_tdd_prompt` and `_get_test_command`.
- **VALIDATE**: `poetry run pytest tests/test_base_developer.py -v` — all new tests pass.

### Task 3: UPDATE `src/cli.py` — extract helper `_format_review_feedback(sec_result, drupal_result)`

- **ACTION**: Add a pure helper function (top-level, near `_format_drupal_findings_comment`) that concatenates findings from security and Drupal into a single `fix_prompt` string.
- **IMPLEMENT**:
  - Signature: `def _format_review_feedback(sec_result: dict | None, drupal_result: dict | None) -> str:`
  - If both None/approved, return `""`.
  - Otherwise produce a prompt with named sections:
    ```
    Address the following review findings. Prioritize BLOCKER severity.

    ## Security Findings
    - {feedback line 1}
    - {feedback line 2}

    ## Drupal Findings
    - {feedback line 1}
    ...
    ```
  - Only include a section if its reviewer returned `approved=False`.
- **MIRROR**: `src/cli.py:383-421` (`_format_drupal_findings_comment`) — same style of helper, same docstring tone.
- **IMPORTS**: None new.
- **GOTCHA**: The `feedback` key on review results is a `list[str]` in both agents; do not `str.join` over the whole dict.
- **VALIDATE**: `poetry run python -c "from src.cli import _format_review_feedback; print(_format_review_feedback({'approved': False, 'feedback': ['a', 'b']}, None))"` prints a non-empty prompt.

### Task 4: UPDATE `src/cli.py` — restructure the initial `execute` flow

- **ACTION**: Replace the current `for iteration in range(1, max_iterations + 1): dev_result = developer.run(...); config check; security; drupal …` at roughly `src/cli.py:780-867` with:

  ```
  developer.run(plan_file, worktree_path, user_prompt=prompt)  # ONCE, before the loop
  # inline config-failure handling (no retry loop; developer.run() already retried internally)
  # if still failing → sys.exit(1) with the same message as today at line 810-812

  drupal_findings_to_post = None
  last_sec_result = None
  last_drupal_result = None

  for attempt in range(1, max_iterations + 1):
      click.echo(f"\n   Review {attempt}/{max_iterations}")

      sec_result = security.run(worktree_path=worktree_path, ticket_id=ticket_id)
      last_sec_result = sec_result
      sec_ok = sec_result["approved"]

      drupal_result = None
      drupal_ok = True
      if stack_type and stack_type.startswith("drupal"):
          drupal_result = DrupalReviewerAgent().run(
              worktree_path=worktree_path,
              ticket_id=ticket_id,
              ticket_description=_fetch_ticket_description(ticket_id),
          )
          last_drupal_result = drupal_result
          drupal_ok = drupal_result["approved"]

      if sec_ok and drupal_ok:
          # SUCCESS: exit review loop
          break

      if attempt < max_iterations:
          fix_prompt = _format_review_feedback(
              sec_result if not sec_ok else None,
              drupal_result if not drupal_ok else None,
          )
          click.echo("   ↻  Developer will address findings...")
          feedback_result = developer.apply_feedback(
              feedback=fix_prompt, worktree_path=worktree_path
          )
          if not feedback_result.get("success"):
              click.echo(
                  f"\n❌ Developer failed to apply feedback: {feedback_result.get('error','unknown')}",
                  err=True,
              )
              sys.exit(1)
      else:
          click.echo("\n⚠️  Max review attempts reached.")
          # Preserve existing "post to MR for human review" behavior
          if drupal_result and not drupal_ok:
              drupal_findings_to_post = drupal_result
          if not sec_ok:
              # Security has no equivalent MR-post today; keep current behavior (exit).
              click.echo("\n❌ Unresolved security findings — manual review required.", err=True)
              sys.exit(1)
  ```
- **IMPLEMENT**: Replace lines 780-867 exactly. Keep the `drupal_findings_to_post` variable name (referenced later at `cli.py:914+` when posting to the MR — grep to confirm). Keep `click.echo` strings that operators rely on (reviewer names, checkmarks, ↻ symbol).
- **MIRROR**: `src/cli.py:587-614` (revise-flow Drupal sub-loop) for the loop shape; `src/cli.py:722-779` for the pre-loop developer setup (environment wiring, agent selection) — **do not change that block**.
- **GOTCHA 1**: The current code at 818-829 nests Drupal review inside `if sec_result["approved"]`. The new structure runs both reviewers in a single pass so a single `apply_feedback` call can address both. Verify this change is acceptable — if security must pass before Drupal runs for cost reasons, switch to "security-first gate then drupal" inside the loop body. **Decision for this plan: run both in parallel order per attempt** (simpler, symmetric; an extra Drupal call is cheap compared to `apply_feedback`).
- **GOTCHA 2**: Config retry at `cli.py:806-812` MUST be removed. Rationale: `developer.run()` has an internal retry at `base_developer.py:699-742`. Two nested retries with different termination semantics caused duplicated work and obscured failures. After removal, if `dev_result['config_validation']['success']` is False after `developer.run()` returns, print the existing "Config validation failed" message once and `sys.exit(1)` — no retry loop at CLI level.
- **GOTCHA 3**: `_fetch_ticket_description` currently runs once per iteration (inside the old loop). Hoist it above the review loop so the Jira fetch happens at most once per `execute` invocation.
- **GOTCHA 4**: Preserve the "MR creation after approval" block (currently after the outer for-loop). It still runs once, on success, with `drupal_findings_to_post` populated only if Drupal max-attempts were hit.
- **VALIDATE**: `poetry run ruff check src/cli.py && poetry run mypy src/cli.py` passes (no new errors); `poetry run python -c "from src.cli import cli; print('cli imports OK')"` succeeds.

### Task 5: CLI smoke test

- **ACTION**: Run one full invocation in "dry-run" mode against a seeded test ticket or with a mocked env. If no dry-run exists, do a stub run where `developer.run`, `security.run`, and `DrupalReviewerAgent.run` are monkey-patched to return fixed results.
- **IMPLEMENT**: Smallest possible harness — a pytest file (`tests/test_cli_execute_initial.py`, new) that:
  1. Patches `PlanGeneratorAgent` (not called in execute anyway), `DrupalDeveloperAgent`, `SecurityReviewerAgent`, `DrupalReviewerAgent`, `EnvironmentManager`, `WorktreeManager`, and the `subprocess.run` used for `git push` and MR creation.
  2. Constructs a `CliRunner()` and invokes `cli, ["execute", "TEST-1"]`.
  3. Asserts:
     - `developer.run` is called exactly **1 time**.
     - On a scenario where security returns approved=False on attempt 1 and approved=True on attempt 2, `developer.apply_feedback` is called exactly **1 time** with a `fix_prompt` containing the security findings.
     - No call to `developer.run_revision` happens in the initial flow.
- **MIRROR**: Click testing docs (`click.testing.CliRunner`) + existing mock fixtures in `tests/test_python_developer.py`. No project-specific CLI test infrastructure found; this establishes it.
- **IMPORTS**: `from click.testing import CliRunner`, `from src.cli import cli`.
- **GOTCHA**: `execute` calls `worktree_mgr.get_worktree_path(...)` and `sys.exit(1)` if None — the test fixture must mock that to return a valid path.
- **VALIDATE**: `poetry run pytest tests/test_cli_execute_initial.py -v` passes.

### Task 6: UPDATE existing flow tests if any break

- **ACTION**: Run the full test suite and fix any regressions introduced by changes to `src/cli.py` or `base_developer.py`.
- **IMPLEMENT**: Baseline today is 38 failing / 667 passing. The refactor must not increase failure count.
- **MIRROR**: Fix-in-place; prefer updating mock signatures (as we did for `test_functional_debrief.py`'s `timeout` kwarg) over changing runtime behavior.
- **GOTCHA**: Some pre-existing failures in `tests/test_plan_generator.py` etc. are unrelated — leave those untouched.
- **VALIDATE**: `poetry run pytest --no-header -q --tb=no | tail -3` shows ≤ 38 failed.

---

## Testing Strategy

### Unit Tests to Write

| Test File                                | Test Cases                                                                                                                                                                                                                                                               | Validates                                                                                                    |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `tests/test_base_developer.py` (new)     | `test_apply_feedback_calls_implement_feature_with_user_prompt`; `test_apply_feedback_commits_when_files_modified`; `test_apply_feedback_skips_commit_when_no_files`; `test_apply_feedback_handles_test_failure`; `test_apply_feedback_truncates_task_summary_to_72_chars` | `apply_feedback` primitive                                                                                   |
| `tests/test_cli_execute_initial.py` (new)| `test_execute_calls_developer_run_once`; `test_execute_calls_apply_feedback_on_security_veto`; `test_execute_calls_apply_feedback_on_drupal_veto`; `test_execute_breaks_when_both_reviews_approve`; `test_execute_posts_drupal_findings_on_max_attempts`; `test_execute_does_not_call_run_revision_in_initial_flow` | Top-level `execute` initial flow — the actual refactor                                                     |
| `tests/test_cli_execute_initial.py` (new)| `test_format_review_feedback_concatenates_security_and_drupal`; `test_format_review_feedback_omits_approved_sections`; `test_format_review_feedback_empty_when_all_approved`                                                                                           | `_format_review_feedback` helper                                                                            |

### Edge Cases Checklist

- [ ] `developer.run()` fails upfront (before the review loop ever runs) → exit with current error message
- [ ] Security approves on attempt 1, Drupal vetoes → one `apply_feedback`, one extra review attempt, approval on attempt 2
- [ ] Security vetoes every attempt → hit max_iterations, exit with "Unresolved security findings" error
- [ ] Drupal vetoes every attempt → hit max_iterations, populate `drupal_findings_to_post`, proceed to MR creation
- [ ] `apply_feedback` returns `success=False` (tests failing) → exit loop with error, no MR creation
- [ ] Non-Drupal stack (e.g. Python) → Drupal reviewer never instantiated; loop terminates on security approval
- [ ] `max_iterations=1` → review runs once; if either vetoes, max hit immediately, falls through to post-findings branch
- [ ] `_fetch_ticket_description` raises → wrapped in `try/except`, loop continues with empty description (today's behavior; unchanged by this refactor)
- [ ] `config_validation` in `dev_result` missing → treat as success (today's `.get("success", True)` semantics; preserved)

---

## Validation Commands

This project is Python with Poetry (`pyproject.toml` at repo root).

### Level 1: STATIC_ANALYSIS

```bash
poetry run ruff check src/cli.py src/agents/base_developer.py tests/test_base_developer.py tests/test_cli_execute_initial.py
poetry run mypy src/cli.py src/agents/base_developer.py
```

**EXPECT**: Exit 0 (or same number of pre-existing F541 lint warnings as HEAD; no new errors).

### Level 2: UNIT_TESTS

```bash
poetry run pytest tests/test_base_developer.py tests/test_cli_execute_initial.py -v
```

**EXPECT**: All new tests pass. Tests of `apply_feedback` alone ≥ 5. Tests of execute initial flow ≥ 6. `test_execute_does_not_call_run_revision_in_initial_flow` is the canonical regression guard against today's bug.

### Level 3: FULL_SUITE

```bash
poetry run pytest --no-header -q --tb=no
```

**EXPECT**: Total failed count ≤ 38 (current baseline). Total passed count ≥ 667 + new tests. Zero new failures in `test_drupal_developer.py`, `test_drupal_reviewer.py`, `test_plan_generator.py`, `test_functional_debrief.py`, `test_ticket_context.py`, `test_guardrails.py` (suites touched by recent commits).

### Level 4: DATABASE_VALIDATION

N/A — no schema changes.

### Level 5: BROWSER_VALIDATION

N/A — no UI changes.

### Level 6: MANUAL_VALIDATION (end-to-end, owner-driven)

Run against a real Drupal ticket with a deliberately-plantable Drupal-idiom bug (e.g., `\Drupal::service()` call inside a Controller):

```bash
sentinel execute DHLEXS_DHLEXC-XXX --max-iterations 3
```

Observe:

1. Output shows `Initial implementation` (singular), then `Review 1/3`, `Review 2/3`, etc.
2. On attempt 1, Drupal review vetoes with the DI finding.
3. CLI prints `↻  Developer will address findings...`.
4. Before attempt 2's review, the worktree has a new commit of the shape `fix: …` addressing the DI issue (`git log --oneline` shows it).
5. Attempt 2's review approves, loop exits.
6. In `logs/agent_diagnostics.jsonl`: exactly 1 `exec_start` for `drupal_developer` initial implementation, 1 `exec_start` for each security + Drupal review, 1 `exec_start` per `apply_feedback` — and **zero** `exec_start` entries with a `prompt_preview` starting with `"You are revising an implementation plan"` (confirms `run_revision` was not invoked).

---

## Acceptance Criteria

- [ ] `developer.run(plan_file, …)` is called at most once per `sentinel execute` invocation (without `--revise`).
- [ ] On security or Drupal veto (non-final attempt), `developer.apply_feedback(feedback, …)` is called with the concatenated findings as `feedback`.
- [ ] `developer.run_revision(…)` is **not** called in the initial flow (grep-able test assertion).
- [ ] The `--revise` flow is byte-for-byte unchanged (diff-check `src/cli.py:508-720`).
- [ ] `_format_review_feedback` helper exists and produces deterministic output for empty/approved inputs.
- [ ] `BaseDeveloperAgent.apply_feedback` exists and is covered by ≥ 5 unit tests.
- [ ] `test_execute_does_not_call_run_revision_in_initial_flow` passes.
- [ ] Level 3 validation: full test suite failure count ≤ 38 (no regressions vs. current HEAD on `feature/upgrade-drupal-developer`).
- [ ] Manual validation (Level 6) shows the worktree accumulates review-driven fix commits between review attempts.

---

## Completion Checklist

- [ ] All tasks completed in dependency order (1 → 2 → 3 → 4 → 5 → 6)
- [ ] Each task validated immediately after completion
- [ ] Level 1: Static analysis (ruff + mypy) passes with no new errors
- [ ] Level 2: New unit tests pass
- [ ] Level 3: Full suite failure count does not increase
- [ ] Level 6: Manual end-to-end run verified (logged in `.claude/PRPs/reports/{name}-report.md` after implementation)
- [ ] All acceptance criteria met

---

## Risks and Mitigations

| Risk                                                                                                                 | Likelihood | Impact | Mitigation                                                                                                                                                                                           |
| -------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apply_feedback`'s task-summary truncation hides critical context from the LLM                                       | MED        | MED    | Full feedback always goes through as `user_prompt` (which is appended verbatim to the TDD prompt). Truncated task is just a label / commit message. Covered by test_apply_feedback_truncates_task_summary. |
| Removing CLI-level config retry surfaces a previously-hidden bug in `developer.run()`'s internal retry                | LOW        | MED    | `developer.run()`'s retry at `base_developer.py:699-742` has been shipping for longer than the CLI-level one. If regression surfaces, re-add CLI-level retry as a separate commit — do not revert this refactor. |
| `implement_feature` runs tests and raises on failure; `apply_feedback` then returns `success=False` and `sys.exit(1)`; operator loses the partial fix | MED        | MED    | Before `sys.exit`, print the full `impl_result` output and the commit graph so the operator can inspect. Do NOT auto-revert partial commits.                                                                     |
| Running security + Drupal in parallel per attempt (vs. security-gate-first) costs an extra Drupal review when security fails   | LOW        | LOW    | One extra LLM call per failed attempt. Accept the cost in exchange for symmetric design. If cost is prohibitive, swap to gate-first later (single `if sec_ok:` guard — trivial follow-up).                                       |
| `apply_feedback` duplicates logic that *could* instead live in a refactored `run_revision`                           | LOW        | LOW    | Out of scope by design. Revisit after this ships and after the `--revise` flow's Drupal sub-loop is similarly migrated in a follow-up.                                                                                 |
| Test failures unrelated to this change are already at 38; reviewer mistakes one of those for a regression            | MED        | LOW    | Validation Level 3 compares counts, not lists. Task 6 explicitly calls out not to fix pre-existing failures. PR description should include before/after failure counts.                                         |

---

## Notes

### Why `apply_feedback` is a new primitive rather than a parameter on `run_revision`

`run_revision`'s contract is "apply MR feedback": fetch discussions, classify, reply, resolve. Adding a `feedback=` escape hatch that skips MR fetch complicates the contract and risks making `run_revision` dual-purpose (MR mode vs free-form mode). A separate 20-line method with a single responsibility is cheaper maintenance and makes the call sites readable.

### Follow-up candidates (not in this plan)

1. **Port the `--revise` flow's Drupal sub-loop to `apply_feedback` too.** Currently that loop calls `run_revision(user_prompt=fix_prompt)` after MR discussions have been processed once. On subsequent iterations there are no new MR discussions, so `run_revision` early-exits just like in the initial flow. Same fix applies.
2. **Add task-level idempotency in `run()`**. Detect "task already implemented" by checking git diff vs plan expectations, and skip. Would restore safety of calling `run()` multiple times. Strictly lower priority than this refactor because this refactor makes multi-call unnecessary.
3. **Persist review-loop state** (`.agents/execute-state/{ticket_id}.json`) so a crashed `sentinel execute` can resume the review loop without re-running initial implementation.

### Confidence rationale

- Exploration returned concrete file:line references with matching code snippets.
- The pattern being introduced is isomorphic to one already shipped and tested (`--revise` flow's Drupal sub-loop + `run_revision`'s internal `implement_feature` call).
- No new third-party dependencies.
- Tests are writable against existing mock fixtures with minimal new infrastructure.
- Primary risk (config-retry removal) is bounded and reversible.
