"""Agent container entrypoint.

This module is the ``ENTRYPOINT`` of the ``developer-hub-agent`` Docker image.
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
import re
import sys
import time
from datetime import UTC, datetime

import httpx

from .chat_client import make_chat_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("agent")

AGENT_ROUND_TIMEOUT = 60  # seconds per LLM call
AGENT_TOOL_CALL_TIMEOUT = int(os.environ.get("AGENT_TOOL_CALL_TIMEOUT", "1200"))
AGENT_TOOL_SCHEMA_SAFE_LIMIT = 120
AGENT_MAX_TOOLS = min(int(os.environ.get("AGENT_MAX_TOOLS", str(AGENT_TOOL_SCHEMA_SAFE_LIMIT))), AGENT_TOOL_SCHEMA_SAFE_LIMIT)
AGENT_TOOL_RESULT_MAX_CHARS = int(os.environ.get("AGENT_TOOL_RESULT_MAX_CHARS", "8000"))


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
    run_id = config.get("run_id") or assignment_session_id
    turn_id = f"{assignment_session_id}-turn"
    tool_count = 0
    started_at = time.monotonic()
    logger.info("[AGENT:%s] Starting — slot=%s role=%s", label, slot_id, role)
    await _emit_pi_event(orchestrator_url, session_id, slot_id, assignment_session_id, "pi.subagents.status", {
        "runId": run_id,
        "agent": agent_id,
        "agentId": assignment_session_id,
        "agentName": role,
        "mode": "single",
        "state": "running",
        "activityState": "active_long_running",
        "task": role,
        "summary": "Foreground Pi subagent session started inside the isolated container.",
        "turnCount": 0,
        "toolCount": 0,
        "durationMs": 0,
        "sessionFile": f"agenthub://sessions/{session_id}/subagents/{run_id}.jsonl",
    }, copilot_token)
    await _emit_pi_event(orchestrator_url, session_id, slot_id, assignment_session_id, "pi.subagent.update", {
        "agentId": assignment_session_id,
        "agentName": role,
        "role": role,
        "state": "running",
        "task": goal[:500],
        "summary": "Pi subagent is running in the container and streaming observability events.",
        "runId": run_id,
        "mode": "single",
    }, copilot_token)
    await _emit_pi_event(orchestrator_url, session_id, slot_id, assignment_session_id, "pi.turn.start", {
        "turnId": turn_id,
        "agentId": assignment_session_id,
        "agentName": role,
        "model": model,
        "title": role,
    }, copilot_token)
    required_tools = _required_tools_for_goal(agent_id, goal)
    completed_required_tools: set[str] = set()

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
        f"WRITE OPERATIONS: Creation and write tools are pre-authorized. "
        f"When this job requires producing Fabric deliverables, call the "
        f"appropriate tool directly; text-only completion is not enough.\n\n"
        f"REPORT QUALITY CONTRACT: For Power BI reports, dashboards, and other "
        f"user-facing analytics deliverables, default to Power BI Data Stories / "
        f"world-championship-caliber quality unless the user explicitly requests "
        f"a lower-fidelity/specific design or supplies a different sample to mimic. "
        f"A vague request like 'nice' or 'appropriate' is not a lower bar. Require "
        f"clear information hierarchy, 3-30-300 storytelling, top-left KPI overview, "
        f"interactive filter-and-zoom usability, details on demand, methodology/source "
        f"transparency, accessible labels/alt text/contrast/tab order, modern multi-hue "
        f"styling, and screenshot-backed browser verification. Reject default-looking, "
        f"cramped, single-card, storyless, or inaccessible report shells.\n\n"
        f"SECURITY: You MUST only call tools against workspace "
        f"{workspace_id}. Cross-workspace tool calls are blocked by "
        f"policy and will be rejected.\n"
    )
    messages: list[dict] = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": goal},
    ]

    # Fetch tool schemas from the orchestrator
    tools = await _fetch_tool_schemas(
        orchestrator_url,
        allowed_tools,
        copilot_token,
        session_id,
        assignment_session_id,
    )

    # Build the chat client from env (default: Copilot)
    try:
        chat_client = make_chat_client(copilot_token=copilot_token)
    except ValueError as exc:
        logger.error("[AGENT:%s] ChatClient config error: %s", label, exc)
        await _emit_pi_terminal_events(
            orchestrator_url, session_id, slot_id, assignment_session_id,
            run_id, turn_id, role, agent_id, "failed",
            f"Chat client configuration error: {exc}", tool_count, started_at,
            copilot_token,
        )
        return 1

    async with httpx.AsyncClient(timeout=AGENT_ROUND_TIMEOUT) as http:
        for round_num in range(max_rounds):
            logger.info("[AGENT:%s] Round %d/%d", label, round_num + 1, max_rounds)
            await _emit_pi_event(orchestrator_url, session_id, slot_id, assignment_session_id, "pi.subagents.status", {
                "runId": run_id,
                "agent": agent_id,
                "agentId": assignment_session_id,
                "agentName": role,
                "mode": "single",
                "state": "running",
                "activityState": "active_long_running",
                "task": role,
                "summary": f"Round {round_num + 1}/{max_rounds}: requesting model response.",
                "turnCount": round_num + 1,
                "toolCount": tool_count,
                "durationMs": int((time.monotonic() - started_at) * 1000),
                "progress": [{
                    "index": 0,
                    "agent": agent_id,
                    "status": "running",
                    "activityState": "active_long_running",
                    "task": role,
                    "recentOutput": [f"Round {round_num + 1}/{max_rounds}: model request started"],
                    "recentTools": [],
                    "toolCount": tool_count,
                    "turnCount": round_num + 1,
                    "tokens": 0,
                    "durationMs": int((time.monotonic() - started_at) * 1000),
                }],
            }, copilot_token)
            missing_required = _missing_required_tool(required_tools, completed_required_tools)
            available_tool_names = {t.get("function", {}).get("name") for t in tools}
            round_tools = _prioritize_tools_for_round(tools, agent_id, goal, required_tools)
            tool_choice: dict | str | None = None
            if missing_required and missing_required in available_tool_names:
                round_tools = [
                    tool for tool in round_tools
                    if tool.get("function", {}).get("name") == missing_required
                ]
                tool_choice = {"type": "function", "function": {"name": missing_required}}
            elif len(round_tools) > AGENT_MAX_TOOLS:
                logger.warning(
                    "[AGENT:%s] Tool schema list has %d tools; truncating to %d for LLM compatibility",
                    label, len(round_tools), AGENT_MAX_TOOLS,
                )
                round_tools = round_tools[:AGENT_MAX_TOOLS]

            streamed_chunks: list[str] = []

            async def emit_model_delta(delta: str) -> None:
                if not delta:
                    return
                streamed_chunks.append(delta)
                await _emit_pi_event(orchestrator_url, session_id, slot_id, assignment_session_id, "pi.turn.delta", {
                    "turnId": turn_id,
                    "textDelta": delta,
                    "trust": {"level": "trusted", "source": "model"},
                }, copilot_token)

            try:
                response = await chat_client.chat_stream(
                    messages=messages,
                    tools=round_tools if round_tools else None,
                    tool_choice=tool_choice,
                    model=model,
                    timeout=AGENT_ROUND_TIMEOUT,
                    on_delta=emit_model_delta,
                )
            except Exception as stream_exc:
                logger.warning("[AGENT:%s] Streaming LLM call failed, retrying without streaming: %s", label, stream_exc)
                try:
                    response = await chat_client.chat(
                        messages=messages,
                        tools=round_tools if round_tools else None,
                        tool_choice=tool_choice,
                        model=model,
                        timeout=AGENT_ROUND_TIMEOUT,
                    )
                except Exception as exc:
                    logger.error("[AGENT:%s] LLM call failed: %s", label, exc)
                    await _emit_pi_terminal_events(
                        orchestrator_url, session_id, slot_id, assignment_session_id,
                        run_id, turn_id, role, agent_id, "failed",
                        f"LLM call failed: {exc}", tool_count, started_at,
                        copilot_token,
                    )
                    return 1
            choice = response.get("choices", [{}])[0]
            assistant_msg = choice.get("message", {})
            has_tool_calls = bool(assistant_msg.get("tool_calls"))
            content = assistant_msg.get("content") or ""

            if content:
                logger.info("[AGENT:%s] Content: %s", label, content[:200])
                if not streamed_chunks:
                    await _emit_pi_event(orchestrator_url, session_id, slot_id, assignment_session_id, "pi.turn.delta", {
                        "turnId": turn_id,
                        "textDelta": content,
                        "trust": {"level": "trusted", "source": "model"},
                    }, copilot_token)

            if not has_tool_calls:
                missing_required = _missing_required_tool(required_tools, completed_required_tools)
                if missing_required:
                    correction = (
                        f"Your role requires a real `{missing_required}` tool call. "
                        "Text-only completion is not enough. Call the tool now against "
                        f"workspace {workspace_id} and the requested run folder."
                    )
                    logger.warning("[AGENT:%s] Missing required tool call: %s", label, missing_required)
                    messages.append(assistant_msg)
                    messages.append({"role": "user", "content": correction})
                    continue

                logger.info("[AGENT:%s] Finished after %d rounds", label, round_num + 1)
                # Report completion to orchestrator
                await _report_completion(
                    orchestrator_url, session_id, slot_id,
                    assignment_session_id, "success", content,
                    copilot_token,
                )
                await _emit_pi_terminal_events(
                    orchestrator_url, session_id, slot_id, assignment_session_id,
                    run_id, turn_id, role, agent_id, "completed", content,
                    tool_count, started_at, copilot_token,
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
                tool_count += 1
                tool_call_id = tc.get("id") or f"{turn_id}-tool-{tool_count}"
                await _emit_pi_event(orchestrator_url, session_id, slot_id, assignment_session_id, "pi.tool.start", {
                    "toolCallId": tool_call_id,
                    "turnId": turn_id,
                    "agentId": assignment_session_id,
                    "agentName": role,
                    "toolName": tool_name,
                    "summary": f"Running {tool_name} through the AgentHub tool policy proxy.",
                    "argsSummary": json.dumps(tool_args, sort_keys=True)[:1200],
                    "sensitivity": _tool_sensitivity(tool_name),
                }, copilot_token)
                tool_started = time.monotonic()
                await _emit_pi_event(orchestrator_url, session_id, slot_id, assignment_session_id, "pi.subagents.status", {
                    "runId": run_id,
                    "agent": agent_id,
                    "agentId": assignment_session_id,
                    "agentName": role,
                    "mode": "single",
                    "state": "running",
                    "activityState": "active_long_running",
                    "currentTool": tool_name,
                    "currentToolStartedAt": int(time.time() * 1000),
                    "task": role,
                    "summary": f"Running tool {tool_name}.",
                    "turnCount": round_num + 1,
                    "toolCount": tool_count,
                    "durationMs": int((time.monotonic() - started_at) * 1000),
                }, copilot_token)
                tool_ok, tool_result = await _proxy_tool_call(
                    orchestrator_url, session_id, slot_id,
                    assignment_session_id, tool_name, tool_args,
                    copilot_token,
                )
                if tool_ok and tool_name in required_tools and _required_tool_result_succeeded(tool_name, tool_result):
                    completed_required_tools.add(tool_name)
                await _emit_pi_event(orchestrator_url, session_id, slot_id, assignment_session_id, "pi.tool.end", {
                    "toolCallId": tool_call_id,
                    "turnId": turn_id,
                    "status": "ok" if tool_ok else "error",
                    "durationMs": int((time.monotonic() - tool_started) * 1000),
                    "display": {
                        "summary": _compact_one_line(tool_result, 500) if tool_ok else f"{tool_name} failed",
                        "outputPreview": _compact_one_line(tool_result, 1000),
                        "trust": {"level": "trusted" if tool_ok else "untrusted", "source": "tool", "summaryOnly": True},
                    },
                    "errorPreview": None if tool_ok else _compact_one_line(tool_result, 500),
                }, copilot_token)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": _compact_tool_result_for_model(tool_name, tool_result),
                })

            if required_tools and not _missing_required_tool(required_tools, completed_required_tools):
                logger.info("[AGENT:%s] Finished after required tool calls: %s", label, ",".join(required_tools))
                await _report_completion(
                    orchestrator_url, session_id, slot_id,
                    assignment_session_id, "success", "Completed required Fabric creation tools",
                    copilot_token,
                )
                await _emit_pi_terminal_events(
                    orchestrator_url, session_id, slot_id, assignment_session_id,
                    run_id, turn_id, role, agent_id, "completed",
                    "Completed required Fabric creation tools", tool_count, started_at,
                    copilot_token,
                )
                return 0

        # Hit max rounds
        missing_required = _missing_required_tool(required_tools, completed_required_tools)
        if missing_required:
            logger.error("[AGENT:%s] Reached max rounds without required tool: %s", label, missing_required)
            await _report_completion(
                orchestrator_url, session_id, slot_id,
                assignment_session_id, "error", f"Missing required tool call: {missing_required}",
                copilot_token,
            )
            await _emit_pi_terminal_events(
                orchestrator_url, session_id, slot_id, assignment_session_id,
                run_id, turn_id, role, agent_id, "failed",
                f"Missing required tool call: {missing_required}", tool_count, started_at,
                copilot_token,
            )
            return 1

        logger.info("[AGENT:%s] Reached max rounds (%d)", label, max_rounds)
        await _report_completion(
            orchestrator_url, session_id, slot_id,
            assignment_session_id, "partial", "Reached max rounds",
            copilot_token,
        )
        await _emit_pi_terminal_events(
            orchestrator_url, session_id, slot_id, assignment_session_id,
            run_id, turn_id, role, agent_id, "paused", "Reached max rounds",
            tool_count, started_at, copilot_token,
        )
        return 0


async def _emit_pi_event(
    orchestrator_url: str,
    session_id: str,
    slot_id: str,
    assignment_session_id: str,
    event_type: str,
    payload: dict,
    token: str,
) -> None:
    try:
        async with httpx.AsyncClient(timeout=8) as http:
            await http.post(
                f"{orchestrator_url}/api/internal/events/emit",
                json={
                    "session_id": session_id,
                    "slot_id": slot_id,
                    "assignment_session_id": assignment_session_id,
                    "type": event_type,
                    "payload": payload,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception as exc:
        logger.debug("[AGENT] Failed to emit Pi event %s: %s", event_type, exc)


async def _emit_pi_terminal_events(
    orchestrator_url: str,
    session_id: str,
    slot_id: str,
    assignment_session_id: str,
    run_id: str,
    turn_id: str,
    role: str,
    agent_id: str,
    status: str,
    summary: str,
    tool_count: int,
    started_at: float,
    token: str,
) -> None:
    turn_status = "completed" if status in {"completed", "complete", "success"} else "failed" if status == "failed" else "aborted"
    result_status = "completed" if turn_status == "completed" else "failed" if turn_status == "failed" else "paused"
    duration_ms = int((time.monotonic() - started_at) * 1000)
    await _emit_pi_event(orchestrator_url, session_id, slot_id, assignment_session_id, "pi.turn.end", {
        "turnId": turn_id,
        "status": turn_status,
        "reason": summary[:1000],
    }, token)
    await _emit_pi_event(orchestrator_url, session_id, slot_id, assignment_session_id, "pi.subagents.result", {
        "runId": run_id,
        "agent": agent_id,
        "agentId": assignment_session_id,
        "agentName": role,
        "mode": "single",
        "status": result_status,
        "summary": summary[:4000] or result_status,
        "usage": {"turns": 1, "toolCount": tool_count, "durationMs": duration_ms},
        "sessionFile": f"agenthub://sessions/{session_id}/subagents/{run_id}.jsonl",
        "artifactPaths": {
            "inputPath": f"agenthub://sessions/{session_id}/subagents/{run_id}_input.md",
            "outputPath": f"agenthub://sessions/{session_id}/subagents/{run_id}_output.md",
            "jsonlPath": f"agenthub://sessions/{session_id}/subagents/{run_id}.jsonl",
            "metadataPath": f"agenthub://sessions/{session_id}/subagents/{run_id}_meta.json",
        },
    }, token)
    await _emit_pi_event(orchestrator_url, session_id, slot_id, assignment_session_id, "pi.subagents.status", {
        "runId": run_id,
        "agent": agent_id,
        "agentId": assignment_session_id,
        "agentName": role,
        "mode": "single",
        "state": "complete" if result_status == "completed" else result_status,
        "activityState": "active_long_running" if result_status == "completed" else "needs_attention",
        "task": role,
        "summary": summary[:1000],
        "turnCount": 1,
        "toolCount": tool_count,
        "durationMs": duration_ms,
        "sessionFile": f"agenthub://sessions/{session_id}/subagents/{run_id}.jsonl",
    }, token)


def _compact_one_line(value: str, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _tool_sensitivity(tool_name: str) -> str:
    lowered = tool_name.lower()
    if any(token in lowered for token in ("delete", "remove", "revoke", "drop")):
        return "destructive"
    if any(token in lowered for token in ("create", "write", "update", "publish", "deploy", "apply", "grant")):
        return "write"
    if any(token in lowered for token in ("get", "read", "query", "definition", "download")):
        return "read-sensitive"
    return "read-safe"


async def _fetch_tool_schemas(
    orchestrator_url: str,
    allowed_tools: set[str],
    token: str,
    session_id: str,
    assignment_session_id: str,
) -> list[dict]:
    """Fetch OpenAI tool schemas from the orchestrator."""
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(
                f"{orchestrator_url}/api/internal/tools/schemas",
                params={
                    "session_id": session_id,
                    "assignment_session_id": assignment_session_id,
                },
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
) -> tuple[bool, str]:
    """Proxy a tool call through the orchestrator's tool runtime."""
    try:
        async with httpx.AsyncClient(timeout=AGENT_TOOL_CALL_TIMEOUT) as http:
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
                body = resp.json()
                return bool(body.get("ok")), body.get("output", "")
            return False, f"TOOL_ERROR: HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        return False, f"TOOL_ERROR: {exc}"


def _required_tools_for_goal(agent_id: str, goal: str) -> tuple[str, ...]:
    if agent_id == "generalist":
        return ()
    if agent_id == "fabric-verifier":
        return _required_verification_tools_for_goal(goal)
    return _required_creation_tools_for_goal(goal)


def _required_creation_tools_for_goal(goal: str) -> tuple[str, ...]:
    goal_lower = goal.lower()
    if (
        "fabric_create_workspace_inventory_solution" in goal_lower
        or (
            "fabric item" in goal_lower
            and "semantic" in goal_lower
            and "report" in goal_lower
            and re.search(r"\b(?:visuali[sz]ation|visuali[sz]e|dashboard|solution)\b", goal_lower)
        )
    ):
        return ("fabric_create_workspace_inventory_solution",)
    return ()


def _required_verification_tools_for_goal(goal: str) -> tuple[str, ...]:
    goal_lower = goal.lower()
    required: list[str] = []
    if "workspace inventory" in goal_lower or "inventory solution" in goal_lower:
        required.append("fabric_verify_workspace_inventory_solution")
    if (
        "browser_verify_visual_render" in goal_lower
        or "browser evidence" in goal_lower
        or "report render" in goal_lower
        or "weburl" in goal_lower
    ):
        required.append("browser_verify_visual_render")
    return tuple(dict.fromkeys(required))


def _missing_required_tool(required_tools: tuple[str, ...], completed_tools: set[str]) -> str | None:
    for required_tool in required_tools:
        if required_tool not in completed_tools:
            return required_tool
    return None


def _required_tool_result_succeeded(tool_name: str, tool_result: str) -> bool:
    if tool_name in {"browser_verify_visual_render", "fabric_verify_report_renderable"}:
        return bool(tool_result and not tool_result.startswith("TOOL_ERROR:"))
    if tool_name == "fabric_verify_workspace_inventory_solution":
        return bool(tool_result and not tool_result.startswith("TOOL_ERROR:"))
    if tool_name != "fabric_create_workspace_inventory_solution":
        return bool(tool_result and not tool_result.startswith("TOOL_ERROR:"))
    try:
        parsed = json.loads(_extract_json_object(tool_result))
    except Exception:
        return False
    errors = parsed.get("errors")
    has_errors = bool(errors) if isinstance(errors, list) else bool(errors)
    return str(parsed.get("status") or "").lower() == "created" and not has_errors


def _prioritize_tools_for_round(
    tools: list[dict],
    agent_id: str,
    goal: str,
    required_tools: tuple[str, ...],
) -> list[dict]:
    if not tools:
        return tools
    required_rank = {name: idx for idx, name in enumerate(required_tools)}
    verifier_priority = {
        "browser_verify_visual_render": 0,
        "fabric_verify_workspace_inventory_solution": 1,
        "fabric_verify_report_renderable": 2,
        "fabric_validate_workspace_capacity": 3,
        "sl_evaluate_dax": 4,
        "sl_run_dax_query": 5,
        "sl_get_refresh_history": 6,
        "sl_get_report_definition": 7,
        "sl_get_semantic_model_definition": 8,
        "sl_get_lakehouse_tables": 9,
        "fabric_diagnose_workspace_artifacts": 10,
    }
    grounding_priority = {
        "microsoft_docs_search": 0,
        "microsoft_docs_fetch": 1,
        "microsoft_code_sample_search": 2,
        "get_azure_bestpractices_get": 3,
        "get_azure_bestpractices_ai_app": 4,
    }
    goal_lower = goal.lower()

    def rank(tool: dict) -> tuple[int, int, str]:
        name = str(tool.get("function", {}).get("name") or "")
        if name in required_rank:
            return (0, required_rank[name], name)
        if name in grounding_priority:
            return (1, grounding_priority[name], name)
        if agent_id == "fabric-verifier" or "verify" in goal_lower:
            return (2, verifier_priority.get(name, 1000), name)
        return (3, 0, name)

    return sorted(tools, key=rank)


def _compact_tool_result_for_model(tool_name: str, tool_result: str) -> str:
    if len(tool_result) <= AGENT_TOOL_RESULT_MAX_CHARS:
        return tool_result
    try:
        parsed = json.loads(_extract_json_value(tool_result))
    except Exception:
        return (
            tool_result[:AGENT_TOOL_RESULT_MAX_CHARS].rstrip()
            + f"\n...[truncated {len(tool_result) - AGENT_TOOL_RESULT_MAX_CHARS} chars from {tool_name} output]"
        )
    compact = _redact_large_payloads(parsed)
    rendered = json.dumps(compact, ensure_ascii=False, indent=2)
    if len(rendered) <= AGENT_TOOL_RESULT_MAX_CHARS:
        return rendered
    return rendered[:AGENT_TOOL_RESULT_MAX_CHARS].rstrip() + "\n...[truncated compact tool output]"


def _redact_large_payloads(value):
    if isinstance(value, dict):
        compact = {}
        for key, child in value.items():
            if key.lower() in {"payload", "definition", "base64", "content"} and isinstance(child, str) and len(child) > 500:
                compact[key] = f"[omitted {len(child)} chars]"
            else:
                compact[key] = _redact_large_payloads(child)
        return compact
    if isinstance(value, list):
        items = [_redact_large_payloads(item) for item in value[:20]]
        if len(value) > 20:
            items.append({"omittedItems": len(value) - 20})
        return items
    if isinstance(value, str) and len(value) > 1200:
        return value[:1200].rstrip() + f"...[omitted {len(value) - 1200} chars]"
    return value


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("tool result did not contain a JSON object")
    return stripped[start:end + 1]


def _extract_json_value(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return stripped
    object_start = stripped.find("{")
    array_start = stripped.find("[")
    starts = [idx for idx in (object_start, array_start) if idx != -1]
    if not starts:
        raise ValueError("tool result did not contain JSON")
    start = min(starts)
    end_char = "}" if stripped[start] == "{" else "]"
    end = stripped.rfind(end_char)
    if end == -1 or end <= start:
        raise ValueError("tool result did not contain complete JSON")
    return stripped[start:end + 1]


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
    import gzip
    encoded = os.environ.get("SLOT_CONFIG")
    if not encoded:
        logger.error("SLOT_CONFIG environment variable is missing")
        sys.exit(1)
    try:
        raw = base64.b64decode(encoded)
        if raw.startswith(b"\x1f\x8b"):
            raw = gzip.decompress(raw)
        config = json.loads(raw)
    except Exception as exc:
        logger.error("Failed to parse SLOT_CONFIG: %s", exc)
        sys.exit(1)

    exit_code = asyncio.run(run_agent_loop(config))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
