"""Configuration models for MSK projects and privacy policies."""

import re
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("allowed_providers")
    @classmethod
    def validate_providers(cls, v: list[str]) -> list[str]:
        cleaned = [p.strip() for p in v if p and p.strip()]
        if not cleaned:
            return ["local"]
        return cleaned


class SecurityPolicy(BaseModel):
    """Security and data classification policies."""

    secrets: PolicyAction = PolicyAction.BLOCK
    pii: PolicyAction = PolicyAction.SANITIZE
    sensitive_files: PolicyAction = PolicyAction.BLOCK
    custom_sensitive_patterns: list[str] = Field(default_factory=list)

    @field_validator("custom_sensitive_patterns")
    @classmethod
    def validate_regex_patterns(cls, patterns: list[str]) -> list[str]:
        for pat in patterns:
            try:
                re.compile(pat)
            except re.error as exc:
                raise ValueError(f"Invalid regex pattern in custom_sensitive_patterns '{pat}': {exc}") from exc
        return patterns


class PolicyConfig(BaseModel):
    """Overall project privacy and security policy."""

    version: str = "1.0.0"
    project_sensitivity: str = "internal"
    external_llm: ExternalLLMPolicy = Field(default_factory=ExternalLLMPolicy)
    security: SecurityPolicy = Field(default_factory=SecurityPolicy)
    category_rules: dict[str, PolicyAction] = Field(default_factory=dict)
    blocked_files: list[str] = Field(default_factory=list)
    allowed_files: list[str] = Field(default_factory=list)
    allow_external_redaction: bool = True

    @field_validator("project_sensitivity")
    @classmethod
    def validate_sensitivity(cls, v: str) -> str:
        valid = {"public", "internal", "confidential", "restricted"}
        normalized = v.lower().strip()
        if normalized not in valid:
            raise ValueError(f"Invalid project_sensitivity '{v}'. Must be one of: {', '.join(sorted(valid))}")
        return normalized

    @field_validator("blocked_files", "allowed_files")
    @classmethod
    def validate_file_patterns(cls, patterns: list[str]) -> list[str]:
        for pat in patterns:
            try:
                re.compile(pat)
            except re.error as exc:
                raise ValueError(f"Invalid file pattern regex '{pat}': {exc}") from exc
        return patterns


class ProjectConfig(BaseModel):
    """Metadata descriptor saved in .msk/project.json."""

    id: str
    name: str
    root: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    version: str = "0.1.0"
