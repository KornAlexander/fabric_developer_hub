"""Structured local ledger for AgentHub mission events.

The human log stream is intentionally compact. This JSONL ledger keeps the
support-grade event envelope with digests and redacted previews so operators can
join backend emits, SSE events, and frontend display rows without dumping full
payloads into normal logs.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.agenthub._db import db_path
from services.observability import safe_json_preview, stable_digest

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


def record_event(entry: dict[str, Any]) -> None:
    """Append one structured event to the local JSONL ledger.

    Ledger failures are deliberately non-fatal: observability must never break
    mission execution or SSE delivery.
    """
    path = _ledger_path()
    if path is None:
        return
    try:
        payload = {
            "recordedAt": datetime.now(UTC).isoformat(),
            **entry,
        }
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")) + "\n")
    except Exception:
        logger.debug("AgentHub event ledger append failed", exc_info=True)


def ledger_digest(payload: Any) -> str:
    return stable_digest(payload)


def ledger_preview(payload: Any, *, max_chars: int = 1200) -> str:
    return safe_json_preview(payload, max_chars=max_chars)


def _ledger_path() -> Path | None:
    raw = os.environ.get("AGENTHUB_EVENT_LEDGER_FILE")
    if raw is not None and not raw.strip():
        return None
    if raw and raw.strip():
        return Path(raw.strip())
    try:
        return Path(db_path()).with_name("agenthub-events.jsonl")
    except Exception:
        return None