from __future__ import annotations

from services.agenthub.compose_models import (
    COMPOSE_FALLBACK_MODEL,
    _classify,
    _family_key,
    _is_excluded,
    _prefer_representative,
    pick_compose_model,
    rank_compose_models,
)


def test_is_excluded_filters_non_chat_and_non_compose_models() -> None:
    assert _is_excluded("text-embedding-3-large", "Embeddings", {"type": "embedding"})
    assert _is_excluded("gpt-4o-audio", "Whisper TTS", {"type": "chat"})
    assert _is_excluded("super-codex", "Code specialist", {"type": "chat"})
    assert not _is_excluded("gpt-4o", "GPT-4o", {"type": "chat"})
    assert not _is_excluded("unknown-chat", "Unknown Chat", None)


def test_classify_known_and_unknown_models() -> None:
    assert _classify("gpt-4o", "") == (0, "Proven default · fast and accurate", "fast")
    assert _classify("claude-3.7-sonnet", "") == (1, "Strong planner · rich rationales", "medium")
    assert _classify("gemini-2.5-flash", "") == (2, "Balanced Gemini", "fast")

    tier, reason, latency = _classify("new-frontier-model", "")
    assert tier > 5
    assert reason is None
    assert latency == "medium"


def test_family_key_prefers_name_and_strips_snapshot_suffixes() -> None:
    assert _family_key("gpt-4o-2024-08-06", "GPT-4o") == "gpt-4o"
    assert _family_key("gpt-4o-2024-08-06", "") == "gpt-4o"
    assert _family_key("model-v20240501", "") == "model"


def test_prefer_representative_keeps_latest_generic_id() -> None:
    assert _prefer_representative("gpt-4o-2024-08-06", "gpt-4o") == "gpt-4o"
    assert _prefer_representative("gpt-4o", "gpt-4o-2024-08-06") == "gpt-4o"
    assert _prefer_representative("z-model", "a-model") == "z-model"


def test_rank_compose_models_filters_dedupes_and_sorts() -> None:
    ranked = rank_compose_models([
        {"id": "", "name": "missing-id"},
        {"id": "text-embedding-3-large", "name": "Embedding", "capabilities": {"type": "embedding"}},
        {"id": "gpt-4o-2024-08-06", "name": "GPT-4o", "owned_by": "openai"},
        {"id": "gpt-4o", "name": "GPT-4o", "publisher": "openai"},
        {"id": "gpt-4o-mini", "name": "GPT-4o mini"},
        {"id": "unknown-family", "name": "ZZ Unknown"},
    ])

    assert [m["id"] for m in ranked] == ["gpt-4o", "gpt-4o-mini", "unknown-family"]
    assert ranked[0]["top_pick"] is True
    assert ranked[0]["publisher"] == "openai"
    assert ranked[-1]["recommended"] is False
    assert ranked[-1]["reason"] is None


def test_pick_compose_model_honors_available_request_then_fallbacks() -> None:
    available = [{"id": "gpt-4o-mini", "name": "GPT-4o mini"}, {"id": "gpt-4o", "name": "GPT-4o"}]

    assert pick_compose_model("gpt-4o-mini", available) == "gpt-4o-mini"
    assert pick_compose_model("missing", available) == "gpt-4o"
    assert pick_compose_model("custom-model", None) == "custom-model"
    assert pick_compose_model(None, []) == COMPOSE_FALLBACK_MODEL