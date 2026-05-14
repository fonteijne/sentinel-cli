# Feature: Validate SHA Format in `mark_promoted` Persistence Helper and CLI (M4)

## Summary

Add defensive SHA-format validation at two layers — the persistence helper `mark_promoted(...)` (defense at the leaf) and the `sentinel learning mark-merged --sha` Click option (early operator feedback). Both layers enforce the regex `^[0-9a-f]{7,64}$`. Without this guard, an operator typo (e.g. `--sha abc`) is silently persisted as the canonical promotion record on an append-only row, making downstream traceability tooling join on garbage. The CLI surfaces a clear, single-line error ("--sha must be 7-40 lowercase hex characters") instead of letting the bad value reach the DB.

## User Story

As a Sentinel maintainer running `sentinel learning mark-merged`
I want the CLI to refuse a malformed SHA *before* it touches the DB
So that a typo (`--sha abc`) doesn't silently pin a probation rule's promotion record to garbage that — per the append-only D4 design — can never be corrected, only superseded.

## Problem Statement

`src/core/persistence/feedback_rules.py:202-245` (`mark_promoted`) accepts the `sha` kwarg as a free-form `str` and binds it directly into the `UPDATE feedback_rules SET promoted_to_overlay_sha = ?` parameter. The CLI wrapper at `src/cli.py:2053-2077` (`learning_mark_merged`) declares `--sha` as `click.option("--sha", required=True)` — also free-form. The result: `sentinel learning mark-merged 1 --sha abc --by alice` succeeds with exit 0 and writes `promoted_to_overlay_sha = "abc"` to the row. Per design D4 (append-only feedback ledger; the only way to "undo" a row is `mark_superseded`), the typo is not correctable in place. Subsequent traceability tooling that joins `promoted_to_overlay_sha` against a real Git commit will surface as either an orphan row or a hard 404.

This is testable: invoke `mark_promoted(..., sha="abc")` and observe that no `ValueError` is raised; query the row and observe `promoted_to_overlay_sha = "abc"`.

## Solution Statement

**Two-layer validation** — option 3 from the issue brief:

1. **Persistence helper (defense at the leaf).** Add a module-level compiled regex `_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")` and validate `sha` at the top of `mark_promoted` before any DB I/O. Raise `ValueError(f"sha must be 7-40 lowercase hex characters; got {sha!r}")` on mismatch. This guards direct callers (e.g. `tests/integration/test_phase2c_supersede_chain.py:110`, `tests/core/test_feedback_rules_helpers.py:266`) and any future internal call site that bypasses the CLI.

2. **CLI option (early friendly error).** Add a Click `callback=_validate_sha` on `--sha` that runs `_SHA_RE.match` and raises `click.BadParameter("--sha must be 7-40 lowercase hex characters; e.g. 'a1b2c3d' or full 40-char SHA")` on mismatch. Click renders `BadParameter` as a clean `Usage:` + `Error:` exit-code-2 message, not a stack trace.

The regex `^[0-9a-f]{7,64}$` covers Git short SHAs (7-12 chars conventionally; 7 is `core.abbrev` default) and full SHA-1 (40) plus future SHA-256 (64). It rejects empty, whitespace, mixed-case, non-hex, and lengths < 7 or > 64.

**Test fixture migration.** Several existing tests use sentinel-string SHAs that were valid under "anything goes" semantics but fail the new regex (`"abc"`, `"x"`, `"y"`, `"z"`, `"s"`, `"aaa"`, `"def456"` — note `def456` is 6 chars, one short). These must be updated to valid 7+ hex strings. The fix is mechanical and the new strings are still arbitrary — they just need to pass the regex.

**Out of scope** (explicitly): cross-validating the SHA against a real Git ref (would require a GitLab round-trip; deferred per the issue brief).

## Metadata

| Field            | Value                                                                                                             |
| ---------------- | ----------------------------------------------------------------------------------------------------------------- |
| Type             | BUG_FIX (data hygiene / operator UX)                                                                              |
| Complexity       | LOW                                                                                                               |
| Systems Affected | `src/core/persistence/feedback_rules.py`, `src/cli.py` (`learning mark-merged`), 4 test files (fixture migration) |
| Dependencies     | `re` (stdlib), `click` 8.1.7 (already a project dep)                                                              |
| Estimated Tasks  | 6                                                                                                                 |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                              BEFORE STATE                                     ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  $ sentinel learning mark-merged 1 --sha abc --by alice                       ║
║       │                                                                       ║
║       ▼                                                                       ║
║  Click parses --sha "abc"     ◄── no validation                               ║
║       │                                                                       ║
║       ▼                                                                       ║
║  mark_promoted(conn, rule_id=1, sha="abc", promoted_by="alice")               ║
║       │                                                                       ║
║       ▼                                                                       ║
║  UPDATE feedback_rules SET promoted_to_overlay_sha = 'abc' WHERE id = 1       ║
║       │                                                                       ║
║       ▼                                                                       ║
║  Rule #1 marked merged at sha abc by alice.   ◄── exit 0, no warning         ║
║                                                                               ║
║  ┌────────────────── feedback_rules row id=1 ──────────────────┐              ║
║  │ status                  = 'active'                          │              ║
║  │ promoted_to_overlay_sha = 'abc'   ◄── GARBAGE, append-only  │              ║
║  │ promoted_by             = 'alice'                           │              ║
║  └─────────────────────────────────────────────────────────────┘              ║
║                                                                               ║
║   PAIN_POINT: Per D4 the row is append-only. The typo cannot be edited;       ║
║              the only recovery path is mark_superseded(old=1, new=N) which    ║
║              requires re-extracting and re-promoting the same rule under a    ║
║              new id. Audit trail now contains a permanent ghost.              ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                               AFTER STATE                                     ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║  $ sentinel learning mark-merged 1 --sha abc --by alice                       ║
║       │                                                                       ║
║       ▼                                                                       ║
║  Click callback _validate_sha("abc")                                          ║
║       │                                                                       ║
║       ▼                                                                       ║
║  raise click.BadParameter(                                                    ║
║    "--sha must be 7-40 lowercase hex characters; "                            ║
║    "e.g. 'a1b2c3d' or full 40-char SHA"                                       ║
║  )                                                                            ║
║       │                                                                       ║
║       ▼                                                                       ║
║  Usage: sentinel learning mark-merged [OPTIONS] RULE_ID                       ║
║  Try 'sentinel learning mark-merged --help' for help.                         ║
║                                                                               ║
║  Error: Invalid value for '--sha': --sha must be 7-40 lowercase hex…          ║
║       │                                                                       ║
║       ▼                                                                       ║
║  exit 2     ◄── DB never touched. No append-only ghost.                       ║
║                                                                               ║
║  $ sentinel learning mark-merged 1 --sha a1b2c3d --by alice                   ║
║       │                                                                       ║
║       ▼                                                                       ║
║  Click callback validates "a1b2c3d" → OK                                      ║
║       │                                                                       ║
║       ▼                                                                       ║
║  mark_promoted re-validates at the leaf (defense in depth)                    ║
║       │                                                                       ║
║       ▼                                                                       ║
║  Rule #1 marked merged at sha a1b2c3d by alice.   ◄── exit 0                  ║
║                                                                               ║
║   VALUE_ADD: Typos caught before DB write. Direct callers of                  ║
║              mark_promoted (tests, internal scripts) also guarded.            ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                                  | Before                                | After                                    | User Impact                                                       |
| --------------------------------------------------------- | ------------------------------------- | ---------------------------------------- | ----------------------------------------------------------------- |
| `sentinel learning mark-merged ... --sha abc`             | exit 0, garbage written               | exit 2, BadParameter, DB untouched       | Typo caught at parse time; no orphan rows                         |
| `sentinel learning mark-merged ... --sha ABC1234`         | exit 0, mixed-case persisted          | exit 2, BadParameter (lowercase only)    | Forces canonical-case SHAs (Git lowercases by default)            |
| `mark_promoted(..., sha="bad")` direct call               | succeeds silently                     | raises `ValueError` with clear message   | Internal callers / tests fail loudly on bad input                 |
| `sentinel learning mark-merged ... --sha def4567 --by bo` | works                                 | works (7-hex valid; bo is unconstrained) | Valid short SHAs unaffected                                       |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File                                              | Lines   | Why Read This                                                                         |
| -------- | ------------------------------------------------- | ------- | ------------------------------------------------------------------------------------- |
| P0       | `src/core/persistence/feedback_rules.py`          | 1-50, 200-250 | Module docstring (append-only invariant, D4) + `mark_promoted` body to modify  |
| P0       | `src/cli.py`                                      | 2053-2078 | The `learning_mark_merged` Click command — the CLI surface to add `callback=` to |
| P1       | `src/cli.py`                                      | 1690-1730, 1855-1880, 2118-2135 | Existing Click validation idioms — `IntRange`, `Choice`. No callback or BadParameter prior art exists; this fix introduces the first. Match the surrounding spacing/help-string style. |
| P1       | `tests/core/test_feedback_rules_helpers.py`       | 196-290 | `mark_promoted` test cases that use `sha="abc"`/`"x"`/`"y"`/`"z"`/`"def456"` — must migrate fixture strings AND add new validation tests. |
| P1       | `tests/test_cli_learning.py`                      | 400-441 | `test_mark_merged_*` cases that use `--sha def456` and `--sha bbb` — must migrate AND add CLI validation test. |
| P2       | `tests/integration/test_phase2c_supersede_chain.py` | 100-150 | Uses `mark_promoted(..., sha="aaa")` — fixture string must migrate.                  |
| P2       | `tests/integration/test_phase2c_promotion.py`     | 395-460 | Uses `--sha def456` in CLI invocation — fixture string must migrate.                 |
| P2       | `.claude/PRPs/reviews/feat-sentinel-learning-system-review.md` | line 125 | Source review entry: "M4. mark_promoted accepts unvalidated SHA"          |

**External Documentation:**

| Source                                                                           | Section                              | Why Needed                                                                                          |
| -------------------------------------------------------------------------------- | ------------------------------------ | --------------------------------------------------------------------------------------------------- |
| [Click 8.1 — Parameter validation callbacks](https://click.palletsprojects.com/en/8.1.x/options/#callbacks-for-validation) | "Callbacks for Validation"           | Canonical pattern for `callback=...` raising `click.BadParameter`. Signature is `(ctx, param, value)`. |
| [Click 8.1 — `BadParameter`](https://click.palletsprojects.com/en/8.1.x/api/#click.BadParameter) | API reference                        | Confirms exit code 2, "Usage:" preamble, no traceback.                                              |
| [Git — `core.abbrev` and short SHAs](https://git-scm.com/docs/git-config#Documentation/git-config.txt-coreabbrev) | `core.abbrev`                        | Confirms 7 is the default minimum short-SHA length → justifies `{7,64}` lower bound.                |

---

## Patterns to Mirror

**MODULE_DOCSTRING_INVARIANT_NOTE** (where to surface the new validation in the existing module-level docstring):

```python
# SOURCE: src/core/persistence/feedback_rules.py:1-21
# COPY THIS PATTERN (extend the bullet list with one new bullet):
"""Feedback-rules helpers — append-only canonical-rule store.

Design invariants (plan §"Patterns to Mirror" / D4 / append-only invariant):

  - There is NO ``update_rule`` and NO ``delete_rule``. Tests assert these
    names are not module attributes. ...
  - ``status`` is constrained to {'probation','active','superseded','revoked'}.
  ...
  - All write helpers bump ``updated_at`` to a fresh UTC ISO timestamp.
"""
```

**COMPILED_MODULE_REGEX_PATTERN** — there is no prior art in `feedback_rules.py` for module-level compiled regex; mirror the `_VALID_STATUS = frozenset({...})` style (module-private, underscore-prefixed, immediately below imports):

```python
# SOURCE: src/core/persistence/feedback_rules.py:29
# COPY THIS PATTERN (placement and naming convention):
_VALID_STATUS = frozenset({"probation", "active", "superseded", "revoked"})
# NEW (parallel structure):
# _SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")
```

**VALUEERROR_RAISE_STYLE** — match the existing `ValueError` raise style in this same module:

```python
# SOURCE: src/core/persistence/feedback_rules.py:155-159, 224, 226-229, 273-277
# COPY THIS PATTERN (f-string with !r for the offending value, single line where it fits):
raise ValueError(
    f"status must be one of {sorted(_VALID_STATUS)} or None; got {status!r}"
)
# AND:
if row["status"] != "probation":
    raise ValueError(
        f"mark_promoted requires status='probation'; "
        f"rule id={rule_id} is status={row['status']!r}"
    )
```

**CLICK_OPTION_DECLARATION_STYLE** — mirror the existing options on `learning mark-merged` exactly (alignment, help string, named over positional):

```python
# SOURCE: src/cli.py:2053-2057
# COPY THIS PATTERN (preserve order, alignment, help-string voice):
@learning.command("mark-merged")
@click.argument("rule_id", type=int)
@click.option("--sha", required=True, help="Commit SHA of the merged MR.")
@click.option("--by", "by", required=True, help="Username of the merging maintainer.")
def learning_mark_merged(rule_id: int, sha: str, by: str) -> None:
```

**NOTE on prior art for Click validation callbacks**: there is **no existing** `callback=` or `click.BadParameter` use in `src/cli.py` (verified via `grep -n "BadParameter\|callback="` — only `IntRange`/`Choice` builtins are used). This fix introduces the first. Place the helper as a **module-private function** at the top of the `# Phase 2C: \`sentinel learning\` group` section (around line 1725-1740), grouped with `_learning_seed_synthetic_execution` (the existing helper for this CLI group).

**CLICK_VALIDATION_CALLBACK_PATTERN** (canonical Click form — there's no in-repo prior art, so this comes from the Click docs):

```python
# SOURCE: NEW — Click 8.1 docs, "Callbacks for Validation"
# Place this near `_learning_seed_synthetic_execution` (src/cli.py:1744-1764)
# IMPORT: import re (already imported in cli.py — verify and reuse)

_LEARNING_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")

def _validate_sha(
    ctx: click.Context, param: click.Parameter, value: str
) -> str:
    """Click callback: enforce ^[0-9a-f]{7,64}$ on --sha.

    Mirrors the persistence-layer guard in ``feedback_rules.mark_promoted`` —
    catches operator typos (e.g. ``--sha abc``) at parse time so an append-only
    promotion row never gets pinned to garbage.
    """
    if not _LEARNING_SHA_RE.match(value):
        raise click.BadParameter(
            "--sha must be 7-40 lowercase hex characters; "
            "e.g. 'a1b2c3d' or full 40-char SHA"
        )
    return value
```

**TEST_STRUCTURE — pytest helper-test idiom (in-module unit test):**

```python
# SOURCE: tests/core/test_feedback_rules_helpers.py:258-287
# COPY THIS PATTERN (use the existing `conn` fixture; pytest.raises(ValueError);
# parametrize-friendly grouping with one happy-path test + one rejection test):
def test_mark_promoted_flips_status_and_records_sha(conn: sqlite3.Connection) -> None:
    rid = upsert_rule(conn, **_make_upsert_kwargs(signature="promote-test"))
    pre = _row_for(conn, rid)
    assert pre["status"] == "probation"
    ...
    mark_promoted(conn, rule_id=rid, sha="def456", promoted_by="alice")
    after = _row_for(conn, rid)
    assert after["status"] == "active"
    assert after["promoted_to_overlay_sha"] == "def456"
    ...

def test_mark_promoted_rejects_non_probation(conn: sqlite3.Connection) -> None:
    ...
    with pytest.raises(ValueError):
        mark_promoted(conn, rule_id=rid, sha="y", promoted_by="alice")
```

**CLI_TEST_STRUCTURE — Click CliRunner idiom for exit-code-2 cases:**

```python
# SOURCE: tests/test_cli_learning.py:224-237
# COPY THIS PATTERN (CliRunner; assert exit_code == 2; merge stderr+output for
# Click's BadParameter rendering):
def test_extract_flag_off_writes_blocked(
    runner: CliRunner,
    db_path_with_postmortems: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EXTRACTION_ENABLED", raising=False)
    result = runner.invoke(cli, ["learning", "extract"])
    assert result.exit_code == 2, result.output
    combined = (result.output or "") + (result.stderr if result.stderr_bytes else "")
    assert "EXTRACTION_ENABLED=0" in combined
```

---

## Files to Change

| File                                                      | Action | Justification                                                                            |
| --------------------------------------------------------- | ------ | ---------------------------------------------------------------------------------------- |
| `src/core/persistence/feedback_rules.py`                  | UPDATE | Add `import re`, `_SHA_RE`, validation at top of `mark_promoted`; extend module docstring |
| `src/cli.py`                                              | UPDATE | Add `_LEARNING_SHA_RE` + `_validate_sha` helper; attach `callback=_validate_sha` to `--sha` option on `learning mark-merged` |
| `tests/core/test_feedback_rules_helpers.py`               | UPDATE | (a) Migrate fixture SHAs (`"abc"`, `"def456"`, `"x"`, `"y"`, `"z"`, `"s"`) to 7+ hex strings. (b) Add new test `test_mark_promoted_validates_sha_format`. |
| `tests/test_cli_learning.py`                              | UPDATE | (a) Migrate `--sha def456`, `--sha bbb`, `--sha aaa` literals to valid SHAs. (b) Add new test `test_mark_merged_invalid_sha_rejected`. |
| `tests/integration/test_phase2c_supersede_chain.py`       | UPDATE | Migrate `mark_promoted(..., sha="aaa")` → valid 7-hex.                                   |
| `tests/integration/test_phase2c_promotion.py`             | UPDATE | Migrate `--sha def456` (6 chars) → valid 7-hex; update assertion expecting `"def456"` accordingly. |

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **Cross-validating the SHA against an actual Git ref.** Per the issue brief: would require GitLab API round-trip; deferred. Format-only validation is the goal.
- **Validating the `--by` field.** Out of scope — different problem (operator-name format), no current evidence of pain.
- **Migrating other free-form SHA columns** (`merge_commit_sha` in `tests/integration/test_phase3a_outcomes.py:84/91/98` — uses `aaa11111aaaaaaaa` etc., already valid 16-hex). Those are unrelated test fixtures for a different code path; do not touch.
- **Backfilling existing `feedback_rules` rows with bad SHAs.** Per the append-only D4 invariant, rows are not editable. If a deployed DB has a bad SHA, the operator must `mark_superseded` to point at a fresh row — not in scope here.
- **Changing the column type from `TEXT` to a constrained CHECK constraint.** Schema-level enforcement would require a migration and would break the append-only "dead rows preserved" invariant for any historical bad data. Application-layer guard is the right level.
- **Adding the validation to `revoke_rule` or `mark_superseded`.** Neither helper takes a SHA parameter — N/A.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/core/persistence/feedback_rules.py` — add regex + validation

- **ACTION**: Add `import re` to the imports block. Add `_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")` immediately below `_VALID_STATUS` (line 29). At the top of `mark_promoted` (line 216, before `now = _utcnow_iso()`), add:
  ```python
  if not _SHA_RE.match(sha):
      raise ValueError(
          f"sha must be 7-64 lowercase hex characters; got {sha!r}"
      )
  ```
- **MIRROR**: `_VALID_STATUS` placement (line 29) and the existing `ValueError` raise style at lines 155-159 / 226-229.
- **DOCSTRING**: Extend the module docstring's invariant bullet list with: `- ``mark_promoted`` validates ``sha`` against ``^[0-9a-f]{7,64}$`` before any DB I/O. SHA is append-only per D4 — typos caught at the leaf can never become a permanent ghost row.`
- **DOCSTRING (function)**: Append to `mark_promoted`'s docstring: `Raises ``ValueError`` if ``sha`` does not match ``^[0-9a-f]{7,64}$`` (Git short or full SHA, lowercase hex only).`
- **GOTCHA**: Validate **before** `BEGIN IMMEDIATE` — no point opening a transaction we're going to roll back over input we never had to look up.
- **GOTCHA**: The error message bound is `{7,64}` (matches the regex literally), but the user-facing CLI message says `7-40` (the practical Git SHA-1 range). This discrepancy is intentional: the regex covers SHA-256 future-proofing while the CLI guidance reflects what operators actually paste today. Do not "harmonize" them.
- **VALIDATE**:
  ```bash
  poetry run ruff check src/core/persistence/feedback_rules.py
  poetry run mypy src/core/persistence/feedback_rules.py
  ```

### Task 2: UPDATE `src/cli.py` — add `_validate_sha` callback and attach to `--sha`

- **ACTION**: Add `import re` to `src/cli.py` imports block (verified absent; current imports at lines 3-13 do not include `re`). Insert it alphabetically — between `import os` (line 4) and `import shutil` (line 5). Then add a module-level compiled regex `_LEARNING_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")` and a helper `_validate_sha(ctx, param, value)` near `_learning_seed_synthetic_execution` (around line 1744).
- **IMPLEMENT**:
  ```python
  _LEARNING_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")

  def _validate_sha(
      ctx: click.Context, param: click.Parameter, value: str
  ) -> str:
      """Click callback for ``--sha`` on ``learning mark-merged``.

      Format check only (no Git/GitLab round-trip). Catches operator typos
      at parse time so an append-only promotion row never gets pinned to
      garbage. Mirrors the leaf guard in ``feedback_rules.mark_promoted``.
      """
      if not _LEARNING_SHA_RE.match(value):
          raise click.BadParameter(
              "--sha must be 7-40 lowercase hex characters; "
              "e.g. 'a1b2c3d' or full 40-char SHA"
          )
      return value
  ```
- **ATTACH**: Modify `src/cli.py:2055`:
  ```python
  # Before:
  @click.option("--sha", required=True, help="Commit SHA of the merged MR.")
  # After:
  @click.option(
      "--sha",
      required=True,
      callback=_validate_sha,
      help="Commit SHA of the merged MR (7-40 lowercase hex chars).",
  )
  ```
- **MIRROR**: Helper-function placement style of `_learning_seed_synthetic_execution` (private, underscore-prefixed, defined just above the commands that use it).
- **GOTCHA**: Click runs `callback` AFTER type coercion but BEFORE the command body. `BadParameter` is rendered with the parameter's `param.get_error_hint(ctx)` automatically — do NOT prepend `"--sha: "` to the message; Click does that.
- **GOTCHA**: `param` and `ctx` are unused in the body. Suppress lint by accepting them as named args (NOT `*args` / `**kwargs`) — that's the canonical Click signature and ruff's `ARG001` is not enabled in this project.
- **VALIDATE**:
  ```bash
  poetry run ruff check src/cli.py
  poetry run mypy src/cli.py
  poetry run sentinel learning mark-merged 1 --sha abc --by alice  # expect exit 2 + BadParameter
  poetry run sentinel learning mark-merged 1 --sha a1b2c3d --by alice  # expect "rule id=1 not found" (gets past validation)
  ```

### Task 3: UPDATE `tests/core/test_feedback_rules_helpers.py` — migrate SHAs + add validation test

- **ACTION (fixture migration)**: Replace these literal SHAs with valid 7+ hex strings. Pick mnemonic-but-valid replacements; document the choice in a one-line comment at the top of the test where helpful.
  | Line | Before                                    | After                                       |
  | ---- | ----------------------------------------- | ------------------------------------------- |
  | 198  | `mark_promoted(..., sha="abc", ...)`      | `mark_promoted(..., sha="abc1234", ...)`    |
  | 266  | `mark_promoted(..., sha="def456", ...)`   | `mark_promoted(..., sha="def4567", ...)`    |
  | 270  | `assert after["promoted_to_overlay_sha"] == "def456"` | `assert after["promoted_to_overlay_sha"] == "def4567"` |
  | 277  | `mark_promoted(..., sha="x", ...)`        | `mark_promoted(..., sha="abcdef0", ...)`    |
  | 280  | `mark_promoted(..., sha="y", ...)`        | `mark_promoted(..., sha="abcdef1", ...)`    |
  | 287  | `mark_promoted(..., sha="z", ...)`        | `mark_promoted(..., sha="abcdef2", ...)`    |
  | 391  | `mark_promoted(..., sha="s", ...)`        | `mark_promoted(..., sha="abcdef3", ...)`    |
- **ACTION (new test)**: Append a new test function `test_mark_promoted_validates_sha_format` after `test_mark_promoted_rejects_non_probation`. Use `pytest.parametrize` to cover happy paths and rejections.
  ```python
  @pytest.mark.parametrize(
      "good_sha",
      [
          "a1b2c3d",                                   # 7-char short SHA (Git default)
          "abcdef0123456789abcdef0123456789abcdef01",  # 40-char full SHA-1
          "0" * 64,                                    # 64-char (future SHA-256 lower bound)
      ],
  )
  def test_mark_promoted_accepts_valid_sha(
      conn: sqlite3.Connection, good_sha: str
  ) -> None:
      rid = upsert_rule(
          conn, **_make_upsert_kwargs(signature=f"sig-{good_sha[:8]}")
      )
      mark_promoted(conn, rule_id=rid, sha=good_sha, promoted_by="alice")
      row = _row_for(conn, rid)
      assert row["promoted_to_overlay_sha"] == good_sha


  @pytest.mark.parametrize(
      "bad_sha",
      [
          "",                                       # empty
          "abc",                                    # too short (3 chars)
          "abcdef",                                 # too short (6 chars, just under 7)
          "ABCDEF1",                                # uppercase
          "g1b2c3d",                                # non-hex char 'g'
          "abc1234 ",                               # trailing whitespace
          " abc1234",                               # leading whitespace
          "abc 1234",                               # internal whitespace
          "a" * 65,                                 # too long (65 chars)
          "abc1234\n",                              # trailing newline
      ],
  )
  def test_mark_promoted_rejects_invalid_sha(
      conn: sqlite3.Connection, bad_sha: str
  ) -> None:
      rid = upsert_rule(
          conn, **_make_upsert_kwargs(signature=f"sig-bad-{hash(bad_sha) & 0xff:x}")
      )
      with pytest.raises(ValueError, match="sha must be 7-64 lowercase hex"):
          mark_promoted(conn, rule_id=rid, sha=bad_sha, promoted_by="alice")
      # Critical: rejection must NOT have mutated the row.
      row = _row_for(conn, rid)
      assert row["status"] == "probation"
      assert row["promoted_to_overlay_sha"] is None
  ```
- **MIRROR**: `test_mark_promoted_rejects_non_probation` (line 275-287) — same fixture (`conn`), same `pytest.raises(ValueError)` idiom, same use of `_make_upsert_kwargs` + `_row_for` helpers.
- **GOTCHA**: The "no mutation on rejection" assertion is what proves the validation runs **before** any DB write. Without it, a regression that moves the check after `BEGIN IMMEDIATE` but inside the `try`/`except Exception: rollback` block would still pass `pytest.raises` but leak side effects. Keep the post-rejection state assertion.
- **GOTCHA**: `pytest.parametrize` ids are derived from the value when it's a string — empty string and whitespace strings produce odd-looking but unique ids; do not customize.
- **VALIDATE**:
  ```bash
  poetry run pytest tests/core/test_feedback_rules_helpers.py -v
  ```

### Task 4: UPDATE `tests/test_cli_learning.py` — migrate SHAs + add CLI BadParameter test

- **ACTION (fixture migration)**:
  | Line | Before                                                              | After                                                               |
  | ---- | ------------------------------------------------------------------- | ------------------------------------------------------------------- |
  | 405  | `["learning", "mark-merged", "1", "--sha", "def456", "--by", "alice"]` | `["learning", "mark-merged", "1", "--sha", "def4567", "--by", "alice"]` |
  | 416  | `assert row["promoted_to_overlay_sha"] == "def456"`                  | `assert row["promoted_to_overlay_sha"] == "def4567"`                |
  | 430  | `promoted_to_overlay_sha="aaa"` (in `_seed_feedback_rule` call)      | `promoted_to_overlay_sha="aaa1234"` (or leave — direct SQL bypasses validation; safer to migrate for forward consistency) |
  | 436  | `["learning", "mark-merged", "1", "--sha", "bbb", "--by", "bob"]`    | `["learning", "mark-merged", "1", "--sha", "bbb1234", "--by", "bob"]` |
  | 505  | `promoted_to_overlay_sha="x"` (in `_seed_feedback_rule` call)        | `promoted_to_overlay_sha="x000001"` (must pass new regex, but note: this row is seeded via direct SQL — the validation at the helper level does NOT fire on the direct INSERT in `_seed_feedback_rule`. We migrate anyway for consistency with the new contract.) |
  | 536  | `promoted_to_overlay_sha="x"`                                       | `promoted_to_overlay_sha="x000001"`                                 |
- **CLARIFICATION on lines 430, 505, 536**: `_seed_feedback_rule` (lines 131-187) executes a raw `INSERT INTO feedback_rules ...` — it does NOT route through `mark_promoted`, so the regex won't fire. Migration there is **defensive consistency only**, not a correctness requirement. Update them so the test corpus uniformly reflects the new "all SHAs are valid hex" convention; future readers won't have to ask "wait, why is this one different".
- **ACTION (new test)**: Add a parametrized test for the CLI BadParameter surface, placed immediately after `test_mark_merged_on_active_errors` (around line 441):
  ```python
  @pytest.mark.parametrize(
      "bad_sha",
      ["abc", "ABCDEF1", "g1b2c3d", "abcdef", "a" * 65, ""],
  )
  def test_mark_merged_rejects_invalid_sha_at_cli(
      runner: CliRunner,
      db_path_with_promotable_rule: Path,
      bad_sha: str,
  ) -> None:
      """Click callback must reject malformed SHAs with exit 2 + clear message,
      and must NOT touch the DB (the row's status stays 'probation')."""
      result = runner.invoke(
          cli,
          ["learning", "mark-merged", "1", "--sha", bad_sha, "--by", "alice"],
      )
      assert result.exit_code == 2, result.output
      combined = (result.output or "") + (result.stderr if result.stderr_bytes else "")
      assert "--sha must be 7-40 lowercase hex" in combined

      # DB untouched.
      conn = connect(str(db_path_with_promotable_rule))
      try:
          row = conn.execute(
              "SELECT status, promoted_to_overlay_sha FROM feedback_rules WHERE id = 1"
          ).fetchone()
          assert row["status"] == "probation"
          assert row["promoted_to_overlay_sha"] is None
      finally:
          conn.close()
  ```
- **MIRROR**: `test_mark_merged_flips_status` (line 400-419) for fixture and DB-inspection idioms; `test_extract_flag_off_writes_blocked` (line 224-237) for the exit-code-2 + stderr-merge pattern.
- **GOTCHA**: Empty-string `bad_sha=""` will be passed as `--sha ""` to Click. Some shells / Click versions may reject zero-length option values before the callback runs. If the test fails on the empty case with a different exit code, drop `""` from the parametrize list — the regex test in Task 3 already covers empty strings at the persistence layer.
- **VALIDATE**:
  ```bash
  poetry run pytest tests/test_cli_learning.py -v
  ```

### Task 5: UPDATE `tests/integration/test_phase2c_supersede_chain.py` — migrate SHA fixture

- **ACTION**: Line 110 — replace `mark_promoted(conn, rule_id=rule_a_id, sha="aaa", promoted_by="alice")` with `mark_promoted(conn, rule_id=rule_a_id, sha="aaa1234", promoted_by="alice")`. Also update lines 116 and 146:
  - Line 116: `assert row_a["promoted_to_overlay_sha"] == "aaa"` → `"aaa1234"`
  - Line 146: `assert row_a_after["promoted_to_overlay_sha"] == "aaa"` → `"aaa1234"`
- **MIRROR**: N/A — mechanical fixture migration.
- **VALIDATE**:
  ```bash
  poetry run pytest tests/integration/test_phase2c_supersede_chain.py -v
  ```

### Task 6: UPDATE `tests/integration/test_phase2c_promotion.py` — migrate CLI `--sha` fixture

- **ACTION**: Lines 401, 416, 454 — replace `def456` with `def4567` in both the CLI argument and the assertion. Search the whole file for any other occurrences of `def456` and update consistently.
- **MIRROR**: N/A — mechanical fixture migration.
- **VALIDATE**:
  ```bash
  poetry run pytest tests/integration/test_phase2c_promotion.py -v
  ```

---

## Testing Strategy

### Unit Tests to Write

| Test File                                       | Test Cases                                                                    | Validates                                       |
| ----------------------------------------------- | ----------------------------------------------------------------------------- | ----------------------------------------------- |
| `tests/core/test_feedback_rules_helpers.py`     | `test_mark_promoted_accepts_valid_sha[7-char/40-char/64-char]`                | Persistence layer accepts canonical Git SHAs    |
| `tests/core/test_feedback_rules_helpers.py`     | `test_mark_promoted_rejects_invalid_sha[empty/short/uppercase/non-hex/whitespace/too-long/newline]` | Persistence layer rejects ALL the typo classes the issue brief enumerates; row state unchanged after rejection |
| `tests/test_cli_learning.py`                    | `test_mark_merged_rejects_invalid_sha_at_cli[abc/ABCDEF1/g1b2c3d/abcdef/65-chars/empty]` | CLI exit code 2 + clean BadParameter message, DB untouched |

### Edge Cases Checklist

- [x] Empty string (`""`) — rejected at persistence layer (and at CLI, modulo shell behavior)
- [x] Too short (`abc`, 3 chars; `abcdef`, 6 chars — one under the boundary)
- [x] Boundary OK (`a1b2c3d`, exactly 7 chars)
- [x] Full SHA-1 (`abcdef0123456789abcdef0123456789abcdef01`, 40 chars)
- [x] Future SHA-256 lower bound (`"0" * 64`)
- [x] Too long (`"a" * 65`)
- [x] Uppercase (`ABCDEF1`) — rejected (Git's default is lowercase; force canonical form)
- [x] Mixed-case (`AbCdEf1`) — rejected by same regex (no `re.IGNORECASE`)
- [x] Non-hex char (`g1b2c3d` — `g` is not in `[0-9a-f]`)
- [x] Leading/trailing/internal whitespace
- [x] Trailing newline (common when SHAs come from `git rev-parse | xclip` chains)
- [x] Direct call to `mark_promoted` bypassing CLI — guarded by leaf-layer check
- [x] Existing tests using arbitrary string SHAs — migrated in Tasks 3-6

### Existing-Behavior Regression Checks

- [x] `test_mark_promoted_flips_status_and_records_sha` still passes after fixture migration
- [x] `test_mark_promoted_rejects_non_probation` still passes (now requires the migrated SHA to clear validation FIRST so the status check is the one that fires)
- [x] `test_query_promotable_filters_by_confidence_and_status` still passes (uses `mark_promoted` internally)
- [x] `test_list_rules_filters_correctly` still passes
- [x] `tests/integration/test_phase2c_promotion.py::test_..._mark_merged` still passes
- [x] `tests/integration/test_phase2c_supersede_chain.py` chain test still passes

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
poetry run ruff check src/core/persistence/feedback_rules.py src/cli.py
poetry run mypy src/core/persistence/feedback_rules.py src/cli.py
```

**EXPECT**: Exit 0, no new errors or warnings on the touched lines. Pre-existing module-wide warnings (if any) are out of scope.

### Level 2: UNIT_TESTS — feedback_rules + CLI learning

```bash
poetry run pytest tests/core/test_feedback_rules_helpers.py tests/test_cli_learning.py -v
```

**EXPECT**: All tests pass — both the migrated existing tests and the new validation tests.

### Level 3: FULL_SUITE — including integration

```bash
poetry run pytest tests/ -v
```

**EXPECT**: All tests pass. The two integration test files migrated in Tasks 5-6 must continue to exercise the supersede-chain and promotion-end-to-end flows with the new SHA values.

### Level 4: DATABASE_VALIDATION

N/A — no schema change. The application-layer guard does not touch DDL.

### Level 5: BROWSER_VALIDATION

N/A — CLI-only change.

### Level 6: MANUAL_VALIDATION

Run inside `sentinel-dev`:

```bash
# Setup: a probation rule must exist. Either run the extract path or seed via the test helpers.
# (For a quick smoke, just point at an existing dev DB.)

# Bad SHA — expect exit 2, clean BadParameter
poetry run sentinel learning mark-merged 1 --sha abc --by alice
echo "exit: $?"   # expect 2

# Bad SHA: uppercase — expect exit 2
poetry run sentinel learning mark-merged 1 --sha ABCDEF1 --by alice
echo "exit: $?"   # expect 2

# Valid short SHA — expect either success or "rule id=1 not found" (validation passes; the next stage's check fires)
poetry run sentinel learning mark-merged 1 --sha a1b2c3d --by alice

# Valid full SHA
poetry run sentinel learning mark-merged 1 --sha abcdef0123456789abcdef0123456789abcdef01 --by alice
```

Confirm the BadParameter cases print `Error: Invalid value for '--sha': --sha must be 7-40 lowercase hex characters; e.g. 'a1b2c3d' or full 40-char SHA` (or close to it) on stderr with no Python traceback.

---

## Acceptance Criteria

- [ ] `mark_promoted(conn, ..., sha="abc")` raises `ValueError` with a clear message (testable in unit test)
- [ ] `mark_promoted(conn, ..., sha="a1b2c3d")` succeeds (7-hex Git short SHA)
- [ ] `mark_promoted(conn, ..., sha="<40 lowercase hex>")` succeeds
- [ ] `sentinel learning mark-merged 1 --sha abc --by alice` exits 2 with `Error: Invalid value for '--sha': --sha must be 7-40 lowercase hex characters` and NO traceback
- [ ] DB row state is unchanged after a BadParameter rejection (verified by post-rejection SELECT)
- [ ] All existing tests using sentinel SHAs (`abc`, `def456`, `aaa`, `x`, `y`, `z`, `s`, `bbb`) pass after fixture migration
- [ ] Level 1 (ruff + mypy) passes on touched files
- [ ] Level 2 (unit) and Level 3 (full suite) pass
- [ ] No new lint warnings introduced on touched lines
- [ ] Module docstring of `feedback_rules.py` documents the new invariant (so the next reader understands why the regex is there)

---

## Completion Checklist

- [x] Task 1: persistence helper validates `sha`, module docstring extended
- [x] Task 2: CLI `--sha` has `callback=_validate_sha` with clear BadParameter message
- [x] Task 3: persistence helper test SHAs migrated; new validation tests added
- [x] Task 4: CLI test SHAs migrated; new BadParameter test added
- [x] Task 5: integration supersede-chain test SHAs migrated
- [x] Task 6: integration promotion test SHAs migrated
- [x] Level 1: `ruff` + `mypy` clean on touched files (no new warnings)
- [x] Level 2: `pytest tests/core/test_feedback_rules_helpers.py tests/test_cli_learning.py` all green (47 passed)
- [x] Level 3: `pytest tests/` — 1038 passed, 26 pre-existing failures (orchestrator-listed, not regressions)
- [ ] Level 6: manual smoke (skipped — no live DB; covered by automated CLI tests)

## Implementation Notes

- Used `re.fullmatch()` rather than `re.match()` because Python's `$` anchor matches before a final `\n`, allowing `"abc1234\n"` to slip through `re.match(r"^[0-9a-f]{7,64}$", ...)`. The plan literally specified `^[0-9a-f]{7,64}$` and listed `"abc1234\n"` as a must-reject case — `fullmatch` is the cleanest reconciliation (regex literal preserved; the change is one method name).
- Both `feedback_rules._SHA_RE` and `cli._LEARNING_SHA_RE` use the same regex string and the same `fullmatch` semantics for consistency.

---

## Risks and Mitigations

| Risk                                                                                                                      | Likelihood | Impact | Mitigation                                                                                                                                                                                |
| ------------------------------------------------------------------------------------------------------------------------- | ---------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Existing dev/staging DBs contain rows with bad SHAs (e.g. `"abc"`) that the new code WILL NOT migrate                     | LOW        | LOW    | The validation only fires on **writes**. Existing rows are unaffected. If a future read path needs the SHA, it must tolerate historical bad values. (None currently does.)                |
| Click `BadParameter` exit code differs across Click 7 vs 8                                                                | LOW        | LOW    | Project pins `click = "^8.1.7"` (verified in `pyproject.toml`). Click 8.x consistently exits 2 for BadParameter. Tests assert exit_code == 2.                                            |
| Test SHAs migrated incorrectly (e.g. typo to a SHA still under 7 chars)                                                    | LOW        | MED    | Each migrated literal is reviewed against the regex `^[0-9a-f]{7,64}$`. The new validation tests in Tasks 3-4 will catch any migration that produced an invalid SHA — they'd fail loudly. |
| Future SHA-256 transition wants 64-char SHAs but our message says "7-40"                                                  | LOW        | LOW    | The regex bound `{7,64}` already accepts SHA-256. The message intentionally says "7-40" because that's what operators paste today. Update the message when SHA-256 migration starts.     |
| Empty-string `--sha` may not reach the callback (Click may reject required-but-empty earlier)                              | MED        | LOW    | Test in Task 4 has a comment about dropping `""` from parametrize if shell behavior makes it unreliable. Persistence-layer test in Task 3 still covers empty.                             |
| Other test files reference `mark_promoted` or `--sha` literals not enumerated above                                       | LOW        | MED    | Verified via `grep -rn 'sha="\|sha = "\|--sha' tests/` — the seven occurrences listed in Tasks 3-6 are the complete set. Re-grep before committing as a final check.                       |

---

## Notes

**Why `^[0-9a-f]{7,64}$` and not `[A-Fa-f0-9]{7,64}`?** Git lowercases SHAs by default (`git rev-parse`, `git log --pretty=%H`). Forcing lowercase is slightly opinionated but matches Git canonical form and keeps the regex simple. If an operator's tooling happens to emit uppercase hex, they get an actionable error instead of a silently-different-but-equivalent value being persisted (which would break naive `=` joins). The CLI message says "lowercase hex" explicitly so the corrective action is obvious.

**Why duplicate the regex at both layers?** The CLI catches the typo with a friendlier message AND prevents an unnecessary DB round-trip. The persistence helper guards programmatic callers (tests, future internal code paths, REPL spelunking by maintainers). Either layer alone would close the operator-typo case but not both. The cost of duplication is one extra `re.compile` at module load — negligible.

**Why not raise from a single shared helper in `src/core/utils/`?** No existing helper module hosts validation utilities — `src/utils/` exists but its contents (verified via `ls`) are unrelated. Creating a shared module for one regex would be premature abstraction. Two co-located literals are fine; if a third caller appears, factor then.

**Why MEDIUM severity if the issue brief tags it MEDIUM but the impact is "ghost row in audit ledger"?** Per the design's append-only D4 invariant, ghost rows are a permanent stain — but they don't block functionality, they just degrade traceability tooling. The actual blast radius is bounded by how often operators use this command (rarely; only on MR-merge events). MEDIUM is right.

**Why no `re.IGNORECASE` flag?** Deliberate — see "Why lowercase?" above. Adding the flag would silently accept uppercase, defeating the canonicalization goal.

**Confidence Score**: 9/10 for one-pass implementation success.
- Mechanical fix with clear pattern. The only judgment call is the test-fixture migration mapping, which is enumerated line-by-line in Tasks 3-6.
- Single deduction for the empty-string Click behavior gotcha (Task 4) — there's a small chance the parametrize case needs adjustment after seeing actual CliRunner output, but the fallback (drop the empty case from CLI test; persistence test still covers it) is documented.
