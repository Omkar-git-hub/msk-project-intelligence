"""Tests for pluggable AI Gateway and PolicyGatedGateway transmission gate."""

from pathlib import Path

from msk.config.models import ExternalLLMPolicy, PolicyAction, PolicyConfig, SecurityPolicy
from msk.context.models import (
    BudgetMetrics,
    ContextItem,
    ContextResult,
    PrivacySummary,
)
from msk.policy.audit import PolicyAuditor
from msk.policy.boundary import PolicyEnforcementBoundary
from msk.policy.gateway import MockAIGateway, PolicyGatedGateway
from msk.policy.models import PolicyDecisionOutcome, PolicyReasonCode
from msk.storage.database import DatabaseManager


def _create_sample_context_result() -> ContextResult:
    """Create a test ContextResult with sample code items and relationships."""
    item = ContextItem(
        id="sym:1",
        name="calculate_total",
        item_type="symbol",
        file_path="src/billing.py",
        relevance_score=100.0,
        reasons=["Direct match"],
        snippet="def calculate_total(items):\n    return sum(i.price for i in items)\n",
        estimated_tokens=15,
    )
    return ContextResult(
        project_name="BillingApp",
        analyzed_at="2026-10-27T00:00:00Z",
        request_summary={"question": "How is billing total calculated?"},
        budget_metrics=BudgetMetrics(
            token_budget=2000,
            estimated_tokens_used=15,
            utilization_pct=0.75,
            total_candidates=1,
            selected_count=1,
            omitted_count=0,
        ),
        privacy_summary=PrivacySummary(local_only=True),
        selected_items=[item],
        llm_context="# Project Context\ndef calculate_total(items): ...",
    )


def test_mock_ai_gateway_offline_operation():
    """Verify MockAIGateway functions entirely offline and records payloads without network."""
    gateway = MockAIGateway()
    payload = {"destination": "local", "items": [{"name": "item1"}]}

    response = gateway.send_context(payload)

    assert gateway.call_count == 1
    assert gateway.last_payload == payload
    assert response["status"] == "success"
    assert response["items_received"] == 1
    assert "The project stays private." in response["simulated_response"]


def test_policy_gated_gateway_blocks_when_external_ai_disabled(tmp_path: Path):
    """Verify PolicyGatedGateway prevents transmission when external AI is disabled."""
    mock_adapter = MockAIGateway()
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=False),
    )
    boundary = PolicyEnforcementBoundary(policy=policy)
    gateway = PolicyGatedGateway(adapter=mock_adapter, boundary=boundary)

    context = _create_sample_context_result()
    response, decision = gateway.transmit(context, destination="external", provider="openai")

    # Gateway adapter must NEVER be invoked on DENY
    assert response is None
    assert decision.outcome == PolicyDecisionOutcome.DENY
    assert decision.reason_code == PolicyReasonCode.EXTERNAL_AI_DISABLED
    assert mock_adapter.call_count == 0
    assert mock_adapter.last_payload is None


def test_policy_gated_gateway_blocks_unauthorized_provider(tmp_path: Path):
    """Verify PolicyGatedGateway blocks transmissions to unapproved providers."""
    mock_adapter = MockAIGateway()
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["anthropic"]),
    )
    boundary = PolicyEnforcementBoundary(policy=policy)
    gateway = PolicyGatedGateway(adapter=mock_adapter, boundary=boundary)

    context = _create_sample_context_result()
    response, decision = gateway.transmit(context, destination="external", provider="openai")

    assert response is None
    assert decision.outcome == PolicyDecisionOutcome.DENY
    assert decision.reason_code == PolicyReasonCode.PROVIDER_NOT_ALLOWED
    assert mock_adapter.call_count == 0


def test_policy_gated_gateway_allows_approved_transmission():
    """Verify PolicyGatedGateway transmits approved context to adapter and returns response."""
    mock_adapter = MockAIGateway()
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["openai"]),
    )
    boundary = PolicyEnforcementBoundary(policy=policy)
    gateway = PolicyGatedGateway(adapter=mock_adapter, boundary=boundary)

    context = _create_sample_context_result()
    response, decision = gateway.transmit(context, destination="external", provider="openai")

    assert response is not None
    assert decision.outcome == PolicyDecisionOutcome.ALLOW
    assert mock_adapter.call_count == 1
    assert mock_adapter.last_payload["destination"] == "external"
    assert mock_adapter.last_payload["provider"] == "openai"
    assert len(mock_adapter.last_payload["items"]) == 1


def test_policy_gated_gateway_redacts_and_forwards_sanitized_payload():
    """Verify PolicyGatedGateway redacts sensitive items and transmits sanitized payload."""
    mock_adapter = MockAIGateway()
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["openai"]),
        security=SecurityPolicy(secrets=PolicyAction.SANITIZE),
        allow_external_redaction=True,
    )
    boundary = PolicyEnforcementBoundary(policy=policy)
    gateway = PolicyGatedGateway(adapter=mock_adapter, boundary=boundary)

    context = _create_sample_context_result()
    # Add synthetic sensitive marker
    context.privacy_summary.redactions_count = 1
    context.request_summary["question"] = "Query with FAKE_TEST_VALUE_REDACTED"

    response, decision = gateway.transmit(context, destination="external", provider="openai")

    assert response is not None
    assert decision.outcome == PolicyDecisionOutcome.REDACT
    assert mock_adapter.call_count == 1
    assert mock_adapter.last_payload is not None


def test_policy_gated_gateway_records_audit_event(tmp_path: Path):
    """Verify transmission attempts through PolicyGatedGateway are recorded in the audit log."""
    db_path = tmp_path / "msk.db"
    db = DatabaseManager(db_path)

    auditor = PolicyAuditor(db=db, project_root=tmp_path)
    mock_adapter = MockAIGateway()

    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=False),
    )
    boundary = PolicyEnforcementBoundary(policy=policy, auditor=auditor, project_id="audit_test")
    gateway = PolicyGatedGateway(adapter=mock_adapter, boundary=boundary)

    context = _create_sample_context_result()
    gateway.transmit(context, destination="external", provider="openai")

    records = auditor.get_recent_records(limit=10)
    assert len(records) == 1
    assert records[0].outcome == PolicyDecisionOutcome.DENY
    assert records[0].destination == "external"
    assert records[0].provider == "openai"
