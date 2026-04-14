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

# ── Launch all services ─────────────────────────────────────
exec docker compose up --abort-on-container-failure "$@"
