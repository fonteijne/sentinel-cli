-- 001_init.sql
-- Minimal foundation for Phase 1 (Loop A). See plan Task 1 and design §2.3.
-- Tables here are the bare minimum needed so postmortems (003) can FK to
-- executions, and so the event bus (Task 3) can persist events with a
-- per-execution monotonic seq.
--
-- Forward-only. Idempotent via IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS executions (
    id            TEXT PRIMARY KEY,
    ticket_id     TEXT NOT NULL,
    kind          TEXT NOT NULL,
    status        TEXT NOT NULL,
    phase         TEXT,
    cost_cents    INTEGER DEFAULT 0,
    error         TEXT,
    metadata_json TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    execution_id TEXT NOT NULL REFERENCES executions(id),
    seq          INTEGER NOT NULL,
    ts           TEXT NOT NULL,
    agent        TEXT,
    type         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (execution_id, seq)
);

CREATE TABLE IF NOT EXISTS agent_results (
    execution_id TEXT NOT NULL REFERENCES executions(id),
    agent        TEXT NOT NULL,
    result_json  TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (execution_id, agent)
);

-- schema_migrations is also created defensively by db.apply_migrations() before
-- any migration runs; the duplicate IF NOT EXISTS here keeps the migration
-- file self-contained for code review.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
