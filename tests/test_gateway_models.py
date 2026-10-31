"""Unit tests for AI gateway and provider configuration data models."""

import pytest
from pydantic import ValidationError

from msk.gateway.models import (
    AuthType,
    GatewayRequest,
    GatewayResponse,
    ModelCapability,
    ModelConfig,
    ProviderConfig,
    ProvidersFileConfig,
    ProviderType,
)


def test_provider_type_and_auth_type_values():
    """Verify supported enum values for providers and authentication."""
    assert ProviderType.MOCK == "mock"
    assert ProviderType.OLLAMA == "ollama"
    assert ProviderType.LOCAL_OPENAI == "local_openai"
    assert ProviderType.OPENAI == "openai"
    assert ProviderType.ANTHROPIC == "anthropic"
    assert ProviderType.GEMINI == "gemini"

    assert AuthType.NONE == "none"
    assert AuthType.BEARER == "bearer"
    assert AuthType.API_KEY == "api_key"


def test_model_config_validation():
    """Verify ModelConfig validation logic."""
    model = ModelConfig(id="llama3:8b", display_name="Llama 3 8B", context_window=8192)
    assert model.id == "llama3:8b"
    assert model.context_window == 8192
    assert ModelCapability.CHAT in model.capabilities

    with pytest.raises(ValidationError):
        ModelConfig(id="   ")


def test_provider_config_validation():
    """Verify ProviderConfig validation rules."""
    provider = ProviderConfig(
        provider_id="my-ollama",
        display_name="Local Ollama",
        provider_type=ProviderType.OLLAMA,
        endpoint="http://localhost:11434/",
        default_model="llama3",
        models=[ModelConfig(id="llama3")],
        auth_type=AuthType.NONE,
        timeout_seconds=45.0,
    )
    assert provider.provider_id == "my-ollama"
    # Trailing slash stripped
    assert provider.endpoint == "http://localhost:11434"
    assert provider.timeout_seconds == 45.0


def test_provider_config_invalid_id():
    """Verify invalid provider_id characters raise ValidationError."""
    with pytest.raises(ValidationError):
        ProviderConfig(
            provider_id="invalid id with spaces!",
            display_name="Invalid",
            provider_type=ProviderType.MOCK,
            endpoint="http://localhost",
            default_model="m1",
        )


def test_provider_config_invalid_endpoint():
    """Verify invalid endpoints (missing scheme) raise ValidationError."""
    with pytest.raises(ValidationError):
        ProviderConfig(
            provider_id="test",
            display_name="Test",
            provider_type=ProviderType.MOCK,
            endpoint="ftp://localhost",
            default_model="m1",
        )


def test_provider_config_timeout_bounds():
    """Verify timeout bounds enforcement."""
    with pytest.raises(ValidationError):
        ProviderConfig(
            provider_id="test",
            display_name="Test",
            provider_type=ProviderType.MOCK,
            endpoint="http://localhost",
            default_model="m1",
            timeout_seconds=0.5,
        )

    with pytest.raises(ValidationError):
        ProviderConfig(
            provider_id="test",
            display_name="Test",
            provider_type=ProviderType.MOCK,
            endpoint="http://localhost",
            default_model="m1",
            timeout_seconds=1000.0,
        )


def test_providers_file_config():
    """Verify ProvidersFileConfig schema and versioning."""
    cfg = ProvidersFileConfig(
        version="1.0.0",
        default_provider="mock",
        providers=[],
    )
    assert cfg.version == "1.0.0"
    assert cfg.default_provider == "mock"

    with pytest.raises(ValidationError):
        ProvidersFileConfig(version="not-semver")


def test_gateway_request_and_response():
    """Verify GatewayRequest and GatewayResponse serialization."""
    req = GatewayRequest(
        prompt="Explain auth",
        system_prompt="You are an expert",
        model="gpt-4o",
        temperature=0.7,
    )
    assert req.prompt == "Explain auth"
    assert req.temperature == 0.7

    resp = GatewayResponse(
        content="Authentication uses JWT tokens.",
        provider="openai",
        model="gpt-4o",
        latency_ms=350.5,
        tokens_used=120,
    )
    assert resp.content == "Authentication uses JWT tokens."
    assert resp.latency_ms == 350.5
    assert resp.tokens_used == 120
