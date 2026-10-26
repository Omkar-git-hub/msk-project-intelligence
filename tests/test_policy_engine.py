"""Unit tests for MSK deterministic policy evaluation engine."""


from msk.config.models import ExternalLLMPolicy, PolicyAction, PolicyConfig, SecurityPolicy
from msk.policy.evaluator import PolicyEvaluator
from msk.policy.models import (
    ContextCategory,
    PolicyDecisionOutcome,
    PolicyReasonCode,
)

SYNTHETIC_TEST_MARKER = "FAKE_TEST_VALUE"
EXAMPLE_REDACTION_VALUE = "EXAMPLE_REDACTION_VALUE"


def test_local_only_operation_allowed() -> None:
    """Test that local destination requests are permitted under local-first policy."""
    policy = PolicyConfig()
    evaluator = PolicyEvaluator(policy)

    decision = evaluator.evaluate_request(destination="local")
    assert decision.outcome == PolicyDecisionOutcome.ALLOW
    assert decision.reason_code == PolicyReasonCode.LOCAL_OPERATION_ALLOWED
    assert decision.destination == "local"


def test_local_operation_with_redactions() -> None:
    """Test that local operation with detected secret redactions produces REDACT outcome."""
    policy = PolicyConfig()
    evaluator = PolicyEvaluator(policy)

    decision = evaluator.evaluate_request(
        destination="local",
        redactions_count=2,
    )
    assert decision.outcome == PolicyDecisionOutcome.REDACT
    assert decision.reason_code == PolicyReasonCode.LOCAL_OPERATION_ALLOWED
    assert decision.redactions_count == 2


def test_external_sharing_disabled_by_default() -> None:
    """Test that external destination requests are DENIED by default."""
    policy = PolicyConfig()  # external_llm.enabled is False by default
    evaluator = PolicyEvaluator(policy)

    decision = evaluator.evaluate_request(destination="external")
    assert decision.outcome == PolicyDecisionOutcome.DENY
    assert decision.reason_code == PolicyReasonCode.EXTERNAL_AI_DISABLED
    assert "disabled by policy" in decision.explanation


def test_restricted_project_blocks_external_sharing() -> None:
    """Test that project marked restricted blocks external sharing even if external LLM is enabled."""
    policy = PolicyConfig(
        project_sensitivity="restricted",
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["approved-ai"]),
    )
    evaluator = PolicyEvaluator(policy)

    decision = evaluator.evaluate_request(
        destination="external",
        provider="approved-ai",
    )
    assert decision.outcome == PolicyDecisionOutcome.DENY
    assert decision.reason_code == PolicyReasonCode.SENSITIVE_PROJECT_BLOCKED


def test_unapproved_provider_denied() -> None:
    """Test that unapproved external AI provider is denied."""
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(
            enabled=True,
            allowed_providers=["approved-partner"],
        )
    )
    evaluator = PolicyEvaluator(policy)

    decision = evaluator.evaluate_request(
        destination="external",
        provider="unapproved-rogue-ai",
    )
    assert decision.outcome == PolicyDecisionOutcome.DENY
    assert decision.reason_code == PolicyReasonCode.PROVIDER_NOT_ALLOWED
    assert "unapproved-rogue-ai" in decision.explanation


def test_approved_external_sharing_allowed() -> None:
    """Test that approved external sharing evaluates to ALLOW for benign content."""
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(
            enabled=True,
            allowed_providers=["mock-ai"],
        )
    )
    evaluator = PolicyEvaluator(policy)

    decision = evaluator.evaluate_request(
        destination="external",
        provider="mock-ai",
        categories=[ContextCategory.DEPENDENCIES.value, ContextCategory.ARCHITECTURE.value],
        file_paths=["src/utils/math.py"],
    )
    assert decision.outcome == PolicyDecisionOutcome.ALLOW
    assert decision.reason_code == PolicyReasonCode.EXPLICITLY_APPROVED


def test_sensitive_file_blocked_before_external_transmission() -> None:
    """Test that sensitive files like .env or keys cause external DENY."""
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["mock-ai"]),
        security=SecurityPolicy(sensitive_files=PolicyAction.BLOCK),
    )
    evaluator = PolicyEvaluator(policy)

    decision = evaluator.evaluate_request(
        destination="external",
        provider="mock-ai",
        file_paths=["src/config.py", ".env.production"],
    )
    assert decision.outcome == PolicyDecisionOutcome.DENY
    assert decision.reason_code == PolicyReasonCode.PROHIBITED_FILE_BLOCKED
    assert any(".env.production" in item for item in decision.denied_items)


def test_blocked_files_custom_pattern() -> None:
    """Test that explicit blocked_files patterns are enforced."""
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["mock-ai"]),
        blocked_files=[r".*secret_module.*\.py$"],
    )
    evaluator = PolicyEvaluator(policy)

    decision = evaluator.evaluate_request(
        destination="external",
        provider="mock-ai",
        file_paths=["src/secret_module_internal.py"],
    )
    assert decision.outcome == PolicyDecisionOutcome.DENY
    assert decision.reason_code == PolicyReasonCode.PROHIBITED_FILE_BLOCKED


def test_secrets_detected_denied() -> None:
    """Test that detected secrets cause DENY when security.secrets is BLOCK."""
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["mock-ai"]),
        security=SecurityPolicy(secrets=PolicyAction.BLOCK),
    )
    evaluator = PolicyEvaluator(policy)

    decision = evaluator.evaluate_request(
        destination="external",
        provider="mock-ai",
        has_secrets=True,
    )
    assert decision.outcome == PolicyDecisionOutcome.DENY
    assert decision.reason_code == PolicyReasonCode.SECRETS_DETECTED_BLOCKED


def test_secrets_redaction_when_configured() -> None:
    """Test that secrets cause REDACT outcome when security.secrets is SANITIZE."""
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["mock-ai"]),
        security=SecurityPolicy(secrets=PolicyAction.SANITIZE),
        allow_external_redaction=True,
    )
    evaluator = PolicyEvaluator(policy)

    decision = evaluator.evaluate_request(
        destination="external",
        provider="mock-ai",
        has_secrets=True,
        redactions_count=1,
    )
    assert decision.outcome == PolicyDecisionOutcome.REDACT
    assert decision.reason_code == PolicyReasonCode.REDACTION_APPLIED
    assert ContextCategory.SECRETS.value in decision.redacted_categories


def test_category_rule_precedence_deny_over_redact_and_allow() -> None:
    """Test strict precedence: DENY > REDACT > ALLOW across multiple matching rules."""
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["mock-ai"]),
        category_rules={
            "source_code": PolicyAction.ALLOW,
            "diagnostics": PolicyAction.SANITIZE,
            "infrastructure": PolicyAction.BLOCK,
        },
    )
    evaluator = PolicyEvaluator(policy)

    # 1. ALLOW + REDACT -> REDACT
    d1 = evaluator.evaluate_request(
        destination="external",
        provider="mock-ai",
        categories=["source_code", "diagnostics"],
    )
    assert d1.outcome == PolicyDecisionOutcome.REDACT

    # 2. ALLOW + REDACT + BLOCK -> DENY (DENY wins over REDACT and ALLOW)
    d2 = evaluator.evaluate_request(
        destination="external",
        provider="mock-ai",
        categories=["source_code", "diagnostics", "infrastructure"],
    )
    assert d2.outcome == PolicyDecisionOutcome.DENY
    assert d2.reason_code == PolicyReasonCode.PROHIBITED_CATEGORY_BLOCKED


def test_evaluator_fails_closed_on_unexpected_error() -> None:
    """Test that unexpected evaluation failures fail closed to DENY."""
    evaluator = PolicyEvaluator(PolicyConfig())

    # Trigger internal exception by injecting broken internal attribute
    evaluator.policy = None  # type: ignore[assignment]

    decision = evaluator.evaluate_request(destination="external")
    assert decision.outcome == PolicyDecisionOutcome.DENY
    assert decision.reason_code == PolicyReasonCode.POLICY_EVALUATION_ERROR
    assert "Failing closed" in decision.explanation
