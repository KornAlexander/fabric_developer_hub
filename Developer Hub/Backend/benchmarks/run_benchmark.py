"""Run the AgentHub orchestration benchmark suite.

This is the **P0 scaffold** referenced from
[agenthub-orchestration-strategy.md](../../../docs/agenthub-orchestration-strategy.md).
It loads the fixture tasks and prints a summary table per baseline.
The actual baselines are stubs that return deterministic placeholder
``TaskResult`` rows so the harness shape can be reviewed and CI-wired
before we connect to real LLM providers.

Usage::

    python -m benchmarks.run_benchmark --baselines single_prompt,agenthub_full

When real provider hooks land, the stubs in ``_run_*`` are replaced
without touching the CLI surface.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from benchmarks.metrics import TaskResult, summarize


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "tasks.json"

BASELINES = ("single_prompt", "cli_like", "agenthub_compose_only", "agenthub_full")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AgentHub orchestration benchmark")
    parser.add_argument(
        "--baselines",
        default=",".join(BASELINES),
        help="Comma-separated baseline ids to run.",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=FIXTURE_PATH,
        help="Path to the task fixture JSON.",
    )
    args = parser.parse_args(argv)

    with args.fixture.open("r", encoding="utf-8") as fh:
        tasks = json.load(fh)

    selected = [b.strip() for b in args.baselines.split(",") if b.strip()]
    unknown = [b for b in selected if b not in BASELINES]
    if unknown:
        print(f"Unknown baselines: {unknown}. Valid: {BASELINES}", file=sys.stderr)
        return 2

    results: list[TaskResult] = []
    for task in tasks:
        for baseline in selected:
            results.append(_run_one(baseline, task))

    summaries = summarize(results)
    _print_table(summaries)
    return 0


def _run_one(baseline: str, task: dict) -> TaskResult:
    """Stub baseline runner. Real implementations land in P0.5b."""
    started = time.monotonic()
    # Deterministic placeholder results so the harness output is
    # reviewable without LLM calls.
    if baseline == "agenthub_full":
        success, validated = True, True
        denials = 1 if task.get("policy_should_block") else 0
    elif baseline == "agenthub_compose_only":
        success, validated = True, False
        denials = 0
    elif baseline == "single_prompt":
        success, validated = False, False
        denials = 0
    else:  # cli_like
        success, validated = bool(not task.get("policy_should_block")), False
        denials = 0

    return TaskResult(
        task_id=task["id"],
        baseline=baseline,
        task_success=success,
        tool_denials=denials,
        recovery_invocations=0,
        user_interventions=0,
        elapsed_s=time.monotonic() - started,
        artifact_validation_passed=validated,
        notes="stub",
    )


def _print_table(summaries) -> None:
    cols = (
        "baseline",
        "runs",
        "success_rate",
        "validation_rate",
        "avg_tool_denials",
        "avg_elapsed_s",
    )
    widths = {c: max(len(c), 16) for c in cols}
    print("\t".join(c.ljust(widths[c]) for c in cols))
    for s in summaries:
        row = (
            s.baseline,
            str(s.runs),
            f"{s.success_rate:.2f}",
            f"{s.validation_rate:.2f}",
            f"{s.avg_tool_denials:.2f}",
            f"{s.avg_elapsed_s:.4f}",
        )
        print("\t".join(v.ljust(widths[c]) for c, v in zip(cols, row)))


if __name__ == "__main__":
    raise SystemExit(main())
