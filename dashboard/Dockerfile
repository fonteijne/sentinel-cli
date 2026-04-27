# Sentinel Command Center Dashboard
#
# Multi-stage build:
#   - build:  Node 20 image runs `npm ci` + `npm run build` to produce
#             dashboard/dist/ (Vite + tsc).
#   - serve:  nginx:alpine serves the static SPA bundle on port 80.
#
# The dashboard is a static SPA fronted by an nginx that also reverse-
# proxies the FastAPI backend at `/api/` (and the WebSocket stream
# underneath it). The browser therefore talks to a single same-origin
# host, sidestepping CORS and host-port discovery. The upstream is
# `${SENTINEL_API_UPSTREAM}` — defaults to `sentinel-serve:8787` (the
# compose service name on the default network); operators can override
# at run time.

# =============================================================================
# BUILD STAGE
# =============================================================================
FROM node:20-alpine AS build

WORKDIR /app

# Install deps first for layer caching.
COPY package.json package-lock.json ./
RUN npm ci

# Copy the rest of the dashboard sources and build.
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src

RUN npm run build

# =============================================================================
# SERVE STAGE
# =============================================================================
FROM nginx:1.27-alpine AS serve

# nginx:alpine processes /etc/nginx/templates/*.template through
# `envsubst` at startup, so ${SENTINEL_API_UPSTREAM} in the template is
# substituted with the env var below before nginx loads it.
ENV SENTINEL_API_UPSTREAM=sentinel-serve:8787
# Only substitute our own variable; leaves nginx's `$var` syntax alone
# (it's already untouched without braces, but the filter is belt-and-braces
# in case a future template adds a `${nginx_var}` reference).
ENV NGINX_ENVSUBST_FILTER='SENTINEL_API_UPSTREAM'
# Drop the default site config so only our template is loaded.
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Drop the bundle into nginx's docroot.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# nginx:alpine ships a default CMD that runs nginx in the foreground; keep it.
