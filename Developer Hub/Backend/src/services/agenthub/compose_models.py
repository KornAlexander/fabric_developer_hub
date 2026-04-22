"""Prioritized model recommendations for the Compose step.

The composer LLM produces a small JSON object describing an agent
team for a user task. What matters, in priority order:

 1. **Fit / accuracy** — follows instructions, respects our architecture
    and agent catalog, emits clean JSON with `response_format=json_object`.
 2. **Structured output reliability** — must not drift into prose /
    markdown / hallucinated agent ids.
 3. **Speed** — the user is staring at a spinner; > 20s feels broken.

The Copilot catalog evolves constantly (new families, codex variants,
MiniMax, etc.). Rather than pin a literal id list that goes stale,
we classify models by substring patterns of their id / name and
compute both ``tier`` (lower = better for compose) and ``latency``
(fast / medium / slow) from those patterns. This keeps the picker
useful even for ids we've never seen before.

Exports:

 * ``rank_compose_models(raw)`` — filter + dedupe + tier + sort; the
   UI renders the result directly.
 * ``pick_compose_model(requested, available)`` — resolve the final
   id to send to the Copilot chat API.
 * ``COMPOSE_FALLBACK_MODEL`` — last-resort id if the catalog is empty.
"""
from __future__ import annotations

import re
from typing import Any


COMPOSE_FALLBACK_MODEL = "gpt-4o"


# ── Filtering ──────────────────────────────────────────────────────

# Substrings that clearly identify a model as unsuitable for compose
# (embeddings, audio, image-only, moderation, legacy text-davinci).
_EXCLUDED_SUBSTRINGS: tuple[str, ...] = (
    "embedding",
    "embed",
    "whisper",
    "tts",
    "dall-e",
    "dalle",
    "moderation",
    "text-davinci",
    "text-embedding",
    "gpt-3.5",
    "vision-preview",
)

# Codex-style / coding-specialist ids — excluded because the composer
# output is a JSON team description, not code. Codex variants tend to
# be less compliant on pure JSON schema tasks.
_CODEX_PATTERNS: tuple[str, ...] = (
    "codex",
    "-code-",
    "code-davinci",
)


def _is_excluded(model_id: str, name: str, capabilities: Any) -> bool:
    mid = (model_id or "").lower()
    nm = (name or "").lower()
    mtype = ""
    if isinstance(capabilities, dict):
        mtype = str(capabilities.get("type") or "").lower()
    # If the catalog tells us this isn't a chat model, trust it.
    if mtype and mtype not in ("chat", "completion", "text"):
        return True
    if any(s in mid or s in nm for s in _EXCLUDED_SUBSTRINGS):
        return True
    if any(s in mid for s in _CODEX_PATTERNS):
        return True
    return False


# ── Tiering (lower = better for compose) ───────────────────────────

# Ordered list of (pattern, tier, reason, latency). The first match
# wins, so put more specific patterns before general ones. Patterns
# match on the lowercase model id; we also try the name for resilience.
#
# Tier semantics:
#   0  — top picks (flagship, strong JSON, balanced latency)
#   1  — strong alternatives (rich rationales, deep reasoning)
#   2  — reliable second-tier
#   3  — reasoning-specialised (slower, highest accuracy)
#   4  — fast mini variants (good for simple teams)
#   5  — legacy chat models (still functional)
_TIER_RULES: list[tuple[re.Pattern[str], int, str, str]] = [
    # Tier 0 — flagships
    (re.compile(r"^gpt-5(?:\.\d+)?$"), 0, "Flagship · strongest reasoning + JSON", "medium"),
    (re.compile(r"^gpt-4\.1$"), 0, "Top pick · strong JSON structure", "medium"),
    (re.compile(r"^gpt-4o(?:-2\d{3}.*)?$"), 0, "Proven default · fast and accurate", "fast"),
    (re.compile(r"^claude-(?:sonnet|opus)-5"), 0, "Flagship Claude · deep planner", "medium"),
    (re.compile(r"^claude-sonnet-4(?:[.-]5)?$"), 0, "Deep reasoning · rich rationales", "medium"),
    (re.compile(r"^claude-opus-4"), 0, "Deep reasoning · rich rationales", "slow"),
    # Tier 1 — strong alternatives
    (re.compile(r"^claude-3[.-]7-sonnet"), 1, "Strong planner · rich rationales", "medium"),
    (re.compile(r"^gemini-2\.5-pro"), 1, "Strong reasoning alternative", "medium"),
    (re.compile(r"^gemini-3"), 1, "Strong reasoning alternative", "medium"),
    # Tier 2 — reliable
    (re.compile(r"^claude-3[.-]5-sonnet"), 2, "Reliable planner", "medium"),
    (re.compile(r"^gemini-2\.5(?!-pro)"), 2, "Balanced Gemini", "fast"),
    # Tier 3 — reasoning models
    (re.compile(r"^o5(?:-mini)?$"), 3, "Reasoning model · highest accuracy", "slow"),
    (re.compile(r"^o4(?:-mini)?$"), 3, "Reasoning model · high accuracy", "slow"),
    (re.compile(r"^o3(?:-mini)?$"), 3, "Reasoning model · careful, slower", "slow"),
    # Tier 4 — fast minis
    (re.compile(r"^gpt-5(?:\.\d+)?-mini$"), 4, "Fast · good for simple teams", "fast"),
    (re.compile(r"^gpt-4\.1-mini$"), 4, "Fast · good for simple teams", "fast"),
    (re.compile(r"^gpt-4o-mini"), 4, "Fastest · good for simple teams", "fast"),
    (re.compile(r"^claude-haiku"), 4, "Fast Claude · simple teams", "fast"),
    (re.compile(r"^gemini-(?:2\.5-)?flash"), 4, "Fast Gemini · simple teams", "fast"),
    # Tier 5 — legacy but functional
    (re.compile(r"^gpt-4(?:-turbo)?$"), 5, "Legacy GPT-4 · slower", "medium"),
    (re.compile(r"^gpt-4-\d{4}"), 5, "Legacy GPT-4 · slower", "medium"),
]


def _classify(model_id: str, name: str) -> tuple[int, str | None, str]:
    """Return (tier, reason, latency) for a model id.

    Falls back to a generic tier (len(_TIER_RULES)) for unknown ids
    — they still appear in the picker, just at the bottom.
    """
    mid = (model_id or "").lower()
    nm = (name or "").lower()
    for pattern, tier, reason, latency in _TIER_RULES:
        if pattern.search(mid) or pattern.search(nm):
            return tier, reason, latency
    return len(_TIER_RULES), None, "medium"


# ── Dedup helpers ──────────────────────────────────────────────────

# Dated snapshot suffixes like "-2024-08-06" or "-20240610" get
# collapsed so we don't show four "GPT-4o" entries (Copilot exposes
# several snapshots of the same family).
_SNAPSHOT_SUFFIX_RE = re.compile(r"-(?:20)?2\d(?:\d{4}|-\d{2}-\d{2})$")
_VERSION_SUFFIX_RE = re.compile(r"-v?\d{4,}$")


def _family_key(model_id: str, name: str) -> str:
    """Canonical family key used to collapse duplicates.

    Prefer the display name (case-folded) because Copilot often ships
    several dated snapshots of the same model with identical names.
    Fall back to a sanitised id when name is empty.
    """
    if name:
        return name.strip().casefold()
    mid = (model_id or "").strip().casefold()
    mid = _SNAPSHOT_SUFFIX_RE.sub("", mid)
    mid = _VERSION_SUFFIX_RE.sub("", mid)
    return mid


def _prefer_representative(a: str, b: str) -> str:
    """Pick the "better" id between two duplicates.

    Prefer ids WITHOUT a dated snapshot suffix (they track the latest),
    breaking ties by taking the lexicographically higher id (later
    snapshot).
    """
    a_dated = bool(_SNAPSHOT_SUFFIX_RE.search(a.lower()) or _VERSION_SUFFIX_RE.search(a.lower()))
    b_dated = bool(_SNAPSHOT_SUFFIX_RE.search(b.lower()) or _VERSION_SUFFIX_RE.search(b.lower()))
    if a_dated and not b_dated:
        return b
    if b_dated and not a_dated:
        return a
    return a if a >= b else b


# ── Public API ─────────────────────────────────────────────────────


def rank_compose_models(available: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter, dedupe, tier, and sort the user's Copilot catalog.

    Input: raw entries from ``/api/github/models``.
    Output: best-first list of the user's ACTUAL available models,
    annotated with recommendation metadata. Duplicates sharing the
    same display name are collapsed to a single representative.
    """
    # Step 1: filter obvious non-chat entries.
    filtered: list[dict[str, Any]] = []
    for m in available or []:
        mid = str(m.get("id") or "").strip()
        nm = str(m.get("name") or "").strip()
        if not mid:
            continue
        if _is_excluded(mid, nm, m.get("capabilities")):
            continue
        filtered.append({
            "id": mid,
            "name": nm or mid,
            "publisher": m.get("publisher") or m.get("owned_by") or "",
        })

    # Step 2: dedupe by family key, keeping the best representative.
    by_family: dict[str, dict[str, Any]] = {}
    for m in filtered:
        key = _family_key(m["id"], m["name"])
        cur = by_family.get(key)
        if cur is None:
            by_family[key] = m
        else:
            chosen_id = _prefer_representative(cur["id"], m["id"])
            by_family[key] = m if chosen_id == m["id"] else cur

    # Step 3: tier + annotate.
    ranked: list[dict[str, Any]] = []
    for m in by_family.values():
        tier, reason, latency = _classify(m["id"], m["name"])
        ranked.append({
            "id": m["id"],
            "name": m["name"],
            "publisher": m["publisher"],
            "tier": tier,
            "recommended": tier < len(_TIER_RULES),
            "top_pick": tier == 0,
            "reason": reason,
            "latency": latency,
        })

    # Step 4: sort — tier ascending; within a tier, entries we
    # classified (have a reason) beat unknown tails; alphabetical by
    # name breaks final ties for stability.
    ranked.sort(key=lambda e: (e["tier"], 0 if e["reason"] else 1, e["name"].lower()))
    return ranked


def pick_compose_model(
    requested: str | None,
    available: list[dict[str, Any]] | None,
) -> str:
    """Resolve the actual model id to send to the Copilot chat API."""
    if requested:
        if not available:
            return requested
        ids = {str(m.get("id") or "") for m in available}
        if requested in ids:
            return requested
    if available:
        ranked = rank_compose_models(available)
        if ranked:
            return ranked[0]["id"]
    return COMPOSE_FALLBACK_MODEL
