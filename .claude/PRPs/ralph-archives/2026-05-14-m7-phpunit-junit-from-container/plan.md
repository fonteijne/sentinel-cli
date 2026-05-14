# Feature: M7 — Surface PHPUnit JUnit Results From The Appserver Container

## Summary

When PHPUnit runs inside the per-ticket appserver container (the standard
DooD path), the JUnit XML it writes via `--log-junit` lives in the
container's filesystem and is invisible to the host-side parser. The host
check `Path("/tmp/phpunit-junit.xml").exists()` always returns `False` in
this path, so `_parse_test_output` returns `[]`, and the verifier loop's
PHPUnit signal is wholly absent on the Drupal stack — only PHPStan and
composer-validate carry through. We fix this by reading the JUnit XML out
of the container with `env_manager.exec(["cat", _PHPUNIT_JUNIT_PATH])`
when a container env is attached, and falling back to the existing
host-path read when it is not.

## User Story

As Sentinel's verifier loop on the Drupal stack
I want PHPUnit failures to produce structured errors in the refine prompt
So that the developer agent has the strongest available signal to fix what's actually broken instead of converging only on static checks

## Problem Statement

Verified present at `src/agents/drupal_developer.py:23,409-432`. Today:

1. `_get_test_command` includes `--log-junit=/tmp/phpunit-junit.xml`
   (path inside whatever process runs phpunit — host OR appserver
   container).
2. `_run_tests_in_container` (in `base_developer.py:1278-1333`) runs
   phpunit via `env_manager.exec(...)`. The XML lands in the appserver
   container's `/tmp/`.
3. `_parse_test_output` does `Path("/tmp/phpunit-junit.xml").exists()` on
   the *host* (sentinel-dev). The appserver and sentinel-dev share neither
   `/tmp` nor `/app` — `/app` is a per-ticket **named Docker volume**
   (`sentinel-projects-<slug>`), seeded one-way from the worktree at
   setup time (see `environment_manager.py:292-354`). There is **no**
   bidirectional bind mount; files written inside the container are not
   visible to the host.
4. The host-side check returns `False`, parser returns `[]`, the
   verifier-loop refine prompt has no PHPUnit-derived structured errors
   on container failures.

Test: today, when phpunit fails inside the container,
`run_tests(...)["structured_errors"]` is `[]` even though the JUnit XML
exists with failures and the existing `parse_phpunit_junit` would yield
3+ entries from it.

## Solution Statement

`_parse_test_output` learns about the container. When `_env_manager` and
`_env_ticket_id` are set, it pulls the JUnit XML out of the appserver via
`env_manager.exec(["cat", _PHPUNIT_JUNIT_PATH], ...)` and feeds the bytes
to `parse_phpunit_junit`. When no environment is attached, it preserves
existing host-path behavior — `Path(...).exists()` + `read_text()`.

This keeps all parser/error wiring identical and adds exactly one
container exec on test failures (the path that today silently returns
`[]`). Static-check parsers (`run_static_checks`) are entirely untouched.

## Metadata

| Field            | Value                                          |
| ---------------- | ---------------------------------------------- |
| Type             | BUG_FIX                                        |
| Complexity       | LOW                                            |
| Systems Affected | drupal verifier loop (loop A signal quality)   |
| Dependencies     | none new — uses existing `EnvironmentManager.exec` |
| Estimated Tasks  | 4                                              |

---

## CRITICAL CONCERN — RAISED LOUDLY

**The user's preferred approach (#3: write JUnit to a path under `/app`
because `/app` is "already bind-mounted from host per CLAUDE.md") DOES
NOT WORK with the actual codebase.**

Per `lando_translator.py:341` and `environment_manager.py:256-354`:
- `/app` in the appserver maps to a **named Docker volume**
  (`sentinel-projects-<slug>`), NOT a host bind mount.
- The volume is seeded from the worktree at setup with `tar | docker run
  --rm -i ... tar -xf - -C /dst` — a **one-way copy at setup time**.
- Files the container writes after seeding (composer's `vendor/`, our
  hypothetical `/app/.junit/phpunit.xml`) live in the volume; the host /
  sentinel-dev cannot path-read them. There is no inverse copy.

CLAUDE.md says "Via `sentinel-projects` volume" for the appserver
mount — that's the named volume, not a host bind. The phrasing was
ambiguous in the issue ticket; the codebase confirms it's a volume.

Therefore approach 3 is rejected. Approach 1 (`exec cat`) is **the** fix.
Approach 1 is also the smaller, safer diff — it threads through nothing,
needs no compose changes, and matches an exec pattern used everywhere
else in this file (`_diagnose_failed_patches`, `validate_config`,
`_ensure_composer_deps`).

If a future change converts `/app` to a true host bind mount and we
*also* want to avoid the extra exec, we can revisit — but that's a much
bigger architectural change with implications for performance, file
permissions, and DooD semantics.

---

## UX Design

### Before State

```
+---------------------------+        +---------------------------+
|       sentinel-dev        |        |     appserver container   |
|---------------------------|        |---------------------------|
| run_tests()               | DooD   | vendor/bin/phpunit        |
|  -> exec(phpunit ...)     |------->|   --log-junit=/tmp/...    |
|                           |        |   writes /tmp/phpunit-    |
|                           |        |   junit.xml in container  |
|                           |        +---------------------------+
| _parse_test_output(raw)   |
|  Path("/tmp/...").exists()|  <--- host /tmp, no shared FS,
|     => False              |       returns False
|  return []                |  <--- ZERO structured errors
+---------------------------+

DATA_FLOW: phpunit failure -> JUnit in container -> host can't see it
           -> parser returns [] -> verifier refine prompt missing test signal
PAIN_POINT: largest correctness gap in Drupal verifier
```

### After State

```
+---------------------------+        +---------------------------+
|       sentinel-dev        |        |     appserver container   |
|---------------------------|        |---------------------------|
| run_tests()               | DooD   | vendor/bin/phpunit        |
|  -> exec(phpunit ...)     |------->|   --log-junit=/tmp/...    |
|                           |        |   writes /tmp/phpunit-    |
|                           |        |   junit.xml in container  |
|                           |        +---------------------------+
|                           |                  ^
| _parse_test_output(raw)   |                  | env_manager.exec(
|   has _env_manager?       |                  |   ["cat",
|     yes -> exec cat ----->+----- DooD -------+   "/tmp/phpunit-
|     parse XML bytes       |                  |   junit.xml"]
|   else: existing host path|                  |
|  return [StructuredError, ...] <-- real entries from JUnit
+---------------------------+

DATA_FLOW: phpunit failure -> JUnit in container -> exec cat pulls bytes
           -> parse_phpunit_junit -> StructuredError list
           -> verifier refine prompt sees per-test failure detail
VALUE_ADD: refine prompt now contains file/line/rule/message of
           every failed test, not just PHPStan + composer signals
```

### Interaction Changes

| Location                     | Before                                  | After                                    | User Impact                                                |
|------------------------------|-----------------------------------------|------------------------------------------|------------------------------------------------------------|
| `_parse_test_output`         | host-only `Path.exists()` check         | container `exec cat` first when env set  | structured errors actually populated on container failures |
| Verifier refine prompt       | only PHPStan + composer errors visible  | also receives PHPUnit failure entries    | developer agent gets richer failure signal → fewer wasted iterations |

---

## Mandatory Reading

| Priority | File                                                                          | Lines      | Why Read This                                                            |
|----------|-------------------------------------------------------------------------------|------------|--------------------------------------------------------------------------|
| P0       | `src/agents/drupal_developer.py`                                              | 23, 409-432| Constant + the function being modified                                   |
| P0       | `src/agents/drupal_developer.py`                                              | 305-373    | `_diagnose_failed_patches` — exec pattern to MIRROR (uses `sh -c`, head) |
| P0       | `src/agents/base_developer.py`                                                | 1278-1334  | `_run_tests_in_container` — call site of `_parse_test_output`            |
| P0       | `src/agents/base_developer.py`                                                | 178-180    | `_env_manager` / `_env_ticket_id` instance attrs and their None defaults |
| P0       | `src/environment_manager.py`                                                  | 177-204    | `exec()` signature: returns `ComposeResult` with stdout/stderr/returncode|
| P0       | `src/compose_runner.py`                                                       | 18-25      | `ComposeResult` dataclass shape                                          |
| P0       | `src/agents/_structured_errors.py`                                            | 68-112     | `parse_phpunit_junit` — accepts XML string, returns list[StructuredError]|
| P1       | `src/lando_translator.py`                                                     | 328-345    | Confirms `/app` is a named-volume mount, NOT a host bind                 |
| P1       | `src/environment_manager.py`                                                  | 256-354    | Volume seeding semantics (one-way, why approach 3 fails)                 |
| P1       | `tests/test_drupal_developer.py`                                              | 330-500    | `TestContainerAwareTests` — Mock-based env exec test pattern to MIRROR   |
| P1       | `tests/agents/test_structured_error_adapters_golden.py`                       | 27-115     | Pattern for asserting parsed JUnit output                                |
| P1       | `tests/fixtures/static_check_output/phpunit_junit_fail.xml`                   | all        | Fixture used in new tests                                                |

**External Documentation**: none required — pure refactor using existing
project APIs (`EnvironmentManager.exec`, `parse_phpunit_junit`).

---

## Patterns to Mirror

**ENV_MANAGER_EXEC_PATTERN** (from this very file, used 4+ times):

```python
# SOURCE: src/agents/drupal_developer.py:348-361
# COPY THIS PATTERN:
dry = self._env_manager.exec(
    ticket_id=self._env_ticket_id,
    service="appserver",
    command=[
        "sh",
        "-c",
        f"patch -p1 --dry-run -i /app/{norm} -d /app/{target_dir} 2>&1 "
        "| head -120",
    ],
    workdir="/app",
)
# dry.stdout, dry.stderr, dry.returncode  -- ComposeResult shape
```

For our case the command is simpler (`["cat", _PHPUNIT_JUNIT_PATH]`),
but the call shape (kwargs, service="appserver", workdir) is identical.

**ENV_GUARD_PATTERN** (consistent across `validate_config`,
`run_static_checks`, `_diagnose_failed_patches`):

```python
# SOURCE: src/agents/drupal_developer.py:183-189, 322-323, 446-453
# COPY THIS PATTERN:
if not self._env_manager or not self._env_ticket_id:
    # ... do the host-path / skip thing ...
```

**HOST_FALLBACK_PARSE_PATTERN** (existing in the function we're editing):

```python
# SOURCE: src/agents/drupal_developer.py:422-431
# KEEP THIS BLOCK (becomes the no-env branch):
try:
    xml_path = Path(_PHPUNIT_JUNIT_PATH)
    if xml_path.exists():
        return parse_phpunit_junit(xml_path.read_text())
    logger.debug(
        "PHPUnit JUnit XML not accessible at %s — returning [] for parser",
        xml_path,
    )
except OSError as e:
    logger.debug("Could not read PHPUnit JUnit XML: %s", e)
```

**TEST_PATTERN — container-aware Mock env_manager** (mirror exactly):

```python
# SOURCE: tests/test_drupal_developer.py:344-393
# COPY THIS PATTERN for new tests:
agent = DrupalDeveloperAgent()
mock_env_mgr = Mock()
mock_env_mgr.exec.return_value = Mock(
    success=True, stdout="...", stderr="", returncode=0,
)
agent.set_environment(mock_env_mgr, "TEST-123")
# ... call agent method ...
calls = mock_env_mgr.exec.call_args_list
assert calls[N].kwargs["command"] == [...]
assert calls[N].kwargs["workdir"] == "/app"
```

**GOLDEN_FIXTURE_PATTERN**:

```python
# SOURCE: tests/agents/test_structured_error_adapters_golden.py:40-115
# Reference the existing fixture by relative path:
FIXTURES = Path(__file__).parent.parent / "fixtures" / "static_check_output"
xml = (FIXTURES / "phpunit_junit_fail.xml").read_text()
# Feed into parse_phpunit_junit() and assert structured-error count
```

---

## Files to Change

| File                                       | Action  | Justification                                                                  |
|--------------------------------------------|---------|--------------------------------------------------------------------------------|
| `src/agents/drupal_developer.py`           | UPDATE  | Modify `_parse_test_output` to read JUnit from container when env attached     |
| `tests/test_drupal_developer.py`           | UPDATE  | Add tests for container-path JUnit parsing (success and absent file)           |
| (no fixture changes)                       | —       | Reuse existing `phpunit_junit_fail.xml` and `phpunit_junit_pass.xml`           |
| (no compose / env_manager changes)         | —       | Approach 1 needs none                                                          |
| (no `_get_test_command` changes)           | —       | The container path `/tmp/phpunit-junit.xml` stays — phpunit writes container-side, parser now reads container-side |

---

## NOT Building (Scope Limits)

- **Bind-mounting `/app` or `/tmp`**: Out of scope. Architecturally larger,
  affects every container, performance/permissions implications. Approach 1
  fixes the bug with a 6-line diff.
- **Improving the structured-error signature beyond JUnit**: explicit
  out-of-scope per the ticket.
- **Changing the python_developer pytest path**: pytest already works
  correctly because pytest output goes through stdout, which we capture.
- **Refactoring `_run_tests_in_container` to bundle the cat into one
  multi-step exec**: tempting (single round-trip via
  `sh -c "vendor/bin/phpunit ...; echo --SEP--; cat /tmp/phpunit-junit.xml"`)
  but couples test execution to JUnit retrieval and complicates output
  handling. Defer until profiling shows the second exec is hot.
- **Backfilling JUnit retrieval for the host-side fallback path**: the
  host path already works (`Path(...).exists()` is true when phpunit ran
  on host).

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/agents/drupal_developer.py` — refactor `_parse_test_output`

- **ACTION**: Modify `_parse_test_output` (lines 409-432) to dispatch on
  whether a container env is attached.
- **IMPLEMENT**:
  - When `self._env_manager` and `self._env_ticket_id` are set: call
    `self._env_manager.exec(ticket_id=..., service="appserver",
    command=["cat", _PHPUNIT_JUNIT_PATH], workdir="/app")`.
    - If `result.returncode == 0` and `result.stdout` non-empty → return
      `parse_phpunit_junit(result.stdout)`.
    - Otherwise (rc != 0 → file missing; or empty stdout) → log debug and
      return `[]`.
    - Catch `Exception` (env exec can raise `RuntimeError` per
      `environment_manager.py:202`) → log debug → return `[]`. Match the
      existing exception-tolerant style of `run_static_checks` (lines
      483-490).
  - When env not attached: keep the existing host-path block verbatim
    (the `Path(...).exists()` / `read_text()` block).
- **MIRROR**:
  - exec call shape → `src/agents/drupal_developer.py:348-361`.
  - env-guard pattern → `src/agents/drupal_developer.py:183-189`.
  - exception tolerance → `src/agents/drupal_developer.py:483-490`.
- **DOCSTRING**: Replace the "we return `[]` and let static checks carry
  the signal — Phase 1 trade" paragraph with a description of the
  container-path read. Cite that `/app` is a named volume so we can't
  path-read; we exec `cat` instead.
- **GOTCHAS**:
  - `result.stdout` is `str`, NOT bytes — `parse_phpunit_junit` expects
    `str`, so feed directly.
  - Don't conflate "rc != 0 because file missing" (real signal: no JUnit
    written, so `[]`) with "exec raised because no environment for
    ticket". Both terminate at `return []` but the first should be a
    `debug`-level log, the second a warning.
  - The `_PHPUNIT_JUNIT_PATH` constant stays at `/tmp/phpunit-junit.xml`.
    Do not change it. The constant value is correct *inside* the
    container (which is where phpunit writes) — the bug is only on the
    read side.
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && python -m pyflakes src/agents/drupal_developer.py
  cd /workspace/sentinel && python -c "from src.agents.drupal_developer import DrupalDeveloperAgent"
  ```
  EXPECT: no errors / clean import.

### Task 2: UPDATE `tests/test_drupal_developer.py` — add 3 new tests under `TestContainerAwareTests`

- **ACTION**: Add three tests asserting the new behavior. Place them
  immediately after `test_run_tests_skips_when_no_config` (around line
  493) so the related cluster stays grouped.
- **IMPLEMENT**:

  **Test A: `test_parse_test_output_reads_junit_from_container`**
  - Build a `DrupalDeveloperAgent`, attach a `Mock` env_manager.
  - Configure `mock_env_mgr.exec.return_value` to return a `Mock` whose
    `stdout` is the contents of
    `tests/fixtures/static_check_output/phpunit_junit_fail.xml` (read via
    a `Path(__file__).parent / "fixtures" / "..."` lookup), `stderr=""`,
    `returncode=0`.
  - Call `agent._parse_test_output(raw="ignored", return_code=1)`.
  - Assert the call: `mock_env_mgr.exec.assert_called_once_with(
        ticket_id="TEST-123", service="appserver",
        command=["cat", "/tmp/phpunit-junit.xml"], workdir="/app",
    )`.
  - Assert returned list has length 3 (matches the fixture: 2 failures +
    1 error per inspection of the file).
  - Assert each entry has keys `file`, `line`, `rule`, `message`
    populated (mirroring `test_structured_error_adapters_golden.py`).

  **Test B: `test_parse_test_output_returns_empty_when_container_file_missing`**
  - Mock env_manager.exec to return `Mock(stdout="", stderr="cat:
    /tmp/phpunit-junit.xml: No such file", returncode=1)`.
  - Call `_parse_test_output("", 1)`.
  - Assert returns `[]`.
  - Assert exec was called exactly once.

  **Test C: `test_parse_test_output_returns_empty_when_exec_raises`**
  - `mock_env_mgr.exec.side_effect = RuntimeError("No active environment for TEST-123")`.
  - Call `_parse_test_output("", 1)` → returns `[]` (no exception
    leaks).

- **MIRROR**: `tests/test_drupal_developer.py:344-393` for fixture/Mock
  setup, `tests/agents/test_structured_error_adapters_golden.py:40-115`
  for fixture lookup pattern.
- **GOTCHAS**:
  - The fixture file lives at
    `tests/fixtures/static_check_output/phpunit_junit_fail.xml`. From
    `tests/test_drupal_developer.py` (which is in `tests/` directly, not
    `tests/agents/`), the path is `Path(__file__).parent / "fixtures" /
    "static_check_output" / "phpunit_junit_fail.xml"`.
  - When constructing the Mock's exec return, set `stdout` to the
    fixture's full content **as a string** (the fixture is XML — read it
    with `.read_text()`).
  - Existing `Mock` returns in this file include a positional
    `success=True` kwarg (`Mock(success=True, ...)`) — keep that for
    consistency with surrounding tests, even though our code only reads
    `.stdout` / `.returncode`.
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && python -m pytest tests/test_drupal_developer.py::TestContainerAwareTests -x -q
  ```
  EXPECT: all tests pass; the 3 new ones run and assert successfully.

### Task 3: REGRESSION CHECK — verify the host-fallback path still works

- **ACTION**: Add or extend a fourth test:
  `test_parse_test_output_falls_back_to_host_path_without_env`.
- **IMPLEMENT**:
  - Build agent with **no** `set_environment` call.
  - Use `tmp_path` fixture to write the JUnit fixture content to
    `tmp_path / "phpunit.xml"`.
  - `monkeypatch` `src.agents.drupal_developer._PHPUNIT_JUNIT_PATH` to
    `str(tmp_path / "phpunit.xml")` (or use `unittest.mock.patch.object`
    on the module attribute) so the host-path reader finds it.
  - Call `_parse_test_output("", 1)` and assert non-empty list of length 3.
- **MIRROR**: `tests/test_drupal_developer.py:395-421` for "no env →
  host path" test pattern.
- **GOTCHAS**:
  - Patch the module-level constant via
    `monkeypatch.setattr("src.agents.drupal_developer._PHPUNIT_JUNIT_PATH", str(tmp_path / "phpunit.xml"))`.
    The function reads the constant by free-variable lookup, so this
    works.
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && python -m pytest tests/test_drupal_developer.py::TestContainerAwareTests -x -q
  ```
  EXPECT: 4 new tests + existing ones pass.

### Task 4: VALIDATE — full Drupal-developer + verifier suite

- **ACTION**: Run the broader test surfaces that touch this code.
- **IMPLEMENT**: Run pytest on:
  - `tests/test_drupal_developer.py`
  - `tests/agents/test_base_developer_verifier_loop.py`
  - `tests/agents/test_structured_error_adapters.py`
  - `tests/agents/test_structured_error_adapters_golden.py`
- **VALIDATE**:
  ```bash
  cd /workspace/sentinel && python -m pytest \
    tests/test_drupal_developer.py \
    tests/agents/test_base_developer_verifier_loop.py \
    tests/agents/test_structured_error_adapters.py \
    tests/agents/test_structured_error_adapters_golden.py \
    -x -q
  ```
  EXPECT: all green. Specifically watch for any test that previously
  asserted `_parse_test_output` returns `[]` under env-attached
  conditions — none exist today (we checked via grep), but the test
  pass is the proof.

---

## Testing Strategy

### Unit Tests to Write

| Test                                                                | Scenario                                       | Validates                                  |
|---------------------------------------------------------------------|------------------------------------------------|--------------------------------------------|
| `test_parse_test_output_reads_junit_from_container`                 | Env attached, exec returns fixture XML         | Container path produces structured errors  |
| `test_parse_test_output_returns_empty_when_container_file_missing`  | Env attached, exec returncode=1                | Graceful degradation, no crash             |
| `test_parse_test_output_returns_empty_when_exec_raises`             | Env attached, exec raises RuntimeError         | Exception handling matches `run_static_checks` |
| `test_parse_test_output_falls_back_to_host_path_without_env`        | No env attached, host file exists              | Host-side path preserved (regression guard)|

### Edge Cases Checklist

- [ ] Env attached, JUnit XML present and non-empty → list of N errors.
- [ ] Env attached, JUnit XML missing (rc != 0, empty stdout) → `[]`.
- [ ] Env attached, exec raises (no env for ticket) → `[]` (no leak).
- [ ] No env, host file exists → `[]` to non-empty depending on file
  content; existing behavior unchanged.
- [ ] No env, host file missing → `[]` (existing behavior unchanged).
- [ ] Malformed XML in container stdout → `[]` (delegated to
  `parse_phpunit_junit`'s ParseError handling, lines 107-109).

### NOT Tested at Unit Level (acknowledged)

- True end-to-end: phpunit-in-container actually writing the file then
  this code reading it back. That requires a real appserver container,
  which we don't run in CI today. The Mock-based test exercises the call
  shape (which is what would break) and the parser is independently
  fixture-tested.

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
cd /workspace/sentinel && python -m pyflakes src/agents/drupal_developer.py tests/test_drupal_developer.py
```

EXPECT: exit 0, no warnings.

### Level 2: UNIT_TESTS (focused)

```bash
cd /workspace/sentinel && python -m pytest tests/test_drupal_developer.py::TestContainerAwareTests -v
```

EXPECT: every test in the class green, including the 4 new ones.

### Level 3: TARGETED REGRESSION

```bash
cd /workspace/sentinel && python -m pytest \
  tests/test_drupal_developer.py \
  tests/agents/test_base_developer_verifier_loop.py \
  tests/agents/test_structured_error_adapters.py \
  tests/agents/test_structured_error_adapters_golden.py \
  -q
```

EXPECT: all green.

### Level 4: FULL SUITE

```bash
cd /workspace/sentinel && python -m pytest -q
```

EXPECT: prior pass count + 4 new tests, no regressions. Skipped /
xfailed counts unchanged.

### Level 5: MANUAL_VALIDATION (optional, infra-permitting)

If a real ticket can be re-run (live appserver):

1. Use `sentinel execute <ticket-with-failing-phpunit>` and watch the
   refine prompt the developer agent receives on iteration 2.
2. Confirm the prompt now contains lines like
   `[file]:[line] [rule] [message]` for failed tests, not just PHPStan
   entries.

---

## Acceptance Criteria

- [ ] `_parse_test_output` returns non-empty `List[StructuredError]`
      when a container env is attached and JUnit XML exists with
      failures (verified by Test A using existing fixture).
- [ ] `_parse_test_output` returns `[]` gracefully when the container
      file is missing OR exec raises OR no env is attached and host
      file is missing.
- [ ] No regression in any existing test (Level 3 + Level 4 pass).
- [ ] No changes to `_get_test_command`, the JUnit constant value,
      `run_static_checks`, or `_run_tests_in_container`.
- [ ] Refine-prompt path: structured PHPUnit errors now appear in the
      verifier-loop's combined `structured_errors` list when phpunit
      fails inside the container.

---

## Completion Checklist

- [x] Task 1 — `_parse_test_output` updated, lints clean
- [x] Task 2 — 3 new container-path tests pass
- [x] Task 3 — host-path regression test passes
- [x] Task 4 — broader suite (drupal_developer + verifier loop +
      structured-error adapters) all green (110/110)
- [x] Level 1 static analysis clean (ruff)
- [x] Level 4 full suite passes (1012 passed; 26 pre-existing failures
      verified unrelated by re-running on unmodified tree → 1008 passed
      baseline + 4 new tests)
- [ ] PR description references this plan and the M7 review entry
      (handled by orchestrator)

---

## Risks and Mitigations

| Risk                                                                        | Likelihood | Impact | Mitigation                                                                                              |
|-----------------------------------------------------------------------------|------------|--------|---------------------------------------------------------------------------------------------------------|
| `cat` returns binary or odd whitespace that confuses `parse_phpunit_junit`  | LOW        | LOW    | `parse_phpunit_junit` already handles ParseError → `[]`. Functionally a no-op vs. status quo.            |
| Extra exec adds latency (one `cat` per failed test run)                     | HIGH       | LOW    | Cat of a few-KB XML over `docker compose exec` is < 100ms, runs only on test failures.                   |
| Mock-based test diverges from real container shape                          | MEDIUM     | MED    | Call shape mirrors the existing `_diagnose_failed_patches` exec exactly; if those work in prod, ours will. |
| Future change to `_PHPUNIT_JUNIT_PATH` (e.g. moving inside `/app`) breaks read | LOW       | LOW    | Constant stays the source of truth; both the writer (`--log-junit=`) and the reader (`cat`) use it. Single point of truth.|
| `_env_manager.exec` could be slow on the very first call (cold container)   | LOW        | LOW    | Container is already warm by the time tests run — test execution itself just happened in it.            |

---

## Notes

**Why not approach 3 (write to `/app/.junit/...`)?**
Repeating loudly because the issue ticket promotes it: `/app` is a
**named Docker volume** populated by a one-way tar-pipe at setup
(`environment_manager.py:292-354`). The host (sentinel-dev) cannot
path-read files written into the volume after seeding. Approach 3 would
require either (a) bind-mounting `/app` (large change with permissions
and DooD implications), or (b) an extra "tar back from volume to host"
step at parse time (which is just a fancier version of the `cat` exec
in approach 1, but with more code). **Approach 1 is strictly better.**

**Why not bundle `cat` into the test exec command?**
Tempting — `sh -c "vendor/bin/phpunit ...; echo --SEP--; cat /tmp/phpunit-junit.xml"`
saves one round-trip. Rejected because:
1. Couples test orchestration to JUnit retrieval; the abstraction
   `_parse_test_output(raw, return_code)` is currently parser-only and
   must stay so the python pytest path doesn't have to care.
2. Mixing test stdout with JUnit XML on stdout would require a
   separator + slicing — fragile (test output could legitimately
   contain `--SEP--`).
3. The extra round-trip only happens on test failures (the only path
   that calls `_parse_test_output` per `base_developer.py:1316`), and
   one extra `docker exec cat` is sub-100ms.

**Drupal-only**: `python_developer` parses pytest from stdout (no
external XML), so it's unaffected. This mirrors the issue's "out of
scope" note.

**Constant stays at `/tmp/...`**: The constant is the path **inside the
container** where phpunit writes. The bug was never the path; it was
the assumption that the host could see container-side files. Don't
move it to `/app/...` chasing a phantom bind mount.
