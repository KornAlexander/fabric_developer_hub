"""Unit tests for hand-written exception classes in ``domain.exceptions``."""
from __future__ import annotations

from uuid import uuid4

from fastapi import status

from domain.exceptions.exceptions import (
    AuthenticationException,
    AuthenticationUIRequiredException,
    DoubledOperandsOverflowException,
    InternalErrorException,
    InvalidItemPayloadException,
    InvalidParameterException,
    InvalidRelativePathException,
    InvariantViolationException,
    ItemMetadataNotFoundException,
    KustoDataException,
    MissingLakehouseReferenceException,
    TooManyRequestsException,
    UnauthorizedException,
    UnexpectedItemTypeException,
    _quote_header_value,
)


def test_quote_header_value_escapes_backslash_and_quote() -> None:
    assert _quote_header_value('a"b\\c') == 'a\\"b\\\\c'


def test_quote_header_value_strips_crlf() -> None:
    assert "\r" not in _quote_header_value("a\r\nb")
    assert "\n" not in _quote_header_value("a\r\nb")


def test_internal_error_exception_shape() -> None:
    e = InternalErrorException("boom")
    assert e.http_status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert e.internal_message == "boom"
    assert e.to_telemetry_string() == "boom"


def test_invariant_violation_telemetry_prefix() -> None:
    e = InvariantViolationException("never happens")
    assert e.to_telemetry_string() == "INVARIANT VIOLATION: never happens"


def test_invalid_relative_path_message() -> None:
    e = InvalidRelativePathException("../etc/passwd")
    assert "../etc/passwd" in e.internal_message


def test_unexpected_item_type_and_unauthorized_and_auth() -> None:
    assert UnexpectedItemTypeException("x").http_status_code == 500
    assert UnauthorizedException().http_status_code == status.HTTP_403_FORBIDDEN
    assert AuthenticationException("bad token").http_status_code == status.HTTP_401_UNAUTHORIZED


def test_auth_ui_required_with_claims_header() -> None:
    e = AuthenticationUIRequiredException("needs MFA")
    e.add_claims_for_conditional_access('{"a":"b"}')
    hdr = e.to_www_authenticate_header()
    assert hdr.startswith("Bearer")
    assert 'error="invalid_token"' in hdr
    assert 'claims="' in hdr
    # Accessor matches what we set
    assert e.claims_for_conditional_access_policy == '{"a":"b"}'


def test_auth_ui_required_with_scopes_header() -> None:
    e = AuthenticationUIRequiredException("needs scopes")
    e.add_scopes_to_consent(["User.Read", "Files.Read"])
    hdr = e.to_www_authenticate_header()
    assert 'error="insufficient_scope"' in hdr
    assert 'scope="User.Read Files.Read"' in hdr


def test_auth_ui_required_interaction_required_default() -> None:
    e = AuthenticationUIRequiredException("interact")
    hdr = e.to_www_authenticate_header()
    assert 'error="interaction_required"' in hdr


def test_auth_ui_required_claims_property_falls_back_to_private() -> None:
    """If no details have been added, the property returns the stored raw value."""
    e = AuthenticationUIRequiredException("x")
    e._claims_for_conditional_access = "raw"
    # No with_detail call was made, so details should be empty → fallback hits.
    assert e.claims_for_conditional_access_policy == "raw"


def test_too_many_requests() -> None:
    e = TooManyRequestsException()
    assert e.http_status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_invalid_item_payload_substitutes_params() -> None:
    e = InvalidItemPayloadException("Notebook", "42")
    # Both parameters should be present in message_parameters.
    assert "Notebook" in e.message_parameters
    assert "42" in e.message_parameters
    assert e.http_status_code == 400


def test_doubled_operands_overflow() -> None:
    e = DoubledOperandsOverflowException(["x+y"])
    assert e.http_status_code == 400
    assert "x+y" in e.message_parameters


def test_item_metadata_not_found_uses_uuid() -> None:
    oid = uuid4()
    e = ItemMetadataNotFoundException(oid)
    assert str(oid) in e.message_parameters


def test_invalid_parameter_exception() -> None:
    e = InvalidParameterException("limit", "must be > 0")
    assert e.http_status_code == 400
    assert "limit" in e.message_parameters


def test_kusto_data_and_missing_lakehouse() -> None:
    assert KustoDataException("oops").http_status_code == 400
    assert MissingLakehouseReferenceException().http_status_code == 400
