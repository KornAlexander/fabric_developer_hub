# syntax=docker/dockerfile:1.7
#
# Two stages:
#
#   dev        — webpack-dev-server with HMR. Requires node_modules on a
#                bind mount or named volume (populated by the host or by
#                the `frontend-installer` service in docker-compose.yaml).
#                Used via `docker compose --profile dev up`.
#
#   production — builds a static bundle in a throwaway node stage, then
#                copies dist/ into nginx:alpine. Final image ~30 MB,
#                zero host prerequisites, zero Node at runtime.
#                Used via the default `docker compose up`.
#
# ─────────────────────────────────────────────────────────────────────
# Stage 1: dev image (tiny — just Node + tsconfig, node_modules mounted)
# ─────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS dev
WORKDIR /app
COPY Frontend/tsconfig.json ./tsconfig.json
COPY .env ../.env
EXPOSE 60006
CMD ["npx", "env-cmd", "-f", "../.env", "webpack", "serve", "--config", "./tools/webpack.config.js", "--host", "0.0.0.0"]

# ─────────────────────────────────────────────────────────────────────
# Stage 2: builder — full npm ci + production webpack build
# ─────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS builder
WORKDIR /app
# Production build of the @fluentui tree is memory-hungry during minification.
ENV NODE_OPTIONS="--max-old-space-size=8192"

# Install deps (cached layer — only re-runs when lockfile changes)
COPY Frontend/package.json Frontend/package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

# Copy sources and build
COPY Frontend/tsconfig.json ./tsconfig.json
COPY Frontend/src ./src
COPY Frontend/tools ./tools
COPY Frontend/Package ./Package
COPY .env ../.env
RUN npx env-cmd -f ../.env webpack --config ./tools/webpack.config.js --mode production

# ─────────────────────────────────────────────────────────────────────
# Stage 3: production — nginx serving the static bundle
# ─────────────────────────────────────────────────────────────────────
FROM nginx:alpine AS production
# Remove the stock default.conf — we ship our own template which is
# envsubst'd at container start by nginx:alpine's docker-entrypoint. Our
# template handles port, SPA fallback, /manifests_new* CORS, and the
# workload metadata JSON served from env vars.
RUN rm -f /etc/nginx/conf.d/default.conf
COPY Frontend/tools/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=builder /app/tools/dist /usr/share/nginx/html
EXPOSE 60006
CMD ["nginx", "-g", "daemon off;"]
