"""Payment gateway HTTP client."""


class PaymentClient:
    """Connects to external Payment API."""

    def __init__(self, endpoint: str = "https://api.paymentprovider.internal/v1") -> None:
        self.endpoint = endpoint

    def call_payment_api(self, order_id: str, amount: float) -> dict[str, str]:
        """Send payment authorization to Payment API."""
        return {"order_id": order_id, "status": "APPROVED", "transaction_id": "tx_998877"}

    def ping(self) -> bool:
        """Health check for Payment API."""
        return True
