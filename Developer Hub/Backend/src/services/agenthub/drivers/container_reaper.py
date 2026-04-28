"""Orphan container reaper — kills agent containers whose sessions ended.

Runs as a background asyncio task. Defends against containers orphaned
by orchestrator crashes, missed cleanup in exception paths, or Docker
daemon hiccups.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, UTC

logger = logging.getLogger(__name__)

_REAP_INTERVAL_S = 60
_ORPHAN_AGE_THRESHOLD_S = 300  # 5 minutes


async def run_orphan_reaper(
    get_active_session_ids: callable,
    *,
    interval: float = _REAP_INTERVAL_S,
    max_age_s: float = _ORPHAN_AGE_THRESHOLD_S,
) -> None:
    """Periodically scan for and remove orphaned agent containers.

    ``get_active_session_ids`` is a callable that returns the set of
    currently active session ids. Containers whose
    ``agenthub.session_id`` label is not in this set (and are older
    than ``max_age_s``) are killed and removed.
    """
    try:
        import docker
        client = docker.from_env()
    except Exception as exc:
        logger.info("[REAPER] Docker not available — orphan reaper disabled: %s", exc)
        return

    logger.info("[REAPER] Starting orphan container reaper (interval=%ds, max_age=%ds)", interval, max_age_s)

    while True:
        try:
            await asyncio.sleep(interval)
            containers = await asyncio.to_thread(
                client.containers.list,
                filters={"label": "agenthub.session_id"},
                all=True,
            )
            active_ids = get_active_session_ids()

            for c in containers:
                session_id = c.labels.get("agenthub.session_id")
                if session_id in active_ids:
                    continue

                # Check age
                created_str = c.attrs.get("Created", "")
                try:
                    created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    age = (datetime.now(UTC) - created).total_seconds()
                except Exception:
                    age = max_age_s + 1  # if we can't parse, reap it

                if age > max_age_s:
                    logger.warning(
                        "[REAPER] Killing orphan container %s (session=%s, age=%.0fs)",
                        c.short_id, session_id, age,
                    )
                    try:
                        await asyncio.to_thread(c.kill)
                    except Exception:
                        pass  # may already be stopped
                    try:
                        await asyncio.to_thread(c.remove, force=True)
                    except Exception as exc:
                        logger.warning("[REAPER] Failed to remove %s: %s", c.short_id, exc)

        except asyncio.CancelledError:
            logger.info("[REAPER] Orphan reaper shutting down")
            return
        except Exception as exc:
            logger.warning("[REAPER] Reaper iteration failed: %s", exc)
