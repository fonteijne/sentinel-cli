# Feature: Phase 3 — Cautious Autonomy (Outcome Ingestion + Skill Library)

## Summary

Close the learning loop with ground-truth production outcomes. Sentinel runs locally with no inbound network path, so outcome ingestion is **pull-on-demand**: at the start of every `sentinel plan` / `sentinel execute` and via an explicit `sentinel outcomes sync` CLI, we query the GitLab MR and pipeline APIs for activity since a per-project watermark and tag prior `execution_id`s as `success | rolled_back | regressed`. Those tags feed an **outcome-weighted confidence** bump/decay on `feedback_rules`, and a nightly job promotes the highest-signal, most-reinforced postmortem fixes to **subagent skills** (YAML commands under `commands/<agent>/`) Voyager-style — human-curated, not autonomous.

## User Story

As a Sentinel maintainer
I want merge/revert/post-merge-CI outcomes to automatically raise or lower rule confidence, and proven recurring fixes to become first-class subagent skills
So that Sentinel's knowledge base is grounded in what *actually shipped and stuck in main* rather than what an LLM-judge thought was right at plan time

## Problem Statement

After Phase 2, Sentinel has a `feedback_rules` ledger with confidence scores derived from *observation counts* (how many reviewers said the same thing). This is a proxy signal — humans can agree on something that turns out to be wrong, and a rule that fired during a plan might be silently contradicted by the fact that the MR got reverted next day. Without outcome feedback the confidence number drifts from reality. Equally, the postmortem table accumulates *fixes* but nothing promotes the repeatedly-successful ones into reusable capabilities the developer agent can invoke explicitly — every ticket re-derives the same fix from scratch.

Concretely: after Phase 2 ships, we cannot answer (a) "did the MR Sentinel built actually merge and stay merged?", (b) "did post-merge CI on `main` regress?", (c) "which postmortem fixes are *so* durable they deserve to be skill-lifted?". Phase 3 closes all three.

## Solution Statement

Five deliverables, strictly dependent on Phase 1 + Phase 2 code landing first:

1. **`GitLabClient` extensions**: `list_merged_mrs_since(project, since_iid)`, `list_pipelines_for_commit(project, sha)`, `get_merge_request(project, iid)` — additive methods mirroring the existing `requests.Session`-based style.
2. **`OutcomeSyncService`**: polls GitLab, tags executions (`success | rolled_back | regressed`), maintains a `project_sync_state(project, last_synced_at, last_seen_mr_iid)` SQLite row per Sentinel installation (per D6), emits `OutcomeRecorded` events. Invoked at the start of `plan` / `execute` and by `sentinel outcomes sync`.
3. **Outcome-weighted confidence**: a `recompute_confidence_for_rule(rule_id)` pass that incorporates the outcome of every execution each observation came from, bumping on `success`, decaying on `rolled_back`, decaying harder on `regressed`, bounded by §C.6's formula.
4. **`sentinel outcomes sync [--project X] [--since DATE] [--all]` CLI** — Click command that drives `OutcomeSyncService` for explicit backfill after long gaps or a fresh install.
5. **Skill library promotion** — a nightly `propose_skills.py` script that reads `postmortems` joined with `executions.outcome='success'`, finds clusters of ≥ K applications of the same fix across ≥ M projects, and opens a PR adding a YAML command file under `commands/<agent>/<slug>.yaml`. Promotion is always human-gated via PR; the skill files are just markdown-ish YAML and reverting is `git rm`.

All five sit behind `OUTCOME_SYNC_ENABLED=false` (default off until data accrues) and feature-flag-free `SKILL_PROMOTION_ENABLED=false`. Nothing writes to weights; every artifact is a file or a SQLite row.

## Metadata

| Field            | Value                                                                                             |
| ---------------- | ------------------------------------------------------------------------------------------------- |
| Type             | NEW_CAPABILITY (extends the learning system)                                                      |
| Complexity       | HIGH                                                                                              |
| Systems Affected | `src/gitlab_client.py`, `src/cli.py`, `src/core/persistence/migrations/`, `src/core/events/types.py`, `src/core/learning/` (new), `src/prompt_loader.py`, `commands/`, `config/config.yaml` |
| Dependencies     | **Phase 1 merged** (postmortems table, `src/core/*` scaffold, structured events). **Phase 2 merged** (`feedback_rules`, `feedback_observations`, distiller, `sentinel rules` CLI, retrieval). `python-gitlab` is NOT used today — continue with `requests.Session`. |
| Estimated Tasks  | 18                                                                                                |

---

## Prerequisites — Phase 1 + Phase 2 must be complete

**Reviewer MUST block this plan from starting if any of these are missing.** Per the existing exploration, the sentinel repo's tracked source tree does not yet contain `src/core/` at all — Phase 1 is the session that introduces it.

- [ ] `src/core/persistence/db.py` exists and exposes a connection / migration runner.
- [ ] `src/core/persistence/migrations/001_init.sql` (executions, events, agent_results) applied.
- [ ] `src/core/persistence/migrations/003_postmortems.sql` applied (Phase 1).
- [ ] `src/core/persistence/migrations/004_feedback_rules.sql` applied (Phase 2; creates `feedback_rules` + `feedback_observations`).
- [ ] `src/core/events/types.py` exists with `PostmortemRecorded`, `FeedbackObservationRecorded`, `FeedbackRulePromoted`, `DeveloperCappedOut`, `FindingPosted`.
- [ ] `src/core/events/bus.py` exists with persist-then-publish semantics.
- [ ] `src/core/execution/post_execute.py` exists with the post-execute hook point.
- [ ] `sentinel rules {show,list,search,active-at,supersede,revoke}` CLI is live.
- [ ] Phase 2 retrieval (`prompt_loader` injects rules with IDs) is live.
- [ ] Phase 2 exit criterion met: ≥ 1 postmortem-driven overlay PR has been merged and demonstrably reduced a blocker rate on a replay (handover §7).

If any box is unchecked, **stop and file a Phase 1 or Phase 2 follow-up issue; do not attempt Phase 3**.

---

## UX Design

### Before State

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║                            END OF PHASE 2 (current)                              ║
╠══════════════════════════════════════════════════════════════════════════════════╣
║                                                                                  ║
║   Jira ticket ──► sentinel plan ──► plan.md ──► sentinel execute ──► MR ──► push║
║                        │                              │                          ║
║                        ▼                              ▼                          ║
║                 feedback_rules ◄── reviewer comments ─┤                          ║
║                 (confidence from obs count only)      │                          ║
║                                                       │                          ║
║                        ┌── postmortems ◄── cap-outs ──┤                          ║
║                        │                                                         ║
║                        ▼                                                         ║
║                  overlay PRs (manual human promotion, Phase 2)                   ║
║                                                                                  ║
║   BLIND SPOTS:                                                                   ║
║     · Did the MR MERGE?             Sentinel doesn't know.                       ║
║     · Did main REGRESS post-merge?  Sentinel doesn't know.                       ║
║     · Was the MR REVERTED?          Sentinel doesn't know.                       ║
║     · Rule confidence is a proxy (observations ≠ correctness).                   ║
║     · Recurring high-quality fixes never get lifted to reusable skills.          ║
║                                                                                  ║
╚══════════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║                         PHASE 3 — CAUTIOUS AUTONOMY                              ║
╠══════════════════════════════════════════════════════════════════════════════════╣
║                                                                                  ║
║   sentinel plan/execute ─┬─► [start-of-run hook]                                 ║
║                          │      if OUTCOME_SYNC_ENABLED:                         ║
║                          │         OutcomeSyncService.sync_project(key)          ║
║                          │            │                                          ║
║                          │            ▼                                          ║
║                          │     GitLab API polled since last_seen_mr_iid          ║
║                          │            │                                          ║
║                          │            ▼                                          ║
║                          │     executions.outcome ← {success,rolled_back,        ║
║                          │                           regressed}                  ║
║                          │            │                                          ║
║                          │            ▼                                          ║
║                          │     OutcomeRecorded event                             ║
║                          │            │                                          ║
║                          │            ▼                                          ║
║                          │     recompute_confidence_for_rules_touched(...)       ║
║                          │            │                                          ║
║                          │            ▼                                          ║
║                          │     feedback_rules.confidence  adjusted               ║
║                          │     feedback_rules.outcome_weight  set                ║
║                          ▼                                                       ║
║   ...normal pipeline...                                                          ║
║                                                                                  ║
║   sentinel outcomes sync [--project X] [--since D] [--all]   ← explicit backfill ║
║                                                                                  ║
║   nightly/weekly: propose_skills.py ── joins postmortems × executions.outcome    ║
║                      │                                                           ║
║                      └──► PR adding commands/<agent>/<slug>.yaml  ←  human gate  ║
║                                                                                  ║
║   DATA:                                                                          ║
║     · project_sync_state(project, last_synced_at, last_seen_mr_iid)              ║
║     · executions.outcome {pending|success|rolled_back|regressed}                 ║
║     · executions.outcome_observed_at                                             ║
║     · feedback_rules.outcome_weight  (double, default 1.0)                       ║
║     · feedback_rules.confidence  (re-derived to include outcome_weight)          ║
║                                                                                  ║
║   GATES / KILL SWITCHES:                                                         ║
║     · OUTCOME_SYNC_ENABLED=false   → sync CLI is a no-op; start-of-run hook skips║
║     · SKILL_PROMOTION_ENABLED=false → propose_skills.py exits 0 without acting   ║
║     · Skill YAML files are plain text; rollback = `git rm commands/<agent>/x.yaml`║
║                                                                                  ║
╚══════════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location | Before | After | User Impact |
|----------|--------|-------|-------------|
| `sentinel plan` / `sentinel execute` startup | Starts plan/execute immediately | Opportunistic outcome sync (≤ 2s P95; skipped if `OUTCOME_SYNC_ENABLED=false`) | Prior MRs get tagged before this run's prompt is built — learned confidence reflects reality |
| `sentinel outcomes sync` | *(doesn't exist)* | New command; `--project`, `--since`, `--all` args | Operators can backfill after a long hiatus or for a fresh install |
| Agent prompts (developer / planner) | `[rule:17, drupal, active, conf 85]` | `[rule:17, drupal, active, conf 85, outcome_weight 1.15]` — weight visible | Agent has dim signal about whether the rule's advice historically shipped clean |
| `commands/<agent>/` | 4 static YAML skills (Phase 0) | Up to N additional auto-promoted YAMLs | Developer agent can invoke `fix-hook-update-signature`-style skills directly rather than re-deriving the fix |
| MR comments | Loop A silent, Loop C = one line (per D8) | **Unchanged** — Phase 3 adds zero new MR-comment surfaces | No reviewer-attention erosion |

---

## Mandatory Reading

**CRITICAL: Implementation agent MUST read these files before starting any task.**

| Priority | File | Lines | Why Read This |
|----------|------|-------|---------------|
| P0 | `docs/agent-learning-from-feedback-2026-05-03.md` | §5, §8, §10, Appendices C–E | Design source of truth. §10 Phase 3 paragraph is binding. Appendix C.6 defines the confidence formula. |
| P0 | `docs/agent-learning-from-feedback-DECISIONS.md` | D1–D8 | Settled decisions. **D6** (per-installation watermark) and **D7/D8** (MR comment policy) bind Phase 3. |
| P0 | `docs/agent-learning-from-feedback-HANDOVER.md` | §4, §6, §7 | 10 invariants; Phase 3 agent roster; Phase 2 exit criteria (gate to Phase 3). |
| P0 | `src/gitlab_client.py` | 40-59, 209-239, 285-381 | Client construction, `raise_for_status` discipline, URL encoding pattern, existing methods to mirror. |
| P0 | `src/cli.py` | 54-89, 443-502 | Click group + `@cli.command` + options pattern for the new `outcomes sync` subgroup. |
| P1 | `src/agents/plan_generator.py` | 1139-1237 | `_detect_plan_state()` — the canonical pull-based polling precedent. |
| P1 | `src/agents/plan_generator.py` | 285-390 | `.sentinel/project-context.md` caching — precedent for per-project artifact discipline. |
| P1 | `src/config_loader.py` | 111-150, 152-186 | `get_env()` + YAML precedence; how feature flags should be surfaced (or not). |
| P1 | `src/core/persistence/migrations/001_init.sql` | all | Schema conventions for new migrations. **Assumes Phase 1 created this file.** |
| P1 | `src/core/persistence/migrations/004_feedback_rules.sql` | `feedback_rules`, `feedback_observations` | Exact columns to join against. **Assumes Phase 2 created this file.** |
| P1 | `commands/python_developer/implement-tdd.yaml` | all | Existing skill-file shape — promotion target format. |
| P1 | `src/command_executor.py` | 58-253 | How YAML commands are loaded/validated — what the promoter must produce. |
| P2 | `src/session_tracker.py` | 64-75 | Lightweight per-project-JSON precedent (fallback if SQLite is unavailable mid-run). |
| P2 | `tests/test_gitlab_client.py` | 1-100 | Canonical test structure; `MagicMock(spec=ConfigLoader)` pattern. |

**External Documentation:**

| Source | Section | Why Needed |
|--------|---------|------------|
| [GitLab REST API — List merge requests](https://docs.gitlab.com/ee/api/merge_requests.html#list-merge-requests) | query params `state`, `updated_after`, `order_by`, `sort` | Backend for `list_merged_mrs_since`. Note: GitLab paginates with `X-Next-Page` header. |
| [GitLab REST API — Pipelines](https://docs.gitlab.com/ee/api/pipelines.html#list-project-pipelines) | query params `sha`, `ref`, `status` | Backend for `list_pipelines_for_commit` + post-merge `main` regression detection. |
| [GitLab REST API — Commits (`get referenced-by`)](https://docs.gitlab.com/ee/api/commits.html) | `GET /projects/:id/repository/commits/:sha/refs` | Needed to detect "this MR's merge commit was reverted" — search later commits whose message contains `Revert "<subject>"`. |
| [Karpathy on System Prompt Learning](https://x.com/karpathy/status/1921368644069765486) | full post | Framing: the leash gets *longer* with evidence, not looser. Outcome signals *extend* the leash. |
| [Voyager skill library](https://voyager.minedojo.org/) | Skill Library section | The reusable-skill idea; we keep it human-curated (not auto-executed into the library). |
| [Lakera — Agentic AI threats, memory poisoning](https://www.lakera.ai/blog/agentic-ai-threats-p1) | full page | Why outcome signals must *only decay on revert*, never silently elevate — protects the postmortem index from being fooled by a fast-merge-then-revert cycle. |

---

## Patterns to Mirror

**CLI subcommand group (Click):**

```python
# SOURCE: src/cli.py:54-89, 443-502
# COPY THIS PATTERN:
@cli.command()
@click.argument("ticket_id")
@click.option(
    "--project", "-p",
    help="Project key (e.g., ACME). If not provided, extracted from ticket ID.",
)
@click.option(
    "--force", is_flag=True,
    help="Skip confidence evaluation (no report generated)",
)
def plan(ticket_id: str, project: Optional[str] = None, force: bool = False) -> None:
    """Generate implementation plan for a Jira ticket."""
    try:
        if project is None:
            project = ticket_id.split("-")[0]
        # ... handler logic
```

Phase 3 target: a `@cli.group("outcomes")` with a `sync` subcommand mirroring the same arg/option style.

**GitLab API method (additive, matches existing style):**

```python
# SOURCE: src/gitlab_client.py:209-239
# COPY THIS PATTERN (construction, URL-encode, session call, raise_for_status, typed return):
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

New methods added in this plan must match this shape exactly (no `python-gitlab`, no dataclass wrappers yet).

**Pull-based polling precedent:**

```python
# SOURCE: src/agents/plan_generator.py:1139-1237
# COPY THIS PATTERN (fetch state, branch on change, return structured dict):
def _detect_plan_state(self, ticket_id: str, worktree_path: Path, project_key: str,
                      ctx: TicketContextBuilder | None = None) -> Dict[str, Any]:
    plan_path = worktree_path / ".agents" / "plans" / f"{ticket_id}.md"
    if not plan_path.exists():
        return {"state": "initial"}

    existing_plan = plan_path.read_text()
    mr_info = self._get_mr_info(ticket_id, project_key)
    if not mr_info:
        return {"state": "initial", "existing_plan": existing_plan}

    # ... branches on discussions / new comments ...

    return {"state": "nothing_changed", "existing_plan": existing_plan, ...}
```

`OutcomeSyncService.sync_project()` mirrors this: fetch watermark → poll GitLab → classify outcomes → `return {"state": ..., "tagged": [...], "skipped_reason": ...}` — never exceptions for expected no-op branches.

**Feature flag (raw env var):**

```python
# SOURCE: src/config_loader.py:111-150 (get_env pattern) + ad-hoc usage elsewhere
# COPY THIS PATTERN:
import os

def _is_outcome_sync_enabled() -> bool:
    return os.environ.get("OUTCOME_SYNC_ENABLED", "false").lower() in ("true", "1", "yes")
```

No new YAML config section; this matches existing practice (Sentinel already uses raw `os.environ.get` for several runtime toggles in `agent_sdk_wrapper.py` and `plan_generator.py`).

**YAML skill-file target (the thing the promoter writes):**

```yaml
# SOURCE: commands/python_developer/implement-tdd.yaml
# COPY THIS PATTERN (for skill promotion output):
name: implement-tdd
description: Execute complete TDD cycle for a Python feature
version: 1.0

parameters:
  feature_description:
    type: string
    required: true
    description: Description of the feature to implement

workflow:
  - name: write_failing_test
    description: Create a test that fails (RED phase)
    actions:
      - create_test_file: tests/test_{feature_name}.py
      - run_tests: pytest tests/test_{feature_name}.py
      - verify: "Tests should FAIL at this stage"

configuration:
  test_framework: pytest

quality_gates:
  - all_tests_passing: true
```

The promoter emits the same schema, with `name` = postmortem slug, `description` = fix summary, `workflow` derived from the fix's canonical action list (stored in `postmortems.fix_steps_json` — a field **Phase 1 must include**, see "Phase 1 dependency notes" below).

**Test structure:**

```python
# SOURCE: tests/test_gitlab_client.py:1-100
# COPY THIS PATTERN:
from unittest.mock import MagicMock, Mock, patch
import pytest

from src.config_loader import ConfigLoader
from src.gitlab_client import GitLabClient


@pytest.fixture
def mock_config():
    config = MagicMock(spec=ConfigLoader)
    config.get_gitlab_config.return_value = {
        "base_url": "https://gitlab.com",
        "api_token": "test_token",
    }
    return config


@pytest.fixture
def gitlab_client(mock_config):
    with patch("src.gitlab_client.get_config", return_value=mock_config):
        return GitLabClient()


class TestListMergedMrsSince:
    def test_returns_only_mrs_since_iid(self, gitlab_client):
        mock_response = Mock()
        mock_response.json.return_value = [
            {"iid": 10, "state": "merged", "merged_at": "2026-05-01T00:00:00Z"},
            {"iid": 11, "state": "merged", "merged_at": "2026-05-02T00:00:00Z"},
        ]
        with patch.object(gitlab_client.session, "get", return_value=mock_response):
            result = gitlab_client.list_merged_mrs_since("acme/backend", since_iid=11)
            assert len(result) == 1
            assert result[0]["iid"] == 11
```

---

## Files to Change

| File | Action | Justification |
|------|--------|---------------|
| `src/core/persistence/migrations/005_outcome_ingestion.sql` | CREATE | `project_sync_state` table + `outcome`, `outcome_observed_at` cols on `executions` + `outcome_weight` on `feedback_rules`. |
| `src/core/events/types.py` | UPDATE | Add `OutcomeRecorded`, `SkillPromoted`, `OutcomeSyncSkipped`. |
| `src/gitlab_client.py` | UPDATE | Additive: `list_merged_mrs_since`, `list_pipelines_for_commit`, `get_commit_refs`, `get_merge_request`. |
| `src/core/learning/outcome_sync.py` | CREATE | `OutcomeSyncService` — poll → classify → tag → emit. |
| `src/core/learning/outcome_confidence.py` | CREATE | `recompute_confidence_for_rule(rule_id)` using §C.6 + outcome weighting from §7.3 below. |
| `src/core/learning/skill_promoter.py` | CREATE | `propose_skills()` — queries postmortems × outcomes, clusters, emits a Git PR creating a `commands/<agent>/<slug>.yaml` file. |
| `src/cli.py` | UPDATE | Add `@cli.group("outcomes")` + `sync` subcommand + wire start-of-run opportunistic sync into `plan` / `execute`. |
| `src/prompt_loader.py` | UPDATE | When injecting rule bullets in the "Known pitfalls" section, include `outcome_weight` next to `confidence` (see §7.4). |
| `scripts/propose-skills.sh` | CREATE | Cron/ops wrapper around `python -m src.core.learning.skill_promoter`. |
| `config/config.yaml` | UPDATE | Document `OUTCOME_SYNC_ENABLED` and `SKILL_PROMOTION_ENABLED` env vars in comments; no functional change. |
| `tests/core/learning/test_outcome_sync.py` | CREATE | Unit tests. |
| `tests/core/learning/test_outcome_confidence.py` | CREATE | Unit tests for confidence recomputation. |
| `tests/core/learning/test_skill_promoter.py` | CREATE | Unit tests for clustering + YAML emission. |
| `tests/integration/test_outcomes_sync_cli.py` | CREATE | End-to-end CLI test with mocked GitLab. |
| `tests/test_gitlab_client.py` | UPDATE | Tests for the three new GitLab methods. |
| `docs/agent-learning-from-feedback-PHASE-3.md` | CREATE | Short operator-facing doc: what the flag does, how to backfill, how to roll back. |

---

## NOT Building (Scope Limits)

- **Webhooks.** Ruled out in design §10 — Sentinel has no inbound network path. Pull-on-demand is final.
- **Inbound webhook receiver or any long-running daemon.** Both the start-of-run hook and `sentinel outcomes sync` are short-lived processes.
- **A UI for outcome viewing.** `sentinel rules show <id>` already prints rules; operators can query the DB directly for outcomes in Phase 3.
- **Automatic skill execution.** The promoter only *proposes* skill files via a PR. It never writes them directly to `main`. There is no auto-commit path.
- **Cross-installation watermark coordination.** Per D6, each Sentinel installation holds its own watermark. No shared state, no advisory locks.
- **Vector search / embeddings for fix-clustering.** Use normalized `failure_signature` + `rapidfuzz.token_set_ratio ≥ 85` on `fix_summary` (same heuristic Phase 2 uses for rule dedup, Appendix C.5). Defer embeddings to a Phase 4 if signature-based clustering measurably caps out.
- **Letta / Mem0 / external memory service integration.** Design §8 task 18 explicitly gates this on "filesystem + SQLite measurably insufficient." Phase 3 does not take this step.
- **Re-weighting plans already in flight.** `executions.rules_snapshot_json` was frozen at plan start (Appendix E.7). Outcome recomputation affects *future* plans only — never in-flight ones.
- **Revocation of overlays based on regression.** If a regression traces to a bad overlay rule, this is a human decision via `sentinel rules revoke`. Phase 3 does not auto-revoke.
- **Fine-tuning.** Out-of-scope per every phase.

---

## Step-by-Step Tasks

Execute in dependency order. Each task ends with a concrete validation command. Tasks 1–6 are the migration + service core; 7–11 wire the CLI; 12–13 wire start-of-run sync; 14–15 wire confidence reweighting; 16–18 add the skill promoter.

### Task 1: CREATE `src/core/persistence/migrations/005_outcome_ingestion.sql`

- **ACTION**: New migration. Depends on 001, 003, 004.
- **IMPLEMENT**:
  ```sql
  -- Watermark: one row per GitLab project this installation has seen
  CREATE TABLE project_sync_state (
    project TEXT PRIMARY KEY,                -- GitLab project path, e.g. 'acme/backend'
    last_synced_at TEXT NOT NULL,            -- ISO8601 UTC
    last_seen_mr_iid INTEGER NOT NULL DEFAULT 0,
    last_seen_pipeline_id INTEGER NOT NULL DEFAULT 0,
    sync_error_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_error_at TEXT
  );

  -- Tag executions with ground-truth outcome
  ALTER TABLE executions ADD COLUMN outcome TEXT
    CHECK (outcome IN ('pending','success','rolled_back','regressed')) DEFAULT 'pending';
  ALTER TABLE executions ADD COLUMN outcome_observed_at TEXT;
  ALTER TABLE executions ADD COLUMN outcome_evidence_json TEXT;  -- {merge_commit_sha, revert_commit_sha, failing_pipeline_id, ...}

  -- Outcome weighting factor for rules
  ALTER TABLE feedback_rules ADD COLUMN outcome_weight REAL NOT NULL DEFAULT 1.0;
  ALTER TABLE feedback_rules ADD COLUMN outcome_weight_updated_at TEXT;

  CREATE INDEX idx_executions_outcome ON executions(outcome, outcome_observed_at);
  ```
- **MIRROR**: `src/core/persistence/migrations/001_init.sql` conventions (TEXT timestamps, integer PKs).
- **GOTCHA**: SQLite's `ALTER TABLE ADD COLUMN` cannot add `NOT NULL` without a default — `outcome` uses default `'pending'`.
- **VALIDATE**: `python -c "from src.core.persistence.db import apply_migrations; apply_migrations()"` — exit 0.

### Task 2: UPDATE `src/core/events/types.py`

- **ACTION**: Add three event types.
- **IMPLEMENT**: Three new dataclass events (mirroring existing `PostmortemRecorded`):
  - `OutcomeRecorded(execution_id, project, mr_iid, outcome, evidence)`
  - `SkillPromoted(skill_name, agent_target, source_postmortem_ids, pr_url)`
  - `OutcomeSyncSkipped(project, reason)` — emitted when `OUTCOME_SYNC_ENABLED=false` or when the sync hits an error budget
- **MIRROR**: Existing event dataclass shape; `type: str`, `timestamp: str`, `payload` fields must serialize cleanly through `bus.persist_then_publish`.
- **GOTCHA**: Keep payloads ≤ 4 KB — `events.payload_json` is queried frequently; don't stuff full GitLab response bodies in.
- **VALIDATE**: `pytest tests/core/events/test_types.py -q` — exit 0.

### Task 3: UPDATE `src/gitlab_client.py` — add `get_merge_request`

- **ACTION**: Additive method.
- **IMPLEMENT**:
  ```python
  def get_merge_request(self, project_id: str, mr_iid: int) -> Dict[str, Any]:
      project_path = project_id.replace("/", "%2F")
      url = f"{self.base_url}/api/v4/projects/{project_path}/merge_requests/{mr_iid}"
      response = self.session.get(url)
      response.raise_for_status()
      return response.json()
  ```
- **MIRROR**: `gitlab_client.py:209-239` (`list_merge_requests`).
- **VALIDATE**: `pytest tests/test_gitlab_client.py::TestGetMergeRequest -q`.

### Task 4: UPDATE `src/gitlab_client.py` — add `list_merged_mrs_since`

- **ACTION**: Additive method with pagination.
- **IMPLEMENT**: Call `GET /projects/:id/merge_requests?state=merged&order_by=updated_at&sort=asc&updated_after=:since_ts&per_page=100`. Follow `X-Next-Page` until exhausted or a hard cap (500). Client-side filter `mr["iid"] >= since_iid`.
- **MIRROR**: `gitlab_client.py:209-239`.
- **GOTCHA**: GitLab capped `per_page` at 100; honor `X-Next-Page`, not a naive `page+=1` loop. Give up after 5 consecutive 5xx with log+`OutcomeSyncSkipped`.
- **VALIDATE**: `pytest tests/test_gitlab_client.py::TestListMergedMrsSince -q`.

### Task 5: UPDATE `src/gitlab_client.py` — add `list_pipelines_for_commit` and `get_commit_refs`

- **ACTION**: Additive.
- **IMPLEMENT**:
  ```python
  def list_pipelines_for_commit(self, project_id: str, sha: str) -> List[Dict[str, Any]]:
      project_path = project_id.replace("/", "%2F")
      url = f"{self.base_url}/api/v4/projects/{project_path}/pipelines"
      response = self.session.get(url, params={"sha": sha})
      response.raise_for_status()
      return response.json()

  def get_commit_refs(self, project_id: str, sha: str, type_: str = "all") -> List[Dict[str, Any]]:
      """Ref-names containing this commit (needed to detect a merge landed on main)."""
      project_path = project_id.replace("/", "%2F")
      url = f"{self.base_url}/api/v4/projects/{project_path}/repository/commits/{sha}/refs"
      response = self.session.get(url, params={"type": type_})
      response.raise_for_status()
      return response.json()
  ```
- **MIRROR**: same as Task 3.
- **VALIDATE**: `pytest tests/test_gitlab_client.py::TestListPipelinesForCommit tests/test_gitlab_client.py::TestGetCommitRefs -q`.

### Task 6: CREATE `src/core/learning/outcome_sync.py` — `OutcomeSyncService`

- **ACTION**: New module. The core of Deliverable 2.
- **IMPLEMENT**:
  - Class `OutcomeSyncService(db, gitlab_client, event_bus)`.
  - `sync_project(project: str) -> SyncResult` — loads `project_sync_state`, calls `list_merged_mrs_since(project, last_seen_mr_iid + 1)`, resolves each returned MR to a prior `execution_id` via `executions.metadata_json->>'mr_iid'` match, classifies:
    - **success**: MR merged into the target branch AND no revert commit with subject `Revert "<MR title>"` found within 30 commits downstream AND no `failed` pipeline on the merge commit's ref.
    - **rolled_back**: a revert commit touching the merge-commit SHA exists.
    - **regressed**: `list_pipelines_for_commit(project, merge_sha)` returns a `failed` pipeline on the default branch.
  - Each classification writes `executions.outcome`, `executions.outcome_observed_at`, `executions.outcome_evidence_json` in one transaction, emits `OutcomeRecorded`, and bumps `project_sync_state.last_seen_mr_iid` monotonically.
  - On exception: increment `project_sync_state.sync_error_count`, stash `last_error`/`last_error_at`, emit `OutcomeSyncSkipped(reason=...)`, re-raise only if count > 3 in a row — otherwise swallow so `sentinel plan` / `execute` continue.
- **MIRROR**: `src/agents/plan_generator.py:1139-1237` structure for branching; `src/gitlab_client.py:40-59` construction.
- **GOTCHA**: The revert-detection heuristic (`git log --grep='Revert "<subject>"'` equivalent via GitLab's commits API) is imperfect. Store the exact rule that fired in `outcome_evidence_json` so failures can be re-audited later.
- **GOTCHA**: If `OUTCOME_SYNC_ENABLED` is false, `sync_project` short-circuits at the top and emits a single `OutcomeSyncSkipped(reason='flag_off')` once per invocation. No GitLab calls, no watermark update.
- **GOTCHA**: Reviewer Decision D6 — per-installation watermark. Don't parameterize by installation id; the SQLite DB file itself is the installation.
- **VALIDATE**: `pytest tests/core/learning/test_outcome_sync.py -q`.

### Task 7: CREATE `src/core/learning/outcome_confidence.py`

- **ACTION**: New module. Deliverable 3.
- **IMPLEMENT**:
  - `recompute_confidence_for_rule(rule_id: int) -> None` — joins `feedback_observations` × `executions`, computes:
    ```
    outcome_weight = clamp(
        1.0
        + 0.05 * count(success)
        - 0.15 * count(rolled_back)
        - 0.25 * count(regressed),
        0.3, 1.5
    )
    confidence = base_formula_from_appendix_C6(obs_stats) * outcome_weight
    confidence = clamp(confidence, 0, 95)
    ```
    (The coefficients above are the Phase 3 starting point — **tune from telemetry**. They are asymmetric on purpose: reverts and regressions weigh more than successes.)
  - Writes `outcome_weight`, `outcome_weight_updated_at`, `confidence`, `updated_at` to `feedback_rules`.
  - `recompute_all_touched_by_execution(execution_id)` — convenience entry point called by `OutcomeRecorded` subscriber.
- **MIRROR**: Appendix C.6 of the design doc (base confidence formula).
- **GOTCHA**: Never let `confidence` hit 100 — §C.6 invariant. Humans can be wrong.
- **GOTCHA**: Don't recompute for rules with `status IN ('revoked', 'superseded')` — those are frozen.
- **VALIDATE**: `pytest tests/core/learning/test_outcome_confidence.py -q` — must cover bump/decay/floor/ceiling.

### Task 8: UPDATE `src/cli.py` — add `@cli.group("outcomes")` with `sync` subcommand

- **ACTION**: CLI surface for Deliverable 4.
- **IMPLEMENT**:
  ```python
  @cli.group("outcomes")
  def outcomes_group() -> None:
      """Outcome ingestion commands (see docs/agent-learning-from-feedback-PHASE-3.md)."""

  @outcomes_group.command("sync")
  @click.option("--project", "-p", help="Project key (omit with --all).")
  @click.option("--since", type=str, help="ISO8601 lower bound (e.g. 2026-04-01).")
  @click.option("--all", "all_", is_flag=True, help="Sync every project in config.")
  def outcomes_sync(project: Optional[str], since: Optional[str], all_: bool) -> None:
      """Backfill MR merge / revert / pipeline outcomes from GitLab."""
      if not _is_outcome_sync_enabled():
          click.echo("OUTCOME_SYNC_ENABLED is not set. No-op.")
          sys.exit(0)
      # ... build service, loop over projects, print per-project summary
  ```
- **MIRROR**: `src/cli.py:54-89, 443-502`.
- **GOTCHA**: `--all` without `--project` iterates the projects listed in `config.yaml:projects`. Require one of `--project` or `--all`; error otherwise.
- **GOTCHA**: `--since` is honored by temporarily lowering the effective `last_seen_mr_iid` to zero and filtering by `updated_after`. Never *overwrite* the stored watermark with `since` — the watermark must stay monotonic.
- **VALIDATE**: `pytest tests/integration/test_outcomes_sync_cli.py -q`.

### Task 9: UPDATE `src/cli.py` — opportunistic sync on plan/execute startup

- **ACTION**: Wire the start-of-run hook.
- **IMPLEMENT**: At the top of the `plan` and `execute` command handlers, after project-key resolution and before any agent work:
  ```python
  if _is_outcome_sync_enabled():
      try:
          OutcomeSyncService(...).sync_project(project_path).summary().echo()
      except Exception as e:  # never let outcome sync break the main flow
          logger.warning(f"Outcome sync failed (non-fatal): {e}")
  ```
- **MIRROR**: existing early-path setup at `cli.py:54-89` where project key is extracted.
- **GOTCHA**: Must **never** raise — Phase 3 is strictly additive. Any failure here is logged-and-swallowed. Errors escalate via `project_sync_state.sync_error_count`.
- **GOTCHA**: P95 added latency target < 2 s. If GitLab is slow, the `list_merged_mrs_since` call takes a hard 5 s timeout and then abandons the sync for this run.
- **VALIDATE**: `pytest tests/integration/test_opportunistic_sync.py -q` — covers flag-off, flag-on-fast-path, flag-on-error-swallowed.

### Task 10: UPDATE `src/prompt_loader.py` — surface `outcome_weight`

- **ACTION**: Include outcome-weight in the "Known pitfalls" bullets Phase 2 injects.
- **IMPLEMENT**: In the existing Phase 2 formatter, replace the bullet template from `[rule:N, scope, status, conf X]` to `[rule:N, scope, status, conf X, outcome_weight W]` **only when W ≠ 1.0** (avoid adding noise for rules without outcome data).
- **MIRROR**: Phase 2's existing rule-rendering function (location: wherever Phase 2 put it — likely `src/core/learning/retrieval.py` — the Phase 2 reviewer must point the integrator at it).
- **GOTCHA**: Do not re-inject rules based on `outcome_weight` rank; Phase 2's ranking is the source of truth. `outcome_weight` is *annotation*, not a re-ranker. That's §E.4 Stage 2's territory and changing it needs a separate ADR.
- **GOTCHA**: `outcome_weight` is already baked into `confidence` after Task 7. Surfacing it separately is purely for operator observability. Do not double-apply.
- **VALIDATE**: `pytest tests/core/learning/test_retrieval.py::test_outcome_weight_annotation -q`.

### Task 11: CREATE `src/core/learning/skill_promoter.py`

- **ACTION**: Deliverable 5. Nightly job.
- **IMPLEMENT**:
  - `propose_skills(min_applications: int = 5, min_projects: int = 2, min_success_rate: float = 0.8) -> List[SkillProposal]`:
    1. Query `postmortems` where `fix_summary IS NOT NULL` (a resolved postmortem).
    2. Join with `executions.outcome` via the observations that reference each postmortem's fix.
    3. Cluster by normalized `failure_signature` (same heuristic as Appendix C.5; `rapidfuzz.token_set_ratio ≥ 85`).
    4. A cluster qualifies when:
       - `count(distinct execution_id) ≥ min_applications`
       - `count(distinct project) ≥ min_projects`
       - `count(outcome='success') / count(outcome IN ('success','rolled_back','regressed')) ≥ min_success_rate` (ignore `pending`)
       - No existing `commands/<agent>/<slug>.yaml` has the proposed slug.
    5. For each qualifying cluster, emit a `SkillProposal(agent_target, slug, title, description, workflow_steps, source_postmortem_ids)`.
  - `write_proposal_pr(proposal: SkillProposal) -> str` — creates a branch `skill-promote/<slug>`, writes `commands/<agent>/<slug>.yaml` from the YAML template (see "Patterns to Mirror"), pushes, opens a GitLab MR with a description that lists every postmortem + execution + outcome that contributed. Returns MR URL.
- **MIRROR**: `commands/python_developer/implement-tdd.yaml` format; `src/command_executor.py:58-253` validation.
- **GOTCHA**: If `SKILL_PROMOTION_ENABLED=false`, `propose_skills` still *returns* proposals (for inspection) but `write_proposal_pr` is a no-op that prints what it would have done.
- **GOTCHA**: The slug must be kebab-case + stack-prefixed (`drupal-fix-hook-update-signature`) to avoid collisions with future stacks.
- **GOTCHA**: If the promoter generates a YAML that fails `command_executor` validation, abort the PR (do not open a broken skill). Log `SkillPromoted{...status='invalid'}`.
- **VALIDATE**: `pytest tests/core/learning/test_skill_promoter.py -q`.

### Task 12: CREATE `scripts/propose-skills.sh`

- **ACTION**: Ops wrapper for cron / manual runs.
- **IMPLEMENT**:
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  cd "$(dirname "$0")/.."
  exec python -m src.core.learning.skill_promoter "$@"
  ```
- **VALIDATE**: `bash -n scripts/propose-skills.sh`.

### Task 13: CREATE `docs/agent-learning-from-feedback-PHASE-3.md`

- **ACTION**: Operator doc; NOT a design doc.
- **IMPLEMENT**: Short (≤ 150 lines). Cover: (a) what flags turn this on/off, (b) how to backfill after a long gap (`sentinel outcomes sync --project X --since 2026-01-01`), (c) how to read `sentinel rules show <id>` once outcomes exist (the `outcome_weight` annotation), (d) how to roll back (flag off + `git rm` for promoted skills), (e) data retention (observations never deleted; evidence JSON retained for audit).
- **VALIDATE**: manual read; reviewer pass.

### Task 14: CREATE tests — `tests/core/learning/test_outcome_sync.py`

- **ACTION**: Unit tests for the sync service.
- **CASES**:
  - Flag off → zero GitLab calls; emits one `OutcomeSyncSkipped`.
  - First run on empty DB → initializes `project_sync_state` at IID 0, paginates correctly.
  - Merged MR classifies as `success` (no revert, no failed pipeline).
  - Revert commit detected → `rolled_back`.
  - Failed pipeline on merge commit ref → `regressed`.
  - Retry/backoff: 3 consecutive 500s → `OutcomeSyncSkipped`, `sync_error_count=3`; main flow not interrupted.
  - Watermark never moves backwards, even with `--since` backfill.
- **MIRROR**: `tests/test_gitlab_client.py:1-100`.
- **VALIDATE**: `pytest tests/core/learning/test_outcome_sync.py -q`.

### Task 15: CREATE `tests/core/learning/test_outcome_confidence.py`

- **ACTION**: Unit tests for confidence recompute.
- **CASES**:
  - Pure successes → `outcome_weight` approaches 1.5 ceiling.
  - Pure reverts → `outcome_weight` floors at 0.3.
  - Mixed → symmetric asymmetry holds (1 revert outweighs 2 successes at the stated coefficients).
  - `pending` outcomes are ignored.
  - `revoked` / `superseded` rules are skipped.
  - Confidence never hits 100.
- **VALIDATE**: `pytest tests/core/learning/test_outcome_confidence.py -q`.

### Task 16: CREATE `tests/core/learning/test_skill_promoter.py`

- **ACTION**: Unit tests for skill promoter.
- **CASES**:
  - Below `min_applications` → no proposal.
  - Below `min_projects` → no proposal.
  - Success rate < threshold → no proposal.
  - All thresholds met + `SKILL_PROMOTION_ENABLED=true` → proposal written, MR opened, `SkillPromoted` event emitted.
  - All thresholds met + flag off → proposal returned, no MR, no file written.
  - Invalid generated YAML → abort, no branch pushed.
  - Slug collision with an existing YAML → skip silently, log.
- **VALIDATE**: `pytest tests/core/learning/test_skill_promoter.py -q`.

### Task 17: CREATE `tests/integration/test_outcomes_sync_cli.py`

- **ACTION**: CLI-level integration.
- **CASES**:
  - `sentinel outcomes sync` (no args) → error "one of --project or --all required".
  - `sentinel outcomes sync --project ACME` + mocked GitLab → prints per-project summary line.
  - `sentinel outcomes sync --all` → iterates projects.
  - Flag off → prints "OUTCOME_SYNC_ENABLED is not set. No-op." and exits 0.
- **VALIDATE**: `pytest tests/integration/test_outcomes_sync_cli.py -q`.

### Task 18: CREATE `tests/integration/test_opportunistic_sync.py`

- **ACTION**: End-to-end test that `plan` / `execute` run opportunistic sync without breaking on GitLab failure.
- **CASES**:
  - Flag off → no sync attempt, plan proceeds.
  - Flag on + GitLab OK → sync runs, plan proceeds, `OutcomeRecorded` emitted.
  - Flag on + GitLab 500 → sync swallows, plan proceeds, `OutcomeSyncSkipped` emitted.
  - Flag on + GitLab timeout (> 5 s) → sync abandoned within 5 s, plan proceeds.
- **VALIDATE**: `pytest tests/integration/test_opportunistic_sync.py -q`.

---

## Testing Strategy

### Unit Tests to Write

| Test File | Test Cases | Validates |
|-----------|-----------|-----------|
| `tests/test_gitlab_client.py` (extend) | new methods × happy/error | API surface |
| `tests/core/learning/test_outcome_sync.py` | 7 cases (Task 14) | Core poll/classify loop |
| `tests/core/learning/test_outcome_confidence.py` | 6 cases (Task 15) | Weighting math |
| `tests/core/learning/test_skill_promoter.py` | 7 cases (Task 16) | Clustering + YAML generation |
| `tests/integration/test_outcomes_sync_cli.py` | 4 cases (Task 17) | CLI wiring |
| `tests/integration/test_opportunistic_sync.py` | 4 cases (Task 18) | Startup hook |

### Edge Cases Checklist

- [ ] Project path rename on GitLab (watermark key still matches because we store path, not numeric id — fallback: store both).
- [ ] MR created by Sentinel but closed without merge → `pending` forever (never flips to `success`).
- [ ] Multiple reverts of the same merge commit → counted once.
- [ ] MR target branch ≠ `main` (e.g. `develop`) → classification still works because we check the actual target branch, not the literal string `main`.
- [ ] Squash merges (no merge commit, just a single commit on main) → need to match by MR `merge_commit_sha` when present, else fall back to `squash_commit_sha`.
- [ ] Rate limit response from GitLab (429) → back off per `Retry-After` header; record `OutcomeSyncSkipped(reason='rate_limited')`.
- [ ] Clock skew between Sentinel and GitLab → `outcome_observed_at` is Sentinel-local time, `last_synced_at` is GitLab-provided; document that these can differ.
- [ ] `--since` before the earliest MR on record → no error; reports "nothing to ingest."
- [ ] Two Sentinel instances racing for the same project → both update `project_sync_state` monotonically; last writer wins, no corruption (per D6, acceptable trade-off).
- [ ] Postmortem with no linked execution (manually inserted) → skill promoter excludes it from clustering.

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
cd /app && ruff check src/core/learning src/gitlab_client.py src/cli.py
cd /app && mypy src/core/learning src/gitlab_client.py src/cli.py
```

**EXPECT**: Exit 0, no errors. `mypy` config is `pyproject.toml:[tool.mypy]` — stay strict.

### Level 2: UNIT_TESTS

```bash
cd /app && pytest tests/core/learning/ tests/test_gitlab_client.py -q
```

**EXPECT**: All pass. Coverage ≥ 80% on `src/core/learning/outcome_sync.py` and `src/core/learning/outcome_confidence.py` (the two modules with non-trivial logic).

### Level 3: INTEGRATION_TESTS

```bash
cd /app && pytest tests/integration/test_outcomes_sync_cli.py tests/integration/test_opportunistic_sync.py -q
```

**EXPECT**: All pass.

### Level 4: FULL_SUITE

```bash
cd /app && pytest -q
```

**EXPECT**: No regression in existing tests. This is a MUST — Phase 3 is additive and must not break Phase 1/2 behaviour.

### Level 5: MANUAL_VALIDATION

Run from `sentinel-dev` (the Claude Code sandbox can't exec Docker):

```bash
# 1. Flag off (default): sync is a no-op
docker compose exec sentinel-dev sentinel outcomes sync --project ACME
# → expect: "OUTCOME_SYNC_ENABLED is not set. No-op."

# 2. Flag on: backfill
docker compose exec -e OUTCOME_SYNC_ENABLED=true sentinel-dev \
  sentinel outcomes sync --project ACME --since 2026-01-01
# → expect: per-MR lines with outcome classification

# 3. Opportunistic sync during plan
docker compose exec -e OUTCOME_SYNC_ENABLED=true sentinel-dev \
  sentinel plan ACME-847
# → expect: one short "outcomes synced: N success, M rolled_back" line before planning starts

# 4. Confidence shift visible
docker compose exec sentinel-dev sentinel rules show 17
# → expect: outcome_weight annotation present (≠ 1.0 if outcomes recorded)

# 5. Skill proposal
docker compose exec -e SKILL_PROMOTION_ENABLED=false sentinel-dev \
  python -m src.core.learning.skill_promoter --dry-run
# → expect: list of proposals without any file written or PR opened

# 6. Rollback
docker compose exec sentinel-dev \
  unset OUTCOME_SYNC_ENABLED SKILL_PROMOTION_ENABLED
# → next plan / execute has zero Phase 3 side effects
```

### Level 6: LEARNING-REVIEWER GATE

Per Handover §6 and Decision D5, `sentinel-learning-reviewer` must sign off **before** merging any PR that touches:
- `src/core/events/types.py` (Task 2)
- `src/core/persistence/migrations/` (Task 1)
- `src/prompt_loader.py` (Task 10)

For this plan, reviewer invocation is **required on Tasks 1, 2, 10** at minimum, and again **before declaring Phase 3 complete**.

---

## Acceptance Criteria (Phase 3 Exit)

From design §10 + handover §7 + task-bound:

- [ ] `OUTCOME_SYNC_ENABLED=true` on at least one Sentinel installation, producing `OutcomeRecorded` events for real merged MRs across ≥ 2 weeks.
- [ ] At least one `feedback_rules` row has `outcome_weight ≠ 1.0` (evidence it's flowing end-to-end).
- [ ] `sentinel rules show <id>` prints the weight.
- [ ] `sentinel outcomes sync --all` completes without raising on the test projects.
- [ ] Opportunistic sync on `plan` / `execute` adds ≤ 2 s P95 latency (measured over 20 invocations); else it's skipped.
- [ ] At least one subagent skill has been promoted via the promoter and merged via a human-reviewed PR. The skill file exists at `commands/<agent>/<slug>.yaml` and `command_executor` validates it.
- [ ] Zero new MR-comment surfaces introduced (per D8).
- [ ] Rollback tested: flag off + `git rm` on a promoted skill returns the system to Phase 2 behaviour with no residual errors.
- [ ] `docs/agent-learning-from-feedback-PHASE-3.md` covers the operator surface.
- [ ] `sentinel-learning-reviewer` has signed off.

---

## Completion Checklist

- [ ] Prerequisites (Phase 1 + Phase 2) verified merged.
- [ ] Tasks 1–18 completed in order with per-task validation green.
- [ ] Level 1 (lint/type) passes.
- [ ] Level 2 (unit) passes with coverage target met.
- [ ] Level 3 (integration) passes.
- [ ] Level 4 (full suite) passes with no regression.
- [ ] Level 5 (manual) walked end-to-end.
- [ ] Level 6 (learning-reviewer) has signed off.
- [ ] Acceptance criteria all ticked.
- [ ] Rollback playbook tested.

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Phase 1 / Phase 2 not actually done; plan gets started on shifting ground | MED | HIGH | Prerequisites checklist is a blocking gate. Reviewer refuses to start Phase 3 tasks if any box unchecked. |
| Opportunistic sync adds latency to every `plan`/`execute` | MED | MED | Hard 5 s timeout; non-fatal swallow; skipped entirely when flag off. Task 18 tests this. |
| Outcome classification is wrong (false-positive reverts, silent regressions) | MED | MED | `outcome_evidence_json` stores the rule that fired so audits are cheap. Asymmetric weights err toward decay, not elevation. Humans can override via `sentinel rules revoke`. |
| Skill promoter emits a broken or subtly-wrong skill | LOW | HIGH (agent starts invoking bad skills) | (a) `command_executor` validation gate before PR is opened; (b) human review is the hard gate; (c) skills live under `commands/<agent>/` as YAML that can be `git rm`'d; (d) zero auto-commit path. |
| Fast-merge-then-revert cycle fools us into a momentary `success` classification before the revert | MED | LOW | Revert detection scans the next 30 commits; re-running `sentinel outcomes sync` is idempotent and will reclassify. We tolerate the brief window. |
| Rule confidence re-derivation becomes a hot-path performance issue | LOW | MED | `recompute_confidence_for_rule` runs only on rules touched by the newly-recorded outcomes (joined via observations). Expected << 100 rules per sync. |
| GitLab API rate limits | MED | MED | Honor `Retry-After`. Give up gracefully with `OutcomeSyncSkipped(reason='rate_limited')`. `--all` processes projects serially to spread load. |
| Memory poisoning via adversarial PR reverting legitimate work | LOW | HIGH | Revert detection is a signal, not a verdict — it decays confidence but never revokes rules automatically. Revocation remains a human action (D5 / §9). |
| Phase 3 overlays confuse reviewers into thinking Phase 2 isn't done | LOW | LOW | `docs/agent-learning-from-feedback-PHASE-3.md` cross-links to Phase 2 exit criteria. Reviewer agent enforces per §7. |
| Skill files balloon under `commands/<agent>/` | MED | LOW | Design §9 overlay bloat concern applies by analogy. Skill-promoter's `min_success_rate` + `min_projects` threshold is intentionally tight; reviewer cadence in D5 applies. |

---

## Notes

**Why this plan is explicitly gated on Phases 1 + 2.** The current tracked tree does not contain `src/core/` at all — there is no `events/`, `persistence/`, or `execution/` module yet. Phase 3 assumes *every* load-bearing Phase 1 and Phase 2 artifact exists. Starting Phase 3 before Phase 1 merges would collapse the layering and re-introduce the coupling the design deliberately avoids. The reviewer agent's invocation policy (Handover §6) is the hard interlock.

**Why pull-on-demand, not webhooks.** Sentinel runs on an operator's machine with no inbound network — the design doc explains this at §10. Outcome ingestion inherits this constraint: everything is a REST `GET` from inside a `sentinel` invocation. The `project_sync_state` watermark is what makes this O(delta) rather than O(history) per run.

**Why asymmetric weights.** §7 outcome coefficients (+0.05 success, −0.15 revert, −0.25 regression) are deliberately biased toward decay. Rule promotion is hard; rule demotion is easy. This matches the memory-poisoning mitigation posture in §9 of the design doc — we would rather be slow to elevate than fast to trust.

**Why skills are YAML, not markdown.** The design doc §8 task 17 says "subagent slash-commands under `commands/`" and uses the word "markdown" loosely. The real codebase uses YAML (see `commands/python_developer/implement-tdd.yaml`), and `src/command_executor.py` validates YAML. The plan mirrors the code, not the doc's informal wording.

**Why Phase 3 adds no MR comment surfaces.** D8 binds: reviewer attention is finite, and every bot comment devalues the signal. Outcome ingestion runs silently; skill promotion happens on a branch and opens a PR against the Sentinel repo — *not* a comment on any project MR.

**Model choice — none needed.** Phase 3 has no new LLM-backed agents. All work is deterministic: SQL queries, REST calls, YAML templating. This is in line with the "the leash gets longer, not looser" principle — we grow capability by giving the existing agents better grounded signals, not by adding new agents.

**Future work explicitly gated to Phase 4.**
- Embeddings-based clustering for skill promotion (only if fuzzy-match measurably misses).
- Letta / Mem0 integration (only if SQLite measurably caps out).
- Autonomous overlay edits (never, per §9).
- A real shared-installation watermark (only if multi-instance deployments become common, per D6's revisit condition).
