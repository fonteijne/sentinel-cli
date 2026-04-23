# Implementation Report — Plan 05 Auth & Network Binding

**Plan**: `.claude/PRPs/plans/command-center/05-auth-and-binding.plan.md`
**Branch**: `experimental/command-center-05-auth`
**Date**: 2026-04-23
**Status**: COMPLETE

---

## Summary

Added the final security seal to the Command Center service: bearer-token
authentication on every non-health HTTP route, per-token concurrent + windowed
rate limiting on write endpoints, structured audit logging, explicit CORS
allowlist validated at startup, and a CLI guard that refuses `--host 0.0.0.0`
without an explicit opt-in flag. Token bootstrap uses an env-var → file →
atomic-create ladder with a race-free concurrent-create implementation.

---

## Assessment vs Reality

| Metric | Predicted | Actual | Reasoning |
|--------|-----------|--------|-----------|
| Complexity | LOW | LOW-MED | Auth/CORS/rate-limit wiring was straightforward; the atomic-create race analysis surfaced two distinct bugs in the plan's original token-file pattern that required a stronger implementation (`os.link` instead of `os.rename`). |
| Confidence | HIGH | HIGH | Core behaviour shipped as specified. Only the token-file atomic-create internals deviated. |

### Deviation — `load_or_create_token` atomic create

The plan specified shared `.tmp` sibling + `O_CREAT|O_EXCL` + `os.rename`, with
the loser path calling `tmp.unlink()` then reading `TOKEN_FILE`. Concurrent
tests exposed two production bugs:

1. **Loser-unlink clobber**: Loser's `tmp.unlink()` removed the winner's
   in-flight tmp before the winner's rename completed → winner raised
   `FileNotFoundError`.
2. **Silent second-winner overwrite**: A slow writer entering the create
   branch after the first winner's `os.rename` had already consumed the
   shared `.tmp` would win a fresh `O_EXCL`, and `os.rename` (not `O_EXCL`
   on the target) would silently OVERWRITE the first token. Two processes
   returned different tokens.

Fix applied: PID-unique tmp path (`*.tmp.<pid>.<hex>`) + `os.link` for atomic
create-if-not-exists (raises `FileExistsError` if the target exists — no
silent overwrite). `_read_token_file` also retries on `FileNotFoundError`
(not just short reads) so losers never observe a silent empty string.

An 8-thread `Barrier`-synchronised concurrent test (`test_atomic_create_
concurrent_calls_agree`) and a stale-tmp-resilience test verify the new
behaviour. Plan reviewer (`cc-plan-reviewer`) approved; the archived plan
text should eventually be retrofitted to match the `os.link` semantics.

---

## Tasks Completed

| # | Task | File | Status |
|---|------|------|--------|
| 1  | Auth dependencies + token bootstrap | `src/service/auth.py` | ✅ |
| 1b | Per-token rate limiter | `src/service/rate_limit.py` | ✅ |
| 2  | Final `create_app()` factory composition | `src/service/app.py` | ✅ |
| 3  | `sentinel serve` bind guard + `--show-token-prefix` | `src/cli.py` | ✅ |
| 4  | Audit write dep wired on write router | `src/service/app.py` + `src/service/auth.py` | ✅ |
| 5  | `service.*` config keys documented | `config/config.yaml` | ✅ |
| 6  | Auth tests + central `conftest.py` | `tests/service/` | ✅ |
| 7  | Version bump 0.3.4 → 0.3.5 | `pyproject.toml` | ✅ |

---

## Validation Results

| Check | Result | Details |
|-------|--------|---------|
| Ruff (`src/core src/service tests/core tests/service`) | ✅ plan 05 files | 3 pre-existing `F401` in `tests/core/test_worker_logging.py` from plan 04 — not touched |
| Mypy (`src/service`) | ✅ plan 05 files | 11 pre-existing errors in `src/core/*`, `src/session_tracker.py`, `src/config_loader.py`, `src/service/deps.py` — all predate plan 05 |
| Plan 05 unit tests (`tests/service/test_auth.py`) | ✅ | 18 passed |
| Full service suite (`tests/service`) | ✅ | 54 passed |
| Integration (`tests/integration`) | ✅ | 3 passed (auth header threaded through `client_with_fake_supervisor`) |
| Full regression (`pytest -x`) | ⚠️ | Plan-05-scoped work: clean. Pre-existing failures in `tests/test_base_agent.py`, `tests/test_confidence_evaluator.py`, `tests/test_environment_manager.py`, `tests/test_jira_server_client.py`, `tests/test_plan_generator.py`, `tests/test_worktree_manager.py` — verified unrelated (agent SDK mock signatures, env manager mocks, etc.) and all present on base branch `v2/command-center`. |

---

## Files Changed

| File | Action | Notes |
|------|--------|-------|
| `src/service/auth.py` | CREATE | 230 lines; auth deps + token bootstrap + rate-limit generator dep + audit helper |
| `src/service/rate_limit.py` | CREATE | 90 lines; thread-safe per-token concurrent + 60s windowed limits |
| `src/service/app.py` | REWRITE | Final factory with `/health` unauth, read/write/WS routers behind auth |
| `src/cli.py` | UPDATE | `serve` bind guard + `--show-token-prefix` + `--i-know-what-im-doing` |
| `config/config.yaml` | UPDATE | Added `service.{bind_address,port,cors_origins,rate_limits}` block |
| `pyproject.toml` | UPDATE | `version = "0.3.5"` |
| `tests/service/conftest.py` | CREATE | Central `authed_env`, `authed_client`, `unauthed_client`, `service_token` fixtures |
| `tests/service/test_auth.py` | CREATE | 18 tests — HTTP/WS auth, token file, rate limiter, CORS validation, concurrent-create, stale-tmp resilience |
| `tests/service/test_executions_routes.py` | UPDATE | Migrated to `authed_client` |
| `tests/service/test_commands_routes.py` | UPDATE | Migrated to `authed_client_with_fake_supervisor`; idempotency test simplified (no middleware fake) |
| `tests/service/test_stream.py` | UPDATE | Migrated to `authed_client` (WS forwards Authorization header) |
| `tests/integration/test_end_to_end.py` | UPDATE | Added `SENTINEL_SERVICE_TOKEN` env + Authorization header |

---

## Acceptance Criteria

- [x] All protected routes reject requests without a valid bearer token (401)
- [x] `/health` is reachable unauthenticated
- [x] WebSocket route accepts `?token=` (loopback only) or `Authorization` header
- [x] Binding to `0.0.0.0` requires the escape-hatch flag (`--i-know-what-im-doing`)
- [x] Token file auto-created with mode 0600
- [x] Env var overrides file
- [x] CORS defaults closed; configurable allowlist; `"*"` rejected at startup
- [x] Auth failures produce a log line with client IP + route (warning level)
- [x] `pyproject.toml` version bumped to `0.3.5`

---

## Tests Written / Strengthened

| Test file | Test cases (highlights) |
|-----------|-------------------------|
| `tests/service/test_auth.py` | HTTP: no-header/wrong-scheme/wrong-token/correct-token (401/200), `/health` unauth, query-string HTTP ignored. WS: no-token rejected, header accepted, loopback `?token=` accepted, wrong query-token rejected. Token file: 0o600 mode, env-wins-over-file. **Concurrent-create: 8-thread Barrier race, all return same token.** Stale-tmp: fresh caller unaffected. CORS: `_validate_cors(["*"])` raises. Rate limiter: concurrent + windowed unit tests; integration 31-POST → 429 + `Retry-After`. |

---

## Risks & Mitigations (for follow-up)

* `audit_write` fires on every authed write including 4xx — document this
  in ops runbook (reviewer nit).
* Rate-limit dicts are never pruned for cold token keys. Bounded in
  practice; belt-and-braces periodic prune is future work.
* `/health` executes `SELECT 1` via `get_db_conn` — it is a "deep" probe,
  so a degraded DB returns 500 (desired behaviour for compose healthchecks,
  but document so callers don't conflate with process liveness).

---

## Next Steps

1. Open PR `experimental/command-center-05-auth` → base branch per
   `00-overview.plan.md` §Branch Strategy.
2. Retrofit the plan text in `05-auth-and-binding.plan.md` §Task 1 code
   block + risk table to match `os.link` semantics (archive-cosmetic, not
   code).
3. Plan 06 (production exposure — docker-compose profile, TLS termination
   notes, ops runbook) can now proceed.
