---
name: cc-event-audit
description: Audit event-type coverage across the Command Center codebase. Cross-references the plan 01 event catalogue, `src/core/events/types.py` pydantic models, actual `bus.publish` call sites, and event types seen in the DB. Flags orphaned types, missing emitters, and renames (which must never happen).
user-invocable: true
allowed-tools:
  - Read
  - Bash(grep *)
  - Bash(rg *)
  - Bash(sqlite3 *)
---

# /cc-event-audit — Audit event type coverage

Per `sentinel/.claude/PRPs/plans/command-center/01-foundation.plan.md` §Event Types, every event type listed in the catalogue must:
1. Have a pydantic model in `src/core/events/types.py` with a `Literal` type string.
2. Be a member of the `AnyEvent` discriminated union.
3. Have at least one `bus.publish(...)` call site producing it (exception: types emitted only by specific agents or SDK conditions).
4. Round-trip through `payload_json` via `AnyEventAdapter`.

## Source-of-truth list (plan 01)

Lifecycle: `execution.started`, `execution.completed`, `execution.failed`, `execution.cancelling`, `execution.cancelled`, `phase.changed`.

Agent/tool: `agent.started`, `agent.finished`, `agent.message_sent`, `agent.response_received`, `tool.called`.

Results: `test_result.recorded`, `finding.posted`, `cost.accrued`.

Interactive/revision: `debrief.turn`, `revision.requested`.

Error-class: `rate_limited`.

**Terminal set** (`TERMINAL_EVENT_TYPES`): `execution.completed`, `execution.failed`, `execution.cancelled`.

## Execution

1. Read `src/core/events/types.py` and extract all `Literal["..."]` type strings.
2. Compare against the catalogue above. Report any:
   - **Missing**: in catalogue, not in types.py → must add.
   - **Extra**: in types.py, not in catalogue → call out for plan update (or deletion).
   - **Renamed**: suspicious close matches (e.g. `execution.complete` vs `execution.completed`) → **FAIL LOUD** — type strings are persisted, never rename.

3. Grep for publish sites:
   ```bash
   cd /workspace/sentinel
   rg -n "bus\.publish\(|\._emit\(|event_bus\.publish\(" src/
   ```
   For each event type in the catalogue, confirm at least one publisher (orchestrator, BaseAgent, AgentSDKWrapper, Supervisor).

4. Mandatory subscribers / consumers:
   - Orchestrator must subscribe to `cost.accrued` → `repo.add_cost`.
   - WS stream (`src/service/routes/stream.py`) must consult `TERMINAL_EVENT_TYPES` + `_END_STATUS` mapping.

5. If the DB exists, compare persisted types to the catalogue:
   ```bash
   sqlite3 "${SENTINEL_DB_PATH:-$HOME/.sentinel/sentinel.db}" \
     "SELECT DISTINCT type FROM events ORDER BY type;"
   ```
   Any persisted type not in the catalogue = silent divergence; flag it.

6. `_END_STATUS` mapping in `stream.py` must be exactly:
   ```python
   {
       "execution.completed": "succeeded",
       "execution.failed":    "failed",
       "execution.cancelled": "cancelled",
   }
   ```
   — never `type.split(".")[-1]`.

## Report format

```
## Event type audit

### Catalogue (plan 01)
<list>

### Defined in types.py
<list>

### Mismatches
- Missing in types.py: ...
- Extra in types.py (plan update needed): ...
- Suspected rename (BLOCKER): ...

### Publish sites (by type)
- execution.started — src/core/execution/orchestrator.py:L
- cost.accrued — src/agent_sdk_wrapper.py:L
- ...

### Consumer integrity
- Cost subscriber wired in Orchestrator.__init__: yes/no + file:line
- _END_STATUS exhaustive mapping in stream.py: yes/no

### Persisted types (DB)
<distinct types + count, flagged if unknown>
```
