"""Checkpoint storage factory — MAF ``CheckpointStorage`` from env.

Opt-in via env vars:
* ``AGENTHUB_CHECKPOINTING_ENABLED=1`` — turn on checkpointing.
* ``AGENTHUB_CHECKPOINT_DIR`` — if set to an existing directory, use
  ``FileCheckpointStorage`` rooted there. Otherwise use
  ``InMemoryCheckpointStorage`` (process-local, lost on restart).

All MAF workflow builders in this package accept the returned storage
and persist a snapshot at each superstep.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from services.agenthub.drivers.maf.availability import ensure_agent_framework_version

logger = logging.getLogger(__name__)


def get_checkpoint_storage() -> Any | None:
    """Return a MAF ``CheckpointStorage`` instance or ``None`` if disabled.

    Imports ``agent_framework`` lazily so modules that never need the
    checkpoint path don't pull it in at import time.
    """
    flag = (os.environ.get("AGENTHUB_CHECKPOINTING_ENABLED") or "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return None

    try:
        ensure_agent_framework_version()
        from agent_framework import (  # type: ignore[import-not-found]
            FileCheckpointStorage,
            InMemoryCheckpointStorage,
        )
    except ImportError:  # pragma: no cover — agent_framework is a required dep
        logger.warning(
            "[CHECKPOINT] agent_framework not importable; checkpointing disabled.",
        )
        return None

    directory = os.environ.get("AGENTHUB_CHECKPOINT_DIR", "").strip()
    if directory and os.path.isdir(directory):
        logger.info("[CHECKPOINT] Using FileCheckpointStorage at %s", directory)
        return FileCheckpointStorage(storage_path=directory)
    logger.info("[CHECKPOINT] Using InMemoryCheckpointStorage")
    return InMemoryCheckpointStorage()
