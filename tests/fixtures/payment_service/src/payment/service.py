"""Payment service core business logic."""

from payment.client import PaymentClient
from payment.repository import PaymentRepository


class PaymentService:
    """Orchestrates order payment processing."""

    def __init__(self, client: PaymentClient, repo: PaymentRepository) -> None:
        self.client = client
        self.repo = repo

    def process_payment(self, order_id: str, amount: float) -> bool:
        """Validate order, call upstream API, and persist transaction."""
        if amount <= 0:
            return False
        response = self.client.call_payment_api(order_id, amount)
        if response.get("status") == "APPROVED":
            self.repo.save_transaction(order_id, amount)
            return True
        return False

    def validate_order(self, order_id: str) -> bool:
        """Validate order integrity."""
        return len(order_id) > 0
