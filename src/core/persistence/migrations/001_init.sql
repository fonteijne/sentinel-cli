CREATE TABLE IF NOT EXISTS executions (
    id                TEXT PRIMARY KEY,
    ticket_id         TEXT NOT NULL,
    project           TEXT NOT NULL,
    kind              TEXT NOT NULL,
    status            TEXT NOT NULL,
    phase             TEXT,
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    cost_cents        INTEGER NOT NULL DEFAULT 0,
    error             TEXT,
    idempotency_token_prefix  TEXT,
    idempotency_key   TEXT,
    metadata_json     TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_executions_idempotency
    ON executions(idempotency_token_prefix, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_executions_ticket ON executions(ticket_id);
CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);
CREATE INDEX IF NOT EXISTS idx_executions_started ON executions(started_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id  TEXT NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    ts            TEXT NOT NULL,
    agent         TEXT,
    type          TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    UNIQUE(execution_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_execution ON events(execution_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS agent_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id  TEXT NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
    agent         TEXT NOT NULL,
    result_json   TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_results_execution ON agent_results(execution_id);
