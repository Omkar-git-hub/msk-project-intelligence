"""Token budgeting, context size estimation, and deterministic selection."""

import math
import re

from msk.context.models import BudgetMetrics, ContextItem, OmittedItem

_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]")


def estimate_tokens(text: str | None) -> int:
    """Documented deterministic token counter approximating BPE tokenizers.

    Uses word and punctuation token splitting (re.findall(r'\\w+|[^\\w\\s]'))
    with character-ratio bounds (max of token chunks and len/3.8).
    Avoids heavy external dependencies (like tiktoken) while maintaining
    deterministic, platform-independent token metrics.
    """
    if not text or not text.strip():
        return 0

    chunks = len(_TOKEN_PATTERN.findall(text))
    char_estimate = math.ceil(len(text) / 3.8)
    return max(chunks, char_estimate, 1)


def select_items_within_budget(
    candidates: list[ContextItem],
    budget_tokens: int,
) -> tuple[list[ContextItem], list[OmittedItem], BudgetMetrics]:
    """Select highest-ranking items within budget_tokens with deterministic tie-breaking and deduplication."""
    selected_items: list[ContextItem] = []
    omitted_items: list[OmittedItem] = []
    seen_keys: set[tuple[str, str, str]] = set()

    current_tokens = 0

    for item in candidates:
        # Deduplication key: (item_type, file_path, name)
        dedup_key = (item.item_type, item.file_path, item.name)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        cost = item.estimated_tokens
        if current_tokens + cost <= budget_tokens:
            selected_items.append(item)
            current_tokens += cost
        else:
            remaining = max(0, budget_tokens - current_tokens)
            omitted_items.append(
                OmittedItem(
                    name=item.name,
                    item_type=item.item_type,
                    file_path=item.file_path,
                    relevance_score=item.relevance_score,
                    estimated_tokens=cost,
                    reason=f"Exceeded token budget: requires {cost} tokens, only {remaining} remaining of {budget_tokens} budget.",
                )
            )

    utilization = (current_tokens / budget_tokens * 100.0) if budget_tokens > 0 else 0.0

    metrics = BudgetMetrics(
        token_budget=budget_tokens,
        estimated_tokens_used=current_tokens,
        utilization_pct=round(utilization, 1),
        total_candidates=len(seen_keys),
        selected_count=len(selected_items),
        omitted_count=len(omitted_items),
    )

    return selected_items, omitted_items, metrics
