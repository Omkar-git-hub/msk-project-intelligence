"""Unit tests for MSK policy models and configuration validation."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from msk.config.loader import (
    load_policy_config,
    save_policy_config,
    validate_policy_dict,
    validate_policy_file,
)
from msk.config.models import (
    ExternalLLMPolicy,
    PolicyAction,
    PolicyConfig,
    SecurityPolicy,
)
from msk.policy.models import (
    PolicyAuditRecord,
    PolicyDecision,
    PolicyDecisionOutcome,
    PolicyReasonCode,
)

SYNTHETIC_MARKER = "FAKE_TEST_VALUE"


def test_policy_config_defaults() -> None:
    """Verify safe default settings for PolicyConfig."""
    policy = PolicyConfig()
    assert policy.version == "1.0.0"
    assert policy.project_sensitivity == "internal"
    assert policy.external_llm.enabled is False
    assert policy.external_llm.allowed_providers == ["local"]
    assert policy.security.secrets == PolicyAction.BLOCK
    assert policy.security.sensitive_files == PolicyAction.BLOCK
    assert policy.security.pii == PolicyAction.SANITIZE
    assert policy.allow_external_redaction is True
    assert policy.blocked_files == []
    assert policy.allowed_files == []


def test_policy_sensitivity_validation() -> None:
    """Test valid and invalid project sensitivity levels."""
    p_valid = PolicyConfig(project_sensitivity="restricted")
    assert p_valid.project_sensitivity == "restricted"

    with pytest.raises(ValidationError) as exc_info:
        PolicyConfig(project_sensitivity="top_secret_invalid")
    assert "Invalid project_sensitivity" in str(exc_info.value)


def test_policy_regex_pattern_validation() -> None:
    """Test that malformed regex patterns in configuration are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        SecurityPolicy(custom_sensitive_patterns=["[unclosed_bracket"])
    assert "Invalid regex pattern" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info2:
        PolicyConfig(blocked_files=["(invalid_group"])
    assert "Invalid file pattern regex" in str(exc_info2.value)


def test_external_llm_provider_validation() -> None:
    """Test allowed provider list validation."""
    llm = ExternalLLMPolicy(allowed_providers=["", "   "])
    assert llm.allowed_providers == ["local"]

    llm2 = ExternalLLMPolicy(allowed_providers=["mock-provider", "test-gateway"])
    assert llm2.allowed_providers == ["mock-provider", "test-gateway"]


def test_policy_decision_model() -> None:
    """Test PolicyDecision model attributes."""
    decision = PolicyDecision(
        outcome=PolicyDecisionOutcome.DENY,
        reason_code=PolicyReasonCode.EXTERNAL_AI_DISABLED,
        explanation="External AI is disabled.",
        destination="external",
        provider="mock-ai",
        denied_items=["blocked_file:config.json"],
    )
    assert decision.outcome == PolicyDecisionOutcome.DENY
    assert decision.reason_code == "EXTERNAL_AI_DISABLED"
    assert "mock-ai" in str(decision.provider)
    assert len(decision.denied_items) == 1


def test_policy_audit_record_structure() -> None:
    """Test minimal non-sensitive audit record structure."""
    record = PolicyAuditRecord(
        id="audit_12345",
        timestamp="2026-10-26T12:00:00Z",
        project_id="test_proj",
        destination="external",
        provider="mock-ai",
        outcome=PolicyDecisionOutcome.ALLOW,
        reason_code=PolicyReasonCode.EXPLICITLY_APPROVED,
        categories=["source_code", "dependencies"],
        item_count=5,
        redactions_count=0,
        explanation="Allowed by rule.",
    )
    assert record.id == "audit_12345"
    assert record.outcome == PolicyDecisionOutcome.ALLOW
    assert "source_code" in record.categories
    # Confirm raw code payload attributes do not exist on the audit model
    assert not hasattr(record, "snippet")
    assert not hasattr(record, "code")
    assert not hasattr(record, "secret")


def test_validate_policy_dict() -> None:
    """Test validation of raw policy dictionary."""
    valid_data = {
        "version": "1.0.0",
        "project_sensitivity": "confidential",
        "external_llm": {
            "enabled": True,
            "allowed_providers": ["mock-ai"],
        },
    }
    is_valid, errors = validate_policy_dict(valid_data)
    assert is_valid is True
    assert len(errors) == 0

    invalid_data = {
        "project_sensitivity": "unknown_value",
        "security": {
            "secrets": "invalid_action_value",
        },
    }
    is_valid, errors = validate_policy_dict(invalid_data)
    assert is_valid is False
    assert len(errors) >= 1


def test_validate_policy_file(tmp_path: Path) -> None:
    """Test validation of policy files."""
    pol_file = tmp_path / "policy.json"
    pol_file.write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "project_sensitivity": "internal",
            }
        ),
        encoding="utf-8",
    )
    is_valid, errors = validate_policy_file(pol_file)
    assert is_valid is True
    assert errors == []

    # Non-existent file
    is_valid, errors = validate_policy_file(tmp_path / "missing.json")
    assert is_valid is False
    assert "not found" in errors[0]

    # Corrupt JSON
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("{ broken json: true", encoding="utf-8")
    is_valid, errors = validate_policy_file(bad_file)
    assert is_valid is False
    assert "Invalid JSON" in errors[0]


def test_load_and_save_policy_config(tmp_path: Path) -> None:
    """Test loading and saving policy configuration in project directory."""
    # When missing, returns safe default
    default_policy = load_policy_config(tmp_path)
    assert default_policy.external_llm.enabled is False

    # Save customized policy
    custom_policy = PolicyConfig(
        project_sensitivity="confidential",
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["custom-llm"]),
        category_rules={"diagnostics": PolicyAction.BLOCK},
    )
    save_policy_config(tmp_path, custom_policy)

    # Reload
    loaded = load_policy_config(tmp_path)
    assert loaded.project_sensitivity == "confidential"
    assert loaded.external_llm.enabled is True
    assert "custom-llm" in loaded.external_llm.allowed_providers
    assert loaded.category_rules.get("diagnostics") == PolicyAction.BLOCK
