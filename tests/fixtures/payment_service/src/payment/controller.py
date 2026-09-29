"""Payment controller module."""

from payment.service import PaymentService


class PaymentController:
    """Handles incoming payment HTTP requests."""

    def __init__(self, service: PaymentService) -> None:
        self.service = service

    def handle_payment(self, order_id: str, amount: float) -> dict[str, str]:
        """Process incoming payment request."""
        success = self.service.process_payment(order_id, amount)
        return {"status": "SUCCESS" if success else "FAILED"}

    def get_status(self, payment_id: str) -> str:
        """Retrieve payment status."""
        return "COMPLETED"

# test change
# trigger update
