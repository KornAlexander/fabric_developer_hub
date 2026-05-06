#!/usr/bin/env bash
set -euo pipefail

if [ -f .env ]; then
  # shellcheck disable=SC1091
  . ./.env
fi

az_signed_in() {
  command -v az >/dev/null 2>&1 && az account show >/dev/null 2>&1
}

azure_rest_tsv() {
  az rest --only-show-errors "$@" -o tsv 2>/dev/null || true
}

fabric_capacity_arm_state() {
  local capacity_arm_id="$1"
  azure_rest_tsv \
    --method get \
    --uri "https://management.azure.com${capacity_arm_id}?api-version=2023-11-01" \
    --query properties.state
}

discover_fabric_capacity_arm_resource() {
  local workspace_capacity_id="${1:-}"
  local capacity_name="${FABRIC_CAPACITY_NAME:-}"
  local capacity_info=""
  local arm_info=""

  if [ -n "${FABRIC_CAPACITY_RESOURCE_ID:-}" ]; then
    printf '%s\t%s\t%s\n' "${FABRIC_CAPACITY_RESOURCE_ID}" "${capacity_name:-unknown}" ""
    return 0
  fi

  if [ -n "$capacity_name" ] && [ -n "${FABRIC_CAPACITY_RESOURCE_GROUP:-}" ] && [ -n "${FABRIC_CAPACITY_SUBSCRIPTION_ID:-}" ]; then
    printf '/subscriptions/%s/resourceGroups/%s/providers/Microsoft.Fabric/capacities/%s\t%s\t%s\n' \
      "${FABRIC_CAPACITY_SUBSCRIPTION_ID}" \
      "${FABRIC_CAPACITY_RESOURCE_GROUP}" \
      "$capacity_name" \
      "$capacity_name" \
      ""
    return 0
  fi

  if [ -z "$workspace_capacity_id" ]; then
    return 1
  fi

  capacity_info=$(azure_rest_tsv \
    --resource https://analysis.windows.net/powerbi/api \
    --method get \
    --uri https://api.powerbi.com/v1.0/myorg/capacities \
    --query "value[?id=='${workspace_capacity_id}'] | [0].[displayName,state]")

  if [ -z "$capacity_info" ]; then
    return 1
  fi

  IFS=$'\t' read -r capacity_name _ <<< "$capacity_info"
  if [ -z "$capacity_name" ] || [ "$capacity_name" = "None" ]; then
    return 1
  fi

  if az extension show --name resource-graph >/dev/null 2>&1; then
    arm_info=$(az graph query --only-show-errors --first 1000 \
      -q "resources | where type =~ 'microsoft.fabric/capacities' | where name =~ '${capacity_name}' | project id, name, state=tostring(properties.state) | take 1" \
      --query "data[0].[id,name,state]" \
      -o tsv 2>/dev/null || true)
  fi

  if [ -z "$arm_info" ]; then
    arm_info=$(az resource list --only-show-errors \
      --resource-type Microsoft.Fabric/capacities \
      --query "[?name=='${capacity_name}'] | [0].[id,name,properties.state]" \
      -o tsv 2>/dev/null || true)
  fi

  if [ -z "$arm_info" ]; then
    return 1
  fi

  printf '%s\n' "$arm_info"
}

ensure_fabric_capacity_running() {
  if [ "${SKIP_FABRIC_CAPACITY_START:-0}" = "1" ]; then
    echo "⏭️  Skipping Fabric capacity auto-start (SKIP_FABRIC_CAPACITY_START=1)"
    return 0
  fi

  if ! az_signed_in; then
    echo "⚠️  Host Azure CLI is not logged in; skipping Fabric capacity auto-start"
    return 0
  fi

  if [ -z "${WORKSPACE_GUID:-}" ]; then
    echo "⚠️  WORKSPACE_GUID is not set; skipping Fabric capacity auto-start"
    return 0
  fi

  echo "🔎 Checking Fabric workspace capacity..."
  local workspace_capacity_id=""
  workspace_capacity_id=$(azure_rest_tsv \
    --resource https://api.fabric.microsoft.com \
    --method get \
    --uri "https://api.fabric.microsoft.com/v1/workspaces/${WORKSPACE_GUID}" \
    --query capacityId)

  if [ -z "$workspace_capacity_id" ] || [ "$workspace_capacity_id" = "None" ]; then
    echo "⚠️  Workspace has no capacityId or it could not be read; skipping capacity auto-start"
    return 0
  fi

  local arm_info=""
  arm_info=$(discover_fabric_capacity_arm_resource "$workspace_capacity_id" || true)
  if [ -z "$arm_info" ]; then
    echo "⚠️  Could not resolve Fabric capacity ${workspace_capacity_id} to an Azure resource."
    echo "   Set FABRIC_CAPACITY_RESOURCE_ID, or FABRIC_CAPACITY_NAME + FABRIC_CAPACITY_RESOURCE_GROUP + FABRIC_CAPACITY_SUBSCRIPTION_ID."
    return 0
  fi

  local capacity_arm_id=""
  local capacity_name=""
  local capacity_state=""
  arm_info=$(printf '%s' "$arm_info" | tr '\n' '\t')
  IFS=$'\t' read -r capacity_arm_id capacity_name capacity_state <<< "$arm_info"
  local capacity_label="${capacity_name:-$capacity_arm_id}"

  if [ -z "$capacity_state" ] || [ "$capacity_state" = "None" ]; then
    capacity_state=$(fabric_capacity_arm_state "$capacity_arm_id")
  fi
  capacity_state="${capacity_state:-Unknown}"

  if [ "$capacity_state" = "Active" ]; then
    echo "✅ Fabric capacity ${capacity_label} is Active"
    return 0
  fi

  echo "⚡ Fabric capacity ${capacity_label} is ${capacity_state}; resuming it..."
  if ! az rest --only-show-errors \
    --method post \
    --uri "https://management.azure.com${capacity_arm_id}/resume?api-version=2023-11-01" \
    -o none; then
    echo "❌ Failed to resume Fabric capacity ${capacity_label}."
    exit 1
  fi

  local timeout_seconds="${FABRIC_CAPACITY_RESUME_TIMEOUT_SECONDS:-600}"
  local started_at=$SECONDS
  while [ $((SECONDS - started_at)) -lt "$timeout_seconds" ]; do
    capacity_state=$(fabric_capacity_arm_state "$capacity_arm_id")
    capacity_state="${capacity_state:-Unknown}"
    case "$capacity_state" in
      Active)
        echo "✅ Fabric capacity ${capacity_label} is Active"
        return 0
        ;;
      Failed|Deleting)
        echo "❌ Fabric capacity ${capacity_label} entered state ${capacity_state}."
        exit 1
        ;;
      *)
        echo "   Capacity state: ${capacity_state}; waiting..."
        sleep 10
        ;;
    esac
  done

  echo "❌ Timed out waiting for Fabric capacity ${capacity_label} to become Active."
  exit 1
}

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

ensure_fabric_capacity_running

# ── Match backend container access to the host Docker socket ─
# Per-mission MCP runtime containers are started by the backend, so Compose
# must add the backend user to the host socket's group. Keep this explicit:
# a guessed group id can make mission startup fail only after orchestration.
if [ -S /var/run/docker.sock ]; then
  DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
  export DOCKER_GID
else
  echo "❌ Docker socket not found at /var/run/docker.sock"
  exit 1
fi

# ── DevGateway amd64 compatibility on ARM hosts ─────────────
# Microsoft ships the Fabric DevGateway runtime as x64-only, so the
# dev-gateway Compose service is pinned to linux/amd64. On arm64 Linux
# Docker hosts that requires binfmt/QEMU registration; without it Docker
# fails immediately with: "exec /bin/bash: exec format error".
host_arch=$(uname -m)
if [ "$host_arch" = "aarch64" ] || [ "$host_arch" = "arm64" ]; then
  if ! docker run --rm --platform linux/amd64 alpine:3 true >/dev/null 2>&1; then
    echo "🧰 Installing amd64 container emulation for Fabric DevGateway..."
    docker run --privileged --rm tonistiigi/binfmt --install amd64 >/dev/null
    if ! docker run --rm --platform linux/amd64 alpine:3 true >/dev/null 2>&1; then
      echo "❌ Docker still cannot run linux/amd64 containers."
      echo "   Run manually: docker run --privileged --rm tonistiigi/binfmt --install amd64"
      echo "   Then retry: ./start.sh ${1:-}"
      exit 1
    fi
  fi
fi

# ── Clean up previous run ────────────────────────────────────
# Stop containers and regenerate the manifest volume.
docker compose --profile prod --profile dev down 2>/dev/null || true
docker compose -p developerhub --profile prod --profile dev down 2>/dev/null || true
docker volume rm developer-hub_manifest-pkg 2>/dev/null || true
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
# Do not use Compose's --abort-on-container-failure/--abort-on-container-exit
# here. Mission agent/MCP runtime containers are isolated execution units; if
# one OOMs or exits, the backend must report that as a mission error while the
# backend, frontend, and DevGateway stay alive for recovery and inspection.
compose_args=()
for arg in "$@"; do
  case "$arg" in
    --abort-on-container-failure|--abort-on-container-exit)
      echo "⚠️  Ignoring ${arg}; mission runtime failures must not tear down Developer Hub"
      ;;
    *)
      compose_args+=("$arg")
      ;;
  esac
done

exec docker compose up --build "${compose_args[@]}"
