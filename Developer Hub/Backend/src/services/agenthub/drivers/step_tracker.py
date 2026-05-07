"""StepTracker — structured step numbering for orchestration observability.

Produces human-readable step labels that make execution order immediately
obvious in the logs:

  Sequential:  STEP 1 · Architect → STEP 2 · Modeler → STEP 3 · FabricAdmin
  Parallel:    STEP 2-A · Worker1 | STEP 2-B · Worker2  (same step, different agents)
  Nested:      STEP 2-A · SubLead1 → STEP 2-A.1 · Worker1  (sub-step under parallel)

Every log line from the orchestration layer uses the [ORCH] prefix followed
by the step label, so ``grep '[ORCH]'`` gives a clean execution timeline.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class StepTracker:
    """Tracks execution steps across an orchestration session."""

    def __init__(self, session_id: str, architecture: str):
        self._sid = session_id[:8]
        self._arch = architecture
        self._step = 0
        self._start = time.monotonic()
        self._slot_names: dict[str, str] = {}  # slot_id → display name
        self._completed: list[str] = []
        self._skipped: list[str] = []
        self._failed: list[str] = []

    def register_names(self, slot_names: dict[str, str]) -> None:
        """Register slot_id → agent display name mapping."""
        self._slot_names.update(slot_names)

    def _name(self, slot_id: str) -> str:
        return self._slot_names.get(slot_id, slot_id)

    # ── Step label generators ────────────────────────────────────

    def next_step(self) -> int:
        """Advance to the next sequential step number."""
        self._step += 1
        return self._step

    @property
    def current(self) -> int:
        return self._step

    def seq_label(self, slot_id: str) -> str:
        """Label for a sequential step: 'STEP 3 · FabricAdmin'."""
        return f"STEP {self._step} · {self._name(slot_id)}"

    def parallel_labels(self, slot_ids: list[str]) -> dict[str, str]:
        """Labels for parallel slots within the same step.
        Returns {slot_id: 'STEP 2-A · Worker1', ...}.
        """
        labels = {}
        for i, sid in enumerate(slot_ids):
            letter = chr(65 + i)  # A, B, C, ...
            labels[sid] = f"STEP {self._step}-{letter} · {self._name(sid)}"
        return labels

    def sub_label(self, parent_tag: str, sub_index: int, slot_id: str) -> str:
        """Label for a nested sub-step: 'STEP 2-A.1 · Worker1'."""
        return f"STEP {self._step}-{parent_tag}.{sub_index} · {self._name(slot_id)}"

    # ── Structured log emitters ──────────────────────────────────

    def log_slot_start(self, label: str, slot_id: str, *, role: str = "", context: str = "") -> None:
        parts = [f"[ORCH s:{self._sid}] {label} · starting"]
        if role:
            parts.append(f"(role={role[:80]})")
        if context:
            parts.append(f"[{context}]")
        logger.info(" ".join(parts))

    def log_slot_done(
        self, label: str, slot_id: str, *,
        status: str, rounds: int, tools: int, duration_s: float,
    ) -> None:
        icon = "✓" if status in ("success", "partial") else "✗" if status == "error" else "⊘"
        logger.info(
            "[ORCH s:%s] %s · done %s (%d rounds, %d tools, %.1fs) status=%s",
            self._sid, label, icon, rounds, tools, duration_s, status,
        )
        if status in ("success", "partial"):
            self._completed.append(slot_id)
        elif status == "error":
            self._failed.append(slot_id)

    def log_slot_skipped(self, slot_id: str, reason: str) -> None:
        label = f"STEP {self._step + 1} · {self._name(slot_id)}"
        logger.info("[ORCH s:%s] %s · SKIPPED (%s)", self._sid, label, reason)
        self._skipped.append(slot_id)

    def log_handoff(self, from_id: str, to_id: str, kind: str = "report") -> None:
        logger.info(
            "[ORCH s:%s] STEP %d→%d · handoff %s → %s (%s)",
            self._sid, self._step, self._step + 1,
            self._name(from_id), self._name(to_id), kind,
        )

    def log_parallel_start(self, slot_ids: list[str]) -> None:
        names = ", ".join(self._name(s) for s in slot_ids)
        logger.info(
            "[ORCH s:%s] STEP %d · %d agents in parallel: [%s]",
            self._sid, self._step, len(slot_ids), names,
        )

    def log_parallel_done(self, succeeded: int, failed: int) -> None:
        logger.info(
            "[ORCH s:%s] STEP %d · parallel complete: %d succeeded, %d failed",
            self._sid, self._step, succeeded, failed,
        )

    def log_phase(self, description: str) -> None:
        """Log a high-level phase within the architecture."""
        logger.info("[ORCH s:%s] ── %s ──", self._sid, description)

    def log_session_summary(self) -> None:
        """Log the final session summary."""
        elapsed = time.monotonic() - self._start
        total = len(self._completed) + len(self._skipped) + len(self._failed)
        parts = []
        if self._completed:
            parts.append(f"{len(self._completed)} completed")
        if self._failed:
            parts.append(f"{len(self._failed)} failed")
        if self._skipped:
            parts.append(f"{len(self._skipped)} skipped")
        summary = ", ".join(parts) or "0 slots"
        logger.info(
            "[ORCH s:%s] ══ SESSION DONE ══ %s/%d slots (%s) · %.1fs total · arch=%s",
            self._sid, summary, total, self._arch,
            elapsed, self._arch,
        )
