"""Prioritized model recommendations for the Compose step.

The composer LLM is asked to produce a small JSON object describing an
agent team for a user task. What matters most, in priority order:

 1. **Fit / accuracy** — follows instructions, respects our architecture
    and agent catalog, emits clean JSON with `response_format=json_object`.
 2. **Structured output reliability** — must not drift into prose /
    markdown / hallucinated agent ids (we have a repair retry but it
    doubles latency).
 3. **Speed** — the user is staring at a spinner; anything above ~20s
    feels broken. Long-context isn't needed (prompt is < 8k tokens).
 4. **Vision support** — only matters when the user attached images.
    Nice-to-have, not a gate; we fall back gracefully.

This module exposes:

 * ``COMPOSE_MODEL_PRIORITY`` — canonical ordered list of *preferred*
   model ids, best first. Used as the tiebreaker when ranking the
   models the user actually has access to in their Copilot catalog.
 * ``COMPOSE_FALLBACK_MODEL`` — used when the user's catalog contains
   none of the preferred ids.
 * ``rank_compose_models(available)`` — takes the raw list returned by
   ``/api/github/models`` and returns a ranked + annotated list of
   compose-capable entries the UI can render directly next to the
   "Plan this" button.

Rationale for the ordering below (April 2026 Copilot catalog):

 * ``gpt-4.1`` — current flagship for structured output; excellent at
   respecting JSON schemas and emitting clean catalog references.
   Slightly slower than 4o but the accuracy gain outweighs it for the
   one-shot plan.  Top recommendation.
 * ``gpt-4o`` — the proven default. Very fast, reliable JSON mode,
   extremely strong on short structured tasks. Still our fallback when
   nothing else is available.
 * ``claude-sonnet-4`` / ``claude-3.7-sonnet`` — excellent planners,
   strong at following detailed rules; marginally slower than gpt-4o
   but produce richer rationales.
 * ``claude-3.5-sonnet`` — reliable second-tier Claude; good fallback
   when Sonnet-4 isn't in the catalog.
 * ``o4-mini`` / ``o3-mini`` — reasoning models. Highest correctness
   for complex team shapes (hierarchical, mixed) but noticeably slower
   (5-15s). Worth surfacing for power users.
 * ``gemini-2.5-pro`` — competitive on reasoning, JSON mode works, but
   empirically more variable on strict catalog adherence than the
   OpenAI / Anthropic tiers above.
 * ``gpt-4.1-mini`` / ``gpt-4o-mini`` — fastest options; good for the
   simple solo/sequential cases. Trade some planning depth for speed.
 * Models we deliberately exclude from the picker: embedding models,
   vision-only variants (e.g. ``gpt-4-vision-preview``), legacy
   ``gpt-3.5-turbo``, and the highest-latency ``o3`` reasoning model
   (the accuracy gain over ``o4-mini`` doesn't justify the latency for
   this one-shot step).
"""
from __future__ import annotations

from typing import Any


# Ordered best→worst. The index of an entry is its tier; lower is
# better. Entries not in this list get a tier of ``len(_PRIORITY)``.
COMPOSE_MODEL_PRIORITY: list[str] = [
    "gpt-4.1",
    "gpt-4o",
    "claude-sonnet-4",
    "claude-3.7-sonnet",
    "claude-3-7-sonnet",  # alternate id spelling
    "claude-3.5-sonnet",
    "claude-3-5-sonnet",
    "o4-mini",
    "o3-mini",
    "gemini-2.5-pro",
    "gpt-4.1-mini",
    "gpt-4o-mini",
]

# Short, stable "why this model" label we render next to recommended
# entries. Keep these < 42 chars so the menu stays tidy.
_REASONS: dict[str, str] = {
    "gpt-4.1": "Best fit · strong JSON structure",
    "gpt-4o": "Recommended · fast and accurate",
    "claude-sonnet-4": "Deep reasoning · rich rationales",
    "claude-3.7-sonnet": "Deep reasoning · rich rationales",
    "claude-3-7-sonnet": "Deep reasoning · rich rationales",
    "claude-3.5-sonnet": "Reliable planner",
    "claude-3-5-sonnet": "Reliable planner",
    "o4-mini": "Reasoning model · highest accuracy",
    "o3-mini": "Reasoning model · slower, more careful",
    "gemini-2.5-pro": "Strong reasoning alternative",
    "gpt-4.1-mini": "Fastest · good for simple teams",
    "gpt-4o-mini": "Fastest · good for simple teams",
}

# Latency-class hint rendered as a small pill. Intentionally coarse —
# the user just wants to know "is this the slow one?".
_LATENCY_HINT: dict[str, str] = {
    "gpt-4.1": "medium",
    "gpt-4o": "fast",
    "claude-sonnet-4": "medium",
    "claude-3.7-sonnet": "medium",
    "claude-3-7-sonnet": "medium",
    "claude-3.5-sonnet": "medium",
    "claude-3-5-sonnet": "medium",
    "o4-mini": "slow",
    "o3-mini": "slow",
    "gemini-2.5-pro": "medium",
    "gpt-4.1-mini": "fast",
    "gpt-4o-mini": "fast",
}

# Fallback used by the compose service when no explicit model was
# requested and nothing in the catalog matches the priority list.
COMPOSE_FALLBACK_MODEL = "gpt-4o"


# Substrings that clearly identify a model as unsuitable for the
# compose step (embedding, image-only, transcription, legacy).
_EXCLUDED_SUBSTRINGS: tuple[str, ...] = (
    "embedding",
    "embed",
    "whisper",
    "tts",
    "dall-e",
    "dalle",
    "moderation",
    "text-davinci",
    "gpt-3.5",
    "text-embedding",
    "vision-preview",
)


def _is_excluded(model_id: str) -> bool:
    mid = (model_id or "").lower()
    return any(s in mid for s in _EXCLUDED_SUBSTRINGS)


def _tier(model_id: str) -> int:
    """Lower is better. Unknown models sort below every known one."""
    try:
        return COMPOSE_MODEL_PRIORITY.index(model_id)
    except ValueError:
        return len(COMPOSE_MODEL_PRIORITY)


def rank_compose_models(available: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rank the user's available Copilot models for the compose step.

    ``available`` is the raw ``/api/github/models`` payload entries —
    each dict has at least an ``id`` and a ``name``. We drop models
    that are clearly unsuitable (embeddings, TTS, legacy, vision-only),
    annotate the remaining entries with ``tier``, ``recommended``,
    ``reason`` and ``latency``, and sort best-first.

    The top entry in the returned list is what the UI should pre-select.
    Callers that want just the *id* can take ``result[0]["id"]``.
    """
    filtered: list[dict[str, Any]] = []
    for m in available or []:
        mid = str(m.get("id") or "").strip()
        if not mid or _is_excluded(mid):
            continue
        tier = _tier(mid)
        entry: dict[str, Any] = {
            "id": mid,
            "name": m.get("name") or mid,
            "publisher": m.get("publisher") or m.get("owned_by") or "",
            "tier": tier,
            # Anything in the priority list is "recommended"; the first
            # few are flagged as top picks so the UI can badge them.
            "recommended": tier < len(COMPOSE_MODEL_PRIORITY),
            "top_pick": tier < 2,
            "reason": _REASONS.get(mid),
            "latency": _LATENCY_HINT.get(mid, "medium"),
        }
        filtered.append(entry)

    # Sort: tier ascending, then alphabetical by id for stable ordering
    # within a tier (mostly relevant for the unknown-tier tail).
    filtered.sort(key=lambda e: (e["tier"], e["id"]))
    return filtered


def pick_compose_model(
    requested: str | None,
    available: list[dict[str, Any]] | None,
) -> str:
    """Resolve the actual model id to send to the Copilot chat API.

    * If the caller explicitly requested a model and it's in the user's
      catalog (or we have no catalog to validate against), honour it.
    * Otherwise return the first ranked entry from the catalog, or
      ``COMPOSE_FALLBACK_MODEL`` if the catalog is empty / not provided.
    """
    if requested:
        if not available:
            return requested
        ids = {str(m.get("id") or "") for m in available}
        if requested in ids:
            return requested
        # Fall through — caller asked for something they don't have.
    if available:
        ranked = rank_compose_models(available)
        if ranked:
            return ranked[0]["id"]
    return COMPOSE_FALLBACK_MODEL
