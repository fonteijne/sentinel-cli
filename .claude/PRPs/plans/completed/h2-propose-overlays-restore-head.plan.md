# Feature: H2 — `propose_overlays` snapshot/restore operator HEAD

## Summary

`propose_overlays` runs `git checkout -b <branch>` on the operator's working tree and never returns the working tree to its starting branch. On a successful real run the operator is left on `sentinel-learning/promote-…`; on a mid-flow failure (push, MR creation) the explicit "don't clean up" comment leaves the tree on a half-staged branch. This plan adds a deterministic startup-ref snapshot (via `git symbolic-ref --short HEAD` with a `git rev-parse HEAD` detached-HEAD fallback), restores HEAD in a `try/finally` that runs on success AND failure, and refuses to checkout if the snapshot itself fails. The promote branch is preserved on failure for operator inspection (existing behaviour), but the operator's HEAD is always returned to its starting position.

## User Story

As an operator running `sentinel learning propose`
I want my working tree to be on the same branch I started on after the command exits
So that subsequent shell commands (`git status`, `git pull`, IDE git widgets) operate on my actual working branch — not a half-staged promote branch I didn't intend to switch to

## Problem Statement

Today, three concrete failure modes are observable in `src/core/learning/propose_overlay.py`:

1. **Real-run success path** (lines 492–558): after `push_overlay_branch` and the GitLab MR call succeed, the function returns with `repo_root`'s HEAD still pointing at `sentinel-learning/promote-<scope>-<stamp>`. The operator's shell prompt now shows the promote branch — they have to manually `git checkout -` to return.
2. **Real-run failure path** (lines 560–564): the explicit `except: raise` block deliberately preserves the promote branch on disk for operator inspection. But it does NOT restore HEAD — so the operator is dropped into a partially-staged or partially-pushed promote branch, exactly when their tree is in an unfamiliar state.
3. **Dry-run path** (lines 467–490): correctly does `git checkout -` and `git branch -D`, but only on the happy dry-run path. If `_apply_overlay_edit` raises mid-loop (e.g. a malformed overlay), control falls into the `except: raise` block and HEAD is again stranded.

This is a HIGH-severity operator-UX bug: the command silently mutates shell state outside its declared output.

## Solution Statement

Capture the starting ref BEFORE any `git checkout -b`. Use `git symbolic-ref --short HEAD` (returns the branch name on a normal checkout, exits non-zero on detached HEAD). On failure, fall back to `git rev-parse HEAD` (the commit SHA — restorable via `git checkout <sha>` to a detached HEAD). If BOTH commands fail — meaning the repo state is unreadable — raise immediately, BEFORE creating the promote branch. This is the "idempotent" guarantee in the brief: if we can't read the starting ref, we refuse to mutate HEAD at all.

Wrap the `git checkout -b <branch> … create-MR` block in a `try / finally` whose `finally` runs `git checkout <starting-ref>` regardless of outcome. The dry-run path's `git branch -D` stays where it is (inside the try, on the happy path); the real-run failure path's "preserve branch on disk" behaviour is unchanged. Only HEAD is restored.

A dedicated `_capture_starting_ref(repo_root)` helper isolates the snapshot logic so it's unit-testable without spinning up the full proposer flow. A `_restore_starting_ref(repo_root, ref)` helper makes the `finally` block one line and lets us log restore failures at WARNING level (we never want a restore failure to mask the original exception).

## Metadata

| Field            | Value                                                                                               |
| ---------------- | --------------------------------------------------------------------------------------------------- |
| Type             | BUG_FIX                                                                                             |
| Complexity       | LOW                                                                                                 |
| Systems Affected | `src/core/learning/propose_overlay.py`, `tests/core/test_propose_overlay.py`                        |
| Dependencies     | None (uses existing `subprocess` + `git` CLI; no new libs)                                          |
| Estimated Tasks  | 5                                                                                                   |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                              BEFORE STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   $ git status                                                                ║
║   On branch feat/my-work                                                      ║
║                                                                               ║
║   $ sentinel learning propose --scope drupal                                  ║
║       ┌───────────────────────────┐                                           ║
║       │ git checkout -b           │  → tree now on sentinel-learning/...      ║
║       │   sentinel-learning/...   │                                           ║
║       └─────────────┬─────────────┘                                           ║
║                     ▼                                                         ║
║       ┌───────────────────────────┐                                           ║
║       │ apply overlay edits       │                                           ║
║       │ + push + create MR        │                                           ║
║       └─────────────┬─────────────┘                                           ║
║                     ▼                                                         ║
║       ┌───────────────────────────┐                                           ║
║       │ return ProposalResults    │  ← HEAD never restored                    ║
║       └───────────────────────────┘                                           ║
║                                                                               ║
║   $ git status                                                                ║
║   On branch sentinel-learning/promote-drupal-20260514-1142  ← surprise!       ║
║                                                                               ║
║   PAIN_POINT: operator must manually `git checkout -` after every run.        ║
║   On failure, tree is on half-pushed branch with no warning.                  ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                               AFTER STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   $ git status                                                                ║
║   On branch feat/my-work                                                      ║
║                                                                               ║
║   $ sentinel learning propose --scope drupal                                  ║
║       ┌───────────────────────────┐                                           ║
║       │ _capture_starting_ref     │  → "feat/my-work" (or SHA if detached)    ║
║       └─────────────┬─────────────┘                                           ║
║                     ▼                                                         ║
║       ┌───────────────────────────┐                                           ║
║       │ git checkout -b           │                                           ║
║       │   sentinel-learning/...   │                                           ║
║       └─────────────┬─────────────┘                                           ║
║                     ▼                                                         ║
║       ┌───────────────────────────┐                                           ║
║       │ try:                      │                                           ║
║       │   apply edits + push + MR │                                           ║
║       │ finally:                  │                                           ║
║       │   git checkout <start>    │  ← always runs                            ║
║       └─────────────┬─────────────┘                                           ║
║                     ▼                                                         ║
║       ┌───────────────────────────┐                                           ║
║       │ return ProposalResults    │                                           ║
║       └───────────────────────────┘                                           ║
║                                                                               ║
║   $ git status                                                                ║
║   On branch feat/my-work  ← restored                                          ║
║                                                                               ║
║   VALUE_ADD: command no longer mutates shell state outside its return value.  ║
║   On failure: HEAD restored, promote branch preserved for inspection.         ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                              | Before                                                               | After                                                                            | User Impact                                                                                       |
| ----------------------------------------------------- | -------------------------------------------------------------------- | -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `propose_overlays` real-run success                   | Tree left on `sentinel-learning/...`                                 | Tree restored to caller's starting ref                                           | Operator's `git status` matches what it was before the command                                    |
| `propose_overlays` real-run failure (push or MR fail) | Tree left on half-staged promote branch; promote branch preserved    | Tree restored to caller's starting ref; promote branch still preserved on disk   | Operator can inspect the promote branch via `git checkout <branch>` deliberately                  |
| `propose_overlays` dry-run mid-flow failure           | Tree left on promote branch (the `git branch -D` cleanup never runs) | Tree restored to caller's starting ref                                           | Dry-run failures behave like real-run failures w.r.t. HEAD                                        |
| `propose_overlays` startup w/ unreadable repo state   | Would still attempt `git checkout -b`                                | Raises `RuntimeError` BEFORE creating the promote branch                         | Operator gets a clear error instead of a stranded HEAD on top of a corrupt repo                   |
| Detached-HEAD start                                   | (untested; would strand on promote branch)                           | Snapshot via `git rev-parse HEAD`; restore via `git checkout <sha>` (re-detach)  | Operator who started in a detached state is returned to that detached state                       |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task.**

| Priority | File                                                  | Lines     | Why Read This                                                                                  |
| -------- | ----------------------------------------------------- | --------- | ---------------------------------------------------------------------------------------------- |
| P0       | `src/core/learning/propose_overlay.py`                | 355–565   | The function being changed. Note: brief said 438–472, real `checkout -b` is at 431–441; real failure-path comment is at 560–564 (not 567). Read the entire `propose_overlays` function to understand the success / dry-run / failure flows. |
| P0       | `src/core/learning/propose_overlay.py`                | 8–28      | Module docstring's design invariants — reviewer tests assert these (e.g., dry-run never publishes events; push failures bubble the exception unchanged). Restore logic must not violate any of them. |
| P1       | `tests/core/test_propose_overlay.py`                  | 50–84     | `tmp_repo` fixture — initial branch is `main`; restore tests will assert HEAD returns to `main` after the call.                |
| P1       | `tests/core/test_propose_overlay.py`                  | 163–198   | `_no_push_overlay_branch` monkeypatch + `_list_branches` helper. New tests need an analogous helper to read current branch (`git rev-parse --abbrev-ref HEAD`).                                |
| P1       | `tests/core/test_propose_overlay.py`                  | 206–238   | `test_dry_run_creates_no_branch_no_mr` — exact pattern for assertions about post-call branch state. New tests should mirror its structure.                                                    |
| P1       | `tests/core/test_propose_overlay.py`                  | 440–462   | `test_propose_missing_overlay_file_raises` — pattern for "function raises mid-flow" tests. We will extend with an HEAD-restored-on-raise assertion.                                          |
| P2       | `src/worktree_manager.py`                             | 240–257   | Existing in-codebase pattern for `git rev-parse --abbrev-ref HEAD`. Confirms the project's conventions for reading git ref state via `subprocess`.                                            |
| P2       | `src/agents/plan_generator.py`                        | 790–855   | Reference for `subprocess.run` git-call style (`capture_output=True, check=True, decode stderr on failure`). Already cited in `propose_overlay.py` docstring at line 228.                     |

**External Documentation:**

| Source                                                                                                  | Section                            | Why Needed                                                                                                                              |
| ------------------------------------------------------------------------------------------------------- | ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| [git-symbolic-ref docs](https://git-scm.com/docs/git-symbolic-ref)                                      | `--short` flag, exit codes         | Confirms `git symbolic-ref --short HEAD` returns the branch name without the `refs/heads/` prefix and exits non-zero when HEAD is detached. This is the documented detection mechanism we rely on. |
| [git-rev-parse docs](https://git-scm.com/docs/git-rev-parse)                                            | `HEAD` resolution                  | Confirms `git rev-parse HEAD` always returns a SHA when there is at least one commit; only fails on an unborn HEAD (no commits at all). Used as detached-HEAD fallback.                            |
| [git-checkout docs](https://git-scm.com/docs/git-checkout)                                              | "Detached HEAD" section            | `git checkout <sha>` puts the tree in detached-HEAD state — the correct restore behaviour when the operator started detached.                                                                       |
| [Python subprocess.run docs](https://docs.python.org/3/library/subprocess.html#subprocess.run)          | `check=False`, `returncode`, stdout decoding | We need `check=False` for the snapshot's symbolic-ref call (non-zero exit is a SIGNAL, not an error). Existing helpers use `check=True` + `CalledProcessError`; the snapshot is the exception.    |

---

## Patterns to Mirror

**SUBPROCESS_GIT_CALL (existing module-internal style):**

```python
# SOURCE: src/core/learning/propose_overlay.py:431-441
# COPY THIS PATTERN for any check=True git call:
try:
    subprocess.run(
        ["git", "checkout", "-b", branch_name],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
except subprocess.CalledProcessError as e:
    stderr = e.stderr.decode() if e.stderr else ""
    raise RuntimeError(
        f"git checkout -b {branch_name} failed: {stderr}"
    ) from e
```

**REV_PARSE_HEAD_READ (in-codebase reference for reading current branch):**

```python
# SOURCE: src/worktree_manager.py:248-257
# Reference for reading git ref state via subprocess (don't copy verbatim — the
# proposer needs symbolic-ref + rev-parse fallback, not abbrev-ref):
branch_result = subprocess.run(
    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
    cwd=worktree_dir,
    capture_output=True,
    text=True,
)
current_branch = (
    branch_result.stdout.strip() if branch_result.returncode == 0 else ""
)
```

**LOGGING:**

```python
# SOURCE: src/core/learning/propose_overlay.py:43, 442-448, 505-509
# COPY THIS PATTERN — module-level logger; INFO for state transitions,
# WARNING for non-fatal degradations (which is what the new "restore failed"
# log line will be):
logger = logging.getLogger(__name__)
...
logger.info(
    "propose_overlays: created branch %s for scope=%s (%d rules across %d agents)",
    branch_name, scope, len(rules), len(rules_by_agent),
)
```

**TEST_STRUCTURE (mirror these for new HEAD-restoration tests):**

```python
# SOURCE: tests/core/test_propose_overlay.py:206-238
# COPY THIS PATTERN — use tmp_repo, mock_gitlab, monkeypatch.setattr for
# push_overlay_branch, then assert post-call branch state:
def test_dry_run_creates_no_branch_no_mr(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
) -> None:
    results = propose_overlays(
        conn_with_promotable_rules,
        gitlab_client=mock_gitlab,
        repo_root=tmp_repo,
        ...
    )
    ...
    branches = _list_branches(tmp_repo)
    assert all(
        not b.startswith("sentinel-learning/promote-drupal-")
        for b in branches
    ), f"dry-run left a stale branch behind: {branches}"
```

**DETACHED_HEAD_SETUP (test fixture pattern — new):**

```python
# To test detached-HEAD start, set up via subprocess on tmp_repo BEFORE
# calling propose_overlays:
sha = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    cwd=tmp_repo, check=True, capture_output=True, text=True,
).stdout.strip()
subprocess.run(
    ["git", "checkout", sha],
    cwd=tmp_repo, check=True, capture_output=True,
)
```

---

## Files to Change

| File                                     | Action | Justification                                                                                                       |
| ---------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------- |
| `src/core/learning/propose_overlay.py`   | UPDATE | Add `_capture_starting_ref` + `_restore_starting_ref` helpers; wrap checkout-and-edit block in `try/finally`.       |
| `tests/core/test_propose_overlay.py`     | UPDATE | Add 4 new tests covering: real-run success restore, real-run failure restore, dry-run mid-flow failure restore, detached-HEAD round-trip; add 1 helper `_current_ref(repo_root)`. |

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **No replacement of `git` CLI calls with a library** (e.g., `pygit2`, `dulwich`). Out of scope per brief; the rest of the proposer uses `subprocess` + `git`, and we mirror that.
- **No refactor of `_apply_overlay_edit`.** Covered by H4 (dry-run overwrites uncommitted overlay edits). H4 will assert a clean working tree before edit; that's a separate, compatible plan.
- **No change to branch-name precision.** Covered by H3 (collision in same minute). H3 changes `_branch_name_for` to add seconds or a random suffix; H2 doesn't care about the branch name format, only about restoring HEAD.
- **No removal of the existing "preserve promote branch on failure" behaviour.** That's an intentional contract — the operator inspects the promote branch by deliberately checking it out. We only restore HEAD; the branch ref stays.
- **No change to `mark_proposed` / event-publish ordering.** Those happen on the success path BEFORE the `finally` runs the restore; the existing invariant (un-marked rules remain promotable on push/MR failure) is unchanged.
- **No change to dry-run's existing `git branch -D` cleanup.** It stays where it is, on the happy dry-run path. The new `finally` runs AFTER it, and the restore-from-branch (which already happened via `git checkout -`) becomes a no-op or a benign re-checkout. We will verify no double-restore weirdness in tests.

---

## Coordination With H3 / H4

- **H3 (branch-name collision)** changes `_branch_name_for` only. H2 captures the starting ref independently of the branch name, so the two are orthogonal: either can land first.
- **H4 (dry-run overwrites uncommitted overlay edits)** adds a clean-tree check BEFORE `_apply_overlay_edit`. That check will run BEFORE H2's `git checkout -b`, which is BEFORE H2's snapshot read on a clean tree — so the order in code becomes: `(1) H4 clean-tree assert → (2) H2 capture starting ref → (3) git checkout -b`. H2's snapshot must run on the operator's actual starting branch, NOT on the freshly-created promote branch. The plan task list reflects this ordering.
- All three plans modify the same function. They are independent and the resulting changes commute. Implementer of any one plan should leave the other two's hooks (the snapshot capture, the branch-name format, the clean-tree assert) cleanly composable.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: ADD helper `_capture_starting_ref(repo_root: Path) -> str`

- **FILE**: `src/core/learning/propose_overlay.py`
- **LOCATION**: New helper, after `_branch_name_for` (around line 99), before `_overlay_relpath_for`. Co-locating with other module-private helpers.
- **ACTION**: ADD function.
- **IMPLEMENT**:
  - Run `git symbolic-ref --short HEAD` with `check=False, capture_output=True, text=True`. If `returncode == 0`, return `result.stdout.strip()` (the branch name).
  - Else, run `git rev-parse HEAD` with `check=False, capture_output=True, text=True`. If `returncode == 0` and stdout is non-empty, return `result.stdout.strip()` (the SHA — restorable as detached HEAD).
  - Else, raise `RuntimeError(f"could not capture starting git ref in {repo_root}: ...")` with both stderr strings concatenated. This is the "idempotent" guarantee — refuse to checkout if we can't snapshot.
- **MIRROR**: Subprocess style from `src/worktree_manager.py:248-257` (note `check=False` rather than `check=True` — non-zero exit is a SIGNAL here, not an error).
- **DOCSTRING**: Document return-value contract (branch name OR SHA), the detached-HEAD case, and the "raises if both fail" idempotency rule.
- **GOTCHA**: Use `text=True` so we don't have to `.decode()` stdout each time. (The existing `propose_overlay.py` uses `capture_output=True` without `text=True` and decodes stderr manually — that's fine for `check=True` calls where we only read stderr on failure, but for the snapshot we read stdout on success, so `text=True` is the cleaner choice.)
- **VALIDATE**: `cd /workspace/sentinel && poetry run mypy src/core/learning/propose_overlay.py` (or whichever type-check command is used in CI; see Validation section).

### Task 2: ADD helper `_restore_starting_ref(repo_root: Path, ref: str) -> None`

- **FILE**: `src/core/learning/propose_overlay.py`
- **LOCATION**: Immediately after `_capture_starting_ref`.
- **ACTION**: ADD function.
- **IMPLEMENT**:
  - Run `git checkout <ref>` with `check=False, capture_output=True`. (`git checkout` accepts both branch names and SHAs; SHAs result in detached HEAD, which is the correct round-trip for an operator who started detached.)
  - If `returncode != 0`, log `logger.warning("propose_overlays: could not restore starting ref %s in %s: %s", ref, repo_root, stderr)` and return WITHOUT raising. Rationale: a restore failure inside a `finally` block must NEVER mask the original exception (if any) bubbling out of the `try`. The operator gets a warning + the original error.
- **MIRROR**: `src/core/learning/propose_overlay.py:431-441` for the subprocess call style; logging pattern from line 442-448.
- **GOTCHA**: Do NOT use `check=True` here. A `subprocess.CalledProcessError` from `finally` would chain unpredictably with the in-flight exception — Python attaches it as `__context__`, but the operator's traceback becomes confusing. Explicit `check=False` + log is the safe pattern.
- **GOTCHA**: Do NOT use `git checkout -` (the "previous branch" shortcut). It depends on git's reflog state, which is mutated by intervening operations and is therefore not deterministic for our purposes. Always pass the captured ref by name/SHA.
- **VALIDATE**: `poetry run mypy src/core/learning/propose_overlay.py`.

### Task 3: WIRE snapshot + restore into `propose_overlays`

- **FILE**: `src/core/learning/propose_overlay.py`
- **LOCATION**: Inside `propose_overlays`, around lines 429–564.
- **ACTION**: UPDATE the orchestration flow.
- **IMPLEMENT**:
  1. **BEFORE** `branch_name = _branch_name_for(scope)` (line 429), add: `starting_ref = _capture_starting_ref(repo_root)`. If this raises, the function exits before any HEAD mutation — exactly the "refuse to checkout if startup ref check fails" constraint.
  2. **WRAP** the existing `try: … except Exception: raise` block (lines 454–564) so its `finally` clause calls `_restore_starting_ref(repo_root, starting_ref)`. Concretely: change `try: … except Exception: raise` to `try: … finally: _restore_starting_ref(repo_root, starting_ref)`. The bare `except: raise` becomes redundant once `finally` is added — remove it AND the comment, but RE-ADD a comment block explaining the new contract (branch survives on failure, HEAD restored regardless).
  3. **DRY-RUN INTERACTION**: The existing dry-run path runs `git checkout -` then `git branch -D <branch>` BEFORE returning (lines 467-490). After Task 3, the `finally` will then run `git checkout <starting_ref>` on top. If `starting_ref` equals the same branch the dry-run path already returned to, this is a benign no-op. Verify in Task 5's tests that no error is raised.
  4. **REAL-RUN SUCCESS**: The success path returns from inside the `try` block (line 558). The `finally` runs after the return, restoring HEAD as the function unwinds.
  5. **FAILURE**: Any exception inside the `try` (push failure, MR-creation failure, FileNotFoundError on missing overlay) propagates as before — the `finally` restores HEAD on the way out. The promote branch is NOT deleted.
- **DELETE**: The old `except Exception: raise` block (lines 560–564) including its inline comment. The new `finally` makes it redundant.
- **ADD**: A short comment ABOVE the `try` explaining the contract:
  ```python
  # State contract: if any step below raises, we re-raise unchanged (un-mark_proposed'd
  # rules stay promotable, and we deliberately do NOT delete the promote branch — the
  # operator may want to inspect partial state). The `finally` restores the operator's
  # HEAD to where they started regardless of success/failure.
  ```
- **MIRROR**: Existing comment style in the function's docstring (lines 398–406).
- **GOTCHA**: The `if not rules: return []` early return at line 415–422 happens BEFORE `_capture_starting_ref` should run. The snapshot is only useful if we're about to `checkout -b`. So: keep the snapshot AFTER the empty-rules early return but BEFORE `branch_name = _branch_name_for(scope)`.
- **GOTCHA**: The `try: subprocess.run(["git", "checkout", "-b", ...]) except CalledProcessError: raise RuntimeError(...)` block at lines 430–441 is OUTSIDE the new `finally`. That's intentional — if `checkout -b` itself fails, HEAD never moved, so there's nothing to restore. The new `finally` only wraps the post-checkout-success block (lines 454+).
- **VALIDATE**: `poetry run pytest tests/core/test_propose_overlay.py -x -q` — all existing tests must still pass with the wiring change BEFORE adding new tests.

### Task 4: ADD `_current_ref` test helper

- **FILE**: `tests/core/test_propose_overlay.py`
- **LOCATION**: Below `_list_branches` (around line 198), before the `# Tests` divider.
- **ACTION**: ADD helper.
- **IMPLEMENT**:
  ```python
  def _current_ref(repo_root: Path) -> str:
      """Return the current branch name, or the SHA if HEAD is detached.

      Mirrors the production `_capture_starting_ref` resolution order so test
      assertions can compare directly against either form.
      """
      sym = subprocess.run(
          ["git", "symbolic-ref", "--short", "HEAD"],
          cwd=repo_root, capture_output=True, text=True,
      )
      if sym.returncode == 0:
          return sym.stdout.strip()
      sha = subprocess.run(
          ["git", "rev-parse", "HEAD"],
          cwd=repo_root, capture_output=True, text=True, check=True,
      )
      return sha.stdout.strip()
  ```
- **MIRROR**: `tests/core/test_propose_overlay.py:189-198` (`_list_branches` style).
- **VALIDATE**: `poetry run pytest tests/core/test_propose_overlay.py -x -q` (helper unused yet → no regression).

### Task 5: ADD HEAD-restoration tests

- **FILE**: `tests/core/test_propose_overlay.py`
- **LOCATION**: After `test_proposal_result_overlay_path_is_string` (end of file).
- **ACTION**: ADD 4 new test functions.
- **TESTS**:

  **5a. `test_real_run_restores_head_on_success`** — Mirror `test_propose_calls_gitlab_with_draft_true` (lines 272–296), but:
  - Capture `starting = _current_ref(tmp_repo)` BEFORE the call (should be `"main"` per the fixture).
  - After the call, assert `_current_ref(tmp_repo) == starting`.
  - Also assert `f"sentinel-learning/promote-drupal-" in " ".join(_list_branches(tmp_repo))` — the promote branch DID get created (regression guard against accidentally also deleting it).

  **5b. `test_real_run_restores_head_on_failure`** — Use `monkeypatch.setattr(propose_module, "push_overlay_branch", <fake that raises RuntimeError>)`. Mirror `test_propose_missing_overlay_file_raises` (lines 440–462) for the `pytest.raises` shape. Inside the `pytest.raises(RuntimeError)` block, run `propose_overlays(...)`. After the block exits:
  - Assert `_current_ref(tmp_repo) == starting`.
  - Assert the promote branch IS still on disk (i.e. `any(b.startswith("sentinel-learning/promote-") for b in _list_branches(tmp_repo))`) — this is the existing "don't clean up on failure" contract, now explicitly tested.

  **5c. `test_dry_run_restores_head_when_apply_overlay_raises_midflow`** — `monkeypatch.setattr(propose_module, "_apply_overlay_edit", Mock(side_effect=RuntimeError("synthetic")))`. Capture `starting`. Call `propose_overlays(..., dry_run=True)` inside `pytest.raises(RuntimeError)`. Assert `_current_ref(tmp_repo) == starting`. Note: this test exercises the dry-run failure path, NOT the dry-run happy path (which already cleans up correctly today).

  **5d. `test_restores_to_detached_head_when_started_detached`** — Before calling `propose_overlays`:
  ```python
  sha = subprocess.run(
      ["git", "rev-parse", "HEAD"],
      cwd=tmp_repo, check=True, capture_output=True, text=True,
  ).stdout.strip()
  subprocess.run(
      ["git", "checkout", sha],
      cwd=tmp_repo, check=True, capture_output=True,
  )
  ```
  Then `monkeypatch.setattr(propose_module, "push_overlay_branch", _no_push_overlay_branch)`. Call `propose_overlays(...)` (real-run, not dry). Assert `_current_ref(tmp_repo) == sha` after the call (i.e. operator returned to detached state at the same SHA).
- **MIRROR**: All four mirror `test_dry_run_creates_no_branch_no_mr` (lines 206–238) and `test_propose_missing_overlay_file_raises` (lines 440–462) for shape; the only new code is the `_current_ref` assertion before/after.
- **GOTCHA**: Test 5b's fake `push_overlay_branch` must accept the same 4-arg signature as the real one (`repo_root, branch_name, paths, commit_message`) — see `_no_push_overlay_branch` at lines 163–186. A `lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("push fail"))` works but is opaque; a `def _failing_push(...): raise RuntimeError("push fail")` is clearer.
- **GOTCHA**: For test 5c, `_apply_overlay_edit` is module-level but only called from inside `propose_overlays` — `monkeypatch.setattr(propose_module, "_apply_overlay_edit", ...)` is correct (this is how `_no_push_overlay_branch` is wired in existing tests at lines 247–251).
- **VALIDATE**: `poetry run pytest tests/core/test_propose_overlay.py -x -q -v -k "restores_head or detached"` — all 4 new tests must pass.

---

## Testing Strategy

### Unit Tests to Write

| Test File                              | Test Cases                                                                                                                                                                                                                            | Validates                                                       |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `tests/core/test_propose_overlay.py`   | `test_real_run_restores_head_on_success` — happy path restores HEAD, leaves promote branch.                                                                                                                                            | `finally` clause runs on success; promote branch preserved.      |
| `tests/core/test_propose_overlay.py`   | `test_real_run_restores_head_on_failure` — push failure restores HEAD, leaves promote branch.                                                                                                                                          | `finally` runs on exception; existing "preserve branch" intact.  |
| `tests/core/test_propose_overlay.py`   | `test_dry_run_restores_head_when_apply_overlay_raises_midflow` — dry-run mid-flow raise still restores HEAD.                                                                                                                          | `finally` covers the dry-run failure path that was previously stranded. |
| `tests/core/test_propose_overlay.py`   | `test_restores_to_detached_head_when_started_detached` — operator started detached → ends detached at same SHA.                                                                                                                       | `_capture_starting_ref` fallback to `rev-parse HEAD` works.      |

### Edge Cases Checklist

- [ ] Operator starts on `main` (covered by 5a, fixture default).
- [ ] Operator starts on a feature branch (NOT separately tested — `tmp_repo` fixture only sets up `main`; the `_capture_starting_ref` logic doesn't branch on the starting branch's name, so 5a covers it).
- [ ] Operator starts in detached HEAD (covered by 5d).
- [ ] Push fails mid-flow (covered by 5b).
- [ ] MR creation fails mid-flow — NOT separately tested; same code path as 5b (any exception inside the `try` triggers `finally`).
- [ ] `_apply_overlay_edit` raises mid-flow (covered by 5c).
- [ ] `git checkout -b <branch>` itself fails — handled by EXISTING `try/except CalledProcessError` at lines 430–441; new `finally` block is OUTSIDE this, so HEAD wasn't moved → nothing to restore. Implicitly tested by existing tests that don't break.
- [ ] `_capture_starting_ref` cannot read repo state — raise `RuntimeError`, no checkout attempt. NOT in the test plan above (would require corrupting tmp_repo's `.git/`); accept as a defensive guard documented in the helper's docstring.
- [ ] `_restore_starting_ref` fails to restore — log warning, do NOT mask the original exception. NOT separately tested (would require a race between the `try` body and the `finally` checkout); covered by the `check=False` design and the docstring.

---

## Validation Commands

This project uses Poetry + ruff + mypy + pytest (see `pyproject.toml:21-50`).

### Level 1: STATIC_ANALYSIS

```bash
cd /workspace/sentinel
poetry run ruff check src/core/learning/propose_overlay.py tests/core/test_propose_overlay.py
poetry run mypy src/core/learning/propose_overlay.py
```

**EXPECT**: Exit 0. The PR review notes 18 pre-existing ruff errors on the branch vs 17 on main (L8 in the review); this fix should not add any new errors.

### Level 2: UNIT_TESTS

```bash
cd /workspace/sentinel
poetry run pytest tests/core/test_propose_overlay.py -v
```

**EXPECT**: All 9 existing tests pass + 4 new tests pass. Total 13 tests green.

### Level 3: FULL_SUITE

```bash
cd /workspace/sentinel
poetry run pytest tests/ -x -q
```

**EXPECT**: No regressions. Particularly verify `tests/integration/test_phase2c_promotion.py` (the end-to-end remote-semantics test referenced in `tests/core/test_propose_overlay.py:13-14`) still passes — it uses a real bare-repo fixture and will catch any restore-related git misbehaviour.

### Level 4: DATABASE_VALIDATION

N/A — this fix touches no schema and no persistence calls.

### Level 5: BROWSER_VALIDATION

N/A — CLI-only change.

### Level 6: MANUAL_VALIDATION

Run inside `sentinel-dev` container against a clean Sentinel repo working tree:

1. `git checkout -b scratch-h2-test` (or any non-main branch).
2. Note current branch: `git branch --show-current` → `scratch-h2-test`.
3. `sentinel learning propose --scope drupal --dry-run` (assumes promotable rules exist; if not, inject a test rule via `sentinel learning extract`).
4. **Verify**: `git branch --show-current` → still `scratch-h2-test`. No `sentinel-learning/promote-drupal-...` in `git branch --list`.
5. `sentinel learning propose --scope drupal` (real run, will hit a real GitLab API — only do this if test GitLab project is configured).
6. **Verify**: `git branch --show-current` → still `scratch-h2-test`. The `sentinel-learning/promote-drupal-...` branch IS in `git branch --list -r` (pushed to remote) and possibly locally.
7. **Failure mode test**: With `GITLAB_API_TOKEN` deliberately invalid, run step 5. Command should error. **Verify**: `git branch --show-current` → still `scratch-h2-test`. Promote branch survives locally for inspection.

---

## Acceptance Criteria

- [ ] `_capture_starting_ref` and `_restore_starting_ref` helpers added with docstrings.
- [ ] `propose_overlays` calls `_capture_starting_ref` BEFORE creating the promote branch.
- [ ] `propose_overlays` wraps the post-checkout flow in `try/finally` calling `_restore_starting_ref`.
- [ ] Existing `except Exception: raise` block removed (redundant with `finally`); replaced with a comment block documenting the new contract.
- [ ] Promote branch is NOT deleted on failure (existing intentional behaviour preserved).
- [ ] All 4 new tests pass: success-restore, failure-restore, dry-run-mid-flow-restore, detached-HEAD round-trip.
- [ ] Level 1–3 validation commands pass with exit 0.
- [ ] No regression in existing 9 `test_propose_overlay.py` tests.
- [ ] Module docstring (lines 8–28) updated if any of its design invariants are touched (none should be — but verify).

---

## Completion Checklist

- [x] Task 1 complete: `_capture_starting_ref` added; mypy passes.
- [x] Task 2 complete: `_restore_starting_ref` added; mypy passes.
- [x] Task 3 complete: `propose_overlays` rewired with snapshot + `try/finally`; all existing tests pass.
- [x] Task 4 complete: `_current_ref` test helper added.
- [x] Task 5 complete: 4 new tests added; all 14 tests in file pass.
- [x] Level 1: ruff + mypy pass with no new errors.
- [x] Level 2: 14 tests pass in `test_propose_overlay.py` (10 pre-existing + 4 new).
- [x] Level 3: full pytest suite passes — 26 pre-existing baseline failures verified unrelated (env_manager/jira/plan_generator/worktree_manager); 0 regressions from H2.
- [ ] Level 6: manual validation completed inside sentinel-dev — DEFERRED (requires GitLab API access).
- [x] All acceptance criteria met.

**Note on deviation**: The plan said "9 existing tests"; file actually had 10. Final count is 10 + 4 = 14 passing. Additionally, `test_propose_writes_provenance_trailer` and the integration test `test_extract_propose_promote_revoke_full_workflow` had to be updated (read commit via `git show <promote_branch>:path` instead of `HEAD:path` / working tree) — necessary consequence of the new HEAD-restoration invariant. Test intent preserved.

---

## Risks and Mitigations

| Risk                                                                                                                                  | Likelihood | Impact | Mitigation                                                                                                                                                                         |
| ------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_restore_starting_ref` itself fails (e.g., dirty working tree from `_apply_overlay_edit` writes that weren't reset on dry-run path). | LOW        | MED    | `check=False` + warning log: never mask the original exception. H4 will assert clean tree before edit, eliminating most of this case once H4 lands.                                |
| `git checkout <sha>` produces "you are in detached HEAD state" warning to stderr.                                                     | HIGH       | LOW    | Expected and harmless. `capture_output=True` swallows it. Document in helper docstring that detached HEAD is an intentional round-trip behaviour, not an error.                    |
| Test fixture `tmp_repo` initial branch is `main` but a future codebase change might rename the project default to e.g. `master`.     | LOW        | LOW    | Tests use `_current_ref()` for both before/after capture rather than hard-coding `"main"` — invariant is "starting == ending", not "ending == 'main'".                              |
| Implementer accidentally removes the "promote branch survives on failure" behaviour while removing the redundant `except: raise`.    | LOW        | HIGH   | Test 5b explicitly asserts the branch survives failure. Acceptance criterion calls it out. Comment block added in Task 3 documents the invariant in code.                          |
| Conflict with H3 / H4 plans modifying the same function.                                                                              | MED        | LOW    | This plan documents the interaction in "Coordination With H3 / H4" above. The three changes commute. Whoever lands second resolves a small textual conflict in `propose_overlays`. |
| `_capture_starting_ref` raises before the operator's first run, blocking them entirely.                                               | LOW        | LOW    | Only happens if `git symbolic-ref --short HEAD` AND `git rev-parse HEAD` BOTH fail — meaning the repo has no commits at all, which is a no-op state for `propose_overlays` anyway.  |

---

**Confidence Score**: 9/10 for one-pass implementation

The change is small, self-contained, and well-bounded: two pure helpers plus a `try/finally` rewire in a single function, with four new tests that exercise both branch and detached-HEAD round-trips. The existing test fixture (`tmp_repo`) already provides the git state we need, the surrounding code conventions (`subprocess.run` style, `check=False` cleanup) are clearly established, and the failure modes are enumerated above with concrete mitigations. The one point withheld covers the textual-conflict risk with parallel H3/H4 work touching the same function — mechanical to resolve, but not zero-cost.

---

## Notes

- **Why `git checkout <ref>` and not `git checkout -`?** `git checkout -` reads from the reflog ("@{-1}"). That value is implicitly mutated by every checkout in the function (e.g., the dry-run path's own `git checkout -`). Using a captured ref is deterministic; using `-` is fragile and order-dependent.
- **Why not `git switch` instead of `git checkout`?** `git switch` was added in 2.23 (2019). The rest of the proposer uses `git checkout` (line 432, 470). Mirror the existing convention for the same reason `subprocess.run` style is mirrored: minimise surface area for a bug fix.
- **Why log restore failures at WARNING, not ERROR?** The original exception (if any) is the user-visible failure. A restore failure is a degradation of cleanup, not a fresh fault — WARNING is the project's convention for "we tried, we couldn't, we're moving on". Consistent with the docstring at lines 18–24 (push failures bubble unchanged; cleanup is best-effort).
- **Future refactor (out of scope):** if/when the proposer is rewritten to operate on a `git worktree add`-isolated copy (similar to `src/worktree_manager.py`), the entire branch-snapshot dance becomes obsolete — the operator's HEAD is never touched. That's the same direction H4 is pointing. This plan deliberately stays minimal; it's a 5-task fix, not an architecture change.
