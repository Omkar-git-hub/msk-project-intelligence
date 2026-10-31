"""Factory and registry for pluggable AI gateway adapters."""

import logging
from typing import Any

from msk.common.errors import MSKError
from msk.gateway.base import AIGatewayAdapter, BaseGatewayAdapter
from msk.gateway.models import GatewayRequest, GatewayResponse, ProviderConfig, ProviderType

logger = logging.getLogger("msk.gateway.registry")


class MockGatewayAdapter(BaseGatewayAdapter):
    """Offline, deterministic mock adapter for testing and air-gapped environments."""

    def __init__(
        self,
        config: ProviderConfig | None = None,
        api_key: str | None = None,
        canned_response: str | None = None,
    ) -> None:
        default_cfg = config or ProviderConfig(
            provider_id="mock",
            display_name="Mock Offline Provider",
            provider_type=ProviderType.MOCK,
            endpoint="http://localhost:0",
            default_model="mock-model",
            enabled=True,
            is_default=True,
        )
        super().__init__(default_cfg, api_key=api_key)
        self.canned_response = canned_response
        self.call_count: int = 0
        self.last_request: GatewayRequest | None = None

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        self.call_count += 1
        self.last_request = request
        content = self.canned_response or (
            "Offline local simulation response. The project stays private. AI gets only what it needs."
        )
        return GatewayResponse(
            content=content,
            provider=self.provider_name,
            model=request.model or self.config.default_model,
            latency_ms=5.0,
            tokens_used=42,
            finish_reason="stop",
        )

    def check_health(self) -> dict[str, Any]:
        return {
            "status": "healthy",
            "provider_id": self.provider_name,
            "endpoint": "local://mock",
            "latency_ms": 0.5,
        }


class AIGatewayRegistry:
    """Registry mapping ProviderType to concrete adapter implementations."""

    def __init__(self) -> None:
        self._adapters: dict[ProviderType, type[AIGatewayAdapter]] = {}
        # Register default mock adapter
        self.register(ProviderType.MOCK, MockGatewayAdapter)
        self._register_local_adapters()
        self._register_cloud_adapters()

    def _register_local_adapters(self) -> None:
        """Register local inference adapters."""
        try:
            from msk.gateway.local import LocalOpenAIAdapter, OllamaAdapter

            self.register(ProviderType.OLLAMA, OllamaAdapter)
            self.register(ProviderType.LOCAL_OPENAI, LocalOpenAIAdapter)
            self.register(ProviderType.CUSTOM_OPENAI, LocalOpenAIAdapter)
        except ImportError as exc:
            logger.debug("Could not auto-register local adapters: %s", exc)

    def _register_cloud_adapters(self) -> None:
        """Register cloud inference adapters."""
        try:
            from msk.gateway.cloud import AnthropicAdapter, GeminiAdapter, OpenAIAdapter

            self.register(ProviderType.OPENAI, OpenAIAdapter)
            self.register(ProviderType.ANTHROPIC, AnthropicAdapter)
            self.register(ProviderType.GEMINI, GeminiAdapter)
        except ImportError as exc:
            logger.debug("Could not auto-register cloud adapters: %s", exc)

    def register(self, provider_type: ProviderType, adapter_cls: type[AIGatewayAdapter]) -> None:
        """Register an adapter class for a provider type."""
        self._adapters[provider_type] = adapter_cls
        logger.debug("Registered gateway adapter for provider type: %s", provider_type)

    def get_adapter_class(self, provider_type: ProviderType) -> type[AIGatewayAdapter] | None:
        """Retrieve registered adapter class for a provider type."""
        return self._adapters.get(provider_type)

    def create(self, config: ProviderConfig, api_key: str | None = None) -> AIGatewayAdapter:
        """Instantiate an adapter instance for the given provider configuration."""
        adapter_cls = self._adapters.get(config.provider_type)
        if adapter_cls is None:
            raise MSKError(
                f"Unsupported AI provider type: '{config.provider_type}' for provider '{config.provider_id}'.",
                hint=f"Available provider types: {', '.join(sorted(t.value for t in self._adapters))}",
            )
        try:
            return adapter_cls(config=config, api_key=api_key)  # type: ignore[call-arg]
        except Exception as exc:
            raise MSKError(
                f"Failed to instantiate AI gateway adapter for '{config.provider_id}': {exc}"
            ) from exc

    def available_types(self) -> list[str]:
        """Return list of all registered provider type values."""
        return sorted(t.value for t in self._adapters)


# Global default registry instance
default_registry = AIGatewayRegistry()


def get_default_registry() -> AIGatewayRegistry:
    """Access the global default AIGatewayRegistry."""
    return default_registry
