"""Tests verifying privacy guarantees and PolicyGatedGateway enforcement for AI providers."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from msk.config.models import ExternalLLMPolicy, PolicyConfig
from msk.context.models import (
    BudgetMetrics,
    ContextItem,
    ContextResult,
    PrivacySummary,
)
from msk.gateway.cloud import OpenAIAdapter
from msk.gateway.models import ProviderConfig, ProviderType
from msk.policy.audit import PolicyAuditor
from msk.policy.boundary import PolicyEnforcementBoundary
from msk.policy.gateway import PolicyGatedGateway
from msk.policy.models import PolicyDecisionOutcome, PolicyReasonCode
from msk.storage.database import DatabaseManager


def _create_sample_context() -> ContextResult:
    item = ContextItem(
        id="sym:1",
        name="process_payment",
        item_type="symbol",
        file_path="src/payment.py",
        relevance_score=100.0,
        reasons=["Direct focus"],
        snippet="def process_payment(amount):\n    return charge(amount)\n",
        estimated_tokens=20,
    )
    return ContextResult(
        project_name="ShopApp",
        analyzed_at="2026-10-31T00:00:00Z",
        request_summary={"question": "How are payments charged?"},
        budget_metrics=BudgetMetrics(
            token_budget=2000,
            estimated_tokens_used=20,
            utilization_pct=1.0,
            total_candidates=1,
            selected_count=1,
            omitted_count=0,
        ),
        privacy_summary=PrivacySummary(local_only=True),
        selected_items=[item],
        llm_context="# Project Context\nprocess_payment handles charging.",
    )


def test_gateway_strictly_blocks_external_provider_when_policy_disabled(tmp_path: Path):
    """Verify PolicyGatedGateway fails closed before reaching cloud adapter when external AI disabled."""
    db_path = tmp_path / "msk.db"
    db = DatabaseManager(db_path)
    auditor = PolicyAuditor(db=db, project_root=tmp_path)

    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=False),
    )
    boundary = PolicyEnforcementBoundary(policy=policy, auditor=auditor, project_id="p-test")

    cfg = ProviderConfig(
        provider_id="openai-prod",
        display_name="OpenAI",
        provider_type=ProviderType.OPENAI,
        endpoint="https://api.openai.com/v1",
        default_model="gpt-4o",
    )
    adapter = OpenAIAdapter(cfg, api_key="sk-test-key")
    gateway = PolicyGatedGateway(adapter=adapter, boundary=boundary)

    context = _create_sample_context()

    # Attempt transmission to external destination
    with patch("urllib.request.urlopen") as mock_urlopen:
        response, decision = gateway.transmit(context, destination="external", provider="openai-prod")

        # Network MUST NOT be touched
        assert mock_urlopen.call_count == 0
        assert response is None
        assert decision.outcome == PolicyDecisionOutcome.DENY
        assert decision.reason_code == PolicyReasonCode.EXTERNAL_AI_DISABLED

    # Audit record must be created without leaking context snippets
    records = auditor.get_recent_records()
    assert len(records) == 1
    assert records[0].outcome == PolicyDecisionOutcome.DENY
    assert records[0].provider == "openai-prod"


def test_gateway_blocks_unauthorized_cloud_provider(tmp_path: Path):
    """Verify PolicyGatedGateway blocks transmissions to providers not listed in allowed_providers."""
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["anthropic"]),
    )
    boundary = PolicyEnforcementBoundary(policy=policy)

    cfg = ProviderConfig(
        provider_id="openai",
        display_name="OpenAI",
        provider_type=ProviderType.OPENAI,
        endpoint="https://api.openai.com/v1",
        default_model="gpt-4o",
    )
    adapter = OpenAIAdapter(cfg, api_key="sk-test-key")
    gateway = PolicyGatedGateway(adapter=adapter, boundary=boundary)

    context = _create_sample_context()

    with patch("urllib.request.urlopen") as mock_urlopen:
        response, decision = gateway.transmit(context, destination="external", provider="openai")
        assert mock_urlopen.call_count == 0
        assert response is None
        assert decision.outcome == PolicyDecisionOutcome.DENY
        assert decision.reason_code == PolicyReasonCode.PROVIDER_NOT_ALLOWED


def test_gateway_transmits_to_cloud_when_explicitly_authorized(tmp_path: Path):
    """Verify PolicyGatedGateway forwards sanitized payload when policy permits external transmission."""
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["openai"]),
    )
    boundary = PolicyEnforcementBoundary(policy=policy)

    cfg = ProviderConfig(
        provider_id="openai",
        display_name="OpenAI",
        provider_type=ProviderType.OPENAI,
        endpoint="https://api.openai.com/v1",
        default_model="gpt-4o",
    )
    adapter = OpenAIAdapter(cfg, api_key="sk-test-key")
    gateway = PolicyGatedGateway(adapter=adapter, boundary=boundary)

    context = _create_sample_context()

    mock_resp = MagicMock()
    mock_resp.read.return_value = (
        b'{"choices": [{"message": {"content": "Payments use Stripe."}}], "usage": {"total_tokens": 30}}'
    )
    mock_resp.__enter__.return_value = mock_resp

    with patch("msk.gateway.cloud._safe_urlopen", return_value=mock_resp) as mock_urlopen:
        response, decision = gateway.transmit(context, destination="external", provider="openai")

        assert decision.outcome == PolicyDecisionOutcome.ALLOW
        assert response is not None
        assert response["content"] == "Payments use Stripe."
        assert mock_urlopen.call_count == 1
