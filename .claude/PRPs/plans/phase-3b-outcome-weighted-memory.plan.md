# Feature: Phase 3B — Outcome-Weighted Memory

## Summary

Phase 3B turns the ground-truth signal Phase 3A produces into pressure on the learning system. When an `executions.outcome` is tagged (`success | rolled_back | regressed`), an event subscriber finds every `feedback_rules` row whose underlying postmortem cluster touched that execution, recomputes the rule's confidence by combining the Phase 2C base curve (`observation_count`, `distinct_projects`) with a deterministic outcome term, and writes both `confidence` and a new transparent `outcome_weight` column atomically. Promotion stays human-gated — the reranker only writes numbers; it does not open MRs, edit overlays, or touch postmortems. All paths are flag-gated (`OUTCOME_WEIGHTING_ENABLED=0` by default until the exit-criterion fixture passes) and a nightly-job CLI hook (`sentinel learning recompute-confidence`) covers backfill and missed events.

PRD reference: `docs/agent-learning-from-feedback-2026-05-03.md` §8 Phase 3B (lines 499-513) + §10 Phase 3 rollback (line 601). Confidence formula reference: §C.6 (lines 810-829). Decision-points adopted in this plan: §"Open Questions Resolved" below.

**Scope guard.** Phase 3B is the reranker only. It MUST NOT extend the Phase 2C schema with `feedback_observations` / `distinct_reviewers` (deferred per migration 004 header). It MUST NOT touch `postmortems` row mutation (postmortems remain append-only). It MUST NOT propose overlays, run extraction, or revoke rules — those are Phase 2C surfaces. Skill promotion is Phase 3C and stays out.

## User Story

As a Sentinel maintainer
I want the confidence of a learned feedback rule to track the real-world fate of the merge requests that produced it
So that durable fixes float to the top of the human-gated promotion queue (`confidence ≥ 80`) and short-lived "fixes" that got reverted or regressed `main` decay back below the bar — instead of all rules drifting upward purely on observation count.

## Problem Statement

Today, `feedback_rules.confidence` is a function of `observation_count` and `distinct_projects` only (`src/core/learning/extract.py:75-86`):

```python
base = 50
obs_term  = 10 * min(5, max(0, observation_count - 1))
proj_term = 5  * min(3, max(0, distinct_projects - 1))
return max(0, min(95, base + obs_term + proj_term))
```

This formula treats every observation equally. Concretely (verifiable):

- `grep -rn "outcome" src/core/learning/extract.py` returns zero matches. The extractor never reads `executions.outcome`.
- `grep -rn "outcome" src/core/persistence/feedback_rules.py` returns zero matches. The persistence layer offers no helper to update `confidence` after the initial UPSERT.
- `grep -rn "OutcomeRecorded" src/core/learning/` returns zero matches. No subscriber consumes Phase 3A's event.
- `grep -rn "OUTCOME_WEIGHTING_ENABLED" src/` returns zero matches. The Phase 3B feature flag does not exist yet.
- `src/core/persistence/migrations/004_feedback_rules.sql:25-49` — `feedback_rules` has no `outcome_weight` column.

Result: Phase 3A landed outcomes (migration `005_outcome_ingestion.sql`), but no consumer reads them. A rule whose underlying MRs all got reverted reaches `confidence=80` on observation count alone and gets queued for promotion, exactly the failure mode §C.6 was designed to prevent.

## Solution Statement

A pull-on-event reranker, additive across four surfaces. Nothing in the existing Phase 2C extractor or proposer is rewritten:

1. **Schema (migration `006_outcome_weighting.sql`).** Add nullable `feedback_rules.outcome_weight INTEGER NOT NULL DEFAULT 0` plus `feedback_rules.outcome_weight_recomputed_at TEXT`. Default 0 keeps existing rows neutral until first recompute. No changes to `postmortems` (append-only invariant preserved) and no changes to `executions` (Phase 3A's `outcome` column is the input).

2. **Persistence helper (`src/core/persistence/feedback_rules.py`).** One new function `update_outcome_weight(conn, *, rule_id, outcome_weight, new_confidence) -> None`. This is the documented exception to the file's append-only invariant: the value is a deterministic recomputation of derived state, bounded, and the function takes both `outcome_weight` and `new_confidence` together so they cannot drift apart. Update the module-level docstring + the corresponding test to allowlist this one helper.

3. **Reranker module (`src/core/learning/outcome_reranker.py`).** New module mirroring `outcome_sync.py` and `extract.py` shape:
   - **Pure functions:** `compute_outcome_weight(success, rolled_back, regressed) -> int` (table-driven, bounded), `find_rules_referenced_by_execution(conn, execution_id) -> list[int]` (signature+agent_target join), `find_outcome_counts_for_rule(conn, rule_id) -> tuple[int,int,int]` (counts of `success | rolled_back | regressed` across the rule's cluster).
   - **Orchestration:** `recompute_confidence_for_rule(conn, rule_id) -> RerankerResult` (reads counts, applies formula, writes via `update_outcome_weight`). `recompute_all_rules(conn) -> RerankerSummary` for the nightly job and backfill — iterates live rules.
   - **Subscriber:** `register_outcome_confidence_reranker(bus, conn) -> None` — closure-based, `OutcomeRecorded` → look up rules → recompute. Mirrors `register_prompt_cache_invalidator` exactly (same swallow-and-log pattern).

4. **CLI + wiring (`src/cli.py`).** Add `_is_outcome_weighting_enabled()` helper. Wire `register_outcome_confidence_reranker` next to `register_prompt_cache_invalidator` at both bus-construction sites (`cli.py:714` and `cli.py:1057`), gated on the flag. Add one new subcommand `learning recompute-confidence` (with `--rule-id`, `--all`, `--dry-run`) for nightly-job + manual backfill, mirroring `learning extract` (cli.py:1701-1758).

5. **Tests.** Unit tests for the formula (table-driven, including bounds), the rule-lookup join (rules with overlap, rules without), the recompute writer (idempotency, atomic confidence-and-weight write), and the subscriber (event triggers recompute, exception in recompute does not crash bus). One integration-style test for the exit criterion: a fixture with one `success`-tagged execution and one `rolled_back`-tagged execution both linked to the same rule, plus one `regressed`-tagged execution, asserting `regressed` decays harder than `rolled_back`.

## Metadata

| Field            | Value                                                                                                                              |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Type             | NEW_CAPABILITY                                                                                                                     |
| Complexity       | LOW–MEDIUM (one new module, one migration, one persistence helper, one CLI subcommand; no external API integration)                |
| Systems Affected | `src/core/persistence/migrations/`, `src/core/persistence/feedback_rules.py`, `src/core/learning/`, `src/cli.py`, tests             |
| Dependencies     | pydantic ^2.5.0 (already present), click ^8.1.7 (already present); **no new dependencies**                                          |
| Estimated Tasks  | 9                                                                                                                                  |
| Hard order       | 3A landed (verified — `executions.outcome` + `OutcomeRecorded` + `OutcomeSyncService` all in place). Within 3B: migration → persistence helper → reranker module → subscriber wiring → CLI → tests. |

---

## Open Questions Resolved

The Explore phase surfaced three load-bearing ambiguities. All are resolved here so the implementation agent does not stall:

**Q1 — Where does `outcome_weight` live: `postmortems` or `feedback_rules`?**
Spec §3B says "or equivalent on `feedback_rules`". **Resolved: `feedback_rules` only.** Rationale: postmortems are append-only observations (the file `src/core/persistence/postmortems.py` exposes only `insert_postmortem` + queries, no UPDATE helpers, and a test asserts the absence). `feedback_rules.confidence` is the value that gates promotion (≥ 80) and gets injected into prompts via Phase 2A; it is the natural place for outcome-weighted adjustment. Postmortems remain untouched.

**Q2 — How do we identify "rules referenced by an execution" when there is no `execution_rules` join table?**
Spec §3B task 16: "Subscriber: when `OutcomeRecorded` fires, recompute confidence for any rule referenced by that execution." **Resolved: signature-and-agent join** through `postmortems`:

```sql
SELECT DISTINCT fr.id
  FROM feedback_rules fr
  JOIN postmortems   p ON p.failure_signature = fr.signature
                      AND p.agent             = fr.agent_target
 WHERE p.execution_id = ?
   AND fr.status IN ('probation', 'active')
```

This works because Phase 2C `extract_clusters` groups postmortems by `(stack_type, agent, failure_signature)` and the resulting `feedback_rules.signature` IS the postmortem's `failure_signature`. The `first_postmortem_id` / `last_postmortem_id` pointers on `feedback_rules` are bookmarks for proposer evidence, not authoritative cluster membership; the signature+agent join recovers the full set deterministically. Live rules only — superseded/revoked rows are intentionally excluded (the partial unique index already guarantees at most one live row per `(scope, agent_target, signature)`).

**Q3 — What is the outcome-weight formula?**
Spec §3B says "Bump on `success`, decay on `rolled_back`, decay harder on `regressed`. Bounded; deterministic." The exact constants are not specified. **Resolved: documented constants in `outcome_reranker.py`:**

```python
WEIGHT_PER_SUCCESS       = +5
WEIGHT_PER_ROLLED_BACK   = -10
WEIGHT_PER_REGRESSED     = -20
OUTCOME_WEIGHT_FLOOR     = -30
OUTCOME_WEIGHT_CEIL      = +25

def compute_outcome_weight(success: int, rolled_back: int, regressed: int) -> int:
    raw = (
        WEIGHT_PER_SUCCESS     * success
        + WEIGHT_PER_ROLLED_BACK * rolled_back
        + WEIGHT_PER_REGRESSED   * regressed
    )
    return max(OUTCOME_WEIGHT_FLOOR, min(OUTCOME_WEIGHT_CEIL, raw))
```

**Final confidence:** `clamp(base + outcome_weight, 0, 95)` where `base = compute_confidence(observation_count, distinct_projects)` from `extract.py:75-86`. Properties:

- A single `regressed` (-20) decays harder than a single `rolled_back` (-10) ✅ exit criterion.
- Bounds chosen so a single bad outcome cannot vaporize a well-supported rule (floor -30) and a few successes cannot push a low-evidence rule past the promotion gate alone (ceiling +25; need base ≥ 55, i.e. observation_count ≥ 1 + distinct_projects ≥ 2 from the Phase 2C floor). Tunable via single-line constants if Phase 3B operational data shows the bounds wrong.
- Deterministic: same `(success, rolled_back, regressed)` counts → same `outcome_weight`, regardless of event arrival order.

---

## UX Design

### Before State

```
╔═══════════════════════════════════════════════════════════════════════════════════════╗
║                          BEFORE — outcomes land but no rule cares                     ║
╠═══════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                       ║
║   ┌────────────────────┐                                                              ║
║   │ sentinel outcomes  │                                                              ║
║   │ sync               │──► UPDATE executions SET outcome=? WHERE outcome IS NULL     ║
║   └────────────────────┘    publish OutcomeRecorded                                   ║
║                                          │                                            ║
║                                          ▼                                            ║
║                              ┌─────────────────────────────┐                          ║
║                              │ EventBus fans out…          │                          ║
║                              │ (no consumer for the event) │                          ║
║                              └─────────────────────────────┘                          ║
║                                                                                       ║
║   ┌─────────────────────────────────┐                                                 ║
║   │ feedback_rules.confidence       │  recomputed only on UPSERT branch of            ║
║   │   = 50                          │  upsert_rule(): re-runs Phase 2C base formula   ║
║   │     + 10 * min(5, obs-1)        │  on observation_count + distinct_projects.      ║
║   │     +  5 * min(3, proj-1)       │                                                 ║
║   └─────────────────────────────────┘                                                 ║
║                                                                                       ║
║   USER_FLOW: maintainer runs `outcomes sync`; outcomes land in DB; nothing changes    ║
║              in `sentinel rules ls --status probation` confidence numbers.            ║
║   PAIN_POINT: a rule whose MRs all got reverted still climbs to 80 on observation     ║
║               count alone, then opens an overlay PR that the maintainer has to        ║
║               manually reject by reading MR history themselves.                       ║
║   DATA_FLOW: executions.outcome → (dead end).                                         ║
║                                                                                       ║
╚═══════════════════════════════════════════════════════════════════════════════════════╝
```

### After State

```
╔═══════════════════════════════════════════════════════════════════════════════════════╗
║                       AFTER — outcomes pull confidence up or down                     ║
╠═══════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                       ║
║   ┌────────────────────┐                                                              ║
║   │ sentinel outcomes  │                                                              ║
║   │ sync               │──► UPDATE executions SET outcome=? WHERE outcome IS NULL     ║
║   └────────────────────┘    publish OutcomeRecorded(execution_id, outcome=…)          ║
║                                          │                                            ║
║                                          ▼                                            ║
║                       ┌──────────────────────────────────────────┐                    ║
║                       │ register_outcome_confidence_reranker(    │                    ║
║                       │   bus, conn)  — gated OUTCOME_WEIGHTING  │                    ║
║                       │                                          │                    ║
║                       │ on OutcomeRecorded:                      │                    ║
║                       │  1. find_rules_referenced_by_execution() │                    ║
║                       │     → JOIN postmortems ON signature+agent│                    ║
║                       │  2. for each rule_id:                    │                    ║
║                       │     a. find_outcome_counts_for_rule()    │                    ║
║                       │        → (success, rolled_back,          │                    ║
║                       │           regressed) across cluster      │                    ║
║                       │     b. weight = compute_outcome_weight() │                    ║
║                       │        bounded [-30, +25]                │                    ║
║                       │     c. base   = compute_confidence(...)  │                    ║
║                       │        Phase 2C extract.py:75            │                    ║
║                       │     d. conf   = clamp(base+weight, 0,95) │                    ║
║                       │     e. update_outcome_weight(rule_id,    │                    ║
║                       │            weight, conf)  ← atomic       │                    ║
║                       │  3. log; never raise                     │                    ║
║                       └──────────────────────────────────────────┘                    ║
║                                          │                                            ║
║                                          ▼                                            ║
║                       ┌──────────────────────────────────────────┐                    ║
║                       │ feedback_rules                           │                    ║
║                       │   .confidence       ← 0..95 (rewritten)  │                    ║
║                       │   .outcome_weight   ← -30..+25 (new col) │                    ║
║                       │   .updated_at       ← UTC ISO            │                    ║
║                       │                                          │                    ║
║                       │ Promotion gate (`learning propose`)      │                    ║
║                       │ unchanged: still confidence >= 80,       │                    ║
║                       │ still human-merged.                      │                    ║
║                       └──────────────────────────────────────────┘                    ║
║                                                                                       ║
║   USER_FLOW: same `outcomes sync` cadence; reranker runs silently in-band.            ║
║              Backfill: `sentinel learning recompute-confidence --all`.                ║
║   VALUE_ADD: rules whose MRs reverted/regressed decay below promotion bar; rules      ║
║              that merged-and-stuck climb. Maintainer auditing the overlay PR queue    ║
║              sees outcome-weighted candidates only.                                   ║
║   DATA_FLOW: executions.outcome → OutcomeRecorded → reranker → feedback_rules.        ║
║                                                                                       ║
╚═══════════════════════════════════════════════════════════════════════════════════════╝
```

### Interaction Changes

| Location | Before | After | User Impact |
|---|---|---|---|
| `OutcomeRecorded` event | published, no subscriber | reranker subscriber recomputes confidence | one extra DB read+write per rule per outcome event (capped by # of live rules touching that signature, typically ≤ 1) |
| `feedback_rules.confidence` | reflects observation count + distinct projects only | also reflects outcome counts via `outcome_weight` | promotion queue (`confidence ≥ 80`) reorders to favor durable fixes |
| `feedback_rules.outcome_weight` | (column does not exist) | -30..+25, recomputed atomically with confidence | new column visible via `sentinel rules ls` (one-line CLI display change in scope) |
| `sentinel learning recompute-confidence` | (does not exist) | new subcommand: `--rule-id ID` / `--all` / `--dry-run` | nightly-job hook + backfill surface; safe to run any time |
| `sentinel learning extract` | confidence written via `compute_confidence()` only | unchanged at insert; reranker is **out-of-band** so concurrency between extract and reranker resolves cleanly via `BEGIN IMMEDIATE` | none in user surface; documented in plan §"Concurrency" |
| Bus invocation sites (cli.py:702-714, cli.py:1045-1057) | wires post_execute + cache_invalidator | also wires reranker (gated on `OUTCOME_WEIGHTING_ENABLED`) | flag-off behavior is bit-identical; flag-on adds in-process subscriber |

---

## Mandatory Reading

**The implementation agent MUST read these files before writing any code:**

| Priority | File | Lines | Why Read This |
|---|---|---|---|
| P0 | `docs/agent-learning-from-feedback-2026-05-03.md` | 499-513 | Phase 3B scope. Quote it; do not extend it. |
| P0 | `docs/agent-learning-from-feedback-2026-05-03.md` | 810-829 | §C.6 confidence formula + promotion thresholds. The formula in `extract.py:75` is a Phase 2C subset — Phase 3B keeps that subset and adds `outcome_weight` on top. Do NOT introduce `distinct_reviewers` or time decay (`feedback_observations` is not yet a table). |
| P0 | `src/core/learning/extract.py` | 75-86 | `compute_confidence` is the base term we add `outcome_weight` to. **Reuse it; do not re-implement it.** |
| P0 | `src/core/persistence/migrations/004_feedback_rules.sql` | 1-57 | Migration header style + table+index pattern. Use `IF NOT EXISTS`, additive `ALTER TABLE ADD COLUMN`. The header comment block on this file is the style to mirror. |
| P0 | `src/core/persistence/migrations/005_outcome_ingestion.sql` | 1-38 | Phase 3A migration shows how to do additive `ALTER TABLE ADD COLUMN` correctly in this codebase: SQLite cannot ADD COLUMN with NOT NULL+DEFAULT in the same statement, but `INTEGER NOT NULL DEFAULT 0` is fine because the literal default is constant. Verify the pattern. |
| P0 | `src/core/persistence/db.py` | 75-161 (apply_migrations + per-statement execution) | Migration runner contract: file stem is the version, numeric leading-digit sort, per-statement execute (`;`-split), idempotent. Use `006_outcome_weighting.sql` filename. |
| P0 | `src/core/persistence/feedback_rules.py` | 1-28 | Module docstring documents the append-only invariant. **You will append one allowed exception (`update_outcome_weight`) and update this docstring.** |
| P0 | `src/core/persistence/feedback_rules.py` | 174-199 | `mark_proposed` is the closest existing UPDATE helper to the new `update_outcome_weight`: same shape (UPDATE, commit, no `BEGIN IMMEDIATE` because the write is single-row and idempotent). Mirror it. |
| P0 | `src/core/persistence/feedback_rules.py` | 202-245 | `mark_promoted` is the model when a stronger guarantee is needed (`BEGIN IMMEDIATE` + verify-then-update). The reranker's writer does NOT need this — `update_outcome_weight` is idempotent on rule_id and racing recomputes converge. Document the choice in the function docstring. |
| P0 | `src/core/events/types.py` | 142-162 | `OutcomeRecorded` payload — `execution_id` is inherited from `BaseEvent` and IS the real run's id (per docstring lines 145-148). The subscriber uses `event.execution_id`, NOT `event.mr_iid`. |
| P0 | `src/core/events/bus.py` | (full file; ~110 lines) | Persist-then-publish; `subscribe(event_type, handler)` filters by exact type; **subscriber exceptions are swallowed and logged**, so the reranker's `try/except` mirrors the Phase 2A pattern below. |
| P0 | `src/core/learning/cache_invalidator.py` | 1-50 (full file) | The exact pattern to mirror for the new subscriber: closure capturing `bus + conn`, `isinstance` defensive check, try/except around the body, `bus.subscribe(EventType, _handle)`. **This is the template.** |
| P0 | `src/core/persistence/postmortems.py` | (full file; verify schema) | `postmortems(execution_id, agent, failure_signature)` are the columns the join uses. The CREATE TABLE in migration `003_postmortems.sql` is authoritative. |
| P1 | `src/core/learning/outcome_sync.py` | (full file) | Phase 3A reference for module shape: dataclass result + service class + pure helpers. Mirror the file layout, type hints, logging, docstring style. |
| P1 | `src/cli.py` | 80-94 | Feature-flag helpers `_is_X_enabled() -> bool`. Add `_is_outcome_weighting_enabled()` next to `_is_outcome_sync_enabled()` at line 86 with the exact same shape. |
| P1 | `src/cli.py` | 700-718 | First bus-construction site (`plan` flow). The new `register_outcome_confidence_reranker(bus, db_conn)` call goes immediately after `register_prompt_cache_invalidator(...)` at line 714, gated on `_is_outcome_weighting_enabled()`. Note: the outer guard at line 670 (`_verifier_loop_enabled() or _loop_c_enabled()`) must be widened to also include the new flag, otherwise the bus is never constructed when only outcome-weighting is on. |
| P1 | `src/cli.py` | 1024-1059 | Second bus-construction site (`execute` flow). Mirror the same change. |
| P1 | `src/cli.py` | 1701-1758 | `learning extract` subcommand — copy structure for the new `learning recompute-confidence` subcommand: option naming (`--dry-run`), seed-synthetic-execution pattern, summary echo. |
| P1 | `src/cli.py` | 1655-1675 | `_learning_seed_synthetic_execution` — the pattern for seeding a synthetic `executions` row so the bus FK is satisfied for the manual-CLI invocation. The reranker subscriber uses this row only as the FK target for any error-summary events (the recompute itself is SQL UPDATE, not an event). |
| P1 | `tests/conftest.py` | 99-142 | `sqlite_mem_conn` and `event_bus` fixtures. Use them; do NOT roll your own. |
| P1 | `tests/core/test_extract.py` | (full file, especially the cluster fixtures) | Test fixture style for postmortem clusters (insert N postmortems → assert extractor output). Reuse the same fixture builders for the reranker tests. |
| P1 | `tests/core/test_feedback_rules.py` | (search for "no update" / "append-only" assertion) | The test that asserts no `update_*` attributes exist on the module — **you will allowlist `update_outcome_weight` here.** Update the assertion list, do not delete it. |
| P1 | `tests/core/test_outcome_sync.py` | (full file) | Test pattern for Phase 3A subscriber-style code: in-memory SQLite, `event_bus` fixture, mock GitLab. Reranker tests need no GitLab mock — they are pure DB. |
| P2 | `.claude/PRPs/plans/completed/phase-3a-outcome-ingestion.plan.md` | all | Style reference for the Phase 3 family of plans. Match section structure verbatim. |
| P2 | `.claude/PRPs/plans/completed/phase-2c-promotion-path.plan.md` | all | Style reference. Phase 2C wrote the `feedback_rules` table; Phase 3B reads it. Helpful context for the conversation around append-only. |

**External Documentation:**

| Source | Section | Why Needed |
|---|---|---|
| [SQLite — `ALTER TABLE`](https://www.sqlite.org/lang_altertable.html#altertabaddcol) | "ADD COLUMN" | Confirms `INTEGER NOT NULL DEFAULT 0` is legal in `ADD COLUMN` (constant default), and that `TEXT` columns must allow NULL when no DEFAULT is given. The `outcome_weight_recomputed_at TEXT` column is therefore nullable on existing rows. |
| [SQLite — `BEGIN IMMEDIATE`](https://www.sqlite.org/lang_transaction.html) | "Immediate mode" | Reranker writer chooses NOT to use `BEGIN IMMEDIATE`: the UPDATE is a single row, idempotent on `rule_id`, and concurrent recomputes converge. Document the choice. Compare with `mark_promoted` which DOES need it (verify-then-update on a transitioning status). |
| [pydantic v2 — `BaseModel`](https://docs.pydantic.dev/2.5/concepts/models/) | n/a | No new event class needed — Phase 3A's `OutcomeRecorded` is the input. Listed here to make explicit: do not add new event types in Phase 3B. |
| [Click — invoke subcommands](https://click.palletsprojects.com/en/8.1.x/commands/) | `click.group().command` | `learning recompute-confidence` is a `@learning.command()` mirroring `learning extract`. |

---

## Patterns to Mirror

**SUBSCRIBER_REGISTRATION_PATTERN** (verbatim from `src/core/learning/cache_invalidator.py`):

```python
"""Subscribe a :class:`PromptLoader` to :class:`PostmortemRecorded`.
…
The subscriber is registered alongside the existing post-execute subscribers
in ``src.cli`` (Task 10) so the invalidator and the cap-out path share a
lifetime. Unit tests construct it directly.
"""

from __future__ import annotations

import logging

from src.core.events import EventBus, PostmortemRecorded
from src.core.events.types import BaseEvent
from src.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)


def register_prompt_cache_invalidator(
    bus: EventBus,
    loader: PromptLoader,
) -> None:
    def _handle(event: BaseEvent) -> None:
        # Defensive isinstance — the bus already filters by exact type, but
        # mirror the existing post_execute pattern for consistency.
        if not isinstance(event, PostmortemRecorded):
            return
        try:
            loader.clear_cache()
            logger.info(
                "Prompt cache cleared after postmortem #%d", event.postmortem_id
            )
        except Exception:
            logger.error("prompt cache invalidator crashed", exc_info=True)

    bus.subscribe(PostmortemRecorded, _handle)
```

The Phase 3B subscriber MUST mirror this shape: closure captures `(bus, conn)`, defensive `isinstance(event, OutcomeRecorded)`, try/except wraps the body, `logger.error(..., exc_info=True)` on failure, no re-raise. **A reranker exception MUST NOT crash the bus fan-out** because `OutcomeRecorded` may have other future subscribers.

**FEATURE_FLAG_HELPER_PATTERN** (verbatim from `src/cli.py:80-94`):

```python
def _is_outcome_sync_enabled() -> bool:
    """Phase 3A feature flag — set OUTCOME_SYNC_ENABLED=1 to enable
    pre-flight outcome sync at `plan` / `execute` start.
    """
    return os.getenv("OUTCOME_SYNC_ENABLED", "0") == "1"
```

**ADD verbatim shape** for `_is_outcome_weighting_enabled` directly after `_is_outcome_sync_enabled` (around line 94):

```python
def _is_outcome_weighting_enabled() -> bool:
    """Phase 3B feature flag — set OUTCOME_WEIGHTING_ENABLED=1 to enable
    the outcome-weighted confidence reranker subscriber.
    """
    return os.getenv("OUTCOME_WEIGHTING_ENABLED", "0") == "1"
```

**FEEDBACK_RULES_UPDATE_PATTERN** (verbatim from `src/core/persistence/feedback_rules.py:174-199`):

```python
def mark_proposed(
    conn: sqlite3.Connection,
    *,
    rule_id: int,
    overlay_path: str,
    mr_url: str,
) -> None:
    """Record that a draft MR has been opened for this rule.

    Sets ``proposed_overlay_path``, ``proposed_overlay_mr_url``, ``proposed_at``,
    bumps ``updated_at``. Status is intentionally NOT changed — promotion to
    'active' only happens on ``mark_promoted`` after a maintainer merges.
    """
    now = _utcnow_iso()
    conn.execute(
        """
        UPDATE feedback_rules
           SET proposed_overlay_path   = ?,
               proposed_overlay_mr_url = ?,
               proposed_at             = ?,
               updated_at              = ?
         WHERE id = ?
        """,
        (overlay_path, mr_url, now, now, rule_id),
    )
    conn.commit()
```

The new `update_outcome_weight` mirrors this: keyword-only after `conn`, `_utcnow_iso()` for `updated_at`, single UPDATE + commit, no BEGIN IMMEDIATE (single-row idempotent write). Add `outcome_weight_recomputed_at = ?` to the SET clause.

**MIGRATION_HEADER_PATTERN** (verbatim from `src/core/persistence/migrations/005_outcome_ingestion.sql:1-23`):

```sql
-- 005_outcome_ingestion.sql
-- Phase 3A schema per design §8 task 14 + DECISIONS.md D6.
-- Plan: .claude/PRPs/plans/phase-3a-outcome-ingestion.plan.md task 1.
--
-- Phase 3A ships ONLY:
--   * executions.outcome (+ evidence_json, recorded_at) for ground-truth tagging
--   * project_sync_state table -- per-installation watermark per D6
-- Reranker math (3B) and skill promotion (3C) are deferred -- when those land,
-- this migration stays untouched and a 006 widens the surface.
…
```

**ADAPT** for `006_outcome_weighting.sql`:

```sql
-- 006_outcome_weighting.sql
-- Phase 3B schema per design §8 task 16.
-- Plan: .claude/PRPs/plans/phase-3b-outcome-weighted-memory.plan.md task 1.
--
-- Phase 3B ships ONLY:
--   * feedback_rules.outcome_weight INTEGER NOT NULL DEFAULT 0
--   * feedback_rules.outcome_weight_recomputed_at TEXT (nullable)
-- Skill promotion (3C) is deferred — when it lands, this migration stays
-- untouched and a 007 widens the surface.
--
-- Why on feedback_rules and not postmortems:
--   * postmortems is append-only (src/core/persistence/postmortems.py docstring,
--     and the test that asserts no UPDATE/DELETE helpers exist).
--   * feedback_rules.confidence is the value that gates promotion (>=80) and
--     gets injected via Phase 2A; it is the natural place for outcome weight.
--
-- Append-only / append-once invariants (Phase 3B specific):
--   * outcome_weight is a deterministic recomputation. The only writer is
--     update_outcome_weight() in src/core/persistence/feedback_rules.py, which
--     atomically writes both outcome_weight and confidence so they cannot drift.
--   * Default 0 keeps existing rows neutral (confidence unchanged) until the
--     reranker first runs. Backfill via `sentinel learning recompute-confidence
--     --all` (manually invoked once after enabling the flag).

ALTER TABLE feedback_rules ADD COLUMN outcome_weight INTEGER NOT NULL DEFAULT 0;
ALTER TABLE feedback_rules ADD COLUMN outcome_weight_recomputed_at TEXT;
```

**CLI_SUBCOMMAND_SEED_PATTERN** (from `src/cli.py:1655-1675` — copy verbatim, just rename slug):

The reranker CLI command seeds a synthetic `executions` row so any future bus event has an FK target. For the recompute path itself there are no events emitted (the writer is direct SQL UPDATE), but seeding is still done because the CLI may, in a future Phase 3B iteration, publish a `RerankerCompleted` event. Adding the seed now keeps the CLI shape uniform across `learning extract`, `learning propose`, `learning recompute-confidence`. **Slug to use: `learning-recompute-<UTC ISO>`.**

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `src/core/persistence/migrations/006_outcome_weighting.sql` | CREATE | Adds `outcome_weight` + `outcome_weight_recomputed_at` columns to `feedback_rules`. |
| `src/core/persistence/feedback_rules.py` | UPDATE | Add `update_outcome_weight()` helper. Update module docstring to document the one allowed exception to append-only. |
| `src/core/learning/outcome_reranker.py` | CREATE | New module: pure formula + lookup helpers + `recompute_confidence_for_rule` + `recompute_all_rules` + `register_outcome_confidence_reranker` subscriber. |
| `src/core/learning/__init__.py` | UPDATE | Re-export `register_outcome_confidence_reranker` and `recompute_all_rules` (the public surface). |
| `src/cli.py` | UPDATE | Add `_is_outcome_weighting_enabled()` helper. Wire `register_outcome_confidence_reranker` at the two existing bus-construction sites (lines 714, 1057) gated on the flag. Widen the outer bus-construction guard (line 670) to include the new flag. Add `learning recompute-confidence` subcommand. |
| `tests/core/test_outcome_reranker.py` | CREATE | Unit tests for the formula, the lookup join, the recompute writer, the subscriber, and the exit-criterion fixture. |
| `tests/core/test_feedback_rules.py` | UPDATE | The test that asserts the absence of `update_*` attributes on the module is allowlisted to permit `update_outcome_weight` (the documented exception). One additional test asserting `update_outcome_weight` writes both columns atomically. |
| `tests/core/test_cli_learning.py` (or equivalent) | UPDATE | Add a smoke test for `sentinel learning recompute-confidence --dry-run`. If the file does not exist, locate the closest existing CLI smoke-test file (e.g. `tests/test_cli.py`) and append. |

**Files explicitly NOT to touch:**

- `src/core/persistence/postmortems.py` — append-only invariant preserved.
- `src/core/learning/extract.py` — `compute_confidence` is reused as the base term, not modified.
- `src/core/learning/propose_overlay.py` — promotion gate `confidence >= 80` is unchanged; the reranker only writes the value the gate reads.
- `src/core/learning/outcome_sync.py` — Phase 3A. The reranker subscribes to `OutcomeRecorded` *after* the sync service publishes; the sync service itself is unchanged.
- `src/core/events/types.py` — no new events. (Note for reviewer: spec §C.11 lists `FeedbackContradictionDetected` and `FeedbackMergeProposed` as future events; these are NOT Phase 3B.)

---

## NOT Building (Scope Limits)

Explicit exclusions to prevent scope creep:

- **No `feedback_observations` table.** The doc §C.3 / §C.6 references `distinct_reviewers` and `feedback_observations`, but Phase 2C migration 004 explicitly defers them ("when that lands, this migration stays untouched and a 005 widens the surface"). Phase 3B keeps the Phase 2C subset of the formula; `distinct_reviewers` and the `decay(days_since_last_observed)` term are not in scope.
- **No mutation of `postmortems` rows.** Postmortems remain append-only. Outcome weight lives entirely on `feedback_rules`. (Q1 above.)
- **No new event types.** `OutcomeRecorded` (Phase 3A) is the only input. The reranker writes via SQL UPDATE, mirroring how `OutcomeSyncService` writes outcomes (§3A pattern). A future `RerankerCompleted` notification event is left for Phase 3C if needed.
- **No autonomous promotion.** The spec is explicit: "Still human-gated at promotion — the reranker only writes confidence numbers, never opens overlay PRs." The proposer (`learning propose`) is unchanged.
- **No skill promotion.** Phase 3C territory. `propose_skills.py` does not exist and is not created here.
- **No external memory store.** Task 18 is gated and out of scope.
- **No CLI for `outcome_weight` introspection beyond what `sentinel rules ls` already shows.** A dedicated `sentinel rules why-confidence <id>` could be valuable but is not in spec; defer.
- **No background scheduler.** The "nightly job" mentioned in spec is implemented as a CLI command (`learning recompute-confidence --all`) that the operator schedules via cron / launchd / their existing Sentinel cron host. Sentinel does not own a scheduler today and Phase 3B does not introduce one.
- **No widening of `OutcomeRecorded.execution_id` semantics.** It already carries the real execution id (per `src/core/events/types.py:142-162` docstring); Phase 3B uses it as-is.

---

## Concurrency Note

Three writers can touch `feedback_rules.confidence`:

1. `upsert_rule` (Phase 2C extractor) — INSERT or UPSERT.
2. `update_outcome_weight` (Phase 3B reranker) — UPDATE.
3. Future Phase 3C — out of scope.

**Race concern:** an extract run and an OutcomeRecorded event arrive concurrently. The extractor writes `confidence = compute_confidence(obs, proj)` (no outcome term), then the reranker writes `confidence = clamp(compute_confidence(obs, proj) + outcome_weight, 0, 95)`. Whichever writes second wins, and the reranker is convergent (always reads current counts). **Outcome:** transient bias toward the extractor's value for the gap between writes; corrected on the next OutcomeRecorded fire or on the next nightly `recompute-confidence --all`.

This is acceptable: confidence is read at the promotion gate (`learning propose`), which runs out-of-band. The race window is microseconds; the corrective signal is durable.

**No `BEGIN IMMEDIATE` needed in `update_outcome_weight`** because the UPDATE is single-row, the value is idempotent on `(observation_count, distinct_projects, success_count, rolled_back_count, regressed_count)`, and the codebase uses SQLite in default isolation (single writer at the connection level). Document this choice in the function docstring.

---

## Step-by-Step Tasks

Execute in order. Each task is atomic and independently verifiable.

### Task 1: CREATE `src/core/persistence/migrations/006_outcome_weighting.sql`

- **ACTION**: Create migration file adding two columns to `feedback_rules`.
- **IMPLEMENT**: See `Patterns to Mirror → MIGRATION_HEADER_PATTERN` for the verbatim shape including header comment block. Two `ALTER TABLE` statements only.
- **MIRROR**: `src/core/persistence/migrations/005_outcome_ingestion.sql` (header style + ALTER pattern).
- **GOTCHA**: SQLite `ADD COLUMN` requires either NULL allowed or a constant DEFAULT — `INTEGER NOT NULL DEFAULT 0` is legal; `TEXT NOT NULL` would not be. Keep `outcome_weight_recomputed_at` nullable. Do not add an index (the column is read by `id`, never filtered).
- **VALIDATE**:
  ```bash
  cd /app && poetry run python -c "
  import sqlite3
  from src.core.persistence.db import apply_migrations
  c = sqlite3.connect(':memory:')
  apply_migrations(c)
  cols = [r[1] for r in c.execute('PRAGMA table_info(feedback_rules)').fetchall()]
  assert 'outcome_weight' in cols, cols
  assert 'outcome_weight_recomputed_at' in cols, cols
  print('migration applies cleanly; columns present')
  "
  ```

### Task 2: UPDATE `src/core/persistence/feedback_rules.py` — add `update_outcome_weight`

- **ACTION**: Append one new function at the end of the file. Update the module-level docstring to document the one allowed exception.
- **IMPLEMENT**:
  ```python
  def update_outcome_weight(
      conn: sqlite3.Connection,
      *,
      rule_id: int,
      outcome_weight: int,
      new_confidence: int,
  ) -> None:
      """Atomically write outcome_weight + confidence for one rule.

      Documented exception to this module's append-only invariant: the value
      is a deterministic recomputation of derived state (Phase 3B reranker,
      ``src/core/learning/outcome_reranker.py``), and writing both columns
      together prevents them drifting apart. Concurrent recomputes for the
      same rule converge: same (observation_count, distinct_projects,
      success/rolled_back/regressed counts) → same (outcome_weight, confidence).

      Single-row UPDATE; no BEGIN IMMEDIATE needed (idempotent on rule_id;
      compare ``mark_promoted`` which DOES need it for the verify-then-update
      status transition).

      Bumps ``updated_at`` and stamps ``outcome_weight_recomputed_at``.
      """
      now = _utcnow_iso()
      conn.execute(
          """
          UPDATE feedback_rules
             SET outcome_weight                = ?,
                 confidence                    = ?,
                 outcome_weight_recomputed_at  = ?,
                 updated_at                    = ?
           WHERE id = ?
          """,
          (outcome_weight, new_confidence, now, now, rule_id),
      )
      conn.commit()
  ```
- **MIRROR**: `mark_proposed` at lines 174-199 (single-row UPDATE, commit, keyword-only, `_utcnow_iso()`).
- **DOCSTRING UPDATE**: Edit the file's top-of-module docstring (lines 1-21) to add one bullet noting `update_outcome_weight` as the documented exception. Do not delete the existing append-only invariant prose.
- **GOTCHA**: Do NOT clamp inside this helper. Clamping is the reranker's job. The persistence helper writes whatever it is given so tests can exercise out-of-bound values and assert the reranker, not the helper, is the source of truth on bounds.
- **VALIDATE**:
  ```bash
  cd /app && poetry run python -c "
  from src.core.persistence import feedback_rules
  assert hasattr(feedback_rules, 'update_outcome_weight'), 'helper missing'
  print('update_outcome_weight present')
  "
  cd /app && poetry run mypy src/core/persistence/feedback_rules.py
  ```

### Task 3: UPDATE `tests/core/test_feedback_rules.py` — allowlist `update_outcome_weight`

- **ACTION**: Find the test that asserts the absence of `update_*` attributes on the module (search `grep -n "update_" tests/core/test_feedback_rules.py`) and update it to allowlist `update_outcome_weight`. Do NOT delete the assertion — it is a structural guard.
- **IMPLEMENT (sketch — exact form depends on the test's current shape)**:
  ```python
  # The append-only invariant has one documented exception: update_outcome_weight
  # (Phase 3B reranker — see module docstring + outcome_reranker.py).
  ALLOWED_UPDATE_HELPERS = {"update_outcome_weight"}
  forbidden = {
      name for name in dir(feedback_rules)
      if name.startswith("update_") and name not in ALLOWED_UPDATE_HELPERS
  }
  assert not forbidden, f"unexpected update_* helpers: {forbidden}"
  ```
- **ADD**: One new test `test_update_outcome_weight_writes_both_columns_atomically` — insert a rule via `upsert_rule`, call `update_outcome_weight(rule_id=…, outcome_weight=-15, new_confidence=42)`, then SELECT and assert both columns set + `outcome_weight_recomputed_at` populated + `updated_at` advanced.
- **VALIDATE**: `cd /app && poetry run pytest tests/core/test_feedback_rules.py -q`

### Task 4: CREATE `src/core/learning/outcome_reranker.py`

- **ACTION**: Create the module with the public surface and pure helpers. Keep it self-contained; no imports from agents.
- **IMPLEMENT (full module sketch — fill in the bodies):**
  ```python
  """Phase 3B outcome-weighted confidence reranker.

  Subscribes to OutcomeRecorded; recomputes feedback_rules.confidence for any
  live rule whose underlying postmortem cluster touches the tagged execution.

  Bounded, deterministic, idempotent:
      confidence = clamp(
          compute_confidence(observation_count, distinct_projects)  # Phase 2C base
          + clamp(
              WEIGHT_PER_SUCCESS     * success
              + WEIGHT_PER_ROLLED_BACK * rolled_back
              + WEIGHT_PER_REGRESSED   * regressed,
              OUTCOME_WEIGHT_FLOOR, OUTCOME_WEIGHT_CEIL,
          ),
          0, 95,
      )

  Same inputs → same outputs, regardless of event arrival order or arrival
  count. Reranker exceptions are swallowed and logged; bus fan-out is not
  affected (mirrors `cache_invalidator.py` pattern).
  """
  from __future__ import annotations

  import logging
  import sqlite3
  from dataclasses import dataclass, field
  from typing import Optional

  from src.core.events import EventBus, OutcomeRecorded
  from src.core.events.types import BaseEvent
  from src.core.learning.extract import compute_confidence
  from src.core.persistence.feedback_rules import update_outcome_weight

  logger = logging.getLogger(__name__)

  # Tunable constants — single-source-of-truth for the formula.
  WEIGHT_PER_SUCCESS     = +5
  WEIGHT_PER_ROLLED_BACK = -10
  WEIGHT_PER_REGRESSED   = -20
  OUTCOME_WEIGHT_FLOOR   = -30
  OUTCOME_WEIGHT_CEIL    = +25

  CONFIDENCE_FLOOR = 0
  CONFIDENCE_CEIL  = 95


  @dataclass
  class RerankerResult:
      rule_id: int
      old_confidence: int
      new_confidence: int
      outcome_weight: int
      success_count: int
      rolled_back_count: int
      regressed_count: int


  @dataclass
  class RerankerSummary:
      rules_considered: int = 0
      rules_recomputed: int = 0  # confidence actually changed
      rules_unchanged: int = 0   # recomputed to the same value
      results: list[RerankerResult] = field(default_factory=list)
      errors: list[str] = field(default_factory=list)


  def compute_outcome_weight(success: int, rolled_back: int, regressed: int) -> int:
      """Pure, bounded, deterministic. See module docstring."""
      raw = (
          WEIGHT_PER_SUCCESS     * success
          + WEIGHT_PER_ROLLED_BACK * rolled_back
          + WEIGHT_PER_REGRESSED   * regressed
      )
      return max(OUTCOME_WEIGHT_FLOOR, min(OUTCOME_WEIGHT_CEIL, raw))


  def find_rules_referenced_by_execution(
      conn: sqlite3.Connection,
      execution_id: str,
  ) -> list[int]:
      """Return live rule ids whose cluster includes any postmortem from this execution.

      Join key: (failure_signature, agent) — exactly the composite the Phase 2C
      extractor uses to form clusters (see extract.py:174-178). The
      first/last_postmortem_id columns on feedback_rules are bookmarks for
      proposer evidence, not authoritative cluster membership; the
      signature+agent join is the canonical lookup.

      'Live' = status IN ('probation', 'active') — matches the partial unique
      index idx_feedback_rules_dedup. Superseded/revoked rows are excluded.
      """
      rows = conn.execute(
          """
          SELECT DISTINCT fr.id
            FROM feedback_rules fr
            JOIN postmortems   p ON p.failure_signature = fr.signature
                                AND p.agent             = fr.agent_target
           WHERE p.execution_id = ?
             AND fr.status IN ('probation', 'active')
          """,
          (execution_id,),
      ).fetchall()
      return [int(r[0]) for r in rows]


  def find_outcome_counts_for_rule(
      conn: sqlite3.Connection,
      rule_id: int,
  ) -> tuple[int, int, int]:
      """Return (success, rolled_back, regressed) counts across the rule's cluster.

      A row is counted once per distinct execution_id. NULL outcomes (executions
      not yet tagged by Phase 3A) contribute nothing. Untagged postmortems are
      neither success nor failure — they wait for a future OutcomeRecorded.
      """
      rows = conn.execute(
          """
          SELECT e.outcome, COUNT(DISTINCT e.id) AS n
            FROM feedback_rules fr
            JOIN postmortems   p ON p.failure_signature = fr.signature
                                AND p.agent             = fr.agent_target
            JOIN executions    e ON e.id = p.execution_id
           WHERE fr.id = ?
             AND e.outcome IS NOT NULL
           GROUP BY e.outcome
          """,
          (rule_id,),
      ).fetchall()
      counts = {"success": 0, "rolled_back": 0, "regressed": 0}
      for outcome, n in rows:
          if outcome in counts:
              counts[outcome] = int(n)
      return (counts["success"], counts["rolled_back"], counts["regressed"])


  def recompute_confidence_for_rule(
      conn: sqlite3.Connection,
      rule_id: int,
  ) -> Optional[RerankerResult]:
      """Recompute confidence for one rule. Returns None if rule_id not found.

      Reads observation_count + distinct_projects from feedback_rules; reads
      outcome counts via find_outcome_counts_for_rule; computes the new value;
      writes via update_outcome_weight. Idempotent: same inputs → same write.
      """
      row = conn.execute(
          "SELECT id, observation_count, distinct_projects, confidence "
          "  FROM feedback_rules WHERE id = ?",
          (rule_id,),
      ).fetchone()
      if row is None:
          return None
      old_conf = int(row[3])
      base = compute_confidence(int(row[1]), int(row[2]))  # Phase 2C reuse
      success, rolled_back, regressed = find_outcome_counts_for_rule(conn, rule_id)
      weight = compute_outcome_weight(success, rolled_back, regressed)
      new_conf = max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEIL, base + weight))
      update_outcome_weight(
          conn,
          rule_id=rule_id,
          outcome_weight=weight,
          new_confidence=new_conf,
      )
      logger.info(
          "reranked rule %d: confidence %d → %d (base=%d, weight=%d, "
          "outcomes=success:%d/rolled_back:%d/regressed:%d)",
          rule_id, old_conf, new_conf, base, weight, success, rolled_back, regressed,
      )
      return RerankerResult(
          rule_id=rule_id,
          old_confidence=old_conf,
          new_confidence=new_conf,
          outcome_weight=weight,
          success_count=success,
          rolled_back_count=rolled_back,
          regressed_count=regressed,
      )


  def recompute_all_rules(conn: sqlite3.Connection) -> RerankerSummary:
      """Backfill / nightly hook. Iterates every live rule and recomputes.

      Order: confidence DESC then updated_at DESC (touch the rules nearest the
      promotion gate first so a flag-flip is visible quickly in operator output).
      """
      rule_ids = [
          int(r[0]) for r in conn.execute(
              "SELECT id FROM feedback_rules "
              " WHERE status IN ('probation', 'active') "
              " ORDER BY confidence DESC, updated_at DESC"
          ).fetchall()
      ]
      summary = RerankerSummary()
      for rid in rule_ids:
          summary.rules_considered += 1
          try:
              result = recompute_confidence_for_rule(conn, rid)
              if result is None:
                  continue
              if result.new_confidence == result.old_confidence:
                  summary.rules_unchanged += 1
              else:
                  summary.rules_recomputed += 1
                  summary.results.append(result)
          except Exception as exc:
              summary.errors.append(f"rule {rid}: {exc!r}")
              logger.error("recompute_all_rules: rule %d failed", rid, exc_info=True)
      return summary


  def register_outcome_confidence_reranker(
      bus: EventBus,
      conn: sqlite3.Connection,
  ) -> None:
      """Wire OutcomeRecorded → reranker. Mirrors register_prompt_cache_invalidator.

      Subscriber exceptions are swallowed and logged; bus fan-out is not
      affected. The reranker uses the same DB connection as the rest of the
      execution; concurrent recomputes converge.
      """
      def _handle(event: BaseEvent) -> None:
          if not isinstance(event, OutcomeRecorded):
              return
          try:
              rule_ids = find_rules_referenced_by_execution(conn, event.execution_id)
              if not rule_ids:
                  logger.debug(
                      "OutcomeRecorded for execution %s: no live rules touch it",
                      event.execution_id,
                  )
                  return
              for rid in rule_ids:
                  try:
                      recompute_confidence_for_rule(conn, rid)
                  except Exception:
                      logger.error(
                          "outcome reranker: recompute failed for rule %d", rid,
                          exc_info=True,
                      )
          except Exception:
              logger.error(
                  "outcome reranker subscriber crashed (execution_id=%s)",
                  event.execution_id, exc_info=True,
              )

      bus.subscribe(OutcomeRecorded, _handle)
  ```
- **MIRROR**: `cache_invalidator.py` for the registrar shape. `outcome_sync.py` for the dataclass + pure helpers + service layout. `extract.py:75-86` for how to import + reuse `compute_confidence`.
- **GOTCHA — re `find_outcome_counts_for_rule`**: the join can return zero rows if no executions in the cluster are tagged yet. The dict-with-defaults handles this — counts are (0, 0, 0) and `compute_outcome_weight` returns 0, so confidence collapses to the Phase 2C base. This is the correct default for a brand-new rule.
- **GOTCHA — re `find_rules_referenced_by_execution`**: scope-aware filtering is intentionally omitted. A rule at scope='stack:drupal' AND a rule at scope='project:acme' can both legitimately be touched by the same postmortem (Phase 2C does not yet generate `project:*` rules, but the schema permits them). The signature+agent join handles all current and forward scopes.
- **VALIDATE**:
  ```bash
  cd /app && poetry run mypy src/core/learning/outcome_reranker.py
  cd /app && poetry run python -c "
  from src.core.learning.outcome_reranker import (
      compute_outcome_weight, find_rules_referenced_by_execution,
      recompute_confidence_for_rule, recompute_all_rules,
      register_outcome_confidence_reranker,
  )
  assert compute_outcome_weight(2, 0, 0) == 10
  assert compute_outcome_weight(0, 1, 0) == -10
  assert compute_outcome_weight(0, 0, 1) == -20
  assert compute_outcome_weight(0, 0, 100) == -30   # floor
  assert compute_outcome_weight(100, 0, 0) == 25    # ceiling
  print('formula constants + bounds correct')
  "
  ```

### Task 5: UPDATE `src/core/learning/__init__.py` — export new public surface

- **ACTION**: Add re-exports of `register_outcome_confidence_reranker` and `recompute_all_rules`.
- **IMPLEMENT**: Match the existing import + `__all__` style (look at the current file shape; if it just imports, add the new imports; if it has `__all__`, append the names).
- **GOTCHA**: Do NOT export the dataclasses (`RerankerResult`, `RerankerSummary`) or the constants. They are internal — keep the public surface small. CLI imports them directly from the module if needed.
- **VALIDATE**:
  ```bash
  cd /app && poetry run python -c "
  from src.core.learning import register_outcome_confidence_reranker, recompute_all_rules
  print('re-exports OK')
  "
  ```

### Task 6: UPDATE `src/cli.py` — add feature-flag helper

- **ACTION**: Add `_is_outcome_weighting_enabled()` directly after `_is_outcome_sync_enabled()` (around line 94).
- **IMPLEMENT**: See `Patterns to Mirror → FEATURE_FLAG_HELPER_PATTERN`. Verbatim shape; only the name + env-var string change.
- **GOTCHA**: Default `"0"` (off). Phase 3B exit criterion fixture must pass before flipping. Document this in the docstring inline.
- **VALIDATE**:
  ```bash
  cd /app && poetry run python -c "
  import os
  os.environ.pop('OUTCOME_WEIGHTING_ENABLED', None)
  from src.cli import _is_outcome_weighting_enabled
  assert _is_outcome_weighting_enabled() is False
  os.environ['OUTCOME_WEIGHTING_ENABLED'] = '1'
  # reload-needed if cached; module re-import
  import importlib, src.cli
  importlib.reload(src.cli)
  assert src.cli._is_outcome_weighting_enabled() is True
  print('flag helper OK')
  "
  ```

### Task 7: UPDATE `src/cli.py` — wire reranker subscriber at both bus-construction sites

- **ACTION**: At lines 670 and the second site (~1024), widen the outer guard. At lines 714 and 1057, add the reranker registration.
- **IMPLEMENT (site 1, around lines 670-718)**:
  - Line 670 — change the guard from:
    ```python
    if _verifier_loop_enabled() or _loop_c_enabled():
    ```
    to:
    ```python
    if _verifier_loop_enabled() or _loop_c_enabled() or _is_outcome_weighting_enabled():
    ```
  - After line 714 (`register_prompt_cache_invalidator(bus, get_prompt_loader())`), add:
    ```python
    if _is_outcome_weighting_enabled():
        from src.core.learning import register_outcome_confidence_reranker
        register_outcome_confidence_reranker(bus, db_conn)
        logger.info(
            "OUTCOME_WEIGHTING_ENABLED=1 — outcome reranker subscribed to OutcomeRecorded"
        )
        click.echo("⚖️  Outcome reranker ACTIVE")
    ```
- **IMPLEMENT (site 2, around lines 1024-1059)**: Mirror the same change at the `execute` flow site. Lazy import inside the conditional keeps the import cost off the cold path when the flag is off.
- **MIRROR**: The lazy-import + log + click.echo shape mirrors how `_verifier_loop_enabled()` is announced at line 678 and `_loop_c_enabled()` at line 680.
- **GOTCHA**: The two sites must be kept in sync. If a future refactor consolidates them, the consolidated callsite must include this registration.
- **GOTCHA**: Do NOT also wire the reranker into the `outcomes sync` subcommand's bus (around line 3426). That bus is constructed solely so `OutcomeSyncService` can publish `OutcomeRecorded` events; subscribing the reranker there would create a nested loop where the same DB connection is read+written under the publish call. The reranker only needs to fire during `plan` / `execute` flows; backfill on the sync path is handled explicitly by `learning recompute-confidence --all` (Task 8). **If this restriction is ever revisited, run the reranker against a separate connection.**
- **VALIDATE**:
  ```bash
  cd /app && poetry run python -m py_compile src/cli.py
  cd /app && poetry run python -c "
  import src.cli
  # no exceptions = imports clean
  print('cli imports clean')
  "
  ```

### Task 8: UPDATE `src/cli.py` — add `learning recompute-confidence` subcommand

- **ACTION**: Add a new `@learning.command()` mirroring `learning extract` (cli.py:1701-1758).
- **IMPLEMENT**:
  ```python
  @learning.command("recompute-confidence")
  @click.option("--rule-id", type=int, default=None,
                help="Recompute one rule. Mutually exclusive with --all.")
  @click.option("--all", "all_rules", is_flag=True, default=False,
                help="Recompute every live rule (probation + active).")
  @click.option("--dry-run", is_flag=True, default=False,
                help="Compute and print the new confidence values; do not write.")
  def learning_recompute_confidence(
      rule_id: Optional[int],
      all_rules: bool,
      dry_run: bool,
  ) -> None:
      """Recompute feedback_rules.confidence using outcome weighting (Phase 3B).

      Manual / nightly-job hook. The OutcomeRecorded subscriber covers the
      in-band path; this command exists for backfill (first run after enabling
      OUTCOME_WEIGHTING_ENABLED) and for periodic re-derivation when the
      formula constants are tuned.
      """
      if (rule_id is None) == (all_rules is False):
          # exactly one must be set
          raise click.UsageError("Specify exactly one of --rule-id or --all.")

      from src.core.persistence.db import connect
      from src.core.learning.outcome_reranker import (
          recompute_all_rules, recompute_confidence_for_rule,
      )

      conn = connect()  # uses the configured DB path
      try:
          if all_rules:
              if dry_run:
                  # Dry-run: compute via a separate read-only path.
                  # Simpler: temporarily wrap update_outcome_weight to a no-op
                  # is invasive; instead, just iterate and report what would
                  # change without writing. For the v1 surface, accept that
                  # dry-run on --all writes nothing only because of the
                  # explicit savepoint + rollback below.
                  conn.execute("SAVEPOINT recompute_dryrun")
                  try:
                      summary = recompute_all_rules(conn)
                      conn.execute("ROLLBACK TO SAVEPOINT recompute_dryrun")
                      conn.execute("RELEASE SAVEPOINT recompute_dryrun")
                  except Exception:
                      conn.execute("ROLLBACK TO SAVEPOINT recompute_dryrun")
                      conn.execute("RELEASE SAVEPOINT recompute_dryrun")
                      raise
              else:
                  summary = recompute_all_rules(conn)
              click.echo(
                  f"considered={summary.rules_considered} "
                  f"recomputed={summary.rules_recomputed} "
                  f"unchanged={summary.rules_unchanged} "
                  f"errors={len(summary.errors)} "
                  f"{'(DRY-RUN)' if dry_run else ''}"
              )
              for r in summary.results:
                  click.echo(
                      f"  rule {r.rule_id}: {r.old_confidence} → {r.new_confidence} "
                      f"(weight={r.outcome_weight}, "
                      f"s={r.success_count} rb={r.rolled_back_count} rg={r.regressed_count})"
                  )
          else:
              if dry_run:
                  conn.execute("SAVEPOINT recompute_dryrun_one")
                  try:
                      result = recompute_confidence_for_rule(conn, rule_id)
                      conn.execute("ROLLBACK TO SAVEPOINT recompute_dryrun_one")
                      conn.execute("RELEASE SAVEPOINT recompute_dryrun_one")
                  except Exception:
                      conn.execute("ROLLBACK TO SAVEPOINT recompute_dryrun_one")
                      conn.execute("RELEASE SAVEPOINT recompute_dryrun_one")
                      raise
              else:
                  result = recompute_confidence_for_rule(conn, rule_id)
              if result is None:
                  raise click.ClickException(f"rule {rule_id} not found")
              click.echo(
                  f"rule {result.rule_id}: {result.old_confidence} → {result.new_confidence} "
                  f"(weight={result.outcome_weight}, "
                  f"s={result.success_count} rb={result.rolled_back_count} rg={result.regressed_count})"
                  + (" (DRY-RUN)" if dry_run else "")
              )
      finally:
          conn.close()
  ```
- **MIRROR**: `learning extract` (cli.py:1701-1758) for option naming and result-echo shape.
- **GOTCHA**: The `--dry-run` implementation uses a SQLite SAVEPOINT + ROLLBACK to undo writes. SQLite's autocommit-via-`conn.commit()` inside `update_outcome_weight` would normally defeat this, but `update_outcome_weight` calls `conn.commit()` AFTER the UPDATE — if a SAVEPOINT is open, the commit only releases nested savepoints, not the outer transaction. **Verify with the test in Task 9.** If verification fails, the simpler fix is to introduce a `dry_run: bool` parameter on `recompute_*` that returns the result without calling the persistence helper.
- **GOTCHA**: Do NOT gate this CLI command on `OUTCOME_WEIGHTING_ENABLED`. Operators must be able to compute and inspect values *before* flipping the flag (the standard "dry-run before enable" pattern Phase 3A uses for `outcomes sync`).
- **VALIDATE**:
  ```bash
  cd /app && poetry run sentinel learning recompute-confidence --help
  # Must list --rule-id, --all, --dry-run.
  ```

### Task 9: CREATE `tests/core/test_outcome_reranker.py`

- **ACTION**: Comprehensive unit tests for the new module + the exit-criterion fixture.
- **IMPLEMENT — test cases (each as a separate `def test_…`)**:
  1. **`test_compute_outcome_weight_table_driven`** — pytest parametrize over the truth table:
     - (0, 0, 0) → 0
     - (1, 0, 0) → +5
     - (5, 0, 0) → +25 (ceiling)
     - (10, 0, 0) → +25 (still ceiling)
     - (0, 1, 0) → -10
     - (0, 0, 1) → -20
     - (0, 0, 2) → -30 (floor)
     - (1, 1, 0) → -5
     - (3, 1, 0) → +5  (mixed)
     - (0, 1, 1) → -30 (floor)
     - **(0, 1, 0) > (0, 0, 1)** in absolute terms — assert `compute_outcome_weight(0, 0, 1) < compute_outcome_weight(0, 1, 0)` (regressed decays harder than rolled_back) — exit-criterion property test.
  2. **`test_find_rules_referenced_by_execution_signature_join`** — fixture: insert two postmortems for execution `exec-1` (signature `sig-A` agent `dev`, signature `sig-B` agent `dev`); insert one rule for `(sig-A, dev)` status='probation'; insert one rule for `(sig-B, dev)` status='revoked' (excluded); insert one rule for `(sig-A, dev)` status='active' but at different scope. Assert returned ids = {rule for (sig-A,dev,probation), rule for (sig-A,dev,active)} — both live, both match signature+agent. Revoked excluded. Sig-B's rule excluded by status. Other-execution postmortems excluded.
  3. **`test_find_rules_referenced_by_execution_returns_empty_when_no_postmortems`** — execution with no postmortems → empty list.
  4. **`test_find_outcome_counts_for_rule_aggregates_distinct_executions`** — insert one rule, three executions tagged `success/rolled_back/success`, two postmortems sharing same signature+agent linked to the same `success` execution (must NOT double-count) → assert (success=2, rolled_back=1, regressed=0). Also assert NULL-outcome executions contribute 0.
  5. **`test_recompute_confidence_for_rule_writes_atomic_pair`** — insert a rule with confidence=80 (e.g., obs=3, projects=2 → base=70 + outcome 0 doesn't match 80; pick obs=3 projects=3 → base=80; with no outcomes weight=0 confidence stays 80). Tag one execution `regressed` linked via signature+agent. Recompute. Assert: `outcome_weight=-20`, `confidence=60`, `outcome_weight_recomputed_at` populated.
  6. **`test_recompute_confidence_for_rule_idempotent`** — call twice; second call should return `RerankerResult(old_confidence=new_confidence)` with no change. `updated_at` will advance, but the value pair is stable.
  7. **`test_recompute_confidence_for_rule_returns_none_on_missing_id`** — call with `rule_id=99999` → `None`, no exception.
  8. **`test_recompute_all_rules_iterates_live_only`** — three rules: probation, active, revoked. Assert `summary.rules_considered == 2` (revoked excluded by status guard).
  9. **`test_recompute_all_rules_collects_errors_without_raising`** — monkeypatch `update_outcome_weight` to raise on a specific `rule_id`. Assert the summary has the rule in `errors` and the other rules still recompute.
  10. **`test_register_outcome_confidence_reranker_fires_on_event`** — wire bus + reranker; insert rule + postmortem + execution; call `bus.publish(OutcomeRecorded(execution_id=..., outcome='success', mr_iid=1, project='x', evidence_summary=''))`; assert the rule's confidence and outcome_weight changed.
  11. **`test_subscriber_swallows_exceptions`** — same setup, monkeypatch `find_rules_referenced_by_execution` to raise; publish event; assert bus.publish does not raise; assert log captured.
  12. **EXIT CRITERION test** `test_regressed_decays_harder_than_rolled_back_same_age`:
     - Setup: two rules (R1, R2) with identical (obs, proj) base. R1 has one `rolled_back`-tagged execution in cluster; R2 has one `regressed`-tagged execution in cluster (same created_at).
     - Recompute both.
     - Assert `R2.confidence < R1.confidence` AND `R2.outcome_weight < R1.outcome_weight`.
     - This is the spec's exit criterion verbatim: *"A `regressed` outcome decays harder than a `rolled_back` outcome of the same age."*
- **MIRROR**: `tests/core/test_extract.py` for fixture builders. `tests/core/test_outcome_sync.py` for subscriber-style fixtures.
- **VALIDATE**:
  ```bash
  cd /app && poetry run pytest tests/core/test_outcome_reranker.py -v
  cd /app && poetry run pytest tests/core/ -q   # full regression
  cd /app && poetry run mypy src/core/learning/outcome_reranker.py src/core/persistence/feedback_rules.py
  ```

---

## Testing Strategy

### Unit Tests to Write

| Test File | Test Cases | Validates |
|---|---|---|
| `tests/core/test_outcome_reranker.py` | 12 tests above | Formula, lookup join, recompute writer, subscriber, exit criterion |
| `tests/core/test_feedback_rules.py` | append-only allowlist update + `test_update_outcome_weight_writes_both_columns_atomically` | Persistence helper + invariant maintenance |

### Edge Cases Checklist

- [ ] Rule has zero tagged executions → `outcome_weight=0`, confidence unchanged (still equal to Phase 2C base).
- [ ] Rule has only NULL-outcome executions → same as above (NULL is filtered in the COUNT query).
- [ ] Rule has only `success` executions, ceiling reached → `outcome_weight=+25`, confidence climbs but cannot exceed 95.
- [ ] Rule has only `regressed` executions, floor reached → `outcome_weight=-30`, confidence floored at 0.
- [ ] Two postmortems from same execution sharing a signature → execution counted once (DISTINCT in COUNT).
- [ ] Rule status transitions (probation → revoked) between recompute and OutcomeRecorded → reranker filters by `status IN ('probation','active')` so revoked rule is silently skipped.
- [ ] Concurrent `upsert_rule` (extractor) and `update_outcome_weight` (reranker) on same row → last-writer-wins; convergent on next event.
- [ ] Execution_id with no postmortems at all → empty rule list, log line at DEBUG level, no error.
- [ ] Subscriber raises in `find_rules_referenced_by_execution` → swallowed; `bus.publish` returns normally.
- [ ] CLI `--rule-id 99999` (missing) → ClickException with clear message.
- [ ] CLI `--rule-id X --all` (both) → UsageError.
- [ ] CLI `--dry-run` writes nothing — verify by SELECTing `outcome_weight_recomputed_at` IS NULL after the dry run on a fresh row.

---

## Validation Commands

### Level 1: STATIC_ANALYSIS

```bash
cd /app && poetry run mypy src/core/learning/outcome_reranker.py \
                          src/core/persistence/feedback_rules.py \
                          src/cli.py
cd /app && poetry run ruff check src/core/learning/outcome_reranker.py \
                                 src/core/persistence/feedback_rules.py
```

**EXPECT**: Exit 0, no errors.

### Level 2: UNIT_TESTS

```bash
cd /app && poetry run pytest tests/core/test_outcome_reranker.py \
                              tests/core/test_feedback_rules.py -v
```

**EXPECT**: All tests pass; the 12 reranker tests + the updated feedback_rules tests are green.

### Level 3: FULL_SUITE

```bash
cd /app && poetry run pytest -q
```

**EXPECT**: No regressions. Phase 2A/2B/2C/3A tests all stay green.

### Level 4: DATABASE_VALIDATION

```bash
cd /app && poetry run python -c "
import sqlite3
from src.core.persistence.db import apply_migrations
c = sqlite3.connect(':memory:')
apply_migrations(c)
cols = {r[1]: r for r in c.execute('PRAGMA table_info(feedback_rules)').fetchall()}
assert 'outcome_weight' in cols
assert cols['outcome_weight'][3] == 1, 'outcome_weight must be NOT NULL'  # notnull flag
assert cols['outcome_weight'][4] == '0', f'default must be 0, got {cols[\"outcome_weight\"][4]}'
assert 'outcome_weight_recomputed_at' in cols
print('schema OK')
"
```

**EXPECT**: `schema OK`, no AssertionError.

### Level 5: CLI_VALIDATION

```bash
cd /app && poetry run sentinel learning recompute-confidence --help
cd /app && poetry run sentinel learning recompute-confidence --all --dry-run
# (Will print empty summary on a fresh DB; should not raise.)
```

**EXPECT**: `--help` lists three options. Dry-run on empty DB prints `considered=0 recomputed=0 unchanged=0 errors=0 (DRY-RUN)`.

### Level 6: MANUAL_VALIDATION (exit criterion fixture)

Create a fixture DB and walk through end-to-end:

```bash
cd /app && poetry run python -c "
import sqlite3
from datetime import datetime, timezone
from src.core.persistence.db import apply_migrations
from src.core.persistence.feedback_rules import upsert_rule
from src.core.persistence.postmortems import insert_postmortem
from src.core.persistence.sync_state import update_execution_outcome
from src.core.learning.outcome_reranker import recompute_confidence_for_rule

c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row
c.execute('PRAGMA foreign_keys=ON')
apply_migrations(c)

now = datetime.now(timezone.utc).isoformat()
# Two executions, same signature+agent
for exec_id in ('exec-success', 'exec-rolled-back'):
    c.execute(
        'INSERT INTO executions (id, ticket_id, kind, status, created_at) VALUES (?,?,?,?,?)',
        (exec_id, 'TKT-1', 'developer', 'completed', now))
c.commit()

p1 = insert_postmortem(c, execution_id='exec-success', stack_type='drupal',
                       agent='developer', failure_signature='sig-X')
p2 = insert_postmortem(c, execution_id='exec-rolled-back', stack_type='drupal',
                       agent='developer', failure_signature='sig-X')

rid = upsert_rule(c, signature='sig-X', scope='stack:drupal', agent_target='developer',
                  rule_text='x', confidence=70, observation_count=2,
                  distinct_projects=1, first_postmortem_id=p1, last_postmortem_id=p2)

update_execution_outcome(c, execution_id='exec-success', outcome='success',
                         evidence={'src': 'manual'})
update_execution_outcome(c, execution_id='exec-rolled-back', outcome='rolled_back',
                         evidence={'src': 'manual'})

result = recompute_confidence_for_rule(c, rid)
print(f'outcome_weight={result.outcome_weight}, new_confidence={result.new_confidence}')
# Expect outcome_weight = +5 + -10 = -5; base = 60 (obs=2, proj=1 → 50+10+0); confidence = 55.

# Now add a regressed outcome on a third exec → confidence must drop further.
c.execute(
    'INSERT INTO executions (id, ticket_id, kind, status, created_at) VALUES (?,?,?,?,?)',
    ('exec-regressed', 'TKT-1', 'developer', 'completed', now))
c.commit()
p3 = insert_postmortem(c, execution_id='exec-regressed', stack_type='drupal',
                       agent='developer', failure_signature='sig-X')
update_execution_outcome(c, execution_id='exec-regressed', outcome='regressed',
                         evidence={'src': 'manual'})

result2 = recompute_confidence_for_rule(c, rid)
print(f'outcome_weight={result2.outcome_weight}, new_confidence={result2.new_confidence}')
# Expect outcome_weight = +5 + -10 + -20 = -25; confidence = max(0, 60 + -25) = 35.
assert result2.new_confidence < result.new_confidence, 'regressed must decay harder'
print('exit criterion: regressed decays harder than rolled_back ✓')
"
```

**EXPECT**: First print shows `outcome_weight=-5, new_confidence=55`; second shows `outcome_weight=-25, new_confidence=35`; assertion passes.

---

## Acceptance Criteria

- [ ] Migration `006_outcome_weighting.sql` applies cleanly on a fresh DB and on a DB with the existing 005-state schema.
- [ ] `feedback_rules.outcome_weight` column is `INTEGER NOT NULL DEFAULT 0`.
- [ ] `feedback_rules.outcome_weight_recomputed_at` column is `TEXT` (nullable).
- [ ] `update_outcome_weight()` is the only new write helper added to `src/core/persistence/feedback_rules.py`. No `update_X` helper for any other column is added.
- [ ] The `tests/core/test_feedback_rules.py` append-only assertion still passes — and explicitly allowlists `update_outcome_weight` (not silently skipped).
- [ ] `compute_outcome_weight` is bounded `[OUTCOME_WEIGHT_FLOOR=-30, OUTCOME_WEIGHT_CEIL=+25]` and deterministic.
- [ ] `find_rules_referenced_by_execution` joins on `(failure_signature, agent)` AND filters `status IN ('probation','active')`.
- [ ] `recompute_confidence_for_rule` writes both `outcome_weight` and `confidence` via `update_outcome_weight` (atomic via single SQL UPDATE).
- [ ] Subscriber registered via `register_outcome_confidence_reranker` does not propagate exceptions.
- [ ] `OUTCOME_WEIGHTING_ENABLED=0` is bit-identical to pre-3B behavior at runtime (verifiable: `git diff` + flag-off integration test).
- [ ] `OUTCOME_WEIGHTING_ENABLED=1` causes the reranker to fire on every `OutcomeRecorded` event in `plan` and `execute` flows.
- [ ] CLI `sentinel learning recompute-confidence --rule-id ID --dry-run` writes nothing.
- [ ] CLI `sentinel learning recompute-confidence --all` updates every live rule and prints a summary.
- [ ] Exit-criterion fixture (Level 6) passes: `regressed` decays harder than `rolled_back` same age.
- [ ] No new event types added (`grep -c "class.*BaseEvent" src/core/events/types.py` is unchanged from pre-3B).
- [ ] No new external dependencies (`pyproject.toml` diff is empty for `[tool.poetry.dependencies]`).
- [ ] `mypy --strict` passes on all modified/new files.
- [ ] `ruff check` passes on all modified/new files.
- [ ] Full `pytest -q` passes with no regressions.

---

## Completion Checklist

- [ ] Tasks 1-9 completed in dependency order
- [ ] Each task validated immediately after completion
- [ ] Level 1: Static analysis (mypy + ruff) passes
- [ ] Level 2: Unit tests pass
- [ ] Level 3: Full test suite passes — no Phase 1/2/3A regressions
- [ ] Level 4: Database validation passes
- [ ] Level 5: CLI validation passes (`--help`, dry-run on empty DB)
- [ ] Level 6: Manual validation script (exit-criterion fixture) passes
- [ ] CLAUDE.md "Landing the Plane" workflow followed: bd issues filed for any deferred follow-ups (e.g. tunable-constants telemetry; CLI `sentinel rules why-confidence`); `git push` succeeds.

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Reranker formula constants are wrong (too aggressive / too gentle) → confidence numbers shift in operationally surprising ways | MED | LOW | Constants are single-line at the top of `outcome_reranker.py`. Flag-gated default-off until exit-criterion fixture is observed. Operator can re-derive with `learning recompute-confidence --all` after tuning. |
| Signature+agent join misses rules whose cluster genuinely included a postmortem from this execution but with a slightly different signature normalization | LOW–MED | MED | Phase 2C extractor groups by exact `failure_signature` (extract.py:174-178); the reranker uses the same predicate. If signature normalization is added in a future phase, the reranker join MUST change in lockstep — make this an explicit `bd` issue under the Phase 2C/3B intersection. |
| Subscriber dies silently because `OutcomeRecorded.execution_id` does not match any postmortem (e.g., synthetic outcomes-sync executions emitted by the CLI seed) | HIGH | LOW | The DEBUG-level "no live rules touch it" log line covers this. Synthetic execution rows from `outcomes sync` have no postmortems and produce zero rule lookups — this is correct, not a bug. |
| Concurrent `upsert_rule` and `update_outcome_weight` race produces transient inconsistency | LOW | LOW | Single-writer SQLite + idempotent recompute means the next event or nightly recompute converges. Documented in plan §"Concurrency Note". |
| The CLI `--dry-run` SAVEPOINT approach fails because `update_outcome_weight` calls `conn.commit()` mid-savepoint | MED | LOW | Test 9 explicitly verifies the savepoint+rollback behavior. If it fails, fall back to a `dry_run=True` parameter on `recompute_*` that returns the result without calling the persistence helper. |
| Reranker exception inside the bus subscriber accidentally bubbles up and crashes `plan` / `execute` | LOW | HIGH | Two layers of try/except in the subscriber (outer + per-rule inner). EventBus also swallows subscriber exceptions per `bus.py`. Belt + braces, intentional. |
| Operator enables `OUTCOME_WEIGHTING_ENABLED` before backfill, causing the first execution to take the only-incremental updates and leaving long-quiet rules at their pre-3B confidence | MED | LOW | Document the recommended bring-up sequence: enable the flag, then run `sentinel learning recompute-confidence --all` once. Add this to the README / phase exit notes. |
| Spec drift: someone later adds a `decay(days_since_last_observed)` term to `compute_confidence` (Phase C.6 full formula) without updating the reranker call site | LOW | LOW | The reranker imports `compute_confidence` from `extract.py`, so any future signature change is a compile-time failure surfaced at mypy. Tests 5/6 will also flake if base values shift unexpectedly. |

---

## Notes

**Why we picked feedback_rules over postmortems for `outcome_weight`.** Postmortems are append-only by deliberate design (`src/core/persistence/postmortems.py` docstring + a structural test). Adding mutable state would either (a) force a new "outcome-weight side table" to preserve append-only on the original table, doubling the schema surface, or (b) break the invariant. `feedback_rules` already has one mutation channel (`mark_proposed`/`mark_promoted`/etc.) and the value lives there naturally — promotion gates read it, prompts inject it via Phase 2A.

**Why we did not add a new event.** Spec §C.11 lists `FeedbackContradictionDetected` and `FeedbackMergeProposed` as future events. Phase 3B's reranker is read-and-write SQL, not a new domain event; mirroring `OutcomeSyncService`'s pattern (write SQL → publish notification) would mean publishing a new `RerankerCompleted` event with no current consumer. Per the project's "no half-finished implementations" directive in CLAUDE.md, defer that event until Phase 3C or a debugging surface needs it.

**Why we kept human-gated promotion unchanged.** Spec §3B is explicit: "Still human-gated at promotion — the reranker only writes confidence numbers, never opens overlay PRs." The `learning propose` flow is untouched. If outcome weighting causes the proposer queue to over-fill or under-fill, the operator's first lever is constant tuning in `outcome_reranker.py`, not a new gate. Add the `bd` issue for telemetry on queue size only after operational data exists.

**Why the recompute is signature+agent based and not first/last-postmortem-id based.** First/last pointers on `feedback_rules` are bookmarks for proposer evidence (used by the overlay PR description per spec §C.6 "PR description auto-populated with the top-3 observations"). They are not authoritative cluster membership. Phase 2C `extract_clusters` re-derives clusters by signature+agent each time (extract.py:174-178); using the same predicate at recompute time means the reranker and extractor agree on which postmortems belong together, which is essential for convergence.

**Why no `BEGIN IMMEDIATE` in `update_outcome_weight`.** Single-row UPDATE on `id`. The value being written is a deterministic function of (observation_count, distinct_projects, success/rolled_back/regressed counts) which are themselves observed by the reader at one point in time. Two concurrent recomputes for the same rule can race only on which write lands last; both writes are valid, the final state converges to the most-recent reader's view. Compare `mark_promoted` which DOES need `BEGIN IMMEDIATE` because it's a state machine transition (probation → active) requiring verify-then-update atomicity.

**Why the subscriber wires at the existing bus-construction sites, not a new one.** Both `cli.py:702-714` and `cli.py:1045-1057` already construct the bus, register `register_post_execute_subscribers` and `register_prompt_cache_invalidator`, and tie its lifetime to the execution. Adding the reranker there means the same connection is shared and the same lifetime applies — no new bus, no new connection pool. The outer guard at line 670 must widen to include `_is_outcome_weighting_enabled()` (otherwise the bus is never constructed when only outcome-weighting is on, and the subscriber is silently a no-op).

**Forward-looking hooks for Phase 3C.** When skill promotion lands, it will read `feedback_rules.confidence` to find durable fixes. Because Phase 3B writes outcome-weighted confidence into the same column, Phase 3C reads it for free. No new query, no new join. This is the deliberate payoff for landing 3B before 3C.
