# Feature: Phase 1 — "Close the Leash" (Agent Learning from Feedback)

## Summary

Give Sentinel's developer agent a grounded, capped, per-task verifier-retry loop (Karpathy Loop A) so that test and static-check failures feed structured errors back to the same agent for up to N=3 attempts before escalating. On cap-out, emit `DeveloperCappedOut`, persist a postmortem row, keep the MR in draft, and post one "Sentinel paused here" comment. This is the single highest-leverage change in the whole learning system (design §1) and the gate to Phase 2. Everything downstream (memory, overlays, outcome ingestion) depends on a reliable source of grounded failure signals, which is what Phase 1 produces.

## User Story

As a **Sentinel maintainer shipping Drupal tickets end-to-end**
I want **the developer agent to notice its own test/static-check failures and make up to 3 targeted fixes before handing back a broken MR**
So that **we stop merging work the agent could have fixed, and when the agent truly can't, we have a structured postmortem row — not a mystery failure — driving Phase 2's learning**.

## Problem Statement

Today, `base_developer.implement_feature()` (`src/agents/base_developer.py:286-404`) runs the TDD workflow **once**, calls `run_tests()` at `:357`, and if tests fail it raises `RuntimeError` (`:365`) — the agent gets no chance to react to its own output. There is no grounded retry loop, no static-check verifier (PHPStan, composer validate), and no failure-capture mechanism that could feed Phase 2 memory. The design report (`docs/agent-learning-from-feedback-2026-05-03.md` §1, §2.5) calls this the largest measurable gap the inventory turned up.

## Solution Statement

1. Restructure `run_tests()` to return `{passed, test_results, structured_errors[]}` with a stack-specific error adapter (pytest/PHPUnit/PHPStan/composer).
2. Add `run_static_checks()` to `DrupalDeveloperAgent` (PHPStan + composer validate via `env_manager.exec`) and to `PythonDeveloperAgent` (ruff + mypy).
3. Wrap the code-write step in a capped retry loop (`MAX_ATTEMPTS=3`, D1) that feeds structured errors into a refine prompt. A hard cap composes with the existing `guardrails.max_consecutive_repeats=10` (second-layer safety).
4. Introduce a **minimal** event bus + SQLite persistence layer sufficient to carry `TestResultRecorded`, `StaticCheckRecorded`, `DeveloperCappedOut`, and `PostmortemRecorded`, plus a persist-then-publish subscriber pattern (seam for Phase 2 extensions). Gated behind `DEV_VERIFIER_LOOP=1`; off by default for rollback.
5. Ship migration `003_postmortems.sql` with the exact schema from design §6.2 — `provenance` and `superseded_by` are **non-negotiable** (reviewer §phase-1 criterion).
6. On cap-out, the `DeveloperCappedOut` subscriber (living in a new `post_execute.py`) (a) inserts a postmortem row with `provenance='auto'`, `fix_summary=NULL`; (b) ensures the MR is in draft state (D7); (c) posts exactly one MR comment (D8 — no per-retry comments).

**Foundation note:** `src/core/{events,persistence,execution}/` is empty on this branch (`feat/sentinel-learning-system`). The Command Center foundation exists on `feat/interactive-cli` but has not merged. Per user direction, Phase 1 builds a **minimal** `core/` subset scoped strictly to what Loop A requires. If `feat/interactive-cli` lands on main later, its fuller infrastructure supersedes ours by commit order; no migration shim is written.

## Metadata

| Field            | Value                                                                                    |
| ---------------- | ---------------------------------------------------------------------------------------- |
| Type             | NEW_CAPABILITY                                                                           |
| Complexity       | HIGH                                                                                     |
| Systems Affected | `src/agents/`, `src/core/` (new), `src/gitlab_client.py`, `src/cli.py`, `tests/`         |
| Dependencies     | existing: `claude-agent-sdk ^0.1.20`, `pydantic ^2.5`, `sqlite3` stdlib, `subprocess`    |
| Estimated Tasks  | 14                                                                                       |
| Duration         | 2–3 weeks (matches design §10 Phase 1 estimate)                                          |
| Rollback         | `DEV_VERIFIER_LOOP=0` → single-shot behavior restored                                    |
| Gate to Phase 2  | ≥20 real executions with Loop A on; no runaway cost; every §7 handover criterion ticked  |

---

## UX Design

### Before State

```
╔══════════════════════════════════════════════════════════════════════╗
║  BEFORE  — single-shot developer, brittle TDD                        ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║   ┌──────────────┐    TDD prompt    ┌──────────────┐                ║
║   │ implement_   │ ───────────────► │  Developer   │                ║
║   │  feature()   │                  │  (Sonnet)    │                ║
║   └──────┬───────┘ ◄─ diff ──       └──────────────┘                ║
║          │                                                           ║
║          ▼                                                           ║
║   ┌──────────────┐                                                   ║
║   │ run_tests()  │ ── fails ──► RuntimeError ──► MR stays as-is     ║
║   │  {success,   │                                                   ║
║   │   output,    │                                                   ║
║   │   return_code│                                                   ║
║   │  }           │                                                   ║
║   └──────────────┘                                                   ║
║                                                                      ║
║   FAIL MODE: agent never sees its own error; no retry; no static-   ║
║   check; nothing captured for future learning. Reviewer inherits    ║
║   broken code.                                                       ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔══════════════════════════════════════════════════════════════════════╗
║  AFTER — Loop A: grounded, capped, with structured feedback          ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║   ┌──────────────┐   attempts=0..2                                   ║
║   │ implement_   │ ──┐                                               ║
║   │  feature()   │   │                                               ║
║   └──────────────┘   │                                               ║
║          ▲           ▼                                               ║
║          │    ┌──────────────┐     refine prompt                    ║
║          │    │  Developer   │ ◄──(structured_errors)──┐            ║
║          │    │  (Sonnet)    │                         │            ║
║          │    └──────┬───────┘                         │            ║
║          │           │ diff                            │            ║
║          │           ▼                                 │            ║
║          │    ┌────────────────────────┐               │            ║
║          │    │  Verifier = tests      │               │            ║
║          │    │  + static-checks       │ ──fail──────┬─┘            ║
║          │    │  -> structured_errors  │             │              ║
║          │    └──────────┬─────────────┘             │              ║
║          │               │ ok                        │              ║
║          └───────────────┘                           │              ║
║                                                      ▼ 3 fails      ║
║                                            ┌───────────────────┐    ║
║                                            │ DeveloperCappedOut│    ║
║                                            └────────┬──────────┘    ║
║                                                     │               ║
║                            ┌────────────────────────┼───────────┐   ║
║                            ▼                        ▼           ▼   ║
║                     postmortems row         ensure MR draft   1 MR  ║
║                     (provenance='auto',     (revert if un-    comm- ║
║                      fix_summary=NULL)      drafted)          ent   ║
║                                                                      ║
║   WIN MODE: agent fixes its own errors 60-70%-of-the-time [target]. ║
║   Cap-out produces structured data Phase 2 will learn from.         ║
║   Reviewer attention is spent on real escalations, not noise.       ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                  | Before                                       | After                                                                          | User Impact                                                    |
| ----------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------ | -------------------------------------------------------------- |
| `sentinel execute <ticket>` exit         | "tests failed" RuntimeError, broken MR live  | up to 3 retries, then `DeveloperCappedOut`; MR stays draft; single escalation  | Reviewer no longer paged on transient failures                 |
| MR timeline                               | 0 or many Sentinel noise comments            | 0 comments on retries (D8); exactly 1 comment on cap-out (D7+D8)               | Draft status is truthful again                                 |
| `sentinel` logs                           | test stdout only                             | structured errors, attempt counter, event-emitted per attempt                  | Postmortem debugging is possible                               |
| `~/.sentinel/<db>.sqlite`                 | n/a                                          | `executions`, `events`, `agent_results`, `postmortems` populated               | Phase 2 has data to query                                      |
| `DEV_VERIFIER_LOOP` env var              | n/a                                          | `=1` enables Loop A, `=0` (default) restores today's behavior                  | Instant rollback during incident                               |

---

## Mandatory Reading

**CRITICAL: Implementation agents MUST read these before starting any task.**

| Priority | File                                                                  | Lines      | Why Read This                                                                |
| -------- | --------------------------------------------------------------------- | ---------- | ---------------------------------------------------------------------------- |
| P0       | `docs/agent-learning-from-feedback-2026-05-03.md`                     | §5.1, §6.2, §7.3–7.5, §8, §9 | Loop A contract; postmortem schema; prompt/policy; Phase 1 tasks; risks      |
| P0       | `docs/agent-learning-from-feedback-DECISIONS.md`                      | all (D1–D8)| Binding ADRs (N=3, draft on cap-out, 0 retry comments, etc.)                 |
| P0       | `docs/agent-learning-from-feedback-HANDOVER.md`                       | §4, §7, §9, §10 | 10 settled invariants, exit criteria, file:line pointers, risk ranking       |
| P0       | `src/agents/base_developer.py`                                        | 286–430    | Current TDD loop; `implement_feature` wraps Loop A; `run_tests` is the entry |
| P0       | `src/agents/drupal_developer.py`                                      | 150–221    | `validate_config` pattern for container exec — mirror for static checks      |
| P1       | `src/guardrails.py`                                                   | 208–237    | `max_consecutive_repeats=10` — Loop A must compose, not replace              |
| P1       | `src/gitlab_client.py`                                                | 117–283    | `update_merge_request`, `mark_as_ready` patterns (for draft revert)          |
| P1       | `src/environment_manager.py`                                          | 168–195    | `exec(ticket_id, service, command, workdir)` signature                       |
| P1       | `src/compose_runner.py`                                               | 18–26      | `ComposeResult(success, stdout, stderr, returncode)`                         |
| P1       | `.claude/agents/sentinel-verifier-loop-expert.md`                     | all        | Owns `base_developer.py`, `drupal_developer.py`, `python_developer.py`       |
| P1       | `.claude/agents/sentinel-persistence-expert.md`                       | all        | Owns migrations + helper modules                                             |
| P1       | `.claude/agents/sentinel-learning-integrator.md`                      | all        | Owns `events/types.py`, `cli.py`, `post_execute.py`                          |
| P1       | `.claude/agents/sentinel-test-harness-expert.md`                      | all        | Owns every test; exit-criterion accountability list                          |
| P1       | `.claude/agents/sentinel-learning-reviewer.md`                        | all        | Read-only gate reviewer; invoked before Phase 1 declared done                |
| P2       | `tests/test_drupal_developer.py`                                      | 13–329     | Fixture + container-test pattern to mirror                                   |
| P2       | `pyproject.toml`                                                      | all        | ruff, mypy, pytest, poetry                                                   |

**External references (already settled in design doc):** no new external research needed. The design report is the external-research artifact.

---

## Patterns to Mirror

### NAMING_CONVENTION — Developer agent subclass pattern

```python
# SOURCE: src/agents/drupal_developer.py:14-46
# COPY THIS PATTERN when extending DrupalDeveloperAgent with run_static_checks()
class DrupalDeveloperAgent(BaseDeveloperAgent):
    """Agent that implements Drupal features using Test-Driven Development."""

    _VALID_EXTENSIONS: frozenset = frozenset({
        ".php", ".module", ".inc", ".install", ".theme", ".profile",
        # ...
    })

    def __init__(self) -> None:
        super().__init__(
            agent_name="drupal_developer",
            model="claude-4-5-sonnet",
            temperature=0.2,
        )
        self._load_stack_overlay()
        self._inject_environment_context()
```

### RUN_TESTS_RETURN_SHAPE — Current shape (must be extended, not replaced)

```python
# SOURCE: src/agents/base_developer.py:547-573
# CURRENT RETURN (rewrite target):
return {
    "success": success,           # bool
    "output": output,             # str (stdout+stderr)
    "return_code": result.returncode,
}

# NEW RETURN SHAPE (Phase 1 target):
return {
    "passed": success,                    # renamed `success` → `passed` per design §5.1
    "test_results": output,               # raw stdout+stderr preserved (back-compat for callers)
    "structured_errors": [                # list[StructuredError]
        {"file": "src/foo.py", "line": 42, "rule": "test_failed", "message": "..."},
        # ...
    ],
    "return_code": result.returncode,     # preserved for logging/exit-code checks
}
```

**Back-compat note:** any existing caller of `run_tests()` that reads `result["success"]` must be updated to `result["passed"]`. Grep `.run_tests` in the repo: `src/agents/base_developer.py:357, :696, :1051`.

### CONTAINER_EXEC_PATTERN — Mirror for PHPStan/composer-validate

```python
# SOURCE: src/agents/drupal_developer.py:150-213 (validate_config)
# COPY THIS PATTERN for run_static_checks():
def run_static_checks(self, worktree_path: Path) -> Dict[str, Any]:
    if not self._env_manager or not self._env_ticket_id:
        logger.warning("No container environment — skipping static checks")
        return {"passed": True, "test_results": "Skipped", "structured_errors": [], "return_code": 0}

    self._ensure_composer_deps()

    phpstan = self._env_manager.exec(
        ticket_id=self._env_ticket_id,
        service="appserver",
        command=["vendor/bin/phpstan", "analyse", "--error-format=json", "--no-progress", "web/modules/custom"],
        workdir="/app",
    )
    composer = self._env_manager.exec(
        ticket_id=self._env_ticket_id,
        service="appserver",
        command=["composer", "validate", "--strict", "--no-check-all"],
        workdir="/app",
    )

    errors = _parse_phpstan_json(phpstan.stdout) + _parse_composer_validate(composer.stdout + composer.stderr)
    passed = (phpstan.returncode == 0) and (composer.returncode == 0)
    return {
        "passed": passed,
        "test_results": phpstan.stdout + composer.stdout + composer.stderr,
        "structured_errors": errors,
        "return_code": 0 if passed else 1,
    }
```

### FEATURE_FLAG — Env-var pattern

```python
# SOURCE: src/cli.py:1185 (existing os.environ.get pattern)
# COPY THIS PATTERN for DEV_VERIFIER_LOOP:
import os

def _verifier_loop_enabled() -> bool:
    """Phase 1 feature flag — set DEV_VERIFIER_LOOP=1 to enable Loop A."""
    return os.getenv("DEV_VERIFIER_LOOP", "0") == "1"
```

### TEST_FIXTURE_PATTERN

```python
# SOURCE: tests/test_drupal_developer.py:13-59
# COPY THIS PATTERN for new Loop A tests:
@pytest.fixture
def mock_config():
    with patch("src.agents.base_agent.get_config") as mock:
        config = Mock()
        config.get_agent_config.return_value = {"model": "claude-4-5-sonnet", "temperature": 0.2}
        config.get_llm_config.return_value = {"mode": "custom_proxy", "api_key": "test-api-key", "base_url": "https://test.api.com/v1"}
        config.get.return_value = ["Read", "Grep", "Glob"]
        mock.return_value = config
        yield config
```

### CONTAINER_TEST_ASSERTION_PATTERN

```python
# SOURCE: tests/test_drupal_developer.py:278-329
# COPY THIS PATTERN for run_static_checks() tests:
def test_static_checks_call_phpstan_and_composer(self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree):
    agent = DrupalDeveloperAgent()
    mock_env_mgr = Mock()
    mock_env_mgr.exec.return_value = Mock(success=True, stdout='{"files":{}}', stderr="", returncode=0)
    agent.set_environment(mock_env_mgr, "TEST-123")

    result = agent.run_static_checks(temp_worktree)

    commands = [call.kwargs["command"] for call in mock_env_mgr.exec.call_args_list]
    assert any("phpstan" in c[0] for c in commands), "phpstan not invoked"
    assert any("composer" in c[0] and "validate" in c for c in commands), "composer validate not invoked"
    assert result["passed"] is True
    assert result["structured_errors"] == []
```

### GITLAB_DRAFT_REVERT — New helper, symmetrical to mark_as_ready

```python
# SOURCE: src/gitlab_client.py:263-283 (mark_as_ready)
# MIRROR as mark_as_draft:
def mark_as_draft(self, project_id: str, mr_iid: int) -> Dict[str, Any]:
    """Revert an un-drafted MR back to draft. Idempotent: no-op if already draft."""
    mr_data = self.get_merge_request(project_id, mr_iid)
    current_title = mr_data.get("title", "")
    if current_title.lower().startswith("draft:"):
        return mr_data  # already draft — D7 says "leave it alone"
    new_title = f"Draft: {current_title}"
    return self.update_merge_request(project_id, mr_iid, title=new_title)
```

### SQLITE_MIGRATION_PATTERN

```sql
-- SOURCE: design §6.2 (reviewer rejects any Phase 1 migration that deviates)
-- Filename: src/core/persistence/migrations/003_postmortems.sql
-- Numbered 003 per design naming, even though 001/002 are freshly created in this phase —
-- leaves room for the Command Center foundation's own 001_init / 002_workers if
-- feat/interactive-cli lands later.
CREATE TABLE postmortems (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  execution_id TEXT NOT NULL REFERENCES executions(id),
  stack_type TEXT NOT NULL,
  agent TEXT NOT NULL,
  failure_signature TEXT NOT NULL,
  context_excerpt TEXT,
  fix_summary TEXT,
  provenance TEXT NOT NULL,            -- 'auto' | 'human-edited'  (NOT NULL — reviewer invariant)
  confidence INTEGER DEFAULT 50,
  created_at TEXT NOT NULL,
  superseded_by INTEGER REFERENCES postmortems(id)
);
CREATE INDEX idx_postmortems_lookup ON postmortems(stack_type, agent, failure_signature);
```

---

## Files to Change

### Foundation (minimal — only what Loop A needs)

| File                                              | Action | Justification                                                               | Owner agent                       |
| ------------------------------------------------- | ------ | --------------------------------------------------------------------------- | --------------------------------- |
| `src/core/__init__.py`                            | CREATE | Package marker                                                              | integrator                        |
| `src/core/persistence/__init__.py`                | CREATE | Package marker; re-export `connect`, `apply_migrations`                     | persistence-expert                |
| `src/core/persistence/db.py`                      | CREATE | `connect()`, `apply_migrations()` — WAL + foreign_keys=ON                   | persistence-expert                |
| `src/core/persistence/migrations/001_init.sql`    | CREATE | `executions`, `events`, `agent_results`, `schema_migrations` (minimal)      | persistence-expert                |
| `src/core/persistence/migrations/003_postmortems.sql` | CREATE | Phase 1 schema per design §6.2 — see §Patterns above                    | persistence-expert                |
| `src/core/persistence/postmortems.py`             | CREATE | `insert_postmortem(conn, **fields)` helper; parameterized SQL only          | persistence-expert                |
| `src/core/events/__init__.py`                     | CREATE | Package marker; re-export `EventBus`, event classes                         | integrator                        |
| `src/core/events/types.py`                        | CREATE | `TestResultRecorded`, `StaticCheckRecorded`, `DeveloperCappedOut`, `PostmortemRecorded` (pydantic v2; `@dataclass(frozen=True)` alt acceptable — pick one and document) | integrator |
| `src/core/events/bus.py`                          | CREATE | `EventBus.publish()` persist-first (INSERT into events, then fan out); swallow subscriber exceptions per design-doc precedent in d75d276 | integrator |
| `src/core/execution/__init__.py`                  | CREATE | Package marker                                                              | integrator                        |
| `src/core/execution/post_execute.py`              | CREATE | `DeveloperCappedOut` subscriber: insert postmortem, ensure MR draft, post one comment | integrator                        |

### Verifier loop + static checks

| File                               | Action | Justification                                                                             | Owner agent              |
| ---------------------------------- | ------ | ----------------------------------------------------------------------------------------- | ------------------------ |
| `src/agents/base_developer.py`     | UPDATE | Rewrite `run_tests()` return shape; wrap `implement_feature` in capped retry loop (gated) | verifier-loop-expert     |
| `src/agents/drupal_developer.py`   | UPDATE | Add `run_static_checks()` (PHPStan + composer validate); PHPUnit-to-structured-error parser | verifier-loop-expert   |
| `src/agents/python_developer.py`   | UPDATE | Add `run_static_checks()` (ruff + mypy); pytest-to-structured-error parser                | verifier-loop-expert     |
| `src/agents/_structured_errors.py` | CREATE | Parsers: `parse_phpstan_json`, `parse_phpunit_junit`, `parse_pytest_short`, `parse_composer_validate`, `parse_mypy`, `parse_ruff_json`. `StructuredError = TypedDict('StructuredError', {'file': str, 'line': int, 'rule': str, 'message': str})` | verifier-loop-expert |

### Seams + CLI

| File                          | Action | Justification                                                                 | Owner agent        |
| ----------------------------- | ------ | ----------------------------------------------------------------------------- | ------------------ |
| `src/gitlab_client.py`        | UPDATE | Add `mark_as_draft()` symmetrical to `mark_as_ready` (:263-283)               | integrator         |
| `src/cli.py`                  | UPDATE | Read `DEV_VERIFIER_LOOP`; wire `EventBus` into execute; attach subscribers; open SQLite connection per run | integrator |
| `prompts/shared/base_instructions.md` | UPDATE | Add refine-prompt policy (design §7.3: "When the verifier fails, respond with a single targeted fix; do not rewrite unrelated code. Stop after 3 failed attempts.") | integrator |

### Tests (every code change has a test)

| File                                                 | Action | Justification                                                                | Owner agent              |
| ---------------------------------------------------- | ------ | ---------------------------------------------------------------------------- | ------------------------ |
| `tests/core/__init__.py`                             | CREATE | Package marker                                                               | test-harness-expert      |
| `tests/core/test_persistence.py`                     | CREATE | Migration idempotency; WAL + FK pragmas; `insert_postmortem` round-trip      | test-harness-expert      |
| `tests/core/test_postmortems.py`                     | CREATE | Schema round-trip; `provenance` NOT NULL enforced; `superseded_by` FK works  | test-harness-expert      |
| `tests/core/test_event_bus.py`                       | CREATE | Persist-first ordering; subscriber exception swallowed; seq monotonic        | test-harness-expert      |
| `tests/agents/test_base_developer_verifier_loop.py`  | CREATE | Retry count caps at 3; flaky-then-pass; structured_errors piped into refine  | test-harness-expert      |
| `tests/agents/test_drupal_static_checks.py`          | CREATE | `run_static_checks` invokes phpstan + composer validate; parses JSON          | test-harness-expert      |
| `tests/agents/test_python_static_checks.py`          | CREATE | `run_static_checks` invokes ruff + mypy; parses output                       | test-harness-expert      |
| `tests/agents/test_structured_error_adapters.py`     | CREATE | Fixture-driven parser tests (`tests/fixtures/static_check_output/*.json/.txt`) | test-harness-expert    |
| `tests/integration/test_verifier_retry.py`           | CREATE | End-to-end: failing-forever developer → 3 attempts → postmortem row → `DeveloperCappedOut` emitted → MR set to draft → 1 MR comment | test-harness-expert |
| `tests/test_gitlab_client.py` (extend)               | UPDATE | Add `mark_as_draft` cases: already-draft (no-op), ready → draft title change | test-harness-expert      |
| `tests/fixtures/static_check_output/`                | CREATE | Real PHPStan `--error-format=json`, PHPUnit JUnit XML, pytest `--tb=short`, composer validate samples | test-harness-expert |

---

## NOT Building (Scope Limits — per design §8, §10, and decisions)

These are Phase 2+ work and MUST NOT leak into Phase 1:

- **Postmortem retrieval injection into prompts** (Phase 2, design §8 task 8). No call into `prompt_loader.py` to read `postmortems`.
- **FeedbackDistiller subagent** (Phase 2, design §C.2, D2). No Haiku calls for MR-comment distillation.
- **`feedback_rules` / `feedback_observations` tables** (Phase 2, design §C.3). Migration 004 is explicitly out of scope.
- **`sentinel rules` CLI** (Phase 2, design §C.7). Not even a stub.
- **Overlay PR proposer** (Phase 2, design §8 task 11, D4 deferred).
- **Reviewer → planner escalation (Loop C)** (Phase 2, design §8 task 12, D8 second bullet).
- **Confidence-miss auto-investigation** (Phase 2, design §8 task 13).
- **Merge-vs-revert outcome ingestion / `project_sync_state`** (Phase 3, D6).
- **Widening of project rules to stack rules** (Phase 2, D4 deferred).
- **Per-stack override of N=3 cap** (design D1 — single global constant; revisit only with telemetry).
- **Probation-rule injection** (D3; no rules exist yet in Phase 1).
- **MR comments on Loop A retries** (D8 — zero comments on retries; only cap-out comment).
- **Un-drafting on cap-out, even "as a hint"** (D7 — never).
- **Pydantic-vs-dataclass debate** — pick one for `events/types.py` and move on; not worth more than a line in the PR description.
- **Vector DB, embeddings, fine-tuning** (design §6.1 "Skip").
- **Async event bus / websockets / HTTP surface** (out of scope; we match the d75d276 pattern of in-process sync persist-then-publish).

---

## Step-by-Step Tasks

Execute in dependency order. Each task is atomic and independently verifiable. Every task lists a **Delegate to** agent per the §6 roster in the handover.

### Task 0: Pre-flight — create the 5 Phase 1 specialist agent files if not yet present

- **ACTION**: VERIFY — all five agents already exist at `.claude/agents/sentinel-*`. If any is missing, STOP and escalate.
- **VALIDATE**: `ls .claude/agents/sentinel-*.md | wc -l` returns 5.
- **Owner**: orchestrator (main session).
- **Handover §6** lists: `sentinel-learning-reviewer`, `sentinel-learning-integrator`, `sentinel-persistence-expert`, `sentinel-verifier-loop-expert`, `sentinel-test-harness-expert`. All present as of this plan's drafting.

### Task 1: CREATE `src/core/persistence/db.py` + `migrations/001_init.sql`

- **Delegate to**: `sentinel-persistence-expert`.
- **ACTION**: CREATE connection helper + runner + minimal `001_init.sql`.
- **IMPLEMENT**:
  - `connect(path: str = None) -> sqlite3.Connection` — honors `SENTINEL_DB_PATH`, validates regular file, enables `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`.
  - `apply_migrations(conn) -> None` — reads `migrations/*.sql` in numeric order, records in `schema_migrations(version TEXT PRIMARY KEY, applied_at TEXT)`, is idempotent.
  - `001_init.sql` creates: `executions(id, ticket_id, kind, status, phase, cost_cents, error, metadata_json, created_at)`, `events(execution_id, seq, ts, agent, type, payload_json, PRIMARY KEY(execution_id, seq))`, `agent_results(execution_id, agent, result_json, created_at)`, `schema_migrations`. Columns match design §2.3 inventory as close as reasonable without over-engineering.
- **MIRROR**: d75d276 commit message text summarizes the expected shape (see handover §9 for context on what interactive-cli has). Split SQL on `;` and exec per-statement inside explicit `BEGIN IMMEDIATE` per that commit's note.
- **GOTCHA**: `executescript()` silently commits. Use per-statement `execute()` inside `BEGIN IMMEDIATE`/`COMMIT`.
- **VALIDATE**: `pytest tests/core/test_persistence.py -v` — migration idempotency, WAL + FK pragmas.

### Task 2: CREATE `src/core/persistence/migrations/003_postmortems.sql` + `postmortems.py`

- **Delegate to**: `sentinel-persistence-expert`.
- **ACTION**: CREATE migration + helper module.
- **IMPLEMENT**: migration per §Patterns section above; `insert_postmortem(conn, execution_id, stack_type, agent, failure_signature, context_excerpt=None, fix_summary=None, provenance='auto', confidence=50) -> int` — returns new row id. NO `update_postmortem`, NO `delete_postmortem` — revocation is `superseded_by`-based later (reviewer invariant §D4 append-only).
- **MIRROR**: persistence-expert agent spec §Phase-1-schema — exact columns.
- **GOTCHA**: `provenance` is `NOT NULL`. Don't let the helper accept `None`.
- **GOTCHA**: `failure_signature` normalization (lowercase, strip paths, strip line numbers, trim 200 chars) — this computation lives in `_structured_errors.py` in Task 6, not here. The helper just inserts.
- **VALIDATE**: `pytest tests/core/test_postmortems.py -v` — schema round-trip; `provenance=None` rejected; `superseded_by` FK works; repeated insert with same signature does NOT deduplicate (that's Phase 2 extraction-job work).

### Task 3: CREATE `src/core/events/types.py` + `bus.py`

- **Delegate to**: `sentinel-learning-integrator`.
- **ACTION**: CREATE event catalogue and persist-then-publish bus.
- **IMPLEMENT**:
  - `types.py`: pydantic v2 `BaseModel` classes. Every event has `execution_id: str`, `ts: str (ISO-8601 UTC)`, `type: Literal[...]` discriminator. Phase-1 events: `TestResultRecorded(passed, attempt, structured_errors_count)`, `StaticCheckRecorded(checker, passed, structured_errors_count)`, `DeveloperCappedOut(agent, attempts, last_structured_errors: list[dict])`, `PostmortemRecorded(postmortem_id, failure_signature)`. Oversized payloads truncated with `_truncated: true` marker (d75d276 pattern).
  - `bus.py`: `EventBus(conn)`; `publish(event)` writes to `events` table first (INSERT with auto-incrementing `seq` per `execution_id`), then calls subscribers; `subscribe(event_type, handler)`; subscriber exceptions caught + logged (so a bad subscriber never crashes a run).
- **MIRROR**: d75d276 commit log explicitly describes this shape ("persist-first; subscriber exceptions swallowed; per-execution seq monotonic; oversized payloads truncated with `_truncated` marker").
- **GOTCHA**: Do not use `datetime.utcnow()` — it's deprecated in 3.12+. Use `datetime.now(timezone.utc).isoformat()`.
- **GOTCHA**: `seq` is per-`execution_id`, not global. Compute via `SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE execution_id = ?` inside the same transaction. No ORM, plain sqlite3.
- **VALIDATE**: `pytest tests/core/test_event_bus.py -v` — persist-first ordering (assert row exists when subscriber fires), subscriber raises → next subscriber still runs, seq monotonic.

### Task 4: CREATE `src/agents/_structured_errors.py`

- **Delegate to**: `sentinel-verifier-loop-expert`.
- **ACTION**: CREATE parser module for all verifier outputs.
- **IMPLEMENT**: `StructuredError = TypedDict("StructuredError", {"file": str, "line": int, "rule": str, "message": str})`. Functions:
  - `parse_pytest_short(stdout: str) -> list[StructuredError]` — parses `pytest --tb=short` text. Each `FAILED file::test - reason` becomes one error; `rule="test_failed"`.
  - `parse_phpunit_junit(xml: str) -> list[StructuredError]` — parses JUnit XML; each `<failure>` or `<error>` becomes one entry.
  - `parse_phpstan_json(json_str: str) -> list[StructuredError]` — `--error-format=json`; iterate `files[<path>].messages[]`, `rule = identifier or f"level:{level}"`.
  - `parse_composer_validate(output: str) -> list[StructuredError]` — binary ok/not-ok; if not-ok, one entry with `file="composer.json"`, `line=0`, `rule="composer_validate"`, `message=output`.
  - `parse_mypy(stdout: str) -> list[StructuredError]` — `file:line: error: message [rule]` regex.
  - `parse_ruff_json(json_str: str) -> list[StructuredError]` — `ruff --output-format=json`.
  - `normalize_failure_signature(errors: list[StructuredError]) -> str` — take first, lowercase, strip absolute paths (`re.sub(r'/[^\s]+/', '', ...)`), strip `line \d+` tokens, trim to 200 chars. Deterministic — same errors produce same signature.
- **GOTCHA**: Always return `[]` on empty input; never `None`. Tests rely on `len(errors)`.
- **GOTCHA**: Be tolerant of malformed JSON (`try/except ValueError → return []` with a warning log) — PHPStan occasionally emits warnings on stderr before JSON.
- **VALIDATE**: `pytest tests/agents/test_structured_error_adapters.py -v` — golden-file tests from `tests/fixtures/static_check_output/` with real samples (see Task 13).

### Task 5: UPDATE `src/agents/base_developer.py` — restructure `run_tests()`

- **Delegate to**: `sentinel-verifier-loop-expert`.
- **ACTION**: Rewrite `run_tests()` (`:411-430`, `:497-590`) to return the new shape.
- **IMPLEMENT**:
  - Keep dispatch `_run_tests_in_container` vs `_run_tests_on_host`; both return the new shape.
  - Parse captured output with the appropriate adapter based on a new abstract `_parse_test_output(raw: str, returncode: int) -> list[StructuredError]` that subclasses implement.
  - Update all three call sites (`:357`, `:696`, `:1051`) to read `result["passed"]` instead of `result["success"]`.
- **BACK-COMPAT**: no legacy dict shim; grep confirms only internal callers read this, and we update all of them. Per project rule (§Extras in CLAUDE.md): "don't play whack-a-mole" — update the shape once and fix the 3 call sites.
- **VALIDATE**: `pytest tests/agents/test_base_developer_verifier_loop.py::test_run_tests_returns_new_shape` + existing `tests/test_drupal_developer.py`, `tests/test_python_developer.py` still green.

### Task 6: UPDATE `src/agents/drupal_developer.py` + `python_developer.py` — implement `_parse_test_output` + `run_static_checks`

- **Delegate to**: `sentinel-verifier-loop-expert`.
- **ACTION**: Wire parsers + add static-check methods.
- **IMPLEMENT (drupal)**: `_parse_test_output` calls `parse_phpunit_junit` if a JUnit file path was produced (phpunit supports `--log-junit`); fallback to text parser. `run_static_checks()` per §Patterns → PHPStan + composer validate. Add `--log-junit=/tmp/phpunit-junit.xml` to the phpunit command at `drupal_developer.py:215-221` and read the file back.
- **IMPLEMENT (python)**: `_parse_test_output` calls `parse_pytest_short`. `run_static_checks()` runs `ruff check --output-format=json .` + `mypy .` (host-side; python projects don't use the appserver container).
- **MIRROR**: `drupal_developer.py:150-213` (validate_config container-exec pattern).
- **GOTCHA**: PHPStan and composer validate need `vendor/` — call `_ensure_composer_deps()` (`base_developer.py:432-446`) before.
- **GOTCHA**: `run_static_checks` returns `passed=True` with `structured_errors=[]` when container is absent (mirrors validate_config behavior at `drupal_developer.py:155-165`). Do NOT fail a ticket because the container is missing — that's an env issue, not agent code.
- **VALIDATE**: `pytest tests/agents/test_drupal_static_checks.py tests/agents/test_python_static_checks.py -v`.

### Task 7: UPDATE `src/agents/base_developer.py` — wrap `implement_feature` with Loop A

- **Delegate to**: `sentinel-verifier-loop-expert`.
- **ACTION**: Convert the single-shot `implement_feature` (`:286-404`) into a capped retry loop.
- **IMPLEMENT**:
  ```python
  MAX_ATTEMPTS: int = 3  # D1 — single global constant, not per-stack
  ```
  ```python
  def implement_feature(self, task, context, worktree_path, commit_prefix="feat", user_prompt=None):
      if not _verifier_loop_enabled():
          return self._implement_feature_single_shot(task, context, worktree_path, commit_prefix, user_prompt)
      tdd_prompt = self._build_tdd_prompt(task, context, worktree_path)
      tdd_prompt = self._append_operator_prompt(tdd_prompt, user_prompt)

      last_errors: list[StructuredError] = []
      for attempt in range(1, MAX_ATTEMPTS + 1):
          result = asyncio.run(self.agent_sdk.execute_with_tools(
              prompt=tdd_prompt if attempt == 1 else self._build_refine_prompt(last_errors, attempt),
              session_id=None,  # SDK preserves history within the call
              system_prompt=self.system_prompt,
              cwd=str(worktree_path),
          ))
          # ... extract files, filter junk (unchanged)
          test_result = self.run_tests(worktree_path)
          static_result = self.run_static_checks(worktree_path)
          self._emit(TestResultRecorded(passed=test_result["passed"], attempt=attempt,
                                         structured_errors_count=len(test_result["structured_errors"])))
          self._emit(StaticCheckRecorded(checker="combined", passed=static_result["passed"],
                                          structured_errors_count=len(static_result["structured_errors"])))
          last_errors = test_result["structured_errors"] + static_result["structured_errors"]
          if test_result["passed"] and static_result["passed"]:
              return {"success": True, ...}  # unchanged happy-path payload

      # capped out
      self._emit(DeveloperCappedOut(agent=self.agent_name, attempts=MAX_ATTEMPTS,
                                     last_structured_errors=last_errors[:10]))
      raise DeveloperCappedOutException(f"Capped at {MAX_ATTEMPTS} attempts for task: {task}")
  ```
- **PATTERN**: `_build_refine_prompt(errors, attempt)` follows verifier-loop-expert spec:
  - Includes `structured_errors` verbatim as a bulleted list.
  - "This is attempt {attempt} of {MAX_ATTEMPTS}. Respond with a single targeted fix; do not rewrite unrelated code."
  - Does NOT include the previous diff (SDK session history already has it).
  - Does NOT inject postmortem rules (Phase 2 concern).
- **GOTCHA**: Guardrails (`guardrails.py:208-237`) caps tool-call repeats at 10. If an iteration triggers guardrail denial, `execute_with_tools` returns a failed result; treat that as a verifier failure and count the attempt — do not reset the counter.
- **GOTCHA**: No partial-credit loops — a single-error-left attempt still counts as a failure. Cap is hard.
- **GOTCHA**: `self._emit` needs an `EventBus`; wire via `set_event_bus(bus, execution_id)` on the agent (new method, called from `cli.py`). If no bus attached → emit is a no-op so unit tests don't need the foundation.
- **VALIDATE**:
  - `pytest tests/agents/test_base_developer_verifier_loop.py` — flaky-then-pass (attempts=2), cap-out (attempts=3, raises), single-pass (attempts=1).
  - `pytest tests/integration/test_verifier_retry.py` — end-to-end.

### Task 8: UPDATE `src/gitlab_client.py` — add `mark_as_draft`

- **Delegate to**: `sentinel-learning-integrator` (seam-only change).
- **ACTION**: Add method per §Patterns above.
- **VALIDATE**: `pytest tests/test_gitlab_client.py::test_mark_as_draft_*` — idempotent on already-draft, adds prefix otherwise.

### Task 9: CREATE `src/core/execution/post_execute.py` — `DeveloperCappedOut` subscriber

- **Delegate to**: `sentinel-learning-integrator`.
- **ACTION**: CREATE subscriber that owns the full cap-out side-effect chain (D7 + D8).
- **IMPLEMENT**:
  ```python
  def handle_developer_capped_out(
      event: DeveloperCappedOut, *,
      conn, gitlab_client, ticket_context, execution_id: str,
  ) -> None:
      signature = normalize_failure_signature(event.last_structured_errors)
      excerpt = json.dumps(event.last_structured_errors)[:4096]
      pid = insert_postmortem(
          conn, execution_id=execution_id, stack_type=ticket_context.stack_type,
          agent=event.agent, failure_signature=signature, context_excerpt=excerpt,
          fix_summary=None, provenance="auto",
      )
      # D7: MR stays/reverts to draft
      gitlab_client.mark_as_draft(ticket_context.gitlab_project, ticket_context.mr_iid)
      # D8: exactly one comment on cap-out
      gitlab_client.add_merge_request_comment(
          ticket_context.gitlab_project, ticket_context.mr_iid,
          f"**Sentinel paused here** — developer agent (`{event.agent}`) capped at "
          f"{event.attempts} attempts on this task. First error: "
          f"`{event.last_structured_errors[0]['rule'] if event.last_structured_errors else 'unknown'}`. "
          f"Postmortem #{pid} recorded.",
      )
      # Emit successor event so tests can assert the row was written
      _bus.publish(PostmortemRecorded(execution_id=execution_id, postmortem_id=pid,
                                       failure_signature=signature))
  ```
- **MIRROR**: No prior post_execute.py exists on this branch. Structure as a function module with `register(bus, ...)` that calls `bus.subscribe(DeveloperCappedOut, handle_developer_capped_out)`.
- **GOTCHA**: `_structured_errors[0]` may not exist if the list is empty — guard.
- **GOTCHA**: Subscriber exceptions are swallowed by the bus, so failure here is silent. Log with `logger.error(exc_info=True)`.
- **VALIDATE**: `pytest tests/integration/test_verifier_retry.py::test_cap_out_side_effects` — asserts all three: postmortem row, draft revert called, exactly one MR comment.

### Task 10: UPDATE `src/cli.py` — wire event bus + DEV_VERIFIER_LOOP flag

- **Delegate to**: `sentinel-learning-integrator`.
- **ACTION**: At `sentinel execute` entry, open SQLite conn, apply migrations, create `EventBus(conn)`, register subscribers via `post_execute.register(bus, conn=conn, gitlab_client=..., ticket_context=...)`, attach bus to developer agent via `agent.set_event_bus(bus, execution_id)`.
- **IMPLEMENT**:
  - `_verifier_loop_enabled()` at module scope; also emits a startup log line so ops can see it.
  - `SENTINEL_DB_PATH` env-var reading matches d75d276 ("regular-file validation").
  - `execution_id` — new ULID or UUID; written to `executions` table at start and updated on end.
- **PATTERN**: Match existing `os.environ.get()` style at `cli.py:1185`.
- **GOTCHA**: When `DEV_VERIFIER_LOOP=0`, still open the DB and apply migrations (tests rely on schema being there), but don't attach the bus — the loop falls back to single-shot via the flag check inside `implement_feature`.
- **VALIDATE**: Smoke: `DEV_VERIFIER_LOOP=1 sentinel execute TICKET-1` on a fixture ticket with a deliberately breaking test. Verify `events` and `postmortems` rows appear.

### Task 11: UPDATE `prompts/shared/base_instructions.md` — refine-prompt policy

- **Delegate to**: `sentinel-learning-integrator`.
- **ACTION**: Append one short paragraph per design §7.3. Exact text:
  > ### Handling verifier failures
  > When a verifier (tests, PHPStan, composer validate, lint/type-check) reports an error, respond with a single targeted fix. Do not rewrite unrelated code, do not refactor, do not hypothesize beyond what the structured error shows. After 3 failed attempts in a row, stop: Sentinel will record a postmortem and return the work to a human reviewer.
- **GOTCHA**: Adds ~80 tokens to the cached static block. Within §E.8 budget. Do not exceed this length — overlay bloat is a tracked risk (handover §10 risk 3).
- **VALIDATE**: `grep -c "Handling verifier failures" prompts/shared/base_instructions.md` == 1.

### Task 12: CREATE Phase 1 tests (main test harness work)

- **Delegate to**: `sentinel-test-harness-expert`.
- **ACTION**: Close each handover §7 exit-criterion test-box. Every box below must point at a real test assertion (no `assert True`, no skip):
  1. `test_run_tests_returns_new_shape` in `test_base_developer_verifier_loop.py` — covers both pass and fail fixtures, asserts `{passed, test_results, structured_errors}` keys exist.
  2. `test_loop_retries_with_structured_feedback_then_passes` — `flaky_developer(n=2)` fixture; asserts attempts == 2 and refine prompt included `structured_errors`.
  3. `test_loop_caps_at_three_when_developer_fails_forever` — `failing_forever_developer`; asserts exactly 3 invocations of `execute_with_tools`; asserts `DeveloperCappedOutException` raised.
  4. `test_static_checks_wired` in `test_drupal_static_checks.py` — asserts phpstan + composer validate commands invoked via mock `env_manager.exec`.
  5. `test_cap_out_posts_exactly_one_mr_comment` in `test_verifier_retry.py` — mock gitlab_client; assert `.add_merge_request_comment` called **once**.
  6. `test_postmortem_inserted_on_cap_out` — assert row exists with `provenance='auto'`, `fix_summary IS NULL`, non-empty `failure_signature`.
  7. `test_superseded_by_fk_roundtrip` in `test_postmortems.py` — write row A, write row B with `superseded_by=A.id`, read B back.
  8. `test_draft_reasserted_on_cap_out` — mock gitlab_client; assert `mark_as_draft` called regardless of initial state.
- **FIXTURES** (put in `tests/conftest.py`):
  - `postmortem_factory(**overrides)` — sensible defaults for every column.
  - `structured_error_factory(**overrides)` — `{file: "src/foo.py", line: 42, rule: "test_failed", message: "..."}`.
  - `failing_forever_developer` — Mock `execute_with_tools` always returning a failing diff.
  - `flaky_developer(n)` — fails first n attempts, then succeeds.
  - `sqlite_mem_conn` — `:memory:` DB with migrations applied.
  - `event_bus(sqlite_mem_conn)` — bus bound to the test connection.
- **MIRROR**: existing `tests/test_drupal_developer.py` fixture style (lines 13-59, container pattern at 278-329).
- **VALIDATE**: `pytest tests/ -v` all green; `pytest tests/ -m integration` one real-container smoke (optional; marker-gated per test-harness-expert spec).

### Task 13: CREATE `tests/fixtures/static_check_output/` with real verifier samples

- **Delegate to**: `sentinel-test-harness-expert`.
- **ACTION**: Capture real-world output once, check into repo, use as parser fixture input.
- **IMPLEMENT**: Files required:
  - `phpstan_pass.json`, `phpstan_fail.json` — `vendor/bin/phpstan analyse --error-format=json` from a real run.
  - `phpunit_junit_pass.xml`, `phpunit_junit_fail.xml`.
  - `pytest_short_pass.txt`, `pytest_short_fail.txt`.
  - `composer_validate_ok.txt`, `composer_validate_fail.txt`.
  - `mypy_pass.txt`, `mypy_fail.txt`.
  - `ruff_pass.json`, `ruff_fail.json`.
- **GOTCHA**: Strip any real secrets / customer identifiers before committing. Replace project paths with `/app/...`.
- **VALIDATE**: `pytest tests/agents/test_structured_error_adapters.py` — golden-file tests all pass.

### Task 14: Phase 1 gate review — invoke `sentinel-learning-reviewer`

- **Delegate to**: `sentinel-learning-reviewer` (read-only).
- **ACTION**: Before declaring Phase 1 done and before any Phase 2 work starts, invoke the reviewer agent. It will check the 10 design invariants, 8 ADRs (D1–D8), and the 8 exit-criterion boxes.
- **INVOKE**: From orchestrator, with the branch diff (`git diff main...HEAD`) as input context.
- **VALIDATE**: Reviewer returns `APPROVE` with every exit-criterion box ticked. `REQUEST_CHANGES` or `BLOCK` means fix-and-retry — do not proceed to the runtime gate (§Phase 2 gate) until APPROVE.
- **NOT DONE UNTIL**: ≥20 real `sentinel execute` runs with `DEV_VERIFIER_LOOP=1`, no runaway cost, cap-hit and first-pass-verifier-pass rates visible in telemetry (query `events` table for `TestResultRecorded` count grouped by `passed`).

---

## Testing Strategy

### Unit Tests

| Test file                                               | Test cases                                                           | Validates                                  |
| ------------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------------ |
| `tests/core/test_persistence.py`                        | migration idempotency, WAL/FK pragmas, path validation               | foundation correctness                     |
| `tests/core/test_postmortems.py`                        | round-trip, provenance NOT NULL, superseded_by FK                    | schema matches design §6.2                 |
| `tests/core/test_event_bus.py`                          | persist-first, subscriber isolation, seq monotonic                   | bus invariants (matches d75d276 behavior)  |
| `tests/agents/test_structured_error_adapters.py`        | golden-file × all parsers × pass/fail                                | parser correctness                         |
| `tests/agents/test_base_developer_verifier_loop.py`     | new shape, retry=1/2/3, cap-out raises, flag off → single-shot       | Loop A invariants (D1)                     |
| `tests/agents/test_drupal_static_checks.py`             | phpstan+composer validate invoked, structured errors shape           | Drupal verifier                            |
| `tests/agents/test_python_static_checks.py`             | ruff+mypy invoked, structured errors shape                           | Python verifier                            |
| `tests/test_gitlab_client.py` (extended)                | mark_as_draft idempotent, adds prefix, preserves non-draft title     | draft revert (D7)                          |

### Integration Tests

| Test file                                          | Test cases                                                    | Validates                              |
| -------------------------------------------------- | ------------------------------------------------------------- | -------------------------------------- |
| `tests/integration/test_verifier_retry.py`         | failing-forever fixture → 3 attempts, postmortem row, event emit, MR draft, 1 MR comment | Phase 1 end-to-end |
| `tests/integration/test_verifier_retry.py`         | flaky-then-pass fixture → ≤2 attempts, 0 MR comments (D8)     | D8 zero-on-retry policy                |

### Edge Cases Checklist

- [ ] Empty `structured_errors` list (test passed but static-check failed)
- [ ] Container unavailable → `run_static_checks` returns passed=True with "Skipped" message (matches validate_config behavior)
- [ ] Guardrail denies a tool call inside an attempt → counts as a failed attempt, does NOT reset counter
- [ ] PHPStan emits warnings to stderr before JSON on stdout → parser recovers, does not crash
- [ ] PHPUnit JUnit XML file not produced (phpunit config error) → fall back to text parser, log the gap
- [ ] MR already in draft when cap-out fires → `mark_as_draft` is no-op
- [ ] MR title contains unicode / special chars → `f"Draft: {title}"` prefix preserved correctly
- [ ] `execution_id` not set on the agent (e.g. unit test of implement_feature) → `_emit` is a no-op, not a crash
- [ ] Concurrent subscriber raises → other subscribers still fire (d75d276 invariant)
- [ ] `DEV_VERIFIER_LOOP=0` → zero behavior change vs today (regression gate)
- [ ] `DEV_VERIFIER_LOOP` unset → defaults to 0 (off)
- [ ] Structured error with `line=None` (e.g. composer validate) → serialization doesn't choke; `line: 0` used
- [ ] `failure_signature` normalization is deterministic — same errors → same signature across runs
- [ ] Postmortem written even if MR-comment post fails (GitLab 500) → persistence first, side-effects best-effort

### Metric-Level Validation (Phase 1 exit)

Query after ≥20 executions:
- `SELECT passed, COUNT(*) FROM events WHERE type='TestResultRecorded' GROUP BY passed;` — first-pass verifier-pass rate.
- `SELECT attempts, COUNT(*) FROM events WHERE type='DeveloperCappedOut' GROUP BY attempts;` — cap-hit rate (all should be `attempts=3`).
- `SELECT COUNT(*) FROM postmortems;` — should match cap-out count.
- Cost: sum `executions.cost_cents` for those 20 runs vs the prior 20 pre-flag runs. Must not exceed +30% (guardrail; any more = investigate runaway).

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
poetry run ruff check src/ tests/
poetry run mypy src/
```

**EXPECT**: Exit 0, no errors. Warnings in test files allowed if justified inline.

### Level 2: UNIT_TESTS

```bash
poetry run pytest tests/core/ tests/agents/test_base_developer_verifier_loop.py \
  tests/agents/test_drupal_static_checks.py tests/agents/test_python_static_checks.py \
  tests/agents/test_structured_error_adapters.py tests/test_gitlab_client.py -v
```

**EXPECT**: all tests pass.

### Level 3: INTEGRATION

```bash
poetry run pytest tests/integration/test_verifier_retry.py -v
```

**EXPECT**: both end-to-end scenarios pass.

### Level 4: FULL_SUITE (regression gate)

```bash
poetry run pytest -v
```

**EXPECT**: all pre-existing tests still green; no regressions in `test_drupal_developer.py`, `test_python_developer.py`, `test_cli_*`.

### Level 5: SMOKE (runtime gate)

```bash
# On a fixture ticket with a deliberately failing test:
DEV_VERIFIER_LOOP=1 SENTINEL_DB_PATH=/tmp/sentinel-smoke.db \
  poetry run sentinel execute TICKET-FIXTURE-FAIL

# Verify:
sqlite3 /tmp/sentinel-smoke.db "SELECT COUNT(*) FROM postmortems;"      # → 1
sqlite3 /tmp/sentinel-smoke.db "SELECT type, COUNT(*) FROM events GROUP BY type;"
# Expect: TestResultRecorded=3, StaticCheckRecorded≥1, DeveloperCappedOut=1, PostmortemRecorded=1
```

### Level 6: REAL-WORLD GATE (Phase 2 unlock)

```bash
# After ≥20 real executions with DEV_VERIFIER_LOOP=1 against real tickets:
sqlite3 ~/.sentinel/sentinel.db "
  SELECT
    (SELECT COUNT(*) FROM events WHERE type='TestResultRecorded' AND json_extract(payload_json,'$.passed')=1 AND json_extract(payload_json,'$.attempt')=1) AS first_pass,
    (SELECT COUNT(*) FROM events WHERE type='DeveloperCappedOut') AS cap_outs,
    (SELECT COUNT(DISTINCT execution_id) FROM events WHERE type='TestResultRecorded') AS total_runs;
"
```

**EXPECT** (Phase 2 gate per handover §7):
- `total_runs >= 20`
- `cap_outs / total_runs < 0.5` (at least half the time the loop succeeds — otherwise the verifier-retry leash is broken, not saving work)
- Cost delta vs baseline < +30%.

---

## Acceptance Criteria

- [ ] All handover §7 exit-criterion boxes tick (checked by `sentinel-learning-reviewer`).
- [ ] All 8 decisions (D1–D8) enforced in code:
  - [ ] D1: `MAX_ATTEMPTS=3` is a single named constant in `base_developer.py`, grep returns exactly 1 match.
  - [ ] D2: no Phase 1 use of any Haiku distiller (N/A — distiller is Phase 2).
  - [ ] D3: no Phase 1 reference to `PROBATION_INJECTION` (N/A).
  - [ ] D4: no widening PR logic (N/A).
  - [ ] D5: no CI overlay-size check added.
  - [ ] D6: no `project_sync_state` table (N/A).
  - [ ] D7: `mark_as_draft` called on cap-out regardless of prior state; tested.
  - [ ] D8: zero MR comments on Loop A retries; exactly one on cap-out; tested.
- [ ] All 10 decisions (handover §4) honored:
  - [ ] Decision 4 (append-only): no `UPDATE` or `DELETE` against `postmortems` in helper module; grep verified.
- [ ] Level 1–4 validation commands exit 0.
- [ ] Rollback verified: `DEV_VERIFIER_LOOP=0` restores single-shot behavior; existing tests stay green.
- [ ] ≥20 real executions logged; telemetry queried and attached to the PR description.
- [ ] `sentinel-learning-reviewer` returns `APPROVE` on the final PR.

---

## Completion Checklist

- [ ] Tasks 1–13 completed and merged (individually or as one branch)
- [ ] Each task validated by the owning specialist agent
- [ ] Level 1: ruff + mypy exit 0
- [ ] Level 2: unit tests pass
- [ ] Level 3: integration test passes
- [ ] Level 4: full regression suite green
- [ ] Level 5: smoke on fixture ticket produces expected rows/events
- [ ] Level 6: ≥20 real-world runs meet gate criteria
- [ ] Task 14: `sentinel-learning-reviewer` APPROVE
- [ ] Phase 2 specialist agents (`sentinel-distiller-expert`, `sentinel-retrieval-expert`, `sentinel-cli-rules-expert`) NOT YET CREATED (they wait on gate)

---

## Risks and Mitigations

| Risk                                                                          | Likelihood | Impact | Mitigation                                                                                                                                                |
| ----------------------------------------------------------------------------- | ---------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Runaway Karpathy loop — 3 retries × long TDD × model cost blows budget        | MEDIUM     | HIGH   | Hard cap N=3 (D1); guardrails second-layer at 10 tool-call repeats; Level-6 gate aborts if cost delta > +30%; feature flag off by default                 |
| Foundation we're building conflicts with `feat/interactive-cli` when it lands | MEDIUM     | MEDIUM | Migration naming `001_init`, `003_postmortems` leaves room for interactive-cli's `002_workers`; by commit order, whichever lands second resolves conflicts |
| PHPUnit JUnit XML not produced in old Drupal configs                          | LOW        | MEDIUM | Fall back to text parser + log warning (Task 6 gotcha); doesn't block retry loop                                                                          |
| Structured-error parser chokes on malformed verifier output                   | LOW        | MEDIUM | Every parser `try/except → return []` with warning; tests include malformed-input fixtures                                                                |
| Agent rewrites unrelated code during refine attempt despite policy            | MEDIUM     | MEDIUM | Refine-prompt explicitly says "single targeted fix, do not rewrite"; if it persists, that's a Phase 2 overlay improvement, not a Phase 1 blocker          |
| `superseded_by` column never used in Phase 1 → reviewer flags as waste        | LOW        | LOW    | Decision 4 (append-only) + design §6.2 require the column; reviewer spec §Phase-1-schema says shipping without it is a rejection. Leave in.               |
| MR comment formatting accidentally re-triggers Sentinel on its own comment    | LOW        | MEDIUM | Handover §4 Decision 9 (`reviewer_is_bot` filter) is Phase 2 work; Phase 1 cap-out comment is a notes post, not a discussion thread, so no self-loop      |
| User's "no whack-a-mole" rule is violated by surface-level retry without understanding | MEDIUM | HIGH | Refine-prompt wording deliberately asks for a "targeted fix"; postmortem's `fix_summary` field left NULL in Phase 1 (Phase 2 extraction job enforces root-cause) |
| Feature flag forgotten, Phase 1 code shipped on by default                    | LOW        | HIGH   | Default `DEV_VERIFIER_LOOP=0`; tests assert default-off preserves legacy behavior                                                                         |
| `run_tests` new shape break 3 call sites silently                             | LOW        | MEDIUM | Task 5 updates all 3 sites atomically; Level-4 full-suite test catches any missed caller                                                                  |

---

## Notes

### Why Phase 1 is bigger than the design doc suggests

The design report was written assuming the Command Center foundation (events, persistence, orchestrator) had already landed from `feat/interactive-cli`. On `feat/sentinel-learning-system` it has not. Per the user's direction, Phase 1 creates a **minimal subset** of that foundation rather than waiting for interactive-cli to merge. Trade-offs:
- **Pro**: learning-system branch is self-contained; can land independently.
- **Pro**: the minimal subset is scoped to exactly what Loop A needs — no TUI, no service HTTP surface, no supervisor/worker split.
- **Con**: some duplication with interactive-cli if both land. Mitigation: migrations are numbered with gaps (003 for postmortems leaves 002 for workers when interactive-cli merges).

### Why the shape change is "passed" not "success"

Design §5.1's Loop A pseudocode uses `result.ok`. We use `passed` in the return dict because:
- `success` is already the key in today's shape — renaming forces every caller to be updated (good — no silent back-compat bugs).
- `passed` reads better alongside `structured_errors`: `if passed: break` is unambiguous.
- `return_code` is preserved so any script that greps return codes still works.

### Why pydantic v2 for events (not dataclass)

- The design report references pydantic v2 (d75d276 commit message: "pydantic v2 event catalogue"). Matching that choice avoids Phase 2 migration churn if/when interactive-cli lands.
- Pydantic gives us JSON serialization for the `payload_json` column for free.
- Reviewer spec (integrator §Event-type-contract point 1) says "dataclass with `@dataclass(frozen=True)`" — this plan overrides that to pydantic; update the reviewer agent spec in the same PR if you agree, or flip the choice.

### Why we accept the `feat/interactive-cli` conflict risk

The user explicitly chose "Plan against current branch as-is" with the acknowledgment "Likely wrong — design doc explicitly references those files." That's an informed trade. Mitigation is in the migration numbering and in the minimal scope of our `core/` subset (~150–250 lines vs interactive-cli's 2685). If interactive-cli lands first, we can delete our `001_init.sql` + `db.py` + `bus.py` + `types.py` and rebase onto the richer versions; only the Phase 1 verifier-loop code in `src/agents/` is irreducibly ours.

### Partial-autonomy slider position

Per design §5.3, Phase 1 ships at Level 3 (guarded autonomous). No change. The cap-out → draft-MR + paused-comment flow is the guardrail.

### Telemetry before metrics before optimization

Do not tune the cap, the refine prompt, or the static-check selection before the Level-6 gate fires. "First tune after you have 20 data points" (design §9 discipline). The temptation to tweak mid-Phase-1 is the biggest process risk.
