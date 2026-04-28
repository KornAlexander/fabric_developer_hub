"""Microsoft Agent Framework (MAF) adapter package.

MAF is the **sole** orchestration backend for agenthub. Every
architecture in the driver registry is served by
``MAFUniversalDriver`` (see ``universal_driver.py``), which
translates a ``Composition`` into a MAF ``Workflow`` via
``MAFWorkflowBuilder`` (see ``workflow_builder.py``).

The ``agent_framework`` Python package is therefore a **required**
dependency of the backend (see ``pyproject.toml``). We keep
``maf_available()`` around as a runtime guard for defensive code
paths but it should always return ``True`` in a correctly-installed
environment.

Modules
-------
* ``container_agent``  — ``ContainerAgent`` BaseAgent wrapping
  ``SlotRunner`` so each participant still runs in an isolated
  Docker container.
* ``workflow_builder`` — ``MAFWorkflowBuilder``: one build method
  per architecture + ``build()`` dispatcher.
* ``event_adapter``    — translates MAF ``WorkflowEvent`` →
  ``_JobExecution.emit``.
* ``checkpointing``    — optional MAF ``CheckpointStorage``
  (env-gated via ``AGENTHUB_CHECKPOINTING_ENABLED``).
* ``universal_driver`` — ``MAFUniversalDriver``: architecture-
  agnostic driver used for every architecture.
* ``sequential_driver``— ``MAFSequentialDriver`` (MAF-backed, kept
  for direct sequential-path tests alongside ``MAFUniversalDriver``).
"""

from __future__ import annotations

from services.agenthub.drivers.maf.availability import maf_available

__all__ = ["maf_available"]
