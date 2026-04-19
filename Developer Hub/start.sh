#!/usr/bin/env bash
set -euo pipefail

# ── Acquire a Fabric token from the host Azure CLI ──────────
# If the host is already logged in, this skips the slow device-code flow
# inside the container entirely.

echo "🔑 Acquiring Power BI token from host Azure CLI..."
if command -v az &>/dev/null; then
  DEVGATEWAY_TOKEN=$(az account get-access-token \
    --scope https://analysis.windows.net/powerbi/api/.default \
    --query accessToken -o tsv 2>/dev/null) || true
fi

if [ -n "${DEVGATEWAY_TOKEN:-}" ]; then
  echo "✅ Token acquired — device-code login will be skipped"
  export DEVGATEWAY_TOKEN
else
  echo "⚠️  Could not get token from host (az CLI missing or not logged in)"
  echo "   Falling back to device-code login inside the container"
fi

# ── Clean up previous run ────────────────────────────────────
# Stop containers and regenerate the manifest volume.
docker compose --profile prod --profile dev down 2>/dev/null || true
docker volume rm developerhub_manifest-pkg 2>/dev/null || true

# ── Frontend dev-mode host-install guard ────────────────────
# When running `./start.sh dev`, we need node_modules on the host
# (bind-mounted into the frontend-dev container).
if [ "${1:-}" = "dev" ]; then
  export COMPOSE_PROFILES=dev
  shift
  if [ ! -d Frontend/node_modules ] || [ Frontend/package-lock.json -nt Frontend/node_modules ]; then
    if command -v npm &>/dev/null; then
      echo "📦 Installing frontend deps on host (dev mode)..."
      (cd Frontend && npm ci --no-audit --no-fund)
    else
      echo "❌ Dev mode requires Node/npm on the host. Install Node 20+ or use prod mode."
      exit 1
    fi
  fi
  echo "🔧 Launching in DEV mode (webpack-dev-server + HMR)"
else
  export COMPOSE_PROFILES=prod
  echo "🚀 Launching in PROD mode (nginx, static bundle, no host Node required)"
  echo "   (use './start.sh dev' for HMR during active development)"
fi

# ── Launch all services ─────────────────────────────────────
exec docker compose up --build --abort-on-container-failure "$@"
