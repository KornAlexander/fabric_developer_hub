"""Metrics and aggregation for the AgentHub orchestration benchmark.

Kept deliberately small: every metric is a primitive that aggregates
without external dependencies. The eval harness itself is a P0
scaffold — see ``benchmarks/README.md`` for status and roadmap.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import mean
from typing import Iterable


@dataclass
class TaskResult:
    """Outcome of running one benchmark task with one baseline."""

    task_id: str
    baseline: str
    task_success: bool = False
    tool_denials: int = 0
    recovery_invocations: int = 0
    user_interventions: int = 0
    elapsed_s: float = 0.0
    artifact_validation_passed: bool = False
    notes: str = ""

    def to_row(self) -> dict:
        return asdict(self)


@dataclass
class BaselineSummary:
    """Aggregate scores for one baseline across the task suite."""

    baseline: str
    runs: int = 0
    success_rate: float = 0.0
    validation_rate: float = 0.0
    avg_tool_denials: float = 0.0
    avg_recoveries: float = 0.0
    avg_interventions: float = 0.0
    avg_elapsed_s: float = 0.0
    raw: list[TaskResult] = field(default_factory=list)

    def to_row(self) -> dict:
        out = asdict(self)
        out["raw"] = [r.to_row() for r in self.raw]
        return out


def summarize(results: Iterable[TaskResult]) -> list[BaselineSummary]:
    """Group ``TaskResult`` rows by baseline and aggregate."""
    by_baseline: dict[str, list[TaskResult]] = {}
    for r in results:
        by_baseline.setdefault(r.baseline, []).append(r)

    summaries: list[BaselineSummary] = []
    for baseline, rows in by_baseline.items():
        if not rows:
            continue
        summaries.append(
            BaselineSummary(
                baseline=baseline,
                runs=len(rows),
                success_rate=_rate(rows, lambda r: r.task_success),
                validation_rate=_rate(rows, lambda r: r.artifact_validation_passed),
                avg_tool_denials=mean(r.tool_denials for r in rows),
                avg_recoveries=mean(r.recovery_invocations for r in rows),
                avg_interventions=mean(r.user_interventions for r in rows),
                avg_elapsed_s=mean(r.elapsed_s for r in rows),
                raw=list(rows),
            )
        )
    summaries.sort(key=lambda s: s.baseline)
    return summaries


def _rate(rows, predicate) -> float:
    if not rows:
        return 0.0
    return sum(1 for r in rows if predicate(r)) / len(rows)
