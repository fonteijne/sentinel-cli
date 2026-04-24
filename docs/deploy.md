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

## 6. Cloudflare Access (identity gate in front of Traefik)

Plan 06's orange-cloud variant relies on **Cloudflare Access** as the
identity gate at the edge. Access enforces before any traffic reaches
the origin, which is what makes the setup safe on a home-network Mac
(Docker Desktop's vpnkit collapses source IPs to the bridge gateway,
so IP-based allowlists in Traefik cannot distinguish a Cloudflare-routed
request from a direct-origin dial — see the cleanup in commit `8f1025d`).

Behind Access, plan 05's bearer token is still the service-level auth.
Two layers, one on each side of Cloudflare.

### 6.1 One-time application setup (humans via SSO)

1. Cloudflare dashboard → **Zero Trust → Access → Applications → Add an
   application → Self-hosted**.
2. Application domain: `${SENTINEL_HOSTNAME}` (path blank).
3. Session duration: `24 hours`.
4. Identity providers: enable at least **One-time PIN** (email OTP) for
   zero-dependency bootstrap. Add Google / GitHub / Microsoft later under
   **Settings → Authentication** if you want SSO.
5. Add a policy:
   - Name: `Operators`
   - Action: **Allow**
   - Include → **Emails** → one row per operator email.
6. Save.

Sanity check from an incognito window:

```bash
# Should redirect to the CF Access login, not the API response.
curl -v https://$SENTINEL_HOSTNAME/health
```

### 6.2 Service tokens (CLI / automation)

For `sentinel execute --remote` or any script hitting the API from a
non-browser context, issue an Access **service token** and whitelist it
on the `Operators` policy. The service token is the CF-edge credential;
plan 05's bearer token is the in-service credential. Both are required
on every request.

1. **Zero Trust → Access → Service Auth → Service Tokens → Create
   Service Token** → name it `sentinel-cli` (or per-client if you want
   to revoke granularly). **Save the Client ID and Client Secret
   immediately — the secret is shown once.**
2. Edit the **Operators** policy on the Sentinel application → add an
   **Include** row: selector **Service Token** = `sentinel-cli`.
3. Calls require three headers:
   ```
   CF-Access-Client-Id:     <client-id>
   CF-Access-Client-Secret: <client-secret>
   Authorization:           Bearer <sentinel-service-token>
   ```

Quick test:

```bash
curl -fsS https://$SENTINEL_HOSTNAME/executions \
  -H "CF-Access-Client-Id: $CF_ACCESS_CLIENT_ID" \
  -H "CF-Access-Client-Secret: $CF_ACCESS_CLIENT_SECRET" \
  -H "Authorization: Bearer $SENTINEL_SERVICE_TOKEN"
```

Expected: 200 with the executions list. `401` means the Sentinel bearer
token is wrong; a CF Access **HTML login page** in the body means the
`CF-Access-Client-*` headers are wrong or the service token isn't
included in the policy.

### 6.3 Rotation

- **Sentinel bearer token**: §5 above.
- **Service token**: Zero Trust → Access → Service Auth → Service Tokens
  → rotate inline. The old secret is invalid immediately; update
  automation before clicking.
- **User additions / removals**: edit the `Operators` policy Include
  list. Revocation takes effect at next session refresh (≤24h with the
  session duration above; force-refresh via **My Team → Users → Revoke
  session**).

### 6.4 Wiring the CLI (follow-up, not today)

`sentinel execute --remote` lands in plan 04. When it does, it must
send all three headers on every HTTPS call. Likely shape: environment
variables `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` alongside
the existing `SENTINEL_SERVICE_TOKEN`, read once at client init and
attached by the HTTP client.

---

## 7. Health check

`/health` is **unauthenticated** by plan 05 design so container runtimes,
compose healthchecks, and external uptime probes work without a token:

```bash
curl -fsI https://$SENTINEL_HOSTNAME/health                    # public
docker inspect sentinel-serve --format '{{.State.Health.Status}}'  # compose view
```

If `/health` is 200 but the container reports `unhealthy`, suspect the
internal curl: `docker exec sentinel-serve curl -v http://127.0.0.1:8787/health`.

---

## 8. `/docs` in prod

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

## 9. Troubleshooting

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
| Curl returns a Cloudflare HTML login page instead of JSON | `CF-Access-Client-Id` / `CF-Access-Client-Secret` missing, wrong, or service token not in the `Operators` policy Include list | Re-check both headers; verify the service token appears under **Access → Applications → Sentinel → Policies → Operators → Include** |
| `401 Unauthorized` from Sentinel after passing CF Access | Sentinel bearer token missing or stale | `Authorization: Bearer <token>`; rotate per §5 if the token on disk and the client disagree |
| Browser succeeds, CLI fails with CF login HTML | Service-token path not wired up; browser has a CF session cookie but CLI does not | Issue a service token (§6.2) and send the two `CF-Access-Client-*` headers from the CLI |

---

## 10. What is NOT in this runbook

Deliberately out of scope (see plan 06 Notes → "Explicitly NOT in this plan"):

- Basic-auth in front of `/docs` — env flag is enough
- Traefik dashboard — extra attack surface, off
- WAF / rate-limiting at Traefik — plan 05's per-token rate limit is the
  single source of truth
- Multi-host / load-balanced deploys — single-instance by plan 00
- Log aggregation (Loki/ELK) — ops concern
- Automated DNS provisioning — operator responsibility
