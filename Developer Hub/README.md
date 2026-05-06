# Developer Hub

Developer Hub is a Microsoft Fabric workload that hosts conversational agents inside Fabric
workspaces. It is packaged as a standard Fabric workload (backend + frontend + manifest)
and runs locally via Docker for development.

## Layout

- `Backend/` — FastAPI service implementing the Fabric workload contract. Uses
  uv for dependency management (`pyproject.toml` + `uv.lock`).
- `Frontend/` — React/TypeScript workload UI hosted in Fabric via the workload client
  SDK.
- `tools/DevGatewayContainer/` — Containerized Microsoft DevGateway used to proxy the
  local backend into Fabric during development.
- `docker-compose.yaml` — Orchestrates backend, frontend, manifest generator, and
  dev-gateway.

> **PBI Fixer roadmap** — see [`Frontend/src/components/PbiFixer/PLAN.md`](Frontend/src/components/PbiFixer/PLAN.md)
> for the parallel-development plan (WS-A … WS-F). Appendix A documents the Script Runner
> feature (power-user backdoor, local-dev only, env-flag gated).
>
> **Workload-level roadmap** (item persistence, manifest, lifecycle) — see [`PLAN.md`](PLAN.md).

## Running locally

See the [root README](../README.md) for the full local setup (prerequisites, `.env`
template, platform notes for WSL/ARM, corporate VPN Docker daemon config, etc.).

Quick start once `.env` is populated:

```bash
cd "Developer Hub"
./start.sh
```

`start.sh` checks the Fabric workspace capacity before Docker starts. If the
capacity assigned to `WORKSPACE_GUID` is paused or suspended, the script resumes
the matching Azure `Microsoft.Fabric/capacities` resource and waits until it is
`Active` so DevGateway registration does not fail on a detached capacity. Set
`SKIP_FABRIC_CAPACITY_START=1` to skip this check. If auto-discovery cannot map
the Fabric capacity ID to an Azure resource, set `FABRIC_CAPACITY_RESOURCE_ID`,
or set `FABRIC_CAPACITY_NAME`, `FABRIC_CAPACITY_RESOURCE_GROUP`, and
`FABRIC_CAPACITY_SUBSCRIPTION_ID`.

If you run `docker compose` directly instead of `start.sh`, export the Docker
socket group id first. The backend uses this to start one MCP runtime container
per mission.

```bash
export DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
docker compose up --build
```

Do not start the stack with Compose's `--abort-on-container-failure` or
`--abort-on-container-exit` flags. Per-mission agent and MCP runtime containers
are expected to fail independently sometimes, for example when a tool process is
OOM-killed. `start.sh` filters those abort flags so the backend, frontend, and
DevGateway remain available and the mission failure can be reported in the UI.

## Manifest templating

`Backend/manifest/WorkloadManifest.xml` and `AgentHubItem.xml` are
templates — `${WORKLOAD_NAME}`, `${CLIENT_ID}`, and `${AUDIENCE}` are substituted
from `Developer Hub/.env` at build time by `tools/manifest_package_generator.py`. Each
developer gets a personalized manifest from their own `.env` without diverging the
checked-in files.
