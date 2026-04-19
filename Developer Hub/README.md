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

## Running locally

See the [root README](../README.md) for the full local setup (prerequisites, `.env`
template, platform notes for WSL/ARM, corporate VPN Docker daemon config, etc.).

Quick start once `.env` is populated:

```bash
cd "Developer Hub"
./start.sh
```

## Manifest templating

`Backend/manifest/WorkloadManifest.xml` and `AgentHubItem.xml` are
templates — `${WORKLOAD_NAME}`, `${CLIENT_ID}`, and `${AUDIENCE}` are substituted
from `Developer Hub/.env` at build time by `tools/manifest_package_generator.py`. Each
developer gets a personalized manifest from their own `.env` without diverging the
checked-in files.
