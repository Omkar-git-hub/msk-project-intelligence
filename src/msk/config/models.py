"""Configuration models for MSK projects and privacy policies."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class PolicyAction(StrEnum):
    """Allowed policy actions."""

    ALLOW = "allow"
    BLOCK = "block"
    SANITIZE = "sanitize"
    LOCAL_ONLY = "local_only"


class ExternalLLMPolicy(BaseModel):
    """Policy rules governing external AI/LLM connectivity."""

    enabled: bool = False
    allowed_providers: list[str] = Field(default_factory=lambda: ["local"])
    enforce_strict_redaction: bool = True


class SecurityPolicy(BaseModel):
    """Security and data classification policies."""

    secrets: PolicyAction = PolicyAction.BLOCK
    pii: PolicyAction = PolicyAction.SANITIZE
    sensitive_files: PolicyAction = PolicyAction.BLOCK
    custom_sensitive_patterns: list[str] = Field(default_factory=list)


class PolicyConfig(BaseModel):
    """Overall project privacy and security policy."""

    version: str = "1.0.0"
    external_llm: ExternalLLMPolicy = Field(default_factory=ExternalLLMPolicy)
    security: SecurityPolicy = Field(default_factory=SecurityPolicy)


class ProjectConfig(BaseModel):
    """Metadata descriptor saved in .msk/project.json."""

    id: str
    name: str
    root: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    version: str = "0.1.0"
