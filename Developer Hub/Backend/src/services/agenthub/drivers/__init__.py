"""Architecture drivers package.

Microsoft Agent Framework is the sole orchestration backend.
``MAFUniversalDriver`` is registered for every architecture id at
import time; there are no legacy hand-rolled drivers.
"""

import logging

from services.agenthub.drivers.maf.universal_driver import MAFUniversalDriver
from services.agenthub.drivers.registry import DriverRegistry

_logger = logging.getLogger(__name__)


# Every architecture served by the registry.
_ARCHITECTURES = (
    "solo",
    "sequential",
    "parallel",
    "supervisor",
    "hierarchical",
    "reflection",
    "mixed",
    "router",
    "network",
    # Legacy aliases that may still be present on in-flight sessions.
    "debate",
    "magentic",
)


_maf_driver = MAFUniversalDriver()
DriverRegistry.register_legacy(_maf_driver)
for _arch in _ARCHITECTURES:
    DriverRegistry.register(_arch, _maf_driver)
_logger.info(
    "[DRIVERS] Registered MAFUniversalDriver for: %s",
    ", ".join(_ARCHITECTURES),
)


__all__ = ["DriverRegistry"]
