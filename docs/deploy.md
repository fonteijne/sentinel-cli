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
| `SENTINEL_ENABLE_DOCS` | optional | Default `false`. Set `true` in `.env` to keep `/docs`, `/redoc`, `/openapi.json` reachable — used as the operator UI until plan 07 ships the dashboard. Gated behind Cloudflare Access + the bearer token like every other endpoint. |
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

## 6. Cloudflare Access — multi-user identity gate

This is the recommended production setup for **teams of two or more
operators**. It replaces plan 05's single-shared-secret posture at the
ingress layer: identity is enforced at Cloudflare's edge by SSO or email
OTP, and every request is attributable to a named person (or a named
service token). Plan 05's bearer token still runs inside Traefik as the
second layer, so every request passes two gates.

Why this matters on a home-network Mac host: Docker Desktop's vpnkit
collapses every source IP to the bridge gateway, so no IP-based
allowlist in Traefik can distinguish a Cloudflare-routed request from
a direct-origin dial — Access at the CF edge is the only place where
identity can be checked reliably (see commit `8f1025d` for the cleanup).

### Architecture

```
Browser / CLI  ──► Cloudflare ──► Traefik ──► sentinel-serve
                     ▲                ▲           ▲
                     │                │           │
                     │                │           └── Plan 05 bearer token
                     │                │                (shared secret today,
                     │                │                 per-user in plan 08)
                     │                └── TLS termination (Let's Encrypt)
                     └── Cloudflare Access policy "Operators":
                         SSO / email identity for humans,
                         service tokens for automation.
```

Humans authenticate to Cloudflare with their email + IdP, get a 24-hour
session cookie, then hit Traefik. Automation sends service-token
headers instead of the cookie. Both paths still need the Sentinel
bearer token.

### Single-operator fallback

If you are the only user, Cloudflare Access with one email in the
policy is still the right setup — the "multi-user" framing just means
you're set up to grow. For true single-user dev-box deployments
without a public DNS record, an SSH tunnel to `127.0.0.1:8787` is
simpler; §0 covers when to pick which.

### 6.1 One-time application setup

1. Cloudflare dashboard → **Zero Trust → Access → Applications → Add an
   application → Self-hosted**.
2. Application name: `Sentinel`.
3. Application domain: `${SENTINEL_HOSTNAME}` (path blank — protects
   the whole host).
4. Session duration: `24 hours`.
5. Identity providers: enable at least **One-time PIN** (email OTP) for
   zero-dependency bootstrap. Add Google / GitHub / Microsoft / Okta
   later under **Settings → Authentication** — once an IdP is wired up
   there, flip it on per-application here.
6. Add a policy:
   - Name: `Operators`
   - Action: **Allow**
   - Include → **Emails** → one row per operator email, *or* Include →
     **Emails ending in** → `@yourcompany.com` if everyone on that
     domain should have access.
7. Save.

Sanity check from an incognito window:

```bash
# Should redirect to the CF Access login, not to the API response.
curl -v https://$SENTINEL_HOSTNAME/health
```

### 6.2 Onboarding a new operator

1. **Zero Trust → Access → Applications → Sentinel → Policies →
   Operators → Edit**.
2. Add a new **Include → Emails** row with their address. Save.
3. Tell them the URL (`https://$SENTINEL_HOSTNAME`). On first visit
   they'll be prompted for SSO / OTP — no onboarding steps on your
   side beyond the policy row.
4. Optional: if your plan uses **Groups** (Zero Trust → Settings →
   Access → Groups), add them to the `operators` group and reference
   the group in the policy instead of listing emails — scales better
   past ~5 operators.

Access logs (**Zero Trust → Logs → Access**) show each successful and
denied sign-in with the email, IP, and user-agent. Use this as the
audit trail for humans.

### 6.3 Offboarding / revoking access

1. Remove the email (or group membership) from the `Operators` policy.
2. Force-expire their active session: **Zero Trust → My Team → Users →
   find the user → Revoke session**. Otherwise the existing 24-hour
   cookie stays valid until it expires.
3. If they had a service token in their name, revoke it (§6.5).

### 6.4 Service tokens (CLI / automation / CI)

For `sentinel execute --remote`, CI pipelines, or any script hitting
the API from a non-browser context, issue an Access **service token**
and whitelist it on the `Operators` policy. The service token is the
CF-edge credential; plan 05's bearer token is the in-service
credential. Both are required on every request.

**One token per actor.** Create per-operator and per-CI tokens rather
than sharing one `sentinel-cli` token — revocation is per-token, so
granular naming pays off the first time someone leaves or a laptop is
lost.

1. **Zero Trust → Access → Service Auth → Service Tokens → Create
   Service Token** → name it after the caller: `sentinel-cli-alice`,
   `sentinel-ci-github`, etc. **Save the Client ID and Client Secret
   immediately — the secret is shown once.**
2. Edit the **Operators** policy on the Sentinel application → add an
   **Include → Service Token** row → select the token you just
   created. (Multiple service-token rows allowed.)
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

Expected: 200 with the executions list. `401` means the Sentinel
bearer token is wrong; a CF Access **HTML login page** in the body
means the `CF-Access-Client-*` headers are wrong or the service token
isn't included in the policy.

### 6.5 Rotation

| What | Where | Effect |
|---|---|---|
| **Sentinel bearer token** | §5 above (shared across all operators today) | All clients need the new token |
| **Service token** | Zero Trust → Access → Service Auth → Service Tokens → rotate | Old secret invalid immediately — update automation before clicking |
| **User additions / removals** | `Operators` policy Include list | Effective at next session refresh (≤24h); force-now via §6.3 |
| **Access session duration** | Application settings → Session duration | Shorter = tighter revocation, more logins |

### 6.6 Known limitation: shared bearer token

Plan 05's in-service bearer token is a **single shared secret** today
— every operator uses the same `~/.sentinel/service_token`. That means
per-user attribution stops at the Cloudflare Access layer: Sentinel's
own logs show every request as "the one bearer token", not Alice vs.
Bob. Plan 08 (deferred) introduces per-user tokens + scopes inside
Sentinel so the two layers report the same identity end-to-end. Until
then, use Cloudflare **Access Logs** (step 6.2) as the authoritative
audit source for who did what.

### 6.7 Origin bypass — closing the remaining hole with AOP

Cloudflare Access only runs on requests that *go through Cloudflare*.
A direct TCP connection to your home IP (`${public-IP}:443`) bypasses
Access entirely — the only gate the attacker hits is plan 05's bearer
token. As long as that token stays on-disk with `0o600` inside the
container, this is usually fine, but for a multi-user team the token
becomes load-bearing across more hands.

Close this with **Authenticated Origin Pulls (AOP)** — Cloudflare
presents a client TLS certificate on every forwarded request, Traefik
rejects anything without it. Attackers dialing the origin directly
can't forge Cloudflare's cert, so the origin becomes unreachable
except through Cloudflare.

Setup (~30 min, not wired today):

1. **Cloudflare** → SSL/TLS → Origin Server → **Authenticated Origin
   Pulls** → toggle on for `${SENTINEL_HOSTNAME}`.
2. **Traefik** — download Cloudflare's [Origin Pull CA bundle][cf-aop]
   to the host, mount into the Traefik container, add a file-provider
   TLS options block:
   ```yaml
   tls:
     options:
       cf-mtls:
         clientAuth:
           caFiles:
             - /certs/cloudflare-origin-pull.crt
           clientAuthType: RequireAndVerifyClientCert
   ```
   Reference on the sentinel router:
   ```yaml
   - "traefik.http.routers.sentinel.tls.options=cf-mtls@file"
   ```
3. Verify: `curl --resolve sentinel.vectorpeaklabs.com:443:<origin-ip> https://sentinel.vectorpeaklabs.com/health`
   should fail the TLS handshake. The same URL via Cloudflare should
   still succeed.

[cf-aop]: https://developers.cloudflare.com/ssl/static/authenticated_origin_pull_ca.pem

Track as a follow-up when the user list grows or when the bearer
token is shared with a new machine.

### 6.8 Wiring the CLI (plan 04 follow-up)

`sentinel execute --remote` lands in plan 04. When it does, it must
send all three headers on every HTTPS call. Likely shape: environment
variables `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET` alongside
the existing `SENTINEL_SERVICE_TOKEN`, read once at client init and
attached by the HTTP client. One service token per developer, stored
in their personal `config/.env.local` (gitignored), not the shared
`.env`.

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

## 8. `/docs` in prod — operator UI until plan 07

Until the dashboard ships in plan 07, FastAPI's built-in Swagger UI at
`/docs` is the primary operator interface for exercising the API. It's
reachable in prod when `SENTINEL_ENABLE_DOCS=true` is set in `.env`
(default `false`). The compose definition honours the env flag directly:

```yaml
# sentinel-serve
environment:
  - SENTINEL_ENABLE_DOCS=${SENTINEL_ENABLE_DOCS:-false}
```

Standing-on configuration:

```bash
# .env
SENTINEL_ENABLE_DOCS=true
```

```bash
docker compose --profile serve up -d sentinel-serve
curl -fsI https://$SENTINEL_HOSTNAME/docs                      # 200 (after CF Access login)
```

Sign in via Cloudflare Access (§6.1) to reach the browser UI; from
there, use Swagger's "Authorize" button with `Bearer <sentinel-token>`
to exercise authenticated endpoints. Two gates are still enforced on
every call — CF Access at the edge and the plan-05 bearer token inside
the service — so enabling `/docs` does not bypass auth, only surfaces
the schema to operators who have already passed both gates.

To turn it off (e.g. before widening Access to a new viewer who should
not see the schema):

```bash
# .env
SENTINEL_ENABLE_DOCS=false

docker compose --profile serve up -d --force-recreate sentinel-serve
curl -fsI https://$SENTINEL_HOSTNAME/docs                      # 404
```

All three of `/docs`, `/redoc`, `/openapi.json` flip together — Swagger
UI fetches `/openapi.json`, so gating only `docs_url` would still leak
the schema. The factory gates them as a group.

**Why the default is still `false`:** unauthenticated probes of the
public hostname should return 404 on schema endpoints, not a routable
200 behind an auth wall. Operators explicitly opt in by setting the env
flag; it's not the kind of thing that should flip on by accident. When
plan 07 lands and provides a real UI, consider flipping the default
back off for prod deployments that don't need Swagger.

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
