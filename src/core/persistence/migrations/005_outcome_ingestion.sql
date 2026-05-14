-- 005_outcome_ingestion.sql
-- Phase 3A schema per design §8 task 14 + DECISIONS.md D6.
-- Plan: .claude/PRPs/plans/phase-3a-outcome-ingestion.plan.md task 1.
--
-- Phase 3A ships ONLY:
--   * executions.outcome (+ evidence_json, recorded_at) for ground-truth tagging
--   * project_sync_state table -- per-installation watermark per D6
-- Reranker math (3B) and skill promotion (3C) are deferred -- when those land,
-- this migration stays untouched and a 006 widens the surface.
--
-- Append-only / append-once invariants:
--   * executions.outcome is set once: NULL -> {success, rolled_back, regressed}.
--     SQLite has no per-column update trigger, so the append-once guarantee
--     lives in src/core/persistence/sync_state.py via WHERE outcome IS NULL.
--   * project_sync_state is upserted; rows are never deleted.
--
-- Per D6: NO installation_id column on project_sync_state. The DB file itself
-- identifies the Sentinel installation; cross-installation dedup is a non-goal.
--
-- Nullable columns by design: SQLite ALTER TABLE ADD COLUMN cannot add NOT NULL
-- without a DEFAULT, and we want the untagged baseline to be NULL anyway.
-- CHECK allows NULL explicitly; SQLite's CHECK on IN(...) treats NULL as
-- unknown which would still permit it, but the explicit OR makes intent loud.

ALTER TABLE executions ADD COLUMN outcome TEXT
    CHECK (outcome IS NULL OR outcome IN ('success', 'rolled_back', 'regressed'));
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
