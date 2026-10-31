"""Base gateway adapter interface and abstractions."""

import time
from abc import ABC, abstractmethod
from typing import Any, Protocol, runtime_checkable

from msk.gateway.models import GatewayRequest, GatewayResponse, ProviderConfig


@runtime_checkable
class AIGatewayAdapter(Protocol):
    """Protocol interface for pluggable AI/LLM backends."""

    provider_name: str

    def send_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Transmit context payload to the AI provider and return the response."""
        ...

    def complete(self, request: GatewayRequest) -> GatewayResponse:
        """Execute a structured completion request against this provider."""
        ...

    def check_health(self) -> dict[str, Any]:
        """Perform a quick health/availability check against the provider endpoint."""
        ...


class BaseGatewayAdapter(ABC):
    """Abstract base class providing standard context translation and error handling."""

    def __init__(self, config: ProviderConfig, api_key: str | None = None) -> None:
        self.config = config
        self.api_key = api_key
        self.provider_name = config.provider_id

    @abstractmethod
    def complete(self, request: GatewayRequest) -> GatewayResponse:
        """Execute a completion request against the provider."""
        ...

    def send_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Bridge between PolicyGatedGateway context payloads and complete()."""
        llm_context = payload.get("llm_context", "")
        question = payload.get("question") or payload.get("query")
        if not question and "items" in payload:
            question = f"Analyze the project context with {len(payload['items'])} items."

        full_prompt = f"{llm_context}\n\nDeveloper Query:\n{question}" if llm_context else (question or "")
        system_prompt = (
            "You are MSK AI assistant. You answer software engineering questions grounded strictly "
            "in the provided project context. Do not hallucinate files or functions not in the context. "
            "Always respect privacy and security constraints."
        )

        req = GatewayRequest(
            prompt=full_prompt,
            system_prompt=system_prompt,
            model=self.config.default_model,
            metadata=payload,
        )

        resp = self.complete(req)

        return {
            "status": "success",
            "provider": resp.provider,
            "model": resp.model,
            "content": resp.content,
            "latency_ms": resp.latency_ms,
            "tokens_used": resp.tokens_used,
            "finish_reason": resp.finish_reason,
            "destination": payload.get("destination", "local"),
            "items_received": len(payload.get("items", [])),
            "simulated_response": resp.content,
        }

    def check_health(self) -> dict[str, Any]:
        """Default health check measuring basic configuration readiness."""
        t0 = time.perf_counter()
        return {
            "status": "healthy" if self.config.enabled else "disabled",
            "provider_id": self.config.provider_id,
            "endpoint": self.config.endpoint,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
