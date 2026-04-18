"""Property + JWT tests for ``mcp_servers.fabric``.

Complements ``test_fabric_tools.py`` with:
  * Hypothesis property test for ``_get_item_route_segment`` — catches edge
    cases in the camelCase → kebab-plural fallback (empty strings, all-caps,
    single chars, numerics, unicode).
  * JWT integration test for ``_build_item_links`` — verifies the
    ``?ctid=...&experience=fabric-developer`` query string is appended when
    the FABRIC_API_TOKEN is a real JWT with a ``tid`` claim. The current
    test suite always falls into the silent-except branch because the token
    fixture isn't a valid JWT, leaving this code path uncovered.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from jose import jwt

from mcp_servers import fabric


# ── Hypothesis: route segmentation ──────────────────────────────────

# Generate non-empty ASCII strings for item types. Avoid the known-good
# entries from ITEM_ROUTE_SEGMENTS so we exercise the fallback path.
_KNOWN = set(fabric.ITEM_ROUTE_SEGMENTS.keys())
_route_segment_re = re.compile(r"^[a-z0-9][-a-z0-9]*s$")


@given(st.text(alphabet=st.characters(min_codepoint=ord("A"), max_codepoint=ord("z"),
                                       whitelist_categories=("Ll", "Lu", "Nd")),
               min_size=1, max_size=20))
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_get_item_route_segment_property(item_type: str) -> None:
    if item_type in _KNOWN:
        # Skip — we want the fallback only
        return
    result = fabric._get_item_route_segment(item_type)
    # Property: always lowercase, ends in 's', non-empty, only ASCII letters/digits/dashes
    assert result, f"empty result for {item_type!r}"
    assert result == result.lower()
    assert result.endswith("s"), f"{item_type!r} → {result!r} doesn't end in 's'"
    assert _route_segment_re.match(result), f"{item_type!r} → {result!r} fails regex"


def test_get_item_route_segment_specific_cases() -> None:
    """Lock specific edge-case mappings so a future regex change is caught."""
    assert fabric._get_item_route_segment("MyCustomItem") == "my-custom-items"
    assert fabric._get_item_route_segment("X") == "xs"
    assert fabric._get_item_route_segment("AB") == "abs"
    # All-caps acronym should NOT insert dashes inside (no lower-then-upper transition)
    assert fabric._get_item_route_segment("URL") == "urls"


# ── JWT _build_item_links coverage ──────────────────────────────────

def _make_jwt(claims: dict) -> str:
    """Build an unverified-readable JWT — signature isn't verified by
    ``jwt.get_unverified_claims``, so we sign with HS256 + a throwaway key."""
    payload = {
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        **claims,
    }
    return jwt.encode(payload, "test-secret", algorithm="HS256")


def test_build_item_links_with_valid_jwt_includes_ctid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REGRESSION: when FABRIC_API_TOKEN is a real JWT with a ``tid`` claim,
    ``webUrl`` must include ``?ctid=<tid>&experience=fabric-developer``."""
    token = _make_jwt({"tid": "tenant-abc-123"})
    monkeypatch.setenv("FABRIC_API_TOKEN", token)

    links = fabric._build_item_links("ws-1", "lh-1", "Lakehouse")

    assert links["hostPath"] == "/groups/ws-1/lakehouses/lh-1"
    assert "ctid=tenant-abc-123" in links["webUrl"]
    assert "experience=fabric-developer" in links["webUrl"]


def test_build_item_links_without_tid_claim_omits_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JWT without ``tid`` claim → no query string appended."""
    token = _make_jwt({"oid": "user-1"})  # no tid
    monkeypatch.setenv("FABRIC_API_TOKEN", token)

    links = fabric._build_item_links("ws-1", "lh-1", "Lakehouse")

    assert links["webUrl"] == "https://app.powerbi.com/groups/ws-1/lakehouses/lh-1"


def test_build_item_links_with_invalid_jwt_silently_omits_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garbage token → silent fallback to no-query, never raises."""
    monkeypatch.setenv("FABRIC_API_TOKEN", "not-a-jwt-at-all")

    links = fabric._build_item_links("ws-1", "lh-1", "Lakehouse")

    assert links["webUrl"].endswith("/groups/ws-1/lakehouses/lh-1")


def test_build_item_links_with_empty_token_omits_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No token at all → no query string."""
    monkeypatch.delenv("FABRIC_API_TOKEN", raising=False)

    links = fabric._build_item_links("ws-1", "lh-1", "Lakehouse")

    assert "?" not in links["webUrl"]
