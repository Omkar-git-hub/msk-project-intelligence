"""Unit tests for AI gateway adapter registry and factory."""

import pytest

from msk.common.errors import MSKError
from msk.gateway.base import BaseGatewayAdapter
from msk.gateway.models import GatewayRequest, GatewayResponse, ProviderConfig, ProviderType
from msk.gateway.registry import (
    AIGatewayRegistry,
    MockGatewayAdapter,
    get_default_registry,
)


def test_default_registry_has_mock():
    """Verify default registry comes pre-configured with mock provider."""
    reg = get_default_registry()
    assert ProviderType.MOCK.value in reg.available_types()


def test_mock_adapter_completion():
    """Verify MockGatewayAdapter executes completion without network calls."""
    adapter = MockGatewayAdapter()
    req = GatewayRequest(prompt="Hello MSK", model="mock-model")

    resp = adapter.complete(req)

    assert resp.provider == "mock"
    assert "The project stays private." in resp.content
    assert adapter.call_count == 1
    assert adapter.last_request == req


def test_mock_adapter_send_context_bridge():
    """Verify MockGatewayAdapter translates send_context() payload appropriately."""
    adapter = MockGatewayAdapter(canned_response="Custom canned answer")
    payload = {
        "destination": "local",
        "question": "Where is the payment API configured?",
        "items": [{"name": "payment.py"}],
    }

    result = adapter.send_context(payload)

    assert result["status"] == "success"
    assert result["content"] == "Custom canned answer"
    assert result["items_received"] == 1
    assert adapter.call_count == 1


def test_mock_adapter_check_health():
    """Verify MockGatewayAdapter health check."""
    adapter = MockGatewayAdapter()
    health = adapter.check_health()
    assert health["status"] == "healthy"
    assert health["provider_id"] == "mock"


def test_registry_create_mock_adapter():
    """Verify registry instantiates adapter from ProviderConfig."""
    reg = AIGatewayRegistry()
    cfg = ProviderConfig(
        provider_id="test-mock",
        display_name="Test Mock",
        provider_type=ProviderType.MOCK,
        endpoint="http://localhost:0",
        default_model="mock-1",
    )

    adapter = reg.create(cfg)
    assert isinstance(adapter, MockGatewayAdapter)
    assert adapter.provider_name == "test-mock"


def test_registry_unknown_provider_raises_error():
    """Verify registry raises MSKError when encountering an unregistered provider type."""
    reg = AIGatewayRegistry()
    reg._adapters.clear()  # Clear registered adapters to test unsupported type error
    cfg = ProviderConfig(
        provider_id="unknown-prov",
        display_name="Unknown",
        provider_type=ProviderType.OPENAI,
        endpoint="https://api.openai.com/v1",
        default_model="gpt-4o",
    )

    with pytest.raises(MSKError) as exc_info:
        reg.create(cfg)

    assert "Unsupported AI provider type" in str(exc_info.value)
    assert exc_info.value.hint is not None


def test_registry_register_custom_adapter():
    """Verify custom adapter registration."""
    reg = AIGatewayRegistry()

    class CustomAdapter(BaseGatewayAdapter):
        def complete(self, request: GatewayRequest) -> GatewayResponse:
            return GatewayResponse(
                content="custom",
                provider=self.provider_name,
                model="custom-model",
            )

    reg.register(ProviderType.CUSTOM_OPENAI, CustomAdapter)
    assert ProviderType.CUSTOM_OPENAI.value in reg.available_types()

    cfg = ProviderConfig(
        provider_id="my-custom",
        display_name="Custom",
        provider_type=ProviderType.CUSTOM_OPENAI,
        endpoint="http://localhost:8000/v1",
        default_model="custom-model",
    )
    adapter = reg.create(cfg)
    resp = adapter.complete(GatewayRequest(prompt="test"))
    assert resp.content == "custom"
