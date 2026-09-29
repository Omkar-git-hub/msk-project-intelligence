"""Payment data persistence repository."""


class PaymentRepository:
    """Manages payment transaction records."""

    def __init__(self) -> None:
        self._records: dict[str, float] = {}

    def save_transaction(self, order_id: str, amount: float) -> str:
        """Store transaction in database."""
        self._records[order_id] = amount
        return f"rec_{order_id}"

    def find_by_id(self, order_id: str) -> float | None:
        """Find transaction amount by order ID."""
        return self._records.get(order_id)
