# Interactive-TUI — deferred follow-ups

Work surfaced during track implementation that was explicitly punted to a later pass. File as bd issues when the workspace bd CLI is available.

## FU-1 — attach-or-start: partial unique index to close concurrent-POST race

- **Priority:** 3
- **Labels:** `tech-debt`, `backend`, `command-center`
- **Source:** Track 2 (`track-2-attach-or-start.md`), flagged by `cc-persistence-expert` and `cc-plan-reviewer`.

### Problem

`POST /executions` (attach-or-start) has a narrow race: two concurrent POSTs for the same `(project, ticket_id, kind)` can both miss `ExecutionRepository.find_active` and both call `orchestrator.begin()`, creating two active rows.

### Current mitigation

SQLite write-serialisation via `BEGIN IMMEDIATE` inside `orchestrator.begin` serialises the two inserts — but does not prevent the second miss, because `find_active` is read outside the write txn. This is a v1 concession per Track 2's "Stale running row" gotcha acceptance.

### Proposed fix

Add migration `003_*.sql` with a partial unique index:

```sql
CREATE UNIQUE INDEX idx_executions_active_triple
  ON executions(project, ticket_id, kind)
  WHERE status IN ('queued','running');
```

### Decisions required before implementing

1. **Handler behaviour on `IntegrityError`** — re-run `find_active` and return attach (idiomatic), or return 409?
2. **Status set in the index predicate** — include `cancelling` or only `queued`/`running`? Track 2's `find_active` excludes `cancelling`; the index should match attach semantics.
3. **Migration-time backfill** — reject existing duplicate active rows loudly, or fix them up.

### Acceptance

- Concurrent POSTs: one inserts, one attaches, never two rows.
- No regression in single-POST latency (partial indexes are cheap).
- Test: fire 10 parallel POSTs via `asyncio.gather`, assert exactly one execution created.

### Context / references

- `/workspace/sentinel/.agents/plans/interactive-tui/track-2-attach-or-start.md` — §Gotchas
- `/workspace/sentinel/src/service/routes/executions.py` — `create_or_attach_execution`
- `/workspace/sentinel/src/core/execution/repository.py` — `find_active`
