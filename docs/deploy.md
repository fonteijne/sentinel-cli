# Sentinel Command Center — Deployment Runbook

This runbook covers deploying the Command Center backend (`sentinel serve`)
for both **dev** (browser-on-localhost) and **prod** (public hostname with
TLS). It is the companion to plan 06; everything else about the backend —
auth, event bus, worker supervisor — lives in plans 01–05.

---

## TL;DR

| Goal | Command |
|---|---|
| Dev on your laptop | `docker compose --profile dev up -d sentinel-dev` → `http://localhost:8787/docs` |
| Prod, bundled Traefik | `cp .env.example .env && $EDITOR .env` → `docker compose --profile serve --profile traefik up -d` |
| Prod, BYO Traefik | `docker compose --profile serve up -d` → `docker network connect sentinel-edge <your-traefik>` |

---

## 0. Prerequisites

### 0.1 DNS

Point `${SENTINEL_HOSTNAME}` at the Docker host **before** first boot. Let's
Encrypt HTTP-01 validates by GETting `http://<hostname>/.well-known/...`, so
the name must already resolve when Traefik asks for a cert.

Minimum record set:

```
sentinel.iobonzai.com.   300   IN   A   <host-ipv4>
; optional
sentinel.iobonzai.com.   300   IN   AAAA <host-ipv6>
```

### 0.2 Firewall / ports

- **80/tcp** and **443/tcp** open inbound from the internet — HTTP-01
  challenges come from Let's Encrypt validation servers (IP ranges not
  stable; do not whitelist).
- No other webserver may be bound to 80/443 on the host. If one is, choose
  the **BYO-Traefik path** below.

### 0.3 `.env`

```bash
cp .env.example .env
$EDITOR .env
```

Required keys before `docker compose --profile serve up`:

| Key | Required for | Notes |
|---|---|---|
| `SENTINEL_HOSTNAME` | `sentinel-serve` | Compose **refuses to start** without it — `${SENTINEL_HOSTNAME:?}` in the label expansion hard-fails |
| `LETSENCRYPT_EMAIL` | `traefik` profile only | Same hard-fail semantics |
| `SENTINEL_ENABLE_DOCS` | optional | Default `false`; set `true` for temporary schema access in prod |
| `SENTINEL_SERVICE_TOKEN` | optional | Override auto-generated token; see §5 |

---

## 1. Dev — browser on `http://localhost:8787`

```bash
docker compose --profile dev up -d sentinel-dev
curl -sI http://localhost:8787/health        # 200
open http://localhost:8787/docs              # Swagger UI
```

Notes:

- `sentinel-dev` publishes `127.0.0.1:8787:8787` — **host-loopback only**.
  Confirm with `ss -tlnp | grep 8787` on the host; bind must be `127.0.0.1`,
  never `0.0.0.0`.
- Dev defaults `SENTINEL_ENABLE_DOCS=true` so `/docs` is reachable without
  editing `.env`.
- The committed default `command:` runs `sentinel serve` directly. If you
  prefer `sleep infinity` + manual `docker exec ... sentinel serve`, drop a
  `docker-compose.override.yml` with your own `command:` — compose merges it
  automatically and it stays local (gitignored).

---

## 2. Prod — bundled Traefik path

This is the default / recommended path when the host has no existing
Traefik.

```bash
cp .env.example .env
$EDITOR .env                                   # set SENTINEL_HOSTNAME + LETSENCRYPT_EMAIL
docker compose --profile serve --profile traefik up -d
```

Verify:

```bash
docker inspect sentinel-serve --format '{{.State.Health.Status}}'     # healthy within 45s
docker logs sentinel-traefik 2>&1 | grep -iE "certificate|acme"       # cert issuance
curl -fsI https://$SENTINEL_HOSTNAME/health                           # 200 over TLS
curl -fsI https://$SENTINEL_HOSTNAME/executions                       # 401 (auth still enforced)
```

First cert issuance takes ~30–90s; browser requests before that will get an
SSL handshake error. Subsequent renewals are silent (see §4).

### 2.1 Staging CA for iterative testing

Let's Encrypt rate-limits production issuance: **5 failures per
account+hostname per hour**, **50 certs per domain per week**. If you are
iterating on compose/Traefik config, point the resolver at the staging CA
(browser will show an untrusted cert — that's expected):

Edit `docker-compose.yml` → `traefik` service `command:` and add **above**
the existing `certificatesresolvers.le.acme.*` lines:

```
- --certificatesresolvers.le.acme.caserver=https://acme-staging-v02.api.letsencrypt.org/directory
```

Delete the `traefik-acme` volume (`docker volume rm sentinel_traefik-acme`)
to force a fresh account. Swap back to production CA when the setup is
green — again deleting the volume so the account is re-registered against
the production CA.

---

## 3. Prod — BYO Traefik path

If the host already runs Traefik (or a shared ingress Traefik on another
host), skip the bundled one:

```bash
docker compose --profile serve up -d          # note: no --profile traefik
docker network connect sentinel-edge <your-traefik-container>
```

Compose creates `sentinel-edge` on first `up`, so `docker network connect`
comes **after**, not before. No one-shot `docker network create` step.

> **⚠️ `stop` vs `down` when BYO Traefik is attached.**
>
> `docker compose down` removes networks compose created — including
> `sentinel-edge`. That disconnects your BYO Traefik. Use
> `docker compose stop` / `docker compose start` for routine restarts, and
> only use `down` when you intend to re-attach your Traefik afterwards
> (re-run the `docker network connect` above). This trade-off is the cost
> of having compose create the network instead of requiring a manual
> `docker network create` step upfront.

The labels on `sentinel-serve` already provide routing rules (`Host()`,
`entrypoints=websecure`, `tls.certresolver=le`, service port 8787). Your
Traefik needs to:

1. Have its Docker provider configured with `network=sentinel-edge` (or
   remove the `traefik.docker.network` label on `sentinel-serve` and let
   your Traefik pick).
2. Expose an entrypoint named `websecure` on 443.
3. Expose a certificate resolver named `le` (rename in the labels if your
   resolver has a different name).

Verify with:

```bash
docker exec <your-traefik> wget -qO- http://sentinel-serve:8787/health    # should 200
curl -fsI https://$SENTINEL_HOSTNAME/health
```

---

## 4. Certificate renewal

Traefik renews automatically **30 days before expiry**. Zero-op under normal
conditions. Inspection commands:

```bash
docker logs sentinel-traefik 2>&1 | grep -iE "certificate|renew|acme"
docker run --rm -v sentinel_traefik-acme:/acme alpine cat /acme/acme.json | jq '.le.Certificates[].domain'
```

**Backup** the volume to survive host replacement:

```bash
docker run --rm -v sentinel_traefik-acme:/acme -v "$PWD":/backup alpine \
  tar czf /backup/traefik-acme.tgz -C / acme
```

**Restore**:

```bash
docker volume create sentinel_traefik-acme
docker run --rm -v sentinel_traefik-acme:/acme -v "$PWD":/backup alpine \
  sh -c 'tar xzf /backup/traefik-acme.tgz -C / && chmod 600 /acme/acme.json'
```

The `chmod 600` is load-bearing — Traefik refuses to start if `acme.json`
has any other mode. Using a named volume on fresh installs sidesteps this;
only restores from tarballs hit the trap.

---

## 5. Service-token rotation

The service token is generated on first boot and stored at
`~/.sentinel/service_token` **inside the container**. Plan 05 owns the
lifecycle; summary here:

```bash
# Rotate:
docker exec sentinel-serve rm -f /root/.sentinel/service_token
docker compose --profile serve restart sentinel-serve
docker exec sentinel-serve cat /root/.sentinel/service_token    # new token

# Override via env (skips the file entirely):
echo "SENTINEL_SERVICE_TOKEN=<your-token>" >> .env
docker compose --profile serve up -d sentinel-serve
```

Clients must update their `Authorization: Bearer …` header after rotation.

---

## 6. Health check

`/health` is **unauthenticated** by plan 05 design so container runtimes,
compose healthchecks, and external uptime probes work without a token:

```bash
curl -fsI https://$SENTINEL_HOSTNAME/health                    # public
docker inspect sentinel-serve --format '{{.State.Health.Status}}'  # compose view
```

If `/health` is 200 but the container reports `unhealthy`, suspect the
internal curl: `docker exec sentinel-serve curl -v http://127.0.0.1:8787/health`.

---

## 7. `/docs` in prod

Off by default and **hardcoded** in `docker-compose.yml` for `sentinel-serve`
— not `.env`-controlled, so a dev-focused `SENTINEL_ENABLE_DOCS=true` in
`.env` can't accidentally enable prod `/docs`. The FastAPI factory passes
`docs_url=None, redoc_url=None, openapi_url=None` when the env is falsy,
which 404s all three endpoints (the paths don't exist — not 401).

Temporary enablement uses a local `docker-compose.override.yml`
(gitignored), not a global env flag:

```bash
cat > docker-compose.override.yml <<'YAML'
services:
  sentinel-serve:
    environment:
      - SENTINEL_ENABLE_DOCS=true
YAML

docker compose --profile serve up -d sentinel-serve            # recreates with override
curl -fsI https://$SENTINEL_HOSTNAME/docs                      # 200

# ...use Swagger UI, then turn it back off...
rm docker-compose.override.yml
docker compose --profile serve up -d --force-recreate sentinel-serve
curl -fsI https://$SENTINEL_HOSTNAME/docs                      # 404
```

`docker-compose.override.yml` is loaded by compose automatically when
present, and values there win over the base file. Keep the file deleted
unless you're actively using `/docs` — its presence is what "prod docs
are on" looks like.

All three of `/docs`, `/redoc`, `/openapi.json` flip together — Swagger UI
fetches `/openapi.json`, so gating only `docs_url` would still leak the
schema. The factory gates them as a group.

---

## 8. Troubleshooting

| Symptom | Likely cause | Next check |
|---|---|---|
| Browser `ERR_SSL_PROTOCOL_ERROR` | Cert not yet issued | `docker logs sentinel-traefik 2>&1 \| grep -i acme` |
| Browser `404 page not found` from Traefik | `Host()` rule vs request `Host:` header mismatch | `curl -fsI -H "Host: $SENTINEL_HOSTNAME" http://<host-ip>/health` |
| Browser `502 Bad Gateway` | `sentinel-serve` is unhealthy | `docker compose --profile serve logs sentinel-serve`; `docker inspect sentinel-serve --format '{{.State.Health.Status}}'` |
| `docker compose up` silently did nothing for the backend | Profile not passed | `docker compose --profile serve up -d` (the profile is required) |
| Two Traefiks fighting over 80/443 | Both bundled and BYO running | `docker ps \| grep traefik` — stop one |
| Traefik logs `Error response from daemon: <empty>` and no routers load | Traefik older than 3.6.1 + Docker Engine 29.x+ (API v1.24 refused) | Upgrade Traefik to ≥ 3.6.1 (bundled image is pinned to `traefik:v3.6`). For BYO Traefik on an older version, either upgrade, set `DOCKER_API_VERSION=1.45` on Traefik, or use a file-provider config pointing at `http://sentinel-serve:8787`. |
| Let's Encrypt rate-limit hit | Too many failed issuances | Switch to staging CA (§2.1) until green |
| `LETSENCRYPT_EMAIL required` on compose up | Forgot to set in `.env` | Set it; `docker compose ... up -d` again |
| `SENTINEL_HOSTNAME required` on compose up | Forgot to set in `.env` | Same — it's intentional; prod without hostname is never correct |
| `/health` on `http://localhost:8787` refuses from host | Dev profile not up or port not published | `docker compose --profile dev ps`; `ss -tlnp \| grep 8787` |
| Port 8787 bound to `0.0.0.0` on host | `docker-compose.yml` edited to drop the `127.0.0.1:` prefix | Restore the prefix; `ss -tlnp` should show `127.0.0.1:8787` only |

---

## 9. What is NOT in this runbook

Deliberately out of scope (see plan 06 Notes → "Explicitly NOT in this plan"):

- Basic-auth in front of `/docs` — env flag is enough
- Traefik dashboard — extra attack surface, off
- WAF / rate-limiting at Traefik — plan 05's per-token rate limit is the
  single source of truth
- Multi-host / load-balanced deploys — single-instance by plan 00
- Log aggregation (Loki/ELK) — ops concern
- Automated DNS provisioning — operator responsibility
