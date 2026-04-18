"""Smoke test for ``main.setup_logging``.

REGRESSION (Phase 4): the function previously had a type-confused ``appdata``
variable that worked only by coincidence (``Path()`` accepts ``str``). The
fix added a proper ``appdata: Path`` annotation and explicit ``Path()``
coercion in the Windows branch.

This test exercises both branches in a tmp directory and verifies that:
  1. The log directory is created
  2. A log handler is wired up
  3. No exception is raised regardless of platform
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from main import setup_logging


@pytest.fixture
def fake_config() -> MagicMock:
    cs = MagicMock()
    cs.get_log_level.return_value = "Information"
    cs.get_app_name.return_value = "FabricBackend Test"  # space → underscore
    return cs


def _isolate_logging() -> None:
    """Clear root handlers so dictConfig doesn't accumulate file handlers
    across test runs."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass


def test_setup_logging_creates_log_dir_unix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_config: MagicMock
) -> None:
    """Unix branch: log dir under ``$HOME/.config/fabric_backend/<app>/logs``."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(os, "name", "posix", raising=False)


    try:
        logger = setup_logging(fake_config)
        log_dir = tmp_path / ".config" / "fabric_backend" / "FabricBackend_Test" / "logs"
        assert log_dir.is_dir(), f"log dir not created at {log_dir}"
        assert isinstance(logger, logging.Logger)
        # Verify file handler was attached at root
        root_handlers = logging.getLogger().handlers
        assert any(
            isinstance(h, logging.handlers.RotatingFileHandler)  # type: ignore[attr-defined]
            for h in root_handlers
        )
    finally:
        _isolate_logging()


@pytest.mark.skipif(os.name != "nt", reason="Path(...) cannot instantiate WindowsPath on non-Windows")
def test_setup_logging_creates_log_dir_windows_branch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_config: MagicMock
) -> None:
    """Windows branch (REGRESSION-locked): used to suffer Path/str type
    confusion. The fix's branch must coerce APPDATA → Path correctly."""
    appdata = tmp_path / "AppData" / "Roaming"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(os, "name", "nt", raising=False)


    try:
        logger = setup_logging(fake_config)
        log_dir = appdata / "FabricBackend_Test" / "logs"
        assert log_dir.is_dir(), f"log dir not created at {log_dir}"
        assert isinstance(logger, logging.Logger)
    finally:
        _isolate_logging()


@pytest.mark.skipif(os.name != "nt", reason="Path(...) cannot instantiate WindowsPath on non-Windows")
def test_setup_logging_windows_fallback_when_appdata_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_config: MagicMock
) -> None:
    """Windows branch with no APPDATA env var: falls back to expanduser. The
    fallback used to also be type-confused — locking it here."""
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # used by os.path.expanduser on Windows
    monkeypatch.setenv("HOME", str(tmp_path))         # used on Unix expanduser
    monkeypatch.setattr(os, "name", "nt", raising=False)


    try:
        # Just verify no exception is raised; the actual fallback path differs
        # by platform so we don't assert the exact location.
        logger = setup_logging(fake_config)
        assert isinstance(logger, logging.Logger)
    finally:
        _isolate_logging()


def test_setup_logging_unknown_log_level_falls_back_to_info(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unknown level string from config → INFO default."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(os, "name", "posix", raising=False)

    cs = MagicMock()
    cs.get_log_level.return_value = "GarbageLevel"
    cs.get_app_name.return_value = "App"


    try:
        setup_logging(cs)
        assert logging.getLogger().level == logging.INFO
    finally:
        _isolate_logging()
