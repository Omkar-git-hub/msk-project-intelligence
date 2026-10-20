"""Deterministic relevance ranking and scoring for MSK Context."""

from typing import Final

from msk.context.models import ContextItem, ContextItemType

# Base signal weights
WEIGHT_EXACT_TARGET: Final[float] = 100.0
WEIGHT_STACK_FRAME: Final[float] = 95.0
WEIGHT_CONTAINED_CONTAINING: Final[float] = 85.0
WEIGHT_DIRECT_RELATIONSHIP: Final[float] = 75.0
WEIGHT_RELATED_TEST: Final[float] = 75.0
WEIGHT_DIRECT_FILE_DEP: Final[float] = 70.0
WEIGHT_EXACT_KEYWORD: Final[float] = 60.0
WEIGHT_TRANSITIVE_DEPTH_2: Final[float] = 40.0
WEIGHT_TRANSITIVE_DEPTH_3: Final[float] = 25.0
WEIGHT_API_ENDPOINT: Final[float] = 45.0
WEIGHT_INFRASTRUCTURE: Final[float] = 35.0
WEIGHT_SECURITY_FINDING: Final[float] = 25.0

# Type priority for deterministic tie-breaking
_TYPE_PRIORITY: Final[dict[str, int]] = {
    ContextItemType.SYMBOL: 1,
    ContextItemType.FILE: 2,
    ContextItemType.TEST: 3,
    ContextItemType.API_ENDPOINT: 4,
    ContextItemType.INFRASTRUCTURE: 5,
    ContextItemType.DEPENDENCY: 6,
    ContextItemType.SECURITY: 7,
}


def sort_and_rank_items(items: list[ContextItem]) -> list[ContextItem]:
    """Sort candidate context items deterministically by relevance score with stable tie-breaking.

    Tie-breaking tuple:
    1. Relevance score descending (-relevance_score)
    2. Entity type priority (symbols > files > tests > APIs > infra > deps > security)
    3. File path ascending
    4. Entity name ascending
    5. Entity ID ascending
    """
    def tie_breaker(item: ContextItem) -> tuple[float, int, str, str, str]:
        type_rank = _TYPE_PRIORITY.get(item.item_type, 99)
        return (
            -item.relevance_score,
            type_rank,
            item.file_path,
            item.name,
            item.id,
        )

    return sorted(items, key=tie_breaker)
