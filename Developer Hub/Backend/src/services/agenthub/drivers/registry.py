"""Driver registry — maps architecture id to driver instance."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.agenthub.drivers.base import ArchitectureDriver

logger = logging.getLogger(__name__)

_FEATURE_FLAG_TPL = "FEATURE_DRIVER_{arch}_ENABLED"


class DriverRegistry:
    _drivers: dict[str, ArchitectureDriver] = {}
    _legacy: ArchitectureDriver | None = None

    @classmethod
    def register(cls, arch_id: str, driver: ArchitectureDriver) -> None:
        cls._drivers[arch_id] = driver

    @classmethod
    def register_legacy(cls, driver: ArchitectureDriver) -> None:
        """Register the fallback driver used when no dedicated driver
        exists or when a driver's feature flag is disabled."""
        cls._legacy = driver

    @classmethod
    def get(cls, arch_id: str) -> ArchitectureDriver:
        driver = cls._drivers.get(arch_id)
        if driver is None:
            if cls._legacy is not None:
                return cls._legacy
            raise ValueError(
                f"No driver registered for architecture '{arch_id}'. "
                f"Registered: {sorted(cls._drivers)}"
            )
        # Feature-flag check: FEATURE_DRIVER_<ARCH>_ENABLED=false → fallback
        env_var = _FEATURE_FLAG_TPL.format(arch=arch_id.upper())
        flag = os.environ.get(env_var)
        if flag is not None and flag.strip().lower() in ("0", "false", "no", "off"):
            logger.info("[DRIVER_REGISTRY] %s disabled by %s — using legacy", arch_id, env_var)
            if cls._legacy is not None:
                return cls._legacy
        return driver

    @classmethod
    def clear(cls) -> None:
        """Test-only: reset registry state."""
        cls._drivers.clear()
        cls._legacy = None
