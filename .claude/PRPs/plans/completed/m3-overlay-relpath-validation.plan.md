# Feature: Validate `scope` and `agent_target` in `_overlay_relpath_for` (M3 — defense-in-depth)

## Summary

Add a strict regex validator to `_overlay_relpath_for(scope, agent_target)` in
`src/core/learning/propose_overlay.py` so that neither argument can ever
contribute path-traversal characters (`/`, `\`, `..`, NUL, etc.) to the
overlay file path. Validation lives at the leaf of the helper (option 1 from
the brief) so the whole module is covered no matter who calls it. Add three
unit tests (one happy-path, two failure-path) and verify the regex matches
every agent name and `stack_type` value used in the codebase today.

## User Story

As a Sentinel maintainer
I want `_overlay_relpath_for` to refuse path-traversal-shaped inputs
So that a future bug or malicious row in `postmortems`/`feedback_rules` cannot redirect an overlay edit outside `prompts/overlays/`

## Problem Statement

`_overlay_relpath_for(scope, agent_target)` currently builds the path
`prompts/overlays/{scope}_{agent_target}.md` by direct f-string interpolation,
with no validation:

```python
def _overlay_relpath_for(scope: str, agent_target: str) -> Path:
    """``prompts/overlays/{scope}_{agent_target}.md`` (relative to repo root)."""
    return Path("prompts") / "overlays" / f"{scope}_{agent_target}.md"
```

Both args originate from database rows (`postmortems.stack_type` →
`feedback_rules.scope`; `postmortems.agent` → `feedback_rules.agent_target`).
If upstream ever produces a row like `agent='drupal_developer/../etc/passwd'`
or `stack_type='../etc'`, the resulting `Path` escapes the overlays directory
and `_apply_overlay_edit` would rewrite a file the operator never intended to
touch. Currently safe because the data flow is internal, but the cost of
defense is one regex.

## Solution Statement

1. Add a module-level regex `_SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")`
   immediately after the existing `_CONTEXT_EXCERPT_MAX_CHARS` constant block.
2. In `_overlay_relpath_for`, validate both `scope` and `agent_target` against
   this regex BEFORE building the `Path`. Raise `ValueError` with a message
   that includes which argument was invalid and its repr so debugging is easy.
3. Add three tests in `tests/core/test_propose_overlay.py` covering: the no-op
   happy path, traversal in `agent_target`, traversal in `scope`. Optional
   parametrized cases for empty string, uppercase, leading digit.

The regex `^[a-z][a-z0-9_]*$` matches every concrete value used today:

| Source | Concrete values | Matches `^[a-z][a-z0-9_]*$`? |
|---|---|---|
| `src/agents/` filenames | `drupal_developer`, `python_developer`, `plan_generator`, `security_reviewer`, `drupal_reviewer`, `confidence_evaluator`, `functional_debrief`, `base_agent`, `base_developer` | YES |
| `agent_target` (in tests) — `feedback_rules.agent_target` | `developer` (see `tests/core/test_propose_overlay.py:104`) | YES |
| `scope` — `postmortems.stack_type` | `drupal9`, `drupal10`, `drupal11` (see `src/stack_profiler.py:72-80,119`), test value `drupal` | YES (digits allowed after first char) |
| Existing overlay filenames | `drupal_developer`, `drupal_exploration`, `drupal_plan_generator`, `drupal_reviewer` (post-`{scope}_{agent_target}.md` join) | YES |

Rejected by the regex (intended): empty string (`""`), uppercase
(`Drupal`), leading digit (`9drupal`), traversal sequences
(`../etc`, `dev/../etc`), Windows separators (`dev\\..\\etc`), NUL bytes,
spaces, dots.

## Metadata

| Field            | Value                                    |
| ---------------- | ---------------------------------------- |
| Type             | BUG_FIX (security hardening)             |
| Complexity       | LOW                                      |
| Systems Affected | `src/core/learning/propose_overlay.py`, `tests/core/test_propose_overlay.py` |
| Dependencies     | stdlib `re` only                         |
| Estimated Tasks  | 2                                        |

---

## UX Design

This is an internal defense-in-depth fix with no user-facing UX. The "user"
is the next agent that touches `_overlay_relpath_for`.

### Before State

```
╔════════════════════════════════════════════════════════════════════╗
║                          BEFORE STATE                               ║
╠════════════════════════════════════════════════════════════════════╣
║                                                                    ║
║  postmortems.agent ──┐                                             ║
║                      │                                             ║
║                      ▼                                             ║
║  feedback_rules.agent_target ──────► _overlay_relpath_for(         ║
║                                          scope, agent_target)      ║
║                                              │                     ║
║                                              ▼ (no validation)     ║
║                       prompts/overlays/{scope}_{agent_target}.md   ║
║                                              │                     ║
║                                              ▼                     ║
║                                       _apply_overlay_edit          ║
║                                                                    ║
║  PAIN_POINT: a bug or malicious row with path separators in        ║
║              `agent` or `stack_type` lets the proposer edit any    ║
║              file the process can write to.                        ║
║                                                                    ║
╚════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔════════════════════════════════════════════════════════════════════╗
║                           AFTER STATE                               ║
╠════════════════════════════════════════════════════════════════════╣
║                                                                    ║
║  postmortems.agent ──┐                                             ║
║                      │                                             ║
║                      ▼                                             ║
║  feedback_rules.agent_target ──────► _overlay_relpath_for(         ║
║                                          scope, agent_target)      ║
║                                              │                     ║
║                                              ▼                     ║
║                          ┌─────────────────────────────┐           ║
║                          │ _SAFE_NAME_RE.fullmatch ?   │           ║
║                          │  ^[a-z][a-z0-9_]*$          │           ║
║                          └──────┬─────────────┬────────┘           ║
║                                 │ no          │ yes                ║
║                                 ▼             ▼                    ║
║                          ValueError    Path("prompts")/...         ║
║                                                                    ║
║  VALUE_ADD: structural guarantee that overlay edits land in        ║
║             prompts/overlays/ and nowhere else.                    ║
║                                                                    ║
╚════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location | Before | After | User Impact |
|---|---|---|---|
| `_overlay_relpath_for(scope, agent_target)` | accepts any `str`; returns `Path` unconditionally | rejects values that don't match `^[a-z][a-z0-9_]*$` with `ValueError` | An upstream bug that writes a malformed `agent` or `stack_type` value now fails loudly at the proposer step instead of silently corrupting an unrelated file |

---

## Mandatory Reading

| Priority | File | Lines | Why Read This |
|---|---|---|---|
| P0 | `src/core/learning/propose_overlay.py` | 184-186 | Function being modified — re-anchor by NAME (`_overlay_relpath_for`); the line range was 194-196 in the original review |
| P0 | `src/core/learning/propose_overlay.py` | 46-50 | The existing module-constant block (`_MR_DESCRIPTION_MAX_BYTES`, `_CONTEXT_EXCERPT_MAX_CHARS`); insert `_SAFE_NAME_RE` here so all module constants live together |
| P1 | `src/core/learning/propose_overlay.py` | 30-43 | Existing imports — `re` is NOT yet imported; needs to be added |
| P1 | `src/core/learning/propose_overlay.py` | 559-572 | Two existing call sites of `_overlay_relpath_for` (the candidate-overlays comprehension and the per-agent loop). After the fix these will surface a `ValueError` if a malformed row sneaks in — verify behavior is acceptable (it is: same fail-fast posture as the existing `FileNotFoundError` at line 616-619) |
| P1 | `tests/core/test_propose_overlay.py` | 1-50 | Existing test imports + module conventions (`from src.core.learning.propose_overlay import ...`, `pytest.raises(...)`, `tmp_repo` / `conn_with_promotable_rules` fixtures) |
| P1 | `tests/core/test_propose_overlay.py` | 480-502 | The closest existing failure-path test (`test_propose_missing_overlay_file_raises`) — mirror its structure |
| P2 | `src/stack_profiler.py` | 72-119 | Confirms `stack_type` values are `drupal9`, `drupal10`, `drupal11` (digits allowed after first char — important because the regex must accept them) |
| P2 | `src/agents/*.py` | filenames | Concrete agent names; cross-check against the regex |

**External Documentation:**

| Source | Section | Why Needed |
|---|---|---|
| [Python `re` docs (3.11)](https://docs.python.org/3/library/re.html#re.fullmatch) | `re.fullmatch` | We use `fullmatch` (not `match`/`search`) so the pattern is anchored on both ends — `match` only anchors the start, which would let `developer\nmalicious` slip past in a multiline string |
| [CWE-22: Path Traversal](https://cwe.mitre.org/data/definitions/22.html) | Mitigations → "Allowlist input validation" | Validates the chosen approach: an allowlist regex is the canonical mitigation pattern |

---

## Patterns to Mirror

**MODULE_CONSTANT_BLOCK** (place new constant alongside existing constants):

```python
# SOURCE: src/core/learning/propose_overlay.py:46-50
# COPY THIS PATTERN (extend with one more constant + a leading-comment block):
# Hard cap on the MR description payload (matches event-bus payload cap from
# src/core/events/bus.py). 64 KiB is the absolute ceiling — we truncate
# per-rule context_excerpts long before then so the description stays readable.
_MR_DESCRIPTION_MAX_BYTES = 64 * 1024
_CONTEXT_EXCERPT_MAX_CHARS = 200
```

**RAISE_PATTERN_INSIDE_HELPER** (mirror the proposer's existing exception style — descriptive message with the offending value via `!r`):

```python
# SOURCE: src/core/learning/propose_overlay.py:617-619
# COPY THIS PATTERN (raise descriptive exception with offending value):
raise FileNotFoundError(
    f"overlay {overlay_relpath} not found in repo {repo_root}"
)
```

**FAILURE_PATH_TEST_STRUCTURE** (mirror the existing missing-overlay test's shape):

```python
# SOURCE: tests/core/test_propose_overlay.py:480-502
# COPY THIS PATTERN:
def test_propose_missing_overlay_file_raises(
    conn_with_promotable_rules: sqlite3.Connection,
    tmp_repo: Path,
    mock_gitlab: Mock,
) -> None:
    overlay_path = tmp_repo / "prompts" / "overlays" / "drupal_developer.md"
    overlay_path.unlink()
    # ...
    with pytest.raises(FileNotFoundError):
        propose_overlays(...)
```

For the new tests we'll call `_overlay_relpath_for` DIRECTLY (no fixture
required) — it's a pure function. That's a simpler shape than the fixture-based
test above, but the `pytest.raises` idiom is identical.

**IMPORT_ADDITION_PATTERN** (existing imports are alphabetized within the stdlib block):

```python
# SOURCE: src/core/learning/propose_overlay.py:32-38
# CURRENT:
import logging
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol
# ADD `import re` between `logging` and `sqlite3` (alphabetical).
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `src/core/learning/propose_overlay.py` | UPDATE | Add `import re`, add `_SAFE_NAME_RE` constant, validate inside `_overlay_relpath_for` |
| `tests/core/test_propose_overlay.py` | UPDATE | Add unit tests for valid, traversal-in-agent, traversal-in-scope cases (and parametrized edge cases) |

No new files.

---

## NOT Building (Scope Limits)

- **DB-write-time sanitization.** Out of scope per brief. The fix is the leaf
  validator, not restructuring how `feedback_rules` stores `agent_target` /
  `scope`. If we add it later, it's additive.
- **Validating `scope`/`agent_target` at every other interpolation site**
  (branch name, MR title, MR description, log lines). Out of scope per brief
  ("simplest, narrowest blast radius"). The leaf validator transitively
  protects all sites in the proposer because `_overlay_relpath_for` is called
  before those sites in `propose_overlays` (see line 569-572 — the candidate
  pre-flight raises before any branch/commit/MR work).
  - Defensible because the call order in `propose_overlays` is:
    1. `query_promotable` (read DB)
    2. group rules by `agent_target`
    3. `[_overlay_relpath_for(scope, at) for at in rules_by_agent]` (line 568-571) → THIS RAISES if anything is malformed
    4. `_capture_starting_ref` / `git checkout -b` / commit / push / MR
  - So the validator effectively gates the whole proposer.
- **Renaming the helper** to e.g. `_safe_overlay_relpath_for`. The brief
  scopes this to a behavioral change, not a rename.
- **Changing `query_promotable`'s SQL** to filter malformed rows. Out of
  scope; that's "DB-write-time sanitization" by another name.
- **Adding logging when validation fails.** A `ValueError` with the offending
  value is enough — production log capture will surface the traceback. Adding
  a `logger.warning` would just be noise before the raise.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/core/learning/propose_overlay.py`

- **ACTION**: Add `import re`, add `_SAFE_NAME_RE` module constant, add validation inside `_overlay_relpath_for`.
- **IMPLEMENT**:
  1. Add `import re` to the stdlib import block (alphabetical — between `logging` and `sqlite3`).
  2. After the existing `_CONTEXT_EXCERPT_MAX_CHARS = 200` line, add:
     ```python
     # Allowlist for `scope` and `agent_target` interpolated into the overlay
     # file path. Both args originate from DB rows (postmortems.stack_type and
     # postmortems.agent respectively); a malformed/malicious upstream row with
     # path separators (e.g. agent='drupal_developer/../etc') would otherwise
     # let _overlay_relpath_for redirect the edit outside prompts/overlays/.
     # Defense in depth (M3): the data flow is internal today, but validating
     # here is one regex.
     #
     # Pattern: lowercase ASCII letter + lowercase ASCII alphanumerics or `_`,
     # matched as fullmatch (anchored both ends). Verified against every agent
     # name in src/agents/ (drupal_developer, python_developer, plan_generator,
     # security_reviewer, drupal_reviewer, confidence_evaluator,
     # functional_debrief, base_agent, base_developer) and every concrete
     # stack_type from src/stack_profiler.py (drupal9, drupal10, drupal11).
     _SAFE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
     ```
  3. Replace the body of `_overlay_relpath_for` with a validation guard:
     ```python
     def _overlay_relpath_for(scope: str, agent_target: str) -> Path:
         """``prompts/overlays/{scope}_{agent_target}.md`` (relative to repo root).

         Raises ``ValueError`` if either arg fails the ``_SAFE_NAME_RE``
         allowlist (defense in depth — see the constant's docstring for why).
         """
         if not _SAFE_NAME_RE.fullmatch(scope):
             raise ValueError(
                 f"_overlay_relpath_for: invalid scope {scope!r}; "
                 f"must match {_SAFE_NAME_RE.pattern}"
             )
         if not _SAFE_NAME_RE.fullmatch(agent_target):
             raise ValueError(
                 f"_overlay_relpath_for: invalid agent_target {agent_target!r}; "
                 f"must match {_SAFE_NAME_RE.pattern}"
             )
         return Path("prompts") / "overlays" / f"{scope}_{agent_target}.md"
     ```
- **MIRROR**:
  - Constant block insertion: `src/core/learning/propose_overlay.py:46-50`
  - Raise message style: `src/core/learning/propose_overlay.py:617-619` (`raise FileNotFoundError(f"overlay {overlay_relpath} not found in repo {repo_root}")`)
- **IMPORTS**: Add `import re` (between `import logging` and `import sqlite3`).
- **GOTCHA**:
  - Use `re.fullmatch`, NOT `re.match` — `match` only anchors the start, so an attacker could append a newline + traversal. The regex itself is anchored (`^…$`) but `fullmatch` is the explicit, double-checked form.
  - The regex MUST allow digits AFTER the first char so `drupal9`/`drupal10`/`drupal11` (real `stack_type` values from `src/stack_profiler.py:72-80`) are accepted. The pattern `^[a-z][a-z0-9_]*$` does this. Do NOT tighten to `^[a-z_]+$` — that would reject `drupal9` and break Drupal-9 projects.
  - Do NOT rename the function or change its signature; multiple call sites depend on `(scope, agent_target)` ordering (line 569, 615).
- **VALIDATE**:
  ```bash
  python -m pyflakes src/core/learning/propose_overlay.py
  python -c "from src.core.learning.propose_overlay import _overlay_relpath_for; \
      assert str(_overlay_relpath_for('drupal','developer')) == 'prompts/overlays/drupal_developer.md'; \
      assert str(_overlay_relpath_for('drupal9','plan_generator')) == 'prompts/overlays/drupal9_plan_generator.md'; \
      import pytest; \
      raised = False
      try: _overlay_relpath_for('drupal','../etc')
      except ValueError: raised = True
      assert raised, 'should have raised'"
  ```

### Task 2: UPDATE `tests/core/test_propose_overlay.py`

- **ACTION**: Add a small test block exercising `_overlay_relpath_for` directly.
- **IMPLEMENT**: Append at end of file (after the last existing test):
  ```python
  # ---------------------------------------------------------------------------
  # _overlay_relpath_for validation (M3 — defense in depth)
  # ---------------------------------------------------------------------------


  def test_overlay_relpath_for_valid_inputs() -> None:
      """No-op proof: known-good ``(scope, agent_target)`` produces the
      expected path under ``prompts/overlays/`` and does not raise."""
      from src.core.learning.propose_overlay import _overlay_relpath_for

      assert (
          str(_overlay_relpath_for("drupal", "developer"))
          == "prompts/overlays/drupal_developer.md"
      )
      # Real stack_type with a digit (drupal9/10/11 from stack_profiler.py)
      assert (
          str(_overlay_relpath_for("drupal10", "plan_generator"))
          == "prompts/overlays/drupal10_plan_generator.md"
      )


  def test_overlay_relpath_for_rejects_traversal_in_agent_target() -> None:
      """``agent_target`` with path separators (e.g. ``"../etc"``) raises
      ``ValueError`` at the leaf — defense in depth even if the DB ever holds
      a malformed row."""
      from src.core.learning.propose_overlay import _overlay_relpath_for

      with pytest.raises(ValueError, match=r"invalid agent_target"):
          _overlay_relpath_for("drupal", "../etc")


  def test_overlay_relpath_for_rejects_traversal_in_scope() -> None:
      """``scope`` is also DB-sourced (postmortems.stack_type) and is
      interpolated into the filename — same validation."""
      from src.core.learning.propose_overlay import _overlay_relpath_for

      with pytest.raises(ValueError, match=r"invalid scope"):
          _overlay_relpath_for("../etc", "developer")


  @pytest.mark.parametrize(
      "scope,agent_target",
      [
          ("", "developer"),                      # empty scope
          ("drupal", ""),                         # empty agent_target
          ("Drupal", "developer"),                # uppercase rejected
          ("drupal", "Developer"),                # uppercase rejected
          ("9drupal", "developer"),               # leading digit rejected
          ("drupal", "9developer"),               # leading digit rejected
          ("drupal", "developer/extra"),          # forward slash
          ("drupal", "developer\\extra"),         # backslash
          ("drupal", "developer.md"),             # dot
          ("drupal", "developer extra"),          # space
          ("drupal", "developer\x00etc"),         # NUL byte
      ],
  )
  def test_overlay_relpath_for_rejects_malformed_inputs(
      scope: str, agent_target: str,
  ) -> None:
      from src.core.learning.propose_overlay import _overlay_relpath_for

      with pytest.raises(ValueError):
          _overlay_relpath_for(scope, agent_target)
  ```
- **MIRROR**:
  - Failure-path test shape: `tests/core/test_propose_overlay.py:480-502` (`test_propose_missing_overlay_file_raises`)
  - Module-level import style at the top of the file already imports `pytest`; the new tests reuse it.
- **IMPORTS**: Existing `import pytest` at top of file (line 32) is sufficient. The new tests do localized `from src.core.learning.propose_overlay import _overlay_relpath_for` inside each function (mirrors the existing convention in this file of importing private helpers per-test rather than at module top — see how `propose_module` and `propose_overlays` are imported once at the top, but private internals are imported only where used).
- **GOTCHA**:
  - Use `match=r"invalid agent_target"` and `match=r"invalid scope"` so we assert the error message routes the operator to the right argument. If a future refactor changes the error message wording, these matches will (correctly) flag it.
  - The `\x00` NUL-byte case: Python `re.fullmatch` on `"developer\x00etc"` will fail the pattern (NUL is not in `[a-z0-9_]`), so this asserts the validator catches NUL — important because some filesystems treat NUL as path-terminator.
  - Do NOT use `match=r"^invalid"` with leading anchor — `pytest.raises(match=...)` uses `re.search` semantics; an unanchored substring match is sufficient and more forgiving to wording tweaks.
- **VALIDATE**:
  ```bash
  pytest tests/core/test_propose_overlay.py -k "_overlay_relpath_for" -v
  pytest tests/core/test_propose_overlay.py -v   # full file — confirm no regressions
  ```

---

## Testing Strategy

### Unit Tests to Write

| Test | Validates |
|---|---|
| `test_overlay_relpath_for_valid_inputs` | Happy path — no behavioral change for valid `(scope, agent_target)` pairs, including digit-bearing scopes (`drupal10`) |
| `test_overlay_relpath_for_rejects_traversal_in_agent_target` | `ValueError` raised when `agent_target` contains `..`/`/` |
| `test_overlay_relpath_for_rejects_traversal_in_scope` | `ValueError` raised when `scope` contains `..`/`/` |
| `test_overlay_relpath_for_rejects_malformed_inputs` (parametrized) | `ValueError` raised for empty, uppercase, leading-digit, slash, backslash, dot, space, NUL-byte inputs in either argument |

### Edge Cases Checklist

- [x] Empty string → covered by parametrized test
- [x] Uppercase → covered
- [x] Leading digit → covered (`9drupal`)
- [x] Forward slash → covered
- [x] Backslash (Windows path separator) → covered
- [x] Dot (`.md` suffix injection attempt) → covered
- [x] Space → covered
- [x] NUL byte (filesystem terminator on POSIX) → covered
- [x] Unicode lookalikes (e.g. Cyrillic `а` U+0430) → implicitly rejected because the regex is ASCII-only `[a-z]`. Not parametrized to keep the test concise; add later if a real concern.

### Regression Check

The change introduces a `ValueError` on a previously-allowed call shape. Risk:
existing tests in `tests/core/test_propose_overlay.py` and
`tests/integration/test_phase2c_promotion.py` could break if any test
fixture seeds a row with an invalid `agent_target` or `scope`. Mitigation:
the tests use `agent_target='developer'` and `scope='drupal'` consistently
(verified — see lines 104, 141, 236, etc., and the `tmp_repo` fixture creates
`prompts/overlays/drupal_developer.md`). Both are valid under the new regex.
Run the full test file as a regression check (validation step in Task 2).

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
python -m pyflakes src/core/learning/propose_overlay.py tests/core/test_propose_overlay.py
```

**EXPECT**: Exit 0, no errors. (Project does not currently use a configured
linter wrapper — pyflakes is the minimal sanity check; if `ruff` is available
in the dev container, also run `ruff check src/core/learning/propose_overlay.py`.)

### Level 2: UNIT_TESTS (targeted)

```bash
pytest tests/core/test_propose_overlay.py -k "_overlay_relpath_for" -v
```

**EXPECT**: 4 tests pass (1 happy + 2 traversal + 1 parametrized with 11 cases = 14 individual assertions, all green).

### Level 3: UNIT_TESTS (full file regression)

```bash
pytest tests/core/test_propose_overlay.py -v
```

**EXPECT**: All previously-passing tests still pass (no behavioral change for valid inputs).

### Level 4: INTEGRATION

```bash
pytest tests/integration/test_phase2c_promotion.py -v
```

**EXPECT**: Pass — uses `agent_target='developer'`, `scope='drupal'` per fixture, both valid under the new regex.

### Level 5: FULL_SUITE

```bash
pytest tests/ -x -q
```

**EXPECT**: Full suite green. Run `-x` so a regression elsewhere fails fast.

### Level 6: MANUAL_VALIDATION (optional smoke)

```bash
python -c "
from src.core.learning.propose_overlay import _overlay_relpath_for
# Real values from src/stack_profiler.py and src/agents/.
for scope in ('drupal', 'drupal9', 'drupal10', 'drupal11'):
    for agent in ('developer', 'plan_generator', 'reviewer', 'exploration',
                  'security_reviewer', 'confidence_evaluator', 'functional_debrief'):
        p = _overlay_relpath_for(scope, agent)
        assert str(p) == f'prompts/overlays/{scope}_{agent}.md', p
print('all real-world combos accepted')
"
```

**EXPECT**: prints `all real-world combos accepted`. Confirms no real value is rejected.

---

## Acceptance Criteria

- [x] `_overlay_relpath_for` raises `ValueError` for `agent_target` containing path traversal characters
- [x] `_overlay_relpath_for` raises `ValueError` for `scope` containing path traversal characters
- [x] `_overlay_relpath_for("drupal", "developer")` continues to return `Path("prompts/overlays/drupal_developer.md")` byte-for-byte
- [x] `_overlay_relpath_for("drupal10", "plan_generator")` continues to return the expected path (digits-after-first-char compatibility)
- [x] Module-level `_SAFE_NAME_RE` constant exists, sits alongside the other module constants, has a comment explaining why
- [x] All existing tests in `tests/core/test_propose_overlay.py` and `tests/integration/test_phase2c_promotion.py` continue to pass
- [x] At least 3 new tests added: 1 happy-path + 1 traversal-in-agent + 1 traversal-in-scope (parametrized edge cases are bonus)
- [x] `pytest tests/` exits 0 (no regressions; pre-existing failures on `main` in test_environment_manager, test_jira_server_client, test_plan_generator, test_worktree_manager, test_agent_integration, test_agent_sdk_* are not regressions)

---

## Completion Checklist

- [x] Task 1 complete (module change applied, `import re` added, constant added, function body validated)
- [x] Task 2 complete (4 new tests landed at end of test file)
- [x] Level 1 (ruff in lieu of pyflakes; pyflakes not installed) passes
- [x] Level 2 (targeted M3 tests) passes — 14 passed
- [x] Level 3 (full propose_overlay test file) passes — 33 passed
- [x] Level 4 (integration test) passes — 1 passed
- [x] Level 5 (full suite) passes (only pre-existing failures remain)
- [x] Level 6 (manual smoke) prints expected message

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Regex rejects a legitimate future agent name (e.g. `agent-with-dash`, `Agent2`) | LOW | LOW (caught immediately by tests when introduced) | The regex is documented inline; future agents who need different naming must update both the constant AND its docstring AND add a test case. The error message includes the regex pattern, so the failure mode is self-explanatory. |
| Regex rejects a legitimate future stack_type (e.g. `python3.11`) | LOW-MED | LOW | Dot is intentionally rejected. If `python3.11` appears as a `stack_type`, normalize it to `python311` upstream (in `src/stack_profiler.py`) — the overlay filename can't contain a dot anyway without confusing other tooling. |
| Production data already contains a malformed `agent` or `stack_type` row | LOW | MED (proposer would raise on the first run after deploy) | Acceptable failure mode — that's exactly what the validator is for. The exception bubbles through `propose_overlays` BEFORE any branch/commit/MR work (see line 568-572), so no partial state is created. The operator sees a clear `ValueError` naming the offending value and can clean the DB row. |
| Someone copies `_overlay_relpath_for` and forgets the validation | LOW | LOW | Out of scope; addressed by code review. The validation lives at the leaf (option 1 from the brief) precisely so every caller is covered without per-caller diligence. |

---

## Notes

**Why option 1 (validate at the leaf)?** The brief explicitly picks option 1
("simplest, narrowest blast radius"). Option 2 (validate at the DB read
boundary) would require touching `query_promotable` in
`src/core/persistence.py` AND every other reader of `feedback_rules` /
`postmortems`. Option 3 (both) is belt-and-braces but unnecessary today;
option 1 alone closes the path-traversal vector for the only consumer that
turns these strings into a filesystem path. If a second consumer appears
later, that consumer should either reuse `_overlay_relpath_for` or carry its
own leaf validator.

**Why `re.fullmatch` and not `Path.resolve()`-based escape detection?**
Allowlist-based validation is the canonical defense (CWE-22); it has zero
filesystem dependencies, is deterministic, and rejects malformed input at the
earliest possible point. Resolve-based detection would still hit the
filesystem to verify the resulting path lives under `prompts/overlays/`,
adding I/O and a TOCTOU window. Allowlist is also less likely to silently
accept future weirdness (e.g. symlink shenanigans).

**Why fail-loud instead of fail-silent (e.g. logger + skip)?** Silent skip
would hide the upstream bug. The existing code at line 617-619 already raises
`FileNotFoundError` for missing overlays (loud failure mode), so the loud
`ValueError` is consistent with the module's posture. The integration test in
`tests/integration/test_phase2c_promotion.py` uses good data, so it won't
notice; production telemetry will surface the traceback if a malformed row
ever appears.

**Why no test for the regex compilation itself?** Compiling a regex literal
with no dynamic input cannot fail at runtime; it would fail at module import
and every other test would already fail. No need for a dedicated test.

**Future work (out of scope, file as separate issue if interesting):**
1. Apply the same allowlist at the database boundary (option 2 from the
   brief) — would be a `CHECK` constraint in the SQL migration plus a
   defensive validator in `src/core/persistence.py`'s insert paths.
2. Add a property-based test (e.g. `hypothesis`) that asserts no string
   matching `_SAFE_NAME_RE` can produce a `Path` outside `prompts/overlays/`.
   Belt-and-braces; the regex makes the proof obvious by inspection.
