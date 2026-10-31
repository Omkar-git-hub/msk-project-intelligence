"""Pluggable AI Gateway and Provider Management for MSK."""

from msk.gateway.base import AIGatewayAdapter, BaseGatewayAdapter
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
from msk.gateway.registry import (
    AIGatewayRegistry,
    MockGatewayAdapter,
    default_registry,
    get_default_registry,
)

__all__ = [
    "AIGatewayAdapter",
    "AIGatewayRegistry",
    "AuthType",
    "BaseGatewayAdapter",
    "GatewayRequest",
    "GatewayResponse",
    "MockGatewayAdapter",
    "ModelCapability",
    "ModelConfig",
    "ProviderConfig",
    "ProviderType",
    "ProvidersFileConfig",
    "default_registry",
    "get_default_registry",
]
