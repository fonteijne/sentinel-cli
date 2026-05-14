# Feature: Pretask SHA Per Loop A Retry Attempt (H7)

## Summary

Fix a subtle correctness bug in Loop A's verifier scoping: `pretask_sha` is currently captured once at the start of `implement_feature()` and reused across all retry attempts. If the developer SDK creates a git commit between attempts (Bash tool is allowed), the per-attempt diff base goes stale and the verifier's "scope tests to changed files" feature can drift — either ballooning scope or silently missing the new attempt's changes. The fix adopts **Option 1**: re-capture `pretask_sha` at the start of every Loop A attempt, anchoring each attempt's diff to "wherever HEAD was when *this* attempt started." A defensive Option-2 detection (`git rev-parse HEAD` compared to the loop-entry SHA) is layered in as a logged warning so unexpected mid-loop commits are observable in the logs without failing the run.

## User Story

As a Sentinel operator running Loop A
I want the verifier's "scope tests to changed files" to reflect *only* what the current retry attempt changed
So that retries after a developer-side commit aren't silently mis-scoped (broader OR narrower than reality), and verifier signals stay trustworthy across the whole 3-attempt window.

## Problem Statement

The verifier's changed-files scope uses `git diff <pretask_sha>` against the working tree. The SHA is captured once in `implement_feature()` (`base_developer.py:532`) and threaded through every retry attempt of `_implement_feature_with_loop()` (`base_developer.py:731`). The accompanying comment correctly notes that on attempt 1 the developer hasn't committed yet, so `HEAD == pretask_sha` and the diff covers all unstaged work. But the comment omits the failure mode for attempt 2+: if the developer ever committed (Bash tool is allowed in `allowed_tools`), `HEAD` advances while `pretask_sha` stays pinned to the loop-entry SHA. The diff base is now stale — `git diff <pretask_sha>` includes the prior commit's changes plus the working tree, *not* the current attempt's net changes.

This is testable: simulate a mid-loop `git commit`, observe that `_derive_changed_test_paths` returns the wrong path set on attempt 2.

## Solution Statement

In `_implement_feature_with_loop()`, replace the single threaded `pretask_sha` with a per-attempt re-capture. The loop body recomputes `attempt_sha = self._capture_pretask_sha(worktree_path)` at the top of each iteration *before* the SDK call, and passes that fresh SHA to `run_tests()`. A new helper `_warn_if_sha_drifted()` compares the loop-entry SHA against the per-attempt SHA and emits a single `logger.warning` when they differ — this is the Option-2 defensive sanity check. The single-shot path (`_implement_feature_single_shot`) is unchanged: it has no retry concept and the existing capture-at-entry semantic is correct there.

## Metadata

| Field            | Value                                                                        |
| ---------------- | ---------------------------------------------------------------------------- |
| Type             | BUG_FIX                                                                      |
| Complexity       | LOW                                                                          |
| Systems Affected | `src/agents/base_developer.py` (Loop A retry), unit + integration test suite |
| Dependencies     | None new (uses only `subprocess`, `logging` already imported)                |
| Estimated Tasks  | 5                                                                            |

---

## UX Design

UX is internal — no operator-facing change. Diagrams below show the verifier-scope data flow.

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                              BEFORE STATE                                     ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  implement_feature(task)                                                      ║
║      │                                                                        ║
║      ├── pretask_sha = capture()    ◄── captured ONCE                         ║
║      │                                                                        ║
║      └── _implement_feature_with_loop(pretask_sha)                            ║
║              │                                                                ║
║              for attempt in 1..3:                                             ║
║                  │                                                            ║
║                  ├── SDK runs (may `git commit` via Bash) ──────┐             ║
║                  │                                              │             ║
║                  └── run_tests(pretask_sha=<stale>)             │             ║
║                          │                                      │             ║
║                          └── git diff <stale_sha>               │             ║
║                                  │                              │             ║
║                                  ▼                              ▼             ║
║                         INCLUDES prior commit's diff     HEAD has moved       ║
║                         (scope balloons)                                      ║
║                         OR                                                    ║
║                         working tree was reset                                ║
║                         (silently misses changes)                             ║
║                                                                               ║
║   DATA FLOW: pretask_sha is invariant across attempts; HEAD is not.           ║
║   PAIN_POINT: Verifier scope drifts when developer commits mid-loop.          ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                               AFTER STATE                                     ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  implement_feature(task)                                                      ║
║      │                                                                        ║
║      ├── loop_entry_sha = capture()    ◄── kept as drift sentinel             ║
║      │                                                                        ║
║      └── _implement_feature_with_loop(loop_entry_sha)                         ║
║              │                                                                ║
║              for attempt in 1..3:                                             ║
║                  │                                                            ║
║                  ├── attempt_sha = capture()  ◄── FRESH each attempt          ║
║                  │       │                                                    ║
║                  │       └── if attempt_sha != loop_entry_sha:                ║
║                  │              logger.warning("SHA drifted: HEAD moved...")  ║
║                  │       (Option-2 sanity check; advisory only)               ║
║                  │                                                            ║
║                  ├── SDK runs (may `git commit` via Bash)                     ║
║                  │                                                            ║
║                  └── run_tests(pretask_sha=attempt_sha)                       ║
║                          │                                                    ║
║                          └── git diff <attempt_sha>                           ║
║                                  │                                            ║
║                                  ▼                                            ║
║                         Always reflects "what this attempt changed"           ║
║                                                                               ║
║   DATA FLOW: each attempt's verifier scope is anchored to its own start.      ║
║   VALUE_ADD: Loop A retries get a correct, stable verifier scope even when    ║
║              the developer commits between attempts.                          ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                                | Before                                | After                                                    | User Impact                                          |
| ------------------------------------------------------- | ------------------------------------- | -------------------------------------------------------- | ---------------------------------------------------- |
| `_implement_feature_with_loop` attempt body             | `run_tests(pretask_sha=<loop-entry>)` | `run_tests(pretask_sha=<attempt-start>)`                 | Verifier scope accurate per attempt                  |
| Logs (attempt 2+ when developer committed mid-loop)     | silent                                | `logger.warning("Loop A: HEAD moved between attempts…")` | Operator sees mid-loop commits in logs               |
| Single-shot path                                        | unchanged                             | unchanged                                                | No behavior change in default path                   |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File                                                       | Lines     | Why Read This                                                                                                  |
| -------- | ---------------------------------------------------------- | --------- | -------------------------------------------------------------------------------------------------------------- |
| P0       | `src/agents/base_developer.py`                             | 497-545   | `implement_feature()` — the entry point that captures `pretask_sha` once today (line 532)                      |
| P0       | `src/agents/base_developer.py`                             | 657-812   | `_implement_feature_with_loop()` — the Loop A retry loop where the per-attempt re-capture must be added        |
| P0       | `src/agents/base_developer.py`                             | 883-904   | `_capture_pretask_sha()` — the helper to call per attempt; understand its `None` failure semantics             |
| P0       | `src/agents/base_developer.py`                             | 906-1024  | `_derive_changed_test_paths()` — the consumer of `pretask_sha`; explains *why* the SHA drift causes scope drift (the comment at 924-937 is load-bearing) |
| P0       | `src/agents/base_developer.py`                             | 1101-1146 | `run_tests()` signature — `pretask_sha: Optional[str]` is the parameter we re-thread per attempt               |
| P1       | `src/agents/base_developer.py`                             | 546-655   | `_implement_feature_single_shot()` — confirm no parallel change needed (single-attempt → existing capture is correct) |
| P1       | `tests/agents/test_base_developer_verifier_loop.py`        | all       | Existing Loop A unit tests — must continue to pass; new test added in same style                               |
| P1       | `tests/integration/test_verifier_retry.py`                 | all       | Existing integration tests for cap-out side-effects — must continue to pass (no functional surface affected)   |
| P2       | `src/agents/base_developer.py`                             | 1-50      | Module imports + `MAX_ATTEMPTS=3` + feature-flag helper; confirm no new imports needed                         |

**External Documentation:**

None. The fix is a pure-Python refactor of an existing `subprocess.run` call site; no new libraries or framework patterns are introduced.

---

## Patterns to Mirror

**SHA_CAPTURE_PATTERN** (existing helper, called once today; we will call it per attempt):

```python
# SOURCE: src/agents/base_developer.py:883-904
# COPY THIS PATTERN (call site only — helper itself is unchanged):
def _capture_pretask_sha(self, worktree_path: Path) -> Optional[str]:
    """Snapshot the worktree's HEAD before a task runs.

    Returns ``None`` on failure (e.g. fresh worktree with no commits,
    worktree is not a git dir, git is unavailable). Callers should
    treat ``None`` as "no diff base — fall back to broad scope".
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as e:
        logger.debug("Could not capture pretask SHA: %s", e)
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None
```

**LOOP_A_ATTEMPT_BODY** (the structure we are inserting the re-capture into):

```python
# SOURCE: src/agents/base_developer.py:708-756
# CURRENT SHAPE (pretask_sha threaded once from outer scope):
for attempt in range(1, MAX_ATTEMPTS + 1):
    logger.info("Loop A attempt %d/%d for task: %s", attempt, MAX_ATTEMPTS, task)

    prompt = (
        tdd_prompt
        if attempt == 1
        else self._build_refine_prompt(last_errors, attempt)
    )

    sdk_result = asyncio.run(self.agent_sdk.execute_with_tools(
        prompt=prompt,
        session_id=None,
        system_prompt=self.system_prompt,
        cwd=str(worktree_path),
    ))

    for tool_use in sdk_result.get("tool_uses", []):
        tool_name = tool_use.get("tool")
        if tool_name == "Write":
            files_created.append(tool_use.get("input", {}).get("file_path", ""))
        elif tool_name == "Edit":
            files_modified.append(tool_use.get("input", {}).get("file_path", ""))

    test_result = self.run_tests(worktree_path, pretask_sha=pretask_sha)
    static_result = self.run_static_checks(worktree_path)
```

**LOGGING_PATTERN** (advisory drift warning — mirror existing `logger.warning` calls):

```python
# SOURCE: src/agents/base_developer.py:617-619, 789-792
# COPY THIS PATTERN:
logger.warning(
    "Loop A attempt %d/%d failed (%d test err, %d static err) — refining",
    attempt, MAX_ATTEMPTS, len(test_errors), len(static_errors),
)
```

**TEST_STRUCTURE** (Loop A unit-test scaffolding — fixtures + monkeypatched env + mocked subprocess + `_CapturingBus`):

```python
# SOURCE: tests/agents/test_base_developer_verifier_loop.py:213-280
# COPY THIS PATTERN:
def test_loop_retries_with_structured_feedback_then_passes(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree, monkeypatch
):
    """First attempt fails, second passes → 2 SDK calls; 2nd prompt carries errors."""
    monkeypatch.setenv("DEV_VERIFIER_LOOP", "1")
    agent = PythonDeveloperAgent()

    sdk_calls: list[str] = []

    async def fake_execute(prompt, session_id=None, system_prompt=None, cwd=None):
        sdk_calls.append(prompt)
        return {"content": "iter", "tool_uses": []}

    agent.agent_sdk.execute_with_tools = fake_execute

    with patch.object(agent, "execute_command") as exec_cmd, \
         patch.object(agent, "run_tests") as run_tests, \
         patch.object(agent, "run_static_checks") as run_static:
        exec_cmd.return_value = {"success": True, "workflow": []}
        run_tests.side_effect = [
            _failing_test_result(),
            _passing_test_result(),
        ]
        run_static.side_effect = [
            _passing_static_result(),
            _passing_static_result(),
        ]

        result = agent.implement_feature("task", {}, temp_worktree)

    assert len(sdk_calls) == 2
    second_prompt = sdk_calls[1]
    assert f"attempt 2 of {MAX_ATTEMPTS}" in second_prompt
    assert "test_failed" in second_prompt
    assert "boom" in second_prompt
    assert result["success"] is True
    assert result["attempts"] == 2
```

**SUBPROCESS_MOCK_PATTERN** (for the new mid-loop-commit test — mirror existing fixtures):

```python
# SOURCE: tests/agents/test_base_developer_verifier_loop.py:107-130
# COPY THIS PATTERN (the part that mocks subprocess.run for git rev-parse):
with patch("src.agents.base_developer.subprocess.run") as mock_run:
    mock_run.return_value = Mock(
        returncode=0,
        stdout="test_x.py PASSED",
        stderr="",
    )

    result = agent.run_tests(temp_worktree)
```

---

## Files to Change

| File                                                    | Action | Justification                                                                                              |
| ------------------------------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------- |
| `src/agents/base_developer.py`                          | UPDATE | Re-capture `pretask_sha` at the top of every Loop A attempt; add Option-2 drift-warning helper             |
| `tests/agents/test_base_developer_verifier_loop.py`     | UPDATE | Add a unit test that simulates a mid-loop developer commit and asserts per-attempt diff scope is correct   |

No new files are created. `tests/integration/test_verifier_retry.py` is **not** modified — it asserts cap-out side-effects (postmortem rows, MR draft revert, MR comment) which are unaffected by this fix; we only re-run it as a regression check.

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **No change to `MAX_ATTEMPTS=3`** — out of scope per the brief; the cap stays at 3.
- **No change to the structured-error `last_errors` carry across iterations** — out of scope per the brief; `_build_refine_prompt` continues to receive errors from the prior attempt unchanged.
- **No change to `_derive_changed_test_paths`'s test-discovery logic** — only its caller (the per-attempt SHA we feed it) changes. The two-step diff + untracked-files union and module-root inference logic are untouched.
- **No change to `_implement_feature_single_shot`** — it has no retry, so the existing single capture-at-entry is already correct.
- **No new event types** — the drift warning is a `logger.warning`, not a new `BaseEvent` subclass. (If we later want telemetry, that's a separate plan.)
- **No change to the loop-entry capture in `implement_feature()`** — we keep it as `loop_entry_sha`, both as the single-shot path's input and as the sentinel against which per-attempt drift is compared. Removing it would silently regress the single-shot path.
- **No "fall back to all tests" branch on drift** (Option-2 variant `c` from the brief) — the design picks Option 1 unconditionally; drift is only logged, because the per-attempt re-capture already gives us the semantically correct scope. Adding a separate fallback would be redundant and add a third code path to test.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: ADD `_warn_if_sha_drifted` helper to `src/agents/base_developer.py`

- **ACTION**: ADD a new private method on `BaseDeveloperAgent`, placed immediately after `_capture_pretask_sha` (line 904).
- **IMPLEMENT**: Compare two SHAs (loop-entry vs. per-attempt). If both non-`None` and differ, emit a single `logger.warning` naming the attempt number and both short SHAs. If either is `None`, no-op (we don't have signal to report). Signature:
  ```python
  def _warn_if_sha_drifted(
      self,
      loop_entry_sha: Optional[str],
      attempt_sha: Optional[str],
      attempt: int,
  ) -> None:
  ```
- **MIRROR**: `src/agents/base_developer.py:789-792` (`logger.warning` call style with `%s`/`%d` formatting).
- **IMPORTS**: None new — `logging` and `Optional` are already imported.
- **GOTCHA**: Use `%s` placeholders, not f-strings, for the log message — this is the project's logging convention (see lines 789, 805 for examples).
- **GOTCHA**: When both SHAs are `None`, this is a *git failure*, not drift — do not warn; the existing `_capture_pretask_sha` already logged at debug level.
- **VALIDATE**: `cd /workspace/sentinel && poetry run python -c "from src.agents.base_developer import BaseDeveloperAgent; print(hasattr(BaseDeveloperAgent, '_warn_if_sha_drifted'))"` → `True`.

### Task 2: UPDATE `_implement_feature_with_loop` to re-capture per attempt

- **ACTION**: MODIFY `src/agents/base_developer.py:708-731`. Inside the `for attempt in range(1, MAX_ATTEMPTS + 1):` loop, *before* the SDK call (i.e. before `sdk_result = asyncio.run(...)` at line 717), insert:
  ```python
  attempt_sha = self._capture_pretask_sha(worktree_path)
  self._warn_if_sha_drifted(pretask_sha, attempt_sha, attempt)
  ```
  Then change the existing `run_tests` call at line 731 from:
  ```python
  test_result = self.run_tests(worktree_path, pretask_sha=pretask_sha)
  ```
  to:
  ```python
  test_result = self.run_tests(worktree_path, pretask_sha=attempt_sha)
  ```
- **IMPLEMENT**: The outer parameter `pretask_sha` (the loop-entry SHA captured by `implement_feature`) is retained — it is now the **drift sentinel** only, not the value handed to `run_tests`.
- **MIRROR**: Existing call site at line 731; only the argument source changes.
- **GOTCHA**: Re-capture must happen **before** the SDK call in each attempt, so attempt 1's `attempt_sha` equals `pretask_sha` on the happy path (no drift warning fires) and attempts 2+ snapshot whatever HEAD looks like *after* the previous attempt's SDK run completed.
- **GOTCHA**: Do **not** rename the outer `pretask_sha` parameter — it's a public-ish keyword on the private helper and renaming risks breaking grep-based searches in the team's review history. Keep the parameter name; only the *use* of it inside the loop changes.
- **GOTCHA**: Do **not** modify `_implement_feature_single_shot` — the brief is explicit that single-shot has no retry semantic to worry about.
- **VALIDATE**: `cd /workspace/sentinel && poetry run pytest tests/agents/test_base_developer_verifier_loop.py -x -q` — all existing tests still pass.

### Task 3: UPDATE the comment at `src/agents/base_developer.py:529-531`

- **ACTION**: MODIFY the comment block above `pretask_sha = self._capture_pretask_sha(worktree_path)` in `implement_feature()`.
- **IMPLEMENT**: Replace the existing comment so it accurately describes Loop A's per-attempt re-capture:
  ```python
  # Snapshot the worktree HEAD *before* the developer agent runs.
  #
  # Single-shot path: this is the diff base for the post-task verifier.
  # Loop A path: this is the LOOP-ENTRY sentinel only — each attempt
  # re-captures its own diff base inside _implement_feature_with_loop
  # so the verifier scope reflects what *this attempt* changed (not
  # what's accumulated since loop entry, which can drift if the SDK
  # commits via Bash between attempts). See _warn_if_sha_drifted.
  #
  # ``None`` from the helper preserves legacy broad-scope behavior.
  ```
- **MIRROR**: Existing multi-line comment style at lines 529-531 and 924-937 (informative, names the design rationale).
- **GOTCHA**: Comments-only change in this task — no runtime behavior delta. Commit it with Task 2 if your VCS workflow prefers grouped commits.
- **VALIDATE**: `cd /workspace/sentinel && poetry run python -c "import src.agents.base_developer"` — module imports cleanly.

### Task 4: ADD a regression unit test for mid-loop commit drift

- **ACTION**: ADD a new test function `test_loop_recaptures_pretask_sha_per_attempt` to `tests/agents/test_base_developer_verifier_loop.py`, placed after `test_loop_retries_with_structured_feedback_then_passes` (line 280).
- **IMPLEMENT**: The test must:
  1. Set `DEV_VERIFIER_LOOP=1` via `monkeypatch.setenv` (mirroring existing tests).
  2. Patch `subprocess.run` so the `["git", "rev-parse", "HEAD"]` invocation returns different SHAs across calls — first call returns `"sha-loop-entry"`, second call (attempt 1 re-capture) returns `"sha-loop-entry"`, third call (attempt 2 re-capture, after a simulated mid-loop commit) returns `"sha-attempt-2"`.
  3. Patch `agent.run_tests` to record the `pretask_sha` kwarg it receives on each invocation, and return `_failing_test_result()` on the first call and `_passing_test_result()` on the second.
  4. Patch `agent.run_static_checks` to return `_passing_static_result()` both times.
  5. Patch `agent.execute_command` to return a successful workflow result.
  6. Patch `agent.agent_sdk.execute_with_tools` (async) to return an empty tool-use payload.
  7. Call `agent.implement_feature("task", {}, temp_worktree)`.
  8. Assert `run_tests.call_args_list[0].kwargs["pretask_sha"] == "sha-loop-entry"`.
  9. Assert `run_tests.call_args_list[1].kwargs["pretask_sha"] == "sha-attempt-2"`.
  10. Assert `result["success"] is True` and `result["attempts"] == 2`.
- **IMPLEMENT (companion test)**: ADD `test_loop_warns_on_mid_loop_sha_drift` — same scaffolding, but use `caplog` (pytest fixture) to assert that a `WARNING`-level record is emitted on the drifted attempt and that its message mentions both short SHAs and the attempt number.
- **MIRROR**: `tests/agents/test_base_developer_verifier_loop.py:213-280` for the SDK-mock + run_tests-side_effect scaffolding; `tests/agents/test_base_developer_verifier_loop.py:107-130` for the `subprocess.run` mock pattern.
- **IMPORTS**: Add `from unittest.mock import call` if needed for `call_args_list` assertions; the file already imports `Mock` and `patch`.
- **GOTCHA**: The fixture `temp_worktree` is a bare `TemporaryDirectory` — it is not a git repo. That's fine because we're patching `subprocess.run`; the helper never actually runs git. **Do not** initialize a real git repo in the fixture, that would invalidate the deterministic SHA mock.
- **GOTCHA**: `subprocess.run` is invoked from BOTH `_capture_pretask_sha` AND `_derive_changed_test_paths`. Since this test patches `agent.run_tests` outright (not the inner `_derive_changed_test_paths`), only the `git rev-parse HEAD` calls hit the mock — three of them: one in `implement_feature`, one per attempt = three total over a 2-attempt run. Sequence the mock's `side_effect` list to match.
- **GOTCHA**: `caplog` requires `caplog.set_level(logging.WARNING, logger="src.agents.base_developer")` at the top of the warning test — the project's loggers default to higher thresholds in the test config.
- **VALIDATE**: `cd /workspace/sentinel && poetry run pytest tests/agents/test_base_developer_verifier_loop.py::test_loop_recaptures_pretask_sha_per_attempt tests/agents/test_base_developer_verifier_loop.py::test_loop_warns_on_mid_loop_sha_drift -x -v` — both pass.

### Task 5: REGRESSION CHECK — full Loop A + integration test pass

- **ACTION**: Run the existing test surface to confirm no regressions.
- **IMPLEMENT**: Execute these commands; all must pass.
- **MIRROR**: n/a (validation-only task).
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && poetry run pytest tests/agents/test_base_developer_verifier_loop.py -x -v
  cd /workspace/sentinel && poetry run pytest tests/integration/test_verifier_retry.py -x -v
  cd /workspace/sentinel && poetry run pytest tests/test_python_developer.py tests/test_drupal_developer.py -x -q
  ```
- **EXPECT**: All test files green. The integration tests in `test_verifier_retry.py` exercise cap-out side-effects (postmortem persistence, MR draft revert, MR comment); they don't depend on `pretask_sha` semantics, so they should pass unchanged.

---

## Testing Strategy

### Unit Tests to Write

| Test File                                              | Test Cases                                                                                                                          | Validates                                                            |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| `tests/agents/test_base_developer_verifier_loop.py`    | `test_loop_recaptures_pretask_sha_per_attempt` — assert `run_tests` receives the per-attempt SHA, not the loop-entry SHA, on attempt 2 | Per-attempt SHA threading (the core bug fix)                         |
| `tests/agents/test_base_developer_verifier_loop.py`    | `test_loop_warns_on_mid_loop_sha_drift` — assert `logger.warning` fires once, names both SHAs and the attempt number               | Option-2 defensive drift detection                                   |

### Edge Cases Checklist

- [ ] Attempt 1 happy path: `attempt_sha == loop_entry_sha`, no warning fires, `run_tests` receives the loop-entry SHA (covered by existing `test_loop_enabled_passes_first_attempt`)
- [ ] Attempt 2 with no commit: `attempt_sha == loop_entry_sha`, no warning fires, `run_tests` receives the same SHA twice (covered by existing `test_loop_retries_with_structured_feedback_then_passes` once we re-thread it)
- [ ] Attempt 2 with mid-loop commit: `attempt_sha != loop_entry_sha`, single `WARNING` log line, `run_tests` receives the per-attempt SHA (NEW test)
- [ ] `_capture_pretask_sha` returns `None` on attempt 2 (transient git failure): drift helper no-ops, `run_tests` receives `None`, broad-scope fallback engages — covered indirectly by existing `test_run_tests_returns_new_shape` plus `_derive_changed_test_paths`'s `if pretask_sha is None: return []` guard
- [ ] Cap-out (3 attempts all fail) with mid-loop commits: warning fires up to 2 times, cap-out event still emitted exactly once (covered by re-running `test_loop_caps_at_three_when_developer_fails_forever`)
- [ ] Single-shot path unchanged: `DEV_VERIFIER_LOOP=0` still calls `_implement_feature_single_shot` with the original loop-entry SHA (covered by `test_loop_disabled_calls_single_shot`)

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
cd /workspace/sentinel && poetry run python -c "import src.agents.base_developer"
cd /workspace/sentinel && poetry run python -m py_compile src/agents/base_developer.py tests/agents/test_base_developer_verifier_loop.py
```

**EXPECT**: Exit 0, no syntax/import errors. (The repo does not configure `mypy` or `ruff` as gates in `pyproject.toml` — `py_compile` + import is the de-facto static check pattern.)

### Level 2: UNIT_TESTS

```bash
cd /workspace/sentinel && poetry run pytest tests/agents/test_base_developer_verifier_loop.py -x -v
```

**EXPECT**: All existing tests still pass + 2 new tests pass.

### Level 3: FULL_SUITE

```bash
cd /workspace/sentinel && poetry run pytest tests/agents/ tests/integration/test_verifier_retry.py tests/test_python_developer.py tests/test_drupal_developer.py -x -q
```

**EXPECT**: All tests pass. No regressions in adjacent developer-agent or integration tests.

### Level 4: DATABASE_VALIDATION

n/a — no schema changes.

### Level 5: BROWSER_VALIDATION

n/a — no UI changes.

### Level 6: MANUAL_VALIDATION

Optional smoke check (only if a `DEV_VERIFIER_LOOP=1` execute run is convenient):

1. Run a real `sentinel execute` against a ticket where the developer agent typically retries (e.g. an intentionally-buggy task).
2. Tail the sentinel log; on attempt 2+ confirm a `Loop A: HEAD moved between attempt N and loop entry…` warning **either** appears (if the developer committed) **or** does not (if the developer did not). Either is correct — the test is that the log line is well-formed when it does appear.

---

## Acceptance Criteria

- [x] `_implement_feature_with_loop` calls `_capture_pretask_sha(worktree_path)` exactly once per attempt and threads that result to `run_tests(pretask_sha=...)`.
- [x] The outer `implement_feature()` capture is retained as the **loop-entry sentinel** and is no longer the value passed to `run_tests` from inside the loop.
- [x] `_warn_if_sha_drifted` emits exactly one `logger.warning` per attempt that drifted; no warning on attempts whose SHA matches loop-entry.
- [x] `_implement_feature_single_shot` is unchanged.
- [x] `MAX_ATTEMPTS = 3` cap and the `last_errors` carry into `_build_refine_prompt` are unchanged.
- [x] Two new unit tests pass: `test_loop_recaptures_pretask_sha_per_attempt`, `test_loop_warns_on_mid_loop_sha_drift`.
- [x] All existing tests in `tests/agents/test_base_developer_verifier_loop.py` and `tests/integration/test_verifier_retry.py` still pass.

---

## Completion Checklist

- [x] Task 1: `_warn_if_sha_drifted` helper added.
- [x] Task 2: Per-attempt re-capture wired into `_implement_feature_with_loop`.
- [x] Task 3: Comment in `implement_feature()` updated to describe loop-entry-vs-per-attempt semantics.
- [x] Task 4: Two new tests added and passing.
- [x] Task 5: Existing Loop A unit tests + cap-out integration tests + adjacent developer-agent tests all green.
- [x] Level 1 static analysis (compile + import) passes.
- [x] Level 2 unit tests pass.
- [x] Level 3 full suite (Loop A + integration + adjacent developer agents) passes.
- [x] All acceptance criteria met.

---

## Risks and Mitigations

| Risk                                                                                                                                                                                       | Likelihood | Impact | Mitigation                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Per-attempt re-capture costs an extra `git rev-parse HEAD` invocation per attempt** (≤3 per `implement_feature` call)                                                                    | LOW        | LOW    | `git rev-parse HEAD` is sub-millisecond; the helper has a 10-second timeout already. Net cost: negligible vs. an SDK call (10s–60s).                                                                                                                                                                                    |
| **Test mocking subtlety**: `subprocess.run` is called from both `_capture_pretask_sha` AND `_derive_changed_test_paths`; getting `side_effect` ordering wrong yields a confusing failure   | MEDIUM     | LOW    | The new test patches `agent.run_tests` *outright*, which short-circuits `_derive_changed_test_paths` entirely. Only `_capture_pretask_sha`'s `git rev-parse HEAD` calls hit the mock. Document this explicitly in a test comment; assert call count to fail loudly if the assumption breaks.                            |
| **Regression: an existing test patches `run_tests` with a single `Mock()` rather than `side_effect=[...]`**, and now sees one extra call                                                   | LOW        | LOW    | Run the full Loop A test file (Task 5 validation). If a test breaks, it's because it assumed a single `run_tests` call — those tests are typically asserting on `result["attempts"]`, which is unchanged.                                                                                                              |
| **Operator confusion from new warning log**: SREs see `HEAD moved between attempts…` and treat it as an error                                                                              | LOW        | LOW    | Phrasing the log message as advisory ("Loop A: HEAD moved between attempts N and N-1; using attempt-anchored diff base.") makes intent clear. Alternative: emit at INFO if WARNING noise becomes a problem, but warning is correct for now since mid-loop commits are unusual.                                          |
| **Future agent change introduces a "build on previous attempt" semantic** (the brief flags this as a theoretical concern)                                                                   | LOW        | MED    | Per the brief, the verifier *should* care about what *this attempt* changed; building cumulative scope would defeat the optimization. If a future feature wants cumulative scope, it should opt in explicitly via a new parameter — out of scope for this fix.                                                          |

---

## Notes

**Design decision: Option 1 (re-capture) is the default, Option 2 (drift detection) is layered on as a logged warning.**

The brief asks the planner to evaluate two options:

- **Option 1** — re-capture per attempt. **Chosen as default.** It directly fixes the bug: each attempt's diff base is "wherever HEAD was when *this attempt* started," matching the verifier's semantic intent ("what did *this attempt* change?"). Implementation cost is one extra `git rev-parse HEAD` call per attempt (≤3 total per `implement_feature`).
- **Option 2** — assert no commits since capture. **Adopted as a defensive layer**, not as a fallback. We compare loop-entry SHA vs per-attempt SHA and log a `WARNING` on drift. We deliberately do NOT (a) refuse to proceed (loud failure would block the loop on a benign developer commit), (b) re-capture-and-continue (Option 1 already does this — Option 2-b would be redundant), or (c) fall back to "all tests" (the per-attempt SHA already gives us correct scope; broadening would lose the optimization). The warning gives operators visibility into mid-loop commits — useful for telemetry — without changing behavior.

**Why not just remove the loop-entry capture entirely?** The loop-entry SHA is still useful as the drift sentinel (Option 2's basis), and it remains the correct value for the single-shot path that doesn't enter the loop. Removing it would require duplicating the capture-on-entry logic into `_implement_feature_single_shot`, increasing the diff for no semantic benefit.

**Why not parameterize `_capture_pretask_sha` to take a description string** (e.g. `"loop-entry"` vs `"attempt-2-start"`)? Logger context is enough; the helper is intentionally minimal. The drift warning carries the attempt number explicitly.

**Future-work pointer (out of scope here):** If telemetry shows mid-loop commits are frequent, a `DeveloperMidLoopCommit` event could replace or supplement the log warning. That's a separate plan and depends on cross-cutting event-bus changes.

---

**Confidence Score**: 9/10 for one-pass implementation.

The change is small, well-scoped, and mirrors patterns already present in `_implement_feature_with_loop` (per-attempt capture of locals, threading kwargs into `run_tests`). Acceptance criteria map 1:1 to concrete code edits, both new tests follow the shape of existing Loop A tests, and the single-shot path is explicitly out of scope. The one residual risk — and the reason this is 9 rather than 10 — is `subprocess.run` mock-ordering: `_capture_pretask_sha` and `_derive_changed_test_paths` both shell out, so a naive `side_effect=[...]` list shared between them produces a confusing failure mode. The plan mitigates this by patching `agent.run_tests` outright in the new tests (which short-circuits `_derive_changed_test_paths`), so only `git rev-parse HEAD` calls hit the subprocess mock; an explicit call-count assertion is included to fail loudly if a future refactor breaks that assumption.
