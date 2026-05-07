# AgentHub Orchestration Benchmark Suite

Scope: a deterministic harness that lets us answer the strategy doc's
question — *"does AgentHub produce better outcomes than a single
prompt or a plain Copilot CLI invocation?"* — with reproducible
numbers instead of opinions.

This directory currently contains the **scaffold**: task fixtures, a
metric definition, and a runner skeleton. Provider wiring (Copilot /
Azure OpenAI / Foundry) is intentionally out of scope for the
scaffold so the harness is reviewable before we burn budget on real
runs.

## Layout

```
benchmarks/
├── README.md                 ← this file
├── fixtures/
│   └── tasks.json            ← deterministic task inputs
├── metrics.py                ← scoring + aggregation
└── run_benchmark.py          ← CLI entrypoint (skeleton)
```

## Metrics tracked per task

| Metric | Why it matters |
|---|---|
| `task_success` (bool) | Did the run produce the requested artifact / decision? |
| `tool_denials` (int) | How often did the policy gate block an unsafe call? |
| `recovery_invocations` (int) | Did the orchestrator self-recover from a failure? |
| `user_interventions` (int) | Number of approvals / clarifications required. |
| `elapsed_s` (float) | Wall-clock time end-to-end. |
| `artifact_validation_passed` (bool) | Did downstream validators (notebook run, SQL parse, PBIR lint) accept the output? |

Aggregation is deliberately simple — task-level pass/fail with a
per-baseline breakdown — because we want quick signal, not a paper.

## Baselines

| id | description |
|---|---|
| `single_prompt` | One LLM call with the user's task and no tools. |
| `cli_like` | One LLM call with the same task plus a generic shell tool. |
| `agenthub_compose_only` | Run only `ComposeService`; do not execute the workflow. |
| `agenthub_full` | Full Mission Control execution with policy + recovery. |

The first two baselines simulate what a stripped-down GitHub Copilot
CLI would do for the same prompt, so we can measure the AgentHub
delta in concrete terms.

## Status

This is a P0 scaffold. The next steps are:

1. Implement runnable `single_prompt` baseline against an
   `OpenAICompatibleChatClient` using a test API key.
2. Hook `agenthub_full` into the existing in-memory session store.
3. Land 5–10 representative Fabric tasks in `fixtures/tasks.json`
   (lakehouse build, governance audit, capacity check, semantic
   model fix, migration assistant).
4. Wire the harness into CI as a nightly job and record baseline
   numbers in `docs/`.
