-- 004_feedback_rules.sql
-- Phase 2C schema per design §8 task 10 + Appendix C.3 (subset).
-- Plan: .claude/PRPs/plans/phase-2c-promotion-path.plan.md task 2.
--
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
--
-- Partial unique index rationale (see plan §"Why the partial unique index"):
--   * Dedup live rules — only one probation-or-active row per
--     (scope, agent_target, signature).
--   * BUT: superseded/revoked predecessors must coexist with their successor
--     so the `superseded_by` chain stays intact. A full unique index would
--     break the chain; partial unique with WHERE status IN ('probation','active')
--     does both.

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
