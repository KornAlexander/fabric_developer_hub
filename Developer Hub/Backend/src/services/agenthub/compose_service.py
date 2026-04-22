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
from services.agenthub.compose import RECIPES, build_system_prompt
from services.agenthub.compose_models import COMPOSE_FALLBACK_MODEL

logger = logging.getLogger(__name__)


COPILOT_API_BASE = "https://api.githubcopilot.com"
# Default model when the caller doesn't specify one. The runtime picks
# the actual model from the user's available catalog via
# ``compose_models.pick_compose_model`` — this constant is only used as
# the last-resort fallback. Kept here for backwards compatibility with
# tests that monkeypatch ``COMPOSE_MODEL``.
COMPOSE_MODEL = COMPOSE_FALLBACK_MODEL
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


SYSTEM_PROMPT_HEADER_MARKER = "You are the AgentHub Composer"


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
        model: str | None = None,
    ) -> Composition:
        """Make the single compose call and return a Composition.

        ``preferred_architecture`` is the "Regenerate as …" override.
        ``model`` overrides the default composer model; validated against
        the user's catalog by the caller.
        """
        correlation_id = uuid.uuid4().hex[:12]
        session_id = session_id or str(uuid.uuid4())
        chosen_model = model or COMPOSE_MODEL

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
            correlation_id, chosen_model,
            len(system_prompt), len(user_msg), len(image_parts),
        )
        t0 = time.monotonic()
        raw = await self._call_llm(
            messages=messages, copilot_token=copilot_token,
            correlation_id=correlation_id, model=chosen_model,
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
                correlation_id=correlation_id, model=chosen_model,
            )
            return self._parse(
                raw2, session_id=session_id, task=task_description,
                require_approvals=require_approvals,
            )

    # ── Internals ────────────────────────────────────────────────

    def _system_prompt(self) -> str:
        """Render (and memoize) the composer's system prompt.

        The prompt is a pure function of the three catalogs
        (architectures, agents, recipes) — all static at runtime — so
        we cache the rendered string on the instance and reuse it
        across every compose call.
        """
        cached = getattr(self, "_system_prompt_cache", None)
        if cached is not None:
            return cached
        agents = (
            list_templates()
            if self._agents is AGENT_TEMPLATES
            else list(self._agents.values())
        )
        rendered = (
            build_system_prompt(ARCHITECTURES, agents, RECIPES)
            + "\n\n" + ATTACHMENT_SHIELD_PROMPT
        )
        self._system_prompt_cache = rendered
        return rendered

    async def _call_llm(
        self, *, messages: list[dict], copilot_token: str, correlation_id: str,
        model: str | None = None,
    ) -> str:
        body = {
            "model": model or COMPOSE_MODEL,
            "messages": messages,
            # Low temperature: compose should be near-deterministic
            # given a static catalog + fixed rules. The original 0.3
            # let different LLM families produce materially different
            # team shapes for the same prompt (two FDE slots vs one,
            # Architect included vs not). 0.1 keeps a tiny amount of
            # slack for genuinely ambiguous tasks without inviting
            # creative composition for unambiguous ones.
            "temperature": 0.1,
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

        # Post-parse normalisation: enforce uniqueness even when the
        # LLM ignores rule 4 in the global rules. For sequential and
        # supervisor architectures, emitting two slots with the same
        # ``agentId`` produces two disjoint agent loops with no shared
        # state — almost always wrong. We merge consecutive duplicates
        # into a single slot (first one wins, roles are concatenated)
        # and rewrite handoffs to match.
        if arch in ("sequential", "supervisor"):
            slots, handoffs, entrypoint = _collapse_duplicate_agent_slots(
                slots, handoffs, entrypoint,
            )

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


def _collapse_duplicate_agent_slots(
    slots: list[AgentSlot],
    handoffs: list[Handoff],
    entrypoint: str,
) -> tuple[list[AgentSlot], list[Handoff], str]:
    """Merge consecutive slots that share the same ``agent_id``.

    Sequential / supervisor plans should never instantiate the same
    agent twice — it produces two disjoint agent loops with no shared
    memory, which almost always means the composer mis-decomposed the
    task (e.g. two ``FabricDataEngineer`` slots for "ingestion" and
    "transformation", when one slot covers the whole build phase).

    Strategy: keep the first occurrence, concatenate its ``role`` with
    the duplicates' roles so context isn't lost, then rewrite every
    handoff that referenced the dropped slot ids to the kept one.
    Self-loop handoffs (from=to) created by the rewrite are removed.

    No-op when all agent_ids are already unique.
    """
    if not slots:
        return slots, handoffs, entrypoint
    seen: dict[str, AgentSlot] = {}
    id_remap: dict[str, str] = {}
    kept: list[AgentSlot] = []
    for s in slots:
        first = seen.get(s.agent_id)
        if first is None:
            seen[s.agent_id] = s
            kept.append(s)
            id_remap[s.id] = s.id
            continue
        # Fold this slot into the first one with the same agent.
        id_remap[s.id] = first.id
        dup_role = (s.role or "").strip()
        if dup_role and dup_role not in first.role:
            combined = f"{first.role}; {dup_role}" if first.role else dup_role
            first.role = combined[:160]
        # Merge skills (dedup by id) so we don't lose skill hints.
        known = {sk.id for sk in first.skills}
        for sk in s.skills:
            if sk.id not in known:
                first.skills.append(sk)
                known.add(sk.id)
    if len(kept) == len(slots):
        return slots, handoffs, entrypoint
    # Rewrite handoffs.
    new_handoffs: list[Handoff] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    for h in handoffs:
        frm = id_remap.get(h.from_, h.from_)
        to = id_remap.get(h.to, h.to)
        if frm == to:
            continue  # self-loop after merge — drop
        key = (frm, to, h.kind)
        if key in seen_pairs:
            continue  # dedup
        seen_pairs.add(key)
        new_handoffs.append(Handoff.model_validate({
            "from": frm, "to": to, "kind": h.kind, "condition": h.condition,
        }))
    new_entry = id_remap.get(entrypoint, entrypoint)
    if new_entry not in {s.id for s in kept}:
        new_entry = kept[0].id
    logger.info(
        "compose merged %d duplicate agent slot(s): %s",
        len(slots) - len(kept),
        [s for s, k in id_remap.items() if s != k],
    )
    return kept, new_handoffs, new_entry
