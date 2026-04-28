"""Runtime detection of the ``agent_framework`` optional dependency.

Isolated in its own module so every other MAF adapter file can do
``from .availability import maf_available`` without circular imports
and without paying the import cost of ``agent_framework`` itself.
"""

from __future__ import annotations

import importlib
from importlib import metadata
import importlib.util
import logging

logger = logging.getLogger(__name__)

_MAF_MODULES = ("agent_framework",)


def maf_available() -> bool:
    """Return ``True`` if the ``agent_framework`` package can be imported.

    Does not actually import it — uses ``importlib.util.find_spec`` so
    the cost is a filesystem lookup, not a full module load.
    """
    for mod in _MAF_MODULES:
        if importlib.util.find_spec(mod) is None:
            logger.debug("[MAF] Package '%s' not installed", mod)
            return False
    return True


def ensure_agent_framework_version() -> None:
    """Patch older/newer ``agent_framework`` builds with missing root exports.

    Some installed builds import ``from . import __version__`` from internal
    submodules even though the package root does not define it. The backend
    container's build also leaves root symbols such as ``Content`` and
    ``Message`` unexported, while orchestration internals import them from the
    package root. Import the root module first and add best-effort attributes
    before any orchestration submodule import triggers those paths.
    """
    module = importlib.import_module("agent_framework")
    if not getattr(module, "__version__", None):
        version = "0.0.0"
        for package_name in ("agent-framework", "agent_framework"):
            try:
                version = metadata.version(package_name)
                break
            except metadata.PackageNotFoundError:
                continue
        setattr(module, "__version__", version)

    for submodule_name in ("agent_framework._types", "agent_framework._agents"):
        try:
            submodule = importlib.import_module(submodule_name)
        except ImportError:
            logger.debug("[MAF] %s not importable for root shim", submodule_name, exc_info=True)
            continue
        for name in dir(submodule):
            if name.startswith("_") or hasattr(module, name):
                continue
            setattr(module, name, getattr(submodule, name))

    # agent-framework 1.x split the monolithic root export surface across
    # private submodules, leaving ``agent_framework/__init__.py`` empty.
    # Re-export the workflow builder symbols our drivers expect so existing
    # ``from agent_framework import WorkflowBuilder`` call sites keep working.
    _WORKFLOW_REEXPORTS = {
        "agent_framework._workflows._workflow_builder": ("WorkflowBuilder",),
        "agent_framework._workflows._workflow": ("Workflow", "WorkflowRunResult"),
    }
    for submodule_name, names in _WORKFLOW_REEXPORTS.items():
        try:
            submodule = importlib.import_module(submodule_name)
        except ImportError:
            logger.debug("[MAF] %s not importable for workflow shim", submodule_name, exc_info=True)
            continue
        for name in names:
            if hasattr(module, name):
                continue
            attr = getattr(submodule, name, None)
            if attr is not None:
                setattr(module, name, attr)
