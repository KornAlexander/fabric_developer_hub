"""MAFWorkflowBuilder — translate a Composition into a MAF Workflow.

Covers every architecture declared in
[architectures.py](../../../domain/catalogs/architectures.py):

* ``solo``         → ``SequentialBuilder`` with a single participant.
* ``sequential``   → ``SequentialBuilder`` in composition handoff order.
* ``parallel``     → ``ConcurrentBuilder`` fan-out / fan-in.
* ``reflection``   → ``WorkflowBuilder`` actor↔critic loop (+ optional tester).
* ``supervisor``   → ``WorkflowBuilder`` lead↔worker edges (supervisor graph).
* ``hierarchical`` → ``WorkflowBuilder`` lead↔worker edges (supervisor graph).
* ``mixed``        → freeform ``WorkflowBuilder`` over declared handoffs.
* ``router``       → freeform ``WorkflowBuilder`` with handoff fan-out.
* ``network``      → freeform ``WorkflowBuilder`` over declared handoffs.

Note: MAF's ``HandoffBuilder`` requires concrete ``Agent`` instances
and rejects ``BaseAgent`` subclasses; our ``ContainerAgent`` is a
``BaseAgent`` subclass, so supervisor topologies use the graph-edge
pattern in ``build_supervisor`` instead.

Dispatch rule: each architecture id maps to its named build_* method.
Unknown ids degrade to ``build_freeform``. Builders that detect a
malformed composition (e.g. a single-slot reflection) degrade to a
sensible fallback rather than raising — keeping the runtime contract
honest with the Step 2 plan the user already approved.

Checkpointing is opt-in via ``AGENTHUB_CHECKPOINTING_ENABLED`` — see
[checkpointing.py](./checkpointing.py).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from services.agenthub.drivers.maf.availability import ensure_agent_framework_version
from services.agenthub.drivers.maf.checkpointing import get_checkpoint_storage
from services.agenthub.drivers.maf.container_agent import make_container_agent

if TYPE_CHECKING:
    from domain.models.composition import Composition
    from services.agenthub.agent_registry import AgentTemplate
    from services.agenthub.drivers.slot_runner import SlotRunner

logger = logging.getLogger(__name__)


class MAFWorkflowBuilder:
    """Compose → MAF Workflow translator. One method per architecture.

    All methods return a MAF ``Workflow`` object. Callers invoke
    ``workflow.run(prompt, stream=True)`` and pump events through
    [event_adapter.pump_workflow_events](./event_adapter.py).
    """

    def __init__(self, slot_runner: SlotRunner) -> None:
        self._runner = slot_runner

    # -- public dispatch ---------------------------------------------

    def build(
        self,
        composition: Composition,
        template_lookup: Callable[[str], AgentTemplate | None],
    ) -> Any:
        """Dispatch by ``composition.architecture`` to the matching
        build method. Each architecture id maps to its **own** topology
        builder — no more flattening every multi-agent shape into a
        coordinated sequence.

        - ``solo`` / ``sequential``     → ``build_sequential``
        - ``parallel``                  → ``build_concurrent``
        - ``reflection``                → ``build_reflection``
        - ``supervisor`` / ``hierarchical`` → ``build_supervisor``
        - ``mixed`` / ``router`` / ``network`` → ``build_freeform``

        Anything else falls through to ``build_freeform`` so the
        workflow still runs but advertises the degradation in logs.
        """
        arch = (composition.architecture or "").lower()
        if arch in ("solo", "sequential"):
            return self.build_sequential(composition, template_lookup)
        if arch == "parallel":
            return self.build_concurrent(composition, template_lookup)
        if arch == "reflection":
            return self.build_reflection(composition, template_lookup)
        if arch in ("supervisor", "hierarchical"):
            # A supervisor with a single slot is just a solo run; route
            # to sequential so the graph builder doesn't reject it.
            if len(composition.slots) <= 1:
                return self.build_sequential(composition, template_lookup)
            return self.build_supervisor(composition, template_lookup)
        if arch in ("mixed", "router", "network"):
            return self.build_freeform(composition, template_lookup)
        logger.warning(
            "[MAF_BUILDER] Unknown architecture %r — falling back to freeform",
            composition.architecture,
        )
        return self.build_freeform(composition, template_lookup)

    # -- per-topology builders ---------------------------------------

    def build_sequential(
        self,
        composition: Composition,
        template_lookup: Callable[[str], AgentTemplate | None],
    ) -> Any:
        ensure_agent_framework_version()
        from agent_framework.orchestrations import SequentialBuilder  # type: ignore[import-not-found]

        ordered = _resolve_sequential_order(composition)
        if not ordered:
            raise ValueError("Sequential composition has no slots")

        participants = [self._agent_for(slot, template_lookup) for slot in ordered]
        kwargs: dict[str, Any] = {"participants": participants}
        storage = get_checkpoint_storage()
        if storage is not None:
            kwargs["checkpoint_storage"] = storage
        workflow = SequentialBuilder(**kwargs).build()
        logger.info(
            "[MAF_BUILDER] sequential workflow: %d participants",
            len(participants),
        )
        return workflow

    def build_concurrent(
        self,
        composition: Composition,
        template_lookup: Callable[[str], AgentTemplate | None],
    ) -> Any:
        ensure_agent_framework_version()
        from agent_framework.orchestrations import ConcurrentBuilder  # type: ignore[import-not-found]

        if not composition.slots:
            raise ValueError("Concurrent composition has no slots")
        participants = [
            self._agent_for(slot, template_lookup) for slot in composition.slots
        ]
        kwargs: dict[str, Any] = {"participants": participants}
        storage = get_checkpoint_storage()
        if storage is not None:
            kwargs["checkpoint_storage"] = storage
        workflow = ConcurrentBuilder(**kwargs).build()
        logger.info(
            "[MAF_BUILDER] concurrent workflow: %d participants",
            len(participants),
        )
        return workflow

    def build_handoff(
        self,
        composition: Composition,
        template_lookup: Callable[[str], AgentTemplate | None],
    ) -> Any:
        """Reserved for future use. The MAF ``HandoffBuilder`` requires
        concrete ``Agent`` instances (not ``BaseAgent`` subclasses), so
        the current ``ContainerAgent`` cannot participate. Supervisor
        and hierarchical topologies are served by ``build_supervisor``
        instead — a graph-edge pattern over ``WorkflowBuilder`` that
        accepts ``BaseAgent`` participants. We keep this method (and
        its existing test coverage) so a future migration to ``Agent``
        subclasses can land without churning the dispatcher again.
        """
        ensure_agent_framework_version()
        from agent_framework.orchestrations import HandoffBuilder  # type: ignore[import-not-found]

        if not composition.slots:
            raise ValueError("Handoff composition has no slots")

        agents_by_slot: dict[str, Any] = {
            slot.id: self._agent_for(slot, template_lookup)
            for slot in composition.slots
        }
        # Pick a lead even when the composer left ``entrypoint_slot_id``
        # blank or pointing at a slot that didn't survive validation —
        # the supervisor topology must always have one.
        lead_id = composition.entrypoint_slot_id or composition.slots[0].id
        if lead_id not in agents_by_slot:
            lead_id = composition.slots[0].id
        lead = agents_by_slot[lead_id]
        workers = [a for sid, a in agents_by_slot.items() if sid != lead_id]

        builder = HandoffBuilder(participants=list(agents_by_slot.values()))
        builder = builder.with_start_agent(lead)
        if workers:
            builder = builder.add_handoff(lead, workers)
        # Workers can hand back to the lead — closes the supervisor loop.
        for worker in workers:
            builder = builder.add_handoff(worker, [lead])

        storage = get_checkpoint_storage()
        if storage is not None:
            builder = builder.with_checkpointing(storage)

        workflow = builder.build()
        logger.info(
            "[MAF_BUILDER] handoff workflow: lead=%s workers=%d",
            lead_id, len(workers),
        )
        return workflow

    def build_supervisor(
        self,
        composition: Composition,
        template_lookup: Callable[[str], AgentTemplate | None],
    ) -> Any:
        """Lead → workers → lead loop using ``WorkflowBuilder`` edges.

        The MAF ``HandoffBuilder`` requires concrete ``Agent``
        instances and rejects ``BaseAgent`` subclasses, which is what
        ``ContainerAgent`` is. This builder produces the same behavior
        the strategy doc asks for — workers can return control to the
        lead — over the graph primitives that *do* accept
        ``BaseAgent``.

        Topology::

            lead ──┬──> w1 ──┐
                   ├──> w2 ──┤
                   └──> wN ──┘
                            │
                            └──> lead (loop)

        Iteration is bounded by ``composition.budget.max_turns`` so a
        non-converging supervisor cannot run forever.
        """
        ensure_agent_framework_version()
        from agent_framework import WorkflowBuilder  # type: ignore[import-not-found]

        if not composition.slots:
            raise ValueError("Supervisor composition has no slots")

        agents_by_slot: dict[str, Any] = {
            slot.id: self._agent_for(slot, template_lookup)
            for slot in composition.slots
        }
        lead_id = composition.entrypoint_slot_id or composition.slots[0].id
        if lead_id not in agents_by_slot:
            lead_id = composition.slots[0].id
        lead = agents_by_slot[lead_id]
        worker_ids = [sid for sid in agents_by_slot if sid != lead_id]

        kwargs: dict[str, Any] = {"start_executor": lead}
        storage = get_checkpoint_storage()
        if storage is not None:
            kwargs["checkpoint_storage"] = storage
        kwargs["max_iterations"] = max(4, composition.budget.max_turns)

        builder = WorkflowBuilder(**kwargs)
        for worker_id in worker_ids:
            worker = agents_by_slot[worker_id]
            builder = builder.add_edge(lead, worker)
            builder = builder.add_edge(worker, lead)

        workflow = builder.build()
        logger.info(
            "[MAF_BUILDER] supervisor workflow: lead=%s workers=%d",
            lead_id, len(worker_ids),
        )
        return workflow

    def build_coordinated_sequence(
        self,
        composition: Composition,
        template_lookup: Callable[[str], AgentTemplate | None],
    ) -> Any:
        ensure_agent_framework_version()
        from agent_framework.orchestrations import SequentialBuilder  # type: ignore[import-not-found]

        ordered = _lead_then_remaining_order(composition)
        if not ordered:
            raise ValueError("Coordinated composition has no slots")

        participants = [self._agent_for(slot, template_lookup) for slot in ordered]
        kwargs: dict[str, Any] = {"participants": participants}
        storage = get_checkpoint_storage()
        if storage is not None:
            kwargs["checkpoint_storage"] = storage
        workflow = SequentialBuilder(**kwargs).build()
        logger.info(
            "[MAF_BUILDER] coordinated sequence workflow: arch=%s slots=%d lead=%s",
            composition.architecture, len(participants), ordered[0].id,
        )
        return workflow

    def build_reflection(
        self,
        composition: Composition,
        template_lookup: Callable[[str], AgentTemplate | None],
    ) -> Any:
        ensure_agent_framework_version()
        from agent_framework import WorkflowBuilder  # type: ignore[import-not-found]

        if len(composition.slots) < 2:
            # Malformed reflection → degrade to sequential.
            return self.build_sequential(composition, template_lookup)

        actor_slot = composition.slots[0]
        critic_slot = composition.slots[1]
        tester_slot = composition.slots[2] if len(composition.slots) >= 3 else None

        actor = self._agent_for(actor_slot, template_lookup)
        critic = self._agent_for(critic_slot, template_lookup)

        kwargs: dict[str, Any] = {"start_executor": actor}
        storage = get_checkpoint_storage()
        if storage is not None:
            kwargs["checkpoint_storage"] = storage
        # Bound the reflection loop — otherwise a non-converging pair
        # runs forever. Budget-driven rather than configurable.
        kwargs["max_iterations"] = max(4, composition.budget.max_turns)

        builder = WorkflowBuilder(**kwargs)
        builder = builder.add_edge(actor, critic)
        builder = builder.add_edge(critic, actor)
        if tester_slot is not None:
            tester = self._agent_for(tester_slot, template_lookup)
            builder = builder.add_edge(actor, tester)

        workflow = builder.build()
        logger.info(
            "[MAF_BUILDER] reflection workflow: actor=%s critic=%s tester=%s",
            actor_slot.id, critic_slot.id, tester_slot.id if tester_slot else None,
        )
        return workflow

    def build_freeform(
        self,
        composition: Composition,
        template_lookup: Callable[[str], AgentTemplate | None],
    ) -> Any:
        ensure_agent_framework_version()
        from agent_framework import WorkflowBuilder  # type: ignore[import-not-found]

        if not composition.slots:
            raise ValueError("Freeform composition has no slots")

        start_slot_id = composition.entrypoint_slot_id or composition.slots[0].id
        agents_by_slot: dict[str, Any] = {
            slot.id: self._agent_for(slot, template_lookup)
            for slot in composition.slots
        }
        start_agent = agents_by_slot[start_slot_id]

        kwargs: dict[str, Any] = {"start_executor": start_agent}
        storage = get_checkpoint_storage()
        if storage is not None:
            kwargs["checkpoint_storage"] = storage
        # Bound freeform / mixed graphs — they typically contain cycles
        # (e.g. builder ↔ reviewer handoffs). Without an explicit cap MAF
        # uses its default of 100 supersteps and, once the per-session
        # budget is exhausted, the workflow keeps routing budget_exhausted
        # responses around the cycle until the runner raises
        # WorkflowConvergenceException. Allow each slot a few cycles plus
        # the user-approved turn budget — never more than the MAF default.
        max_iter = max(8, len(composition.slots) * 3, composition.budget.max_turns + 5)
        kwargs["max_iterations"] = min(max_iter, 100)
        builder = WorkflowBuilder(**kwargs)

        if composition.handoffs:
            for h in composition.handoffs:
                from_id = getattr(h, "from_", None) or getattr(h, "from", None)
                to_id = h.to
                if not from_id or from_id not in agents_by_slot or to_id not in agents_by_slot:
                    logger.debug(
                        "[MAF_BUILDER] Skipping handoff %s→%s (missing slot)",
                        from_id, to_id,
                    )
                    continue
                builder = builder.add_edge(
                    agents_by_slot[from_id], agents_by_slot[to_id],
                )
        else:
            # Self-loop keeps the workflow validator happy when there
            # are no edges — the start agent still runs once.
            builder = builder.add_edge(start_agent, start_agent)

        workflow = builder.build()
        logger.info(
            "[MAF_BUILDER] freeform workflow: arch=%s slots=%d handoffs=%d",
            composition.architecture,
            len(composition.slots),
            len(composition.handoffs or []),
        )
        return workflow

    # -- helpers -----------------------------------------------------

    def _agent_for(
        self,
        slot: Any,
        template_lookup: Callable[[str], AgentTemplate | None],
    ) -> Any:
        tpl = template_lookup(slot.agent_id)
        display = getattr(tpl, "display_name", None)
        # MAF uses ``agent.name`` as the executor id in a workflow, so
        # two slots sharing a template (common in supervisor topologies
        # where the lead and workers all start from the same agent id)
        # would collide. Scope the name with the slot id to keep ids
        # unique without losing the human-readable display name.
        agent_name = _safe_agent_name(slot.id, display or slot.agent_id)
        role = _safe_role(getattr(slot, "role", None), agent_name)
        return make_container_agent(
            slot_id=slot.id,
            role=role,
            agent_name=agent_name,
            slot_runner=self._runner,
        )


# ── helpers ──────────────────────────────────────────────────────────


def _resolve_sequential_order(composition: Composition) -> list[Any]:
    """Determine slot execution order. Prefer handoff-edge traversal
    from the single root slot; fall back to ``composition.slots``
    order when handoffs are absent or ambiguous."""
    slots = list(composition.slots)
    if not composition.handoffs:
        return slots

    successors: dict[str, str] = {}
    predecessors: set[str] = set()
    for h in composition.handoffs:
        from_id = getattr(h, "from_", None) or getattr(h, "from", None)
        to_id = h.to
        if from_id is None:
            continue
        successors[from_id] = to_id
        predecessors.add(to_id)

    slot_ids = [s.id for s in slots]
    roots = [sid for sid in slot_ids if sid not in predecessors]
    if len(roots) != 1:
        return slots

    by_id = {s.id: s for s in slots}
    ordered: list[Any] = []
    visited: set[str] = set()
    current: str | None = roots[0]
    while current and current not in visited:
        visited.add(current)
        if current in by_id:
            ordered.append(by_id[current])
        current = successors.get(current)

    for sid in slot_ids:
        if sid not in visited and sid in by_id:
            ordered.append(by_id[sid])
    return ordered


def _lead_then_remaining_order(composition: Composition) -> list[Any]:
    slots = list(composition.slots)
    if not slots:
        return []
    lead_id = composition.entrypoint_slot_id or slots[0].id
    lead = next((slot for slot in slots if slot.id == lead_id), None)
    if lead is None:
        return slots
    return [lead, *[slot for slot in slots if slot.id != lead_id]]


def _safe_agent_name(slot_id: str, display: str | None) -> str:
    """Build a unique MAF-friendly agent name for a slot.

    MAF uses the agent name as the workflow executor id, which must
    be unique across the workflow. Multiple slots often reuse the
    same agent template (e.g. two ``FabricAdmin`` workers), so we
    combine the display name with the slot id to guarantee
    uniqueness while keeping names human-readable in traces.
    """
    display = (display or "agent").strip()
    base = "".join(c if (c.isalnum() or c in ("_", "-")) else "_" for c in display)
    suffix = "".join(c if (c.isalnum() or c in ("_", "-")) else "_" for c in (slot_id or ""))
    name = f"{base}_{suffix}" if suffix else base
    return name or "agent"


def _safe_role(role: str | None, fallback: str) -> str:
    role = (role or "").strip()
    return role or fallback
