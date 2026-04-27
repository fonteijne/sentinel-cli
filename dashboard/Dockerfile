# Sentinel Command Center Dashboard
#
# Multi-stage build:
#   - build:  Node 20 image runs `npm ci` + `npm run build` to produce
#             dashboard/dist/ (Vite + tsc).
#   - serve:  nginx:alpine serves the static SPA bundle on port 80.
#
# The dashboard is a pure static SPA — it talks to the FastAPI backend
# from the user's browser, not from this container. The API base URL is
# entered on the first-load splash (or via ?token=…) and persists in the
# browser, so this image carries no backend wiring of its own.

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

# Replace the default site config with one that does SPA fallback.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Drop the bundle into nginx's docroot.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# nginx:alpine ships a default CMD that runs nginx in the foreground; keep it.
