CREATE TABLE IF NOT EXISTS workers (
    execution_id      TEXT PRIMARY KEY REFERENCES executions(id) ON DELETE CASCADE,
    pid               INTEGER NOT NULL,
    started_at        TEXT NOT NULL,
    last_heartbeat_at TEXT NOT NULL,
    compose_projects  TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_workers_heartbeat ON workers(last_heartbeat_at);
