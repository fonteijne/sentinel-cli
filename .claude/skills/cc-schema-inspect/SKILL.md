---
name: cc-schema-inspect
description: Inspect the Command Center SQLite database — schema, migration state, row counts, recent executions, and event type distribution. Use when debugging persistence issues, verifying a migration, or sanity-checking after a smoke run.
user-invocable: true
allowed-tools:
  - Bash(sqlite3 *)
  - Bash(ls *)
  - Bash(test *)
  - Bash(echo *)
---

# /cc-schema-inspect — Inspect the Command Center DB

Queries `$SENTINEL_DB_PATH` (or `~/.sentinel/sentinel.db`) and reports schema + state.

Arguments: `$ARGUMENTS` — optional path override or one of `schema`, `migrations`, `executions`, `events`, `workers`, `all`.

## Execution

```bash
DB="${SENTINEL_DB_PATH:-$HOME/.sentinel/sentinel.db}"
test -f "$DB" || { echo "No DB at $DB — run ensure_initialized() first"; exit 1; }
ls -la "$DB" "$DB-wal" "$DB-shm" 2>/dev/null
```

### `schema`
```bash
sqlite3 "$DB" ".schema"
```

### `migrations`
```bash
sqlite3 "$DB" "SELECT version, applied_at FROM schema_migrations ORDER BY version;"
```

### `executions`
```bash
sqlite3 -header -column "$DB" "
  SELECT status, COUNT(*) AS n
  FROM executions
  GROUP BY status
  ORDER BY n DESC;
"
sqlite3 -header -column "$DB" "
  SELECT id, ticket_id, project, kind, status, phase,
         started_at, ended_at, cost_cents
  FROM executions
  ORDER BY started_at DESC
  LIMIT 10;
"
```

### `events`
```bash
sqlite3 -header -column "$DB" "
  SELECT type, COUNT(*) AS n FROM events GROUP BY type ORDER BY n DESC;
"
sqlite3 -header -column "$DB" "
  SELECT execution_id, MAX(seq) AS last_seq FROM events
  GROUP BY execution_id
  ORDER BY last_seq DESC LIMIT 10;
"
```

### `workers`
```bash
sqlite3 -header -column "$DB" "
  SELECT execution_id, pid, started_at, last_heartbeat_at,
         json_array_length(compose_projects) AS n_compose
  FROM workers;
"
```

### `all`
Run every block above.

## Post-mortem-incomplete rows (useful when debugging reconciliation)

```bash
sqlite3 -header -column "$DB" "
  SELECT id, status, ended_at, json_extract(metadata_json,'\$.compose_projects') AS compose
  FROM executions
  WHERE status IN ('failed','cancelled')
    AND (json_extract(metadata_json,'\$.post_mortem_complete') IS NOT 1);
"
```

## Backup reminder

If the user asks for a backup, recommend (from `00-overview.plan.md` §Backup):

```bash
sqlite3 "$DB" ".backup '/path/to/backup.db'"
```

Do NOT recommend `cp sentinel.db backup.db` without checkpointing — WAL makes that a corrupt restore.

## Report format

For each section requested: echo the query, print tabular output, and flag anomalies (e.g. `running` rows older than 10 minutes without a live worker heartbeat; event types not in the plan 01 catalogue).
