from __future__ import annotations

from unittest.mock import MagicMock

from services import opentelemetry_setup


def test_configure_local_opentelemetry_disabled(monkeypatch) -> None:
    monkeypatch.setenv("AGENTHUB_OTEL_ENABLED", "0")

    assert opentelemetry_setup.configure_local_opentelemetry(
        MagicMock(),
        service_name="test-service",
    ) is False