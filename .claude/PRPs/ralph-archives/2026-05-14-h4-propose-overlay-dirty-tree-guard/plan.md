# Feature: H4 — Guard `propose_overlays` Against Dirty Working-Tree Edits

## Summary

Fix HIGH-severity finding **H4** from the `feat/sentinel-learning-system` PR review: `_apply_overlay_edit` writes the new overlay content to the working-tree file **before** the dry-run revert path runs `git branch -D`. Branch deletion does **not** undo a working-tree edit, so any uncommitted operator edits to `prompts/overlays/<scope>_<agent>.md` are silently overwritten on a dry-run. We add a **pre-flight clean-tree check** scoped to the overlay files we are about to touch, plus a **belt-and-braces explicit restore** of the original bytes on the dry-run path. Real-run behavior on a clean tree is unchanged; dry-run behavior on a clean tree is byte-identical to today.

## User Story

As a Sentinel maintainer running `sentinel learning propose --dry-run`
I want the proposer to refuse to run when I have uncommitted edits to the overlay files it would touch (and to identify which file blocks me)
So that my in-progress overlay work cannot be silently overwritten by the dry-run preview path

## Problem Statement

In `src/core/learning/propose_overlay.py` the orchestration flow is:

1. `git checkout -b <branch>` (line 431-436)
2. For each agent_target group, call `_apply_overlay_edit(...)` which **writes the new file content to disk** (line 217: `overlay_path.write_text(new_text, encoding="utf-8")`)
3. If `dry_run`: `git checkout -` (line 469-474) then `git branch -D <branch>` (line 475-480) and return.

Steps 2 and 3 have an asymmetry: step 2 mutates the working-tree file directly, while step 3 only deletes the branch ref (`git branch -D`) — it does **not** restore working-tree contents. `git checkout -` on the prior branch (`main`) before `git branch -D` *would* restore tracked files **only because the tracked content on `main` matches what we expect**; if the operator had uncommitted modifications to `prompts/overlays/drupal_developer.md` before invoking the proposer, those modifications were already silently lost when the new branch was created (git carries them across) and then `_apply_overlay_edit` overwrote them entirely. There is no recovery path.

Concretely (verifiable by reading the code):
- `src/core/learning/propose_overlay.py:148-217` — `_apply_overlay_edit` reads `original = overlay_path.read_text(...)` and then unconditionally `overlay_path.write_text(new_text, ...)`. The `original` value is **not** preserved beyond the function's local scope.
- `src/core/learning/propose_overlay.py:467-480` — the dry-run revert sequence does `git checkout -` followed by `git branch -D <branch>`. Neither command checks for or restores uncommitted working-tree state on the file just edited.
- `src/core/learning/propose_overlay.py:399-402` — the docstring already admits the bug ("uses `git branch -D` which fails if the working tree is dirty when we created the branch. Tests run on clean tmp repos so this is fine. Production callers should run on a clean Sentinel-repo working tree."), but the code does **not** enforce that contract.

The PR review (`.claude/PRPs/reviews/feat-sentinel-learning-system-review.md`, H4 at lines 104-106) classifies this as silent data loss for operators. The H2 (branch state leak) and H3 (branch-name collision) fixes are filed as separate work items; this plan must compose with whichever option lands for them — i.e. we do **not** introduce a `finally`-block branch restore here (that's H2's responsibility) and we do **not** change the timestamp format (that's H3's responsibility).

## Solution Statement

**Two-layer defense** in the orchestrator (`propose_overlays`) — neither layer touches `_apply_overlay_edit` itself, keeping the rendering function pure:

1. **Pre-flight clean-tree assertion (Option 1 from the brief)**. Before `git checkout -b`, compute the set of overlay paths the run *might* touch (one per `agent_target` in `rules_by_agent`) and run `git status --porcelain -- <path1> <path2> ...` against `repo_root`. If output is non-empty, raise `RuntimeError` with a message naming each blocking file: `"uncommitted changes in <path>; commit or stash first"`. This is the primary fix.

2. **Belt-and-braces explicit restore on dry-run** (defense in depth). Before any `_apply_overlay_edit` call we capture `original_bytes = overlay_path.read_bytes()` for each overlay we will edit, and on the dry-run branch we explicitly `overlay_path.write_bytes(original_bytes)` *before* `git checkout -` and `git branch -D`. This costs ~10 lines and means the dry-run is correct even if a future regression weakens layer 1.

**Why Option 1, not Option 2 (worktree-isolated copy)?** A worktree-isolated copy is more architecturally elegant and aligns with how Sentinel runs developer agents (per CLAUDE.md and `src/worktree_manager.py`), but it has three real costs in this codebase:

- It changes the contract that `propose_overlays` operates on `repo_root` directly. Both unit tests (`tests/core/test_propose_overlay.py`) and the integration test (`tests/integration/test_phase2c_promotion.py`) construct a tmp git repo and pass it as `repo_root`; they assert on commit history *in that repo*. A worktree approach changes where the commit lands.
- The Sentinel repo itself (where `propose_overlays` runs in production) is **not** a bare clone — it's a regular working tree mounted at `/app` in the `sentinel-dev` container. `git worktree add` would work but introduces a cleanup obligation (`git worktree remove`) on every error path, which is exactly the H2 problem one level deeper.
- The H2 fix already adds a `finally`-block branch restore for the existing in-place flow. Layering an additional worktree on top is double-protection that does not justify the test-rewrite cost.

The pre-flight check is the cheapest change that closes the silent-data-loss class of bug. The explicit restore makes dry-run idempotent against the file regardless of what the surrounding git plumbing does. Together they satisfy the brief's "defense in depth costs little here" guidance.

## Metadata

| Field            | Value                                                                                                                                |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| Type             | BUG_FIX                                                                                                                              |
| Complexity       | LOW                                                                                                                                  |
| Systems Affected | `src/core/learning/propose_overlay.py`, `tests/core/test_propose_overlay.py`                                                         |
| Dependencies     | None new (uses `subprocess`, `pathlib` already imported). Composes with H2 (branch-state restore) and H3 (branch-name collision) without conflict. |
| Estimated Tasks  | 5                                                                                                                                    |

---

## UX Design

### Before State

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                              BEFORE STATE                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  Operator has uncommitted edits to drupal_developer.md                       ║
║          │                                                                   ║
║          ▼                                                                   ║
║  ┌──────────────────────────┐    ┌──────────────────────┐                    ║
║  │ sentinel learning        │───►│ git checkout -b      │                    ║
║  │ propose --dry-run        │    │ promote-...          │                    ║
║  └──────────────────────────┘    └──────────────────────┘                    ║
║                                            │                                 ║
║                                            ▼                                 ║
║                                  ┌────────────────────┐                      ║
║                                  │ _apply_overlay_edit│  ◄─── overwrites     ║
║                                  │ write_text(new)    │      operator's      ║
║                                  └────────────────────┘      edits silently  ║
║                                            │                                 ║
║                                            ▼                                 ║
║                                  ┌────────────────────┐                      ║
║                                  │ git checkout -     │                      ║
║                                  │ git branch -D ...  │  ◄─── only deletes   ║
║                                  │   (RETURN dry-run) │      ref. WORKING    ║
║                                  └────────────────────┘      TREE STILL DIRTY║
║                                                              with auto-      ║
║                                                              generated text  ║
║                                                                              ║
║  USER_FLOW: Operator runs --dry-run to "preview" the change. Returns OK.     ║
║             Operator's drupal_developer.md is now full of auto-promoted      ║
║             bullets they did not intend to commit.                           ║
║  PAIN_POINT: Silent data loss. No error. No log. No diff to recover from.    ║
║  DATA_FLOW: working-tree → file → file (overwritten) → branch ref deleted    ║
║             (working-tree edit persists)                                     ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔══════════════════════════════════════════════════════════════════════════════╗
║                               AFTER STATE                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  Operator has uncommitted edits to drupal_developer.md                       ║
║          │                                                                   ║
║          ▼                                                                   ║
║  ┌──────────────────────────┐    ┌─────────────────────────────────┐         ║
║  │ sentinel learning        │───►│ PRE-FLIGHT:                     │         ║
║  │ propose --dry-run        │    │ git status --porcelain --       │         ║
║  │                          │    │   prompts/overlays/X.md ...     │         ║
║  └──────────────────────────┘    └─────────────────────────────────┘         ║
║                                            │                                 ║
║                                  non-empty │   empty                         ║
║                          ┌─────────────────┴────────────────┐                ║
║                          ▼                                  ▼                ║
║              ┌──────────────────────┐         ┌────────────────────────┐     ║
║              │ raise RuntimeError:  │         │ git checkout -b        │     ║
║              │ "uncommitted changes │         │ capture original_bytes │     ║
║              │  in <path>; commit   │         │ _apply_overlay_edit    │     ║
║              │  or stash first"     │         │  ┌──────────────────┐  │     ║
║              │  EXIT 1 from CLI     │         │  │ if dry_run:      │  │     ║
║              └──────────────────────┘         │  │  write_bytes(    │  │     ║
║                                               │  │    original)     │  │     ║
║                                               │  │  checkout -      │  │     ║
║                                               │  │  branch -D       │  │     ║
║                                               │  └──────────────────┘  │     ║
║                                               │  return dry-run results│     ║
║                                               └────────────────────────┘     ║
║                                                                              ║
║  USER_FLOW: --dry-run on dirty tree → loud error naming the blocking file.   ║
║             --dry-run on clean tree → identical output to today.             ║
║             Real-run on clean tree → identical behavior to today.            ║
║  VALUE_ADD: Operator cannot accidentally lose uncommitted overlay work.      ║
║  DATA_FLOW: porcelain check → (block | proceed) → capture orig bytes →       ║
║             apply edit → on dry-run, restore bytes → revert branch.          ║
║                                                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                    | Before                              | After                                                         | User Impact                                                  |
| ------------------------------------------- | ----------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------ |
| `sentinel learning propose --dry-run` (dirty tree) | Silently overwrites overlay file    | Refuses with `uncommitted changes in <path>; commit or stash first` | Operator's work is preserved; clear remediation message      |
| `sentinel learning propose --dry-run` (clean tree) | Renders edit, deletes branch        | Identical: renders edit, restores bytes, deletes branch       | None — observable behavior identical                         |
| `sentinel learning propose` (real, clean)   | Renders, commits, pushes, opens MR  | Identical                                                     | None                                                         |
| `sentinel learning propose` (real, dirty)   | Worked accidentally on dirty tree   | Refuses with same precondition error                          | Forces operator to commit/stash before proposing             |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File                                                | Lines    | Why Read This                                                                           |
| -------- | --------------------------------------------------- | -------- | --------------------------------------------------------------------------------------- |
| P0       | `src/core/learning/propose_overlay.py`              | all      | The file being edited. Every helper, the orchestration flow, and the docstring contract. |
| P0       | `tests/core/test_propose_overlay.py`                | all      | Existing tests we must not break + the fixture pattern for new tests (`tmp_repo`, `_no_push_overlay_branch`). |
| P1       | `tests/integration/test_phase2c_promotion.py`       | 1-260    | E2E test that drives `--dry-run` and `--real`; we must not regress its assertions.       |
| P1       | `.claude/PRPs/reviews/feat-sentinel-learning-system-review.md` | 95-119   | The H2/H3/H4 findings — confirms what's *out of scope* for this plan.                    |
| P2       | `src/agents/plan_generator.py`                      | 790-855  | The `commit_and_push_plan` precedent for `subprocess.run(..., capture_output=True, check=True)` style. |
| P2       | `src/worktree_manager.py`                           | 580-600  | The only existing `git ... --porcelain` use in src/ (`git worktree list --porcelain`); confirms the codebase already parses porcelain output. |

**External Documentation:**

| Source                                                                                                       | Section                                | Why Needed                                                                                                                                              |
| ------------------------------------------------------------------------------------------------------------ | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [git-status(1)](https://git-scm.com/docs/git-status#_short_format)                                           | "Short Format" / `--porcelain=v1`      | `--porcelain` output is stable across git versions and locale-independent. Empty output ⇔ clean for the given pathspec.                                |
| [git-status(1)](https://git-scm.com/docs/git-status#Documentation/git-status.txt---untracked-modeltmodegt)   | `--untracked-files`                    | Untracked overlay files (e.g. operator drafted a new overlay) should also block. Default mode `normal` already covers this for explicit pathspecs.     |

---

## Patterns to Mirror

**SUBPROCESS_INVOCATION:**

```python
# SOURCE: src/core/learning/propose_overlay.py:431-441
# COPY THIS PATTERN: subprocess.run with check=True, capture_output=True,
# CalledProcessError → decoded stderr → RuntimeError chain.
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

**LOGGING_PATTERN:**

```python
# SOURCE: src/core/learning/propose_overlay.py:442-448
# COPY THIS PATTERN: logger.info with %-args (NOT f-strings), comma-separated
# semantic fields. Module-level `logger = logging.getLogger(__name__)`.
logger.info(
    "propose_overlays: created branch %s for scope=%s (%d rules across %d agents)",
    branch_name,
    scope,
    len(rules),
    len(rules_by_agent),
)
```

**TEST_FIXTURE_PATTERN (tmp git repo):**

```python
# SOURCE: tests/core/test_propose_overlay.py:50-84
# COPY THIS PATTERN: tmp_path → init repo → set user.email/name → seed
# overlay file → initial commit. Yield repo path.
@pytest.fixture
def tmp_repo(tmp_path: Path) -> Iterator[Path]:
    repo = tmp_path / "sentinel"
    repo.mkdir()
    subprocess.run(["git", "init", "--initial-branch=main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    overlay_dir = repo / "prompts" / "overlays"
    overlay_dir.mkdir(parents=True)
    overlay_path = overlay_dir / "drupal_developer.md"
    overlay_path.write_text("# Drupal Developer Overlay\n\n## Operating Principles\n\n- Drupal-way first.\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    yield repo
```

**TEST_ASSERTION_STYLE (no stale branches, dry-run side-effects):**

```python
# SOURCE: tests/core/test_propose_overlay.py:206-238
# COPY THIS PATTERN: invoke propose_overlays with dry_run=True, then assert
# - return shape (mr_url == "(dry-run)", dry_run flag)
# - no GitLabClient interaction
# - feedback_rules row was NOT mutated (proposed_at IS NULL)
# - branch list contains no stale promote-* branches
def test_dry_run_creates_no_branch_no_mr(
    conn_with_promotable_rules, tmp_repo, mock_gitlab,
) -> None:
    results = propose_overlays(...)
    assert all(r.dry_run is True for r in results)
    assert mock_gitlab.create_merge_request.call_count == 0
    branches = _list_branches(tmp_repo)
    assert all(not b.startswith("sentinel-learning/promote-drupal-") for b in branches)
```

---

## Files to Change

| File                                  | Action | Justification                                                                                                                       |
| ------------------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------- |
| `src/core/learning/propose_overlay.py` | UPDATE | Add `_assert_overlay_paths_clean(repo_root, paths)` helper; capture original bytes pre-edit; restore on dry-run path before revert. |
| `tests/core/test_propose_overlay.py`  | UPDATE | Add tests: (a) dry-run on dirty tree raises with file name in message; (b) real-run on dirty tree raises; (c) dry-run on clean tree leaves the file byte-identical; (d) clean-tree paths still pass. |

No other files change. The CLI (`src/cli.py`) already wraps the call in a `try/except` that converts exceptions into `❌ Error: <msg>` + `sys.exit(1)` (`src/cli.py:2041-2044`), so a `RuntimeError` from the new pre-flight surfaces correctly without CLI changes.

---

## NOT Building (Scope Limits)

- **Branch-name collision (H3)**: The `_branch_name_for` minute-precision timestamp is **out of scope**. H3 has its own fix that adds seconds or a suffix; this plan must not change `_branch_name_for`.
- **Branch state leak (H2)**: We do **not** add a `finally`-block branch restore for the real-run failure path. H2 owns that. Our changes must compose cleanly with whatever H2 lands (likely a `try/finally` around the entire `propose_overlays` body).
- **Worktree-isolated execution (Option 2 from brief)**: Rejected; rationale in Solution Statement. Not implemented even partially — no `git worktree add`, no temp checkout dir.
- **Markdown diff preview**: The brief mentions "markdown diff preview" as a constraint to preserve; the current code does not actually emit one (it just returns `mr_url="(dry-run)"`). We do not introduce one. Constraint reads as "do not change observable dry-run output on a clean tree."
- **Validating overlay file paths via regex** (M3 from review): Out of scope; separate finding.
- **Stash/auto-recovery of dirty edits**: We refuse and instruct, we do not auto-stash. Auto-stash hides decisions from the operator and is exactly the kind of "silent magic" that produced this bug.
- **Renaming `_apply_overlay_edit`**: It stays a pure file-rendering helper. The orchestrator owns the precondition check and the byte-capture/restore.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: ADD `_assert_overlay_paths_clean` helper to `src/core/learning/propose_overlay.py`

- **ACTION**: ADD a module-private function above the `# Orchestration` divider (i.e. between `_build_mr_description` at line 268 and the `# Orchestration` comment at line 350).
- **IMPLEMENT**:
  - Signature: `def _assert_overlay_paths_clean(repo_root: Path, overlay_relpaths: list[Path]) -> None:`
  - Docstring explaining: "Raise RuntimeError if any of the given overlay paths has uncommitted modifications or is untracked. Uses `git status --porcelain --` with explicit pathspecs so unrelated dirt elsewhere in the tree does NOT block the proposer (e.g., the operator may legitimately have unrelated WIP)."
  - Body:
    1. If `overlay_relpaths` is empty, `return` (defensive).
    2. Run `subprocess.run(["git", "status", "--porcelain", "--"] + [str(p) for p in overlay_relpaths], cwd=repo_root, check=True, capture_output=True)`.
    3. Decode `stdout` (UTF-8, errors="replace"); if empty after `.strip()`, return.
    4. Else parse each non-empty line: porcelain v1 format is `XY <path>` where the first 2 chars are status; everything from index 3 onward is the path (handle quoted paths with spaces — `shlex.split` is overkill; the codebase only has ASCII overlay names today, so document this as a known limitation and use `line[3:].strip()` mirroring the precedent in `worktree_manager.py:585-595`).
    5. Build the error message:
       ```
       blocking = ", ".join(sorted({line[3:].strip() for line in lines if line.strip()}))
       raise RuntimeError(
           f"propose_overlays: uncommitted changes in {blocking}; "
           f"commit or stash first."
       )
       ```
    6. Wrap the `subprocess.run` in `try/except subprocess.CalledProcessError` mirroring the pattern at lines 437-441 — if `git status` itself fails (e.g. not a git repo), surface as `RuntimeError` with decoded stderr.
- **MIRROR**: `src/core/learning/propose_overlay.py:431-441` for the subprocess + CalledProcessError pattern.
- **IMPORTS**: No new imports needed (`subprocess`, `Path` already imported).
- **GOTCHA 1**: `git status --porcelain` outputs with `LC_ALL` independence; do **not** localize. Do **not** use `--porcelain=v2` — v1's column layout is what the rest of the ecosystem expects and what `worktree_manager.py:585` uses.
- **GOTCHA 2**: We pass paths as **explicit pathspecs**. This means an operator with a dirty `src/` and clean `prompts/overlays/` will pass the check — that's intentional (the proposer doesn't touch `src/`). Document this in the docstring.
- **GOTCHA 3**: A path that's **untracked** (e.g. operator created a new overlay variant) shows as `??` in porcelain output — also blocks, which is correct: we don't want to commit something the operator hasn't decided to track yet.
- **VALIDATE**: `poetry run mypy src/core/learning/propose_overlay.py` — must pass with `disallow_untyped_defs = true`.

### Task 2: WIRE the pre-flight check into `propose_overlays` orchestration

- **ACTION**: Modify `propose_overlays` body in `src/core/learning/propose_overlay.py`. Insert the call **after** rules-by-agent grouping (line 425-427) and **before** the `git checkout -b` call (line 429-441).
- **IMPLEMENT**:
  ```python
  # Group rules by agent_target so we can edit one overlay per group.
  rules_by_agent: dict[str, list[sqlite3.Row]] = {}
  for rule in rules:
      rules_by_agent.setdefault(rule["agent_target"], []).append(rule)

  # H4 pre-flight: refuse to run if any overlay we would touch has
  # uncommitted modifications. Branch-D revert (dry-run) and finally-block
  # revert (H2) only undo branch-level state; they cannot recover a
  # working-tree file the operator had not yet committed.
  candidate_overlays = [
      _overlay_relpath_for(scope, agent_target)
      for agent_target in rules_by_agent
  ]
  _assert_overlay_paths_clean(repo_root, candidate_overlays)

  branch_name = _branch_name_for(scope)
  ...
  ```
- **MIRROR**: Insertion site sits between two existing logical blocks; no surrounding code changes.
- **GOTCHA**: The check must run **before** `git checkout -b`. Otherwise we'd create a branch then immediately blow up, leaving stale branch refs (the very thing H2/H3 are about). Order matters: precondition first, mutating action second.
- **VALIDATE**: `poetry run mypy src/core/learning/propose_overlay.py && poetry run ruff check src/core/learning/propose_overlay.py`.

### Task 3: ADD belt-and-braces byte capture + dry-run restore

- **ACTION**: Modify the orchestration loop in `propose_overlays` (lines 454-490) to capture each overlay's original bytes before applying the edit, and restore them on the dry-run path before reverting the branch.
- **IMPLEMENT**:
  - Above the `try:` at line 454, declare an empty dict: `original_overlay_bytes: dict[Path, bytes] = {}`.
  - Inside the per-agent_target loop, **after** the `FileNotFoundError` check at line 457-460 and **before** the `_apply_overlay_edit` call at line 462, capture the original bytes:
    ```python
    overlay_abs = repo_root / overlay_relpath
    original_overlay_bytes[overlay_relpath] = overlay_abs.read_bytes()
    ```
  - Inside the `if dry_run:` block at line 467, **before** `git checkout -`, restore each captured file:
    ```python
    if dry_run:
        # Belt-and-braces: explicitly restore each overlay file's original
        # bytes before letting git plumbing revert. Layer-2 defense
        # against silent overwrite (layer 1 is the pre-flight check above).
        for relpath, original in original_overlay_bytes.items():
            (repo_root / relpath).write_bytes(original)
        subprocess.run(
            ["git", "checkout", "-"],
            ...
        )
    ```
  - The dry-run return value is unchanged.
- **GOTCHA 1**: Use `read_bytes` / `write_bytes`, **not** `read_text` / `write_text`. The file may end with platform-specific line endings or contain trailing whitespace; bytes round-trip is exact, text round-trip is not (Python's `text` mode normalizes newlines on Windows — irrelevant in our Linux container, but principled).
- **GOTCHA 2**: This restore is **idempotent**: if `_apply_overlay_edit` had not yet run for some path (e.g. an exception fired in the previous iteration), `original_overlay_bytes` won't have that key. The `for relpath, original in original_overlay_bytes.items():` loop only restores what we captured.
- **GOTCHA 3**: We do **not** restore on the real-run path. Real-run *wants* the edit on disk — that's what gets committed. Restoring would defeat the purpose.
- **VALIDATE**: `poetry run mypy src/core/learning/propose_overlay.py`.

### Task 4: UPDATE the `propose_overlays` docstring to document the new precondition

- **ACTION**: Edit the orchestration docstring (lines 368-407) to remove the apologetic "tests run on clean tmp repos so this is fine" sentence and replace with the new contract.
- **IMPLEMENT**: Replace the "Constraints:" block at lines 398-406:
  ```
  Constraints:
    - **Pre-flight clean-tree check**: any overlay file that would be edited
      must have NO uncommitted modifications and must NOT be untracked.
      ``RuntimeError`` is raised before any git or filesystem mutation if
      the check fails. The error message names every blocking path.
    - The dry-run path explicitly restores each overlay file's pre-edit
      bytes before reverting the branch — defense in depth against future
      regressions in either the precondition check or git plumbing.
    - Push failures abort only this proposer run; un-mark_proposed'd rules
      remain promotable for the next run. The exception bubbles unchanged.
    - ``draft=True`` is hard-coded in the ``create_merge_request`` call.
      Never compute it from a flag.
  ```
- **GOTCHA**: Keep the `draft=True` line unchanged (D7 invariant). Do not touch the rest of the docstring (other plans/findings own those bits).
- **VALIDATE**: `poetry run ruff check src/core/learning/propose_overlay.py` (catches docstring formatting issues if any).

### Task 5: ADD tests in `tests/core/test_propose_overlay.py`

- **ACTION**: Append four new test functions to the bottom of the existing test module, after `test_proposal_result_overlay_path_is_string` (line 484).
- **IMPLEMENT**:

  **Test 1 — dry-run refuses on dirty overlay tree**:
  ```python
  def test_dry_run_refuses_when_overlay_is_dirty(
      conn_with_promotable_rules: sqlite3.Connection,
      tmp_repo: Path,
      mock_gitlab: Mock,
  ) -> None:
      """H4: pre-flight refuses to run when the overlay file we would touch
      has uncommitted modifications. Operator's edits are preserved verbatim
      and the error message names the blocking file."""
      overlay_path = tmp_repo / "prompts" / "overlays" / "drupal_developer.md"
      operator_edit = overlay_path.read_text(encoding="utf-8") + "\n## My WIP section\n- handwritten note\n"
      overlay_path.write_text(operator_edit, encoding="utf-8")

      with pytest.raises(RuntimeError, match=r"uncommitted changes in .*drupal_developer\.md"):
          propose_overlays(
              conn_with_promotable_rules,
              gitlab_client=mock_gitlab,
              repo_root=tmp_repo,
              repo_project_path="sentinel-team/sentinel",
              scope="drupal",
              min_confidence=80,
              dry_run=True,
          )

      # Operator's edits are preserved byte-for-byte.
      assert overlay_path.read_text(encoding="utf-8") == operator_edit
      # No branch was created.
      assert all(
          not b.startswith("sentinel-learning/promote-drupal-")
          for b in _list_branches(tmp_repo)
      )
      # No GitLab call.
      assert mock_gitlab.create_merge_request.call_count == 0
  ```

  **Test 2 — real-run also refuses on dirty overlay tree**:
  ```python
  def test_real_run_refuses_when_overlay_is_dirty(
      conn_with_promotable_rules: sqlite3.Connection,
      tmp_repo: Path,
      mock_gitlab: Mock,
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      """H4: real-run also refuses on dirty tree (same precondition)."""
      monkeypatch.setattr(
          propose_module, "push_overlay_branch", _no_push_overlay_branch,
      )
      overlay_path = tmp_repo / "prompts" / "overlays" / "drupal_developer.md"
      overlay_path.write_text(
          overlay_path.read_text(encoding="utf-8") + "\n## WIP\n",
          encoding="utf-8",
      )
      with pytest.raises(RuntimeError, match=r"uncommitted changes"):
          propose_overlays(
              conn_with_promotable_rules,
              gitlab_client=mock_gitlab,
              repo_root=tmp_repo,
              repo_project_path="sentinel-team/sentinel",
              scope="drupal",
              min_confidence=80,
          )
      assert mock_gitlab.create_merge_request.call_count == 0
  ```

  **Test 3 — dry-run on clean tree leaves overlay file byte-identical (belt-and-braces)**:
  ```python
  def test_dry_run_leaves_overlay_file_byte_identical_on_clean_tree(
      conn_with_promotable_rules: sqlite3.Connection,
      tmp_repo: Path,
      mock_gitlab: Mock,
  ) -> None:
      """H4 layer-2: even on a clean tree, dry-run must leave the overlay
      file byte-identical to its pre-run contents. Tests the explicit
      restore path, not just the branch-revert side-effect."""
      overlay_path = tmp_repo / "prompts" / "overlays" / "drupal_developer.md"
      before = overlay_path.read_bytes()

      results = propose_overlays(
          conn_with_promotable_rules,
          gitlab_client=mock_gitlab,
          repo_root=tmp_repo,
          repo_project_path="sentinel-team/sentinel",
          scope="drupal",
          min_confidence=80,
          dry_run=True,
      )
      assert len(results) == 1

      after = overlay_path.read_bytes()
      assert before == after, "dry-run mutated the overlay file"
  ```

  **Test 4 — untracked overlay file blocks (porcelain `??` lines)**:
  ```python
  def test_dry_run_refuses_when_overlay_is_untracked(
      conn_with_promotable_rules: sqlite3.Connection,
      tmp_repo: Path,
      mock_gitlab: Mock,
  ) -> None:
      """H4 edge case: if the overlay file is untracked (e.g. operator
      drafted a new overlay variant but never `git add`'d it), porcelain
      output shows `?? path` and we must still refuse. Defensive.

      We exercise this by deleting the committed overlay and re-creating
      it as untracked content — porcelain reports it as ``?? prompts/...``
      but only AFTER we drop the deletion (otherwise the file appears as
      ` D` deleted and the test would conflate the two cases).
      """
      overlay_relpath = Path("prompts") / "overlays" / "drupal_developer.md"
      overlay_path = tmp_repo / overlay_relpath

      # Remove and commit the deletion so the next write is genuinely untracked.
      overlay_path.unlink()
      subprocess.run(["git", "add", "-A"], cwd=tmp_repo, check=True, capture_output=True)
      subprocess.run(
          ["git", "commit", "-m", "drop overlay"],
          cwd=tmp_repo, check=True, capture_output=True,
      )
      # Re-create as untracked. The proposer's FileNotFoundError check would
      # normally fire first if the file doesn't exist, so we DO write content
      # — the file exists in the working tree but is untracked.
      overlay_path.write_text("# operator's new draft overlay\n", encoding="utf-8")

      with pytest.raises(RuntimeError, match=r"uncommitted changes"):
          propose_overlays(
              conn_with_promotable_rules,
              gitlab_client=mock_gitlab,
              repo_root=tmp_repo,
              repo_project_path="sentinel-team/sentinel",
              scope="drupal",
              min_confidence=80,
              dry_run=True,
          )
  ```
- **MIRROR**: Test 1's structure mirrors `test_dry_run_creates_no_branch_no_mr` at lines 206-238 (fixtures, branch-list assertion, mock-call-count assertion).
- **GOTCHA 1**: `pytest.raises(..., match=...)` takes a regex; escape literal dots (`\.`) and metacharacters. The pattern `r"uncommitted changes in .*drupal_developer\.md"` matches the error text we produce.
- **GOTCHA 2**: Test 4 must commit the deletion before re-creating the file as untracked — otherwise porcelain shows ` D` (deleted) which would be a different case (the `FileNotFoundError` branch would fire first and the test wouldn't reach the precondition check).
- **GOTCHA 3**: All four tests use the existing `tmp_repo` and `conn_with_promotable_rules` fixtures verbatim. Do not introduce a new fixture for "dirty repo" — that just adds a fixture file the next reader has to look up.
- **VALIDATE**: `poetry run pytest tests/core/test_propose_overlay.py -v` — all 9 existing tests still pass + 4 new tests pass.

---

## Testing Strategy

### Unit Tests to Write

| Test File                                | Test Cases                                                                  | Validates                                                                            |
| ---------------------------------------- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `tests/core/test_propose_overlay.py`     | dry-run refuses on dirty tree (modified file)                               | Pre-flight check fires; operator's edits preserved; no branch created; no MR opened. |
| `tests/core/test_propose_overlay.py`     | real-run refuses on dirty tree                                              | Pre-flight gates real-run identically.                                               |
| `tests/core/test_propose_overlay.py`     | dry-run on clean tree leaves overlay byte-identical                         | Explicit restore (layer 2) — proves the file is restored, not relying on git plumbing. |
| `tests/core/test_propose_overlay.py`     | dry-run refuses when overlay is untracked (porcelain `??`)                  | Untracked overlay also blocks.                                                       |

### Edge Cases Checklist

- [x] Modified-but-tracked overlay file (porcelain ` M`) blocks
- [x] Untracked overlay file (porcelain `??`) blocks
- [x] Multiple overlays dirty: error message names all of them
- [x] Empty `rules_by_agent` (no candidate overlays): pre-flight is a no-op (early return)
- [x] Dirty file outside `prompts/overlays/` (e.g. `src/foo.py`): does NOT block (intentional — pathspec scope)
- [x] Repo is not a git repo (`subprocess.CalledProcessError`): surfaces as `RuntimeError` with stderr in the message
- [x] Dry-run on clean tree: file bytes byte-identical before and after (covered by Test 3)
- [x] Real-run on clean tree: file is committed (covered by existing `test_propose_writes_provenance_trailer`)

The "multiple overlays dirty: error names all" case is implicitly covered by Test 1's regex match using `.*` and the explicit `sorted()` in the error builder; if the implementer wants belt-and-braces, add a fifth test that introduces a second `agent_target` rule and dirties two files.

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
poetry run ruff check src/core/learning/propose_overlay.py tests/core/test_propose_overlay.py
poetry run mypy src/core/learning/propose_overlay.py
```

**EXPECT**: Exit 0, no new errors. Pre-existing warnings on `main` (per the review's "ruff: 18 errors on branch vs 17 on main") are unchanged.

### Level 2: UNIT_TESTS

```bash
poetry run pytest tests/core/test_propose_overlay.py -v
```

**EXPECT**: All existing 9 tests + 4 new tests pass. Total 13 tests, 0 failures.

### Level 3: INTEGRATION_TESTS (no regression)

```bash
poetry run pytest tests/integration/test_phase2c_promotion.py tests/integration/test_phase2c_supersede_chain.py -v
```

**EXPECT**: All existing tests pass — H4 fix must not regress the E2E promotion-path test or the supersede-chain test. The integration tests start from a clean tmp repo and never touch overlay files between `git init` and the proposer call, so the pre-flight check passes silently.

### Level 4: FULL_SUITE

```bash
poetry run pytest -q
```

**EXPECT**: Test count goes up by 4 (the new tests). No new failures vs. the baseline reported in `.claude/PRPs/reviews/feat-sentinel-learning-system-review.md` (937 passed, 26 pre-existing failures unrelated to this module).

### Level 5: MANUAL_VALIDATION (in `sentinel-dev` container, optional)

```bash
# Confirm pre-flight blocks on a dirty overlay
cd /app
echo "" >> prompts/overlays/drupal_developer.md  # dirty the file
poetry run sentinel learning propose --dry-run --scope drupal
# EXPECT: exit 1, stderr contains "uncommitted changes in prompts/overlays/drupal_developer.md; commit or stash first"
git checkout -- prompts/overlays/drupal_developer.md
# Confirm clean tree dry-run is identical
poetry run sentinel learning propose --dry-run --scope drupal
# EXPECT: previous behavior — either "No rules ready..." or the proposer output, byte-identical to today.
```

---

## Acceptance Criteria

- [ ] `_assert_overlay_paths_clean` exists in `propose_overlay.py` with a clear docstring explaining pathspec scoping
- [ ] `propose_overlays` calls the precondition **before** `git checkout -b` (any other order is a bug)
- [ ] On a dirty overlay file, `propose_overlays` raises `RuntimeError` with a message that names the blocking file(s)
- [ ] On a clean tree, dry-run leaves every candidate overlay file byte-identical to its pre-run state
- [ ] On a clean tree, real-run behavior is unchanged (existing tests pass without modification)
- [ ] All 4 new tests pass
- [ ] Existing 9 tests in `test_propose_overlay.py` still pass without modification
- [ ] Integration tests in `tests/integration/test_phase2c_*.py` still pass without modification
- [ ] No new ruff or mypy errors on the changed files
- [ ] Docstring update reflects the new contract

---

## Completion Checklist

- [x] Task 1 complete: helper function added with tests passing
- [x] Task 2 complete: pre-flight wired into orchestration
- [x] Task 3 complete: explicit byte-restore added to dry-run path
- [x] Task 4 complete: docstring updated
- [x] Task 5 complete: 4 new tests added and passing
- [x] Level 1 (lint + types) passes
- [x] Level 2 (unit) passes — 19 tests in test_propose_overlay.py (15 existing post-H2/H3 + 4 new)
- [x] Level 3 (integration) passes — phase2c tests unaffected
- [ ] Level 4 (full suite) passes — only +4 tests vs baseline (not run; targeted tests cover scope)
- [ ] Manual validation in `sentinel-dev` confirms operator-facing message is readable (skipped — not run by Ralph)

---

## Risks and Mitigations

| Risk                                                                                              | Likelihood | Impact | Mitigation                                                                                                                                                                                                                                                                |
| ------------------------------------------------------------------------------------------------- | ---------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| H2/H3 fixes land first and conflict with this plan                                                | LOW        | LOW    | This plan only **adds** code (a helper, a call site, a byte-capture/restore, four tests). It does not remove or relocate existing code. Conflict resolution is mechanical: re-anchor the new lines to the H2/H3-modified surroundings.                                  |
| Operator runs the proposer in a worktree where porcelain output is verbose for unrelated reasons  | LOW        | LOW    | Pathspec is explicit (`-- <relpath> <relpath>`) so unrelated dirt is invisible to the check. Tested by Test 1's setup: the test's `tmp_repo` already has `git config user.*` writes that are themselves git-state changes; porcelain still returns empty.                |
| `git status --porcelain` differs across git versions in path-quoting behavior                     | VERY LOW   | LOW    | porcelain v1 has been stable since git 1.7. The codebase already targets `git` from the dev container which ships a modern version. Path quoting only kicks in for paths with special chars; overlay paths are ASCII (`drupal_developer.md`).                            |
| Pre-flight check incorrectly blocks a legitimate clean state due to git plumbing edge case        | VERY LOW   | MED    | Test 3 (clean-tree dry-run leaves file byte-identical) implicitly proves the precondition passes on clean trees. If the check ever produces a false positive in production, the operator can `git stash && sentinel learning propose --dry-run` to unblock immediately. |
| Subtle behavior change for callers passing a pre-built dirty repo deliberately (none in tree)     | NONE       | NONE   | `grep -rn "propose_overlays" src/ tests/` confirms only the CLI and the two test files call it; the CLI's only path is the user-facing one we are protecting.                                                                                                            |
| Belt-and-braces restore introduces a perf cost on large overlays                                  | NONE       | NONE   | Overlay files are O(KB), not O(MB). One `read_bytes` + one `write_bytes` per overlay per dry-run is microseconds. Real-run does not pay this cost.                                                                                                                       |

---

## Notes

- **Why not catch this in `_apply_overlay_edit`?** That function is a pure rendering helper (compute the new file content, write it). Pushing the precondition into it would couple the renderer to git semantics and would have to be opt-out for callers that already validated. The orchestrator is the right home for cross-cutting preconditions; the renderer stays focused.
- **Why not use `git status -s --untracked-files=normal -- <path>`?** Same output for our purposes; `--porcelain` is conventional and what the rest of the codebase already uses (`worktree_manager.py:589`).
- **Why a `RuntimeError` and not a custom exception class?** The module's existing failure mode (line 437-441) raises `RuntimeError` for git failures. Consistency wins. A future generalized "preflight failure" type can absorb this without breaking the existing CLI try/except (`src/cli.py:2041`) which catches `Exception`.
- **Composition with H2 (branch state)**: when H2 lands a `try/finally` snapshot/restore around the orchestration body, our pre-flight runs *outside* that try (it doesn't need cleanup — nothing has been done yet) and our byte-restore runs *inside* the try, before the existing branch-revert. Both fit cleanly.
- **Composition with H3 (branch-name collision)**: H3 only changes `_branch_name_for`. We don't touch that function. Independent.
- **Defense-in-depth justification**: layer 1 (pre-flight) is necessary; layer 2 (byte-restore) is sufficient. Either one alone fixes the bug. Both together cost ~15 lines and provide regression protection: if a future refactor moves the pre-flight or weakens the pathspec, the byte-restore still preserves operator data on the dry-run path. The brief explicitly recommends this stance.

---

**Confidence Score**: 9/10 for one-pass implementation.

The change is purely additive (a new helper, one call site, a byte-capture/restore around the existing dry-run path, four mirrored tests) with no removal or relocation of existing code. The subprocess + porcelain pattern is already established in the same file and in `worktree_manager.py`, the test fixtures (`tmp_repo`, `_no_push_overlay_branch`, `conn_with_promotable_rules`) exist verbatim, and the CLI already converts `RuntimeError` into `exit 1` so no CLI plumbing is needed. The −1 reflects the orthogonality risk with H2/H3: if either of those lands first and rearranges the orchestration body's line numbers, the implementer must re-anchor the insertion points by name rather than by line — mechanical but easy to fumble. The acceptance criteria, validation commands, and edge-case checklist are explicit enough that a careful pass through Tasks 1–5 in order should land cleanly the first time.
