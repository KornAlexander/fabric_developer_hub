"""ComposeService — the single LLM analysis step that replaces plan
generation.

Takes (task prompt, attachments, architecture catalog, agent+skill
catalog, optional preferred architecture) and returns a
``Composition`` — the complete description of how to execute the task.
No intermediate "Plan" artifact is produced.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import httpx

from domain.catalogs.architectures import (
    ARCHITECTURES,
    ARCHITECTURES_BY_ID,
    catalog_prompt_block,
)
from domain.models.agent_models import AgentTemplate
from domain.models.composition import (
    AgentSlot,
    Budget,
    Composition,
    CompositionError,
    Handoff,
    SkillRef,
)
from services.agenthub.agent_registry import AGENT_TEMPLATES, list_templates
from services.agenthub.attachments import ATTACHMENT_SHIELD_PROMPT, process_attachments

logger = logging.getLogger(__name__)


COPILOT_API_BASE = "https://api.githubcopilot.com"
COMPOSE_MODEL = "gpt-4o"
COMPOSE_TIMEOUT_S = 60

# Module-level HTTP client reused across all compose calls. Opening a
# fresh client per request costs ~100–300ms in TCP+TLS handshake against
# api.githubcopilot.com; the shared client keeps the connection warm in
# the pool. Initialised lazily on first use.
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return the shared httpx client, creating it on first use."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=COMPOSE_TIMEOUT_S,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _http_client


# Common English synonyms the compose LLM occasionally emits instead of
# our canonical architecture ids. Mapping here lets the parser accept
# them silently rather than triggering a repair retry — which doubles
# compose latency and burns ~2k tokens per miss.
_ARCHITECTURE_ALIASES: dict[str, str] = {
    "orchestrator": "supervisor",
    "orchestration": "supervisor",
    "fanout": "parallel",
    "fan-out": "parallel",
    "map-reduce": "parallel",
    "mapreduce": "parallel",
    "pipeline": "sequential",
    "chain": "sequential",
    "single": "solo",
    "actor-critic": "reflection",
}


SYSTEM_PROMPT_TEMPLATE = """\
You are the AgentHub Composer. Given a user's task, the attachments they
provided, a catalog of available multi-agent **architectures**, and a
catalog of available **agents and their skills**, you produce a single
**Composition** describing exactly how the task should be executed.

You do NOT produce a "plan" — no step list, no prerequisites, no
workspace item inventory. Plans are emitted by agents at execution
time if they choose to. Your job is to pick the right *shape* and the
right *people* for the job.

# Architectures available
{architectures}

# Agents available
{agents}

# Picking rules
1. Prefer the simplest architecture that fits. Start at `solo` and
   escalate only when the task clearly needs coordination.
2. If the caller provides a `preferredArchitecture`, honour it unless
   it's unsuitable (e.g. `solo` requested for a clearly multi-domain
   task). Explain briefly in `rationale` if you override it.
3. Every slot's `agentId` MUST be one of the agent ids above. Every
   `skills[].id` MUST be one of that agent's declared skills.
4. Keep the slot count minimal. Don't add an agent unless it owns a
   distinct capability the task needs.
5. For `reflection`: exactly two slots — an actor and a critic.
6. For `sequential`: slots are ordered; handoffs go slot[i] → slot[i+1]
   with kind="report".
7. For `supervisor`: one lead slot at index 0 delegating to
   each worker with kind="delegate".
8. For `parallel`: one fan-out supervisor, N parallel workers, one
   reducer. Workers report to the reducer, not back to the supervisor.
9. For `router`: one triage slot, with kind="handoff" edges to each
   candidate specialist.
10. For `hierarchical`: 2-3 sub-leads under one lead, each
    owning their own workers. Use `parentId` to express the tree.
11. For `mixed`: label each cluster with a `subteam` string on its
    member slots.
12. Pick a sensible `budget`. Default (20/100/600) is fine for most
    tasks; scale up for genuinely big work, never above the schema
    caps.
13. `architecture` MUST be exactly one of the ids from the
    "Architectures available" section above — never a slot role
    like `lead`, `worker`, or `reducer`.

# Output
Respond with ONLY valid JSON matching this schema (camelCase keys):

{{
  "architecture": "<one of the ids above>",
  "rationale": "<why this shape fits this task, 1-2 sentences>",
  "headline": "<one-liner shown above the graph>",
  "subtitle": "<short subtitle — what this team is going to do>",
  "slots": [
    {{
      "id": "<slot id, e.g. 'lead' or 'worker-1'>",
      "agentId": "<agent id>",
      "role": "<what this slot does in this task>",
      "skills": [{{"id": "<skill id>", "name": "<skill name>"}}],
      "parentId": null,
      "subteam": null
    }}
  ],
  "handoffs": [
    {{"from": "<slot id>", "to": "<slot id>", "kind": "delegate|report|peer|handoff|critique", "condition": null}}
  ],
  "entrypointSlotId": "<slot id>",
  "budget": {{"maxTurns": 20, "maxToolCalls": 100, "maxWallclockS": 600, "requireApprovals": true}}
}}

No markdown fences. No prose outside the JSON object.
"""


class ComposeService:
    """Produces a Composition from a user task. One LLM call, one retry
    on schema failure, then raise ``CompositionError``."""

    def __init__(
        self,
        agents: dict[str, AgentTemplate] | None = None,
    ):
        self._agents = agents if agents is not None else AGENT_TEMPLATES

    async def compose(
        self,
        task_description: str,
        workspace_id: str,
        copilot_token: str,
        *,
        session_id: str | None = None,
        attachments: list[Any] | None = None,
        preferred_architecture: str | None = None,
        require_approvals: bool = True,
        branch_out: bool = False,
    ) -> Composition:
        """Make the single compose call and return a Composition.

        ``preferred_architecture`` is the "Regenerate as …" override.
        """
        correlation_id = uuid.uuid4().hex[:12]
        session_id = session_id or str(uuid.uuid4())

        att_dicts: list[dict] = []
        for a in attachments or []:
            att_dicts.append(a.model_dump() if hasattr(a, "model_dump") else a)
        text_block, image_parts, att_warnings = process_attachments(att_dicts)
        if att_warnings:
            logger.info(
                "[COMPOSE][%s] attachment warnings: %s",
                correlation_id, att_warnings,
            )

        # The system prompt depends only on the architecture + agent
        # catalogs (both static at runtime), so cache the rendered string
        # on the instance. Saves ~1–5ms and skips a few hundred string
        # concatenations per compose call.
        system_prompt = self._system_prompt()

        user_msg_parts: list[str] = [f"USER TASK:\n{task_description}"]
        if text_block:
            user_msg_parts.append(f"ATTACHMENTS:\n{text_block}")
        if preferred_architecture:
            user_msg_parts.append(
                f"PREFERRED ARCHITECTURE: {preferred_architecture}"
            )
        user_msg_parts.append(
            f"CONTEXT: workspace_id={workspace_id}, "
            f"require_approvals={require_approvals}, "
            f"branch_out={branch_out}"
        )
        user_msg = "\n\n".join(user_msg_parts)

        if image_parts:
            user_content: Any = [{"type": "text", "text": user_msg}, *image_parts]
        else:
            user_content = user_msg

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        logger.info(
            "[COMPOSE][%s] calling LLM (model=%s, sys_chars=%d, user_chars=%d, images=%d)",
            correlation_id, COMPOSE_MODEL,
            len(system_prompt), len(user_msg), len(image_parts),
        )
        t0 = time.monotonic()
        raw = await self._call_llm(
            messages=messages, copilot_token=copilot_token,
            correlation_id=correlation_id,
        )
        logger.info(
            "[COMPOSE][%s] LLM responded in %.2fs (%d chars)",
            correlation_id, time.monotonic() - t0, len(raw),
        )
        try:
            return self._parse(
                raw, session_id=session_id, task=task_description,
                require_approvals=require_approvals,
            )
        except CompositionError as e:
            logger.warning(
                "[COMPOSE][%s] parse failed — retrying once: %s",
                correlation_id, e.reason,
            )
            # Single repair retry — tell the model exactly what broke.
            messages.append({
                "role": "system",
                "content": (
                    "Your previous response failed schema validation: "
                    f"{e.reason}. Emit ONLY a valid JSON Composition."
                ),
            })
            raw2 = await self._call_llm(
                messages=messages, copilot_token=copilot_token,
                correlation_id=correlation_id,
            )
            return self._parse(
                raw2, session_id=session_id, task=task_description,
                require_approvals=require_approvals,
            )

    # ── Internals ────────────────────────────────────────────────

    def _system_prompt(self) -> str:
        """Render (and memoize) the static system prompt.

        Both ``catalog_prompt_block()`` and ``_agent_catalog_block()`` are
        deterministic for a given process, so we cache the rendered
        prompt on first use and reuse it across every compose call.
        """
        cached = getattr(self, "_system_prompt_cache", None)
        if cached is not None:
            return cached
        rendered = SYSTEM_PROMPT_TEMPLATE.format(
            architectures=catalog_prompt_block(),
            agents=self._agent_catalog_block(),
        ) + "\n\n" + ATTACHMENT_SHIELD_PROMPT
        self._system_prompt_cache = rendered
        return rendered

    def _agent_catalog_block(self) -> str:
        lines: list[str] = []
        for t in list_templates() if self._agents is AGENT_TEMPLATES else self._agents.values():
            skills_str = ", ".join(
                f"{s.id} ({s.name})" for s in t.skills
            ) if t.skills else "(no declared skills)"
            lines.append(
                f"- {t.id}: {t.description} | skills: {skills_str}"
            )
        return "\n".join(lines)

    async def _call_llm(
        self, *, messages: list[dict], copilot_token: str, correlation_id: str,
    ) -> str:
        body = {
            "model": COMPOSE_MODEL,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 2_000,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {copilot_token}",
            "Content-Type": "application/json",
            "Copilot-Integration-Id": "vscode-chat",
            "Editor-Version": "vscode/1.100.0",
        }
        resp = await _get_http_client().post(
            f"{COPILOT_API_BASE}/chat/completions",
            json=body, headers=headers,
        )
        if resp.status_code != 200:
            raise CompositionError(
                f"LLM call failed HTTP {resp.status_code}",
                {"body": resp.text[:400]},
            )
        try:
            return resp.json()["choices"][0]["message"]["content"] or ""
        except Exception as exc:  # noqa: BLE001
            raise CompositionError(f"malformed LLM response: {exc}") from exc

    def _parse(
        self,
        raw: str,
        *,
        session_id: str,
        task: str,
        require_approvals: bool,
    ) -> Composition:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CompositionError(f"not JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise CompositionError("expected JSON object")

        # Architecture validation. Accept a small set of common
        # English synonyms the LLM occasionally emits instead of our
        # canonical ids — cheaper than a retry round-trip.
        arch = str(payload.get("architecture") or "").strip().lower()
        arch = _ARCHITECTURE_ALIASES.get(arch, arch)
        if arch not in ARCHITECTURES_BY_ID:
            raise CompositionError(
                f"unknown architecture '{arch}' — must be one of "
                f"{sorted(ARCHITECTURES_BY_ID)}"
            )

        # Slot / agent / skill validation. We fix up minor issues the
        # LLM tends to make (missing skill.name, unknown skill id) by
        # dropping the bad field rather than failing the whole compose.
        raw_slots = payload.get("slots") or []
        if not isinstance(raw_slots, list) or not raw_slots:
            raise CompositionError("slots must be a non-empty list")
        slots: list[AgentSlot] = []
        for idx, s in enumerate(raw_slots):
            if not isinstance(s, dict):
                raise CompositionError(f"slot[{idx}] must be an object")
            agent_id = str(s.get("agentId") or s.get("agent_id") or "")
            if agent_id not in self._agents:
                raise CompositionError(
                    f"slot[{idx}] agentId '{agent_id}' is not a known agent"
                )
            tpl = self._agents[agent_id]
            known_skills = {sk.id: sk for sk in tpl.skills}
            raw_skills = s.get("skills") or []
            resolved_skills: list[SkillRef] = []
            for sk in raw_skills:
                if not isinstance(sk, dict):
                    continue
                sk_id = str(sk.get("id") or "")
                tpl_sk = known_skills.get(sk_id)
                if tpl_sk is None:
                    # Drop unknown skill refs rather than fail.
                    logger.info(
                        "compose dropping unknown skill '%s' on agent %s",
                        sk_id, agent_id,
                    )
                    continue
                resolved_skills.append(SkillRef(id=tpl_sk.id, name=tpl_sk.name))
            slots.append(AgentSlot(
                id=str(s.get("id") or f"slot-{idx + 1}"),
                agent_id=agent_id,
                role=str(s.get("role") or agent_id)[:160],
                skills=resolved_skills,
                parent_id=s.get("parentId") or s.get("parent_id"),
                subteam=s.get("subteam"),
            ))

        # Handoffs (optional — sequential/supervisor may emit none and
        # the driver fills them in from slot order).
        raw_handoffs = payload.get("handoffs") or []
        handoffs: list[Handoff] = []
        slot_ids = {s.id for s in slots}
        for idx, h in enumerate(raw_handoffs):
            if not isinstance(h, dict):
                continue
            frm = str(h.get("from") or "")
            to = str(h.get("to") or "")
            if frm not in slot_ids or to not in slot_ids:
                logger.info(
                    "compose dropping handoff with unknown slot ids: %s → %s",
                    frm, to,
                )
                continue
            handoffs.append(Handoff.model_validate({
                "from": frm, "to": to,
                "kind": h.get("kind") or "delegate",
                "condition": h.get("condition"),
            }))

        entrypoint = str(payload.get("entrypointSlotId") or payload.get("entrypoint_slot_id") or "")
        if entrypoint not in slot_ids:
            entrypoint = slots[0].id

        budget_raw = payload.get("budget") or {}
        if not isinstance(budget_raw, dict):
            budget_raw = {}
        budget = Budget(
            max_turns=int(budget_raw.get("maxTurns") or budget_raw.get("max_turns") or 20),
            max_tool_calls=int(budget_raw.get("maxToolCalls") or budget_raw.get("max_tool_calls") or 100),
            max_wallclock_s=int(budget_raw.get("maxWallclockS") or budget_raw.get("max_wallclock_s") or 600),
            require_approvals=bool(
                budget_raw.get("requireApprovals")
                if budget_raw.get("requireApprovals") is not None
                else budget_raw.get("require_approvals", require_approvals)
            ),
        )

        headline = str(payload.get("headline") or "").strip()
        subtitle = str(payload.get("subtitle") or "").strip()
        if not headline:
            entry = ARCHITECTURES_BY_ID[arch]
            headline = entry.headline
        if not subtitle:
            subtitle = ARCHITECTURES_BY_ID[arch].description[:200]

        rationale = str(payload.get("rationale") or "Composition derived from the task prompt.").strip()

        return Composition(
            session_id=session_id,
            task=task[:16_000],
            architecture=arch,  # type: ignore[arg-type]
            rationale=rationale[:1_200],
            headline=headline[:200],
            subtitle=subtitle[:400],
            slots=slots,
            handoffs=handoffs,
            entrypoint_slot_id=entrypoint,
            budget=budget,
        )


_service_singleton: ComposeService | None = None


def get_compose_service() -> ComposeService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = ComposeService()
    return _service_singleton
