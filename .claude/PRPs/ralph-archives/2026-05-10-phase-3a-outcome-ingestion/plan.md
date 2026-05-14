# Feature: Phase 3A — Outcome Ingestion (Pull Path)

## Summary

Phase 3A introduces the **ground-truth signal** for the learning system: did the work Sentinel produced actually land and stick? It pulls merge / revert / post-merge-CI facts from GitLab on demand, matches them back to prior `executions` rows via the deterministic branch-name convention `sentinel/feature/{TICKET_ID}`, tags those rows with one of `success | rolled_back | regressed`, and emits an `OutcomeRecorded` event per tag. State is durable: a per-project watermark (`project_sync_state`) is advanced after each sync so re-runs do not re-paginate. All sync paths are flag-gated (`OUTCOME_SYNC_ENABLED=0` by default until the exit-criterion fixture passes) and non-fatal in CLI hot paths. **Phase 3A produces the outcome rows; it does NOT consume them — Phase 3B does the reranking, Phase 3C the skill promotion. This plan must not bleed into either.**

PRD reference: `docs/agent-learning-from-feedback-2026-05-03.md` §8 Phase 3A (lines 482-498) + Appendix coverage. Decision references: `docs/agent-learning-from-feedback-DECISIONS.md` D6 (per-installation watermark).

## User Story

As a Sentinel maintainer
I want every prior `execution_id` whose MR has now merged, been reverted, or regressed `main` CI to be tagged with the matching outcome
So that the postmortem reranker (Phase 3B) and skill promoter (Phase 3C) have a grounded `success | rolled_back | regressed` signal to weight by — instead of trusting raw observation counts that conflate "happened a lot" with "actually fixed the bug."

## Problem Statement

Sentinel today runs an execution, opens a draft MR, and stops. Whatever happens after merge — including a same-day revert or a post-merge `main` CI failure — never returns to the system. Concretely (verifiable):

- `src/core/persistence/migrations/001_init.sql:9-19` — `executions` table has no `outcome` column. The only post-execution field is `status`, set to `running`/`completed` at the start of the run.
- `grep -r "OutcomeRecorded" src/` returns zero matches. No event class for outcomes.
- `grep -r "project_sync_state" src/` returns zero matches. No watermark table.
- `grep -r "outcomes" src/cli.py` returns zero matches. No `sentinel outcomes` CLI surface.
- `src/gitlab_client.py:1-510` — `get_merge_request`, `list_merge_requests`, `add_merge_request_comment`, `get_merge_request_discussions` exist; `list_merged_mrs_since` and `list_pipelines_for_commit` do **not**.
- No code path inserts MR IID, commit SHA, or revert ref onto an `executions` row. The branch name is the only durable link from execution to GitLab artifact (worktree_manager.py:10-23 — `BRANCH_PREFIX = "sentinel/feature"`, `get_branch_name(ticket_id)` returns `f"{BRANCH_PREFIX}/{ticket_id}"`).

Result: Phase 2C's extractor and Appendix C.6's confidence curve are blind to outcome. The reranker proposed in Phase 3B has nothing to weight by; the skill promoter in Phase 3C cannot tell a durable fix from a flaky one.

## Solution Statement

A pull-on-demand outcome sync, additive across five surfaces. Nothing in the existing hot path is rewritten:

1. **Schema (migration `005_outcome_ingestion.sql`).** Add nullable `executions.outcome` column with `CHECK(outcome IN ('success','rolled_back','regressed'))`, plus `executions.outcome_evidence_json` and `executions.outcome_recorded_at` for auditability. Create `project_sync_state(project TEXT PRIMARY KEY, last_synced_at TEXT, last_seen_mr_iid INTEGER, last_seen_updated_at TEXT)` per D6.

2. **GitLabClient extensions (`src/gitlab_client.py`).** Three additive methods that mirror the existing `requests.Session` style — pagination via `?page=`, URL encoding via `replace("/", "%2F")`, errors via `raise_for_status()`, no decorators:
   - `list_merged_mrs_since(project_id, *, updated_after, per_page=100) -> Iterator[Dict]` — paginates `GET /projects/:id/merge_requests?state=merged&order_by=updated_at&sort=asc&updated_after=...`.
   - `list_pipelines_for_commit(project_id, *, sha, ref="main") -> List[Dict]` — `GET /projects/:id/pipelines?sha=...&ref=main`.
   - `get_merge_request(project_id, mr_iid)` already exists at line 157; reuse unchanged.

3. **`OutcomeRecorded` event (`src/core/events/types.py`).** Pydantic v2 `BaseEvent` subclass with `Literal["OutcomeRecorded"]` discriminator, mirroring `FeedbackRulePromoted` shape. Re-export from `src/core/events/__init__.py`. **Event is informational; the outcome write itself happens via SQL UPDATE in the sync service, not via the bus** (mirrors how postmortems are inserted then `PostmortemRecorded` is published as a notification).

4. **OutcomeSyncService (`src/core/learning/outcome_sync.py`).** New module. Pure functions plus a small `OutcomeSyncService` class taking `(conn, gitlab, event_bus=None)`. Public surface:
   - `sync(*, project=None, since=None, full_backfill=False, dry_run=False) -> OutcomeSyncSummary`
   - For each project (one explicit, all known, or by config): walks merged MRs since watermark, classifies each one, UPDATEs matching `executions` rows, advances watermark, publishes `OutcomeRecorded`.
   - **Matching key:** `mr['source_branch']` matches `^sentinel/feature/(?P<ticket_id>.+)$` → SELECT FROM executions WHERE ticket_id = ? AND outcome IS NULL → tag every match. (PRD line 487 says "tag prior `execution_id`s" — plural.)
   - **Classification order (most-severe wins):** `regressed` > `rolled_back` > `success`. Evidence stored in `executions.outcome_evidence_json` so the audit trail survives even if upstream GitLab data later changes.

5. **CLI (`src/cli.py`).** New `@cli.group() outcomes` group with one subcommand `outcomes sync` accepting `--project`, `--since`, `--all`, `--dry-run`. Mirrors `learning extract` (cli.py:1701-1758) for option naming and dry-run conventions. **Pre-flight hook** at the top of `plan` (cli.py:211) and `execute` (cli.py:600) calls the sync once per invocation, gated on `OUTCOME_SYNC_ENABLED=1`, and swallows exceptions (sync failure must never block plan/execute).

6. **Tests.** Unit tests for the GitLabClient methods (mock `session.get`), the classifier (table-driven), and the sync service (mock `GitLabClient`, real in-memory SQLite via `sqlite_mem_conn` fixture). One integration test for the exit-criterion fixture: a project with one merged MR, one reverted MR, one post-merge-pipeline-failure → correct tags + watermark advance + idempotent re-run.

## Metadata

| Field            | Value                                                                                          |
| ---------------- | ---------------------------------------------------------------------------------------------- |
| Type             | NEW_CAPABILITY                                                                                 |
| Complexity       | MEDIUM                                                                                          |
| Systems Affected | `src/gitlab_client.py`, `src/cli.py`, `src/core/events/`, `src/core/learning/`, `src/core/persistence/migrations/`, tests |
| Dependencies     | requests ^2.31.0 (already present), pydantic ^2.5.0 (already present), click ^8.1.7 (already present); **no new dependencies** |
| Estimated Tasks  | 11                                                                                              |
| Hard order       | 3A is upstream of 3B and 3C. Within 3A, migration → events → gitlab methods → service → CLI → hook → tests. |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════════════╗
║                           BEFORE — outcomes are black-boxed                           ║
╠═══════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                       ║
║   ┌────────────────┐    ┌───────────────────┐    ┌────────────────────────────┐       ║
║   │ sentinel plan  │───►│ executions row    │───►│ Draft MR opened on GitLab  │       ║
║   │ + execute      │    │ (status=running)  │    │ branch sentinel/feature/X  │       ║
║   └────────────────┘    └───────────────────┘    └────────────────────────────┘       ║
║                                                          │                            ║
║                                  (gap)                   ▼                            ║
║                                          ┌────────────────────────────────┐           ║
║                                          │ Human merges / reverts / sees  │           ║
║                                          │ pipeline fail on main          │           ║
║                                          └────────────────────────────────┘           ║
║                                                                                       ║
║   USER_FLOW: maintainer manually checks GitLab; Sentinel never knows the outcome.     ║
║   PAIN_POINT: Phase 3B/3C cannot weight memory by ground truth.                       ║
║   DATA_FLOW: executions(.outcome) is NULL forever.                                    ║
║                                                                                       ║
╚═══════════════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════════════╗
║                            AFTER — outcomes pulled on demand                          ║
╠═══════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                       ║
║   ┌────────────────────┐                                                              ║
║   │ sentinel plan      │ pre-flight (OUTCOME_SYNC_ENABLED=1)                          ║
║   │ sentinel execute   │──┐                                                           ║
║   │ sentinel outcomes  │  │                                                           ║
║   │ sync               │  │                                                           ║
║   └────────────────────┘  │                                                           ║
║                           ▼                                                           ║
║              ┌──────────────────────────────┐                                         ║
║              │ OutcomeSyncService.sync()    │                                         ║
║              │  1. read project_sync_state  │                                         ║
║              │  2. GET /merge_requests      │  paginated, updated_after=watermark     ║
║              │     ?state=merged&order_by   │                                         ║
║              │     =updated_at&sort=asc     │                                         ║
║              │  3. classify each MR:        │                                         ║
║              │     regressed > rolled_back  │                                         ║
║              │     > success                │                                         ║
║              │  4. UPDATE executions SET    │                                         ║
║              │     outcome=?,evidence_json  │                                         ║
║              │     WHERE ticket_id=? AND    │                                         ║
║              │     outcome IS NULL          │                                         ║
║              │  5. publish OutcomeRecorded  │                                         ║
║              │  6. UPDATE project_sync_state│                                         ║
║              └──────────────────────────────┘                                         ║
║                           │                                                           ║
║                           ▼                                                           ║
║              ┌──────────────────────────────┐                                         ║
║              │ executions.outcome = success │ ← Phase 3B reads this                   ║
║              │                  | rolled_back| ← Phase 3C reads this                 ║
║              │                  | regressed  │                                        ║
║              └──────────────────────────────┘                                         ║
║                                                                                       ║
║   USER_FLOW: maintainer's daily run (or explicit `outcomes sync`) tags executions.    ║
║   VALUE_ADD: ground truth lands in the DB; Phase 3B/3C unblocked.                     ║
║   DATA_FLOW: GitLab API → OutcomeSyncService → executions.outcome + OutcomeRecorded.  ║
║                                                                                       ║
╚═══════════════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location | Before | After | User Impact |
|---|---|---|---|
| `sentinel plan` start | runs immediately | pre-flight outcome sync (silent unless verbose) | adds ~0–5s on first run per project; cheap thereafter (watermark) |
| `sentinel execute` start | runs immediately | same pre-flight hook | same |
| `sentinel outcomes sync` | (does not exist) | new top-level subcommand | explicit backfill / debugging surface |
| `executions` table | `outcome` column absent | `outcome` ∈ {NULL, 'success', 'rolled_back', 'regressed'} | downstream readers can join on outcome |
| Event log | no `OutcomeRecorded` rows | one event per tagged execution | traceable from `events` table by `type='OutcomeRecorded'` |

---

## Mandatory Reading

**The implementation agent MUST read these files before writing any code:**

| Priority | File | Lines | Why Read This |
|---|---|---|---|
| P0 | `src/gitlab_client.py` | 1-260 | Mirror `__init__` (40-59), `list_merge_requests` (209-239), `get_merge_request` (157-177) verbatim. URL encoding `replace("/", "%2F")`. Errors via `raise_for_status()`. |
| P0 | `src/core/persistence/migrations/004_feedback_rules.sql` | 1-57 | Migration header style + table+index pattern. Use `IF NOT EXISTS`, `CHECK` for enums, partial unique indexes. |
| P0 | `src/core/persistence/migrations/001_init.sql` | 9-19 | Existing `executions` schema — we ALTER it. |
| P0 | `src/core/persistence/db.py` | 75-161 | Migrations runner. Migration version = file stem; numeric leading-digit sort; per-statement execute, never `executescript()`; explicit `BEGIN IMMEDIATE`/`COMMIT`. |
| P0 | `src/core/events/types.py` | 1-140 | Pydantic v2 `BaseEvent` shape; `Literal["X"]` discriminator pattern. **Do NOT use `Field(default_factory=...)` for `ts`** — types.py:10-13 explains why. |
| P0 | `src/core/events/bus.py` | 35-103 | Persist-then-publish; `seq` is per-execution monotonic; subscriber exceptions are swallowed. The OutcomeRecorded event needs an `execution_id` that exists in `executions`. |
| P0 | `src/core/events/__init__.py` | 1-34 | Add `OutcomeRecorded` to imports + `__all__`. |
| P1 | `src/core/persistence/postmortems.py` | 1-95 | Helper-function pattern: keyword-only after `conn`; commit inside helper; parameterized queries. |
| P1 | `src/core/persistence/__init__.py` | 1-46 | Re-export new helpers from `outcome_sync_state.py` (project_sync_state read/upsert). |
| P1 | `src/cli.py` | 44-83 | Feature flag pattern (`os.getenv(NAME, "0") == "1"`, read at call time). |
| P1 | `src/cli.py` | 158-263 | `plan` entrypoint — exact line for pre-flight hook is **after** `click.echo(f"📋 Planning ticket: {ticket_id}")` at line 211. |
| P1 | `src/cli.py` | 571-630 | `execute` entrypoint — pre-flight hook goes after worktree check at line 600. |
| P1 | `src/cli.py` | 1649-1758 | `learning` group (1649-1652) and `learning extract` subcommand (1701-1758) — mirror exactly for `outcomes` group. |
| P1 | `src/cli.py` | 1655-1675 | `_learning_seed_synthetic_execution` — model for the synthetic execution row that `outcomes sync` events FK to. |
| P1 | `src/worktree_manager.py` | 10-23 | `BRANCH_PREFIX = "sentinel/feature"`. Matching regex anchor: `^sentinel/feature/(?P<ticket_id>.+)$`. |
| P1 | `tests/conftest.py` | 99-142 | `sqlite_mem_conn` (lines 99-132) and `event_bus` (139-142) fixtures. Use these — do NOT roll your own. |
| P1 | `tests/test_gitlab_client.py` | 1-100 | Mock pattern: `MagicMock(spec=ConfigLoader)` + `patch("src.gitlab_client.get_config", ...)` + `patch.object(client.session, "get", return_value=mock_response)`. |
| P2 | `.claude/PRPs/plans/completed/phase-2c-promotion-path.plan.md` | all | Style reference for sentinel-internal plans. |
| P2 | `docs/agent-learning-from-feedback-DECISIONS.md` | 105-122 | D6 — watermark is per Sentinel installation. No `installation_id` column. |
| P2 | `docs/agent-learning-from-feedback-2026-05-03.md` | 482-498 | Phase 3A scope. Quote it; do not extend it. |

**External Documentation:**

| Source | Section | Why Needed |
|---|---|---|
| [GitLab REST API — list project MRs](https://docs.gitlab.com/api/merge_requests.html#list-project-merge-requests) | `state`, `updated_after`, `order_by`, `sort`, `per_page`, `page` query params | All four are needed for incremental pull. `updated_after` is ISO-8601; `order_by=updated_at`+`sort=asc` is essential so we never skip an MR whose `updated_at` is between two pages. |
| [GitLab REST API — get single MR](https://docs.gitlab.com/api/merge_requests.html#get-single-mr) | `merge_commit_sha`, `state`, `merged_at` | `merge_commit_sha` is the join key for pipelines on `main`. |
| [GitLab REST API — list project pipelines](https://docs.gitlab.com/api/pipelines.html#list-project-pipelines) | `sha`, `ref`, `status` | `sha` filter is what we need for "did the pipeline on main for this merge commit pass?" |
| [GitLab REST API — pagination](https://docs.gitlab.com/api/rest/index.html#offset-based-pagination) | `X-Total-Pages` header / `?page=` walk | Existing `list_merge_requests` does **not** paginate. We must paginate the new method to satisfy the "re-run does not re-paginate" exit criterion. |
| [PEP 405 / pyproject.toml deps](https://peps.python.org/pep-0405/) | n/a | Confirm: NO new dependencies. Sticking to `requests` per `pyproject.toml:9-18` (no `python-gitlab`). |

---

## Patterns to Mirror

**GITLABCLIENT_METHOD** (verbatim from `src/gitlab_client.py:209-239`):
```python
def list_merge_requests(
    self,
    project_id: str,
    state: str = "opened",
    source_branch: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List merge requests for a project."""
    project_path = project_id.replace("/", "%2F")
    url = f"{self.base_url}/api/v4/projects/{project_path}/merge_requests"

    params = {"state": state}
    if source_branch:
        params["source_branch"] = source_branch

    response = self.session.get(url, params=params)
    response.raise_for_status()

    result: List[Dict[str, Any]] = response.json()
    return result
```
COPY THIS PATTERN for the new methods. Add pagination as a `while page <= total_pages` loop using the `X-Total-Pages` response header (or fall back to "page returns < per_page rows → stop").

**EVENT_CLASS** (verbatim from `src/core/events/types.py:116-127`):
```python
class FeedbackRulePromoted(BaseEvent):
    """Emitted by `sentinel learning propose` per rule once a draft MR lands.

    `mr_url` MUST be a real URL — dry-run paths do NOT publish this event.
    `execution_id` is the synthetic `learning-propose-<UTC ISO>` row.
    """

    type: Literal["FeedbackRulePromoted"] = "FeedbackRulePromoted"
    rule_id: int
    scope: str
    mr_url: str
    branch_name: str
```
COPY THIS PATTERN. `OutcomeRecorded` adds: `mr_iid: int`, `project: str`, `outcome: Literal["success","rolled_back","regressed"]`, `merged_at: Optional[str]`, `evidence_summary: str`.

**MIGRATION_HEADER** (verbatim from `src/core/persistence/migrations/004_feedback_rules.sql:1-23`):
```sql
-- 004_feedback_rules.sql
-- Phase 2C schema per design §8 task 10 + Appendix C.3 (subset).
-- Plan: .claude/PRPs/plans/phase-2c-promotion-path.plan.md task 2.
--
-- Phase 2C ships ONLY the columns the extractor + proposer + revoker actually
-- use. The richer Appendix C.3 schema (...) is deferred — when that lands, this
-- migration stays untouched and a 005 widens the surface.
--
-- Append-only:
--   * No DELETE anywhere.
--   ...
```
COPY THIS PATTERN — header explains scope, defers richer-schema temptations, declares append-only invariants. **Migration filename is `005_outcome_ingestion.sql` because 004 is taken** (PRD says "004_project_sync_state.sql" but that slot is occupied by `004_feedback_rules.sql`; `db.py` numerically sorts by leading digits so `005` is the next slot).

**PERSISTENCE_HELPER** (verbatim from `src/core/persistence/postmortems.py:26-74`):
```python
def insert_postmortem(
    conn: sqlite3.Connection,
    *,
    execution_id: str,
    stack_type: str,
    agent: str,
    failure_signature: str,
    context_excerpt: Optional[str] = None,
    fix_summary: Optional[str] = None,
    provenance: str = "auto",
    confidence: int = 50,
) -> int:
    if provenance is None or provenance not in _VALID_PROVENANCE:
        raise ValueError(...)
    created_at = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """INSERT INTO postmortems (...) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (execution_id, stack_type, agent, failure_signature, ...),
    )
    conn.commit()
    rowid = cursor.lastrowid
    if rowid is None:
        raise RuntimeError("INSERT did not return a lastrowid")
    return rowid
```
COPY THIS PATTERN for `read_sync_state(conn, project) -> Optional[Row]` and `upsert_sync_state(conn, *, project, last_synced_at, last_seen_mr_iid, last_seen_updated_at) -> None`.

**CLI_GROUP** (verbatim from `src/cli.py:1649-1758`):
```python
@cli.group()
def learning() -> None:
    """Phase 2C: extract → propose → mark-merged / revoke pipeline."""
    pass

# ... helpers ...

@learning.command("extract")
@click.option("--days", type=click.IntRange(1, 365), default=30, ...)
@click.option("--min-observations", type=click.IntRange(2, 50), default=3, ...)
@click.option("--dry-run", is_flag=True, default=False, ...)
def learning_extract(days: int, ..., dry_run: bool) -> None:
    """Cluster recent postmortems and UPSERT feedback_rules at probation."""
    if not _extraction_enabled() and not dry_run:
        click.echo("EXTRACTION_ENABLED=0 — pass --dry-run...", err=True)
        sys.exit(2)
    try:
        conn = connect()
        try:
            apply_migrations(conn)
            from src.core.events import EventBus  # noqa: PLC0415
            from src.core.learning.extract import extract_clusters  # noqa: PLC0415
            ...
```
COPY THIS PATTERN. Heavy imports (event bus, learning modules, gitlab client) are deferred into the function body so `import src.cli` stays cheap for `--help`. Flag check + dry-run override + `sys.exit(2)` on disabled-without-dry-run.

**TEST_FIXTURE_USAGE** (verbatim from `tests/conftest.py:99-142`):
```python
@pytest.fixture
def sqlite_mem_conn() -> Iterator[sqlite3.Connection]:
    conn = connect(":memory:")
    apply_migrations(conn)
    conn.execute(
        "INSERT INTO executions (id, ticket_id, kind, status, created_at) VALUES (?, ?, ?, ?, ?)",
        ("test-exec-1", "TEST-1", "execute", "running",
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()

@pytest.fixture
def event_bus(sqlite_mem_conn: sqlite3.Connection) -> EventBus:
    return EventBus(sqlite_mem_conn)
```
USE THIS FIXTURE in new tests. The single seeded execution `test-exec-1` with `ticket_id='TEST-1'` is enough; tests that need multiple executions per ticket should INSERT additional rows in the test body, not in a new fixture.

**GITLAB_MOCK** (verbatim from `tests/test_gitlab_client.py:70-99`):
```python
def test_create_merge_request_success(self, gitlab_client):
    mock_response = Mock()
    mock_response.json.return_value = {"iid": 123, "web_url": "...", ...}
    with patch.object(
        gitlab_client.session, "post", return_value=mock_response
    ) as mock_post:
        result = gitlab_client.create_merge_request(...)
        assert result["iid"] == 123
        # Verify URL encoding
        call_args = mock_post.call_args
        assert "acme%2Fbackend" in call_args[0][0]
```
COPY THIS PATTERN. Mock per-method via `patch.object(client.session, "get", ...)`. Assert URL encoding by inspecting `call_args`.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `src/core/persistence/migrations/005_outcome_ingestion.sql` | CREATE | Adds `executions.outcome` (+ `outcome_evidence_json`, `outcome_recorded_at`) and `project_sync_state` table. Single migration so the two changes land atomically. |
| `src/core/persistence/sync_state.py` | CREATE | Read/upsert helpers for `project_sync_state`, mirrored on `postmortems.py`. |
| `src/core/persistence/__init__.py` | UPDATE | Export `read_sync_state`, `upsert_sync_state`, plus `update_execution_outcome` (helper used by sync service to UPDATE `executions`). |
| `src/core/events/types.py` | UPDATE | Append `OutcomeRecorded` BaseEvent subclass. |
| `src/core/events/__init__.py` | UPDATE | Add `OutcomeRecorded` to imports + `__all__`. |
| `src/gitlab_client.py` | UPDATE | Add `list_merged_mrs_since`, `list_pipelines_for_commit` methods (additive, no existing method changed). |
| `src/core/learning/outcome_sync.py` | CREATE | `OutcomeSyncService` + `classify_outcome()` pure function + `OutcomeSyncSummary` dataclass. |
| `src/core/learning/__init__.py` | UPDATE | Re-export `OutcomeSyncService` + `classify_outcome`. |
| `src/cli.py` | UPDATE | Add `_outcome_sync_enabled()` flag helper; `@cli.group() outcomes`; `outcomes sync` subcommand; pre-flight hook in `plan` (line 211) and `execute` (line 600). |
| `tests/test_gitlab_client.py` | UPDATE | Add tests for the two new methods (mock pagination, URL encoding, error path). |
| `tests/core/test_outcome_sync.py` | CREATE | Unit tests: classifier table, sync service end-to-end (mocked GitLab, real in-memory SQLite), watermark advancement, idempotent re-run, dry-run leaves nothing written. |
| `tests/integration/test_phase3a_outcomes.py` | CREATE | Exit-criterion fixture: project with merged MR + reverted MR + pipeline-failed merge → tags + watermark + idempotency. |
| `tests/test_cli_outcomes.py` | CREATE | CLI surface tests: `outcomes sync --dry-run` exits 0 with flag off; `outcomes sync` honors `--project`/`--since`/`--all`; pre-flight hook in `plan` is a no-op when flag off. |

---

## NOT Building (Scope Limits)

Phase 3A bleeds into 3B and 3C very easily. Reject all of the following in code review:

- **Confidence reranking** — that's Phase 3B (task 16). This plan adds the column `executions.outcome`; it does not bump or decay any postmortem confidence in response. No call to `recompute_confidence_for_rule`. No subscriber on `OutcomeRecorded` in this phase.
- **Skill promotion / `propose_skills.py`** — that's Phase 3C (task 17). No `commands/<agent>/<slug>.yaml` writer.
- **Webhooks / push-style ingestion** — PRD line 590: "All outcome ingestion is **pull-on-demand** ... webhooks are not viable." No HTTP listener, no GitLab webhook config, no inbound network surface.
- **`python-gitlab` dependency** — PRD line 490: "mirror existing `requests.Session` style — `python-gitlab` is **not** used today." Confirm via `pyproject.toml:9-18`.
- **Storing MR IID on `executions`** — branch-name matching (`sentinel/feature/{TICKET_ID}`) is the agreed key. Do NOT add an `mr_iid` column on executions in this migration; that's a future widening if branch-name matching proves insufficient. Documented and tested.
- **`installation_id` column on `project_sync_state`** — D6: "Schema stays as specified in the design: no `installation_id` column. The DB file itself identifies the installation." Reject any review comment asking for one.
- **Cross-installation deduplication** — D6 explicitly accepts duplicated GitLab API traffic across installations as a non-goal. Do not add it.
- **Auto-revert detection beyond title prefix `Revert "..."` and `revert_commit` field** — heuristic enough for Phase 3A; richer detection is future work.
- **Modifying `_detect_plan_state` or any plan/execute control flow beyond the two-line pre-flight call.** This is an additive seam, not a refactor.
- **Updating CLAUDE.md / docs/ / handover** — docs are out of scope unless a Phase 3A decision lands that contradicts an ADR (in which case open a separate decision PR).
- **Task 18 (Letta / Mem0)** — explicitly gated, not in any sub-phase (PRD line 533).

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and validates green before the next starts.

### Task 1: CREATE `src/core/persistence/migrations/005_outcome_ingestion.sql`

- **ACTION**: New SQLite migration that ALTERs `executions` (3 new columns) and CREATEs `project_sync_state`.
- **IMPLEMENT**:
  ```sql
  -- 005_outcome_ingestion.sql
  -- Phase 3A schema per design §8 task 14 + DECISIONS.md D6.
  -- Plan: .claude/PRPs/plans/phase-3a-outcome-ingestion.plan.md task 1.
  --
  -- Phase 3A ships ONLY:
  --   * executions.outcome (+ evidence_json, recorded_at) for ground-truth tagging
  --   * project_sync_state table — per-installation watermark per D6
  -- Reranker math (3B) and skill promotion (3C) are deferred.
  --
  -- Append-only invariants:
  --   * executions.outcome is set once (UPDATE only when previously NULL).
  --     Helper enforces. SQLite has no per-column update trigger, so the
  --     append-once guarantee lives in src/core/persistence/sync_state.py.
  --   * project_sync_state is upserted; rows are never deleted.

  ALTER TABLE executions ADD COLUMN outcome TEXT
      CHECK (outcome IS NULL OR outcome IN ('success','rolled_back','regressed'));
  ALTER TABLE executions ADD COLUMN outcome_evidence_json TEXT;
  ALTER TABLE executions ADD COLUMN outcome_recorded_at TEXT;

  CREATE INDEX IF NOT EXISTS idx_executions_outcome_lookup
      ON executions(ticket_id, outcome);

  CREATE TABLE IF NOT EXISTS project_sync_state (
      project              TEXT PRIMARY KEY,
      last_synced_at       TEXT NOT NULL,
      last_seen_mr_iid     INTEGER,
      last_seen_updated_at TEXT
  );
  ```
- **MIRROR**: `src/core/persistence/migrations/004_feedback_rules.sql:1-57` for header style.
- **GOTCHA**:
  - `db.py:104-122` splits on `;` after stripping `-- ` line comments. Do NOT introduce block comments (`/* */`). Do NOT put `;` inside string literals (none needed here).
  - SQLite `ALTER TABLE ADD COLUMN` cannot add NOT NULL without DEFAULT; we want NULL-able anyway (untagged executions). Do not add DEFAULT.
  - `CHECK (outcome IS NULL OR ...)` — must allow NULL explicitly because SQLite's CHECK on `IN (...)` treats NULL as unknown but the migration tests should verify NULL inserts succeed.
- **VALIDATE**:
  - `cd /workspace/sentinel && poetry run python -c "from src.core.persistence import connect, apply_migrations; c = connect(':memory:'); apply_migrations(c); print(list(c.execute('PRAGMA table_info(executions)')))"` — confirms `outcome` column exists.
  - `pytest tests/core/test_db_migrations.py -q` if it exists, else add an inline migration test in Task 11.

### Task 2: CREATE `src/core/persistence/sync_state.py`

- **ACTION**: Helpers for the new table + the `executions.outcome` UPDATE.
- **IMPLEMENT** (signature only — body mirrors `postmortems.py`):
  ```python
  """Phase 3A persistence helpers — project_sync_state + executions.outcome.

  Append-once on executions.outcome: a row's outcome can be set from NULL to
  one of {success, rolled_back, regressed}, but never overwritten — once
  ground truth lands, it does not change without explicit human intervention.
  Enforced via WHERE outcome IS NULL in the UPDATE.
  """

  _VALID_OUTCOMES = frozenset({"success", "rolled_back", "regressed"})

  def read_sync_state(conn, project: str) -> Optional[sqlite3.Row]: ...

  def upsert_sync_state(
      conn,
      *,
      project: str,
      last_synced_at: str,
      last_seen_mr_iid: Optional[int],
      last_seen_updated_at: Optional[str],
  ) -> None:
      # Use INSERT ... ON CONFLICT(project) DO UPDATE — SQLite >= 3.24
      # available in Python 3.11.

  def update_execution_outcome(
      conn,
      *,
      execution_id: str,
      outcome: str,           # one of _VALID_OUTCOMES
      evidence_json: str,     # JSON string written verbatim
      recorded_at: str,       # ISO-8601 UTC
  ) -> int:
      # Returns 1 if the row was tagged, 0 if outcome was already set
      # (append-once: WHERE outcome IS NULL).

  def list_executions_for_ticket_untagged(
      conn,
      ticket_id: str,
  ) -> list[sqlite3.Row]:
      # SELECT id FROM executions
      # WHERE ticket_id = ? AND outcome IS NULL ORDER BY created_at
  ```
- **MIRROR**: `src/core/persistence/postmortems.py:26-74` for `keyword-only after conn`, parameterized SQL, `conn.commit()` inside helper, `ValueError` on bad enum.
- **GOTCHA**:
  - Append-once is enforced by the UPDATE's WHERE clause, NOT by a CHECK constraint. Tests must assert that a second UPDATE returns 0 affected rows and leaves the original outcome intact.
  - `INSERT ... ON CONFLICT(project) DO UPDATE` requires SQLite 3.24+; Python 3.11 ships 3.40+ on Linux (verify via `sqlite3.sqlite_version` if in doubt).
- **VALIDATE**: `poetry run pytest tests/core/test_sync_state.py -q` (test file added in Task 11).

### Task 3: UPDATE `src/core/persistence/__init__.py`

- **ACTION**: Re-export the four new helpers.
- **IMPLEMENT**: Add to imports + `__all__`:
  ```python
  from src.core.persistence.sync_state import (
      list_executions_for_ticket_untagged,
      read_sync_state,
      update_execution_outcome,
      upsert_sync_state,
  )
  ```
- **MIRROR**: existing alphabetized re-export pattern in `__init__.py:1-46`.
- **VALIDATE**: `poetry run python -c "from src.core.persistence import upsert_sync_state, update_execution_outcome; print('ok')"`.

### Task 4: UPDATE `src/core/events/types.py`

- **ACTION**: Append `OutcomeRecorded` Pydantic v2 class at end of file.
- **IMPLEMENT**:
  ```python
  class OutcomeRecorded(BaseEvent):
      """Emitted by ``OutcomeSyncService`` when an execution is tagged.

      One event per (execution_id, outcome) tag. The ``execution_id`` is the
      real run; for offline ``sentinel outcomes sync`` the bus also has a
      synthetic ``outcomes-sync-<UTC ISO>`` execution row created by the CLI
      so non-tagged-execution events (errors, summaries) have an FK target.
      """

      type: Literal["OutcomeRecorded"] = "OutcomeRecorded"
      mr_iid: int
      project: str
      outcome: Literal["success", "rolled_back", "regressed"]
      merged_at: Optional[str] = None
      reverted_by_mr_iid: Optional[int] = None
      regressed_pipeline_id: Optional[int] = None
      evidence_summary: str
  ```
- **MIRROR**: `FeedbackRulePromoted` at types.py:116-127 — Literal discriminator, optional fields, docstring covers `execution_id` semantics.
- **GOTCHA**: Add `from typing import Optional` if not already imported (currently only `Literal` is imported on line 24). Keep `from __future__ import annotations` so Optional[...] resolves under Pydantic v2 with deferred evaluation.
- **VALIDATE**: `poetry run python -c "from src.core.events import OutcomeRecorded; print(OutcomeRecorded.model_fields.keys())"`.

### Task 5: UPDATE `src/core/events/__init__.py`

- **ACTION**: Re-export `OutcomeRecorded`.
- **IMPLEMENT**: Add `OutcomeRecorded` to the alphabetized `from src.core.events.types import (...)` block AND to `__all__`.
- **MIRROR**: existing alphabetized re-export pattern in `__init__.py:9-34`.
- **VALIDATE**: `poetry run python -c "from src.core.events import OutcomeRecorded, EventBus; print('ok')"`.

### Task 6: UPDATE `src/gitlab_client.py` — `list_merged_mrs_since`

- **ACTION**: Add additive method on `GitLabClient`.
- **IMPLEMENT**:
  ```python
  def list_merged_mrs_since(
      self,
      project_id: str,
      *,
      updated_after: str,
      per_page: int = 100,
  ) -> List[Dict[str, Any]]:
      """List merged MRs updated at or after ``updated_after`` (ISO-8601 UTC).

      Uses ``order_by=updated_at, sort=asc`` so callers can advance a watermark
      to the LAST returned MR's ``updated_at`` without losing rows that share
      the same timestamp on the page boundary.

      Pagination: walks `?page=N` until ``X-Total-Pages`` is exhausted (or, if
      that header is missing, until a page returns fewer than ``per_page`` rows).
      """
      project_path = project_id.replace("/", "%2F")
      url = f"{self.base_url}/api/v4/projects/{project_path}/merge_requests"
      results: List[Dict[str, Any]] = []
      page = 1
      while True:
          params = {
              "state": "merged",
              "order_by": "updated_at",
              "sort": "asc",
              "updated_after": updated_after,
              "per_page": per_page,
              "page": page,
          }
          response = self.session.get(url, params=params)
          response.raise_for_status()
          batch: List[Dict[str, Any]] = response.json()
          results.extend(batch)
          total_pages_hdr = response.headers.get("X-Total-Pages")
          if total_pages_hdr is not None:
              if page >= int(total_pages_hdr):
                  break
          elif len(batch) < per_page:
              break
          page += 1
      return results
  ```
- **MIRROR**: `list_merge_requests` at gitlab_client.py:209-239 (URL encoding, session.get, raise_for_status, return type annotation).
- **GOTCHA**:
  - GitLab paginates from `page=1`, not `page=0`.
  - GitLab's `updated_after` is **inclusive**; the watermark store therefore must be the strict-greatest `updated_at` seen, and the caller passes that value back as `updated_after` on the next sync. We accept the small possibility of seeing the boundary MR twice on consecutive runs and dedupe via the append-once UPDATE.
  - `X-Total-Pages` is omitted on installations with `>10000` results unless `pagination=keyset` is used; the fallback (`len(batch) < per_page`) covers this.
- **VALIDATE**: `poetry run pytest tests/test_gitlab_client.py::TestListMergedMrsSince -q`.

### Task 7: UPDATE `src/gitlab_client.py` — `list_pipelines_for_commit`

- **ACTION**: Add second additive method.
- **IMPLEMENT**:
  ```python
  def list_pipelines_for_commit(
      self,
      project_id: str,
      *,
      sha: str,
      ref: str = "main",
  ) -> List[Dict[str, Any]]:
      """List pipelines on ``ref`` for a given commit SHA.

      Used by the regression detector: a merged MR is `regressed` if any
      pipeline for the merge commit on the target ref has status='failed'.
      """
      project_path = project_id.replace("/", "%2F")
      url = f"{self.base_url}/api/v4/projects/{project_path}/pipelines"
      params = {"sha": sha, "ref": ref}
      response = self.session.get(url, params=params)
      response.raise_for_status()
      result: List[Dict[str, Any]] = response.json()
      return result
  ```
- **MIRROR**: same as Task 6.
- **GOTCHA**: A merge commit on `main` may have multiple pipelines (e.g. retried). Treat the **most recent** non-running pipeline as ground truth in the classifier.
- **VALIDATE**: `poetry run pytest tests/test_gitlab_client.py::TestListPipelinesForCommit -q`.

### Task 8: CREATE `src/core/learning/outcome_sync.py`

- **ACTION**: New module containing pure classifier + service class + dataclass summary.
- **IMPLEMENT** (skeleton):
  ```python
  """Phase 3A outcome ingestion service.

  Pulls merge / revert / post-merge-CI facts from GitLab and tags prior
  ``executions`` rows with one of {success, rolled_back, regressed}.
  Feature-gated (OUTCOME_SYNC_ENABLED) at the CLI; the service itself runs
  unconditionally so it is testable in isolation.

  Matching key: ``mr['source_branch']`` matches ``^sentinel/feature/(?P<ticket_id>.+)$``
  → SELECT FROM executions WHERE ticket_id = ? AND outcome IS NULL → tag.
  """

  from __future__ import annotations

  import json
  import logging
  import re
  import sqlite3
  from dataclasses import dataclass, field
  from datetime import datetime, timezone
  from typing import Optional

  from src.core.events import EventBus, OutcomeRecorded
  from src.core.persistence import (
      list_executions_for_ticket_untagged,
      read_sync_state,
      update_execution_outcome,
      upsert_sync_state,
  )
  from src.gitlab_client import GitLabClient

  logger = logging.getLogger(__name__)

  _BRANCH_RE = re.compile(r"^sentinel/feature/(?P<ticket_id>.+)$")
  _DEFAULT_LOOKBACK = "1970-01-01T00:00:00+00:00"  # for --all backfill

  Outcome = str  # one of "success", "rolled_back", "regressed"

  @dataclass
  class OutcomeSyncSummary:
      project: str
      mrs_seen: int = 0
      executions_tagged: int = 0
      tag_counts: dict[str, int] = field(default_factory=dict)
      watermark_advanced_to: Optional[str] = None
      errors: list[str] = field(default_factory=list)
      dry_run: bool = False

  def classify_outcome(
      mr: dict,
      pipelines: list[dict],
      revert_mr: Optional[dict],
  ) -> tuple[Outcome, dict]:
      """Pure function: given an MR + its post-merge pipelines + optional revert MR,
      return (outcome_label, evidence_dict). Severity order: regressed > rolled_back > success.
      """
      # 1. regressed: any post-merge pipeline status in {'failed','canceled'}
      #    on the target branch (default 'main')
      # 2. rolled_back: revert_mr is not None AND its state == 'merged'
      # 3. else: success
      ...

  class OutcomeSyncService:
      def __init__(
          self,
          conn: sqlite3.Connection,
          gitlab: GitLabClient,
          event_bus: Optional[EventBus] = None,
      ) -> None: ...

      def sync(
          self,
          *,
          project: str,
          since: Optional[str] = None,
          full_backfill: bool = False,
          dry_run: bool = False,
      ) -> OutcomeSyncSummary: ...
  ```

  **The `sync` method's algorithm:**
  1. Determine `updated_after`: explicit `since` > `read_sync_state(project).last_seen_updated_at` > `_DEFAULT_LOOKBACK` if `full_backfill`.
  2. Call `gitlab.list_merged_mrs_since(project, updated_after=...)`.
  3. For each MR:
     - Skip MRs whose `source_branch` does not match `_BRANCH_RE`.
     - Extract `ticket_id`.
     - Call `list_executions_for_ticket_untagged(conn, ticket_id)`. If empty → skip (Sentinel did not own this MR, OR we already tagged it).
     - Call `gitlab.list_pipelines_for_commit(project, sha=mr['merge_commit_sha'], ref=mr.get('target_branch','main'))`.
     - Detect revert: list the project's MRs with `source_branch="revert-...(merge_commit_sha[:8] OR mr_iid)"` OR title prefix `Revert "<title>"` referencing the merge SHA. Helper kept simple: query `gitlab.list_merge_requests(project_id, state='merged')` filtered to titles starting with `Revert ` referencing this MR. **If the revert lookup raises, log + continue with `(success, evidence)` — never fail the whole sync on a revert lookup.**
     - Call `classify_outcome(mr, pipelines, revert_mr)` → `(outcome, evidence)`.
     - For each untagged execution: `update_execution_outcome(...)` + `OutcomeRecorded` publish (skipped on dry_run). Increment `tag_counts[outcome]`.
  4. After the loop: `upsert_sync_state(conn, project=..., last_synced_at=now_iso, last_seen_mr_iid=max(iid), last_seen_updated_at=max(updated_at))`.
  5. Return `OutcomeSyncSummary`.

- **MIRROR**: `src/core/learning/extract.py` for module structure (dataclasses, pure functions). `src/core/persistence/postmortems.py` for SQL helper conventions.
- **GOTCHA**:
  - "Append-once" semantics live in `update_execution_outcome` (Task 2) — service does not need to re-check.
  - On dry_run, do everything except the UPDATE and the bus publish; still compute the would-be summary so the CLI can preview.
  - On per-MR exceptions (e.g. transient HTTP error from `list_pipelines_for_commit`): catch at the per-MR boundary, append to `summary.errors`, continue. Watermark only advances past MRs whose classification AND tagging both succeeded, so a transient failure resumes cleanly next run.
  - **No imports from `src.agents.*`** — learning is a foundation layer, agents depend on it (matches the rule asserted in `core/events/types.py:18-19`).
- **VALIDATE**:
  - `poetry run pytest tests/core/test_outcome_sync.py -q`.
  - `poetry run mypy src/core/learning/outcome_sync.py` (project uses pyproject mypy if configured).

### Task 9: UPDATE `src/core/learning/__init__.py`

- **ACTION**: Re-export `OutcomeSyncService`, `classify_outcome`, `OutcomeSyncSummary`.
- **IMPLEMENT**: Append imports + `__all__` entries alphabetized.
- **MIRROR**: existing pattern in `src/core/learning/__init__.py`.
- **VALIDATE**: `poetry run python -c "from src.core.learning import OutcomeSyncService; print('ok')"`.

### Task 10: UPDATE `src/cli.py` — flag, group, subcommand, hooks

Three sub-edits in one file. Apply in this order so the file stays valid between edits.

**10.a — Add feature flag helper** (after `_overlay_proposer_enabled` at cli.py:78-83):
```python
def _outcome_sync_enabled() -> bool:
    """Phase 3A feature flag — set OUTCOME_SYNC_ENABLED=1 to enable
    pull-on-demand outcome ingestion at the start of plan/execute and via
    the explicit `sentinel outcomes sync` subcommand.

    Default off until the exit-criterion fixture (one merged MR + one
    reverted MR + one post-merge-CI failure) tags correctly. Mirrors the
    EXTRACTION_ENABLED / OVERLAY_PROPOSER_ENABLED pattern.
    """
    return os.getenv("OUTCOME_SYNC_ENABLED", "0") == "1"
```

**10.b — Add `outcomes` group + `sync` subcommand** (append at end of cli.py, after the last existing group):
```python
@cli.group()
def outcomes() -> None:
    """Phase 3A: pull merge / revert / post-merge-CI outcomes from GitLab."""
    pass


@outcomes.command("sync")
@click.option(
    "--project",
    "-p",
    default=None,
    help="Project path (e.g., acme/backend). If omitted, sync every "
         "project that has a row in project_sync_state OR can be inferred "
         "from existing executions.ticket_id prefixes mapped via config.",
)
@click.option(
    "--since",
    type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"]),
    default=None,
    help="Override watermark; only sync outcomes updated_after this date.",
)
@click.option(
    "--all",
    "all_history",  # avoid shadowing builtin
    is_flag=True,
    default=False,
    help="Backfill from epoch (ignores watermark). Use sparingly.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Preview tags + watermark advance; do not write or publish events.",
)
def outcomes_sync(
    project: Optional[str],
    since: Optional[datetime],
    all_history: bool,
    dry_run: bool,
) -> None:
    """Sync GitLab outcomes into executions.outcome (Phase 3A)."""
    if not _outcome_sync_enabled() and not dry_run:
        click.echo(
            "OUTCOME_SYNC_ENABLED=0 — pass --dry-run to preview, or set "
            "OUTCOME_SYNC_ENABLED=1 to write.",
            err=True,
        )
        sys.exit(2)
    try:
        conn = connect()
        try:
            apply_migrations(conn)
            from src.core.events import EventBus  # noqa: PLC0415
            from src.core.learning.outcome_sync import OutcomeSyncService  # noqa: PLC0415
            from src.gitlab_client import GitLabClient  # noqa: PLC0415

            event_bus: Optional[EventBus]
            execution_id: Optional[str]
            if dry_run:
                event_bus = None
                execution_id = None
            else:
                execution_id = _learning_seed_synthetic_execution(
                    conn, prefix="outcomes-sync"
                )
                event_bus = EventBus(conn)

            service = OutcomeSyncService(conn, GitLabClient(), event_bus=event_bus)
            since_iso = since.replace(tzinfo=timezone.utc).isoformat() if since else None
            projects = [project] if project else _discover_known_projects(conn)
            for proj in projects:
                summary = service.sync(
                    project=proj,
                    since=since_iso,
                    full_backfill=all_history,
                    dry_run=dry_run,
                )
                _print_outcome_sync_summary(summary)
        finally:
            conn.close()
    except Exception as e:
        click.echo(f"❌ outcomes sync failed: {e}", err=True)
        sys.exit(1)
```
Plus a small `_discover_known_projects(conn)` helper near `_learning_seed_synthetic_execution` (cli.py:1655) and a `_print_outcome_sync_summary(summary)` helper that prints `mrs_seen`, `executions_tagged`, `tag_counts`, `watermark_advanced_to`. Both helpers stay in `cli.py` (no new module).

**10.c — Pre-flight hook in `plan`** (after cli.py:211, the `click.echo(f"📋 Planning ticket: {ticket_id}")` line):
```python
        # Phase 3A: pull-on-demand outcome ingestion. Non-fatal — sync
        # failures must never block planning. Gated on OUTCOME_SYNC_ENABLED.
        if _outcome_sync_enabled():
            try:
                _run_outcome_sync_preflight(project=project)
            except Exception as e:
                logger.warning("outcome sync preflight failed: %s", e)
```

Same in `execute` after cli.py:600. The shared `_run_outcome_sync_preflight(project)` helper opens its own short-lived connection, applies migrations, runs `OutcomeSyncService.sync(project=...)`, closes — so it doesn't touch the `db_conn` the caller may already hold.

- **MIRROR**: `learning extract` subcommand at cli.py:1701-1758 verbatim for option style and dry-run/flag-off semantics.
- **GOTCHA**:
  - Heavy imports (`GitLabClient`, `OutcomeSyncService`, `EventBus`) MUST stay inside the function body — see cli.py:1644-1645 comment about keeping `import src.cli` cheap for `--help`.
  - The pre-flight hook calls `_outcome_sync_enabled()` at every invocation; do NOT cache. The flag-read pattern documented at cli.py:46-50 says "Re-defined here at module scope so cli.py is self-contained" — apply the same discipline.
  - `--all` shadows Python's builtin; use `"all_history"` as the Click-level Python identifier (Click handles the `--all` flag → `all_history` arg).
  - `_learning_seed_synthetic_execution` already exists at cli.py:1655. Reuse it; pass `prefix="outcomes-sync"`.
- **VALIDATE**:
  - `poetry run sentinel outcomes --help` shows the group + sync.
  - `OUTCOME_SYNC_ENABLED=0 poetry run sentinel outcomes sync` exits 2 with the expected message.
  - `poetry run sentinel outcomes sync --dry-run --project acme/backend` runs and prints summary even with the flag off.
  - `OUTCOME_SYNC_ENABLED=0 poetry run sentinel plan ACME-1` (mocked Jira) — pre-flight is a no-op, plan proceeds.
  - `poetry run pytest tests/test_cli_outcomes.py -q`.

### Task 11: CREATE tests

Three test files. The exit-criterion test (`tests/integration/test_phase3a_outcomes.py`) is the gating one.

**11.a — `tests/test_gitlab_client.py` (UPDATE)** — append two test classes:
```python
class TestListMergedMrsSince:
    def test_paginates_via_x_total_pages_header(self, gitlab_client): ...
    def test_falls_back_to_short_page_when_header_missing(self, gitlab_client): ...
    def test_url_encoding_and_required_params(self, gitlab_client):
        # assert "state=merged", "order_by=updated_at", "sort=asc",
        # "updated_after=...", "acme%2Fbackend" in URL/params
        ...
    def test_raise_for_status_propagates(self, gitlab_client): ...

class TestListPipelinesForCommit:
    def test_basic_call(self, gitlab_client): ...
    def test_url_encoding_and_params(self, gitlab_client): ...
```
Mirror existing test patterns (`tests/test_gitlab_client.py:70-99`).

**11.b — `tests/core/test_outcome_sync.py` (CREATE)**:
```python
class TestClassifyOutcome:
    """Pure function table — no DB, no GitLab client."""
    def test_post_merge_pipeline_failed_is_regressed(self): ...
    def test_revert_mr_merged_is_rolled_back(self): ...
    def test_clean_merge_is_success(self): ...
    def test_severity_order_regressed_beats_rolled_back(self): ...
    def test_pending_pipeline_does_not_mark_regressed(self): ...

class TestOutcomeSyncService:
    """Real in-memory SQLite (sqlite_mem_conn fixture); mocked GitLabClient."""
    def test_tags_executions_for_matching_branch(self, sqlite_mem_conn, event_bus): ...
    def test_does_not_overwrite_existing_outcome(self, sqlite_mem_conn): ...
    def test_advances_watermark_to_last_seen_updated_at(self, sqlite_mem_conn): ...
    def test_idempotent_rerun_does_not_repagnate(self, sqlite_mem_conn): ...
    def test_dry_run_writes_nothing_and_publishes_no_events(self, sqlite_mem_conn, event_bus): ...
    def test_branch_without_sentinel_prefix_is_skipped(self, sqlite_mem_conn): ...
    def test_pipeline_lookup_failure_does_not_abort_sync(self, sqlite_mem_conn): ...
    def test_publishes_outcome_recorded_per_tagged_execution(self, sqlite_mem_conn, event_bus): ...

class TestSyncStateHelpers:
    def test_update_execution_outcome_is_append_once(self, sqlite_mem_conn): ...
    def test_upsert_sync_state_replaces_on_conflict(self, sqlite_mem_conn): ...
```

**11.c — `tests/integration/test_phase3a_outcomes.py` (CREATE)** — the exit-criterion fixture. PRD line 496-497:
> on a fixture project with a known merged MR and a known reverted MR, `sentinel outcomes sync` correctly tags the matching `execution_id`s; the watermark advances; a re-run does not re-paginate. A post-merge pipeline failure on `main` tags the originating `execution_id` as `regressed`.

```python
def test_phase3a_exit_criterion(sqlite_mem_conn, event_bus, monkeypatch):
    """Exit criterion: 3 MRs (success, rolled_back, regressed) → tagged
    correctly, watermark advances, re-run is a no-op."""
    # Seed three executions with ticket_ids ACME-1, ACME-2, ACME-3.
    # Build a fake GitLabClient with hard-coded list_merged_mrs_since
    # returning three MRs:
    #   - sentinel/feature/ACME-1 — clean merge → success
    #   - sentinel/feature/ACME-2 — has revert MR → rolled_back
    #   - sentinel/feature/ACME-3 — post-merge pipeline status='failed' → regressed
    # Run service.sync(project='acme/backend').
    # Assert: 3 OutcomeRecorded events, 3 tag_counts entries, watermark
    # = max updated_at, second sync.summary.mrs_seen == 0.
```

**11.d — `tests/test_cli_outcomes.py` (CREATE)** — Click runner-based tests for the surface:
```python
from click.testing import CliRunner
from src.cli import cli

def test_outcomes_sync_disabled_without_dry_run_exits_2(monkeypatch):
    monkeypatch.delenv("OUTCOME_SYNC_ENABLED", raising=False)
    result = CliRunner().invoke(cli, ["outcomes", "sync", "--project", "acme/backend"])
    assert result.exit_code == 2
    assert "OUTCOME_SYNC_ENABLED=0" in result.output

def test_outcomes_sync_dry_run_runs_with_flag_off(monkeypatch, sqlite_mem_conn): ...
def test_plan_preflight_is_noop_with_flag_off(monkeypatch): ...
def test_plan_preflight_swallows_sync_exceptions(monkeypatch): ...
```

- **MIRROR**: `tests/test_cli_learning.py` (mirrored CLI surface tests for the `learning` group), `tests/integration/test_phase2c_promotion.py` (integration-style fixture).
- **GOTCHA**: CliRunner-invoked CLI tests must monkeypatch `connect` and `GitLabClient` so they don't hit the real `~/.sentinel/sentinel.db` or live GitLab.
- **VALIDATE**: `poetry run pytest tests/ -q -k "outcome or phase3a or cli_outcomes" --no-header`.

---

## Testing Strategy

### Unit Tests to Write

| Test File | Test Cases | Validates |
|---|---|---|
| `tests/test_gitlab_client.py` | pagination via header, fallback via short page, URL encoding, raise_for_status | new GitLab methods |
| `tests/core/test_outcome_sync.py` | classifier truth table, append-once UPDATE, watermark advance, idempotent re-run, dry-run, branch-prefix filter | service + helpers |
| `tests/test_cli_outcomes.py` | flag-off + dry-run, flag-off + non-dry-run exits 2, plan/execute preflight no-op, preflight swallows exceptions | CLI surface |

### Integration Test (exit criterion)

| Test File | Test Case | PRD Line |
|---|---|---|
| `tests/integration/test_phase3a_outcomes.py` | merged MR → success; reverted MR → rolled_back; post-merge pipeline failure → regressed; watermark advances; second run is no-op | 496-497 |

### Edge Cases Checklist

- [ ] MR whose `source_branch` does not match `sentinel/feature/...` → skipped, summary records `mrs_seen += 1` but no tag.
- [ ] MR matching prefix but no execution row exists for the ticket_id → skipped (logged at INFO, no error).
- [ ] Multiple executions for same ticket_id → all untagged ones tagged with the same outcome; emits one `OutcomeRecorded` per tagged execution.
- [ ] Same MR appears in two consecutive syncs (boundary `updated_at`) → second-sync UPDATE returns 0 rows (append-once); no duplicate `OutcomeRecorded`.
- [ ] Post-merge pipeline still running (status='running'/'pending') → does NOT mark regressed; defer to next sync.
- [ ] Revert MR detection lookup raises HTTPError → logged, classified as `success`, error appended to `summary.errors`, sync continues.
- [ ] `--all` ignores the existing watermark and re-paginates; append-once UPDATE prevents tag duplication.
- [ ] Dry-run: zero rows written, zero events published, `summary.dry_run==True`, watermark untouched.
- [ ] CLI hot path with flag off: zero GitLab calls (`OutcomeSyncService` not constructed at all).
- [ ] CLI hot path with flag on but GitLab token missing: `GitLabClient.__init__` raises ValueError; preflight catches, plan/execute proceeds with WARNING log.
- [ ] Migration is a no-op on a DB that already has the columns (re-run safety).

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
poetry run mypy src/core/learning/outcome_sync.py src/core/persistence/sync_state.py src/gitlab_client.py
poetry run ruff check src/core/learning/outcome_sync.py src/core/persistence/sync_state.py src/cli.py
```
EXPECT: exit 0, no errors.

### Level 2: UNIT_TESTS

```bash
poetry run pytest tests/test_gitlab_client.py -q
poetry run pytest tests/core/test_outcome_sync.py -q
poetry run pytest tests/test_cli_outcomes.py -q
```
EXPECT: all green.

### Level 3: INTEGRATION_TEST

```bash
poetry run pytest tests/integration/test_phase3a_outcomes.py -q
```
EXPECT: green. This is the **exit-criterion** test from PRD line 496-497.

### Level 4: FULL_SUITE_REGRESSION

```bash
poetry run pytest -q
```
EXPECT: zero new failures vs. baseline. Existing 850+ tests unchanged.

### Level 5: MIGRATION_SAFETY

```bash
poetry run python - <<'PY'
from src.core.persistence import connect, apply_migrations
c = connect(":memory:")
apply_migrations(c)
apply_migrations(c)  # idempotent — no-op on second run
cols = {row[1] for row in c.execute("PRAGMA table_info(executions)")}
assert "outcome" in cols
assert "outcome_evidence_json" in cols
assert "outcome_recorded_at" in cols
assert c.execute("SELECT COUNT(*) FROM project_sync_state").fetchone()[0] == 0
print("migration ok")
PY
```
EXPECT: prints `migration ok`.

### Level 6: MANUAL_VALIDATION (post-merge, before flipping flag)

1. Set `OUTCOME_SYNC_ENABLED=1` in a dev env.
2. Run `poetry run sentinel outcomes sync --project acme/backend --dry-run --all` against a real GitLab project that Sentinel has historical executions for.
3. Inspect output: tag counts plausible, no exceptions in log.
4. Re-run without `--dry-run`. Re-run again. Assert second run reports `mrs_seen=0`.
5. SELECT `outcome, COUNT(*)` GROUP BY `outcome` from `executions` — non-zero counts in the expected categories.
6. Flip flag back off; confirm `sentinel plan ACME-1` runs without preflight overhead.

---

## Acceptance Criteria

- [ ] Migration `005_outcome_ingestion.sql` lands; `executions.outcome` column + `project_sync_state` table present in fresh DB.
- [ ] `GitLabClient.list_merged_mrs_since` and `list_pipelines_for_commit` exist; pagination tested.
- [ ] `OutcomeRecorded` event is exported from `src.core.events`.
- [ ] `OutcomeSyncService.sync()` correctly tags all three outcome categories on the integration fixture.
- [ ] Watermark advances; re-run reports zero new MRs (`summary.mrs_seen == 0`).
- [ ] `executions.outcome` is append-once (second UPDATE leaves the original row intact).
- [ ] CLI subcommand `sentinel outcomes sync` honors `--project`, `--since`, `--all`, `--dry-run`, and gates on `OUTCOME_SYNC_ENABLED`.
- [ ] Pre-flight hook in `plan` and `execute` is a no-op with the flag off; with flag on, sync exceptions are logged and swallowed (plan/execute always proceeds).
- [ ] Level 1–4 commands green.
- [ ] No new dependencies in `pyproject.toml`.
- [ ] No write to `postmortems`, `feedback_rules`, `prompts/`, or `commands/` from any code added in this phase (Phase 3B/3C territory).

---

## Completion Checklist

- [ ] Tasks 1–11 complete in dependency order
- [ ] Each task's VALIDATE command run green before moving to the next
- [ ] Migration safety check (Level 5) passes
- [ ] Integration exit-criterion test (Level 3) passes
- [ ] Full-suite regression (Level 4) passes — zero new failures
- [ ] `git grep -nE "OutcomeRecorded|outcome_sync|project_sync_state"` confirms changes are in expected files only
- [ ] `git grep -n "python-gitlab"` returns zero matches (still not used)
- [ ] `git grep -n "outcome_weight\|outcome_weight_recompute\|propose_skills"` returns zero matches (Phase 3B/3C not bled into 3A)

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Pre-flight sync slows `plan`/`execute` startup | MED | MED | Watermark + small per-page lookback ⇒ steady-state cost is O(MRs since last run) ≈ 0–5. Log timing at INFO so regressions are visible. Flag default OFF until measured. |
| GitLab `updated_after` boundary causes double-counting | MED | LOW | Append-once UPDATE in `update_execution_outcome` is idempotent; double-tag is impossible. |
| Revert detection too narrow (e.g. squash + revert via Git) | MED | MED | Heuristic intentionally simple in 3A. Documented as known limitation; widen in 3B if reranker shows missed signals. |
| Branch-name matching fails for executions whose ticket_id casing differs | LOW | MED | Use exact match against `executions.ticket_id`; the worktree branch always derives from this column (`worktree_manager.py:23`), so they agree by construction. Test asserts. |
| Live GitLab call breaks `sentinel plan` for offline users | HIGH if uncaught | HIGH | Pre-flight wrapped in `try/except Exception: logger.warning(...)`. Flag-gated default OFF. |
| Migration column add fails on existing DBs (NOT NULL without DEFAULT) | LOW | HIGH | Columns are nullable by design (untagged executions); test Level 5 explicitly. |
| Phase 3A code accidentally implements Phase 3B reranking | MED (scope drift) | MED | "NOT Building" section + completion-checklist `git grep` are the gates. PR description must quote the relevant NOT-Building lines. |
| `_discover_known_projects` over-syncs (CLI without `--project`) | LOW | LOW | Default behavior of the CLI is documented; exit-criterion fixture uses an explicit `--project` so the broader path is reviewer-checked separately. |

---

## Notes

**Why no MR IID column on `executions`.** PRD §10 establishes branch-name as the canonical identity (`sentinel/feature/{TICKET_ID}`). Adding an `mr_iid` column would require backfill (existing rows have no MR IID) AND duplicate information already derivable from the branch. If branch-name matching ever proves insufficient (e.g. multi-MR-per-ticket workflows), the column is a small additive migration in a future phase — out of scope here.

**Why a single migration instead of two.** A `005_executions_outcome.sql` + `006_project_sync_state.sql` split is cleaner in isolation but means two `apply_migrations` boundaries; if the first lands and the second fails, the DB is in a half-state where the column exists but the watermark table doesn't. One migration keeps the rollback story to "delete migration row 005 and re-run", which is what `db.py:140-161` already does atomically.

**Why feature-flag default is OFF.** Mirrors `EXTRACTION_ENABLED=0` and `OVERLAY_PROPOSER_ENABLED=0` default-off discipline. PRD line 497 describes the rollback behavior — the flag's role at flip-on is left to the operator's judgement once the exit-criterion fixture passes. Document this in the PR description.

**Why `OutcomeRecorded` per execution, not per MR.** The downstream consumers (Phase 3B reranker, 3C skill promoter) will want to count outcomes by execution, not by MR. Per-execution events are easier to filter; the per-MR rollup is reconstructable from the `mr_iid` field if anyone needs it.

**Why no async, no retry, no decorator.** `gitlab_client.py:1-510` is sync, has no retries, has no decorators — the established style. Phase 3A inherits it. If retries become necessary they belong in a cross-cutting wrapper, not bolted onto two new methods.

**Suggested orchestration.** This plan is best executed by:
- **`sentinel-persistence-expert`** for Tasks 1–3 (migration + sync_state helpers + persistence `__init__` re-export).
- **`sentinel-learning-integrator`** for Tasks 4–5 + 10 (event types, events `__init__` re-export, CLI seam + preflight hooks).
- A general-purpose worker (or a future `sentinel-outcome-poller-expert` if the role is created) for Tasks 6–9 (GitLabClient methods + service module).
- **`sentinel-test-harness-expert`** for Task 11.
- **`sentinel-learning-reviewer`** runs read-only review BEFORE merge per its agent contract — it inspects events/types.py, persistence migrations, prompt_loader (untouched here), base_developer (untouched), post_execute (untouched).
