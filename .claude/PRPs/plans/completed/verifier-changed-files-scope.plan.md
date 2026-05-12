# Feature: Scope Verifier to Changed Files Only

## Summary

Replace the current "run all tests under `web/modules/custom`" behavior in the post-task verifier with a changed-files-scoped run: only execute tests that were touched (created/modified) by the current task, derived via `git diff` against a pre-task SHA snapshot. Falls back to a broader scope when no test files were changed (implementation-only tasks). Stops prior tasks' broken test setups from poisoning the current task's verifier and makes the verifier-retry loop effective again, because the failures it sees actually belong to the code the task is responsible for.

## User Story

As a Sentinel operator running multi-task implementation plans
I want each task's post-implementation verifier to grade only the code that task touched
So that the verifier-retry loop can actually fix the failures it sees, instead of every task being blamed for cumulative test debt across the whole plan

## Problem Statement

The current `_get_test_command` returns `vendor/bin/phpunit web/modules/custom …`, which sweeps in every test under custom modules — including ones written by *prior* tasks in the same plan. When task 4 writes a test with a stale path (e.g. `/app/web/config/sync/...` vs the actual `/app/config/sync/...`), task 5's verifier inherits that failure even though task 5 has no business touching it. The verifier-retry loop hands the developer agent task 5's prompt back saying "tests failed, try again" — but the failures aren't from task 5's code, so retry burns turns producing nothing.

Net effect (observed on DHLEXS_DHLEXC-311):
- 122 tests run, 102 pass, 20 fail consistently across iterations
- Same 4 errors + 16 failures, identical line numbers, every task
- Every task gets marked failed, the commit gate refuses, nothing reaches the branch
- Verifier-retry loop produces zero useful work because retries can't reach the broken-test files

## Solution Statement

In `_run_tests_in_container`, derive a list of changed `.php` test files from git diff between the worktree's pre-task SHA and HEAD, plus changed implementation files mapped back to their module's tests directory. Pass that file list as positional args to phpunit instead of the directory. When no test files match (e.g. the task only touched docs, configs outside any module, or made no meaningful changes), fall back to the current `web/modules/custom` scope so the verifier still runs *something* rather than skipping silently.

The pre-task SHA is captured at the start of `implement_feature` and threaded into `_run_tests_in_container` via the existing call chain.

## Metadata

| Field            | Value                                                        |
| ---------------- | ------------------------------------------------------------ |
| Type             | ENHANCEMENT                                                  |
| Complexity       | LOW-MEDIUM                                                   |
| Systems Affected | base developer, drupal developer, verifier-retry loop        |
| Dependencies     | None (uses only `git` already in the worktree + existing exec helpers) |
| Estimated Tasks  | 5                                                            |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════╗
║                           BEFORE STATE                              ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                     ║
║   Iteration 1:                                                      ║
║                                                                     ║
║   Task 1: Add module via Composer                                   ║
║     → developer writes ResponsivePreviewComposerInstallTest.php     ║
║     → phpunit web/modules/custom (122 tests, 5 pre-existing fails)  ║
║     → task marked FAILED on someone else's broken test              ║
║                                                                     ║
║   Task 2: Enable the module                                         ║
║     → developer writes ResponsivePreviewModuleTest.php              ║
║     → phpunit web/modules/custom (122 tests, 7 fails now)           ║
║     → task marked FAILED on tasks 1+2's accumulated failures        ║
║                                                                     ║
║   ... (every task fails the same way)                               ║
║                                                                     ║
║   Result: 0 of 7 tasks committed. Every retry useless.              ║
║                                                                     ║
╚═══════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════╗
║                            AFTER STATE                              ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                     ║
║   Iteration 1:                                                      ║
║                                                                     ║
║   Task 1: Add module via Composer                                   ║
║     → pre-task SHA: abc1234                                         ║
║     → developer writes ResponsivePreviewComposerInstallTest.php     ║
║     → git diff abc1234..HEAD --name-only -- '**/tests/**/*.php'     ║
║       → ResponsivePreviewComposerInstallTest.php                    ║
║     → phpunit <that one file> (3 tests, all pass)                   ║
║     → task marked SUCCESS ✓                                         ║
║                                                                     ║
║   Task 2: Enable the module                                         ║
║     → pre-task SHA: def5678 (after task 1's commit)                 ║
║     → developer writes ResponsivePreviewModuleTest.php              ║
║     → diff yields just that test file                               ║
║     → phpunit <that one file> (5 tests, 1 fail in current task)     ║
║     → verifier-retry hands the failure back to developer            ║
║     → developer sees its own broken assertion, fixes it             ║
║     → retry: phpunit <that file> (5 tests pass)                     ║
║     → task marked SUCCESS ✓                                         ║
║                                                                     ║
║   Result: tasks commit progressively. Cross-task debt isolated.     ║
║                                                                     ║
╚═══════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location | Before | After | User Impact |
|----------|--------|-------|-------------|
| `phpunit` invocation | `phpunit web/modules/custom` | `phpunit <changed-test-paths>` (or fallback) | Runs in seconds for typical task instead of 60s |
| Verifier-retry signal | Tests "fail" but errors are from other tasks | Tests fail only on current task's code | Retry loop becomes effective |
| Iteration-end aggregate | Blamed for full project debt | Sees only what each task introduced | Real signal vs noise |
| Implementation-only task (no test changes) | Same broad run | Falls back to `web/modules/custom` | Behavior unchanged |

---

## Mandatory Reading

Before implementing, read these files end-to-end:

1. **`src/agents/base_developer.py`** — focus on `_run_tests_in_container`, `_get_test_command`, `implement_feature`, `run_implementation_plan`. The verifier-retry loop and the per-task implement→test flow live here.
2. **`src/agents/drupal_developer.py`** — `_get_test_command` (the path-scoped phpunit command we modified earlier), `_resolve_test_cmd_for_container` if present (the helper that adapts commands).
3. **`src/agents/python_developer.py`** — same surface, different test runner. Whatever shape we land on for Drupal needs to compose for Python too (or leave Python's behavior unchanged if scoping there is harder).
4. **`tests/test_drupal_developer.py`** — tests around `_get_test_command` and the run-tests flow. The mocked subprocess sequences need updating once the call signature changes.
5. **`tests/test_environment_manager.py::TestExec`** — pattern for mocking `_env_manager.exec` (we'll add `git diff` calls).

## Patterns to Mirror

- **Composer-deps short-circuit** (`base_developer.py:_ensure_composer_deps`) — runs idempotently inside the container, swallows non-fatal errors. Same shape applies to "git diff inside the container or worktree".
- **Test-cmd resolution** (`_resolve_test_cmd_for_container`) — already adapts the command to what the container supports (e.g. strips `--testsuite=` when not defined). New scope-derivation logic should plug in at the same layer rather than at the agent level.
- **Per-task structured-error parsing** (`_parse_test_output`, `_PHPUNIT_JUNIT_PATH`) — already produces structured errors from JUnit XML. We don't need to change parsing, only what tests run.

## Files to Change

| File | Change |
|------|--------|
| `src/agents/base_developer.py` | Add `_capture_pretask_sha`, `_derive_changed_test_paths`. Thread pre-task SHA into `implement_feature` → `_run_tests_in_container`. |
| `src/agents/drupal_developer.py` | `_get_test_command` accepts an optional `paths: list[str] \| None`. Default behavior (no paths) keeps the current `web/modules/custom` fallback. |
| `src/agents/python_developer.py` | Same shape change to `_get_test_command` for parity (paths are pytest-style). |
| `tests/test_drupal_developer.py` | Update mocks to include the git-diff exec call; add tests for changed-files scope, fallback, and implementation-only tasks. |
| `tests/test_base_developer.py` (or new) | Tests for `_derive_changed_test_paths` (pure logic, easy to unit test). |

## NOT Building (Scope Limits)

- **Cross-iteration feedback** — that's plan B (`verifier-cross-iteration-feedback.plan.md`). The two plans compose but ship independently.
- **Pre-flight prompt rule** ("fix existing failing tests before doing your task") — separate, defense-in-depth. Adds a few lines to the developer overlay; not part of this plan.
- **Smarter implementation-→-test mapping** beyond "same module dir" — e.g. taint-tracking from edited code to test classes that exercise it. Out of scope; "module-dir tests" is the heuristic for now.
- **Python developer parity in this PR** — keep behavior unchanged for Python unless a test there blocks landing. If parity needs more work, it can land as a follow-up plan.

---

## Step-by-Step Tasks

### Task 1: ADD `_capture_pretask_sha` and `_derive_changed_test_paths` to `BaseDeveloperAgent` in `src/agents/base_developer.py`

Pure functions that produce diff inputs from the worktree.

```python
def _capture_pretask_sha(self, worktree_path: Path) -> Optional[str]:
    """Snapshot the worktree's HEAD before a task runs. Returns None on
    failure (e.g. fresh worktree with no commits) — caller should treat
    None as "no diff base, fall back to broad scope"."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None
```

```python
def _derive_changed_test_paths(
    self,
    worktree_path: Path,
    pretask_sha: Optional[str],
    test_glob: str = "**/tests/**/*.php",
) -> list[str]:
    """Return paths of test files changed since ``pretask_sha`` plus any
    test files that live in the same module dir as a changed
    implementation file. Empty list means "fall back to broad scope".
    """
    if pretask_sha is None:
        return []
    # 1. Test files directly changed
    diff = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AM",
         f"{pretask_sha}..HEAD", "--", test_glob],
        cwd=worktree_path, capture_output=True, text=True,
    )
    direct = [p for p in diff.stdout.splitlines() if p]

    # 2. Implementation files → look up module-dir tests/
    impl = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AM",
         f"{pretask_sha}..HEAD"],
        cwd=worktree_path, capture_output=True, text=True,
    )
    inferred = self._infer_module_test_dirs(
        [p for p in impl.stdout.splitlines() if p]
    )
    # Dedup, preserve order
    seen = set()
    out: list[str] = []
    for p in direct + inferred:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out
```

`_infer_module_test_dirs` walks each impl path back to the nearest dir containing an `.info.yml` (Drupal module root), then returns its `tests/` subdir if it exists. Pure logic, easy to unit-test.

### Task 2: Plumb pretask-SHA through `implement_feature` → `_run_tests_in_container`

`implement_feature` already orchestrates per-task implementation. Capture the SHA before invoking the agent SDK:

```python
def implement_feature(self, task, ...):
    pretask_sha = self._capture_pretask_sha(worktree_path)
    # ... existing agent-SDK call ...
    # Then in the verifier path:
    test_result = self.run_tests(worktree_path, pretask_sha=pretask_sha)
```

`run_tests` accepts the new kwarg and threads it into `_run_tests_in_container`. `_run_tests_in_container` calls `_derive_changed_test_paths`, then constructs the command via `_get_test_command(paths=...)`.

### Task 3: ADD `paths` parameter to `_get_test_command` in `src/agents/drupal_developer.py`

```python
def _get_test_command(self, paths: Optional[list[str]] = None) -> list[str]:
    """When ``paths`` is non-empty, run phpunit against those files only —
    the changed-files scope. When empty/None, fall back to the broad
    scope (``web/modules/custom``) so implementation-only tasks still get
    *some* verifier signal."""
    cmd = ["vendor/bin/phpunit"]
    cmd += paths if paths else ["web/modules/custom"]
    cmd += ["--no-coverage", f"--log-junit={_PHPUNIT_JUNIT_PATH}"]
    return cmd
```

`python_developer.py:_get_test_command` gets the same signature; can ignore the param if pytest-style scoping doesn't fit cleanly in this PR.

### Task 4: UPDATE `_resolve_test_cmd_for_container` to skip the testsuite-grep when paths are present

If `paths` ended up in the command (positional args), there's no `--testsuite=` flag for the helper to inspect — its current strip-when-undefined logic is irrelevant. Verify the helper handles this gracefully (likely already does, since it only kicks in when `--testsuite=` is in the cmd).

### Task 5: UPDATE tests in `tests/test_drupal_developer.py`

Add tests for:
1. **Changed-files path** — pre-task SHA captured, single test file changed, phpunit runs against just that file.
2. **Fallback path** — pre-task SHA captured, no test files changed (impl-only task), phpunit runs against `web/modules/custom`.
3. **No SHA path** — `_capture_pretask_sha` returns None (e.g. fresh worktree), phpunit runs against `web/modules/custom`.
4. **Module-dir inference** — implementation file changed in `web/modules/custom/foo/foo.module`, no test changed directly, phpunit picks up `web/modules/custom/foo/tests/`.

The existing mocked-subprocess sequences need to grow new entries for the `git diff` calls. Pattern is: `Mock(returncode=0, stdout="path1\npath2\n")` per call.

### Task 6: Manual Verification

After implementation, run on DHLEXS_DHLEXC-311 (or a fresh ticket) and verify:
- Each task's post-test run completes in seconds, not minutes
- Tasks that genuinely pass actually get marked succeeded
- The verifier-retry loop, when it triggers, hands the developer agent failures from its own code

## Testing Strategy

### Verification Approach

- **Unit-level**: `_derive_changed_test_paths` is pure logic given a worktree fixture and pretask_sha. Build a tiny git fixture with two commits, assert the returned paths match expectations.
- **Integration-level**: existing `test_run_tests_*` patterns in `test_drupal_developer.py` already exercise the full implement→test flow with mocked exec. Extend with new mock entries for the git-diff calls.
- **Smoke**: a real run on a multi-task ticket, comparing wall-clock and pass/fail rates against today's behavior.

### Edge Cases Checklist

- Fresh worktree with no commits (rev-parse fails) → fallback
- Pre-task SHA equals HEAD (no changes) → no test paths derived → fallback
- Only doc files changed → no test paths derived → fallback (or skip; consider)
- Test file deleted (D in diff filter) → ignored (we use `--diff-filter=AM`)
- Renamed test file → counts as A (handled by `--diff-filter=AM` if rename detection is on)
- Worktree on detached HEAD → still works (`HEAD` resolves)
- Diff command fails for unrelated reason (perms, corrupt index) → caught by None/empty-list path → fallback

## Validation Commands

### Level 1: STATIC ANALYSIS

```bash
cd /workspace/sentinel
python3 -c "import ast; ast.parse(open('src/agents/base_developer.py').read())"
python3 -c "import ast; ast.parse(open('src/agents/drupal_developer.py').read())"
ruff check src/agents/base_developer.py src/agents/drupal_developer.py
```

### Level 2: UNIT TESTS

```bash
cd /workspace/sentinel
pytest tests/test_drupal_developer.py -q
pytest tests/test_base_developer.py -q  # if added
```

All tests green.

### Level 3: INTEGRATION SMOKE

Run a real multi-task ticket end-to-end and inspect:

```bash
sentinel execute <SOME-MULTI-TASK-TICKET>
# After:
git -C /root/sentinel-workspaces/<proj>/<TICKET> log --oneline origin/<base>..HEAD
```

Expect: tasks that genuinely succeeded produce commits. Verifier wall-clock per task drops sharply for changed-files-scope runs.

## Implementation Order

1. Task 1 (pure helpers, easy to unit-test)
2. Task 5 (write the new tests first — TDD against the helpers)
3. Task 3 (`_get_test_command` signature)
4. Task 2 (plumb pretask-SHA through; this is the integration point)
5. Task 4 (resolver-helper sanity check)
6. Task 6 (manual verification)
