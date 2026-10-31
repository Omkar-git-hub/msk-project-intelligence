"""Data models for MSK Pluggable AI Gateway and Provider Management."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ProviderType(StrEnum):
    """Supported AI provider types."""

    MOCK = "mock"
    OLLAMA = "ollama"
    LOCAL_OPENAI = "local_openai"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    CUSTOM_OPENAI = "custom_openai"


class AuthType(StrEnum):
    """Authentication methods for AI providers."""

    NONE = "none"
    BEARER = "bearer"
    API_KEY = "api_key"
    HEADER = "header"


class ModelCapability(StrEnum):
    """Capabilities supported by models."""

    CHAT = "chat"
    COMPLETION = "completion"
    STREAMING = "streaming"
    JSON_OUTPUT = "json_output"


class ModelConfig(BaseModel):
    """Configuration for a single model supported by a provider."""

    id: str
    display_name: str | None = None
    context_window: int = 8192
    supports_streaming: bool = True
    capabilities: list[ModelCapability] = Field(default_factory=lambda: [ModelCapability.CHAT])

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("Model id cannot be empty.")
        return cleaned


class ProviderConfig(BaseModel):
    """Configuration for an AI provider."""

    provider_id: str
    display_name: str
    provider_type: ProviderType
    endpoint: str
    default_model: str
    models: list[ModelConfig] = Field(default_factory=list)
    auth_type: AuthType = AuthType.BEARER
    credential_ref: str | None = None
    enabled: bool = True
    is_default: bool = False
    timeout_seconds: float = 30.0
    custom_headers: dict[str, str] = Field(default_factory=dict)
    extra_settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider_id")
    @classmethod
    def validate_provider_id(cls, v: str) -> str:
        cleaned = v.strip().lower()
        if not cleaned:
            raise ValueError("Provider ID cannot be empty.")
        for ch in cleaned:
            if not (ch.isalnum() or ch in ("-", "_")):
                raise ValueError(f"Invalid character '{ch}' in provider_id. Must be alphanumeric or hyphens/underscores.")
        return cleaned

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        cleaned = v.strip()
        if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
            raise ValueError(f"Endpoint URL must start with http:// or https://: '{v}'")
        return cleaned.rstrip("/")

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v: float) -> float:
        if v < 1.0 or v > 600.0:
            raise ValueError("timeout_seconds must be between 1.0 and 600.0 seconds.")
        return v


class ProvidersFileConfig(BaseModel):
    """Schema for the .msk/config/providers.json file."""

    version: str = "1.0.0"
    default_provider: str = "mock"
    providers: list[ProviderConfig] = Field(default_factory=list)

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        parts = v.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError(f"Version must be semver formatted, got '{v}'")
        return v


class GatewayRequest(BaseModel):
    """Standardized inference request sent to an AI adapter."""

    prompt: str
    system_prompt: str | None = None
    model: str | None = None
    temperature: float = 0.2
    max_tokens: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GatewayResponse(BaseModel):
    """Standardized inference response returned by an AI adapter."""

    content: str
    provider: str
    model: str
    latency_ms: float = 0.0
    tokens_used: int | None = None
    finish_reason: str = "stop"
    raw_response: dict[str, Any] | None = None
