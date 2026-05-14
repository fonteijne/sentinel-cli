# Feature: Phase 2C — "Promotion Path" (Agent Learning from Feedback)

## Summary

Close the learning loop. Phase 1 writes `postmortems` on cap-out; Phase 2A injects them into the planner prompt; Phase 2B closes reactive loops. **Phase 2C is the pipeline that grows memory on its own** — a heuristic extraction job clusters recurring postmortems by `failure_signature`, increments their confidence as evidence accumulates across executions and projects, and (when confidence ≥ 80 + ≥ 3 observations + ≥ 2 distinct projects) opens a draft GitLab MR against the **Sentinel repo** that proposes adding the rule to `prompts/overlays/drupal_*.md`. A human Sentinel maintainer is the gate. Revocation is append-only via `superseded_by`. Both jobs are flag-gated (`EXTRACTION_ENABLED=0`, `OVERLAY_PROPOSER_ENABLED=0`) and ship disabled until exit-criterion fixtures pass.

## User Story

As a Sentinel maintainer
I want recurring failure signatures across multiple projects to surface as a draft overlay PR with the source postmortems quoted as evidence
So that durable lessons travel across projects without me hand-curating overlay files, while every promoted rule keeps a one-hop audit trail back to the executions that taught it.

## Problem Statement

After Phase 2A + 2B ship, postmortems written in run N are surfaced to the planner in run N+1 (within the same DB) and reviewer vetoes trigger replans. **Memory still doesn't grow on its own**: postmortems with identical `failure_signature` accumulate as separate rows with `confidence=50` (the cap-out default — `src/core/persistence/postmortems.py:36`) regardless of how many times the same root cause recurs. There is no mechanism to:

- Detect that the same `failure_signature` has fired across N executions and M projects.
- Promote a high-evidence cluster from `postmortems` (per-incident) to `prompts/overlays/drupal_*.md` (durable, cross-project knowledge).
- Maintain provenance: a future maintainer reading an overlay bullet must be able to trace it back to the postmortem rows that justified it.
- Revoke a bad auto-promoted rule without breaking the append-only invariant (Decision 4 / §6.2).

Concretely (verifiable):
- `src/core/persistence/postmortems.py:1-135` — has `insert_postmortem`, `query_active_postmortems`, `list_postmortems`. **No clustering helper, no signature-aggregation query, no `mark_superseded` write path.**
- `prompts/overlays/drupal_developer.md`, `drupal_plan_generator.md`, `drupal_reviewer.md`, `drupal_exploration.md` — static, hand-curated. No `<!-- postmortem:N origin:exec_X -->` provenance markers anywhere in tree (`grep -r "postmortem:" prompts/` returns nothing).
- `src/gitlab_client.py:61-115` — has `create_merge_request(project_id, title, source_branch, target_branch, description, draft)`. **No git-side helper to commit/push a branch on the Sentinel repo itself.** `commit_and_push_plan` (`src/agents/plan_generator.py:790-855`) commits to *project* worktrees, not the Sentinel repo.
- `src/core/learning/__init__.py:1-17` — re-exports `render_pitfalls_section` and `MAX_PITFALL_CHARS` only. No `extract` module.
- No `scripts/` directory. No CI scheduler config. No `cron`/`systemd` timer file in tree.
- `agent-learning-from-feedback-DECISIONS.md:66-82` — **D4 is still `Deferred`**, with the explicit revisit trigger: "Starting work on Phase 2 task 'Overlay PR proposer' (handover §8 / design §8 task 11)." This plan must resolve D4 before any code lands.

## Solution Statement

A two-stage offline pipeline driven by Click subcommands so the existing CLI is the operational surface (no new daemon, no cron file, no systemd unit — operators run `sentinel learning extract` and `sentinel learning propose` manually or wire them into their own scheduler):

1. **D4 resolution (decision artifact, not code).** Update `docs/agent-learning-from-feedback-DECISIONS.md` D4: PR targets the **Sentinel repo** (this repo, not the originating project). Approver is the Sentinel maintainer. Promotion is **never automatic** — the extractor inserts to a `feedback_rules` table at probation; the proposer opens a *draft* MR; merge is human action. This matches §8 task 11 wording verbatim.

2. **`feedback_rules` table (new migration `004_feedback_rules.sql`).** Single new table with the minimum columns Phase 2C actually uses — project-count and observation-count, NOT the full Appendix C/D schema (deferred). Schema:
   ```sql
   CREATE TABLE feedback_rules (
     id                       INTEGER PRIMARY KEY AUTOINCREMENT,
     signature                TEXT NOT NULL,           -- normalized failure_signature
     scope                    TEXT NOT NULL,           -- 'drupal' | 'python' | ... (Phase 2C: stack only)
     agent_target             TEXT NOT NULL,           -- 'developer' | 'planner'  (Phase 2C derived from postmortems.agent)
     rule_text                TEXT NOT NULL,           -- one-line policy (== failure_signature in 2C; LLM distillation is later phase)
     status                   TEXT NOT NULL,           -- 'probation' | 'active' | 'superseded' | 'revoked'
     confidence               INTEGER NOT NULL,        -- 0..95
     observation_count        INTEGER NOT NULL,        -- distinct postmortems
     distinct_projects        INTEGER NOT NULL,        -- distinct executions.ticket_id prefixes
     first_postmortem_id      INTEGER REFERENCES postmortems(id),
     last_postmortem_id       INTEGER REFERENCES postmortems(id),
     proposed_overlay_path    TEXT,
     proposed_overlay_mr_url  TEXT,
     proposed_at              TEXT,
     promoted_to_overlay_sha  TEXT,
     promoted_by              TEXT,
     promoted_at              TEXT,
     superseded_by            INTEGER REFERENCES feedback_rules(id),
     revoked_by               TEXT,
     revoked_at               TEXT,
     revocation_reason        TEXT,
     created_at               TEXT NOT NULL,
     updated_at               TEXT NOT NULL
   );
   CREATE UNIQUE INDEX idx_feedback_rules_dedup
       ON feedback_rules(scope, agent_target, signature)
       WHERE status IN ('probation','active');
   CREATE INDEX idx_feedback_rules_status
       ON feedback_rules(status, confidence DESC);
   ```
   The unique partial index enforces "one live rule per `(scope, agent, signature)` triple" — superseded/revoked rows are excluded so revocation can re-create.

3. **Extraction module (`src/core/learning/extract.py`).** Pure functions over a SQLite connection. The orchestration entrypoint is `extract_clusters(conn, *, days=30, min_observations=3, min_projects=2) -> list[ExtractionResult]`. It:
   1. SELECTs all `postmortems` JOIN `executions` from the last N days where `superseded_by IS NULL`.
   2. Groups by `(stack_type, agent, failure_signature)`.
   3. Derives a project key from `executions.ticket_id` (uppercase prefix before the dash, e.g. `ACME-847` → `ACME`).
   4. Filters clusters meeting `observation_count ≥ min_observations` AND `distinct_projects ≥ min_projects`.
   5. Computes confidence per Appendix C.6 bounded curve, clamped to ≤ 95 (never 100 — humans can be wrong).
   6. **Whack-a-mole guardrail:** rejects clusters where the `failure_signature` is purely symptomatic (regex blacklist of substrings like `"failed assertion"`, `"undefined index"` without a more specific qualifier — the failure signature in §A.5 is normalized to drop paths/line numbers, so a signature that is ONLY `"failed assertion"` is too generic). Documented and tested explicitly per §9 last row of design doc.
   7. UPSERTs `feedback_rules` rows with `status='probation'`, `provenance='auto'`. Idempotent: re-running extraction does not duplicate; it bumps `observation_count`, `distinct_projects`, `confidence`, and updates `last_postmortem_id`.

4. **Overlay PR proposer (`src/core/learning/propose_overlay.py`).** Pure functions plus a Click subcommand `sentinel learning propose [--scope drupal] [--min-confidence 80] [--dry-run]`. It:
   1. SELECTs `feedback_rules` with `status='probation'` AND `confidence >= min_confidence` AND `proposed_at IS NULL` (not yet proposed).
   2. Locates the Sentinel repo root (the directory containing `pyproject.toml` with `name = "sentinel"`) — falls back to `Path(__file__).parent.parent.parent.parent` (the test-friendly path; production deployment runs the script from the repo root anyway).
   3. Creates a new git branch `sentinel-learning/promote-<scope>-<YYYYMMDD-HHMM>` off `main`.
   4. Edits the relevant `prompts/overlays/<scope>_<agent_target>.md` file: appends a `## Auto-promoted pitfalls` section (creates if missing) with one bullet per rule, including the trailing provenance HTML comment `<!-- rule:N origin:postmortem-X first_seen:YYYY-MM-DD -->`.
   5. Commits with a deterministic message that quotes rule IDs.
   6. Pushes the branch (`git push -u origin <branch>`).
   7. Calls `GitLabClient.create_merge_request(project_id=<sentinel-repo-path>, ..., draft=True)`. Project path is read from a new config key `sentinel.repo_project_path` (e.g., `"sentinel-team/sentinel"`).
   8. UPDATEs each `feedback_rules` row: `proposed_overlay_path`, `proposed_overlay_mr_url`, `proposed_at`. Status stays `probation` until a maintainer merges and runs `sentinel learning mark-merged <rule_id> --sha <commit-sha>` to flip status to `active`.
   9. Emits `FeedbackRulePromoted` event per rule (publish-only — Phase 2C has no subscriber; the event surfaces in the events table for audit).

5. **Append-only revocation path.** New helpers in `src/core/persistence/postmortems.py` (or a new sibling module `src/core/persistence/feedback_rules.py`):
   - `mark_superseded(conn, *, old_rule_id, new_rule_id)` — sets `superseded_by` on the OLD row, leaves the new row in place. INSERT-only-then-UPDATE-pointer. Tested with FK enforcement on.
   - `revoke_rule(conn, *, rule_id, revoked_by, reason)` — sets `status='revoked'`, `revoked_by`, `revoked_at`, `revocation_reason`. Does NOT delete.
   - `mark_promoted(conn, *, rule_id, sha, promoted_by)` — flips `status` from `probation` to `active`, captures the merged commit SHA.

6. **CLI surface.** New `sentinel learning` group with subcommands:
   - `sentinel learning extract [--days 30] [--min-observations 3] [--min-projects 2] [--dry-run]`
   - `sentinel learning propose [--scope drupal] [--min-confidence 80] [--dry-run]`
   - `sentinel learning mark-merged <rule_id> --sha <commit-sha> --by <username>`
   - `sentinel learning revoke <rule_id> --by <username> --reason "<text>"`
   - `sentinel learning list [--status probation] [--scope drupal]` — inspector.

7. **Events** (Pydantic v2 in `src/core/events/types.py`):
   - `FeedbackRuleExtracted(rule_id, signature, scope, agent_target, observation_count, distinct_projects, confidence)` — emitted per cluster the extractor lands.
   - `FeedbackRulePromoted(rule_id, scope, mr_url, branch_name)` — emitted per rule in a proposer run.
   - `FeedbackRuleRevoked(rule_id, revoked_by, reason)` — emitted on `revoke_rule`.

8. **Feature flags.** `EXTRACTION_ENABLED` and `OVERLAY_PROPOSER_ENABLED`, both default `0`. Read at call time (mirrors `POSTMORTEM_INJECTION` at `src/prompt_loader.py:12-19`). The Click commands run regardless of the flags (operators must be able to dry-run); the flags gate the side-effecting paths (DB writes from extraction; git push + MR creation from proposer). When off, the commands print what they *would* do and exit 0.

## Metadata

| Field            | Value                                                                                                                       |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Type             | NEW_CAPABILITY (extraction + proposer + new table) + ENHANCEMENT (CLI, events)                                              |
| Complexity       | HIGH (touches git/MR side effects on the Sentinel repo itself; new schema; D4 resolution; whack-a-mole guardrail)           |
| Systems Affected | persistence (new table + helpers), learning (extract + propose), gitlab_client (read-only — no new methods needed), CLI, events, decisions doc |
| Dependencies     | Phase 2A landed (postmortems table + `query_active_postmortems` + cache invalidation). 2B *recommended* (escalation path). |
| Estimated Tasks  | 18                                                                                                                          |

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║                BEFORE: postmortems pile up; nothing promotes                  ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   Run N (ACME)        Run N+1 (BRAVO)        Run N+2 (CHARLIE)                ║
║   ──────────────       ──────────────         ──────────────                  ║
║   developer caps       developer caps         developer caps                  ║
║   sig=foo              sig=foo                sig=foo                         ║
║         │                    │                      │                          ║
║         ▼                    ▼                      ▼                          ║
║   insert_postmortem    insert_postmortem      insert_postmortem               ║
║   conf=50              conf=50                conf=50                         ║
║                                                                               ║
║   ┌──────────────────────────────────────────────────────────┐                ║
║   │ postmortems                                              │                ║
║   │  id=1 sig=foo conf=50 stack=drupal exec=ACME             │                ║
║   │  id=2 sig=foo conf=50 stack=drupal exec=BRAVO            │                ║
║   │  id=3 sig=foo conf=50 stack=drupal exec=CHARLIE          │                ║
║   │  ... 47 more                                             │                ║
║   └──────────────────────────────────────────────────────────┘                ║
║                                                                               ║
║   ❌ Same signature, three projects, three reviewers — no aggregation.        ║
║   ❌ Phase 2A renders all three as separate bullets with conf=50 each.        ║
║   ❌ No path from postmortems → prompts/overlays/drupal_*.md.                 ║
║   ❌ No way to revoke a bad rule without violating append-only.               ║
║   ❌ Maintainer can read sqlite3 manually but has no proposed PR.             ║
║                                                                               ║
║   PAIN: durable cross-project lessons stay locked in per-incident rows.       ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════╗
║              AFTER: Phase 2C — clusters → probation → draft MR                ║
╠═══════════════════════════════════════════════════════════════════════════════╣
║                                                                               ║
║   Cron / operator                                                             ║
║         │                                                                     ║
║         ▼                                                                     ║
║   sentinel learning extract --days 30                                         ║
║         │                                                                     ║
║         ▼                                                                     ║
║   ┌─────────────────────────────────────────────────────────────┐            ║
║   │ extract_clusters(conn, days=30, min_obs=3, min_proj=2)      │            ║
║   │   1. JOIN postmortems × executions (last 30d)               │            ║
║   │   2. GROUP BY (stack, agent, signature)                     │            ║
║   │   3. project_key = ticket_id.split('-')[0]                  │            ║
║   │   4. filter: ≥3 obs AND ≥2 distinct projects                │            ║
║   │   5. whack-a-mole filter: reject pure symptoms              │            ║
║   │   6. confidence = 50 + 10·min(5, obs−1) + 5·min(3, proj−1)  │            ║
║   │   7. UPSERT feedback_rules (status='probation')             │            ║
║   └─────────────────────────────────────────────────────────────┘            ║
║         │                                                                     ║
║         ▼                                                                     ║
║   ┌──────────────────────────────────────────────────────────┐                ║
║   │ feedback_rules                                           │                ║
║   │  id=1  sig=foo conf=80 obs=3 proj=2 status=probation     │                ║
║   │       first_pm=1 last_pm=3                               │                ║
║   └──────────────────────────────────────────────────────────┘                ║
║                                                                               ║
║   sentinel learning propose --scope drupal --min-confidence 80                ║
║         │                                                                     ║
║         ▼                                                                     ║
║   ┌─────────────────────────────────────────────────────────────┐            ║
║   │ git checkout -b sentinel-learning/promote-drupal-...        │            ║
║   │ EDIT prompts/overlays/drupal_developer.md                   │            ║
║   │  + ## Auto-promoted pitfalls                                │            ║
║   │  + - **[rule:1 conf:80]** sig=foo  ◄── provenance trailer   │            ║
║   │       <!-- rule:1 origin:postmortem-1 first_seen:2026-... --> │          ║
║   │ git commit -m "promote rule:1 ..."                          │            ║
║   │ git push -u origin <branch>                                 │            ║
║   │ GitLabClient.create_merge_request(draft=True)               │            ║
║   │ UPDATE feedback_rules: proposed_overlay_mr_url, proposed_at │            ║
║   └─────────────────────────────────────────────────────────────┘            ║
║         │                                                                     ║
║         ▼                                                                     ║
║   Sentinel maintainer reviews the draft MR (D4-resolved gate).                ║
║   On merge:                                                                   ║
║     sentinel learning mark-merged 1 --sha def456 --by alice                   ║
║         → status: probation → active                                          ║
║                                                                               ║
║   Bad rule? Append-only revocation:                                           ║
║     sentinel learning revoke 1 --by alice --reason "policy change ..."        ║
║         → status: active → revoked  (row preserved; ledger intact)            ║
║                                                                               ║
║   VALUE: cross-project lessons promote themselves with a draft MR + audit.    ║
║                                                                               ║
╚═══════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location                                       | Before                                                          | After                                                                                                                                | User Impact                                                                       |
| ---------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `src/core/persistence/migrations/`             | `001_init.sql`, `003_postmortems.sql`                          | Adds `004_feedback_rules.sql`                                                                                                        | New canonical durable-rule store; postmortems remain per-incident                  |
| `src/core/learning/`                           | Renderer + cache invalidator only                               | Adds `extract.py`, `propose_overlay.py`, `feedback_rules.py` helpers                                                                 | Extraction + proposer become first-class learning concerns                        |
| `src/core/events/types.py`                     | `PostmortemRecorded`, `PromptBudgetExceeded`, etc.              | + `FeedbackRuleExtracted`, `FeedbackRulePromoted`, `FeedbackRuleRevoked`                                                            | Audit trail in the events table                                                   |
| CLI                                            | `sentinel postmortems list` only                                | + `sentinel learning {extract, propose, mark-merged, revoke, list}`                                                                   | Operators have a single, scriptable surface                                       |
| `prompts/overlays/drupal_*.md`                 | Hand-curated only                                               | + `## Auto-promoted pitfalls` section appended by proposer; bullets carry `<!-- rule:N origin:postmortem-X --> ` trailers            | Provenance is visible in the file itself; a future maintainer can `git blame`     |
| `docs/agent-learning-from-feedback-DECISIONS.md` | D4 status: `Deferred`                                          | D4 status: `Accepted`. Decision body documents Sentinel-repo target + Sentinel-maintainer approver + never-auto-merge.               | Removes the documented blocker for `sentinel-distiller-expert` / `sentinel-cli-rules-expert` |
| Config                                         | `gitlab.base_url`, `gitlab.api_token`                           | + `sentinel.repo_project_path` (e.g., `"sentinel-team/sentinel"`)                                                                    | Proposer knows where to open the MR                                               |

---

## Mandatory Reading

**CRITICAL:** Implementation agent MUST read these files and code spans before writing any code.

### P0 — Cannot start without reading

| File                                                          | Lines      | Why                                                                                       |
| ------------------------------------------------------------- | ---------- | ----------------------------------------------------------------------------------------- |
| `docs/agent-learning-from-feedback-2026-05-03.md`             | 452-468    | Phase 2C scope, exit criterion, dependency on 2A, rollback plan                          |
| `docs/agent-learning-from-feedback-2026-05-03.md`             | 619-735    | Appendix C.3-C.6 — schema reference for the *future-richer* table; we're shipping a subset |
| `docs/agent-learning-from-feedback-2026-05-03.md`             | 484-498    | §9 risks — memory poisoning, prompt drift, **whack-a-mole** (last row)                    |
| `docs/agent-learning-from-feedback-DECISIONS.md`              | 66-82      | **D4 — must be resolved before code lands**                                               |
| `docs/agent-learning-from-feedback-DECISIONS.md`              | 85-100     | D5 — overlay char cap is reviewer discipline; no CI check                                 |
| `docs/agent-learning-from-feedback-DECISIONS.md`              | 123-146    | D7 — never un-draft on cap-out (proposer MR is *always* `draft=True`)                     |
| `src/core/persistence/postmortems.py`                         | all (135)  | Append-only invariants; the read helpers we'll lean on                                    |
| `src/core/persistence/migrations/003_postmortems.sql`         | all (33)   | Schema reality; the `superseded_by` column is the precedent                               |
| `src/core/persistence/migrations/001_init.sql`                | all (46)   | `executions.ticket_id` is the project-key source                                          |
| `src/core/persistence/db.py`                                  | all (162)  | Migration runner — per-statement, BEGIN IMMEDIATE, never `executescript()`                |
| `src/agents/_structured_errors.py`                            | 300-326    | `normalize_failure_signature` — used at write time; we cluster by the same key            |
| `src/core/execution/post_execute.py`                          | 75-178     | Subscriber-registration + emit pattern to MIRROR for our new events                       |
| `src/core/events/types.py`                                    | 29-96      | Pydantic event-class shape; `Literal` discriminator pattern                               |
| `src/gitlab_client.py`                                        | 40-115     | `create_merge_request` signature — we DO NOT add new methods, only call existing one       |
| `src/agents/plan_generator.py`                                | 790-855    | Existing git commit/push pattern — same approach for the Sentinel-repo branch             |
| `.claude/PRPs/plans/completed/phase-2a-pitfalls-visible.plan.md` | all      | Style template — Patterns to Mirror, Files to Change, atomic tasks                         |
| `prompts/overlays/drupal_developer.md`                        | all        | The file the proposer edits — read before writing the editor function                      |

### P1 — Read before touching the relevant slice

| File                                                                 | Lines     | Why                                                                                  |
| -------------------------------------------------------------------- | --------- | ------------------------------------------------------------------------------------ |
| `src/cli.py`                                                         | 1580-1614 | `postmortems list` group — MIRROR for `sentinel learning list`                       |
| `src/cli.py`                                                         | 129-137   | Click root group; how new groups attach                                              |
| `src/prompt_loader.py`                                               | 12-19     | Feature-flag pattern (`POSTMORTEM_INJECTION`) — copy verbatim for our two new flags  |
| `src/core/learning/cache_invalidator.py`                             | all       | Subscriber wiring boilerplate                                                         |
| `tests/core/test_postmortems.py`                                     | 1-72      | In-memory SQLite fixture + parent execution row — base for our extraction tests     |
| `tests/test_cli_postmortems.py`                                      | 1-140     | `CliRunner` + `SENTINEL_DB_PATH` monkeypatch — mirror for `sentinel learning` tests  |
| `src/config_loader.py`                                               | 1-60      | How to add `sentinel.repo_project_path`                                              |
| `src/core/persistence/__init__.py`                                   | all (25)  | Re-export contract                                                                    |

### P2 — Style references (skim only)

| File                                                                | Why                                                                |
| ------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `.claude/PRPs/plans/completed/phase-1-close-the-leash.plan.md`      | Original migration + event style                                    |
| `.claude/PRPs/plans/completed/phase-2b-closed-loops.plan.md`        | Cancellation/seam patterns; useful if we add a stop-extraction signal |
| `.claude/agents/sentinel-persistence-expert.md`                     | Owning agent for `004_feedback_rules.sql`                          |
| `.claude/agents/sentinel-learning-reviewer.md`                      | Reviewer who must sign off this PR                                  |

### External Documentation

| Source                                                                                  | Section                          | Why                                                              |
| ---------------------------------------------------------------------------------------- | -------------------------------- | ---------------------------------------------------------------- |
| [SQLite — partial indexes](https://www.sqlite.org/partialindex.html)                    | "WHERE clause" syntax            | The dedup unique index uses `WHERE status IN ('probation','active')` |
| [Click v8 — option groups](https://click.palletsprojects.com/en/stable/commands/)        | nested `Group` with subcommands  | `sentinel learning <subcmd>` follows the existing `postmortems` group pattern |
| [GitLab API v4 — MRs](https://docs.gitlab.com/ee/api/merge_requests.html#create-mr)     | POST /projects/:id/merge_requests | Endpoint already wrapped by `GitLabClient.create_merge_request`  |
| [Python sqlite3 — `lastrowid`](https://docs.python.org/3/library/sqlite3.html#sqlite3.Cursor.lastrowid) | INSERT semantics              | `mark_promoted` returns the rowid pattern — same as `insert_postmortem` |

---

## Patterns to Mirror

### MIGRATION_PATTERN — `004_feedback_rules.sql`

```sql
-- SOURCE: src/core/persistence/migrations/003_postmortems.sql:1-33
-- COPY THIS HEADER-COMMENT STYLE: design ref, invariants, append-only banner.
-- 004_feedback_rules.sql
-- Phase 2C schema per design §8 task 10 + Appendix C.3 (subset).
-- Phase 2C ships ONLY the columns the extractor + proposer + revoker actually
-- use. The richer Appendix C.3 schema (feedback_observations, MR-comment
-- provenance, fuzzy text dedup) is deferred — when that lands, this migration
-- stays untouched and a 005 widens the surface.
--
-- Append-only:
--   * No DELETE anywhere. Revocation is status='revoked' + revoked_*.
--   * Widening (project:X → stack) is a NEW row + superseded_by pointer on the
--     OLD row, not an UPDATE of `scope`.
--   * Tests assert no UPDATE/DELETE helpers are exported from
--     src.core.persistence.feedback_rules.

CREATE TABLE IF NOT EXISTS feedback_rules (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    signature                TEXT    NOT NULL,
    scope                    TEXT    NOT NULL,
    agent_target             TEXT    NOT NULL,
    rule_text                TEXT    NOT NULL,
    status                   TEXT    NOT NULL,
    confidence               INTEGER NOT NULL,
    observation_count        INTEGER NOT NULL,
    distinct_projects        INTEGER NOT NULL,
    first_postmortem_id      INTEGER REFERENCES postmortems(id),
    last_postmortem_id       INTEGER REFERENCES postmortems(id),
    proposed_overlay_path    TEXT,
    proposed_overlay_mr_url  TEXT,
    proposed_at              TEXT,
    promoted_to_overlay_sha  TEXT,
    promoted_by              TEXT,
    promoted_at              TEXT,
    superseded_by            INTEGER REFERENCES feedback_rules(id),
    revoked_by               TEXT,
    revoked_at               TEXT,
    revocation_reason        TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_rules_dedup
    ON feedback_rules(scope, agent_target, signature)
    WHERE status IN ('probation', 'active');

CREATE INDEX IF NOT EXISTS idx_feedback_rules_status
    ON feedback_rules(status, confidence DESC);
```

### POSTMORTEM_QUERY_PATTERN — extraction's input SELECT

```python
# SOURCE: src/core/persistence/postmortems.py:77-105 (existing query helper).
# COPY THIS PATTERN — same module style, keyword-only args after conn.
def query_postmortem_clusters(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    only_active: bool = True,
) -> list[sqlite3.Row]:
    """Return one row per postmortem in the window, with project_key derived
    from the joined executions.ticket_id.

    Returns rows with keys: id, stack_type, agent, failure_signature,
    context_excerpt, confidence, created_at, ticket_id, project_key.
    Caller groups by (stack_type, agent, failure_signature) in Python rather
    than SQL — keeps the SQL trivial and lets the whack-a-mole filter run
    against grouped clusters before any UPSERT.
    """
    cursor = conn.execute(
        """
        SELECT p.id, p.stack_type, p.agent, p.failure_signature,
               p.context_excerpt, p.confidence, p.created_at,
               e.ticket_id,
               UPPER(SUBSTR(e.ticket_id, 1, INSTR(e.ticket_id, '-') - 1)) AS project_key
        FROM postmortems p
        JOIN executions e ON e.id = p.execution_id
        WHERE (:only_active = 0 OR p.superseded_by IS NULL)
          AND p.created_at >= datetime('now', :window)
        ORDER BY p.stack_type, p.agent, p.failure_signature, p.created_at ASC
        """,
        {"only_active": 1 if only_active else 0, "window": f"-{int(days)} days"},
    )
    return cursor.fetchall()
```

`UPPER(SUBSTR(...))` derives the project key from the ticket prefix (`ACME-847` → `ACME`). If `ticket_id` does not contain `-`, `INSTR` returns 0 and `SUBSTR(s, 1, -1)` yields an empty string — clusters with empty `project_key` get filtered out by the `distinct_projects ≥ 2` guard, which is the correct behavior for non-Jira-style tickets in 2C.

### CONFIDENCE_FORMULA — Appendix C.6, clamped per Phase 2C subset

```python
# SOURCE: docs/agent-learning-from-feedback-2026-05-03.md:751-758
# COPY THIS CURVE, but drop the `distinct_reviewers` term (we don't have
# reviewer data on postmortems in 2C — see §NOT Building).
def compute_confidence(observation_count: int, distinct_projects: int) -> int:
    """Phase 2C confidence curve.

    base = 50 (cap-out default — matches insert_postmortem default at
    src/core/persistence/postmortems.py:36).
    """
    base = 50
    obs_term = 10 * min(5, max(0, observation_count - 1))
    proj_term = 5 * min(3, max(0, distinct_projects - 1))
    return max(0, min(95, base + obs_term + proj_term))
```

The cap of 95 is non-negotiable (Appendix C.6: "never 100 — humans can always be wrong"). The Phase 2A injection floor is 70 (`src/prompt_loader.py:90`); the proposer's promotion floor is 80; both stay below 95 so a probation-but-not-yet-promoted rule (conf in [70, 79]) gets injected as a pitfall but does NOT trigger a PR.

### WHACK_A_MOLE_GUARDRAIL — pure-symptom signature rejection

```python
# SOURCE: design doc §9 last row (whack-a-mole risk) +
# user-instruction reminder in CLAUDE.md ("doesn't want you to play whack-a-mole").
# This guardrail rejects clusters whose failure_signature is purely
# symptomatic — i.e. the normalized signature is short AND lexically generic.
# Real root causes leave specific tokens (a rule code, a service name, a
# class/method, a Drupal hook) behind even after path stripping.

_WHACK_A_MOLE_BLACKLIST = (
    "failed assertion",
    "assertion failed",
    "test failed",
    "unknown error",
    "error: unknown",
    "syntax error",  # too generic alone — a syntax error WITH a token is fine
)

def is_pure_symptom(failure_signature: str) -> bool:
    """True if the signature is too generic to promote.

    Heuristic: short (< 30 chars after normalization) AND its lowercased form
    starts with one of the blacklist phrases AND contains no tokens that
    normalize_failure_signature would have left in (no `::`, no `.`, no
    digits-besides-stripped-line-numbers).
    """
    s = failure_signature.lower().strip()
    if len(s) >= 30:
        return False
    if not any(s.startswith(prefix) for prefix in _WHACK_A_MOLE_BLACKLIST):
        return False
    # If the signature carries any structural token, it's specific enough.
    if "::" in s or "." in s or any(c.isdigit() for c in s):
        return False
    return True
```

A cluster that flunks this filter is logged at `WARNING` and **not** UPSERTed. The extraction summary printed at end-of-run includes a "rejected as pure symptom" count so an operator notices when the filter is biting.

### UPSERT_PATTERN — feedback_rules write

```python
# SOURCE: SQLite UPSERT (ON CONFLICT) — used here because the unique partial
# index `idx_feedback_rules_dedup` is the natural conflict target. The
# alternative (SELECT-then-INSERT-or-UPDATE) races under concurrent extraction
# runs; UPSERT inside an explicit transaction does not.
def upsert_rule(
    conn: sqlite3.Connection,
    *,
    signature: str,
    scope: str,
    agent_target: str,
    rule_text: str,
    confidence: int,
    observation_count: int,
    distinct_projects: int,
    first_postmortem_id: int,
    last_postmortem_id: int,
) -> int:
    """Insert a probation row or update an existing one. Returns the rowid."""
    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO feedback_rules (
            signature, scope, agent_target, rule_text, status, confidence,
            observation_count, distinct_projects, first_postmortem_id,
            last_postmortem_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'probation', ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope, agent_target, signature)
        WHERE status IN ('probation', 'active')
        DO UPDATE SET
            confidence        = excluded.confidence,
            observation_count = excluded.observation_count,
            distinct_projects = excluded.distinct_projects,
            last_postmortem_id = excluded.last_postmortem_id,
            updated_at        = excluded.updated_at
        """,
        (signature, scope, agent_target, rule_text, confidence,
         observation_count, distinct_projects, first_postmortem_id,
         last_postmortem_id, now, now),
    )
    conn.commit()
    # On UPDATE, lastrowid may be 0; SELECT to recover the canonical row id.
    if cursor.lastrowid and cursor.rowcount == 1 and not _was_update(cursor):
        return cursor.lastrowid
    row = conn.execute(
        "SELECT id FROM feedback_rules "
        "WHERE scope = ? AND agent_target = ? AND signature = ? "
        "  AND status IN ('probation', 'active')",
        (scope, agent_target, signature),
    ).fetchone()
    return row["id"]
```

The `lastrowid` quirk on UPSERT is real — sqlite3 returns the new row's id on INSERT, but on UPDATE it can return the *previous* INSERT's id. The SELECT-recovery branch keeps the contract honest.

### EVENT_PATTERN — three new Pydantic events

```python
# SOURCE: src/core/events/types.py:60-96 (existing DeveloperCappedOut /
# PostmortemRecorded / PromptBudgetExceeded shape). COPY VERBATIM.
class FeedbackRuleExtracted(BaseEvent):
    type: Literal["FeedbackRuleExtracted"] = "FeedbackRuleExtracted"
    rule_id: int
    signature: str
    scope: str
    agent_target: str
    observation_count: int
    distinct_projects: int
    confidence: int


class FeedbackRulePromoted(BaseEvent):
    type: Literal["FeedbackRulePromoted"] = "FeedbackRulePromoted"
    rule_id: int
    scope: str
    mr_url: str
    branch_name: str


class FeedbackRuleRevoked(BaseEvent):
    type: Literal["FeedbackRuleRevoked"] = "FeedbackRuleRevoked"
    rule_id: int
    revoked_by: str
    reason: str
```

`execution_id` on these events is set to a synthetic value (`"learning-extract-<UTC ISO>"` / `"learning-propose-<UTC ISO>"` / `"learning-revoke-<UTC ISO>"`) — the bus requires it for FK to `executions`, so the extraction script also INSERTs a placeholder execution row of `kind='learning'` for the duration of the run, mirroring how Phase 1 tests seed `("exec-1", "TEST-1", "developer", "running", ...)` in `tests/core/test_postmortems.py:14-42`.

### CLI_GROUP_PATTERN — `sentinel learning ...`

```python
# SOURCE: src/cli.py:1580-1614 (existing `postmortems` group + `list` subcommand).
# COPY THIS PATTERN — group decorator, keyword options with click.IntRange,
# try/except around connect() with apply_migrations().
@cli.group()
def learning() -> None:
    """Phase 2C: extract → propose → mark-merged / revoke pipeline."""
    pass


@learning.command("extract")
@click.option("--days", type=click.IntRange(1, 365), default=30,
              help="Window in days (default 30).")
@click.option("--min-observations", type=click.IntRange(2, 50), default=3,
              help="Minimum cluster size (default 3).")
@click.option("--min-projects", type=click.IntRange(1, 50), default=2,
              help="Minimum distinct projects in cluster (default 2).")
@click.option("--dry-run", is_flag=True, default=False,
              help="Print clusters and would-be UPSERTs; do not write.")
def learning_extract(days: int, min_observations: int, min_projects: int,
                     dry_run: bool) -> None:
    """Cluster recent postmortems and UPSERT feedback_rules at probation."""
    if not _extraction_enabled() and not dry_run:
        click.echo("EXTRACTION_ENABLED=0 — pass --dry-run to preview, or set "
                   "EXTRACTION_ENABLED=1 to write.", err=True)
        sys.exit(2)
    try:
        conn = connect()
        try:
            apply_migrations(conn)
            from src.core.learning.extract import extract_clusters
            results = extract_clusters(
                conn,
                days=days,
                min_observations=min_observations,
                min_projects=min_projects,
                dry_run=dry_run,
            )
            click.echo(f"Clusters considered: {results.considered}")
            click.echo(f"  ↳ accepted:        {results.accepted}")
            click.echo(f"  ↳ pure-symptom:    {results.rejected_pure_symptom}")
            click.echo(f"  ↳ below thresholds: {results.rejected_below_thresholds}")
            for r in results.rules:
                click.echo(
                    f"  rule#{r.rule_id:>4}  conf={r.confidence:>3}  "
                    f"obs={r.observation_count:>2}  proj={r.distinct_projects:>2}  "
                    f"{r.scope}/{r.agent_target}  {r.signature}"
                )
        finally:
            conn.close()
    except Exception as exc:
        logger.error("learning extract failed: %s", exc, exc_info=True)
        click.echo(f"\n❌ Error: {exc}", err=True)
        sys.exit(1)
```

### GIT_BRANCH_PATTERN — Sentinel-repo side effects

```python
# SOURCE: src/agents/plan_generator.py:790-855 (commit_and_push_plan).
# COPY THIS subprocess pattern — capture_output=True, check=True,
# decode stderr on failure, branch via -u origin <name>.
def push_overlay_branch(
    repo_root: Path,
    branch_name: str,
    overlay_relpath: Path,
    commit_message: str,
) -> None:
    """Stage, commit, and push the overlay edit on `branch_name`.

    Caller must have already created and switched to the branch (so this
    function is testable against a tmp-path bare repo without spawning git
    twice).
    """
    subprocess.run(
        ["git", "add", str(overlay_relpath)],
        cwd=repo_root, check=True, capture_output=True,
    )
    diff_result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_root, capture_output=True,
    )
    if diff_result.returncode == 0:
        raise RuntimeError("No staged overlay changes — refusing to push empty branch.")
    subprocess.run(
        ["git", "commit", "-m", commit_message],
        cwd=repo_root, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "push", "-u", "origin", branch_name],
        cwd=repo_root, check=True, capture_output=True,
    )
```

### TEST_FIXTURE_PATTERN — same as 2A

```python
# SOURCE: tests/core/test_postmortems.py:14-42, tests/test_cli_postmortems.py:46-72.
# COPY THIS in-memory or tmp-file SQLite + apply_migrations + parent
# executions row(s).
@pytest.fixture
def conn_with_executions() -> Iterator[sqlite3.Connection]:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    apply_migrations(c)
    now = datetime.now(timezone.utc).isoformat()
    for exec_id, ticket in [
        ("exec-acme-1", "ACME-847"),
        ("exec-acme-2", "ACME-901"),
        ("exec-bravo-1", "BRAVO-112"),
        ("exec-charlie-1", "CHARLIE-203"),
    ]:
        c.execute(
            "INSERT INTO executions (id, ticket_id, kind, status, created_at) "
            "VALUES (?, ?, 'developer', 'completed', ?)",
            (exec_id, ticket, now),
        )
    c.commit()
    try:
        yield c
    finally:
        c.close()
```

### FEATURE_FLAG_PATTERN — already proven in 2A

```python
# SOURCE: src/prompt_loader.py:12-19 (POSTMORTEM_INJECTION).
# COPY VERBATIM for two new flags; same default-off, read-at-call-time contract.
def _extraction_enabled() -> bool:
    """Phase 2C feature flag — set EXTRACTION_ENABLED=1 to allow DB writes.

    Read at call time so flipping the env var takes effect on the next
    invocation. Default off until exit-criterion fixture passes.
    """
    return os.getenv("EXTRACTION_ENABLED", "0") == "1"


def _overlay_proposer_enabled() -> bool:
    """Phase 2C feature flag — set OVERLAY_PROPOSER_ENABLED=1 to allow git
    push + MR creation against the Sentinel repo. Default off.
    """
    return os.getenv("OVERLAY_PROPOSER_ENABLED", "0") == "1"
```

---

## Files to Change

### Schema + persistence

| File                                                               | Action | Justification                                                                            |
| ------------------------------------------------------------------ | ------ | ---------------------------------------------------------------------------------------- |
| `src/core/persistence/migrations/004_feedback_rules.sql`           | CREATE | New table per `MIGRATION_PATTERN`. Forward-only.                                         |
| `src/core/persistence/feedback_rules.py`                           | CREATE | `upsert_rule`, `mark_superseded`, `revoke_rule`, `mark_promoted`, `list_rules`, `query_promotable`. |
| `src/core/persistence/postmortems.py`                              | UPDATE | Add `query_postmortem_clusters` (read-only JOIN to executions). Append-only invariant intact. |
| `src/core/persistence/__init__.py`                                 | UPDATE | Re-export new helpers.                                                                    |

### Learning subsystem

| File                                          | Action | Justification                                                                                |
| --------------------------------------------- | ------ | -------------------------------------------------------------------------------------------- |
| `src/core/learning/extract.py`                | CREATE | Cluster + confidence + whack-a-mole filter + UPSERT orchestration. Returns `ExtractionSummary`. |
| `src/core/learning/propose_overlay.py`        | CREATE | Branch, edit overlay file, commit, push, MR. Returns per-rule outcomes.                       |
| `src/core/learning/__init__.py`               | UPDATE | Re-export new entrypoints (kept narrow — the modules don't auto-import on package import).    |

### Events

| File                            | Action | Justification                                                                          |
| ------------------------------- | ------ | -------------------------------------------------------------------------------------- |
| `src/core/events/types.py`      | UPDATE | Add `FeedbackRuleExtracted`, `FeedbackRulePromoted`, `FeedbackRuleRevoked` per `EVENT_PATTERN`. |
| `src/core/events/__init__.py`   | UPDATE | Re-export new event classes.                                                            |

### CLI

| File          | Action | Justification                                                                       |
| ------------- | ------ | ----------------------------------------------------------------------------------- |
| `src/cli.py`  | UPDATE | Add `learning` group + 5 subcommands per `CLI_GROUP_PATTERN`. Imports for new helpers. |

### Config + decisions

| File                                                                | Action | Justification                                                                                       |
| ------------------------------------------------------------------- | ------ | --------------------------------------------------------------------------------------------------- |
| `src/config_loader.py`                                              | UPDATE | Read `sentinel.repo_project_path` from config. Optional in Phase 2C but required when proposer is enabled. |
| `config.example.yaml` (or whatever the existing example is)         | UPDATE | Add `sentinel.repo_project_path: "sentinel-team/sentinel"` example.                                  |
| `docs/agent-learning-from-feedback-DECISIONS.md`                    | UPDATE | **Resolve D4** — flip `Status: Deferred` → `Accepted`. Body: target = Sentinel repo; approver = Sentinel maintainer; never auto-merge. |

### Tests (every code change has a test)

| File                                                              | Action | Validates                                                                                      |
| ----------------------------------------------------------------- | ------ | ---------------------------------------------------------------------------------------------- |
| `tests/core/test_feedback_rules_schema.py`                        | CREATE | Migration applies idempotently; partial-index rejects duplicate live rows; superseded row collisions allowed. |
| `tests/core/test_feedback_rules_helpers.py`                       | CREATE | `upsert_rule` UPSERT contract; `mark_superseded` chain; `revoke_rule` terminal state; `mark_promoted` SHA capture. |
| `tests/core/test_postmortem_clusters.py`                          | CREATE | `query_postmortem_clusters` JOIN; project_key derivation; window filter; superseded exclusion. |
| `tests/core/test_extract.py`                                      | CREATE | `extract_clusters` end-to-end on seeded postmortems; whack-a-mole rejects pure-symptom clusters; threshold rejection; idempotency on re-run. |
| `tests/core/test_propose_overlay.py`                              | CREATE | Branch creation; overlay edit shape (provenance trailer); commit message; uses tmp-path bare repo. Mocks `GitLabClient.create_merge_request`. |
| `tests/test_cli_learning.py`                                      | CREATE | All 5 subcommands: `--dry-run`, flag-off path, happy path, `mark-merged`, `revoke`, `list`.    |
| `tests/integration/test_phase2c_promotion.py`                     | CREATE | **Exit-criterion fixture**: seed 3 postmortems across 2 projects → run extract → run propose --dry-run → assert one rule, conf ≥ 80, dry-run output mentions provenance trailer; with `OVERLAY_PROPOSER_ENABLED=1` and a tmp bare repo + monkeypatched GitLab client, full pipeline lands a draft MR call. |
| `tests/integration/test_phase2c_supersede_chain.py`               | CREATE | `mark_superseded` end-to-end: insert old, insert new, point old at new; partial unique index allows re-insert of (scope, agent, signature); rule injected by Phase 2A loader is the new one, not the old. |

---

## NOT Building (Scope Limits)

Phase 2C is intentionally narrow. Out of scope (lands in 2D / Phase 3 / a future appendix-C-richer phase):

- **No `feedback_observations` table.** The full Appendix C.3 schema with MR-comment provenance, reviewer username, `commit_sha_at_comment`, etc. is *not* shipped. Phase 2C uses the existing `postmortems` rows as the observation ledger. Reviewer-distinctness is therefore **deferred** — the `≥ 2 distinct reviewers` widening criterion in Appendix D.3 is not enforced; only `≥ 2 distinct projects` is. Documented explicitly in the extractor's docstring and in the D4 update.
- **No FeedbackDistiller LLM call.** The extractor's `rule_text` IS the `failure_signature` (already normalized at write time). LLM distillation of free-form MR comments into rule text is a Phase-2D-or-later concern (Appendix C.2).
- **No cron / systemd / scheduled-job infra.** Operators run `sentinel learning extract` and `sentinel learning propose` manually or wire them into their own scheduler. No `scripts/cron.d/*.timer` files are added.
- **No `feedback_rule_exceptions` table.** Project-level opt-outs (Appendix D.5) wait for the project-rules path.
- **No `project:<KEY>` scope.** Phase 2C only handles `scope=<stack>` (e.g. `drupal`). Project-scoped rules and `.sentinel/project-rules.md` are explicitly Phase-2D / future work (Appendix D).
- **No fuzzy-text dedup (rapidfuzz / sentence-transformers).** Exact `failure_signature` match only. The `idx_feedback_rules_dedup` partial unique index is the sole dedup layer.
- **No automatic merge of overlay PRs.** The proposer opens `draft=True` MRs only. `mark-merged` is a *human-driven* CLI invocation after the maintainer reviews and merges. This is the D4 promise.
- **No automated revocation.** `revoke_rule` requires `--by` and `--reason` and is human-invoked. There is no auto-revoke heuristic in 2C.
- **No new `GitLabClient` methods.** The proposer uses the existing `create_merge_request` only. If the Sentinel repo isn't on the configured GitLab host, the proposer fails loudly — no fallback to GitHub or a second client.
- **No `[probation]` tag in injected pitfalls.** Phase 2A injects from `postmortems` only. The `feedback_rules` table is read by extraction/proposer only — Phase 2A's loader does NOT yet read it. Wiring `feedback_rules` into the planner prompt is a Phase 2D follow-up so the read-side contract stays simple in 2C.
- **No CI-side overlay character cap.** Per D5 (`agent-learning-from-feedback-DECISIONS.md:85-100`), reviewer discipline only. The proposer does not check overlay size.
- **No subagent skill promotion** (Voyager-style). That's Phase 3 task 17.
- **No outcome-weighted confidence.** Merge-vs-revert weighting is Phase 3 (already planned in `phase-3-cautious-autonomy.plan.md`).

---

## Step-by-Step Tasks

Execute top-to-bottom. Each task is atomic and has its own validation command. Stop and re-plan if any validation fails.

### Task 1 — UPDATE `docs/agent-learning-from-feedback-DECISIONS.md` (resolve D4)

- **ACTION**: Flip D4 from `Deferred` to `Accepted`. Add a dated decision body.
- **IMPLEMENT** — replace the body of `## D4` with:
  - **Status:** `Accepted` (date today).
  - **Decision.**
    1. Overlay-promotion MRs target the **Sentinel repo** (this repo), not the originating project.
    2. The approver is the Sentinel maintainer pool. The originating project's reviewer does NOT block.
    3. MRs are always `draft=True` on creation. Merge is a human action; the proposer never un-drafts.
    4. There is no automatic widening from `project:<KEY>` to `<stack>` in Phase 2C — `project:<KEY>` scope is not implemented yet (see §NOT Building).
    5. When the project-scoped path lands (future phase), this decision is revisited only for the *project → stack* widening flow; the mechanism here remains unchanged.
  - **Implementation pointer.** `src/core/learning/propose_overlay.py` reads target from config key `sentinel.repo_project_path`.
- **GOTCHA**: Don't delete the `Context.` and `Revisit trigger.` paragraphs — append the `Decision.` block after them so the historical context survives.
- **VALIDATE**: `grep -A2 '^## D4 ' docs/agent-learning-from-feedback-DECISIONS.md | grep 'Status:.*Accepted'` returns a hit.

### Task 2 — CREATE `src/core/persistence/migrations/004_feedback_rules.sql`

- **ACTION**: New migration per `MIGRATION_PATTERN`.
- **IMPLEMENT**: Exact schema from §Solution Statement / §MIGRATION_PATTERN, including the partial UNIQUE INDEX on `(scope, agent_target, signature) WHERE status IN ('probation','active')` and the secondary INDEX on `(status, confidence DESC)`.
- **MIRROR**: `src/core/persistence/migrations/003_postmortems.sql:1-33` (header comment style + numbering).
- **GOTCHAS**:
  - Ensure each statement ends with `;` and there are no `executescript`-only constructs (no nested transactions, no `BEGIN`/`COMMIT` inside the file). The migration runner adds those (`src/core/persistence/db.py:148-158`).
  - The partial unique index is what makes UPSERT possible — **do not** use a full unique index on `(scope, agent_target, signature)` because superseded rows must be allowed to coexist with their successor.
- **VALIDATE**: `poetry run python -c "from src.core.persistence import connect, apply_migrations; c = connect(':memory:'); apply_migrations(c); print(list(c.execute('SELECT name FROM sqlite_master WHERE type=\"table\"').fetchall()))"` lists `feedback_rules`.

### Task 3 — CREATE `tests/core/test_feedback_rules_schema.py`

- **ACTION**: New unit-test module.
- **IMPLEMENT** (test cases):
  - `test_migration_creates_table_and_indexes` — assert table exists; both indexes exist; columns match the spec.
  - `test_migration_idempotent` — apply twice; no error.
  - `test_partial_unique_blocks_duplicate_live_rule` — INSERT a probation row; INSERT a duplicate `(scope, agent, signature)` with status='probation' — expect `IntegrityError`.
  - `test_partial_unique_allows_superseded_collision` — INSERT a row, set its status='superseded'; a new probation row with the same `(scope, agent, signature)` is allowed.
  - `test_fk_to_postmortems` — INSERT a feedback_rules row referencing a non-existent `first_postmortem_id` — expect `IntegrityError` with `PRAGMA foreign_keys=ON`.
- **MIRROR**: `tests/core/test_postmortems.py:14-72` (in-memory fixture).
- **VALIDATE**: `poetry run pytest tests/core/test_feedback_rules_schema.py -x -v`.

### Task 4 — CREATE `src/core/persistence/feedback_rules.py`

- **ACTION**: New persistence module — narrow surface, append-only spirit.
- **IMPLEMENT** (functions, all keyword-only after `conn`):
  - `upsert_rule(conn, *, signature, scope, agent_target, rule_text, confidence, observation_count, distinct_projects, first_postmortem_id, last_postmortem_id) -> int` per `UPSERT_PATTERN`.
  - `query_promotable(conn, *, scope=None, min_confidence=80, only_unproposed=True, limit=50) -> list[sqlite3.Row]` — SELECT `status='probation' AND confidence >= ? AND (NOT only_unproposed OR proposed_at IS NULL)` ordered by `confidence DESC, updated_at DESC`.
  - `list_rules(conn, *, status=None, scope=None, limit=50) -> list[sqlite3.Row]` — for the CLI inspector. No status filter ⇒ return all.
  - `mark_proposed(conn, *, rule_id, overlay_path, mr_url) -> None` — sets `proposed_overlay_path`, `proposed_overlay_mr_url`, `proposed_at`. Status untouched.
  - `mark_promoted(conn, *, rule_id, sha, promoted_by) -> None` — flips `status` from `probation` to `active`; sets `promoted_to_overlay_sha`, `promoted_by`, `promoted_at`. Raises `ValueError` if the row is not in `probation`.
  - `revoke_rule(conn, *, rule_id, revoked_by, reason) -> None` — flips `status` to `revoked`; sets `revoked_by`, `revoked_at`, `revocation_reason`. Idempotent on already-revoked? **No** — raise `ValueError` (revoking twice masks intent; force the operator to read the existing row).
  - `mark_superseded(conn, *, old_rule_id, new_rule_id) -> None` — sets `superseded_by` on the old row AND its `status` to `'superseded'`. Validates that the new row exists. Both updates inside a single explicit `BEGIN IMMEDIATE / COMMIT`.
- **MIRROR**: `src/core/persistence/postmortems.py:26-135` for module shape (docstring style, keyword-only args, `_VALID_STATUS` frozenset for guards).
- **GOTCHAS**:
  - **NO update_rule, NO delete_rule are exported.** Tests assert `'update_rule'` and `'delete_rule'` are NOT in `dir(src.core.persistence.feedback_rules)`. Append-only spirit.
  - `mark_superseded` runs both UPDATEs in one transaction so a crash mid-call doesn't leave a row pointing at a `superseded_by` whose own `status` is still `'probation'`.
  - `mark_promoted` must verify the prior status is `'probation'` *inside* the transaction (SELECT ... THEN UPDATE under `BEGIN IMMEDIATE`) — a parallel `revoke_rule` could race otherwise.
- **VALIDATE**: `poetry run pytest tests/core/test_feedback_rules_helpers.py -x -v`.

### Task 5 — CREATE `tests/core/test_feedback_rules_helpers.py`

- **ACTION**: New unit-test module.
- **IMPLEMENT** (test cases):
  - `test_upsert_inserts_new_row` — fresh signature → INSERT; rowid > 0; status='probation'.
  - `test_upsert_updates_existing_row` — same signature again with new counts → UPDATE; same rowid; counts/confidence updated; status untouched.
  - `test_upsert_returns_canonical_id_after_update` — assert the returned rowid matches the row's actual `id`, not 0 or last-inserted-anywhere.
  - `test_query_promotable_filters_by_confidence_and_status` — insert {probation conf=85, probation conf=70, active conf=95, revoked conf=85}. With min=80, only the first row.
  - `test_query_promotable_only_unproposed` — call `mark_proposed` on a row; subsequent `query_promotable(only_unproposed=True)` excludes it.
  - `test_mark_promoted_flips_status_and_records_sha` — after call, row has `status='active'`, `promoted_to_overlay_sha`, `promoted_by`, `promoted_at` populated.
  - `test_mark_promoted_rejects_non_probation` — call on an `active` row → `ValueError`.
  - `test_revoke_rule_terminal` — flips to `revoked`; second call to `revoke_rule` → `ValueError`.
  - `test_revoke_rule_does_not_delete` — row still exists in `SELECT * FROM feedback_rules`.
  - `test_mark_superseded_chain` — insert A; insert B (with same scope/agent/signature, but A's status flipped to 'superseded' first by the helper); call `mark_superseded(old=A, new=B)`. Verify A.superseded_by = B.id and A.status = 'superseded'. New `query_promotable` returns B only.
  - `test_no_update_or_delete_helpers_exported` — assert `update_rule` and `delete_rule` are NOT module attributes.
- **MIRROR**: `tests/core/test_postmortems.py` test layout.
- **VALIDATE**: `poetry run pytest tests/core/test_feedback_rules_helpers.py -x -v`.

### Task 6 — UPDATE `src/core/persistence/postmortems.py` (add `query_postmortem_clusters`)

- **ACTION**: Add the JOIN read helper used by extraction. The module stays append-only.
- **IMPLEMENT**: Per `POSTMORTEM_QUERY_PATTERN`. Add to the existing module so the public surface stays in `src/core/persistence/__init__.py`.
- **GOTCHAS**:
  - The SQLite parameter `:window` must be a string like `"-30 days"`; using `?` positional and the `text` column type interpolation gets messy. Named params are clearer.
  - `INSTR(ticket_id, '-')` returns 0 when no dash — the `SUBSTR(s, 1, -1)` then yields an empty string. That's intentional: those clusters get filtered out downstream because `distinct_projects ≥ 2` cannot be satisfied with a single empty key.
  - `only_active=False` is offered for tests that need the full history, but production callers always pass `only_active=True` (default).
- **VALIDATE**: `poetry run pytest tests/core/test_postmortem_clusters.py -x -v`.

### Task 7 — CREATE `tests/core/test_postmortem_clusters.py`

- **ACTION**: New unit-test module.
- **IMPLEMENT** (test cases):
  - `test_window_filters_old_postmortems` — seed one row at `created_at` = 60 days ago, one at 5 days ago; `days=30` returns only the recent.
  - `test_project_key_derivation` — seed `ACME-847`, `BRAVO-112`, plain `whatever`; assert project_keys are `'ACME'`, `'BRAVO'`, `''`.
  - `test_excludes_superseded` — seed two rows with same signature; superseded one is excluded.
  - `test_orders_by_grouping_keys` — assert rows arrive sorted by `(stack_type, agent, failure_signature, created_at)`.
- **MIRROR**: `tests/core/test_postmortems.py:14-42` (fixture).
- **VALIDATE**: `poetry run pytest tests/core/test_postmortem_clusters.py -x -v`.

### Task 8 — UPDATE `src/core/persistence/__init__.py`

- **ACTION**: Re-export new helpers and the new event imports as needed.
- **IMPLEMENT**: Add re-exports for `query_postmortem_clusters` (from `postmortems.py`) and `upsert_rule`, `query_promotable`, `list_rules`, `mark_proposed`, `mark_promoted`, `revoke_rule`, `mark_superseded` (from `feedback_rules.py`). Update `__all__`.
- **VALIDATE**: `poetry run python -c "from src.core.persistence import query_postmortem_clusters, upsert_rule, mark_promoted, revoke_rule, mark_superseded; print('OK')"`.

### Task 9 — UPDATE `src/core/events/types.py` and `src/core/events/__init__.py`

- **ACTION**: Add three new events.
- **IMPLEMENT**: `FeedbackRuleExtracted`, `FeedbackRulePromoted`, `FeedbackRuleRevoked` per `EVENT_PATTERN`.
- **GOTCHAS**:
  - All three events still require `execution_id` (BaseEvent invariant). The CLI commands seed a synthetic `executions` row of `kind='learning'` for the duration of the run. Document this in each event's docstring.
  - `FeedbackRulePromoted.mr_url` must always be a real URL (the proposer asserts this); a dry-run path should NOT publish this event.
- **VALIDATE**: `poetry run pytest tests/core/test_event_bus.py -x` (Phase 1 / 2A tests still pass with the new types in tree — Pydantic discriminator is a `Literal`, so it's additive).

### Task 10 — CREATE `src/core/learning/extract.py`

- **ACTION**: New module.
- **IMPLEMENT**:
  - `@dataclass class ExtractionResult`: `rule_id, signature, scope, agent_target, observation_count, distinct_projects, confidence`.
  - `@dataclass class ExtractionSummary`: `considered, accepted, rejected_pure_symptom, rejected_below_thresholds, rules: list[ExtractionResult]`.
  - `compute_confidence(observation_count, distinct_projects) -> int` per `CONFIDENCE_FORMULA`.
  - `is_pure_symptom(failure_signature) -> bool` per `WHACK_A_MOLE_GUARDRAIL`.
  - `extract_clusters(conn, *, days=30, min_observations=3, min_projects=2, dry_run=False) -> ExtractionSummary`:
    1. Calls `query_postmortem_clusters(conn, days=days, only_active=True)`.
    2. Iterates with Python `itertools.groupby` over `(stack_type, agent, failure_signature)`.
    3. For each group: counts observations, counts `distinct_projects` (set comprehension over `project_key`, dropping empty strings).
    4. Filters threshold; counts rejected reasons.
    5. Filters whack-a-mole; counts rejected.
    6. For survivors: computes confidence; if `dry_run` skips the UPSERT (still returns the would-be `ExtractionResult` with `rule_id=-1`); else calls `upsert_rule`, then publishes `FeedbackRuleExtracted` via a passed-in `EventBus | None` (the CLI passes a real bus; tests pass `None`).
- **GOTCHAS**:
  - The `EventBus` import is local to the function (`from src.core.events.bus import EventBus`) so unit tests don't need a bus to import the module.
  - Idempotency: re-running extraction over the same window MUST produce the same `ExtractionSummary` (modulo `updated_at`). The UPSERT contract guarantees this; the test asserts it.
  - The CLI's synthetic `executions` row insertion happens in `cli.py` before calling `extract_clusters`, NOT inside the module — keeps the module testable without the bus.
- **VALIDATE**: `poetry run pytest tests/core/test_extract.py -x -v`.

### Task 11 — CREATE `tests/core/test_extract.py`

- **ACTION**: New unit-test module.
- **IMPLEMENT** (test cases):
  - `test_compute_confidence_curve` — 1 obs / 1 proj → 50; 3 obs / 2 proj → 50 + 20 + 5 = 75; 6 obs / 4 proj → 50 + 50 + 15 → clamped 95.
  - `test_is_pure_symptom_blocks_generic` — `"failed assertion"` → True; `"failed assertion in foo::bar"` → False; `"phpunit::failed_assertion::sentinel_demo"` → False; long signatures with structural tokens → False.
  - `test_extract_below_thresholds_rejected` — seed 2 obs across 1 project; assert `accepted=0`.
  - `test_extract_pure_symptom_rejected` — seed 5 obs across 3 projects with `failure_signature='failed assertion'`; assert `rejected_pure_symptom=1`, `accepted=0`.
  - `test_extract_happy_path_inserts_probation_row` — seed 3 obs across 2 projects with a specific signature; assert `accepted=1`; `feedback_rules` row exists with `status='probation'`, `confidence>=70`, `first_postmortem_id` and `last_postmortem_id` set.
  - `test_extract_idempotent_on_rerun` — run twice; second run UPDATEs (same rowid), counts unchanged, `updated_at` advances.
  - `test_extract_dry_run_no_writes` — `dry_run=True`; row count in `feedback_rules` unchanged; returned summary still lists the cluster with `rule_id=-1`.
  - `test_extract_skips_superseded_postmortems` — set one of the cluster postmortems' `superseded_by`; cluster size drops below threshold → rejected.
- **VALIDATE**: `poetry run pytest tests/core/test_extract.py -x -v`.

### Task 12 — CREATE `src/core/learning/propose_overlay.py`

- **ACTION**: New module — branch + edit + commit + push + MR.
- **IMPLEMENT**:
  - `@dataclass class ProposalResult`: `rule_id, branch_name, mr_url, dry_run`.
  - `_overlay_path_for(scope, agent_target) -> Path` — `prompts/overlays/{scope}_{agent_target}.md` relative to repo root. Fail loudly if file doesn't exist.
  - `_render_rule_bullet(row) -> str` — produces `"- **[rule:{id} conf:{conf} obs:{obs} proj:{projects}]** {signature}\n  Source: postmortem #{first_postmortem_id} (first seen {first_seen_iso}) → most recent {last_postmortem_id}.\n  <!-- rule:{id} origin:postmortem-{first_postmortem_id} first_seen:{first_seen_date} -->\n"`.
  - `_apply_overlay_edit(repo_root, overlay_relpath, bullets) -> None` — finds or creates the `## Auto-promoted pitfalls` H2 section, appends bullets at the end of that section. If the section doesn't exist, creates it at the end of the file with a leading blank line.
  - `_branch_name_for(scope) -> str` — `f"sentinel-learning/promote-{scope}-{datetime.utcnow().strftime('%Y%m%d-%H%M')}"`.
  - `propose_overlays(conn, *, gitlab_client, repo_root, repo_project_path, scope, min_confidence=80, dry_run=False) -> list[ProposalResult]`:
    1. `query_promotable(conn, scope=scope, min_confidence=min_confidence, only_unproposed=True)`.
    2. If empty list → return `[]` immediately (extra noisy log line at INFO).
    3. `branch_name = _branch_name_for(scope)`.
    4. `subprocess.run(["git", "checkout", "-b", branch_name], cwd=repo_root, check=True, capture_output=True)`.
    5. `_apply_overlay_edit(...)` for the consolidated set of rules grouped by `agent_target`.
    6. If `dry_run`: revert branch (`git checkout -` then `git branch -D <branch>`), return `[ProposalResult(rule_id=r.id, branch_name=branch_name, mr_url="(dry-run)", dry_run=True) for r in rules]`.
    7. Else: `push_overlay_branch(...)`; call `gitlab_client.create_merge_request(project_id=repo_project_path, title="Auto-promote {scope} pitfalls — {len(rules)} rules", source_branch=branch_name, target_branch="main", description=<rendered evidence>, draft=True)`.
    8. For each rule, call `mark_proposed(conn, rule_id=r.id, overlay_path=str(overlay_relpath), mr_url=mr["web_url"])` and publish `FeedbackRulePromoted`.
- **MIRROR**: `src/agents/plan_generator.py:790-855` (subprocess pattern) + `src/gitlab_client.py:61-115` (create_merge_request signature).
- **GOTCHAS**:
  - Always `draft=True` in `create_merge_request`. **Hard-coded.** D7 invariant.
  - `repo_root` is passed in by the CLI (it resolves it once, validating that `pyproject.toml` exists and contains `name = "sentinel"`). The module never assumes a specific cwd.
  - **Never auto-merge.** The proposer never calls `update_merge_request(state_event="merge")`.
  - **Failure of git push is non-fatal for the rest of the queue.** Iterate rules in a batch; one failed push aborts only that proposer run, not subsequent runs (the un-`mark_proposed`d rules pop up again next time).
  - The MR description must include the postmortem rows as evidence — for each rule, query the first/last postmortem rows and embed `failure_signature`, `context_excerpt`, `created_at`, plus a link to `executions.ticket_id`. PR description hard cap: 64 KiB (matches event bus payload cap from `src/core/events/bus.py:32`).
- **VALIDATE**: `poetry run pytest tests/core/test_propose_overlay.py -x -v`.

### Task 13 — CREATE `tests/core/test_propose_overlay.py`

- **ACTION**: New unit-test module.
- **IMPLEMENT** (test cases — all use a tmp-path bare git repo + monkeypatched `GitLabClient`):
  - Fixture: `tmp_repo` — `git init`, commit a starter `prompts/overlays/drupal_developer.md`, set `user.email` / `user.name` for the commit.
  - Fixture: `mock_gitlab` — a `Mock` with `create_merge_request` returning `{"web_url": "https://gl/proj/-/merge_requests/42", "iid": 42, "state": "opened", "title": "...", "raw": {}}`.
  - `test_dry_run_creates_no_branch_no_mr` — assert `_apply_overlay_edit` was not called for real (use a temp branch + cleanup); `gitlab_client.create_merge_request.call_count == 0`; `feedback_rules.proposed_at` still NULL.
  - `test_propose_writes_provenance_trailer` — happy path, real overlay file edited; assert `<!-- rule:1 origin:postmortem-` substring present in committed file.
  - `test_propose_calls_gitlab_with_draft_true` — assert `mock_gitlab.create_merge_request` called with `draft=True` and `target_branch="main"` and `project_id=` the configured Sentinel repo path.
  - `test_propose_records_mr_url_and_path` — after success, `feedback_rules.proposed_overlay_mr_url == mock url`, `proposed_at` non-NULL, `proposed_overlay_path == 'prompts/overlays/drupal_developer.md'`.
  - `test_propose_idempotent_only_unproposed` — call twice; second call returns empty list (no rules left after `proposed_at` set on first call).
  - `test_propose_branch_naming` — branch name matches `r"^sentinel-learning/promote-drupal-\d{8}-\d{4}$"`.
  - `test_propose_zero_rules_no_branch_creation` — empty `query_promotable` result; no branch created; no MR call.
- **VALIDATE**: `poetry run pytest tests/core/test_propose_overlay.py -x -v`.

### Task 14 — UPDATE `src/cli.py` — add `learning` group + 5 subcommands

- **ACTION**: Append the new group below the existing `postmortems` group.
- **IMPLEMENT**:
  - `learning extract` per `CLI_GROUP_PATTERN`. Seeds a synthetic `executions` row (`id="learning-extract-{utc-iso}"`, `ticket_id="LEARNING-EXTRACT"`, `kind="learning"`) before invoking `extract_clusters` so events have an FK to land on. Publishes `FeedbackRuleExtracted` for each accepted rule via the same event bus the CLI already constructs in `execute()` / `plan()`.
  - `learning propose --scope drupal --min-confidence 80 --dry-run`. Constructs a real `GitLabClient`. Seeds a synthetic execution row similarly. Resolves `repo_root` (start from `Path(__file__).resolve().parents[2]` — the inner `sentinel/` dir; verify `pyproject.toml` exists there). Reads `repo_project_path` from `config_loader.get_config().get("sentinel", {}).get("repo_project_path")` — error and exit 1 if missing AND `OVERLAY_PROPOSER_ENABLED=1`.
  - `learning mark-merged <rule_id> --sha <sha> --by <username>`.
  - `learning revoke <rule_id> --by <username> --reason <text>`.
  - `learning list [--status probation|active|revoked|superseded] [--scope drupal] [--limit N]` — uses `list_rules` helper.
  - All commands wrap `connect()` + `apply_migrations()` in try/finally per the `postmortems list` template.
- **MIRROR**: `src/cli.py:1580-1614` (existing `postmortems` group + `list` subcommand).
- **GOTCHAS**:
  - Both extraction and propose commands must **gate side effects on the feature flags**, not gate the dry-run path. `--dry-run` works regardless of flag state.
  - `mark-merged` and `revoke` are immediate effects — no flag gate, but they require explicit `--by` and `--reason` so an accidental invocation shows up in the audit ledger with attribution.
  - Imports go in the function bodies for the sub-commands that pull in heavy deps (`from src.core.learning.extract import extract_clusters`, etc.) — keeps `import src.cli` cheap for `--help`.
- **VALIDATE**: `poetry run pytest tests/test_cli_learning.py -x -v`.

### Task 15 — CREATE `tests/test_cli_learning.py`

- **ACTION**: New CLI integration test module.
- **IMPLEMENT** (test cases — `CliRunner` + `SENTINEL_DB_PATH` fixture per `tests/test_cli_postmortems.py:1-140`):
  - Fixture: `db_path_with_postmortems` — temp DB, seeded executions and postmortems for two projects with three observations of the same signature.
  - `test_extract_dry_run_prints_clusters` — `runner.invoke(cli, ["learning", "extract", "--dry-run"])`; exit 0; output mentions cluster signature; DB unchanged.
  - `test_extract_flag_off_writes_blocked` — without `EXTRACTION_ENABLED=1`, no `--dry-run`, exit 2 with stderr message.
  - `test_extract_flag_on_writes_row` — `monkeypatch.setenv("EXTRACTION_ENABLED", "1")`; exit 0; `feedback_rules` has one probation row.
  - `test_propose_zero_rules_when_extract_unrun` — exit 0; output `"No rules ready"`; no GitLab call.
  - `test_propose_dry_run_no_writes` — seed a probation row above threshold; `monkeypatch.setenv("OVERLAY_PROPOSER_ENABLED", "0")`; `--dry-run`; exit 0; `proposed_at` still NULL.
  - `test_mark_merged_flips_status` — seed an `active`-able row at `probation`; `mark-merged 1 --sha def456 --by alice`; row is now `active` with sha+promoted_by populated.
  - `test_revoke_terminal` — seed a row; `revoke 1 --by bob --reason "policy"`; row is now `revoked` with metadata.
  - `test_list_filters_by_status` — seed multiple statuses; `list --status active` returns only active.
- **GOTCHA**: For CLI tests that call the propose path with `OVERLAY_PROPOSER_ENABLED=1`, **monkeypatch `GitLabClient`** to avoid real network. Use `monkeypatch.setattr("src.core.learning.propose_overlay.GitLabClient", FakeGitLabClient)` if the module imports the class at module level — easier: have `cli.py` accept an injected `gitlab_client` parameter wired via `click.pass_context` for the test path.
- **VALIDATE**: `poetry run pytest tests/test_cli_learning.py -x -v`.

### Task 16 — CREATE `tests/integration/test_phase2c_promotion.py` (exit-criterion fixture)

- **ACTION**: The thing the reviewer checks at gate. Run the full pipeline end-to-end against a tmp git repo + mocked GitLabClient.
- **IMPLEMENT**:
  - Fixture: tmp-path bare repo with the real `prompts/overlays/drupal_developer.md` from this tree copy-pasted in (pull from `Path(__file__).parent.parent.parent / "prompts/overlays/drupal_developer.md"`).
  - Fixture: temp DB + 3 postmortems across 2 projects (ACME, BRAVO) with the same signature `phpunit::failed_assertion::sentinel_demo`.
  - Fixture: monkeypatched `GitLabClient.create_merge_request` returning a deterministic dict.
  - Step 1: `runner.invoke(cli, ["learning", "extract"])` with `EXTRACTION_ENABLED=1`. Assert exit 0, `feedback_rules` has 1 probation row at conf=75.
  - Step 2: Seed 2 more postmortems for the same signature in a 3rd project (CHARLIE). Re-run extract. Assert conf=80 (now 5 obs / 3 projects → 50 + 40 + 10 = 100 → clamped to 95? Recompute: obs_term=10·min(5, 4)=40; proj_term=5·min(3, 2)=10; 50+40+10=100→95). Adjust seeds so we land at 80 exactly: 3 obs / 2 projects gives 75; 4 obs / 2 projects gives 80 with default formula? `10·min(5,3)=30 + 5·min(3,1)=5 = 50+30+5=85`. Pick seed counts to land at exactly 80 to match the proposer threshold; document the math in the test.
  - Step 3: `runner.invoke(cli, ["learning", "propose", "--scope", "drupal", "--min-confidence", "80"])` with `OVERLAY_PROPOSER_ENABLED=1`. Assert exit 0; `mock_gitlab.create_merge_request.call_count == 1`; `feedback_rules.proposed_overlay_mr_url` populated; the overlay file in tmp repo contains the rendered bullet with the `<!-- rule:1 origin:postmortem-` provenance trailer.
  - Step 4: `runner.invoke(cli, ["learning", "mark-merged", "1", "--sha", "def456", "--by", "alice"])`. Assert `status='active'`.
  - Step 5: `runner.invoke(cli, ["learning", "revoke", "1", "--by", "alice", "--reason", "policy change"])`. Assert `status='revoked'`. Row still exists.
- **EXIT CRITERION (design doc §8 Phase 2C):** "extraction produces ≥ 1 confidence-≥-80 row across a Phase-1 cap-out backlog → proposer opens a draft PR against `prompts/overlays/drupal_*.md` quoting the source rows → a Sentinel maintainer can revert it like any other commit. End-to-end `superseded_by` test passes." This test covers all four clauses.
- **VALIDATE**: `EXTRACTION_ENABLED=1 OVERLAY_PROPOSER_ENABLED=1 poetry run pytest tests/integration/test_phase2c_promotion.py -x -v`.

### Task 17 — CREATE `tests/integration/test_phase2c_supersede_chain.py`

- **ACTION**: The append-only / `superseded_by` end-to-end test.
- **IMPLEMENT**:
  - Insert rule A via `upsert_rule` at conf=80; mark proposed; `mark_promoted(rule_id=A, sha='aaa', promoted_by='x')` → status='active'.
  - Insert rule B (different `failure_signature` to bypass the unique partial index) at conf=85.
  - Call `mark_superseded(old_rule_id=A, new_rule_id=B)`.
  - Assert: A.status='superseded'; A.superseded_by=B.id; B unchanged.
  - Assert the partial unique index now permits inserting a fresh probation row with the same `(scope, agent, signature)` as A (because A is no longer in `('probation','active')`).
  - Assert `query_promotable(only_unproposed=False)` returns B but not A.
  - Assert reading the persisted bus events shows no `FeedbackRuleRevoked` for A — supersede is not revocation; the old row is preserved as a historical pointer, not as a cancellation.
- **VALIDATE**: `poetry run pytest tests/integration/test_phase2c_supersede_chain.py -x -v`.

### Task 18 — UPDATE `src/config_loader.py` (and example config) for `sentinel.repo_project_path`

- **ACTION**: Read a new optional config key.
- **IMPLEMENT**:
  - Add a method (or extend an existing one) that returns the configured Sentinel repo project path, e.g. `get_sentinel_repo_project_path() -> Optional[str]`. Returns `None` if not configured.
  - Update the example config file (whatever the existing one is — usually `config.example.yaml` or similar — locate via `grep -l 'gitlab' /Users/carsten.delafonteijne/webserver/sentinel/sentinel/*.yaml /Users/carsten.delafonteijne/webserver/sentinel/sentinel/config/*.yaml 2>/dev/null`) with a commented example: `# sentinel:\n#   repo_project_path: "sentinel-team/sentinel"`.
- **GOTCHA**: Do NOT make `repo_project_path` required to construct the config — many legitimate Sentinel runs (single-project use) never invoke the proposer. Only the proposer command errors out when the value is missing.
- **VALIDATE**:
  - `poetry run pytest tests/test_config_loader.py -x -v` passes.
  - `poetry run python -c "from src.config_loader import get_config; print(get_config().get_sentinel_repo_project_path())"` runs without error (value may be None).

---

## Testing Strategy

### Unit Tests

| Test File                                          | Cases                                                                                     | Validates                                                  |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `tests/core/test_feedback_rules_schema.py`         | Migration idempotent; partial unique blocks live dup; allows superseded coexistence; FK   | Schema correctness                                         |
| `tests/core/test_feedback_rules_helpers.py`        | UPSERT semantics; mark_proposed; mark_promoted (probation→active); revoke (terminal); supersede chain; no UPDATE/DELETE exports | Persistence helpers + append-only invariant |
| `tests/core/test_postmortem_clusters.py`           | Window filter; project_key derivation; superseded exclusion; ordering                      | New SELECT helper                                          |
| `tests/core/test_extract.py`                       | Confidence curve; whack-a-mole rejection; threshold rejection; happy path UPSERT; idempotency; dry-run; superseded postmortems excluded | Extraction logic                                |
| `tests/core/test_propose_overlay.py`               | Provenance trailer in committed file; `draft=True`; mark_proposed side effects; idempotency on re-run; branch naming; zero-rules path | Proposer + git/GitLab integration               |
| `tests/test_cli_learning.py`                       | All 5 subcommands; flag gating; dry-run; mock GitLabClient                                | CLI surface                                                |

### Integration Tests

| Test File                                          | Cases                                                                                     | Validates                                                  |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `tests/integration/test_phase2c_promotion.py`      | Exit-criterion: 3 obs / 2 proj → extract → propose --dry-run → propose → mark-merged → revoke | The thing the reviewer checks at gate                       |
| `tests/integration/test_phase2c_supersede_chain.py`| Append-only revocation via supersede; partial unique index permits the chain              | Decision 4 invariant                                       |

### Edge Cases Checklist

- [ ] Empty postmortems table: extract runs successfully, returns `accepted=0`, no DB writes.
- [ ] All clusters below `min_observations`: extract rejects all; `accepted=0`; `rejected_below_thresholds` = number of clusters considered.
- [ ] All clusters fail whack-a-mole: extract rejects all; `accepted=0`; `rejected_pure_symptom` = N.
- [ ] Postmortem with `superseded_by` set: never enters the cluster.
- [ ] Postmortem from a non-Jira ticket (no `-` in ticket_id): `project_key` is empty; cluster's `distinct_projects` doesn't count it (filter strips empty keys before `min_projects`).
- [ ] Re-running extract over identical input: idempotent; no duplicate rule rows; `updated_at` advances; `observation_count` and `confidence` unchanged.
- [ ] New postmortem arrives between two extract runs: second run UPDATEs the existing rule, bumps observation_count, recomputes confidence.
- [ ] `feedback_rules` row at conf=79: not promoted at default threshold (80); `query_promotable` excludes it.
- [ ] Proposer with zero promotable rules: no branch created, no MR call, exits 0.
- [ ] Proposer with `OVERLAY_PROPOSER_ENABLED=0` and not `--dry-run`: exit 2 with stderr message; no side effects.
- [ ] Proposer with `repo_project_path` unset: exit 1 with config error message before any git call.
- [ ] `mark-merged` on an already-active row: `ValueError`.
- [ ] `revoke` on an already-revoked row: `ValueError`.
- [ ] `mark_superseded` with non-existent `new_rule_id`: `IntegrityError` (FK).
- [ ] Re-extract after a rule has been revoked but a new postmortem cluster matches: the partial unique index allows inserting a fresh probation row (revoked is excluded from the index predicate). Tested explicitly.
- [ ] Phase 2A loader still works: `query_active_postmortems` is unchanged; injection floor is still 70; the new `feedback_rules` table is invisible to the loader (Phase 2C does NOT wire feedback_rules into prompts).

---

## Validation Commands

### Level 1 — STATIC_ANALYSIS

```bash
poetry run ruff check src/ tests/
poetry run mypy src/
```

**Expect:** exit 0, no new errors.

### Level 2 — UNIT_TESTS (Phase 2C scope only)

```bash
poetry run pytest \
  tests/core/test_feedback_rules_schema.py \
  tests/core/test_feedback_rules_helpers.py \
  tests/core/test_postmortem_clusters.py \
  tests/core/test_extract.py \
  tests/core/test_propose_overlay.py \
  tests/test_cli_learning.py \
  -x -v
```

**Expect:** all green.

### Level 3 — FULL_SUITE (no regressions)

```bash
poetry run pytest tests/ -x
```

**Expect:** Phase 1 and Phase 2A/2B suites still green. New `004_feedback_rules.sql` migration applies idempotently in every existing fixture.

### Level 4 — INTEGRATION (exit criterion)

```bash
EXTRACTION_ENABLED=1 OVERLAY_PROPOSER_ENABLED=1 \
  poetry run pytest \
    tests/integration/test_phase2c_promotion.py \
    tests/integration/test_phase2c_supersede_chain.py \
    -x -v
```

**Expect:** both fixtures pass — promotion pipeline lands a draft MR call; supersede chain preserves the old row.

### Level 5 — DATABASE_VALIDATION

```bash
poetry run python -c "
from src.core.persistence import connect, apply_migrations
c = connect(':memory:')
apply_migrations(c)
tables = [r[0] for r in c.execute(
    \"SELECT name FROM sqlite_master WHERE type='table' ORDER BY name\"
).fetchall()]
indexes = [r[0] for r in c.execute(
    \"SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_feedback%' ORDER BY name\"
).fetchall()]
assert 'feedback_rules' in tables, tables
assert 'idx_feedback_rules_dedup' in indexes, indexes
assert 'idx_feedback_rules_status' in indexes, indexes
print('OK', tables, indexes)
"
```

### Level 6 — MANUAL_VALIDATION

1. With a real Sentinel install and `EXTRACTION_ENABLED=1`:
   ```bash
   sentinel learning extract --days 30 --dry-run
   ```
   Confirm: clusters considered/accepted/rejected counts make sense relative to `sentinel postmortems list`.

2. Drop the `--dry-run`:
   ```bash
   sentinel learning extract --days 30
   sentinel learning list --status probation
   ```
   Confirm: at least one probation row, with confidence in [50, 95].

3. Bring `OVERLAY_PROPOSER_ENABLED=1` AND a real `sentinel.repo_project_path` config; check out a clean local copy of the Sentinel repo:
   ```bash
   sentinel learning propose --scope drupal --min-confidence 80 --dry-run
   ```
   Confirm: dry-run output includes the rule ID, branch name, and which overlay file would change.

4. Real run:
   ```bash
   sentinel learning propose --scope drupal --min-confidence 80
   ```
   Confirm: GitLab now shows a draft MR; the MR description quotes postmortem rows; the branch contains a single overlay-file commit; `sentinel learning list` shows `proposed_overlay_mr_url` populated.

5. Maintainer merges in GitLab, then:
   ```bash
   sentinel learning mark-merged <rule_id> --sha <merge-sha> --by <username>
   ```
   Confirm: status flips to `active`; `promoted_*` columns populated.

6. Revocation drill:
   ```bash
   sentinel learning revoke <rule_id> --by <username> --reason "test revocation"
   ```
   Confirm: status flips to `revoked`; row still exists (`SELECT * FROM feedback_rules WHERE id=<rule_id>` returns it); next `sentinel learning list --status revoked` shows it.

---

## Acceptance Criteria

- [ ] **D4 resolved** in `docs/agent-learning-from-feedback-DECISIONS.md` — Status `Accepted`, body documents Sentinel-repo target + Sentinel-maintainer approver + always-draft + never-auto-merge.
- [ ] **Migration `004_feedback_rules.sql` lands** with the partial unique index and the secondary status index. Idempotent.
- [ ] **Persistence helpers** export `upsert_rule`, `query_promotable`, `list_rules`, `mark_proposed`, `mark_promoted`, `revoke_rule`, `mark_superseded`. **Do NOT** export `update_rule` or `delete_rule`.
- [ ] **Extraction**: `extract_clusters` clusters by signature, derives project key from ticket prefix, computes confidence per Appendix C.6 (clamped 95), rejects pure-symptom signatures. Idempotent on re-run.
- [ ] **Whack-a-mole guardrail** is exercised by tests; rejection rationale is logged at WARN.
- [ ] **Proposer**: opens a `draft=True` MR against the configured Sentinel repo path; commits a single overlay edit with provenance trailer per bullet; updates `proposed_overlay_mr_url`/`proposed_at` on success; never un-drafts.
- [ ] **CLI** `sentinel learning {extract, propose, mark-merged, revoke, list}` works as documented; `--dry-run` exists on extract and propose.
- [ ] **Feature flags** `EXTRACTION_ENABLED` and `OVERLAY_PROPOSER_ENABLED` default off; commands gated correctly; flag-off paths exit cleanly without side effects.
- [ ] **Append-only revocation**: `revoke_rule` flips status; row still exists; `mark_superseded` chains old → new; partial unique index allows post-supersede inserts of the same `(scope, agent, signature)`.
- [ ] **Events** `FeedbackRuleExtracted`, `FeedbackRulePromoted`, `FeedbackRuleRevoked` are published and round-trip through the bus.
- [ ] **Phase 2A loader is unchanged**. Phase 2A integration tests still pass with no modification.
- [ ] **Exit-criterion integration test** `tests/integration/test_phase2c_promotion.py` passes end-to-end.
- [ ] **Supersede-chain integration test** passes.
- [ ] **No regressions** — Level 3 full suite green.
- [ ] **Reviewer sign-off** — `sentinel-learning-reviewer` (per `.claude/agents/sentinel-learning-reviewer.md`) and `sentinel-persistence-expert` (`004_feedback_rules.sql` is in their owned-files list per `.claude/agents/sentinel-persistence-expert.md`) approve before merge.

---

## Risks and Mitigations

| Risk                                                                                                              | Likelihood | Impact | Mitigation                                                                                                                                                                                                                                                            |
| ----------------------------------------------------------------------------------------------------------------- | ---------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Memory poisoning** — extraction promotes a low-quality cluster                                                 | MED        | HIGH   | (1) Confidence floor 80 for proposer; (2) human-gated MR merge (D4); (3) whack-a-mole filter; (4) append-only revocation with `revoke_rule`; (5) `OVERLAY_PROPOSER_ENABLED=0` until exit-criterion fixture green; (6) reviewer-agent sign-off explicitly required.    |
| **Whack-a-mole fixes** — extracted rules capture symptoms, not root causes (user's CLAUDE.md says "don't")         | MED        | HIGH   | `is_pure_symptom` blacklist in `extract.py`; tested with multiple symptom phrases; rejected count printed in extraction summary so operators notice. Documented in extractor's docstring with the design-doc §9 reference.                                            |
| **Proposer pushes to the wrong repo** — config `repo_project_path` typo points at a project repo                  | LOW        | HIGH   | (1) Config validation: `repo_project_path` must match `GitLabClient.extract_project_path(<sentinel-git-url>)` — assert in CLI before calling proposer; (2) MRs are always `draft=True`; (3) tests assert the `project_id` argument matches the configured value.       |
| **Append-only invariant violated** — a future helper adds an UPDATE/DELETE to feedback_rules                     | LOW        | HIGH   | Static export test in `test_feedback_rules_helpers.py` (`assert 'update_rule' not in dir(module)`); reviewer-agent policy.                                                                                                                                            |
| **Supersede chain bug** — `mark_superseded` leaves the old row in `('probation','active')` mid-transaction       | LOW        | MED    | Both UPDATEs in one explicit `BEGIN IMMEDIATE / COMMIT`; integration test exercises the chain.                                                                                                                                                                        |
| **Race between concurrent extract runs**                                                                          | LOW        | MED    | UPSERT with the partial unique index serializes; SQLite WAL + `BEGIN IMMEDIATE` prevent lost updates. Documented as "extraction is single-writer; running two concurrently is supported but not exercised."                                                          |
| **Project-key derivation from ticket_id is wrong for non-Jira-style projects**                                    | MED        | LOW    | Empty `project_key` is filtered out before the `min_projects` check; documented in `query_postmortem_clusters` docstring; clusters from such tickets are never promoted. Resolution comes when project-scoped rules land in a future phase.                            |
| **Overlay file growth (D5 risk: prompt drift)**                                                                  | MED        | MED    | (1) Proposer appends to a single `## Auto-promoted pitfalls` section, NOT scattered through the file (so reviewers can scan one block); (2) `sentinel-learning-reviewer` agent's spec requires overlay-size scrutiny per D5; (3) Phase 2A's 8K char budget guards prompt-build time. |
| **Sentinel repo doesn't have a configured GitLab remote**                                                         | LOW        | LOW    | The proposer does an explicit `git remote get-url origin` check against the configured `sentinel.repo_project_path` before pushing; mismatch → exit 1 with a helpful error.                                                                                            |
| **Phase 2A injection contradicts a freshly-promoted overlay rule**                                                 | LOW        | MED    | When a rule is promoted, the overlay file change is the *durable* version; the `postmortems` rows that justified it remain. Phase 2A's loader injects from `postmortems` at confidence ≥ 70; the overlay's bullet appears separately. Overlap is OK — both say the same thing. The risk is real only if postmortems are NOT-yet-revoked-but-the-overlay-was-edited-to-disagree; that requires a maintainer to break the contract manually. |
| **Reviewer fatigue from auto-MRs**                                                                                | MED        | MED    | Threshold tuning (default 80, plus `≥3 obs` AND `≥2 projects`) is conservative; flag-off-by-default ships safe; the operator explicitly opts in by setting `OVERLAY_PROPOSER_ENABLED=1`.                                                                              |
| **Backwards-incompatible `feedback_rules` column changes** (a future appendix-C-richer phase widens the schema) | LOW        | LOW    | Phase 2C's columns are a strict subset of Appendix C.3. A future migration `005_feedback_observations.sql` adds the observation table without modifying `feedback_rules` columns. Documented in §NOT Building.                                                         |

---

## Notes

### Why we don't yet introduce `feedback_observations`

Phase 2C's source of evidence is the existing `postmortems` table, not raw MR comments. Postmortems already carry `failure_signature`, `execution_id`, `agent`, `stack_type`, `created_at` — enough to cluster and compute confidence per Appendix C.6 (subset). Introducing `feedback_observations` would also require:
- Reviewer username + display name capture (no current code path captures these)
- `gitlab_project_path` + `mr_iid` + `mr_note_id` ingestion (no current ingestion path)
- A `FeedbackDistiller` LLM agent (separate config, separate model gate per D2)

That's a 2× scope expansion. The doc itself, in §8 Phase 2C, restricts the file list to `src/core/learning/extract.py`, `scripts/propose-overlay-pr.py`, `src/core/persistence/postmortems.py`, and tests. We honor that boundary. When `feedback_observations` lands, the extractor's source SELECT switches from `postmortems` to a UNION of postmortems + observations; the rest of the pipeline stays.

### Why the proposer is a Click subcommand, not a `scripts/` standalone

The doc names `scripts/propose-overlay-pr.py`. A standalone script would (a) need its own logging setup, (b) need to re-resolve config that the CLI already wires, (c) duplicate the existing `connect()` + `apply_migrations()` boilerplate, and (d) require a fresh `pyproject.toml` `[tool.poetry.scripts]` entry. None of that earns its keep. A subcommand is the same code, in the same place, runnable with `sentinel learning propose ...`. If a future operator wants `scripts/propose-overlay-pr.py`, they wrap a one-line subprocess call to `sentinel learning propose --scope drupal`.

### Why the partial unique index on `(scope, agent_target, signature)`

Two competing requirements:
1. Dedup live rules — only one probation-or-active row per `(scope, agent_target, signature)`.
2. Allow `superseded_by` chains — the OLD row stays after the NEW row is inserted.

A full unique index satisfies (1) but breaks (2). A non-unique index satisfies (2) but breaks (1). Partial unique with `WHERE status IN ('probation','active')` does both: only live rows participate in the uniqueness constraint, so a superseded predecessor with the same key coexists with its successor.

This is the same trick `src/core/persistence/postmortems.py` doesn't use because postmortems don't dedupe — every cap-out gets its own row. `feedback_rules` is the canonical-rule store and DOES need dedup, so the partial unique index is the right tool.

### Why we skip the `feedback_rules → prompt_loader` wiring in 2C

Phase 2A reads `postmortems` and injects pitfalls. Phase 2C writes `feedback_rules` and proposes overlay PRs. **Wiring `feedback_rules` into the loader is intentionally a separate phase.** Two reasons:

1. **Two read paths are confusing.** If 2C wired both, the loader would have two queries (`query_active_postmortems` + `query_active_rules`) and two render contracts. Combining them is its own design problem — we get to the right answer faster by shipping each independently.
2. **The overlay file is the rule's home once promoted.** The doc puts the rule into `prompts/overlays/drupal_*.md` precisely so the planner already sees it via the existing overlay-loading path (`src/agents/plan_generator.py:285-330`). A `feedback_rules` injection would be a *third* pipe — we don't need it until probation rules need to reach the planner before promotion. That's a future phase if we determine it's worth the prompt-budget cost.

So Phase 2C's promoted rules reach the planner via the `prompts/overlays/*.md` file edit (already loaded), NOT via a new DB query in `prompt_loader.py`. This keeps the read surface identical to Phase 2A.

### Confidence floor 80 for the proposer

Phase 2A injects at confidence ≥ 70 (`src/prompt_loader.py:90`). Phase 2C promotes at confidence ≥ 80. The 10-point gap is intentional:

- Rules in [70, 79] *get injected* as pitfalls, but don't trigger an MR. This is the "probation but not yet stack-wide" zone.
- Rules at ≥ 80 trigger the proposer. The MR is a *human gate*, not an auto-merge.

If the human merges, status flips to `active`; the rule lives in the overlay file durably. If the human revokes, `revoke_rule` fires; the rule is excluded from injection (Phase 2A's `query_active_postmortems` already filters by `superseded_by IS NULL`, but not by `feedback_rules.status='revoked'` — that's fine because Phase 2A reads postmortems, not feedback_rules; a revoked feedback_rules row leaves its source postmortems untouched. If the operator wants to suppress the underlying postmortems too, that's a separate `mark_superseded`-on-postmortems action — out of 2C scope but the helper exists in 2A's persistence module).

### Reviewer invocation

Per `.claude/agents/sentinel-persistence-expert.md`: `004_feedback_rules.sql` is in the persistence expert's owned files. Per `.claude/agents/sentinel-learning-reviewer.md`: this PR touches the learning subsystem and must run the learning-reviewer agent before merge. The implementing agent must:
1. Spawn `sentinel-persistence-expert` for review of the migration + `feedback_rules.py` helpers — explicit ask: "verify Decision 4 (append-only) is honored; verify the partial unique index is correct."
2. Spawn `sentinel-learning-reviewer` for review of `extract.py` + `propose_overlay.py` + the D4 update — explicit ask: "verify the whack-a-mole guardrail; verify the proposer always opens draft=True; verify the provenance trailer carries postmortem IDs."

### What lands "for free" once 2C ships

- `sentinel learning list --status probation` becomes the operator's daily smoke test for "is the system learning?"
- The `events` table now carries `FeedbackRuleExtracted` and `FeedbackRulePromoted` per execution, so a `sqlite3 ~/.sentinel/sentinel.db 'SELECT * FROM events WHERE type LIKE "FeedbackRule%" ORDER BY ts DESC LIMIT 20'` shows the learning timeline.
- The provenance trailer in committed overlay bullets is `git blame`-friendly: `git blame prompts/overlays/drupal_developer.md` shows which auto-promote PR landed each line.

### Future seams left intentionally undone

- **`feedback_observations` table.** Schema is ready (Appendix C.3); migration would be `005_feedback_observations.sql`. Extraction's source SELECT widens at that point.
- **Distiller LLM step.** `rule_text` in 2C is the raw `failure_signature`. Phase 2D introduces the FeedbackDistiller (Haiku, temp=0) per D2.
- **`project:<KEY>` scope.** Project-scoped rules live in `.sentinel/project-rules.md` per Appendix D. Out of 2C scope.
- **Outcome-weighted confidence.** Merge-vs-revert from Phase 3.
- **`sentinel rules` CLI surface (Appendix C.7).** The richer audit commands (`rules show`, `rules active-at`, `rules search`) are deferred — `sentinel learning list` covers the 2C minimum.
- **CI scheduler.** Operators wire their own cron for now; a `scripts/cron.d/` template is Phase 3 if the user-base demands it.
