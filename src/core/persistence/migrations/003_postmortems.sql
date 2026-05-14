-- 003_postmortems.sql
-- Phase 1 schema per design §6.2 and plan §Patterns SQLITE_MIGRATION_PATTERN.
-- Numbered 003 (not 002) per the plan's note: leaves room for the Command
-- Center foundation's own 001_init / 002_workers if feat/interactive-cli
-- lands later.
--
-- Non-negotiable columns (reviewer invariants — design §4 invariant 4,
-- handover §10 risk 1):
--   * provenance: NOT NULL. 'auto' for cap-out inserts, 'human-edited' once
--     a maintainer touches the row. Any future audit needs to tell those apart.
--   * superseded_by: nullable self-FK. Append-only revocation (Decision 4):
--     superseding a postmortem points the old row at the new one — we never
--     DELETE.
--
-- Append-only: there is no UPDATE/DELETE helper, anywhere.

CREATE TABLE IF NOT EXISTS postmortems (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id       TEXT NOT NULL REFERENCES executions(id),
    stack_type         TEXT NOT NULL,
    agent              TEXT NOT NULL,
    failure_signature  TEXT NOT NULL,
    context_excerpt    TEXT,
    fix_summary        TEXT,
    provenance         TEXT NOT NULL,        -- 'auto' | 'human-edited'
    confidence         INTEGER DEFAULT 50,
    created_at         TEXT NOT NULL,
    superseded_by      INTEGER REFERENCES postmortems(id)
);

CREATE INDEX IF NOT EXISTS idx_postmortems_lookup
    ON postmortems(stack_type, agent, failure_signature);
