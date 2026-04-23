---
name: cc-gotcha-check
description: Given a file path (or area keyword) you're about to edit in the Command Center backend, surface the relevant GOTCHA notes from the five plans so you don't re-violate them. Use before starting non-trivial edits to any `src/core/`, `src/service/`, or CLI-integration file.
user-invocable: true
allowed-tools:
  - Read
  - Bash(grep *)
---

# /cc-gotcha-check — Surface plan GOTCHAs for a file

Arguments: `$ARGUMENTS` — a file path or area keyword (e.g. `src/core/events/bus.py`, `supervisor`, `auth`, `stream`).

## Execution

1. Match `$ARGUMENTS` to one or more plan areas:
   | Keyword / path | Plans to scan |
   |----------------|---------------|
   | `persistence`, `db.py`, `migrations/`, `repository.py` | 01, 04 |
   | `events/`, `bus.py`, `types.py` | 01 |
   | `execution/orchestrator.py`, `base_agent`, `agent_sdk_wrapper` | 01 |
   | `service/app.py`, `deps.py`, `schemas.py` | 02, 04, 05 |
   | `routes/executions.py` | 02 |
   | `routes/stream.py`, `websocket`, `stream` | 03 |
   | `routes/commands.py`, `commands` | 04 |
   | `supervisor.py`, `worker.py`, `logging_config.py` | 04 |
   | `auth.py`, `rate_limit.py`, `cors`, `token` | 05 |
   | `cli.py` | 01, 02, 04, 05 |

2. For each matched plan, grep `sentinel/.claude/PRPs/plans/command-center/NN-*.plan.md` for `GOTCHA` (case-sensitive) and print each surrounding paragraph.
3. Also print relevant entries from `bd-residuals.md` so known debt isn't silently re-violated.
4. Return a consolidated list grouped by severity: **Blockers** (explicit MUST/MUST NOT), **Warnings**, **Residuals**.

## Quick-reference for common footguns

Print this fixed list at the top of the output in addition to the extracted GOTCHAs:

- Never share a sqlite3 connection across threads/processes → always `connect()` factory.
- Always `BEGIN IMMEDIATE` for writers; readers use WAL snapshot.
- Always `datetime.now(timezone.utc)` — `utcnow()` is deprecated and naive.
- Event `type` strings are persisted — never rename.
- Subscribers must not bubble exceptions.
- `payload_json` stores full dump including `type` (for discriminator round-trip).
- WS stream polls the DB; never subscribe to bus (subprocess workers are invisible).
- Worker: `configure_logging()` FIRST, before any heavy import.
- Supervisor: `RLock`, not `Lock`; `post_mortem` is reentrant, NOT `@_locked`.
- Auth: `secrets.compare_digest`, atomic token file via tmp+rename.
- WS auth: `WebSocketException(1008)`, not `HTTPException`.
- `?token=` NEVER on HTTP; only loopback on WS.

## Report format

```
## GOTCHAs for <file/area>
### Blockers
- ...

### Warnings
- ...

### Residuals (bd-residuals.md)
- ...
```
