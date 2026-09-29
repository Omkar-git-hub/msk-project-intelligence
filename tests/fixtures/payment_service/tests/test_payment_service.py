"""Unit tests for payment service."""

from payment.client import PaymentClient
from payment.repository import PaymentRepository
from payment.service import PaymentService


class PaymentServiceTest:
    """Test suite targeting PaymentService."""

    def test_process_payment_success(self) -> None:
        client = PaymentClient()
        repo = PaymentRepository()
        service = PaymentService(client, repo)
        assert service.process_payment("ord_123", 100.0) is True

    def test_process_payment_negative_amount(self) -> None:
        client = PaymentClient()
        repo = PaymentRepository()
        service = PaymentService(client, repo)
        assert service.process_payment("ord_123", -5.0) is False
