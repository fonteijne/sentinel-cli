# Feature: Command Center — Production Exposure

## Summary

Make the Command Center backend reachable from a browser in both development and production, without either mode leaking into the other. Adds a dev host-port publish on `sentinel-dev`, a long-running `sentinel-serve` service for the backend, and a bundled Traefik reverse proxy gated behind the compose profile `traefik`. Traefik terminates TLS via Let's Encrypt (HTTP-01) in prod. All compose-exposure that plans 01–05 deliberately deferred lives here.

## User Story

As a Sentinel operator
I want `http://localhost:8787/docs` to work in dev and `https://<hostname>/` to work in prod
So that I can exercise the API from a browser without editing compose ad-hoc, and without the prod image ever opening an unintended host port.

As an operator with an existing Traefik
I want the bundled Traefik to stay off unless I opt in via `--profile traefik`
So that two Traefik instances don't fight over ports 80/443 or over Docker-provider label scanning on my host.

## Problem Statement

After plans 01–05 the backend:
- Runs inside `sentinel-dev` on `127.0.0.1:8787` (container loopback), invisible to the host browser
- `docker-compose.yml` publishes no port for the new service
- Has no deployed counterpart — `sentinel` (prod image) entrypoints to the CLI, not `sentinel serve`
- Speaks plain HTTP/WS — no TLS, no hostname, no cert story
- `/docs` is auto-generated but both unreachable and un-gated for prod

Without this plan you either ship a backend nobody can see, or a backend exposed directly on `0.0.0.0` with a bearer-token-only gate.

## Solution Statement

1. **Dev exposure**: publish `127.0.0.1:8787:8787` on `sentinel-dev` only. Host-loopback scope keeps it local to the developer's machine. `sentinel serve --host 0.0.0.0 --i-know-what-im-doing` runs inside the container so the port forward sees a listener.
2. **Prod backend service**: new `sentinel-serve` compose service that runs `sentinel serve` as its main process (replaces idle `sentinel` CLI container for backend duty). Profile-gated under `serve` so bare `docker compose up` retains today's CLI-idle behaviour.
3. **Bundled Traefik**: new `traefik` compose service, **profile-gated under `traefik`** so it does not start unless explicitly opted in. Terminates TLS via Let's Encrypt HTTP-01 on ports 80/443. Uses Docker provider with `exposedByDefault=false` so it only routes services that carry the `traefik.enable=true` label.
4. **BYO-Traefik friendly**: `sentinel-serve` joins an **external shared network** `sentinel-edge`. Either the bundled Traefik attaches to that same network, or an operator's existing Traefik attaches to it manually. The two code paths diverge only at which Traefik is running.
5. **`/docs` prod policy**: FastAPI docs/redoc/openapi endpoints disabled by default in prod. Env flag `SENTINEL_ENABLE_DOCS=true` opts them back in. Dev defaults to `true`.
6. **CORS prod origin**: `service.cors_origins` in `config/config.yaml` takes the dashboard origin (HTTPS). Same config key plan 05 introduced; plan 06 documents the prod value.
7. **Healthcheck wired up**: uses `/health` from plan 02 (unauthenticated by plan 05 design) so compose `healthcheck` and Traefik readiness probes both work without a token.

## Metadata

| Field | Value |
|---|---|
| Type | ENHANCEMENT |
| Complexity | LOW-MEDIUM |
| Systems Affected | `docker-compose.yml`, `src/service/app.py`, `src/config_loader.py`, `config/config.yaml`, new `docs/deploy.md` |
| Dependencies | No new Python deps; adds `traefik:v3.1` image |
| Estimated Tasks | 6 |
| Prerequisite | Plans 02 (`/health`, `/docs`), 05 (auth, CORS allowlist) |

---

## Mandatory Reading

| Priority | File / URL | Why |
|---|---|---|
| P0 | `docker-compose.yml` | Current services, volumes, networks; bind-mounts and DooD wiring to preserve |
| P0 | `src/service/app.py` (from plans 02 + 05) | Where `FastAPI(...)` is constructed — `docs_url`/`redoc_url`/`openapi_url` set here |
| P0 | `src/config_loader.py:31-150` | Adding `service.enable_docs` key follows the same pattern as `service.cors_origins` |
| P1 | [Traefik Docker provider](https://doc.traefik.io/traefik/providers/docker/) | Label-driven routing, `exposedByDefault`, network selection |
| P1 | [Traefik ACME / Let's Encrypt](https://doc.traefik.io/traefik/https/acme/) | HTTP-01 challenge config; `acme.json` storage and permissions |
| P1 | [Docker Compose profiles](https://docs.docker.com/compose/how-tos/profiles/) | Semantics of `profiles:` — service is inert unless its profile is activated |
| P2 | [FastAPI — metadata and docs URLs](https://fastapi.tiangolo.com/tutorial/metadata/) | `docs_url=None` pattern |

---

## Files to Change

| File | Action |
|---|---|
| `docker-compose.yml` | UPDATE — add `sentinel-serve` service, `traefik` service (profile `traefik`), `sentinel-edge` external network, `traefik-acme` volume, dev `ports:` on `sentinel-dev`, Traefik labels + healthcheck on `sentinel-serve` |
| `src/service/app.py` | UPDATE — read `SENTINEL_ENABLE_DOCS`; pass `docs_url=None, redoc_url=None, openapi_url=None` when disabled |
| `src/config_loader.py` | UPDATE — surface `service.enable_docs` (bool, default `False`). Env `SENTINEL_ENABLE_DOCS` overrides |
| `config/config.yaml` | UPDATE — document `service.enable_docs` (default `false`) and a `service.cors_origins` prod example (commented) |
| `.env.example` | CREATE (or UPDATE if present) — document `SENTINEL_HOSTNAME`, `LETSENCRYPT_EMAIL`, `SENTINEL_ENABLE_DOCS` |
| `docs/deploy.md` | CREATE — deployment runbook (bundled Traefik vs BYO Traefik, DNS prerequisites, cert renewal, log locations, token rotation pointer) |
| `tests/service/test_docs_gate.py` | CREATE — asserts `/docs` returns 404 when `SENTINEL_ENABLE_DOCS` unset, 200 when set |

---

## Tasks

### Task 1 — UPDATE `docker-compose.yml` — dev port publish + sentinel-serve + shared network

**Goal**: dev-browser reachable on `http://localhost:8787`; prod `sentinel-serve` ready to be routed by either bundled or BYO Traefik.

Concrete diff, described in words (the implementer writes the YAML — this plan is prescriptive, not mechanical):

1. **Add `ports:` to `sentinel-dev`**:
   ```yaml
   ports:
     - "127.0.0.1:8787:8787"   # host-loopback ONLY — never 0.0.0.0:8787
   ```
   The `127.0.0.1:` prefix is load-bearing. Publishing to `0.0.0.0` would expose the dev backend to anything on the machine's network.

2. **Override `sentinel-dev` command** (or document that operators run it via exec) to start the backend:
   ```yaml
   command: ["sentinel", "serve", "--host", "0.0.0.0", "--i-know-what-im-doing", "--port", "8787"]
   ```
   `0.0.0.0` binding **inside the container** is required for `ports:` forwarding to see the listener — this is safe precisely because the host publish is scoped to `127.0.0.1`. Keep `entrypoint: []` as-is. Dev ergonomics: if operators prefer `sleep infinity` + manual `docker exec ... sentinel serve` they can override locally via `docker-compose.override.yml`; the committed default starts the backend so `/docs` just works.

3. **Add `sentinel-serve` service** (new, profile `serve`):
   ```yaml
   sentinel-serve:
     build: { context: ., dockerfile: Dockerfile, target: app }
     container_name: sentinel-serve
     profiles: [serve]                           # not in default `up`
     command: ["sentinel", "serve", "--host", "0.0.0.0", "--i-know-what-im-doing", "--port", "8787"]
     volumes:
       - sentinel-workspaces:/workspaces
       - sentinel-projects:/workspace/projects
       - /var/run/docker.sock:/var/run/docker.sock
       - ./config:/app/config:ro
       - ./config/.env.local:/app/config/.env.local:rw
       - ~/.ssh:/root/.ssh:ro
     environment:
       - WORKSPACE_ROOT=/workspaces
       - DOCKER_HOST=unix:///var/run/docker.sock
       - SENTINEL_ENABLE_DOCS=${SENTINEL_ENABLE_DOCS:-false}
       - SENTINEL_SERVICE_TOKEN=${SENTINEL_SERVICE_TOKEN:-}
     networks: [default, sentinel-edge]
     restart: unless-stopped
     healthcheck:
       test: ["CMD-SHELL", "curl -fsS http://127.0.0.1:8787/health || exit 1"]
       interval: 30s
       timeout: 5s
       retries: 3
       start_period: 15s
     labels:
       - "traefik.enable=true"
       - "traefik.docker.network=sentinel-edge"
       - "traefik.http.routers.sentinel.rule=Host(`${SENTINEL_HOSTNAME:?SENTINEL_HOSTNAME required}`)"
       - "traefik.http.routers.sentinel.entrypoints=websecure"
       - "traefik.http.routers.sentinel.tls=true"
       - "traefik.http.routers.sentinel.tls.certresolver=le"
       - "traefik.http.services.sentinel.loadbalancer.server.port=8787"
     # NO `ports:` — reached via Traefik on sentinel-edge, never directly on host
   ```
   - **No host port**. Host reachability is Traefik's job.
   - **`traefik.enable=true`** matters because Traefik is started with `exposedByDefault=false` (Task 2) — only explicitly-labelled services get routed.
   - **`${SENTINEL_HOSTNAME:?...}`** intentionally hard-fails compose if unset. Starting the prod service without a hostname is never what you want.

4. **Add external shared network**:
   ```yaml
   networks:
     sentinel-edge:
       external: true
       name: sentinel-edge
   ```
   `external: true` means compose will NOT create it. One-time host setup: `docker network create sentinel-edge`. Document in `docs/deploy.md`. This is what lets a BYO Traefik attach to it without compose stomping on it.

5. **Leave `sentinel` (CLI idle container) alone**. It remains the ad-hoc CLI entrypoint for `sentinel auth configure`, `sentinel execute`, etc. Backend duty belongs to `sentinel-serve`.

**VALIDATE**:
```bash
docker network create sentinel-edge || true
docker compose --profile dev up -d sentinel-dev
curl -sI http://localhost:8787/health      # expect 200
curl -sI http://localhost:8787/docs        # expect 200 (dev default enable_docs=true via env)

docker compose --profile serve up -d sentinel-serve
docker inspect sentinel-serve --format '{{json .State.Health.Status}}'   # "healthy" within ~45s
docker ps --filter name=sentinel-serve --format '{{.Ports}}'             # empty — no host ports
```

---

### Task 2 — UPDATE `docker-compose.yml` — bundled Traefik (profile `traefik`)

```yaml
traefik:
  image: traefik:v3.1
  container_name: sentinel-traefik
  profiles: [traefik]                            # inert unless --profile traefik
  restart: unless-stopped
  ports:
    - "80:80"
    - "443:443"
  volumes:
    - /var/run/docker.sock:/var/run/docker.sock:ro
    - traefik-acme:/acme
  networks: [sentinel-edge]
  command:
    - --providers.docker=true
    - --providers.docker.exposedbydefault=false
    - --providers.docker.network=sentinel-edge
    - --entrypoints.web.address=:80
    - --entrypoints.websecure.address=:443
    - --entrypoints.web.http.redirections.entrypoint.to=websecure
    - --entrypoints.web.http.redirections.entrypoint.scheme=https
    - --certificatesresolvers.le.acme.httpchallenge=true
    - --certificatesresolvers.le.acme.httpchallenge.entrypoint=web
    - --certificatesresolvers.le.acme.email=${LETSENCRYPT_EMAIL:?LETSENCRYPT_EMAIL required}
    - --certificatesresolvers.le.acme.storage=/acme/acme.json
    - --accesslog=true
    - --log.level=INFO
```

Add volume:
```yaml
volumes:
  traefik-acme:
    driver: local
```

**Why profile `traefik`**: bare `docker compose up` and `docker compose --profile serve up` both leave Traefik untouched. An operator with an existing Traefik simply never passes `--profile traefik`; they join `sentinel-edge` from their own stack and add the same labels on their Traefik side (or rely on the labels already on `sentinel-serve`).

**GOTCHA — HTTP-01 prerequisites**:
- Ports 80 and 443 on the host must be free (no other webserver)
- DNS A/AAAA record for `${SENTINEL_HOSTNAME}` must resolve to the host
- Port 80 must be reachable from Let's Encrypt's validation servers (no firewall block on inbound 80)
- First certificate issuance can take ~30–90s; subsequent renewals are silent

**GOTCHA — `acme.json` permissions**: Traefik refuses to start if `/acme/acme.json` exists with mode other than `600`. Using a named volume (`traefik-acme`) sidesteps this on fresh installs. Document the restore path: copy into volume with correct mode.

**GOTCHA — rate limits on Let's Encrypt**: 5 failures per account+hostname per hour, 50 certs per registered domain per week. For iterative testing, point `acme.caServer` at `https://acme-staging-v02.api.letsencrypt.org/directory` until green, then remove.

**VALIDATE**:
```bash
export SENTINEL_HOSTNAME=sentinel.iobonzai.com LETSENCRYPT_EMAIL=sentinel.utrecht@iodigital.com
docker compose --profile serve --profile traefik up -d
docker logs sentinel-traefik 2>&1 | grep -iE "certificate|acme"
curl -fsI https://$SENTINEL_HOSTNAME/health    # expect 200 over TLS
curl -fsI https://$SENTINEL_HOSTNAME/executions # expect 401 (auth still enforced)
```

---

### Task 3 — UPDATE `src/service/app.py` — `/docs` gate

FastAPI constructor accepts `docs_url`, `redoc_url`, `openapi_url`. Setting them to `None` removes the endpoints entirely (404, not 401).

```python
# In create_app() — plan 05 owns this function
import os

def _enable_docs() -> bool:
    raw = os.environ.get("SENTINEL_ENABLE_DOCS")
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return bool(config.get("service.enable_docs", default=False))

docs_enabled = _enable_docs()
app = FastAPI(
    title="Sentinel Command Center",
    docs_url="/docs" if docs_enabled else None,
    redoc_url="/redoc" if docs_enabled else None,
    openapi_url="/openapi.json" if docs_enabled else None,
)
```

**GOTCHA — Swagger UI needs `/openapi.json`**. Gating `docs_url` but leaving `openapi_url` open still leaks the full schema; gate all three together.

**Note**: `/docs` is app-level, not router-level, so plan 05's router-level bearer-token dependency doesn't apply to it. Gating is the only protection; don't skip this.

**VALIDATE**:
```bash
SENTINEL_ENABLE_DOCS=false sentinel serve &
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/docs         # 404
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/openapi.json # 404
kill %1

SENTINEL_ENABLE_DOCS=true sentinel serve &
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/docs         # 200
```

---

### Task 4 — UPDATE `src/config_loader.py` and `config/config.yaml`

Add `service.enable_docs` key alongside the `service.*` keys plans 02/05 introduce.

`config/config.yaml`:
```yaml
service:
  bind_address: 127.0.0.1
  port: 8787
  enable_docs: false                    # OpenAPI /docs off by default; env SENTINEL_ENABLE_DOCS overrides
  cors_origins: []                      # prod: ["https://dashboard.iobonzai.com"]; dev: ["http://localhost:5173"]
  rate_limits:
    max_concurrent: 3
    max_per_minute: 30
```

`src/config_loader.py`: add schema entry for `service.enable_docs` (bool, default `False`). No new config-framework work — same pattern as `service.cors_origins`.

**VALIDATE**: `pytest tests/test_config_loader.py` + a targeted test asserting precedence: env > config file > default.

---

### Task 5 — CREATE `.env.example`

```
# Required for `docker compose --profile serve up`
SENTINEL_HOSTNAME=sentinel.iobonzai.com

# Required for `docker compose --profile traefik up` (Let's Encrypt registration email).
# One-time use: Let's Encrypt emails this address for expiry warnings and policy changes.
# Use a shared/alias inbox so it survives team changes. Matches the git identity already
# configured in the Dockerfile so the stack is consistent end-to-end.
LETSENCRYPT_EMAIL=sentinel.utrecht@iodigital.com

# Optional — default false. Set to true to expose FastAPI /docs + /redoc + /openapi.json
SENTINEL_ENABLE_DOCS=false

# Optional — override the on-disk service token (~/.sentinel/service_token)
# SENTINEL_SERVICE_TOKEN=

# Reserved for future plan 07 (dashboard UI) — profile-gated container under `--profile dashboard`.
# Not read by any service today; pre-documented so operators know the slot exists and to keep
# hostname conventions consistent with SENTINEL_HOSTNAME. Uncomment and set when plan 07 lands.
# SENTINEL_DASHBOARD_HOSTNAME=dashboard.iobonzai.com
```

Add `.env` to `.gitignore` if not already there. `docker compose` auto-loads `.env` next to `docker-compose.yml`.

---

### Task 6 — CREATE `docs/deploy.md`

Runbook, must cover:

1. **DNS prerequisites** — A record to the host; port 80/443 open to the internet for HTTP-01.
2. **One-time network setup** — `docker network create sentinel-edge`.
3. **Bundled-Traefik path**:
   ```bash
   cp .env.example .env   # fill in SENTINEL_HOSTNAME, LETSENCRYPT_EMAIL
   docker compose --profile serve --profile traefik up -d
   ```
4. **BYO-Traefik path**:
   - Start Sentinel only: `docker compose --profile serve up -d`
   - Attach your existing Traefik container to `sentinel-edge`: `docker network connect sentinel-edge <your-traefik>`
   - Labels on `sentinel-serve` already provide routing rules; verify your Traefik's Docker provider is configured with `network=sentinel-edge` (or remove the `traefik.docker.network` label and let your Traefik pick)
5. **Cert renewal** — Traefik renews automatically 30 days before expiry. Logs in `docker logs sentinel-traefik`. Backup: copy the `traefik-acme` volume.
6. **Token rotation** — cross-reference plan 05: delete `~/.sentinel/service_token` inside the container and restart `sentinel-serve`.
7. **Health check** — `curl https://$SENTINEL_HOSTNAME/health` should return 200 without a token.
8. **`/docs` in prod** — default off. To enable temporarily, set `SENTINEL_ENABLE_DOCS=true` in `.env` and `docker compose up -d sentinel-serve` to recreate. Turn it off again when done.
9. **Troubleshooting**:
   - `ERR_SSL_PROTOCOL_ERROR`: cert not yet issued — check Traefik logs for ACME failures
   - `404` from Traefik: hostname mismatch between `Host()` rule and request `Host:` header
   - `502`: `sentinel-serve` unhealthy — check `docker compose logs sentinel-serve`
   - Two Traefiks fighting: check `docker ps` for profile `traefik` running alongside a BYO instance — stop one

---

## Validation Commands

```bash
# Dev — browser-reachable FastAPI /docs
docker network create sentinel-edge || true
docker compose --profile dev up -d sentinel-dev
curl -sI http://localhost:8787/health
curl -sI http://localhost:8787/docs                      # 200 in dev

# Prod — TLS via bundled Traefik
cp .env.example .env && $EDITOR .env                     # fill in hostname + email
docker compose --profile serve --profile traefik up -d
docker inspect sentinel-serve --format '{{.State.Health.Status}}'
curl -fsI https://$SENTINEL_HOSTNAME/health              # 200 over TLS
curl -fsI https://$SENTINEL_HOSTNAME/docs                # 404 (gated off)

# Prod — BYO Traefik (no bundled)
docker compose --profile serve up -d                     # no traefik profile
docker network connect sentinel-edge <your-existing-traefik>
# verify your Traefik picks up the labels

# Unit / integration
poetry run pytest tests/service/test_docs_gate.py -v
poetry run pytest tests/test_config_loader.py -v
```

## Acceptance Criteria

- [ ] `http://localhost:8787/docs` reachable from host browser when `sentinel-dev` is running (`--profile dev`)
- [ ] `http://localhost:8787` NOT reachable from any non-loopback interface on the host (bound to `127.0.0.1:`)
- [ ] `docker compose up` (no profiles) does NOT start `sentinel-serve`, `sentinel-dev`, or `traefik`
- [ ] `docker compose --profile serve up` starts `sentinel-serve` without Traefik (BYO path stays possible)
- [ ] `docker compose --profile traefik up` without `--profile serve` starts Traefik but nothing for it to route (safe no-op)
- [ ] With both profiles: `https://$SENTINEL_HOSTNAME/health` returns 200 over TLS within 2 min of first boot
- [ ] With both profiles: `https://$SENTINEL_HOSTNAME/executions` returns 401 without token, 200 with token (plan 05 gate still holds)
- [ ] `/docs` returns 404 when `SENTINEL_ENABLE_DOCS` unset; 200 when set to `true`
- [ ] Healthcheck flips container status to `healthy` within 45s of start
- [ ] `SENTINEL_HOSTNAME` unset → compose refuses to start `sentinel-serve` with a clear error
- [ ] `LETSENCRYPT_EMAIL` unset → compose refuses to start `traefik` with a clear error
- [ ] `docs/deploy.md` covers both bundled-Traefik and BYO-Traefik paths end-to-end
- [ ] `pyproject.toml` version bumped to `0.3.6`; `sentinel --version` reports `0.3.6`

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Operator runs `docker compose up` expecting the backend to come up | HIGH | LOW | Documented: backend requires `--profile serve`. `docs/deploy.md` leads with this. |
| Bundled Traefik clashes with existing Traefik on ports 80/443 | MED | HIGH | Profile-gated — bundled Traefik never starts without `--profile traefik`. BYO path documented. |
| `exposedByDefault=false` forgotten, a mislabelled container gets routed | LOW | HIGH | Explicit in `command:` of the traefik service; VALIDATE step confirms an un-labelled container is NOT exposed. |
| Let's Encrypt rate-limit lockout during iterative testing | MED | MED | Document staging CA URL in runbook; use it until green, then swap. |
| `acme.json` permission-mode trap on restored backups | MED | LOW | Named volume avoids fresh-install trap; runbook documents restore procedure. |
| `SENTINEL_HOSTNAME` left as placeholder, cert issued for `sentinel.example.com` | LOW | MED | `${SENTINEL_HOSTNAME:?}` hard-fails compose; staging CA prevents wasted quota. |
| `/docs` leaks schema in prod because only one of three URLs was gated | MED (if done naively) | MED | Task 3 gates all three (`docs_url`, `redoc_url`, `openapi_url`) together; test asserts all three. |
| Dev port `0.0.0.0:8787` accidentally exposed | LOW | HIGH | `127.0.0.1:` prefix is mandatory in the YAML; review-time check; test: run `ss -tlnp` on host, expect bind on 127.0.0.1 only. |
| Browser blocks WS upgrade over HTTPS due to mixed-content | LOW | MED | Traefik terminates TLS; downstream WS is plain HTTP inside `sentinel-edge` (fine); dashboard must use `wss://` when talking to prod. |
| First-boot cert issuance slow → healthcheck flaps | LOW | LOW | `start_period: 15s` absorbs cold start; Traefik cert issuance is independent of `/health`. |
| Two `sentinel-edge` networks with the same name exist on the host | LOW | MED | `external: true` forces reuse of a named network; `docker network create` is idempotent (script checks first). |
| Traefik v3 label syntax drift from v2 examples operator may paste in | MED | LOW | Image tag pinned to `traefik:v3.1`; labels in this plan are v3-correct. |

## Notes

- Branch: `experimental/command-center-06-production-exposure`
- This plan **does not** introduce user/role management, multi-tenant auth, or token scoping — plan 05's single-shared-secret stays the auth model. Traefik does not do auth here; it terminates TLS only.
- **Explicitly NOT in this plan**:
  - Basic-auth in front of `/docs` (env-flag on/off is enough for now)
  - Traefik dashboard (`--api.dashboard=true`) — extra attack surface, not needed
  - Rate-limiting or WAF at the Traefik layer — plan 05's per-token rate limit is the single source of truth
  - Multi-host / load-balanced deploys — single-instance matches plan 00's "Single-instance assumption" caveat
  - Log aggregation (Loki/ELK) for Traefik access logs — ops concern
  - Automated DNS record provisioning — operator's responsibility, documented as prereq
- **Follow-ups worth capturing as separate plans when needed**:
  - **Plan 07 — Dashboard UI**: separate compose service, **profile-gated under `dashboard`** (same pattern as `traefik`), Traefik labels for `Host(\`${SENTINEL_DASHBOARD_HOSTNAME}\`)` with `tls.certresolver=le`. Default expected hostname: `dashboard.iobonzai.com`. The dashboard being a separate container (not served from the API host) is a deliberate architectural choice so operators can turn it on/off independently of the backend. CORS origin `https://dashboard.iobonzai.com` is already pre-documented in `config/config.yaml` by this plan.
  - Plan 08 (hypothetical): multi-user auth / token scoping — supersedes plan 05's shared-secret
  - Metrics endpoint + Traefik metrics scrape for Prometheus
- **Session-completion ritual** (CLAUDE.md "Landing the Plane"): quality gates, `bd sync`, `git push` apply. Standard.
