"""Catalog subpackage — single source of truth for architectures and
skills the compose LLM may pick from.
"""

from domain.catalogs.architectures import (
    ARCHITECTURES,
    ARCHITECTURES_BY_ID,
    ArchitectureCatalogEntry,
    get_architecture,
)

__all__ = [
    "ARCHITECTURES",
    "ARCHITECTURES_BY_ID",
    "ArchitectureCatalogEntry",
    "get_architecture",
]
