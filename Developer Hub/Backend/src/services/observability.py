"""Small helpers for safe, high-signal backend observability logs."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_KEY_MARKERS = (
    "authorization",
    "bearer",
    "token",
    "secret",
    "password",
    "credential",
    "api_key",
    "apikey",
    "connection_string",
    "connectionstring",
    "sas",
)


def is_sensitive_key(key: object) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    return any(marker in normalized for marker in SENSITIVE_KEY_MARKERS)


def redact_mapping(
    values: Mapping[str, Any] | Iterable[tuple[str, Any]],
    *,
    max_value_chars: int = 160,
    max_items: int = 20,
) -> dict[str, Any]:
    """Return a bounded mapping with secrets redacted and long values trimmed."""
    items = list(values.items() if isinstance(values, Mapping) else values)
    result: dict[str, Any] = {}
    for key, value in items[:max_items]:
        key_text = str(key)
        if is_sensitive_key(key_text):
            result[key_text] = "[redacted]"
            continue
        text = str(value).replace("\r", " ").replace("\n", " ").strip()
        if len(text) > max_value_chars:
            text = text[:max_value_chars] + "..."
        result[key_text] = text
    if len(items) > max_items:
        result["_truncated"] = f"{len(items) - max_items} more item(s)"
    return result


def safe_url(url: object, *, max_query_value_chars: int = 120) -> str:
    """Return a URL string with sensitive query values redacted."""
    text = str(url)
    try:
        parts = urlsplit(text)
    except Exception:
        return text[:500]
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    safe_query = urlencode(
        [
            (
                key,
                "[redacted]" if is_sensitive_key(key) else str(value)[:max_query_value_chars],
            )
            for key, value in query_pairs
        ],
        doseq=True,
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, safe_query, ""))[:700]


def summarize_headers(headers: Mapping[str, Any], *, allowed: Iterable[str] | None = None) -> dict[str, Any]:
    """Summarize request/response headers without dumping credentials."""
    if allowed is not None:
        allowed_set = {name.lower() for name in allowed}
        pairs = ((key, value) for key, value in headers.items() if str(key).lower() in allowed_set)
    else:
        pairs = headers.items()
    return redact_mapping(pairs, max_value_chars=140, max_items=25)


def bounded_text(value: object, *, max_chars: int = 500) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) > max_chars:
        marker = f"... [truncated chars={len(text)} max={max_chars}]"
        if max_chars <= len(marker):
            return marker[:max_chars]
        return text[:max_chars - len(marker)] + marker
    return text


def stable_digest(value: Any, *, length: int = 16) -> str:
    """Return a short stable digest for structured data or text."""
    try:
        body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        body = repr(value)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:length]


def safe_json_preview(value: Any, *, max_chars: int = 1200) -> str:
    """Return a bounded JSON preview with sensitive keys redacted."""
    try:
        safe_value = _redact_value(value)
        body = json.dumps(safe_value, sort_keys=True, default=str, separators=(",", ":"))
    except Exception:
        body = bounded_text(value, max_chars=max_chars)
    if len(body) > max_chars:
        return bounded_text(body, max_chars=max_chars)
    return body


def _redact_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[max-depth]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 30:
                result["_truncated"] = f"{len(value) - 30} more item(s)"
                break
            key_text = str(key)
            result[key_text] = "[redacted]" if is_sensitive_key(key_text) else _redact_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        items = [_redact_value(item, depth=depth + 1) for item in list(value)[:30]]
        if len(value) > 30:
            items.append(f"... {len(value) - 30} more item(s)")
        return items
    if isinstance(value, str):
        return bounded_text(value, max_chars=500)
    return value


def collection_counts(rows: Iterable[Mapping[str, Any]], key: str, *, max_items: int = 8) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:max_items])
