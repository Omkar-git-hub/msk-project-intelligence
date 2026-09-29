"""Knowledge graph relationship types and definitions."""

from enum import StrEnum


class RelationshipType(StrEnum):
    """Canonical edge types for the MSK Project Knowledge Graph."""

    FILE_CONTAINS_SYMBOL = "FILE_CONTAINS_SYMBOL"
    SYMBOL_IMPORTS_SYMBOL = "SYMBOL_IMPORTS_SYMBOL"
    FILE_DEPENDS_ON_FILE = "FILE_DEPENDS_ON_FILE"
    SYMBOL_CALLS_SYMBOL = "SYMBOL_CALLS_SYMBOL"
    TEST_TARGETS_SYMBOL = "TEST_TARGETS_SYMBOL"
    COMPONENT_DEPENDS_ON_COMPONENT = "COMPONENT_DEPENDS_ON_COMPONENT"
