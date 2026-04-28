"""Agent container entrypoint.

This module is the ``ENTRYPOINT`` of the ``agenthub-agent`` Docker image.
It reads the ``SLOT_CONFIG`` environment variable, runs the agent loop,
and exits when the slot completes.

Phase 1: the agent loop calls the LLM directly but proxies tool calls
back to the orchestrator via HTTP. The orchestrator runs them through
``tool_runtime.execute()`` preserving the single security chokepoint.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime

import httpx

from services.agenthub.agent.chat_client import make_chat_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("agent")

AGENT_ROUND_TIMEOUT = 60  # seconds per LLM call


async def run_agent_loop(config: dict) -> int:
    """Run the agentic LLM loop. Returns 0 on success, 1 on error."""
    slot_id = config["slot_id"]
    agent_id = config["agent_id"]
    session_id = config["session_id"]
    role = config["role"]
    goal = config["goal"]
    system_prompt = config["system_prompt"]
    model = config.get("model", "gpt-4o")
    max_rounds = config.get("max_rounds", 15)
    allowed_tools = set(config.get("allowed_tools", []))
    orchestrator_url = config.get("orchestrator_endpoint", "http://host.docker.internal:5000")
    copilot_token = config["copilot_token"]
    workspace_id = config["workspace_id"]
    assignment_session_id = config.get("assignment_session_id", session_id)

    label = f"{agent_id}({assignment_session_id[:8]})"
    logger.info("[AGENT:%s] Starting — slot=%s role=%s", label, slot_id, role)

    # Build system message
    system_content = (
        f"{system_prompt}\n\n"
        f"WORKSPACE: {workspace_id}\n"
        f"YOUR GOAL FOR THIS JOB: {goal}\n"
        f"Emit structured phase markers in your responses:\n"
        f"PHASE_START: <phase_number> | <title>\n"
        f"PHASE_END: <phase_number>\n"
        f"ACTION: <type> | ENTITY: <name> | TYPE: <entity_type>\n"
        f"DECISION: <your reasoning summary>\n\n"
        f"SECURITY: You MUST only call tools against workspace "
        f"{workspace_id}. Cross-workspace tool calls are blocked by "
        f"policy and will be rejected.\n"
    )
    messages: list[dict] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": goal},
    ]

    # Fetch tool schemas from the orchestrator
    tools = await _fetch_tool_schemas(orchestrator_url, allowed_tools, copilot_token)

    # Build the chat client from env (default: Copilot)
    try:
        chat_client = make_chat_client(copilot_token=copilot_token)
    except ValueError as exc:
        logger.error("[AGENT:%s] ChatClient config error: %s", label, exc)
        return 1

    async with httpx.AsyncClient(timeout=AGENT_ROUND_TIMEOUT) as http:
        for round_num in range(max_rounds):
            logger.info("[AGENT:%s] Round %d/%d", label, round_num + 1, max_rounds)

            try:
                response = await chat_client.chat(
                    messages=messages,
                    tools=tools if tools else None,
                    model=model,
                    timeout=AGENT_ROUND_TIMEOUT,
                )
            except Exception as exc:
                logger.error("[AGENT:%s] LLM call failed: %s", label, exc)
                return 1

            choice = response.get("choices", [{}])[0]
            assistant_msg = choice.get("message", {})
            has_tool_calls = bool(assistant_msg.get("tool_calls"))
            content = assistant_msg.get("content") or ""

            if content:
                logger.info("[AGENT:%s] Content: %s", label, content[:200])

            if not has_tool_calls:
                logger.info("[AGENT:%s] Finished after %d rounds", label, round_num + 1)
                # Report completion to orchestrator
                await _report_completion(
                    orchestrator_url, session_id, slot_id,
                    assignment_session_id, "success", content,
                    copilot_token,
                )
                return 0

            # Process tool calls — proxy through orchestrator
            messages.append(assistant_msg)
            for tc in assistant_msg["tool_calls"]:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "unknown")
                tool_args_str = fn.get("arguments", "{}")
                try:
                    tool_args = json.loads(tool_args_str)
                except json.JSONDecodeError:
                    tool_args = {}

                logger.info("[AGENT:%s] Tool call: %s", label, tool_name)
                tool_result = await _proxy_tool_call(
                    orchestrator_url, session_id, slot_id,
                    assignment_session_id, tool_name, tool_args,
                    copilot_token,
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })

        # Hit max rounds
        logger.info("[AGENT:%s] Reached max rounds (%d)", label, max_rounds)
        await _report_completion(
            orchestrator_url, session_id, slot_id,
            assignment_session_id, "partial", "Reached max rounds",
            copilot_token,
        )
        return 0


async def _fetch_tool_schemas(
    orchestrator_url: str, allowed_tools: set[str], token: str,
) -> list[dict]:
    """Fetch OpenAI tool schemas from the orchestrator."""
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(
                f"{orchestrator_url}/api/internal/tools/schemas",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                all_tools = resp.json()
                if allowed_tools:
                    return [
                        t for t in all_tools
                        if t.get("function", {}).get("name") in allowed_tools
                    ]
                return all_tools
    except Exception as exc:
        logger.warning("[AGENT] Failed to fetch tool schemas: %s", exc)
    return []


async def _proxy_tool_call(
    orchestrator_url: str,
    session_id: str,
    slot_id: str,
    assignment_session_id: str,
    tool_name: str,
    tool_args: dict,
    token: str,
) -> str:
    """Proxy a tool call through the orchestrator's tool runtime."""
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                f"{orchestrator_url}/api/internal/tools/execute",
                json={
                    "session_id": session_id,
                    "slot_id": slot_id,
                    "assignment_session_id": assignment_session_id,
                    "tool_name": tool_name,
                    "arguments": tool_args,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                return resp.json().get("output", "")
            return f"TOOL_ERROR: HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        return f"TOOL_ERROR: {exc}"


async def _report_completion(
    orchestrator_url: str,
    session_id: str,
    slot_id: str,
    assignment_session_id: str,
    status: str,
    summary: str,
    token: str,
) -> None:
    """Report slot completion back to the orchestrator."""
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(
                f"{orchestrator_url}/api/internal/slots/complete",
                json={
                    "session_id": session_id,
                    "slot_id": slot_id,
                    "assignment_session_id": assignment_session_id,
                    "status": status,
                    "summary": summary[:2000],
                },
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception as exc:
        logger.warning("[AGENT] Failed to report completion: %s", exc)


def main():
    """Container entrypoint."""
    import base64
    encoded = os.environ.get("SLOT_CONFIG")
    if not encoded:
        logger.error("SLOT_CONFIG environment variable is missing")
        sys.exit(1)
    try:
        config = json.loads(base64.b64decode(encoded))
    except Exception as exc:
        logger.error("Failed to parse SLOT_CONFIG: %s", exc)
        sys.exit(1)

    exit_code = asyncio.run(run_agent_loop(config))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
