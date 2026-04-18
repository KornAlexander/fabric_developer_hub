"""Orchestrator engine — plans tasks, manages multi-agent execution, streams events."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC, datetime

import httpx

from domain.models.agent_models import (
    AgentAction,
    AgentAssignment,
    AgentDecision,
    AgentStatus,
    ExecutionPlan,
    Job,
    JobStatus,
    PhaseStatus,
    PlannedAgent,
    ReasoningPhase,
)
from services.agenthub.agent_registry import AGENT_TEMPLATES, get_template
from services.agenthub.session_store import log_audit, update_session

logger = logging.getLogger(__name__)

COPILOT_API_BASE = "https://api.githubcopilot.com"
TOOL_MODEL = "gpt-4o"
MAX_AGENT_ROUNDS = 15
AGENT_ROUND_TIMEOUT = 60  # seconds per LLM call

ORCHESTRATOR_SYSTEM_PROMPT = (
    "You are the AgentHub Orchestrator. Given a user's task, you decompose it into "
    "sub-tasks and assign them to available agents. You MUST respond with ONLY valid JSON — "
    "no markdown, no code fences, no explanation.\n\n"
    "Available agent templates:\n{agent_list}\n\n"
    "Respond with this exact JSON structure:\n"
    '{{"summary": "<human-readable plan summary>", '
    '"agents": [{{"agent_template_id": "<id>", "role": "<role in this job>", '
    '"goal": "<what this agent should accomplish>", "depends_on": ["<other agent_template_id or empty>"], '
    '"tool_groups": ["fabric_rest", "onelake"]}}], '
    '"communication_graph": {{"<agent_template_id>": ["<agent_template_id it talks to>"]}}, '
    '"estimated_duration": "<e.g. 5-10 minutes>"}}'
)


class _JobExecution:
    """Runtime state for a single running job."""

    def __init__(self, job: Job, copilot_token: str, mcp_tokens: dict | None):
        self.job = job
        self.copilot_token = copilot_token
        self.mcp_tokens = mcp_tokens
        self.event_queue: asyncio.Queue = asyncio.Queue()
        self.user_message_queues: dict[str, asyncio.Queue] = {}  # session_id -> Queue
        self.tasks: list[asyncio.Task] = []
        self.cancelled = False

    def emit(self, event_type: str, **kwargs):
        payload = {"type": event_type, **kwargs}
        self.event_queue.put_nowait(payload)

    async def events(self) -> AsyncGenerator[dict]:
        while True:
            try:
                ev = await asyncio.wait_for(self.event_queue.get(), timeout=30)
                yield ev
                if ev.get("type") == "job_complete" or ev.get("type") == "job_failed":
                    return
            except TimeoutError:
                yield {"type": "heartbeat"}


def _copilot_headers(copilot_token: str) -> dict:
    return {
        "Authorization": f"Bearer {copilot_token}",
        "Content-Type": "application/json",
        "Copilot-Integration-Id": "vscode-chat",
        "Editor-Version": "vscode/1.100.0",
        "Editor-Plugin-Version": "copilot-chat/0.25.0",
    }


class OrchestratorEngine:
    """Plans tasks, runs multi-agent executions, and streams job events."""

    def __init__(
        self,
        mcp_manager=None,
        copilot_token_fn: Callable[[str], Awaitable[str]] | None = None,
        acquire_mcp_tokens_fn: Callable[[str], Awaitable[dict | None]] | None = None,
    ):
        self.mcp_manager = mcp_manager
        self.copilot_token_fn = copilot_token_fn
        self.acquire_mcp_tokens_fn = acquire_mcp_tokens_fn
        self._active_jobs: dict[str, _JobExecution] = {}

    def configure(self, mcp_manager, copilot_token_fn, acquire_mcp_tokens_fn) -> None:
        """Inject shared dependencies at application startup."""
        self.mcp_manager = mcp_manager
        self.copilot_token_fn = copilot_token_fn
        self.acquire_mcp_tokens_fn = acquire_mcp_tokens_fn

    # ── Plan Generation ──────────────────────────────────────────────

    async def generate_plan(
        self,
        task_description: str,
        workspace_id: str,
        copilot_token: str,
        context: dict | None = None,
    ) -> ExecutionPlan:
        """Use the LLM to decompose a task into an ExecutionPlan."""
        agent_list = "\n".join(
            f"- {t.id}: {t.display_name} ({t.category.value}) — {t.description}  Tools: {t.available_tools}"
            for t in AGENT_TEMPLATES.values()
        )
        system = ORCHESTRATOR_SYSTEM_PROMPT.format(agent_list=agent_list)
        workspace_name = ""
        if context and context.get("workspace_name"):
            workspace_name = context["workspace_name"]
        user_content = f"Task: {task_description}\nWorkspace ID: {workspace_id}"
        if workspace_name:
            user_content += f"\nWorkspace Name: {workspace_name}"
            user_content += "\nIMPORTANT: Use the workspace name (not the ID) when describing the plan to the user."
        if context:
            user_content += f"\nAdditional context: {json.dumps(context)}"

        body = {
            "model": TOOL_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 2048,
            "temperature": 0.3,
            "stream": False,
        }

        headers = _copilot_headers(copilot_token)
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{COPILOT_API_BASE}/chat/completions", json=body, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Copilot API error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        logger.info("[ORCHESTRATOR] Raw plan response: %s", content[:500])

        # Parse JSON from response (strip possible markdown fences)
        content = content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

        try:
            plan_data = json.loads(content)
        except json.JSONDecodeError:
            logger.error("[ORCHESTRATOR] Failed to parse plan JSON: %s", content[:300])
            # Return a simple single-agent fallback plan
            plan_data = {
                "summary": f"I'll assign Xi (Data Engineer) to handle: {task_description}",
                "agents": [
                    {
                        "agent_template_id": "xi-data-engineer",
                        "role": "Data Engineer",
                        "goal": task_description,
                        "depends_on": [],
                        "tool_groups": ["fabric_rest", "onelake"],
                    }
                ],
                "communication_graph": {},
                "estimated_duration": "5-10 minutes",
            }

        job_id = str(uuid.uuid4())
        agents = [PlannedAgent(**a) for a in plan_data.get("agents", [])]
        logger.info("[ORCHESTRATOR] Plan summary: %s", plan_data.get("summary", "")[:200])
        logger.info("[ORCHESTRATOR] Agents in plan: %s",
                    ", ".join(f"{a.agent_template_id}({a.role})" for a in agents))
        logger.info("[ORCHESTRATOR] Communication graph: %s", plan_data.get("communication_graph", {}))

        return ExecutionPlan(
            job_id=job_id,
            agents=agents,
            communication_graph=plan_data.get("communication_graph", {}),
            estimated_duration=plan_data.get("estimated_duration"),
            summary=plan_data.get("summary", ""),
        )

    # ── Job Execution ────────────────────────────────────────────────

    async def start_job(self, job: Job, copilot_token: str, mcp_tokens: dict | None) -> str:
        """Begin executing an approved job. Returns the job ID."""
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC)

        # Create agent assignments from the plan
        if job.plan:
            for pa in job.plan.agents:
                session_id = str(uuid.uuid4())
                get_template(pa.agent_template_id)
                job.agents.append(
                    AgentAssignment(
                        agent_id=pa.agent_template_id,
                        session_id=session_id,
                        role=pa.role,
                        goal=pa.goal,
                        status=AgentStatus.QUEUED,
                    )
                )

        update_session(job)

        execution = _JobExecution(job, copilot_token, mcp_tokens)
        self._active_jobs[job.id] = execution

        # Start agent tasks
        for agent in job.agents:
            tpl = get_template(agent.agent_id)
            if not tpl:
                logger.warning("[ORCHESTRATOR] No template for %s", agent.agent_id)
                continue
            user_q: asyncio.Queue = asyncio.Queue()
            execution.user_message_queues[agent.session_id] = user_q
            task = asyncio.create_task(
                self._run_agent(execution, agent, tpl, user_q)
            )
            execution.tasks.append(task)

        # Monitor task: wait for all agents to finish
        asyncio.create_task(self._monitor_job(execution))

        return job.id

    async def _monitor_job(self, execution: _JobExecution):
        """Wait for all agent tasks to complete, then mark job done."""
        try:
            await asyncio.gather(*execution.tasks, return_exceptions=True)
        except Exception as e:
            logger.error("[ORCHESTRATOR] Monitor error: %s", e, exc_info=True)

        job = execution.job
        any_error = any(a.status == AgentStatus.ERROR for a in job.agents)
        job.status = JobStatus.FAILED if any_error else JobStatus.COMPLETED
        job.completed_at = datetime.now(UTC)
        update_session(job)

        duration = ""
        if job.started_at:
            secs = (job.completed_at - job.started_at).total_seconds()
            mins = int(secs // 60)
            secs_rem = int(secs % 60)
            duration = f"{mins}m {secs_rem}s" if mins else f"{secs_rem}s"

        execution.emit(
            "job_complete" if not any_error else "job_failed",
            jobId=job.id,
            status=job.status.value,
            totalDuration=duration,
        )
        self._active_jobs.pop(job.id, None)

    async def _run_agent(
        self,
        execution: _JobExecution,
        assignment: AgentAssignment,
        template,
        user_queue: asyncio.Queue,
    ):
        """Run a single agent's agentic loop."""
        job = execution.job
        agent_label = f"{template.name}({assignment.session_id[:8]})"
        logger.info("[AGENT:%s] Starting — goal: %s", agent_label, assignment.goal)

        assignment.status = AgentStatus.RUNNING
        update_session(job)
        execution.emit("agent_status", agentId=assignment.session_id,
                        agentName=template.display_name, status="running",
                        currentStep="Starting...", role=assignment.role,
                        goal=assignment.goal)

        # Build messages
        system_content = (
            f"{template.system_prompt}\n\n"
            f"WORKSPACE: {job.workspace_id}\n"
            f"YOUR GOAL FOR THIS JOB: {assignment.goal}\n"
            f"Emit structured phase markers in your responses:\n"
            f"PHASE_START: <phase_number> | <title>\n"
            f"PHASE_END: <phase_number>\n"
            f"ACTION: <type> | ENTITY: <name> | TYPE: <entity_type>\n"
            f"DECISION: <your reasoning summary>"
        )
        messages: list[dict] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": assignment.goal},
        ]

        # Filter tools for this agent
        all_tools = self.mcp_manager.get_openai_tools_schema() if self.mcp_manager else []
        allowed_names = set(template.available_tools)
        tools = [t for t in all_tools if t.get("function", {}).get("name") in allowed_names]

        phase_counter = 0

        for round_num in range(MAX_AGENT_ROUNDS):
            if execution.cancelled:
                assignment.status = AgentStatus.ERROR
                assignment.current_step = "Cancelled by user"
                execution.emit("agent_status", agentId=assignment.session_id,
                                agentName=template.display_name, status="error",
                                currentStep="Cancelled")
                return

            # Check for user messages
            while not user_queue.empty():
                try:
                    user_msg = user_queue.get_nowait()
                    messages.append({"role": "user", "content": user_msg})
                    execution.emit("agent_status", agentId=assignment.session_id,
                                    agentName=template.display_name, status="running",
                                    currentStep="Processing user message...")
                except asyncio.QueueEmpty:
                    break

            # Mark previous phase completed before starting a new one
            if assignment.phases and assignment.phases[-1].status == PhaseStatus.EXECUTING:
                assignment.phases[-1].status = PhaseStatus.COMPLETED
                assignment.phases[-1].completed_at = datetime.now(UTC)
                execution.emit("phase_complete", agentId=assignment.session_id,
                                agentName=template.display_name,
                                phaseNumber=assignment.phases[-1].phase_number)

            # Auto-create a phase for each round
            phase_counter += 1
            phase_title = f"Round {phase_counter}" if phase_counter > 1 else "Initializing"
            phase = ReasoningPhase(
                phase_number=phase_counter,
                title=phase_title,
                description="",
                status=PhaseStatus.EXECUTING,
            )
            assignment.phases.append(phase)
            update_session(job)
            execution.emit("phase_start", agentId=assignment.session_id,
                            agentName=template.display_name,
                            phase={"number": phase_counter, "title": phase_title,
                                   "timestamp": datetime.now(UTC).isoformat()})

            body = {
                "model": TOOL_MODEL,
                "messages": messages,
                "max_tokens": 4096,
                "temperature": 0.4,
                "stream": False,
            }
            if tools:
                body["tools"] = tools
                body["tool_choice"] = "auto"

            logger.info("[AGENT:%s] Round %d: %d messages, %d tools",
                        agent_label, round_num + 1, len(messages), len(tools))

            try:
                headers = _copilot_headers(execution.copilot_token)
                async with httpx.AsyncClient(timeout=AGENT_ROUND_TIMEOUT) as client:
                    resp = await client.post(
                        f"{COPILOT_API_BASE}/chat/completions", json=body, headers=headers
                    )
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                response = resp.json()
            except Exception as e:
                logger.error("[AGENT:%s] LLM call failed round %d: %s", agent_label, round_num + 1, e)
                assignment.status = AgentStatus.ERROR
                assignment.current_step = f"LLM error: {str(e)[:100]}"
                update_session(job)
                execution.emit("agent_error", agentId=assignment.session_id,
                                agentName=template.display_name,
                                error=str(e)[:200], phase=phase_counter)
                return

            choice = response.get("choices", [{}])[0]
            assistant_msg = choice.get("message", {})
            has_tool_calls = bool(assistant_msg.get("tool_calls"))
            content = assistant_msg.get("content") or ""

            logger.info("[AGENT:%s] Round %d LLM response: finish_reason=%s, has_content=%s (%d chars), has_tool_calls=%s",
                        agent_label, round_num + 1,
                        choice.get("finish_reason"),
                        bool(content), len(content),
                        has_tool_calls)
            if content:
                for i, line in enumerate(content.strip().split("\n")):
                    if line.strip():
                        logger.info("[AGENT:%s] Round %d content[%d]: %s",
                                    agent_label, round_num + 1, i, line.strip()[:200])
            if has_tool_calls:
                for tc in assistant_msg["tool_calls"]:
                    fn = tc.get("function", {})
                    logger.info("[AGENT:%s] Round %d tool_call: %s(%s)",
                                agent_label, round_num + 1,
                                fn.get("name"), fn.get("arguments", "")[:200])

            # Parse any structured markers from content
            _parse_agent_output(content, assignment, execution, template)

            # Capture LLM reasoning text into the current phase
            current_phase = assignment.phases[-1] if assignment.phases else None
            if current_phase and content.strip():
                lines = [l.strip() for l in content.strip().split("\n") if l.strip()]
                if current_phase.title.startswith("Round") or current_phase.title == "Initializing":
                    first_line = lines[0][:80] if lines else "Processing"
                    for prefix in ("PHASE_START:", "PHASE_END:", "ACTION:", "DECISION:", "#", "*", "-"):
                        first_line = first_line.lstrip(prefix).strip()
                    if first_line:
                        current_phase.title = first_line
                        logger.info("[AGENT:%s] Phase %d title: %s", agent_label, current_phase.phase_number, first_line)
                for line in lines:
                    stripped = line.strip()
                    if stripped and not stripped.startswith(("PHASE_START:", "PHASE_END:")):
                        current_phase.details.append(stripped)
                        execution.emit("phase_detail", agentId=assignment.session_id,
                                        agentName=template.display_name,
                                        phaseNumber=current_phase.phase_number,
                                        detail=stripped)

            if not has_tool_calls:
                logger.info("[AGENT:%s] Finished after %d rounds", agent_label, round_num + 1)
                if assignment.phases:
                    assignment.phases[-1].status = PhaseStatus.COMPLETED
                    assignment.phases[-1].completed_at = datetime.now(UTC)
                    execution.emit("phase_complete", agentId=assignment.session_id,
                                    agentName=template.display_name,
                                    phaseNumber=assignment.phases[-1].phase_number)
                    if content.strip():
                        clean = content.strip()
                        for prefix in ("PHASE_START:", "PHASE_END:", "ACTION:", "DECISION:"):
                            while prefix in clean:
                                idx = clean.index(prefix)
                                end = clean.find("\n", idx)
                                if end == -1:
                                    clean = clean[:idx].strip()
                                else:
                                    clean = (clean[:idx] + clean[end+1:]).strip()
                        if clean and len(clean) > 10:
                            assignment.phases[-1].decisions.append(
                                AgentDecision(summary=clean[:300])
                            )
                            logger.info("[AGENT:%s] Decision: %s", agent_label, clean[:200])
                            execution.emit("agent_decision", agentId=assignment.session_id,
                                            agentName=template.display_name,
                                            phaseNumber=phase_counter,
                                            decision=clean[:300])
                assignment.status = AgentStatus.COMPLETED
                assignment.current_step = "Completed"
                update_session(job)
                execution.emit("agent_status", agentId=assignment.session_id,
                                agentName=template.display_name, status="completed",
                                currentStep="Completed")
                return

            # Process tool calls
            messages.append(assistant_msg)
            for tc in assistant_msg["tool_calls"]:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "unknown")
                tool_args_str = fn.get("arguments", "{}")
                try:
                    tool_args = json.loads(tool_args_str)
                except json.JSONDecodeError:
                    tool_args = {}

                assignment.current_step = f"Calling {tool_name}..."
                update_session(job)
                execution.emit("agent_status", agentId=assignment.session_id,
                                agentName=template.display_name, status="running",
                                currentStep=f"Calling {tool_name}...")

                try:
                    result = await self.mcp_manager.call_tool(tool_name, tool_args, execution.mcp_tokens)
                    tool_result = str(result)
                    result_preview = tool_result[:150]
                    logger.info("[AGENT:%s] Tool %s result (%d chars): %s",
                                agent_label, tool_name, len(tool_result), result_preview)
                    log_audit(job.id, assignment.session_id, tool_name, tool_args, result_preview, job.user_id)

                    action = _detect_action_from_tool(tool_name, tool_args, tool_result)
                    if action:
                        assignment.actions.append(action)
                        logger.info("[AGENT:%s] Action: %s %s (%s)",
                                    agent_label, action.action_type, action.entity_name, action.entity_type)
                        execution.emit("action", agentId=assignment.session_id,
                                        agentName=template.display_name,
                                        action=action.model_dump(mode="json"))

                except Exception as e:
                    tool_result = f"Error executing {tool_name}: {e}"
                    logger.error("[AGENT:%s] Tool %s failed: %s", agent_label, tool_name, e)

                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": tool_result})

            if assignment.phases:
                assignment.phases[-1].status = PhaseStatus.COMPLETED
                assignment.phases[-1].completed_at = datetime.now(UTC)
                execution.emit("phase_complete", agentId=assignment.session_id,
                                agentName=template.display_name,
                                phaseNumber=assignment.phases[-1].phase_number)
            update_session(job)

        # Hit round limit
        assignment.status = AgentStatus.COMPLETED
        assignment.current_step = "Reached max rounds"
        update_session(job)
        execution.emit("agent_status", agentId=assignment.session_id,
                        agentName=template.display_name, status="completed",
                        currentStep="Reached max rounds")

    # ── Active-job bookkeeping ───────────────────────────────────────

    def get_job_execution(self, job_id: str) -> _JobExecution | None:
        return self._active_jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        exe = self._active_jobs.get(job_id)
        if not exe:
            return False
        exe.cancelled = True
        for t in exe.tasks:
            t.cancel()
        return True

    def inject_message(self, job_id: str, message: str, target_agent_session_id: str | None = None) -> bool:
        """Push a user message into a running agent's queue."""
        exe = self._active_jobs.get(job_id)
        if not exe:
            return False
        if target_agent_session_id and target_agent_session_id in exe.user_message_queues:
            exe.user_message_queues[target_agent_session_id].put_nowait(message)
        else:
            for q in exe.user_message_queues.values():
                q.put_nowait(message)
        return True


# ── Output parsing helpers (stateless) ───────────────────────────────


def _parse_agent_output(content: str, assignment: AgentAssignment,
                        execution: _JobExecution, template):
    """Extract structured phase/action/decision markers from LLM output."""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("PHASE_START:"):
            if assignment.phases and assignment.phases[-1].status == PhaseStatus.EXECUTING:
                assignment.phases[-1].status = PhaseStatus.COMPLETED
                assignment.phases[-1].completed_at = datetime.now(UTC)
                execution.emit("phase_complete", agentId=assignment.session_id,
                                agentName=template.display_name,
                                phaseNumber=assignment.phases[-1].phase_number)
            parts = line[len("PHASE_START:"):].strip().split("|", 1)
            num = len(assignment.phases) + 1
            title = parts[1].strip() if len(parts) > 1 else parts[0].strip()
            phase = ReasoningPhase(
                phase_number=num, title=title,
                description="", status=PhaseStatus.EXECUTING,
            )
            assignment.phases.append(phase)
            execution.emit("phase_start", agentId=assignment.session_id,
                            agentName=template.display_name,
                            phase={"number": num, "title": title,
                                   "timestamp": datetime.now(UTC).isoformat()})

        elif line.startswith("PHASE_END:"):
            if assignment.phases:
                assignment.phases[-1].status = PhaseStatus.COMPLETED
                assignment.phases[-1].completed_at = datetime.now(UTC)

        elif line.startswith("DECISION:"):
            decision_text = line[len("DECISION:"):].strip()
            if assignment.phases:
                assignment.phases[-1].decisions.append(
                    AgentDecision(summary=decision_text)
                )
            execution.emit("agent_decision", agentId=assignment.session_id,
                            agentName=template.display_name,
                            phaseNumber=len(assignment.phases),
                            decision=decision_text)

        elif line.startswith("ACTION:"):
            parts = [p.strip() for p in line[len("ACTION:"):].split("|")]
            if len(parts) >= 3:
                atype = parts[0]
                ename = parts[1].replace("ENTITY:", "").strip() if "ENTITY:" in parts[1] else parts[1]
                etype = parts[2].replace("TYPE:", "").strip() if "TYPE:" in parts[2] else parts[2]
                action = AgentAction(
                    id=str(uuid.uuid4()), action_type=atype,
                    entity_name=ename, entity_type=etype,
                )
                assignment.actions.append(action)
                execution.emit("action", agentId=assignment.session_id,
                                agentName=template.display_name,
                                action=action.model_dump(mode="json"))


def _detect_action_from_tool(tool_name: str, tool_args: dict, result: str) -> AgentAction | None:
    """Infer an action from a tool call."""
    result_lower = result.lower()
    is_error = any(marker in result_lower for marker in ("error", "failed", "unauthorized", "forbidden", "not found", "featurenotavailable", "badrequest"))

    if tool_name == "fabric_create_item":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Failed" if is_error else "Created",
            entity_name=tool_args.get("display_name", "unknown"),
            entity_type=tool_args.get("item_type", "Item"),
            details=result[:200],
        )
    if tool_name == "fabric_write_file":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Failed" if is_error else "Modified",
            entity_name=tool_args.get("file_path", "unknown"),
            entity_type="File",
            details=result[:200] if is_error else None,
        )
    if tool_name == "fabric_delete_file":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Failed" if is_error else "Deleted",
            entity_name=tool_args.get("file_path", "unknown"),
            entity_type="File",
        )
    if tool_name == "fabric_delete_item":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Failed" if is_error else "Deleted",
            entity_name=tool_args.get("item_id", "unknown"),
            entity_type="Item",
            details=result[:200],
        )
    if tool_name == "fabric_create_directory":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Created",
            entity_name=tool_args.get("directory_path", "unknown"),
            entity_type="Directory",
        )
    if tool_name == "fabric_list_workspaces":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Queried",
            entity_name="All workspaces",
            entity_type="Workspace",
            details=f"{result[:120]}..." if len(result) > 120 else result,
        )
    if tool_name == "fabric_list_items":
        ws = tool_args.get("workspace_id", "?")[:8]
        item_type = tool_args.get("item_type", "all items")
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Queried",
            entity_name=f"{item_type} in workspace {ws}...",
            entity_type="Items",
            details=f"{result[:120]}..." if len(result) > 120 else result,
        )
    if tool_name == "fabric_list_files":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Queried",
            entity_name=tool_args.get("path", "/"),
            entity_type="Files",
        )
    if tool_name == "fabric_read_file":
        return AgentAction(
            id=str(uuid.uuid4()),
            action_type="Read",
            entity_name=tool_args.get("file_path", "unknown"),
            entity_type="File",
        )
    return None


# ── Singleton accessor (backed by ServiceRegistry) ───────────────────


def get_orchestrator_engine() -> OrchestratorEngine:
    """Get the singleton OrchestratorEngine from ServiceRegistry."""
    from app.core.service_registry import get_service_registry
    registry = get_service_registry()
    if not registry.has(OrchestratorEngine):
        registry.register(OrchestratorEngine, OrchestratorEngine())
    return registry.get(OrchestratorEngine)
