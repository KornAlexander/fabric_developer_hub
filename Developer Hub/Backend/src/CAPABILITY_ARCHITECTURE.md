# AgentHub capability catalog — architecture

This document captures how MCP servers, skills, and agents fit together
after the 2026-04 redesign. It is short by design — the authoritative
detail lives in the docstrings of the modules linked below.

## Layers

```
┌──────────────────────────┐     ┌──────────────────────────┐
│  mcp_servers.json        │     │  catalog.yaml            │
│  MCP server fleet:       │     │  Declarative catalog:    │
│  command, transport,     │ ──▶ │  skills + agent→skill    │
│  auth, tool_allowlist    │     │  mapping                 │
└──────────────────────────┘     └──────────────────────────┘
              │                              │
              ▼                              ▼
     ┌───────────────────────────────────────────────┐
     │            Startup pipeline                   │
     │  1. MCPClientManager.discover_tools()         │
     │  2. capability_registry.validate_catalog()    │
     │     → logs drift between catalog and tools    │
     │  3. agent templates attach skills from catalog│
     └───────────────────────────────────────────────┘
              │                              │
              ▼                              ▼
     ┌──────────────────────────┐  ┌──────────────────────────┐
     │  Compose LLM             │  │  MCPClientManager        │
     │  picks agents + skills   │  │  routes call_tool() to   │
     │  per task                │  │  the right transport     │
     └──────────────────────────┘  └──────────────────────────┘
```

## Files of interest

| Layer | File | Purpose |
|---|---|---|
| MCP fleet | [`mcp_servers.json`](./mcp_servers.json) | Declares each MCP server's transport, command, and optional tool allow-list. |
| MCP routing | [`services/mcp/mcp_client_manager.py`](./services/mcp/mcp_client_manager.py) | Discovers tools, enforces arg policies, dispatches `call_tool`. Transport is pluggable (stdio today, streamable-HTTP scaffolded). |
| Capability catalog | [`services/agenthub/catalog.yaml`](./services/agenthub/catalog.yaml) | Skills + agent→skill map. Editable without a code change. |
| Catalog loader | [`services/agenthub/catalog_loader.py`](./services/agenthub/catalog_loader.py) | Parses base YAML plus optional overlay (`AGENTHUB_CATALOG_OVERLAY` env var). |
| Catalog validator | [`services/agenthub/capability_registry.py`](./services/agenthub/capability_registry.py) | Crosscheck: every tool a skill names is provided by a live server; every skill an agent names exists. Non-fatal; logs at boot. |
| Agent templates | [`services/agenthub/agent_registry.py`](./services/agenthub/agent_registry.py) | Per-agent persona + prompt. `available_tools` derived from the attached skills' tool lists (no duplication). |

## Lifecycle

```
   import agent_registry
      │
      ▼
   load_catalog()  ◀── reads catalog.yaml (+ overlay)
      │
      ▼
   SKILLS, _AGENT_SKILLS populated
      │
      ▼
   AgentTemplate(...) registered
      │
      ▼
   _attach_skills() for each template
      │
      ▼
   t.skills, t.available_tools filled from catalog
      │
      ▼ (at startup, in main.py)
   MCPClientManager.discover_tools()
      │
      ▼
   capability_registry.validate_catalog(SKILLS, _AGENT_SKILLS, mgr)
      │
      ▼
   Backend ready — errors logged if catalog drifts from live tools.
```

## Adding a new MCP server

1. Register it in [`mcp_servers.json`](./mcp_servers.json):
   ```json
   "my-server": {
     "command": "npx",
     "args": ["-y", "@org/my-mcp"],
     "requires_auth": false,
     "tool_allowlist": ["safe_tool_1", "safe_tool_2"]
   }
   ```
   Use `tool_allowlist` to avoid name collisions with existing tools.
2. Add a skill to [`catalog.yaml`](./services/agenthub/catalog.yaml)
   that references the new tools under `tools:`.
3. Attach the skill to the agents that should see it via
   `agent_skills:`.
4. Restart — startup validation will complain if anything is missing.

No Python changes are needed for steps 2–3.

## Adding a new skill without touching production catalog

Create an overlay YAML file and point at it with
`AGENTHUB_CATALOG_OVERLAY=/path/to/overlay.yaml`. The overlay may
contain just a `skills:` section (new skills appended) or just
`agent_skills:` (replaces per-agent lists) or both. See
`catalog_loader._load_one` for the precise merge semantics.

## Deployment notes

### Making MCP servers reachable inside the container

Two classes of MCP server need special treatment when the backend
runs in Docker:

1. **Python scripts under `${REPO_DIR}/mcp/`** (e.g. `pbi-fixer`,
   `pbir-tools`). The build context is `Developer Hub/Backend/`, so
   these files are outside the Docker COPY path. The compose file
   bind-mounts the repo-root `mcp/` directory at
   `/opt/agenthub-mcp/mcp:ro` and sets `MCP_REPO_DIR=/opt/agenthub-mcp`
   so the `${REPO_DIR}/mcp/...` templates in `mcp_servers.json`
   resolve. The path deliberately avoids `/app/mcp` because that
   would shadow the installed `mcp` Python SDK package and break
   `from mcp import ClientSession` at import time. Without the mount
   the manager silently prunes those servers and their skills become
   unreachable.

2. **Node-based MCPs launched via `npx`** (e.g. `fabric-docs` →
   `@microsoft/fabric-mcp`). The production image is `python:3.13-slim`
   and ships no Node.js runtime, so `npx` is absent and the server
   fails to discover. The capability validator classifies this as
   an **ops** issue (one WARNING line with the unavailable server
   list) rather than a catalog bug. To enable the server, opt in by
   adding to the Backend Dockerfile's production stage:

   ```dockerfile
   RUN apt-get update \
       && apt-get install -y --no-install-recommends nodejs npm \
       && rm -rf /var/lib/apt/lists/*
   ```

   (~150 MB image size increase; skip if `docs_*` grounding isn't
   needed for your deployment.)

### Reading the capability validator output

On boot, `capability_registry.validate_catalog` emits at most one
line per skill plus one `INFO` summary of unavailable servers.
Severity rules:

* `ERROR` — skill references a tool no server provides **and** every
  server started successfully. Fix the YAML.
* `WARNING` — skill references a tool no server provides **and** at
  least one server is pruned/failed. Fix the ops problem (mount,
  install, credentials) or accept the skill is offline this deploy.

## Deferred work

* **Fabric Remote MCP (streamable HTTP)** — scaffolded in
  `MCPClientManager._start_http_server` but raises
  `NotImplementedError`. Blocked on Microsoft shipping Service
  Principal auth for the Remote MCP; the current preview only
  supports browser-interactive Entra ID, which cannot proxy our OBO
  tokens.
* **Per-tenant catalog editing from the workspace UI** — the overlay
  mechanism is ready; the UI piece is a product decision (what can a
  workspace admin override? who reviews?) and is out of scope here.

## Horizontal scale posture

The backend is designed for many users × multiple concurrent sessions per user. Today's
single-replica Docker Compose topology handles this up to the limits of one uvicorn
worker; scaling beyond one backend replica requires the fixes below.

### What already scales within one replica

* `AuthenticationService` is a singleton registered in `ServiceRegistry`, and its
  `_msal_apps` dict persists one `msal.ConfidentialClientApplication` per tenant. MSAL's
  built-in in-memory cache serves repeat OBO calls for the same `(user_assertion, scopes)`
  for the full token lifetime (~1 h) — no Entra RTT on cache hits.
* All MSAL apps share one `requests.Session` with `urllib3` `pool_maxsize=50`, so bursty
  parallel OBO exchanges don't saturate the pool.
* OBO + S2S calls are wrapped in `asyncio.to_thread` inside `AuthenticationService` and
  parallelized in `build_composite_token` via `asyncio.gather`, so slow Entra RTTs do not
  stall the event loop.
* `MCPClientManager.discover_tools` fans server discovery out with `asyncio.gather`.
* Rate limiter is keyed by `(user_id, action)` so one noisy user cannot starve others.

### What breaks at >1 backend replica

* **Rate limiter is per-process.** `services/agenthub/rate_limit.py` is a `threading.Lock`
  + dict. Two replicas → total budget becomes `2 × declared`, and a user hitting replica A
  has no awareness of their traffic on replica B. Swap to a Redis-backed bucket (the
  interface comment already anticipates this) or move enforcement to APIM / ingress.
* **MSAL token cache is per-process.** Each replica warms its own cache, so a user bounced
  between replicas pays the Entra RTT on every replica hop. Options: sticky sessions by
  `oid` claim, or plug a shared `SerializableTokenCache` backed by Redis.
* **MCP subprocess-per-call.** `MCPClientManager.call_tool` spawns a fresh stdio
  subprocess per invocation. For N users × M sessions × K tool calls this becomes the
  dominant cost. Mitigations: warm per-server sessions reused across calls, or a subprocess
  pool. Deferred — larger refactor.
* **Single-replica deployment.** `docker-compose.yaml` has one backend; production
  multi-replica needs a load balancer and the two distributed-state fixes above.

When adding a second replica, address the rate limiter and MSAL cache first; MCP
subprocess pooling can wait until profiling shows it dominates.
