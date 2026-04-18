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
# Stop containers and remove the manifest volume so it gets regenerated
docker compose down -v 2>/dev/null || true

# ── Launch all services ─────────────────────────────────────
# --build: ensure images reflect any Dockerfile / non-mounted source changes.
# Manifest XML templates are bind-mounted (see docker-compose.yaml), so
# editing WorkloadManifest.xml / Item1.xml is picked up without a rebuild —
# but --build is cheap thanks to layer caching and covers everything else.
exec docker compose up --build --abort-on-container-failure "$@"
