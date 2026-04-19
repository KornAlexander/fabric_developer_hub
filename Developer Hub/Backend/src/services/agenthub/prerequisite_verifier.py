"""Prerequisite verifier — runs the server-side checks that populate
each ``Prerequisite.verification.{status, checked_at, evidence,
unknown_reason}`` before the plan is returned to the UI.

Spec: docs/plan-generation-overhaul.md §4.

The planner proposes the ``kind`` + ``spec`` for each prerequisite; the
verifier is the single source of truth for the actual status. We never
fail a plan because a verifier errors — a verifier that raises, times
out, or lacks credentials downgrades that prerequisite to
``status == "unknown"`` with a concise ``unknown_reason`` and we move on.

This module deliberately keeps the ``fabric_api`` / ``graph_api`` / etc.
probe implementations as small, dependency-injected functions. The
wired-up probes that actually hit Fabric/Graph/Capacity live with the
orchestrator so tests can mock them per-kind.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from domain.models.plan import (
    Plan,
    PrereqStatus,
    Prerequisite,
    PrereqVerification,
)

logger = logging.getLogger(__name__)


# Per-kind probe signature: given the prerequisite's ``spec`` dict plus
# optional request-scoped tokens, return ``(status, evidence_or_reason)``.
# Implementations SHOULD raise on transport errors so the retry/timeout
# wrapper downgrades cleanly to ``unknown``.
ProbeFn = Callable[[dict[str, Any], dict[str, Any] | None], Awaitable[tuple[PrereqStatus, str]]]

_VERIFIER_TIMEOUT_S = 3.0
_VERIFIER_MAX_PARALLEL = 8


async def _manual_probe(
    spec: dict[str, Any], _tokens: dict[str, Any] | None,
) -> tuple[PrereqStatus, str]:
    """Manual checks can't be automated; they stay unknown by design."""
    reason = (
        spec.get("unknownReason")
        or spec.get("unknown_reason")
        or "Manual verification required — no automated probe exists for this check."
    )
    return "unknown", str(reason)


# Default registry. Kinds other than "manual" start as stubs that return
# unknown + a note — concrete implementations hook in via
# ``PrerequisiteVerifier(probes=...)`` so tests can inject their own.
DEFAULT_PROBES: dict[str, ProbeFn] = {"manual": _manual_probe}


class PrerequisiteVerifier:
    """Runs each prerequisite's verifier in parallel with a short timeout.

    Rules (spec §4.3):
      1. Each verifier has a hard timeout (suggested 3 s) and ONE retry
         on transient errors. Retries are caller's responsibility —
         concretely, the probe function can do its own backoff; the
         verifier only enforces the outer timeout.
      2. Run all verifiers in parallel, bounded to 8 concurrent.
      3. Never fail the whole plan if a verifier errors — downgrade to
         ``status == "unknown"`` with a concise ``unknown_reason``.
      4. Cache successful verifications for the session keyed by
         ``(user_id, spec)``; invalidate on explicit user refresh.
      5. Log each verification (kind, status, latency) for diagnosis.
    """

    def __init__(
        self,
        probes: dict[str, ProbeFn] | None = None,
        *,
        timeout_s: float = _VERIFIER_TIMEOUT_S,
        max_parallel: int = _VERIFIER_MAX_PARALLEL,
    ):
        self.probes: dict[str, ProbeFn] = {**DEFAULT_PROBES, **(probes or {})}
        self.timeout_s = timeout_s
        self.max_parallel = max_parallel
        # Session-scoped cache: (user_id, kind, sorted-spec-json) -> verification.
        self._cache: dict[tuple[str, str, str], PrereqVerification] = {}

    def clear_cache(self, user_id: str | None = None) -> None:
        """Drop cached verifications. If ``user_id`` is given, only that
        user's entries are evicted — matches spec §4.3 rule 4.
        """
        if user_id is None:
            self._cache.clear()
            return
        keys = [k for k in self._cache if k[0] == user_id]
        for k in keys:
            self._cache.pop(k, None)

    async def verify_plan(
        self,
        plan: Plan,
        *,
        user_id: str = "",
        tokens: dict[str, Any] | None = None,
    ) -> Plan:
        """Populate every prerequisite's verification fields in-place and
        recompute ``footer.execution_blocked``. Returns the same plan
        for chaining.
        """
        if not plan.prerequisites:
            plan.footer.execution_blocked = False
            return plan

        sem = asyncio.Semaphore(self.max_parallel)

        async def _one(p: Prerequisite) -> None:
            async with sem:
                await self._verify_one(p, user_id=user_id, tokens=tokens)

        await asyncio.gather(*(_one(p) for p in plan.prerequisites))

        plan.footer.execution_blocked = any(
            p.verification.status == "missing" for p in plan.prerequisites
        )
        return plan

    async def _verify_one(
        self,
        p: Prerequisite,
        *,
        user_id: str,
        tokens: dict[str, Any] | None,
    ) -> None:
        v = p.verification
        cache_key = self._cache_key(user_id, v.kind, v.spec)
        cached = self._cache.get(cache_key)
        if cached is not None:
            p.verification = cached
            # Mirror the new status onto the legacy top-level field.
            p.status = cached.status
            if cached.evidence and not p.evidence:
                p.evidence = cached.evidence
            return

        probe = self.probes.get(v.kind)
        if probe is None:
            # Unknown kind — treat as manual with a reason, don't raise.
            status: PrereqStatus = "unknown"
            evidence = f"No verifier registered for kind '{v.kind}'; manual check required."
            result_is_error = False
        else:
            start = datetime.now(UTC)
            try:
                async with asyncio.timeout(self.timeout_s):
                    status, evidence = await probe(v.spec, tokens)
                result_is_error = False
            except TimeoutError:
                status = "unknown"
                evidence = f"Verifier '{v.kind}' timed out after {self.timeout_s:.1f}s."
                result_is_error = True
            except Exception as e:  # noqa: BLE001 — we explicitly want to swallow
                status = "unknown"
                evidence = f"Verifier '{v.kind}' failed: {e.__class__.__name__}: {e}"
                result_is_error = True
                logger.warning(
                    "[prereq] probe kind=%s failed: %s", v.kind, e, exc_info=True,
                )
            finally:
                latency_ms = int(
                    (datetime.now(UTC) - start).total_seconds() * 1000,
                )
                logger.info(
                    "[prereq] kind=%s status=%s latency=%dms text=%r",
                    v.kind, status, latency_ms, p.text or p.title,
                )

        updated = PrereqVerification(
            kind=v.kind,
            spec=v.spec,
            status=status,
            checked_at=datetime.now(UTC).isoformat(),
            evidence=evidence if status != "unknown" else None,
            unknown_reason=evidence if status == "unknown" else None,
        )
        p.verification = updated
        p.status = status
        # Surface evidence into the legacy field so pre-spec-§4 UI keeps
        # rendering something useful.
        if updated.evidence:
            p.evidence = updated.evidence
        elif updated.unknown_reason:
            p.evidence = updated.unknown_reason

        # Cache successful verifications only — transient errors should
        # be retriable on the next request (spec §4.3 rule 4).
        if not result_is_error and status != "unknown":
            self._cache[cache_key] = updated

    @staticmethod
    def _cache_key(
        user_id: str, kind: str, spec: dict[str, Any],
    ) -> tuple[str, str, str]:
        import json as _json
        try:
            spec_key = _json.dumps(spec, sort_keys=True, default=str)
        except Exception:
            spec_key = repr(spec)
        return (user_id, kind, spec_key)


__all__ = ["PrerequisiteVerifier", "ProbeFn", "DEFAULT_PROBES"]
