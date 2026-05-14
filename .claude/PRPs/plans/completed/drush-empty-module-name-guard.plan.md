# Feature: Drop empty/whitespace-only module-name matches in `parse_drush_config_validation`

## Summary

`parse_drush_config_validation` uses lazy regexes whose module-name capture group `[\w\- ]+?` legally matches whitespace-only spans because the character class includes the literal space. When drush prose has odd spacing (e.g. `"Unable to install the  module since it does not exist."`), the parser captures `"  "`, strips it to `""`, and still emits a polluting `StructuredError` like `"module '' is referenced in config/sync but is not installed."`. This plan adds a one-line boundary guard in each of the two affected loops to `continue` on empty captures, plus an explanatory comment and a regression test. The regex itself is left untouched to avoid regressions on real drush output.

## User Story

As an agent consuming the structured error stream
I want `parse_drush_config_validation` to silently drop empty/whitespace-only module-name captures
So that my error stream is not polluted with unhelpful `"module '' ..."` bullets that contain no actionable signal.

## Problem Statement

Concrete, testable failure mode:

Given input `"Unable to install the  module since it does not exist."` (note: two spaces — empty module slot), today `parse_drush_config_validation` returns a list with one `StructuredError` whose message is `"module '' is referenced in config/sync but is not installed. Hint: composer require drupal/ ..."`. The expected behaviour is to return `[]` for that input — no signal is better than a malformed signal.

## Solution Statement

Apply the lowest-risk fix from issue M2 option 2: filter at the boundary. After the existing `module = m.group(...).strip()` (and `dep = m.group(2).strip()` in the requires loop), `continue` if the stripped value is falsy. Add a brief comment above the first guard explaining *why* (the lazy `[\w\- ]+?` legally matches whitespace-only spans because `[ ]` is in the class; tightening the regex risks regressions on real-world drush spacing, so we drop empty captures at the boundary instead). Apply the guard in BOTH `_DRUSH_MODULE_REQUIRES` and `_DRUSH_MODULE_DOES_NOT_EXIST` loops. Add one new test method asserting the guard behaviour for both the HTML-wrapped and plaintext malformed shapes. The `AlreadyInstalledException` and `_DRUSH_GENERIC_EXCEPTION` paths do not capture human-prose names and are unaffected.

## Metadata

| Field            | Value                                          |
| ---------------- | ---------------------------------------------- |
| Type             | BUG_FIX                                        |
| Complexity       | LOW                                            |
| Systems Affected | `src/agents/_structured_errors.py`, drush adapter tests |
| Dependencies     | None (pure-Python stdlib `re`; pytest 7.4.3 already in `pyproject.toml`) |
| Estimated Tasks  | 3                                              |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                              BEFORE STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   ┌──────────────┐      ┌─────────────────────────────┐      ┌─────────────┐ ║
║   │ drush stdout │ ───► │ parse_drush_config_validation│ ───► │ Agent error │ ║
║   │ (malformed,  │      │  regex captures "  "         │      │ stream      │ ║
║   │  double-space│      │  strip() -> ""               │      │             │ ║
║   │  prose)      │      │  emits StructuredError       │      │             │ ║
║   └──────────────┘      └─────────────────────────────┘      └─────────────┘ ║
║                                                                       │      ║
║                                                                       ▼      ║
║                                                  ┌──────────────────────────┐║
║                                                  │ "module '' is referenced │║
║                                                  │  in config/sync ...      │║
║                                                  │  composer require        │║
║                                                  │  drupal/ ..."            │║
║                                                  │  (USELESS — agent cannot │║
║                                                  │  act on empty module)    │║
║                                                  └──────────────────────────┘║
║                                                                               ║
║   PAIN_POINT: Empty bullet pollutes structured error stream and triggers     ║
║   downstream "fix this" loops with no actionable target.                     ║
║   DATA_FLOW: drush text -> regex match (whitespace only) -> StructuredError ║
║              with empty fields -> agent prompt -> wasted iteration           ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                               AFTER STATE                                      ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   ┌──────────────┐      ┌─────────────────────────────┐      ┌─────────────┐ ║
║   │ drush stdout │ ───► │ parse_drush_config_validation│ ───► │ Agent error │ ║
║   │ (malformed,  │      │  regex captures "  "         │      │ stream      │ ║
║   │  double-space│      │  strip() -> ""               │      │  (empty for │ ║
║   │  prose)      │      │  GUARD: continue             │      │   malformed │ ║
║   │              │      │  (no StructuredError emitted)│      │   line)     │ ║
║   └──────────────┘      └─────────────────────────────┘      └─────────────┘ ║
║                                                                               ║
║   Real drush output (with valid module names) is unaffected:                  ║
║   "responsive_preview" still parses to a single missing-module entry.         ║
║                                                                               ║
║   VALUE_ADD: Agents see only actionable error bullets. Malformed input        ║
║   degrades silently to "no signal" rather than "garbage signal".              ║
║   DATA_FLOW: drush text -> regex match -> stripped name empty? skip          ║
║              -> only valid captures reach StructuredError list               ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location | Before | After | User Impact |
|----------|--------|-------|-------------|
| `_structured_errors.py` _DRUSH_MODULE_REQUIRES loop | emits `StructuredError(message="module '' requires '' which is not enabled...")` for empty captures | skips empty captures via `continue` | Agent error stream contains only actionable bullets |
| `_structured_errors.py` _DRUSH_MODULE_DOES_NOT_EXIST loop | emits `StructuredError(message="module '' is referenced in config/sync...")` for empty captures | skips empty captures via `continue` | Agent error stream contains only actionable bullets |
| `parse_drush_config_validation("Unable to install the  module since it does not exist.")` | returns 1-element list with empty-name bullet | returns `[]` | Malformed input degrades to "no signal" not "garbage signal" |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File | Lines | Why Read This |
|----------|------|-------|---------------|
| P0 | `src/agents/_structured_errors.py` | 300-455 | The function being patched and its surrounding regex/loop structure |
| P0 | `tests/agents/test_structured_error_adapters.py` | 311-441 | The TestParseDrushConfigValidation class — pattern to MIRROR exactly for the new test method |
| P1 | `src/agents/_structured_errors.py` | 145-160 | Existing `continue`-on-skip pattern in this same file (PHPStan adapter) — the idiom to mirror |
| P1 | `.claude/PRPs/reviews/feat-sentinel-learning-system-review.md` | (issue M2 section) | Original review note describing the bug and the chosen mitigation (option 2) |

**External Documentation:**

None. This is a pure-Python defensive guard using stdlib `re` only. No external library version pinning is relevant.

---

## Patterns to Mirror

**CONTINUE_ON_SKIP_PATTERN** (already used elsewhere in the same file):

```python
# SOURCE: src/agents/_structured_errors.py:155-157
# COPY THIS IDIOM (skip iterations whose captured payload is unusable):
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
```

**EXISTING DEDUP/CONTINUE PATTERN INSIDE THE SAME FUNCTION** (lines 365-371):

```python
# SOURCE: src/agents/_structured_errors.py:365-371
# COPY THIS PATTERN (the new guard sits ALONGSIDE this `key in seen` skip):
        for m in _DRUSH_MODULE_REQUIRES.finditer(output):
            module = m.group(1).strip()
            dep = m.group(2).strip()
            key = ("requires", f"{module}->{dep}")
            if key in seen:
                continue
            seen.add(key)
```

**TEST_STRUCTURE** (every method in `TestParseDrushConfigValidation` follows this shape):

```python
# SOURCE: tests/agents/test_structured_error_adapters.py:380-390
# COPY THIS PATTERN for the new malformed-input test:
    def test_plaintext_variant_without_em_tags(self) -> None:
        # Some drush versions / verbosity levels emit the message without HTML
        # wrapping. The parser must handle the unwrapped form too.
        plain = (
            "Unable to install the responsive_preview module since it "
            "does not exist."
        )
        out = parse_drush_config_validation(plain)
        assert len(out) == 1
        assert out[0]["rule"] == "drush.config.missing_module"
        assert "responsive_preview" in out[0]["message"]
```

**EXISTING FIXTURE CONSTANTS** (drush tests inline HTML constants — no external fixture file):

```python
# SOURCE: tests/agents/test_structured_error_adapters.py:320-329
# COPY THIS NAMING/SHAPE for the new malformed constant:
_DRUSH_MISSING = (
    '<li class="messages__item">Unable to install the '
    '<em class="placeholder">responsive_preview</em> module since it does '
    'not exist.</li>'
)
_DRUSH_REQUIRES = (
    '<li class="messages__item">Unable to install the '
    '<em class="placeholder">Drupal Symfony Mailer</em> module since it '
    'requires the <em class="placeholder">Mailer Transport</em> module.</li>'
)
```

---

## Files to Change

| File                                                  | Action | Justification                                                       |
| ----------------------------------------------------- | ------ | ------------------------------------------------------------------- |
| `src/agents/_structured_errors.py`                    | UPDATE | Add boundary guards in both drush module loops + explanatory comment |
| `tests/agents/test_structured_error_adapters.py`      | UPDATE | Add regression test asserting empty/whitespace captures are dropped |

No new files. No fixture file under `tests/fixtures/static_check_output/` — the existing drush tests inline HTML constants and the new test follows that convention.

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **Tightening the regex itself.** The issue explicitly asks for option 2 (boundary filter). Modifying `[\w\- ]+?` risks regressions on legitimate drush output with spacing variations (e.g. `"Drupal Symfony Mailer"`), which is exactly why option 2 was chosen over options 1 and 3.
- **Refactoring the broader drush adapter.** Out of scope per the issue. No changes to `_drush_module_slug`, `_DRUSH_ALREADY_INSTALLED`, `_DRUSH_GENERIC_EXCEPTION`, or the `parse_drush_config_validation` control flow beyond the two `continue` guards.
- **Adding non-drush parsers.** Out of scope per the issue.
- **Adding a fixture file under `tests/fixtures/static_check_output/`.** The existing drush tests inline HTML strings as Python constants (lines 320-329); the new test will follow the same convention. The fixture directory is for static-analyzer outputs (phpstan, ruff, mypy, pytest, phpunit, composer), not drush.
- **Logging the dropped match.** A `logger.debug` would be defensible but adds noise and a log-assertion to the test for marginal value. Silent drop is what the issue asks for.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/agents/_structured_errors.py` — guard the requires loop

- **ACTION**: Add a `continue` guard after the `.strip()` calls in the `_DRUSH_MODULE_REQUIRES.finditer(output)` loop (currently around lines 365-385).
- **IMPLEMENT**:
  - After the existing two lines `module = m.group(1).strip()` and `dep = m.group(2).strip()`, add a guard `if not module or not dep: continue`.
  - Add an explanatory comment immediately above the guard (this is the *first* of the two guards, so the explanation lives here):

    ```python
    # The lazy `[\w\- ]+?` capture in _DRUSH_MODULE_REQUIRES /
    # _DRUSH_MODULE_DOES_NOT_EXIST legally matches whitespace-only spans
    # because the character class includes ` `. Tightening the regex risks
    # regressions on real drush prose (e.g. multi-word names like
    # "Drupal Symfony Mailer"), so we drop empty captures at the boundary
    # instead. See issue M2 in feat-sentinel-learning-system-review.md.
    if not module or not dep:
        continue
    ```
- **MIRROR**: `src/agents/_structured_errors.py:155-157` (continue-on-skip idiom) and the existing `if key in seen: continue` at line 369 in this same function.
- **PLACEMENT**: The new guard goes BEFORE the existing `key = ("requires", ...)` / `if key in seen: continue` block, so the dedup `key` is never built from empty strings.
- **IMPORTS**: None required — `re`, `logging`, `StructuredError` already imported.
- **GOTCHA**: Place the guard *before* `seen.add(key)` so an empty key never enters `seen` and never blocks a later legitimate match (extremely defensive — also keeps `seen` clean for debugging).
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && python -c "from src.agents._structured_errors import parse_drush_config_validation; assert parse_drush_config_validation('Unable to install the  module since it requires the  module.') == [], 'requires-loop guard failed'; print('OK')"
  ```

### Task 2: UPDATE `src/agents/_structured_errors.py` — guard the does-not-exist loop

- **ACTION**: Add a `continue` guard after the `.strip()` call in the `_DRUSH_MODULE_DOES_NOT_EXIST.finditer(output)` loop (currently around lines 387-408).
- **IMPLEMENT**:
  - After the existing line `module = m.group(1).strip()`, add `if not module: continue` (no comment needed here — the explanatory comment lives above the first guard in Task 1; a one-line `# Same boundary guard as above — drop whitespace-only captures.` is enough).
- **MIRROR**: Same idiom as Task 1.
- **PLACEMENT**: Goes BEFORE the existing `key = ("missing", module)` / `if key in seen: continue` block, for the same reason as Task 1.
- **GOTCHA**: Do NOT remove or weaken the existing `key in seen` dedup — it handles a different concern (drush echoing the same line twice), unrelated to the empty-capture concern.
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && python -c "from src.agents._structured_errors import parse_drush_config_validation; assert parse_drush_config_validation('Unable to install the  module since it does not exist.') == [], 'does-not-exist-loop guard failed'; print('OK')"
  ```

### Task 3: UPDATE `tests/agents/test_structured_error_adapters.py` — add regression test

- **ACTION**: Add a new test method to `class TestParseDrushConfigValidation` (currently ends at line 441). Place it between the existing `test_plaintext_variant_without_em_tags` (line 380) and `test_already_installed_exception` (line 392) for thematic grouping with the other "shape variant" tests.
- **IMPLEMENT**: A single test method `test_empty_module_name_is_silently_dropped` covering three scenarios:

  ```python
  def test_empty_module_name_is_silently_dropped(self) -> None:
      # The lazy `[\w\- ]+?` regex legally matches whitespace-only spans
      # because the character class includes a literal space. Real-world
      # drush prose with odd spacing must NOT produce polluting bullets
      # like "module '' is referenced..." — the parser drops them at the
      # boundary instead. See issue M2 in
      # feat-sentinel-learning-system-review.md.

      # 1. HTML-wrapped variant with empty <em>.
      malformed_html = (
          '<li class="messages__item">Unable to install the '
          '<em class="placeholder">  </em> module since it does '
          'not exist.</li>'
      )
      assert parse_drush_config_validation(malformed_html) == []

      # 2. Plaintext variant with double-spacing (empty module slot).
      malformed_plain = (
          "Unable to install the  module since it does not exist."
      )
      assert parse_drush_config_validation(malformed_plain) == []

      # 3. Requires-variant: either side empty must drop the entry.
      malformed_requires = (
          "Unable to install the  module since it requires the  module."
      )
      assert parse_drush_config_validation(malformed_requires) == []

      # 4. Mixed: a malformed line alongside a valid one must yield only
      # the valid bullet (we do not regress on the surrounding loop).
      mixed = malformed_plain + "\n" + _DRUSH_MISSING
      out = parse_drush_config_validation(mixed)
      assert len(out) == 1
      assert out[0]["rule"] == "drush.config.missing_module"
      assert "responsive_preview" in out[0]["message"]
  ```
- **MIRROR**: `tests/agents/test_structured_error_adapters.py:380-390` (`test_plaintext_variant_without_em_tags`) for the inline-string + assert-on-output structure. Reuses the existing module-level `_DRUSH_MISSING` constant (line 320) — do not redefine it.
- **GOTCHA**: The empty-`<em>` HTML case (#1) only matches if the regex captures the `  ` between `<em ...>` and `</em>`. Verify by running the assertion — if scenario #1 unexpectedly returns `[]` even *without* the patch (i.e. the regex doesn't match at all), that's still correct behaviour, but scenarios #2 and #3 are the load-bearing assertions because they exercise the prose-only path where the regex *does* match whitespace.
- **GOTCHA**: Do NOT add a fixture file under `tests/fixtures/static_check_output/`. Drush tests in this codebase inline their inputs as Python string constants. The fixture directory is reserved for static-analyzer outputs (phpstan/ruff/mypy/pytest/phpunit/composer).
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && python -m pytest tests/agents/test_structured_error_adapters.py::TestParseDrushConfigValidation -v
  ```
  EXPECT: All 11 existing tests + 1 new test pass. No existing assertion changes its truth value.

---

## Testing Strategy

### Unit Tests to Write

| Test File                                            | Test Cases                                                                       | Validates                          |
| ---------------------------------------------------- | -------------------------------------------------------------------------------- | ---------------------------------- |
| `tests/agents/test_structured_error_adapters.py`     | `test_empty_module_name_is_silently_dropped` (4 sub-scenarios in one method)     | Boundary guard in both drush loops |

### Edge Cases Checklist

- [ ] Empty module in HTML-wrapped variant (`<em class="placeholder">  </em>`) — returns `[]`
- [ ] Empty module in plaintext variant (`"Unable to install the  module since it does not exist."`) — returns `[]`
- [ ] Empty module *and* empty dep in requires variant — returns `[]`
- [ ] Mixed input: one malformed line + one valid line — only valid line emits a bullet
- [ ] Existing valid HTML input (`_DRUSH_MISSING`, `_DRUSH_REQUIRES`) — unchanged output
- [ ] Existing plaintext input (`test_plaintext_variant_without_em_tags`) — unchanged output
- [ ] `AlreadyInstalledException` and `_DRUSH_GENERIC_EXCEPTION` paths — unchanged (they don't capture human-prose names)

### Regression Surface

The two new `continue` lines only fire when `m.group(...).strip()` is falsy. For ALL existing fixtures and all 11 existing test cases, every captured group is a non-empty word — the guard is a no-op for them. Byte-identical output is guaranteed for the existing test suite.

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

Looking for the project's lint/type-check tooling. The working directory is `/workspace/sentinel` (a Python project; `pyproject.toml` shows pytest 7.4.3). Run:

```bash
cd /workspace/sentinel && python -m py_compile src/agents/_structured_errors.py
```

If the project has `ruff` or `mypy` configured, also run them. Otherwise `py_compile` is sufficient for a fix this small.

**EXPECT**: Exit 0, no errors.

### Level 2: UNIT_TESTS (drush adapter only)

```bash
cd /workspace/sentinel && python -m pytest tests/agents/test_structured_error_adapters.py::TestParseDrushConfigValidation -v
```

**EXPECT**: 12/12 tests pass (11 existing + 1 new). The 11 existing tests must pass with byte-identical assertions — no test files modified except the addition of the new method.

### Level 3: FULL_SUITE (whole structured-errors test module)

```bash
cd /workspace/sentinel && python -m pytest tests/agents/test_structured_error_adapters.py -v
```

**EXPECT**: All tests in the file pass. No regressions in `parse_phpstan_json`, `parse_ruff_json`, `parse_mypy_text`, `parse_pytest_short`, `parse_phpunit_junit`, `parse_composer_validate`, or `normalize_failure_signature`.

### Level 4: DATABASE_VALIDATION

N/A — no schema changes.

### Level 5: BROWSER_VALIDATION

N/A — no UI changes.

### Level 6: MANUAL_VALIDATION

```bash
cd /workspace/sentinel && python -c "
from src.agents._structured_errors import parse_drush_config_validation

# Before fix: returns 1 polluting bullet. After fix: returns [].
malformed = 'Unable to install the  module since it does not exist.'
result = parse_drush_config_validation(malformed)
print('malformed ->', result)
assert result == [], f'expected [], got {result}'

# Existing valid input still works.
valid = '<li class=\"messages__item\">Unable to install the <em class=\"placeholder\">responsive_preview</em> module since it does not exist.</li>'
result = parse_drush_config_validation(valid)
print('valid    ->', len(result), 'entries,', result[0]['rule'] if result else 'none')
assert len(result) == 1
assert result[0]['rule'] == 'drush.config.missing_module'
print('OK')
"
```

**EXPECT**: `malformed -> []`, `valid -> 1 entries, drush.config.missing_module`, `OK`.

---

## Acceptance Criteria

- [ ] `parse_drush_config_validation(<malformed plaintext>)` returns `[]`
- [ ] `parse_drush_config_validation(<malformed requires>)` returns `[]`
- [ ] `parse_drush_config_validation(<mixed valid + malformed>)` returns ONLY the valid entries
- [ ] All 11 pre-existing tests in `TestParseDrushConfigValidation` pass without modification
- [ ] The full `test_structured_error_adapters.py` suite passes
- [ ] An explanatory comment exists above the first guard referencing issue M2 / the review file
- [ ] The regex itself is unchanged (`[\w\- ]+?` preserved verbatim)
- [ ] No fixture file added under `tests/fixtures/static_check_output/`
- [ ] No new logging — silent drop

---

## Completion Checklist

- [ ] Task 1 done and validated (one-liner sanity check passes)
- [ ] Task 2 done and validated (one-liner sanity check passes)
- [ ] Task 3 done and validated (`pytest TestParseDrushConfigValidation` passes 12/12)
- [ ] Level 1: `py_compile` succeeds
- [ ] Level 2: drush adapter tests pass
- [ ] Level 3: full module test suite passes
- [ ] Level 6: manual validation script prints `OK`
- [ ] All acceptance criteria met

---

## Risks and Mitigations

| Risk                                                                                                                    | Likelihood | Impact | Mitigation                                                                                                                                                                       |
| ----------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Guard placed AFTER `seen.add(key)` accidentally pollutes the dedup set with empty keys, blocking a later legit match    | LOW        | LOW    | Task 1 + Task 2 explicitly require placing the new guard BEFORE the `key = ...` / `if key in seen` block. Reviewer must verify ordering.                                         |
| Author tightens the regex instead of adding the boundary guard                                                          | LOW        | MEDIUM | Plan calls out option-2 explicitly in NOT_BUILDING; the regex literal must be byte-identical pre/post change. Reviewer compares the two regex constants in the diff.             |
| Empty-`<em>` HTML scenario (#1 in the test) doesn't actually trigger the regex in the way assumed, masking the test    | LOW        | LOW    | Scenarios #2 and #3 (plaintext double-space) are the load-bearing assertions; #1 is a sanity check. The mixed-input scenario #4 also exercises the guard alongside a real match. |
| Adding a fixture file in `tests/fixtures/static_check_output/` violates the directory's static-analyzer-only convention | LOW        | LOW    | Plan explicitly forbids new fixture files; the new test inlines strings like every other drush test in the file.                                                                 |
| Future contributor "fixes" the regex without removing the guard, leaving dead code                                       | LOW        | LOW    | Comment above the guard documents *why* it exists; a future regex tightener can grep for "issue M2" / "feat-sentinel-learning-system-review" and remove the guard cleanly.       |

---

## Notes

- The `AlreadyInstalledException` path (lines 412-428) and `_DRUSH_GENERIC_EXCEPTION` fallback (lines 432-449) do not capture human-prose module names — they capture either a fixed substring or a `Drupal\...Exception` class name where `\w` and `\\` cannot produce a whitespace-only match. They need no guard.
- `_drush_module_slug("")` returns `""` (verified: `"".strip().lower().replace(" ", "_") == ""`), confirming the bug surface described in the issue.
- The issue's option 2 was chosen explicitly because the existing regex's space tolerance is *load-bearing* for legitimate inputs like `"Drupal Symfony Mailer"` and `"Mailer Transport"` (already covered by `_DRUSH_REQUIRES` test). Tightening to `[\w\-]+(?:\s+[\w\-]+)*` would technically work but adds review surface; the boundary filter is two lines of trivially-reviewable code.
- The plan deliberately does NOT add a `logger.debug("dropped empty drush match")` because (a) the issue asks for silent drop, (b) it would require a caplog fixture in the test, and (c) the dropped-match rate in production is expected to be approximately zero — instrumenting it adds noise without value.
