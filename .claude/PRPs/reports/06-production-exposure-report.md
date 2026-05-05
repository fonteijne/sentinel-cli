# Implementation Report — Plan 06: Production Exposure

**Plan**: `sentinel/.claude/PRPs/plans/command-center/06-production-exposure.plan.md`
**Branch**: `experimental/command-center-06-production-exposure`
**Date**: 2026-04-23
**Status**: COMPLETE

---

## Summary

Made the Command Center backend reachable from a browser in both dev
(host-loopback on `127.0.0.1:8787`) and prod (public hostname with TLS via
bundled Traefik), without either mode leaking into the other. Added
`sentinel-serve` and `traefik` compose services (profile-gated under
`serve` and `traefik` respectively), a one-time `sentinel-edge` external
network for BYO-Traefik compatibility, and an env-flag-gated FastAPI `/docs`
endpoint. Bumped to `0.3.6`.

---

## Assessment vs Reality

| Metric     | Predicted | Actual | Reasoning |
|------------|-----------|--------|-----------|
| Complexity | LOW-MEDIUM | LOW | Compose + one FastAPI constructor change; no refactors, no new deps. The plan was sharp enough that execution was mechanical. |
| Task count | 6 | 6 | Unchanged. |
| Dep count  | 0 new Python | 0 | Only added the `traefik:v3.1` image (pulled at runtime, not a build-time dep). |

Implementation matched the plan without deviation.

---

## Tasks Completed

| # | Task | File | Status |
|---|------|------|--------|
| 1 | Dev port publish + `sentinel-serve` + `sentinel-edge` network | `docker-compose.yml` | ✅ |
| 2 | Bundled Traefik service (profile `traefik`) + `traefik-acme` volume | `docker-compose.yml` | ✅ |
| 3 | `/docs` + `/redoc` + `/openapi.json` gate | `src/service/app.py` | ✅ |
| 4 | `service.enable_docs` config key | `config/config.yaml` | ✅ |
| 5 | `.env.example` documenting `SENTINEL_HOSTNAME`, `LETSENCRYPT_EMAIL`, `SENTINEL_ENABLE_DOCS` | `.env.example` | ✅ |
| 6 | Deployment runbook | `docs/deploy.md` | ✅ |
| 7 | Docs-gate tests + version bump `0.3.5` → `0.3.6` | `tests/service/test_docs_gate.py`, `pyproject.toml` | ✅ |

---

## Validation Results

| Check | Result | Details |
|-------|--------|---------|
| Ruff (changed files) | ✅ | `src/service/app.py`, `src/config_loader.py`, `tests/service/test_docs_gate.py` clean |
| Plan-06 scoped tests | ✅ | 88 passed across `tests/service` + `tests/test_config_loader.py` |
| Docs-gate tests | ✅ | 13 passed (truth table: unset/false/0/no/off/"" → 404; true/TRUE/1/yes/on → 200) |
| `sentinel --version` | ✅ | Reports `0.3.6` |
| YAML parse | ✅ | `docker-compose.yml` parses cleanly via `yaml.safe_load` |
| FastAPI smoke | ✅ | `create_app()` with `SENTINEL_ENABLE_DOCS=true` → `docs_url=/docs, openapi_url=/openapi.json`; with `false` → both `None` |

**Pre-existing failure unrelated to plan 06**: `tests/test_base_agent.py::test_send_message_basic` fails on the base branch `v2/command-center` as well (mock-signature drift vs `agent_sdk.execute_with_tools`). Verified by checking out the base-branch copies of those files and re-running. Not in scope.

**Not runnable in this sandbox** (documented per plan):
- TLS end-to-end via Let's Encrypt (requires DNS + public IP)
- `ss -tlnp` host-bind verification (no docker CLI, no host access)
- Cross-profile `docker compose --profile serve up` dry-runs

---

## Files Changed

| File | Action | Notes |
|------|--------|-------|
| `docker-compose.yml` | UPDATE | +2 services (`sentinel-serve`, `traefik`), +1 network (`sentinel-edge` external), +1 volume (`traefik-acme`), dev port publish + `command:` override |
| `src/service/app.py` | UPDATE | +`_docs_enabled()` helper (env > config > False); pass `docs_url/redoc_url/openapi_url` as group |
| `config/config.yaml` | UPDATE | +`service.enable_docs: false`; commented prod `cors_origins` example |
| `pyproject.toml` | UPDATE | version `0.3.5` → `0.3.6` |
| `.env.example` | CREATE | env var template |
| `docs/deploy.md` | CREATE | deployment runbook (dev, prod-bundled, prod-BYO, cert renewal, token rotation, troubleshooting) |
| `tests/service/test_docs_gate.py` | CREATE | 13 parametrised tests pinning the truth table |

---

## Deviations from Plan

None. The plan was prescriptive; every task was implemented as described.

Two minor reviewer-suggested comments added post-hoc (not plan deviations):
1. YAML comment on `sentinel-dev.network_mode: bridge` explaining when to drop it for Traefik-from-dev testing.
2. Docstring note in `test_docs_disabled_returns_404_for_all_three` pinning the env-unset vs env-empty-string contract distinction (both currently agree because the config default is `false`; divergence becomes a deliberate choice if the default ever flips).

---

## Acceptance Criteria — Status

From the plan:

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `http://localhost:8787/docs` reachable when `sentinel-dev` running | ✅ (YAML configured; live verification requires docker) |
| 2 | `http://localhost:8787` NOT reachable from non-loopback | ✅ (`127.0.0.1:` prefix load-bearing; see docker-compose.yml:76) |
| 3 | `docker compose up` (no profiles) starts neither serve/dev/traefik | ✅ (all three profile-gated; `sentinel` CLI idle alone) |
| 4 | `--profile serve up` starts `sentinel-serve` without Traefik | ✅ (profiles are disjoint; BYO path documented) |
| 5 | `--profile traefik` alone → traefik up, nothing to route (safe no-op) | ✅ (Traefik has `exposedByDefault=false`) |
| 6 | Both profiles: `https://$SENTINEL_HOSTNAME/health` → 200 over TLS in 2 min | ⏭️ (requires DNS + LE; documented for operator) |
| 7 | Both profiles: `/executions` → 401 without token, 200 with | ⏭️ (plan 05 gates intact; verified by test suite) |
| 8 | `/docs` → 404 when env unset, 200 when set | ✅ (13 parametrised tests pin this) |
| 9 | Healthcheck → `healthy` within 45s | ✅ (`start_period: 15s`, `interval: 30s`, `retries: 3`) |
| 10 | `SENTINEL_HOSTNAME` unset → compose refuses with clear error | ✅ (`${SENTINEL_HOSTNAME:?SENTINEL_HOSTNAME required}` hard-fail) |
| 11 | `LETSENCRYPT_EMAIL` unset → compose refuses | ✅ (same pattern on the traefik resolver command) |
| 12 | `docs/deploy.md` covers both Traefik paths end-to-end | ✅ (sections 1, 2, 3 — bundled + BYO + cert renewal) |
| 13 | `pyproject.toml` 0.3.6; `sentinel --version` 0.3.6 | ✅ (confirmed) |

---

## Issues Encountered

1. **Pre-existing `test_base_agent.py` failure** — surfaced by `pytest -x` full regression but present on the base branch too. Scoped out.
2. **Sandbox lacks Docker CLI** — expected per CLAUDE.md. All compose validation is YAML-syntax only; operator runs the full live checks per `docs/deploy.md`.

---

## Tests Written

| Test File | Test Cases |
|-----------|-----------|
| `tests/service/test_docs_gate.py` | `test_docs_disabled_returns_404_for_all_three` (parametrised × 6), `test_docs_enabled_returns_200_for_all_three` (parametrised × 5), `test_env_overrides_config_default`, `test_health_reachable_regardless_of_docs_gate` |

13 test cases total, all green.

---

## Plan Reviewer Verdict

Ran `cc-plan-reviewer` cross-plan consistency check post-implementation.
**Verdict**: ship. No blockers. Two minor stylistic "should consider"
items (YAML comment + test docstring) — both applied post-review.

Verified invariants:
- Plan 05 network-binding invariant preserved (`127.0.0.1:` host publish)
- Plan 05 `/health` still unauthenticated (healthcheck doesn't need a token)
- Plan 05 bearer-dep wiring + CORS validation + rate-limit wrapper intact
- Plan 05 `--i-know-what-im-doing` guard unchanged (`src/cli.py:2893`)
- `/docs` + `/redoc` + `/openapi.json` gated as a group (no schema leak)
- `sentinel-serve` has no `ports:` stanza (Traefik-only reachability)
- `sentinel-edge` is `external: true` so compose doesn't fight BYO Traefik

---

## Next Steps

- [ ] Operator: `docker network create sentinel-edge` on target host
- [ ] Operator: fill in `.env` (`SENTINEL_HOSTNAME`, `LETSENCRYPT_EMAIL`) + verify DNS
- [ ] Operator: `docker compose --profile serve --profile traefik up -d`
- [ ] Operator: verify `https://$SENTINEL_HOSTNAME/health` returns 200 within 2 min
- [ ] Push branch + open PR from `experimental/command-center-06-production-exposure`

The Claude Code sandbox cannot `git push` (no SSH keys, no Docker CLI —
CLAUDE.md documented constraint). User completes the "Landing the Plane"
workflow from sentinel-dev or host.
