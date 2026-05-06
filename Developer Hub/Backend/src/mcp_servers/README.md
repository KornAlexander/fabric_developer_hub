# `src/mcp_servers/`

Stdio-MCP **subprocesses** that the backend launches and talks to over stdio.
These are *not* services in the DI sense — they're independent processes
spawned by `MCPClientManager` (see `src/services/mcp/`).

| File | Role |
|---|---|
| `fabric.py` | Fabric REST + OneLake tools exposed to the orchestrator LLM. |
| `semantic_link.py` | Semantic-link / dataset tools. |
| `stdio_jsonrpc_filter.py` | Wrapper for third-party stdio MCP servers that print non-protocol banners to stdout. |

Configuration lives in [`../mcp_servers.json`](../mcp_servers.json); the
manager reads it at startup and launches each entry as a subprocess.

## Contract

Each server is a plain `python -m …` entrypoint that speaks MCP over
stdio. It:

1. Reads stdin for MCP JSON-RPC frames.
2. Exposes a `tools/list` that enumerates Fabric operations.
3. Implements `tools/call` with a bearer token the orchestrator supplies.

## Do not

- Import anything from `services.*` or `api.*` — these run in separate
  processes and must stay self-contained. Share code via `domain/` if needed.
- Write logs or banners to stdout from a stdio MCP process. stdout is reserved
  for JSON-RPC frames; diagnostics belong on stderr.
