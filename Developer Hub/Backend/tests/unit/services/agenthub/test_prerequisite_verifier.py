"""PrerequisiteVerifier tests — spec §4.3.

Covers:
  * manual kind leaves status as unknown with a reason.
  * missing verifier probe downgrades cleanly to unknown (never raises).
  * probe timeouts / exceptions are caught and stamped as unknown.
  * successful verification caches per-user + per-spec.
  * ``footer.execution_blocked`` is true iff any prereq is ``missing``.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from domain.models.plan import (
    Plan,
    PlanFooter,
    Prerequisite,
    PrereqVerification,
)
from services.agenthub.prerequisite_verifier import PrerequisiteVerifier


def _plan_with(prereqs: list[Prerequisite]) -> Plan:
    return Plan(
        job_id="j1",
        summary="x",
        prerequisites=prereqs,
        steps=[],
        footer=PlanFooter(),
    )


def _prereq(
    pid: str,
    *,
    kind: str = "manual",
    spec: dict[str, Any] | None = None,
) -> Prerequisite:
    return Prerequisite(
        id=pid,
        title=f"prereq {pid}",
        description="d",
        text=f"t {pid}",
        category="workspace_role",
        applies_to_step_ids=["s1"],
        verification=PrereqVerification(kind=kind, spec=spec or {}),
    )


@pytest.mark.asyncio
async def test_manual_kind_yields_unknown_with_reason() -> None:
    plan = _plan_with([_prereq("p1", kind="manual", spec={"unknownReason": "why"})])
    v = PrerequisiteVerifier()
    await v.verify_plan(plan, user_id="u1")

    p = plan.prerequisites[0]
    assert p.verification.status == "unknown"
    assert p.verification.unknown_reason == "why"
    assert plan.footer.execution_blocked is False


@pytest.mark.asyncio
async def test_missing_probe_downgrades_to_unknown() -> None:
    # ``fabric_api`` has no probe in default registry → unknown.
    plan = _plan_with([_prereq("p1", kind="fabric_api", spec={"item": "x"})])
    v = PrerequisiteVerifier()
    await v.verify_plan(plan, user_id="u1")

    p = plan.prerequisites[0]
    assert p.verification.status == "unknown"
    assert p.verification.unknown_reason
    assert "fabric_api" in p.verification.unknown_reason


@pytest.mark.asyncio
async def test_probe_exception_downgrades_to_unknown() -> None:
    async def boom(_spec, _tokens):
        raise RuntimeError("nope")

    plan = _plan_with([_prereq("p1", kind="fabric_api")])
    v = PrerequisiteVerifier(probes={"fabric_api": boom})
    await v.verify_plan(plan, user_id="u1")

    p = plan.prerequisites[0]
    assert p.verification.status == "unknown"
    assert "RuntimeError" in (p.verification.unknown_reason or "")


@pytest.mark.asyncio
async def test_probe_timeout_downgrades_to_unknown() -> None:
    async def slow(_spec, _tokens):
        await asyncio.sleep(5)
        return "satisfied", "ok"

    plan = _plan_with([_prereq("p1", kind="fabric_api")])
    v = PrerequisiteVerifier(probes={"fabric_api": slow}, timeout_s=0.05)
    await v.verify_plan(plan, user_id="u1")

    assert plan.prerequisites[0].verification.status == "unknown"


@pytest.mark.asyncio
async def test_missing_prereq_blocks_execute() -> None:
    async def missing(_spec, _tokens):
        return "missing", "user not a Member"

    plan = _plan_with([
        _prereq("p1", kind="graph_api", spec={"scope": "Tenant.Read.All"}),
    ])
    v = PrerequisiteVerifier(probes={"graph_api": missing})
    await v.verify_plan(plan, user_id="u1")

    assert plan.prerequisites[0].verification.status == "missing"
    assert plan.footer.execution_blocked is True


@pytest.mark.asyncio
async def test_verified_cached_per_user_and_spec() -> None:
    calls: list[int] = []

    async def ok(_spec, _tokens):
        calls.append(1)
        return "satisfied", "evidence-text"

    v = PrerequisiteVerifier(probes={"capacity_api": ok})
    # First pass — probe runs.
    plan1 = _plan_with([_prereq("p1", kind="capacity_api", spec={"cap": "c1"})])
    await v.verify_plan(plan1, user_id="u1")
    assert plan1.prerequisites[0].verification.status == "satisfied"
    assert len(calls) == 1

    # Second pass with same user + same spec — cache hit, no probe.
    plan2 = _plan_with([_prereq("p1", kind="capacity_api", spec={"cap": "c1"})])
    await v.verify_plan(plan2, user_id="u1")
    assert plan2.prerequisites[0].verification.status == "satisfied"
    assert len(calls) == 1  # not called again

    # Different user — probe runs again.
    plan3 = _plan_with([_prereq("p1", kind="capacity_api", spec={"cap": "c1"})])
    await v.verify_plan(plan3, user_id="u2")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_clear_cache_per_user_evicts_only_that_user() -> None:
    calls_by_user: dict[str, int] = {}

    async def ok(_spec, tokens):
        uid = (tokens or {}).get("uid", "?")
        calls_by_user[uid] = calls_by_user.get(uid, 0) + 1
        return "satisfied", "ok"

    v = PrerequisiteVerifier(probes={"license_lookup": ok})
    for uid in ("a", "b"):
        plan = _plan_with([_prereq("p1", kind="license_lookup", spec={"sku": "pbi"})])
        await v.verify_plan(plan, user_id=uid, tokens={"uid": uid})
    assert calls_by_user == {"a": 1, "b": 1}

    v.clear_cache(user_id="a")

    plan_a = _plan_with([_prereq("p1", kind="license_lookup", spec={"sku": "pbi"})])
    await v.verify_plan(plan_a, user_id="a", tokens={"uid": "a"})
    # User 'b' still cached; user 'a' probed again.
    plan_b = _plan_with([_prereq("p1", kind="license_lookup", spec={"sku": "pbi"})])
    await v.verify_plan(plan_b, user_id="b", tokens={"uid": "b"})
    assert calls_by_user == {"a": 2, "b": 1}


@pytest.mark.asyncio
async def test_no_prereqs_sets_execute_unblocked() -> None:
    plan = _plan_with([])
    v = PrerequisiteVerifier()
    await v.verify_plan(plan, user_id="u1")
    assert plan.footer.execution_blocked is False
