"""Data models and enums for MSK privacy policy enforcement."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class PolicyDecisionOutcome(StrEnum):
    """Deterministic outcome of a policy evaluation."""

    ALLOW = "ALLOW"
    REDACT = "REDACT"
    DENY = "DENY"


class ContextCategory(StrEnum):
    """Categorization of project intelligence entities for policy governance."""

    SOURCE_CODE = "source_code"
    SYMBOLS = "symbols"
    TESTS = "tests"
    DEPENDENCIES = "dependencies"
    ARCHITECTURE = "architecture"
    INFRASTRUCTURE = "infrastructure"
    SECURITY_FINDINGS = "security_findings"
    SECRETS = "secrets"
    SENSITIVE_FILES = "sensitive_files"
    DIAGNOSTICS = "diagnostics"
    USER_QUERY = "user_query"


class SensitivityLevel(StrEnum):
    """Project or context sensitivity classification."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class CategoryPolicyAction(StrEnum):
    """Configured rule action for an individual context category."""

    ALLOW = "allow"
    REDACT = "redact"
    DENY = "deny"


class PolicyReasonCode(StrEnum):
    """Machine-readable reason code for policy decision."""

    LOCAL_OPERATION_ALLOWED = "LOCAL_OPERATION_ALLOWED"
    EXTERNAL_AI_DISABLED = "EXTERNAL_AI_DISABLED"
    PROVIDER_NOT_ALLOWED = "PROVIDER_NOT_ALLOWED"
    SENSITIVE_PROJECT_BLOCKED = "SENSITIVE_PROJECT_BLOCKED"
    PROHIBITED_FILE_BLOCKED = "PROHIBITED_FILE_BLOCKED"
    PROHIBITED_CATEGORY_BLOCKED = "PROHIBITED_CATEGORY_BLOCKED"
    SECRETS_DETECTED_BLOCKED = "SECRETS_DETECTED_BLOCKED"
    REDACTION_APPLIED = "REDACTION_APPLIED"
    EXPLICITLY_APPROVED = "EXPLICITLY_APPROVED"
    POLICY_EVALUATION_ERROR = "POLICY_EVALUATION_ERROR"
    INVALID_POLICY_CONFIGURATION = "INVALID_POLICY_CONFIGURATION"


class PolicyDecision(BaseModel):
    """Structured, deterministic decision produced by policy evaluation."""

    outcome: PolicyDecisionOutcome
    reason_code: str
    explanation: str
    destination: str = "local"
    provider: str | None = None
    rule_id: str | None = None
    redactions_count: int = 0
    denied_items: list[str] = Field(default_factory=list)
    allowed_categories: list[str] = Field(default_factory=list)
    redacted_categories: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class PolicyAuditRecord(BaseModel):
    """Privacy-safe minimal audit log record of a policy decision.

    Guaranteed to never contain raw source code, secrets, credentials, or user payload.
    """

    id: str
    timestamp: str
    project_id: str
    destination: str
    provider: str | None = None
    outcome: PolicyDecisionOutcome
    reason_code: str
    rule_id: str | None = None
    categories: list[str] = Field(default_factory=list)
    item_count: int = 0
    redactions_count: int = 0
    explanation: str
