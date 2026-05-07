"""Unit tests for the MAF adapter subpackage.

These tests mock the ``agent_framework`` package entirely so they
run without the optional dependency installed. They validate:

* ``maf_available()`` reflects presence of the dep
* ``ContainerAgent`` correctly delegates to the SlotRunner and
  wraps results as MAF messages
* ``MAFWorkflowBuilder.build_sequential`` preserves handoff order
* Driver registry picks the legacy driver when the flag is off or
  MAF is unavailable, and the MAF driver when both line up
* ``MAFSequentialDriver`` short-circuits gracefully when
  ``agent_framework`` isn't importable at runtime
* Event adapter suppresses MAF internal executors and maps
  executor invoke/complete events onto ``slot_progress``
"""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import MagicMock

import pytest

from services.agenthub.drivers.maf.availability import maf_available
from services.agenthub.drivers.maf.event_adapter import (
    _is_internal_executor,
    pump_workflow_events,
)

from tests.unit.services.drivers.conftest import (
    make_composition,
    make_execution,
    make_runner,
)


# ── Helpers ──────────────────────────────────────────────────────────


class _FakeMessage:
    """Mimics MAF's ``Message`` shape enough for our adapter."""

    def __init__(self, role="user", contents=None, *, text=None, author_name=None):
        self.role = role
        self.contents = list(contents) if contents else ([text] if text else [])
        # ``text`` mirrors MAF's property: join text attributes of content
        # objects, falling back to the raw content when it is a string.
        parts: list[str] = []
        for c in self.contents:
            if isinstance(c, str):
                parts.append(c)
            else:
                t = getattr(c, "text", None)
                if t:
                    parts.append(t)
        self.text = text if text is not None else "".join(parts)
        self.author_name = author_name


class _FakeContent:
    """Minimal ``Content`` stand-in with a ``type='text'`` discriminator."""

    def __init__(self, text: str):
        self.text = text
        self.type = "text"

    @classmethod
    def from_text(cls, text: str) -> "_FakeContent":
        return cls(text)


class _FakeAgentResponse:
    def __init__(self, *, messages, response_id=None):
        self.messages = messages
        self.response_id = response_id


class _FakeAgentResponseUpdate:
    """``AgentResponseUpdate`` stand-in — *must* carry ``author_name``
    because MAF's ``AgentResponse.from_updates`` reads it."""

    def __init__(self, *, contents, role=None, author_name=None):
        self.contents = list(contents)
        self.role = role
        self.author_name = author_name
        # ``.text`` mirrors MAF's property.
        self.text = "".join(
            getattr(c, "text", "") for c in self.contents
            if getattr(c, "type", None) == "text"
        )


class _FakeBaseAgent:
    """Minimal stand-in for MAF's ``BaseAgent``."""

    def __init__(self, *, id=None, name=None):
        self.id = id
        self.name = name


def _install_fake_maf(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Install a fake ``agent_framework`` module into ``sys.modules``.

    Returns the fake module so tests can assert on interactions.
    Cleans up on monkeypatch teardown.
    """
    import importlib.machinery as _m
    fake = types.ModuleType("agent_framework")
    fake.__spec__ = _m.ModuleSpec("agent_framework", loader=None)
    fake.BaseAgent = _FakeBaseAgent
    fake.Message = _FakeMessage
    fake.Content = _FakeContent
    fake.AgentResponse = _FakeAgentResponse
    fake.AgentResponseUpdate = _FakeAgentResponseUpdate

    orchestrations = types.ModuleType("agent_framework.orchestrations")
    orchestrations.__spec__ = _m.ModuleSpec("agent_framework.orchestrations", loader=None)

    captured: dict = {}

    class _FakeWorkflow:
        def __init__(self, participants):
            self.participants = participants

        def run(self, initial_input, *, stream=True):
            async def _gen():
                # Yield a synthetic executor lifecycle for each participant.
                for agent in self.participants:
                    ev = MagicMock()
                    ev.type = "executor_invoke"
                    ev.executor_id = agent.id
                    yield ev
                    await agent._run_once(initial_input)
                    done = MagicMock()
                    done.type = "executor_completed"
                    done.executor_id = agent.id
                    yield done
            return _gen()

    class _FakeSequentialBuilder:
        def __init__(self, *, participants, checkpoint_storage=None):
            captured["participants"] = list(participants)
            captured["sequential_checkpoint"] = checkpoint_storage
            self._participants = list(participants)

        def build(self):
            return _FakeWorkflow(self._participants)

    class _FakeConcurrentBuilder:
        def __init__(self, *, participants, checkpoint_storage=None):
            captured["concurrent_participants"] = list(participants)
            captured["concurrent_checkpoint"] = checkpoint_storage
            self._participants = list(participants)

        def build(self):
            return _FakeWorkflow(self._participants)

    class _FakeHandoffBuilder:
        def __init__(self, *, participants):
            captured["handoff_participants"] = list(participants)
            self._participants = list(participants)
            self._start = None
            self._handoffs: list[tuple] = []
            self._checkpoint = None

        def with_start_agent(self, agent):
            self._start = agent
            captured["handoff_start"] = agent
            return self

        def add_handoff(self, source, targets):
            self._handoffs.append((source, list(targets)))
            captured["handoff_edges"] = list(self._handoffs)
            return self

        def with_checkpointing(self, storage):
            self._checkpoint = storage
            captured["handoff_checkpoint"] = storage
            return self

        def build(self):
            return _FakeWorkflow(self._participants)

    class _FakeWorkflowBuilder:
        def __init__(self, *, start_executor, checkpoint_storage=None, max_iterations=None):
            captured["wb_start"] = start_executor
            captured["wb_checkpoint"] = checkpoint_storage
            captured["wb_max_iterations"] = max_iterations
            self._participants = [start_executor]
            self._edges: list[tuple] = []

        def add_edge(self, source, target, condition=None):
            self._edges.append((source, target))
            captured["wb_edges"] = list(self._edges)
            if target not in self._participants:
                self._participants.append(target)
            return self

        def build(self):
            return _FakeWorkflow(self._participants)

    orchestrations.SequentialBuilder = _FakeSequentialBuilder
    orchestrations.ConcurrentBuilder = _FakeConcurrentBuilder
    orchestrations.HandoffBuilder = _FakeHandoffBuilder
    fake.WorkflowBuilder = _FakeWorkflowBuilder

    monkeypatch.setitem(sys.modules, "agent_framework", fake)
    monkeypatch.setitem(sys.modules, "agent_framework.orchestrations", orchestrations)
    # Also reset the lazy type cache in container_agent so the fake
    # types are picked up. Ensure we clear it again on teardown so
    # subsequent tests using the real agent_framework (via a clean
    # sys.modules) rebuild a cache with real BaseAgent — otherwise the
    # cached _FakeBaseAgent subclass leaks across tests.
    from services.agenthub.drivers.maf import container_agent as _ca
    _ca._MAF_TYPES.clear()
    monkeypatch.setattr(_ca, "_MAF_TYPES", _ca._MAF_TYPES)

    def _clear_cache():
        _ca._MAF_TYPES.clear()

    # pytest monkeypatch supports .addfinalizer in recent versions via
    # _finalizers; use a request-free approach: register via atexit-
    # style — we leverage monkeypatch.undo() semantics by patching an
    # attribute that is restored on teardown *and* triggers clearing.
    # Simpler: rely on an explicit fixture in _restore_maf_cache below.
    fake._captured = captured
    fake._clear_cache = _clear_cache
    return fake


@pytest.fixture(autouse=True)
def _restore_maf_cache():
    """Ensure the container_agent ``_MAF_TYPES`` cache is cleared after
    each test — otherwise fake BaseAgent classes installed via
    ``_install_fake_maf`` would leak into tests that expect the real
    ``agent_framework``."""
    yield
    from services.agenthub.drivers.maf import container_agent as _ca
    _ca._MAF_TYPES.clear()


# ── maf_available ────────────────────────────────────────────────────


def test_maf_available_true_when_module_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_maf(monkeypatch)
    assert maf_available() is True


def test_maf_available_false_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "agent_framework", raising=False)

    import importlib.util as _util
    orig = _util.find_spec

    def _no_maf(name, *a, **kw):
        if name == "agent_framework":
            return None
        return orig(name, *a, **kw)

    monkeypatch.setattr(_util, "find_spec", _no_maf)
    assert maf_available() is False


# ── ContainerAgent ───────────────────────────────────────────────────


def test_container_agent_delegates_to_slot_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.container_agent import make_container_agent

    composition = make_composition(
        architecture="sequential",
        slots=[{"id": "s1", "agent_id": "fabric-admin", "role": "Admin"}],
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    agent = make_container_agent(
        slot_id="s1",
        role="Admin",
        agent_name="fabric_admin",
        slot_runner=runner,
    )

    response = asyncio.run(agent._run_once("do something"))

    # Runner was called with the correct slot id and upstream context.
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["slot_id"] == "s1"
    assert call["step_label"] == "Admin"
    assert call["upstream_handoffs"] is not None
    assert call["upstream_handoffs"][0].to_slot_id == "s1"
    assert "do something" in call["upstream_handoffs"][0].summary

    # Response is a MAF-compatible shape with one assistant message.
    assert isinstance(response, _FakeAgentResponse)
    assert len(response.messages) == 1
    msg = response.messages[0]
    assert msg.role == "assistant"
    assert msg.author_name == "fabric_admin"


def test_container_agent_handles_empty_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.container_agent import make_container_agent

    composition = make_composition(
        architecture="sequential",
        slots=[{"id": "s1", "agent_id": "fabric-admin", "role": "Admin"}],
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    agent = make_container_agent(
        slot_id="s1", role="Admin", agent_name="fabric_admin", slot_runner=runner,
    )

    asyncio.run(agent._run_once(None))
    assert runner.calls[0]["upstream_handoffs"] is None


# ── MAFWorkflowBuilder ───────────────────────────────────────────────


def test_workflow_builder_sequential_preserves_handoff_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.workflow_builder import MAFWorkflowBuilder

    composition = make_composition(
        architecture="sequential",
        slots=[
            {"id": "s3", "agent_id": "fabric-admin", "role": "Last"},
            {"id": "s1", "agent_id": "fabric-admin", "role": "First"},
            {"id": "s2", "agent_id": "fabric-admin", "role": "Middle"},
        ],
        handoffs=[
            {"from": "s1", "to": "s2", "kind": "handoff"},
            {"from": "s2", "to": "s3", "kind": "handoff"},
        ],
        entrypoint="s1",
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    builder = MAFWorkflowBuilder(slot_runner=runner)

    def _lookup(_agent_id):
        return MagicMock(display_name="FabricAdmin")

    workflow = builder.build_sequential(composition, _lookup)
    participants = fake._captured["participants"]
    assert [p.id for p in participants] == ["s1", "s2", "s3"]
    assert workflow is not None


def test_workflow_builder_rejects_empty_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.workflow_builder import MAFWorkflowBuilder

    composition = make_composition(
        architecture="sequential",
        slots=[{"id": "s1", "agent_id": "fabric-admin", "role": "First"}],
    )
    # Drop the slot to force the empty path without bypassing pydantic
    # validation at composition-construction time.
    composition.slots.clear()
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    builder = MAFWorkflowBuilder(slot_runner=runner)
    with pytest.raises(ValueError):
        builder.build_sequential(composition, lambda _a: None)


@pytest.mark.parametrize("architecture", ["mixed", "network"])
def test_workflow_builder_routes_freeform_topologies(
    monkeypatch: pytest.MonkeyPatch,
    architecture: str,
) -> None:
    """``mixed``/``router``/``network`` are graph topologies and must
    use ``WorkflowBuilder`` (freeform), not ``SequentialBuilder``."""
    fake = _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.workflow_builder import MAFWorkflowBuilder

    composition = make_composition(
        architecture=architecture,
        slots=[
            {"id": "lead", "agent_id": "fabric-admin", "role": "Lead"},
            {"id": "worker", "agent_id": "fabric-admin", "role": "Worker"},
        ],
        handoffs=[{"from": "lead", "to": "worker", "kind": "handoff"}],
        entrypoint="lead",
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)
    builder = MAFWorkflowBuilder(slot_runner=runner)

    workflow = builder.build(composition, lambda _a: MagicMock(display_name="FabricAdmin"))

    assert workflow is not None
    # Freeform path uses WorkflowBuilder, not SequentialBuilder.
    assert "wb_start" in fake._captured
    assert "participants" not in fake._captured


# ── Event adapter ────────────────────────────────────────────────────


def test_is_internal_executor() -> None:
    assert _is_internal_executor("input-conversation") is True
    assert _is_internal_executor("end") is True
    assert _is_internal_executor("to-conversation:writer") is True
    assert _is_internal_executor("s1") is False
    assert _is_internal_executor(None) is False


def test_pump_workflow_events_maps_executor_lifecycle() -> None:
    composition = make_composition(
        architecture="sequential",
        slots=[{"id": "s1", "agent_id": "fabric-admin", "role": "A"}],
    )
    exe = make_execution(composition)

    async def _stream():
        invoke = MagicMock(type="executor_invoke", executor_id="s1")
        completed = MagicMock(type="executor_completed", executor_id="s1")
        internal = MagicMock(type="executor_invoke", executor_id="input-conversation")
        for ev in (invoke, internal, completed):
            yield ev

    asyncio.run(pump_workflow_events(_stream(), exe))

    emitted_types = [e["type"] for e in exe._test_events]
    # Two slot_progress entries for the real executor, zero for the internal.
    assert emitted_types.count("slot_progress") == 2
    slot_events = [e for e in exe._test_events if e["type"] == "slot_progress"]
    assert [e["status"] for e in slot_events] == ["running", "completed"]
    assert all(e["slotId"] == "s1" for e in slot_events)


# ── Driver registration ──────────────────────────────────────────────


def test_registry_uses_maf_for_every_architecture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAF is the sole orchestration backend — every architecture id
    resolves to ``MAFUniversalDriver`` in the registry."""
    _install_fake_maf(monkeypatch)

    import importlib
    import services.agenthub.drivers as drivers_pkg
    importlib.reload(drivers_pkg)

    from services.agenthub.drivers.maf.universal_driver import MAFUniversalDriver

    for arch in (
        "solo", "sequential", "parallel", "supervisor", "hierarchical",
        "reflection", "mixed", "router", "network",
    ):
        driver = drivers_pkg.DriverRegistry.get(arch)
        assert isinstance(driver, MAFUniversalDriver), (
            f"Architecture {arch!r} did not resolve to MAFUniversalDriver"
        )


# ── MAFSequentialDriver ──────────────────────────────────────────────


def test_maf_sequential_driver_runs_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.sequential_driver import MAFSequentialDriver
    from services.agenthub.drivers.budget import BudgetTracker
    from domain.models.composition import Budget

    composition = make_composition(
        architecture="sequential",
        slots=[
            {"id": "s1", "agent_id": "fabric-admin", "role": "A"},
            {"id": "s2", "agent_id": "fabric-admin", "role": "B"},
        ],
        handoffs=[{"from": "s1", "to": "s2", "kind": "handoff"}],
        entrypoint="s1",
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    # Stub get_template globally — the driver looks it up by agent_id.
    import services.agenthub.agent_registry as _ar
    monkeypatch.setattr(
        _ar, "get_template",
        lambda _aid: MagicMock(display_name="FabricAdmin"),
    )

    driver = MAFSequentialDriver()
    asyncio.run(driver.run(
        composition=composition,
        execution=exe,
        slot_runner=runner,
        budget=BudgetTracker(budget=Budget()),
    ))

    # Both slots were invoked by the fake workflow.
    slot_ids_run = [c["slot_id"] for c in runner.calls]
    assert slot_ids_run == ["s1", "s2"]

    # Event stream observed both slots transitioning through running
    # and completed states.
    statuses = [e for e in exe._test_events if e["type"] == "slot_progress"]
    assert any(e["slotId"] == "s1" and e["status"] == "running" for e in statuses)
    assert any(e["slotId"] == "s2" and e["status"] == "completed" for e in statuses)


def test_maf_sequential_driver_short_circuits_on_budget_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.sequential_driver import MAFSequentialDriver
    from services.agenthub.drivers.budget import BudgetTracker
    from domain.models.composition import Budget

    composition = make_composition(
        architecture="sequential",
        slots=[{"id": "s1", "agent_id": "fabric-admin", "role": "A"}],
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    # Exhaust budget before the driver runs.
    budget = BudgetTracker(budget=Budget(max_turns=1))
    budget.turns_used = 5

    driver = MAFSequentialDriver()
    asyncio.run(driver.run(
        composition=composition,
        execution=exe,
        slot_runner=runner,
        budget=budget,
    ))

    assert runner.calls == []
    assert any(e["type"] == "budget_exhausted" for e in exe._test_events)


# ── Dispatcher & new topology builders ───────────────────────────────


def test_workflow_builder_build_concurrent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.workflow_builder import MAFWorkflowBuilder

    composition = make_composition(
        architecture="mixed",
        slots=[
            {"id": "s1", "agent_id": "fabric-admin", "role": "A"},
            {"id": "s2", "agent_id": "fabric-admin", "role": "B"},
        ],
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    builder = MAFWorkflowBuilder(slot_runner=runner)
    workflow = builder.build_concurrent(
        composition, lambda _a: MagicMock(display_name="X"),
    )
    assert workflow is not None
    assert len(fake._captured["concurrent_participants"]) == 2


def test_workflow_builder_supervisor_runs_lead_then_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``supervisor`` must use the supervisor graph (lead↔workers
    edges) so workers can hand back to the lead — not flatten into a
    coordinated sequence and not call ``HandoffBuilder`` (which only
    accepts concrete ``Agent`` instances)."""
    fake = _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.workflow_builder import MAFWorkflowBuilder

    composition = make_composition(
        architecture="supervisor",
        slots=[
            {"id": "lead", "agent_id": "fabric-admin", "role": "Lead"},
            {"id": "w1", "agent_id": "fabric-admin", "role": "W1"},
            {"id": "w2", "agent_id": "fabric-admin", "role": "W2"},
        ],
        entrypoint="lead",
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    builder = MAFWorkflowBuilder(slot_runner=runner)
    builder.build(composition, lambda _a: MagicMock(display_name="X"))

    # Supervisor must NOT use HandoffBuilder (real MAF rejects
    # ContainerAgent there) and must NOT use SequentialBuilder.
    assert "handoff_start" not in fake._captured
    assert "participants" not in fake._captured
    assert fake._captured["wb_start"].id == "lead"

    edges = [(src.id, dst.id) for src, dst in fake._captured["wb_edges"]]
    assert ("lead", "w1") in edges
    assert ("w1", "lead") in edges
    assert ("lead", "w2") in edges
    assert ("w2", "lead") in edges


def test_workflow_builder_hierarchical_uses_supervisor_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``hierarchical`` shares the supervisor topology — both must use
    the supervisor graph builder."""
    fake = _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.workflow_builder import MAFWorkflowBuilder

    composition = make_composition(
        architecture="hierarchical",
        slots=[
            {"id": "lead", "agent_id": "fabric-admin", "role": "Lead"},
            {"id": "w1", "agent_id": "fabric-admin", "role": "W1"},
        ],
        entrypoint="lead",
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)
    builder = MAFWorkflowBuilder(slot_runner=runner)
    builder.build(composition, lambda _a: MagicMock(display_name="X"))

    assert "wb_start" in fake._captured
    assert fake._captured["wb_start"].id == "lead"
    assert "handoff_start" not in fake._captured


def test_workflow_builder_supervisor_single_slot_falls_back_to_sequential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 1-slot supervisor cannot have workers; degrade to sequential
    rather than hand a degenerate graph to MAF."""
    fake = _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.workflow_builder import MAFWorkflowBuilder

    composition = make_composition(
        architecture="supervisor",
        slots=[{"id": "only", "agent_id": "fabric-admin", "role": "Solo"}],
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    MAFWorkflowBuilder(slot_runner=runner).build(
        composition, lambda _a: MagicMock(display_name="X"),
    )

    assert [p.id for p in fake._captured["participants"]] == ["only"]
    assert "wb_start" not in fake._captured


def test_workflow_builder_reflection_uses_actor_critic_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``reflection`` must build an actor↔critic loop via
    ``WorkflowBuilder`` — not be flattened into a sequential run.
    This was the production gap: Step 2 advertised ``Reflection`` but
    the runtime ran a single forward pass."""
    fake = _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.workflow_builder import MAFWorkflowBuilder

    composition = make_composition(
        architecture="reflection",
        slots=[
            {"id": "actor", "agent_id": "fabric-admin", "role": "Actor"},
            {"id": "critic", "agent_id": "fabric-admin", "role": "Critic"},
        ],
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    builder = MAFWorkflowBuilder(slot_runner=runner)
    builder.build(composition, lambda _a: MagicMock(display_name="X"))

    # Reflection must use WorkflowBuilder edges, not SequentialBuilder.
    assert "wb_start" in fake._captured
    assert fake._captured["wb_start"].id == "actor"
    edges = [(src.id, dst.id) for src, dst in fake._captured["wb_edges"]]
    assert ("actor", "critic") in edges
    assert ("critic", "actor") in edges
    assert "participants" not in fake._captured


def test_workflow_builder_reflection_single_slot_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 1-slot reflection composition cannot loop; degrade to
    sequential instead of hanging the workflow."""
    fake = _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.workflow_builder import MAFWorkflowBuilder

    composition = make_composition(
        architecture="reflection",
        slots=[{"id": "only", "agent_id": "fabric-admin", "role": "Solo"}],
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    MAFWorkflowBuilder(slot_runner=runner).build(
        composition, lambda _a: MagicMock(display_name="X"),
    )
    assert [p.id for p in fake._captured["participants"]] == ["only"]


def test_workflow_builder_unknown_architecture_falls_back_to_freeform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown architecture ids must keep running (degrade) instead of
    crashing the orchestrator. ``Composition.architecture`` is a strict
    Literal — we mutate after construction to simulate a stale legacy
    session id reaching the dispatcher."""
    fake = _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.workflow_builder import MAFWorkflowBuilder

    composition = make_composition(
        architecture="sequential",
        slots=[{"id": "only", "agent_id": "fabric-admin", "role": "Solo"}],
    )
    object.__setattr__(composition, "architecture", "not-a-real-arch")

    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    MAFWorkflowBuilder(slot_runner=runner).build(
        composition, lambda _a: MagicMock(display_name="X"),
    )
    assert "wb_start" in fake._captured


def test_checkpointing_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTHUB_CHECKPOINTING_ENABLED", raising=False)
    fake = _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.workflow_builder import MAFWorkflowBuilder

    composition = make_composition(
        architecture="sequential",
        slots=[{"id": "s1", "agent_id": "fabric-admin", "role": "A"}],
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    builder = MAFWorkflowBuilder(slot_runner=runner)
    builder.build_sequential(composition, lambda _a: MagicMock(display_name="X"))

    # Checkpoint storage is omitted entirely when the env var is off.
    assert "sequential_checkpoint" not in fake._captured or (
        fake._captured.get("sequential_checkpoint") is None
    )


def test_structured_handoff_emitted_in_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ContainerAgent emits a JSON-fenced handoff in addition to the
    human-readable summary — replacing regex-only extraction."""
    _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.container_agent import make_container_agent

    composition = make_composition(
        architecture="sequential",
        slots=[{"id": "s1", "agent_id": "fabric-admin", "role": "Admin"}],
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    agent = make_container_agent(
        slot_id="s1", role="Admin", agent_name="fabric_admin", slot_runner=runner,
    )
    response = asyncio.run(agent._run_once("do something"))
    body = response.messages[0].contents[0].text

    assert "===HANDOFF_START===" in body
    assert "===HANDOFF_END===" in body
    # Payload JSON survives round-trip.
    start = body.index("===HANDOFF_START===") + len("===HANDOFF_START===\n")
    end = body.index("===HANDOFF_END===")
    import json as _json
    payload = _json.loads(body[start:end].strip())
    assert payload["from"] == "s1"
    # role is pulled from SlotResult.role (may be empty in mock)
    assert "role" in payload
    assert "status" in payload
    assert "summary" in payload


def test_container_agent_stream_yields_update_with_author_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: MAF's ``AgentExecutor`` reassembles the response via
    ``AgentResponse.from_updates`` which reads ``update.author_name``.
    ``_run_stream`` must therefore yield ``AgentResponseUpdate``
    instances (not ``AgentResponse``) — yielding ``AgentResponse``
    caused ``AttributeError: 'AgentResponse' object has no attribute
    'author_name'`` in production."""
    _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.container_agent import make_container_agent

    composition = make_composition(
        architecture="sequential",
        slots=[{"id": "s1", "agent_id": "fabric-admin", "role": "Admin"}],
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    agent = make_container_agent(
        slot_id="s1", role="Admin", agent_name="fabric_admin", slot_runner=runner,
    )

    async def _drain() -> list:
        out = []
        # ``run(stream=True)`` must return an async iterator.
        async for update in agent.run("do something", stream=True):
            out.append(update)
        return out

    updates = asyncio.run(_drain())
    assert len(updates) == 1
    update = updates[0]

    # This is the critical invariant the production bug violated.
    assert isinstance(update, _FakeAgentResponseUpdate)
    assert hasattr(update, "author_name"), (
        "AgentResponseUpdate must carry author_name — MAF's "
        "AgentResponse.from_updates will raise AttributeError otherwise"
    )
    assert update.author_name == "fabric_admin"
    assert update.role == "assistant"
    # Must carry proper Content objects (type='text'), not raw strings,
    # otherwise MAF's AgentResponseUpdate.text property crashes.
    assert update.contents, "update must have contents"
    for c in update.contents:
        assert getattr(c, "type", None) == "text"
        assert isinstance(getattr(c, "text", None), str) and c.text


def test_container_agent_run_once_returns_agent_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-streaming path still returns a full ``AgentResponse``."""
    _install_fake_maf(monkeypatch)
    from services.agenthub.drivers.maf.container_agent import make_container_agent

    composition = make_composition(
        architecture="sequential",
        slots=[{"id": "s1", "agent_id": "fabric-admin", "role": "Admin"}],
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    agent = make_container_agent(
        slot_id="s1", role="Admin", agent_name="fabric_admin", slot_runner=runner,
    )
    # run(stream=False) returns a coroutine.
    response = asyncio.run(agent.run("upstream text", stream=False))
    assert isinstance(response, _FakeAgentResponse)
    assert len(response.messages) == 1
    assert response.messages[0].author_name == "fabric_admin"
    assert response.messages[0].role == "assistant"
    # Each message carries a proper Content object, not a bare string.
    content = response.messages[0].contents[0]
    assert getattr(content, "type", None) == "text"
    assert "===HANDOFF_START===" in content.text


# ── Real-MAF integration smoke tests ─────────────────────────────────
#
# These run against the **actual** ``agent_framework`` package (no
# fake) to catch protocol-shape regressions like the production bug
# where ``_run_stream`` yielded ``AgentResponse`` instead of
# ``AgentResponseUpdate``. They require ``agent_framework`` to be
# importable (which it always is — it's a required dependency now).


@pytest.fixture
def _real_maf(monkeypatch: pytest.MonkeyPatch):
    """Clear the cached fake types so the real MAF is used. Restore
    on teardown."""
    # Remove any previously installed fake.
    monkeypatch.delitem(sys.modules, "agent_framework", raising=False)
    monkeypatch.delitem(sys.modules, "agent_framework.orchestrations", raising=False)
    # Clear the container_agent cache.
    from services.agenthub.drivers.maf import container_agent as _ca
    _ca._MAF_TYPES.clear()
    yield
    _ca._MAF_TYPES.clear()


def test_stream_yields_real_AgentResponseUpdate(_real_maf) -> None:
    """Regression against the production ``AttributeError`` — yield
    value must be a genuine ``AgentResponseUpdate`` (``author_name``
    present) so ``AgentResponse.from_updates`` can aggregate it."""
    from agent_framework import AgentResponseUpdate  # type: ignore[import-not-found]
    from services.agenthub.drivers.maf.container_agent import make_container_agent

    composition = make_composition(
        architecture="sequential",
        slots=[{"id": "s1", "agent_id": "fabric-admin", "role": "Admin"}],
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    agent = make_container_agent(
        slot_id="s1", role="Admin", agent_name="fabric_admin", slot_runner=runner,
    )

    async def _drain():
        return [u async for u in agent.run("go", stream=True)]

    updates = asyncio.run(_drain())
    assert len(updates) == 1
    update = updates[0]
    assert isinstance(update, AgentResponseUpdate)
    assert update.author_name == "fabric_admin"
    # ``.text`` property must not raise — this is the exact failure
    # mode seen in the backend traceback.
    assert isinstance(update.text, str)
    assert update.text


def test_response_from_updates_reassembles(_real_maf) -> None:
    """End-to-end: collecting our stream and running it through MAF's
    ``AgentResponse.from_updates`` yields a valid response. This is
    precisely the call ``AgentExecutor._run_agent_streaming`` makes."""
    from agent_framework import AgentResponse  # type: ignore[import-not-found]
    from services.agenthub.drivers.maf.container_agent import make_container_agent

    composition = make_composition(
        architecture="sequential",
        slots=[{"id": "s1", "agent_id": "fabric-admin", "role": "Admin"}],
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    agent = make_container_agent(
        slot_id="s1", role="Admin", agent_name="fabric_admin", slot_runner=runner,
    )

    async def _drain():
        return [u async for u in agent.run("go", stream=True)]

    updates = asyncio.run(_drain())
    # The assertion below is the one that crashed in production.
    response = AgentResponse.from_updates(updates)
    assert response.messages
    assert any(m.author_name == "fabric_admin" for m in response.messages)


def test_real_maf_sequential_workflow_runs_end_to_end(_real_maf) -> None:
    """Full regression: build an actual MAF ``SequentialBuilder``
    workflow from our ``ContainerAgent`` participants and run it.

    This is the path that crashed in production with
    ``AttributeError: 'AgentResponse' object has no attribute
    'author_name'``. The test passes only when ``_run_stream`` yields
    genuine ``AgentResponseUpdate`` objects with ``author_name``."""
    from services.agenthub.drivers.maf.workflow_builder import MAFWorkflowBuilder

    composition = make_composition(
        architecture="sequential",
        slots=[
            {"id": "s1", "agent_id": "fabric-admin", "role": "First"},
            {"id": "s2", "agent_id": "fabric-admin", "role": "Second"},
        ],
        handoffs=[{"from": "s1", "to": "s2", "kind": "handoff"}],
        entrypoint="s1",
    )
    exe = make_execution(composition)
    runner = make_runner(exe, composition)

    builder = MAFWorkflowBuilder(slot_runner=runner)
    workflow = builder.build_sequential(
        composition, lambda _aid: MagicMock(display_name="FabricAdmin"),
    )

    async def _run() -> list:
        events = []
        async for ev in workflow.run("kick off mission", stream=True):
            events.append(ev)
        return events

    events = asyncio.run(_run())

    # Both slots actually executed — proves the real MAF pipeline
    # drove our ContainerAgent through its streaming path.
    slot_ids = [c["slot_id"] for c in runner.calls]
    assert slot_ids == ["s1", "s2"], (
        f"Expected both slots to run via real MAF, got {slot_ids}"
    )
    assert events, "Expected events from the real MAF workflow"
