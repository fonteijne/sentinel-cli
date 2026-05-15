# Feature: `sentinel reset` removes per-ticket Docker volumes

## Summary

Make `sentinel reset <ticket>` (and `sentinel reset --all`) explicitly remove the per-ticket Docker volumes — the external `sentinel-projects-<slug>` volume that Sentinel creates outside compose, and the compose-managed `<project>_db-data` volume — so that "reset" is a real wipe and not just a worktree/branch cleanup. Today reset only stops containers via `compose down -v`, which never touches the externally-declared `sentinel-projects-<slug>` volume; once the warm-volume change in IDEAS.md lands, this gap will silently defeat reset, leaving a `vendor/`-laden volume for the next `execute` to pick up. Implementation extracts a small idempotent helper in `environment_manager.py` and wires it into the existing `_teardown_containers()` flow in `cli.py`.

## User Story

As a Sentinel operator
I want `sentinel reset <ticket>` to also remove the ticket's Docker volumes
So that "reset" actually wipes everything for the ticket and the next `execute` starts from a clean state — even after we keep volumes warm across runs.

## Problem Statement

`src/cli.py:_teardown_containers()` (lines 1602-1641) runs `ComposeRunner.down(volumes=True)` and returns. `down -v` only removes **compose-managed** volumes. The per-ticket project volume is declared `external: true` in the generated compose file (`src/lando_translator.py:152`), so compose intentionally leaves it alone — `EnvironmentManager.teardown()` removes it explicitly via `_remove_volume()` (`src/environment_manager.py:154`), but the reset code path never calls `EnvironmentManager.teardown()`, only `ComposeRunner.down()`. Net effect: `sentinel reset <ticket>` leaves `sentinel-projects-<slug>` on the host, and once the IDEAS.md "keep volumes warm across runs" change ships, that stale volume will be picked up by the next `execute`, defeating the purpose of resetting.

Two further concrete failure modes today:
1. After a partially-failed `execute`, the `docker-compose.sentinel.yml` may not exist (no compose file path) — `_teardown_containers` falls back to `cleanup_orphans` (containers only) and never even attempts volume cleanup.
2. The DB volume `<project>_db-data` is compose-managed and *would* be removed by `down -v` — but only when the compose file exists. Same fallback gap.

## Solution Statement

Add a small idempotent helper `remove_ticket_volumes(ticket_id, compose_project)` in `src/environment_manager.py` that runs `docker volume rm` for both the per-ticket external volume (`sentinel-projects-<slug>`) and the compose-managed DB volume (`<compose_project>_db-data`), captures `docker volume rm`'s exit code, logs a warning on failure, and returns the list of volumes actually removed (so the CLI can show meaningful output). Mirror the existing best-effort pattern from `EnvironmentManager._remove_volume` (`src/environment_manager.py:356-373`).

Wire the helper into `_teardown_containers()` so it runs **after** the compose-down attempt (and after the fallback orphan cleanup), unconditionally. Reset becomes the single place that owns "wipe everything for this ticket"; `EnvironmentManager.teardown()` keeps its existing per-ticket volume removal (unchanged) and grows no new responsibility.

## Metadata

| Field            | Value                                                              |
| ---------------- | ------------------------------------------------------------------ |
| Type             | BUG_FIX (preventative — pairs with upcoming volume-warm change)    |
| Complexity       | LOW                                                                |
| Systems Affected | `src/cli.py`, `src/environment_manager.py`, tests                  |
| Dependencies     | docker CLI (already required); no new Python deps                  |
| Estimated Tasks  | 6                                                                  |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║ $ sentinel reset DHLEXC-311 --yes                                             ║
║                                                                               ║
║   1️⃣  Stopping containers...                                                  ║
║       ✓ Containers stopped and removed       ◄── compose down -v ran          ║
║   2️⃣  Removing worktree...                                                    ║
║       ✓ Worktree removed                                                      ║
║   3️⃣  Deleting local branch...                                                ║
║       ✓ Branch sentinel/feature/DHLEXC-311 deleted                            ║
║   ✅ Reset complete for DHLEXC-311                                            ║
║                                                                               ║
║ $ docker volume ls | grep dhlexc-311                                          ║
║   local   sentinel-projects-dhlexc-311        ◄── ❌ STILL THERE             ║
║                                                                               ║
║ PAIN_POINT: External per-ticket volume survives reset. Once warm-volume       ║
║             lifecycle lands, next `execute` reuses stale vendor/, lockfile    ║
║             drift, and "reset" stops meaning what it says.                    ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║ $ sentinel reset DHLEXC-311 --yes                                             ║
║                                                                               ║
║   1️⃣  Stopping containers...                                                  ║
║       ✓ Containers stopped and removed                                        ║
║       ✓ Removed volume sentinel-projects-dhlexc-311                           ║
║       ✓ Removed volume sentinel-dhlexc-311_db-data                            ║
║   2️⃣  Removing worktree...                                                    ║
║       ✓ Worktree removed                                                      ║
║   3️⃣  Deleting local branch...                                                ║
║       ✓ Branch sentinel/feature/DHLEXC-311 deleted                            ║
║   ✅ Reset complete for DHLEXC-311                                            ║
║                                                                               ║
║ $ sentinel reset NEVER-RAN --yes        ◄── ticket never executed             ║
║   1️⃣  Stopping containers...                                                  ║
║       ℹ️  No active containers found (or cleaned up orphans)                  ║
║       ℹ️  No volumes to remove           ◄── idempotent no-op                 ║
║   2️⃣  Removing worktree...                                                    ║
║       ℹ️  No worktree found                                                   ║
║   ...                                                                         ║
║                                                                               ║
║ VALUE_ADD: Reset becomes a real wipe. Pairs with warm-volume lifecycle —      ║
║            users have an explicit "drop everything for this ticket" handle.   ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                              | Before                                | After                                   | User Impact                          |
| ------------------------------------- | ------------------------------------- | --------------------------------------- | ------------------------------------ |
| `sentinel reset <ticket>`             | Containers + worktree + branch only   | + Per-ticket external + DB volumes      | Real clean-slate reset               |
| `sentinel reset --all`                | Same as above, per ticket             | Same as above, per ticket               | Real clean-slate reset for all       |
| `sentinel reset <never-ran-ticket>`   | Worktree/branch no-op                 | Worktree/branch + volume no-ops         | Stays a no-op (idempotent)           |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task:**

| Priority | File                                              | Lines     | Why Read This                                                              |
| -------- | ------------------------------------------------- | --------- | -------------------------------------------------------------------------- |
| P0       | `/workspace/sentinel/src/cli.py`                  | 1602-1641 | `_teardown_containers` — exact integration point                           |
| P0       | `/workspace/sentinel/src/environment_manager.py`  | 256-373   | `_volume_name_for`, `_ensure_volume_exists`, `_remove_volume` — patterns to MIRROR |
| P0       | `/workspace/sentinel/src/lando_translator.py`     | 129-154   | Volume declarations in compose (external vs compose-managed)               |
| P1       | `/workspace/sentinel/src/cli.py`                  | 1644-1788 | `_reset_ticket` and `_reset_all` — output style and step numbering         |
| P1       | `/workspace/sentinel/src/compose_runner.py`       | 75-122    | `_build_cmd`, `_run` — subprocess pattern (capture_output=True, text=True) |
| P1       | `/workspace/sentinel/src/compose_runner.py`       | 296-330   | `cleanup_orphans` — best-effort pattern with `try/except`                  |
| P2       | `/workspace/sentinel/tests/test_environment_manager.py` | 81-188 | Mock patterns for ComposeRunner and `subprocess.run`                       |
| P2       | `/workspace/sentinel/tests/test_compose_runner.py` | 63-100   | `@patch("subprocess.run")` decorator pattern                               |
| P2       | `/workspace/sentinel/tests/test_cli_postmortems.py` | 40-72   | Click `CliRunner` test pattern                                             |
| P2       | `/workspace/sentinel/pyproject.toml`              | 22-52    | pytest config, ruff line length, mypy strict mode                          |

**External Documentation:**

| Source                                                                                          | Section                       | Why Needed                                                            |
| ----------------------------------------------------------------------------------------------- | ----------------------------- | --------------------------------------------------------------------- |
| [Docker `volume rm` docs](https://docs.docker.com/reference/cli/docker/volume/rm/)              | Exit codes & `--force`        | Confirms exit code 1 on "volume not found" and "volume in use"        |
| [Docker Compose external volumes](https://docs.docker.com/reference/compose-file/volumes/#external) | `external: true` semantics  | Why compose's `down -v` ignores the per-ticket project volume         |

GOTCHA from docker docs: `docker volume rm <name>` exits **1** when the volume doesn't exist *and* when it's still attached to a container. The error stderr distinguishes them (`"No such volume"` vs `"volume is in use"`), but for our idempotent best-effort behavior we treat both as warn-and-continue, exactly mirroring `EnvironmentManager._remove_volume` (`src/environment_manager.py:365-373`).

---

## Patterns to Mirror

**VOLUME_NAME_CONSTRUCTION** (reuse, do not duplicate):
```python
# SOURCE: src/environment_manager.py:256-271
# COPY THIS PATTERN — it's already a @staticmethod, call it directly:
@staticmethod
def _volume_name_for(ticket_id: str) -> str:
    """Construct a per-ticket project volume name."""
    slug = re.sub(r"[^a-z0-9_-]", "-", ticket_id.lower())
    return f"sentinel-projects-{slug}"
```

**BEST_EFFORT_VOLUME_REMOVAL** (mirror this exactly):
```python
# SOURCE: src/environment_manager.py:356-373
# COPY THIS PATTERN:
def _remove_volume(self, volume_name: str) -> None:
    """Best-effort removal of a per-ticket volume after teardown.

    Compose's ``down --volumes`` only cleans up compose-managed
    volumes; the project volume is declared ``external: true``, so we
    remove it explicitly. If removal fails (volume still attached, or
    already gone), we log and continue — failing teardown over a
    stranded volume isn't worth it.
    """
    result = subprocess.run(
        ["docker", "volume", "rm", volume_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning(
            f"Could not remove volume '{volume_name}': {result.stderr.strip()}"
        )
```

**CLI_OUTPUT_STYLE** (matches existing reset output):
```python
# SOURCE: src/cli.py:1670-1683
# COPY THIS EMOJI/INDENT PATTERN:
click.echo("\n1️⃣  Stopping containers...")
_teardown_containers(worktree_mgr, ticket_id, project)
# Inside _teardown_containers we currently emit:
click.echo("   ✓ Containers stopped and removed")
click.echo(f"   ⚠️  Container teardown issue: {result.stderr}")
click.echo("   ✓ No active containers found (or cleaned up orphans)")
click.echo("   ℹ️  Docker not available — skipping container cleanup")
# NEW lines must use the same `   ✓ ` / `   ℹ️  ` / `   ⚠️  ` prefix.
```

**CLI_TEST_PATTERN** (Click `CliRunner`):
```python
# SOURCE: tests/test_cli_postmortems.py:40-72
# COPY THIS PATTERN:
@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()

def test_reset_removes_volumes(runner: CliRunner, ...) -> None:
    result = runner.invoke(cli, ["reset", "DHLEXC-311", "--yes"])
    assert result.exit_code == 0, result.output
    assert "Removed volume sentinel-projects-dhlexc-311" in result.output
```

**SUBPROCESS_MOCK_PATTERN**:
```python
# SOURCE: tests/test_compose_runner.py:63-77
# COPY THIS PATTERN:
@patch("subprocess.run")
def test_xxx(self, mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    # ...
    # For "volume not found" simulate non-zero exit:
    mock_run.return_value = MagicMock(
        returncode=1, stdout="",
        stderr='Error response from daemon: get volume: no such volume: sentinel-projects-foo'
    )
```

**IDEMPOTENCY_TEST_PATTERN**:
```python
# SOURCE: tests/test_environment_manager.py:180-188
# COPY THIS PATTERN — the literal "no-op" assertion shape:
def test_teardown_inactive_noop(self, env_mgr):
    success = env_mgr.teardown("NONEXISTENT-001")
    assert success is True
```

---

## Files to Change

| File                                                  | Action | Justification                                                                      |
| ----------------------------------------------------- | ------ | ---------------------------------------------------------------------------------- |
| `src/environment_manager.py`                          | UPDATE | Add `remove_ticket_volumes()` module-level helper; expose `_volume_name_for` as `volume_name_for` (drop the underscore for the new public form, keep the old name as an alias for back-compat with internal callers). |
| `src/cli.py`                                          | UPDATE | Call `remove_ticket_volumes()` from `_teardown_containers()` after compose-down / fallback orphan cleanup. |
| `tests/test_environment_manager.py`                   | UPDATE | Unit tests for `remove_ticket_volumes()` covering: both volumes present, both absent, one absent, in-use volume warning. |
| `tests/test_cli_reset.py`                             | CREATE | End-to-end CLI tests for `sentinel reset` invoking volume cleanup. Uses Click `CliRunner` + `@patch("subprocess.run")`. |

No changes to `src/lando_translator.py`, `src/compose_runner.py`, `src/worktree_manager.py`, or `src/session_tracker.py`.

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **Volume warm/persistence lifecycle**: A separate IDEAS.md entry (line 22). This plan ONLY makes reset remove volumes — it does not change when volumes are created/destroyed during normal `execute` flow. `EnvironmentManager._setup_lando` keeps its current "wipe-and-recopy" behavior unchanged.
- **Orphan/legacy volume reaper (`sentinel doctor`)**: A separate IDEAS.md entry (line 29). Out of scope; this plan only removes the *current* naming scheme's volumes for the *given* ticket, not orphans from prior naming schemes.
- **`sentinel reset --volumes-only` flag or partial reset**: Not asked for; reset stays one-shot all-or-nothing.
- **Removing volumes from `EnvironmentManager.teardown()` differently**: Existing teardown path stays intact. We only add a *separate* helper called from the reset path. Don't refactor `_remove_volume` away — it has a single in-class caller and changing its signature risks breaking the live `execute → teardown` flow.
- **Confirmation prompt changes**: The existing "This will remove: ..." block in `_reset_ticket` (`src/cli.py:1657-1663`) gains one extra bullet ("Docker volumes for <ticket>"); no new interactive flow.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: UPDATE `src/environment_manager.py` — add `remove_ticket_volumes` helper

- **ACTION**: Add a new module-level function `remove_ticket_volumes(ticket_id: str, compose_project: str) -> list[str]`.
- **IMPLEMENT**:
  - Compute two volume names:
    - `project_volume = f"sentinel-projects-{re.sub(r'[^a-z0-9_-]', '-', ticket_id.lower())}"` (mirrors `_volume_name_for`)
    - `db_volume = f"{compose_project}_db-data"` (compose-managed naming convention; `compose_project` is already lowercased by the caller)
  - For each volume, call `subprocess.run(["docker", "volume", "rm", name], capture_output=True, text=True)`.
  - On `returncode == 0`: append name to the returned list (caller can echo it).
  - On `returncode != 0`: detect `"No such volume"` substring in stderr → log at `DEBUG` (it's the expected idempotent path); other failures → `logger.warning(f"Could not remove volume '{name}': {stderr.strip()}")`.
  - Return list of names that were actually removed (possibly empty).
- **MIRROR**: `src/environment_manager.py:356-373` (`_remove_volume`) — copy the subprocess call shape and the warning format verbatim.
- **IMPORTS**: Already in file: `import re`, `import subprocess`, `import logging`. Module logger already exists at line 24.
- **GOTCHA**: `compose_project` must be lowercased *before* being passed in. Existing callers in `cli.py` already do `f"sentinel-{ticket_id}".lower()` (`src/cli.py:1614`); document that as a precondition in the docstring rather than re-lowering inside the helper (single source of truth).
- **GOTCHA**: Don't call `EnvironmentManager._remove_volume` — it's an instance method and we don't have an `EnvironmentManager` to construct meaningfully on the reset path (no env state). Keep this as a free function.
- **VALIDATE**: `cd /workspace/sentinel && poetry run mypy src/environment_manager.py`

### Task 2: UPDATE `src/cli.py` — wire helper into `_teardown_containers`

- **ACTION**: Modify `_teardown_containers()` (`src/cli.py:1602-1641`) to call `remove_ticket_volumes` after the compose-down attempt and the fallback orphan cleanup.
- **IMPLEMENT**:
  - Import the new helper: `from src.environment_manager import remove_ticket_volumes, SENTINEL_COMPOSE_FILE` (extend the existing import on `src/cli.py:1611`).
  - Restructure so volume cleanup runs **regardless** of which branch the function took (compose-down success, compose-down failure, fallback). Suggested shape:
    ```python
    # ... existing compose-down block, but DROP the early `return`
    # ... existing fallback block

    # Always attempt volume removal — idempotent, safe when nothing exists
    removed = remove_ticket_volumes(ticket_id, compose_project)
    for vol in removed:
        click.echo(f"   ✓ Removed volume {vol}")
    if not removed:
        click.echo("   ℹ️  No volumes to remove")
    ```
  - Update the confirmation block in `_reset_ticket` (`src/cli.py:1657-1663`) to add: `click.echo(f"  • Docker volumes for {ticket_id} (if present)")`.
- **MIRROR**: Output prefixes match `src/cli.py:1623, 1625, 1635, 1638` exactly (`   ✓ `, `   ℹ️  `, `   ⚠️  `).
- **GOTCHA**: The existing function has `return` statements after `runner.down()` in the success and failure branches (`src/cli.py:1626, 1629`). Remove the early-return on success so the volume cleanup always runs. The failure branch should also fall through.
- **GOTCHA**: When Docker is not available (`RuntimeError` from `ComposeRunner` constructor), do NOT call `remove_ticket_volumes` — it would also fail. Skip volume cleanup in that branch with a matching info line: `click.echo("   ℹ️  Docker not available — skipping volume cleanup")`.
- **VALIDATE**: `cd /workspace/sentinel && poetry run mypy src/cli.py`

### Task 3: UPDATE `tests/test_environment_manager.py` — add tests for `remove_ticket_volumes`

- **ACTION**: Add a new `class TestRemoveTicketVolumes:` block at the end of the file.
- **IMPLEMENT** four tests:
  1. `test_removes_both_volumes_when_present` — `mock_run.side_effect = [MagicMock(returncode=0), MagicMock(returncode=0)]`; assert returned list contains both `sentinel-projects-stnl-001` and `sentinel-stnl-001_db-data`; assert `mock_run.call_count == 2`; verify the exact argv passed.
  2. `test_no_op_when_volumes_absent` — both calls return `returncode=1, stderr="Error: No such volume: ..."`; assert returned list is empty; assert no warning logged (use `caplog` at `WARNING`).
  3. `test_warns_when_volume_in_use` — `returncode=1, stderr="Error response from daemon: remove sentinel-projects-stnl-001: volume is in use"`; assert empty return list; assert one `WARNING` log entry containing `"in use"`.
  4. `test_slugifies_ticket_id` — call with `ticket_id="STNL/001 BAD"`, `compose_project="sentinel-stnl-001-bad"`; assert the project volume argv is `["docker", "volume", "rm", "sentinel-projects-stnl-001-bad"]`.
- **MIRROR**: `tests/test_compose_runner.py:63-100` — `@patch("subprocess.run")` decorator pattern.
- **PATTERN**: Place under existing `class TestTeardown` style in the same file. Use `caplog` fixture for log assertions (already used elsewhere in the test suite — search for `caplog` to confirm).
- **GOTCHA**: Patch the right module — `@patch("src.environment_manager.subprocess.run")`, not `subprocess.run` globally.
- **VALIDATE**: `cd /workspace/sentinel && poetry run pytest tests/test_environment_manager.py::TestRemoveTicketVolumes -v`

### Task 4: CREATE `tests/test_cli_reset.py` — integration tests

- **ACTION**: New test file mirroring `tests/test_cli_postmortems.py` shape.
- **IMPLEMENT** three tests:
  1. `test_reset_ticket_removes_volumes(runner, monkeypatch, tmp_path)` — patch `WorktreeManager`, `ComposeRunner`, and `subprocess.run`; invoke `cli, ["reset", "DHLEXC-311", "--yes"]`; assert exit code 0; assert `"Removed volume sentinel-projects-dhlexc-311"` and `"Removed volume sentinel-dhlexc-311_db-data"` appear in `result.output`.
  2. `test_reset_ticket_idempotent_when_volumes_absent(runner, ...)` — same setup but `subprocess.run` returns `returncode=1, stderr="No such volume"`; assert exit code 0; assert `"No volumes to remove"` in `result.output`; assert NO traceback / NO `Error:` prefix.
  3. `test_reset_ticket_no_worktree_no_volumes(runner, ...)` — `WorktreeManager.get_worktree_path` returns `None` (ticket never executed), no compose file; assert volume cleanup still attempted (idempotent fallthrough), output stays clean.
- **MIRROR**: `tests/test_cli_postmortems.py:40-72` for the `CliRunner` + monkeypatch fixture pattern.
- **GOTCHA**: Click's `CliRunner.invoke` swallows exceptions by default — set `catch_exceptions=False` if a real bug surfaces during dev.
- **GOTCHA**: To avoid coupling to `WorktreeManager` internals, patch the *class* used by `cli.py`: `monkeypatch.setattr("src.cli.WorktreeManager", lambda: <MagicMock>)`. Same for `ComposeRunner`.
- **VALIDATE**: `cd /workspace/sentinel && poetry run pytest tests/test_cli_reset.py -v`

### Task 5: VALIDATE — full unit test run + lint + types

- **ACTION**: Run the project quality gates.
- **VALIDATE**:
  ```
  cd /workspace/sentinel && \
    poetry run ruff check src/environment_manager.py src/cli.py tests/test_environment_manager.py tests/test_cli_reset.py && \
    poetry run mypy src/environment_manager.py src/cli.py && \
    poetry run pytest tests/test_environment_manager.py tests/test_cli_reset.py tests/test_cli_menu.py -v
  ```
- **EXPECT**: Ruff clean (line length 88, target py311), mypy clean (`disallow_untyped_defs = true`), all tests pass — including `tests/test_cli_menu.py` (regression check on the existing reset-menu integration tests at lines 322-715).

### Task 6: MANUAL VALIDATE — exec on a real ticket from inside `sentinel-dev`

- **ACTION**: Verify the change end-to-end against a live Docker daemon. (Cannot be run from the Claude Code sandbox — it has no Docker socket; per `CLAUDE.md` lines 130-133, this runs from `sentinel-dev` or the host.)
- **STEPS** (operator runs):
  1. From `sentinel-dev`: `sentinel execute DHLEXC-311` (or any cheap ticket) until the volume `sentinel-projects-dhlexc-311` exists. Verify with `docker volume ls | grep dhlexc-311`.
  2. `sentinel reset DHLEXC-311 --yes`.
  3. `docker volume ls | grep dhlexc-311` — must produce **no output**.
  4. Re-run `sentinel reset DHLEXC-311 --yes` immediately. Output must include `"No volumes to remove"`, exit code 0.
  5. `sentinel reset NEVER-EXECUTED-999 --yes` (a ticket that was never run). Must exit 0, no traceback, includes `"No volumes to remove"`.
- **EXPECT**: All five steps succeed; in particular, step 3 confirms the bug is fixed and step 4–5 confirm idempotency.

---

## Testing Strategy

### Unit Tests to Write

| Test File                                | Test Cases                                                 | Validates                                  |
| ---------------------------------------- | ---------------------------------------------------------- | ------------------------------------------ |
| `tests/test_environment_manager.py`      | `TestRemoveTicketVolumes` (4 cases above)                 | Volume helper correctness + idempotency    |
| `tests/test_cli_reset.py`                | 3 cases above                                              | CLI integration + output format            |
| `tests/test_cli_menu.py`                 | (existing, untouched)                                      | Regression — interactive menu reset still works |

### Edge Cases Checklist

- [ ] Both volumes present → both removed, both reported
- [ ] Both volumes absent → empty list returned, "No volumes to remove" emitted
- [ ] Project volume present, DB volume absent (Python project, no Lando) → only project volume removed
- [ ] Volume in-use (live container holding it) → warning logged, exit code 0, not raised
- [ ] Ticket ID with slash/space (e.g. `STNL-001/branch foo`) → slugified correctly
- [ ] Docker not available (no socket) → graceful skip with info line, no traceback
- [ ] `sentinel reset --all` across N tickets → each ticket's volumes attempted (per-ticket idempotency holds)

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
cd /workspace/sentinel && \
  poetry run ruff check src/environment_manager.py src/cli.py tests/test_environment_manager.py tests/test_cli_reset.py && \
  poetry run mypy src/environment_manager.py src/cli.py
```

**EXPECT**: Exit 0, no errors or warnings. (Project enforces `disallow_untyped_defs = true`, line length 88.)

### Level 2: UNIT_TESTS

```bash
cd /workspace/sentinel && poetry run pytest \
  tests/test_environment_manager.py \
  tests/test_cli_reset.py \
  -v
```

**EXPECT**: All new tests pass.

### Level 3: FULL_SUITE (regression check)

```bash
cd /workspace/sentinel && poetry run pytest tests/ -x --timeout=120
```

**EXPECT**: No regressions. `tests/test_cli_menu.py` (existing reset-menu tests at lines 322-715) must remain green — the confirmation block change only *adds* a bullet, doesn't change flow.

### Level 4: DATABASE_VALIDATION

N/A — no schema changes.

### Level 5: BROWSER_VALIDATION

N/A — CLI-only feature.

### Level 6: MANUAL_VALIDATION

See Task 6 above. Must be executed from `sentinel-dev` or the host (Claude Code sandbox has no Docker socket).

---

## Acceptance Criteria

- [ ] `sentinel reset <ticket>` removes `sentinel-projects-<slug>` from `docker volume ls`
- [ ] `sentinel reset <ticket>` removes `sentinel-<ticket>_db-data` from `docker volume ls` (when it exists)
- [ ] Running `sentinel reset <ticket>` twice in a row exits 0 both times — second is a no-op
- [ ] Running `sentinel reset <never-executed-ticket>` exits 0 with no traceback
- [ ] Output mirrors existing reset emoji/indent style
- [ ] Confirmation block lists "Docker volumes for <ticket>" before the `--yes`-bypassable prompt
- [ ] Level 1–3 validation commands pass

---

## Completion Checklist

- [ ] Task 1: helper added in `src/environment_manager.py`
- [ ] Task 2: `_teardown_containers` calls helper, no early-return
- [ ] Task 3: `TestRemoveTicketVolumes` (4 cases) added and green
- [ ] Task 4: `tests/test_cli_reset.py` (3 cases) created and green
- [ ] Task 5: ruff + mypy + full pytest suite all green
- [ ] Task 6: operator-run manual validation on a real Docker daemon passes 5/5 steps

---

## Risks and Mitigations

| Risk                                                                            | Likelihood | Impact | Mitigation                                                                                                  |
| ------------------------------------------------------------------------------- | ---------- | ------ | ----------------------------------------------------------------------------------------------------------- |
| `docker volume rm` fails because volume is still attached to a running container | LOW        | LOW    | Best-effort with warning log (mirrors `_remove_volume`). Operator action is to teardown first; reset runs `down -v` first anyway. |
| Wrong DB volume naming convention (e.g. project doesn't use `db-data`)         | LOW        | LOW    | `docker volume rm` is idempotent on missing volume — extra "no such volume" message is logged at DEBUG, not ERROR. Slight log noise, no functional impact. |
| Tests over-mock and pass without exercising real argv                            | MEDIUM     | MEDIUM | Each unit test asserts the exact `mock_run.call_args_list` argv (`["docker", "volume", "rm", "<exact-name>"]`). |
| Removing the early `return` in `_teardown_containers` accidentally double-runs orphan cleanup | LOW        | LOW    | Restructure with `if/elif/else` so each branch sets a "did we handle compose?" flag; orphan cleanup only runs in fallback branch. Code review pre-commit. |
| Future "warm volume" feature lands and inadvertently re-introduces the bug      | MEDIUM     | HIGH   | Out of scope here, but the test suite (`tests/test_cli_reset.py`) becomes a regression net for any future change to teardown. |

---

## Notes

- **Pairs with IDEAS.md line 22** (warm-volume lifecycle): This change is the explicit "wipe everything" handle that the warm-volume change is predicated on. Without this, `reset` becomes silently broken once volumes survive across runs.
- **Distinct from IDEAS.md line 29** (orphan reaper): This plan only handles the *current* naming scheme for the *given* ticket. A `sentinel doctor` reaper for legacy/orphan volumes is a separate piece of work.
- **Why a free function, not an `EnvironmentManager` method**: The reset path has no live `EnvironmentManager` instance — there's no compose runner to teardown, no env state to flip. Constructing one just to call `_remove_volume` would couple two unrelated lifecycles. A free helper that owns one small responsibility (idempotently remove the two known per-ticket volume names) keeps the code honest.
- **Why log at DEBUG for "No such volume" but WARNING for everything else**: Idempotent reset should be noiseless on the happy "already gone" path. A volume that's *in use* is a real anomaly worth surfacing — the operator probably has a leftover `docker run` somewhere.
