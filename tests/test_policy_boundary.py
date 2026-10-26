"""Integration tests for MSK policy enforcement boundary and audit logging."""

import json
from pathlib import Path

from msk.config.models import ExternalLLMPolicy, PolicyAction, PolicyConfig, SecurityPolicy
from msk.context.engine import ContextEngine
from msk.context.models import (
    BudgetMetrics,
    ContextItem,
    ContextResult,
    PrivacySummary,
)
from msk.knowledge.repository import KnowledgeRepository
from msk.policy.audit import PolicyAuditor
from msk.policy.boundary import PolicyEnforcementBoundary
from msk.policy.models import PolicyDecisionOutcome, PolicyReasonCode
from msk.storage.database import DatabaseManager

SYNTHETIC_SECRET_MARKER = "SYNTHETIC_SECRET_MARKER"
EXAMPLE_REDACTION_VALUE = "EXAMPLE_REDACTION_VALUE"


def _build_sample_context_result(
    secret_in_snippet: bool = False,
    sensitive_file: bool = False,
) -> ContextResult:
    """Helper to build a deterministic ContextResult for boundary tests."""
    file_path = "src/payment/service.py" if not sensitive_file else ".env.production"
    snippet_content = (
        f"def process_payment():\n    token = '{SYNTHETIC_SECRET_MARKER}'\n    return True"
        if secret_in_snippet
        else "def process_payment():\n    return True"
    )

    items = [
        ContextItem(
            id="item-1",
            name="process_payment",
            item_type="symbol",
            file_path=file_path,
            relevance_score=10.0,
            reasons=["Exact symbol match"],
            snippet=snippet_content,
            estimated_tokens=50,
            metadata={"visibility": "public"},
        )
    ]

    bm = BudgetMetrics(
        token_budget=2000,
        estimated_tokens_used=50,
        utilization_pct=2.5,
        total_candidates=1,
        selected_count=1,
        omitted_count=0,
    )

    priv = PrivacySummary(
        local_only=True,
        redactions_count=1 if secret_in_snippet else 0,
        excluded_sensitive_files=[file_path] if sensitive_file else [],
        notes=["Local test note"],
    )

    return ContextResult(
        project_name="boundary_test_project",
        analyzed_at="2026-10-26T12:00:00Z",
        request_summary={
            "question": f"How to configure {EXAMPLE_REDACTION_VALUE}?",
            "file": file_path,
        },
        budget_metrics=bm,
        privacy_summary=priv,
        selected_items=items,
        relationships=[],
        related_tests=[],
        omitted_items=[],
        warnings=[],
        llm_context=f"# Context\n{snippet_content}",
    )


def test_boundary_denies_external_transmission_when_disabled(tmp_path: Path) -> None:
    """Verify that boundary completely blocks transmission when external sharing is disabled."""
    db_path = tmp_path / "msk.db"
    db_manager = DatabaseManager(db_path)
    auditor = PolicyAuditor(db=db_manager, project_root=tmp_path)

    policy = PolicyConfig()  # external_llm.enabled is False
    boundary = PolicyEnforcementBoundary(
        policy=policy,
        auditor=auditor,
        project_id="test_proj",
    )

    context = _build_sample_context_result()
    transmitted, decision = boundary.enforce_transmission(context, destination="external")

    # Guaranteed: context payload is blocked (None returned)
    assert transmitted is None
    assert decision.outcome == PolicyDecisionOutcome.DENY
    assert decision.reason_code == PolicyReasonCode.EXTERNAL_AI_DISABLED

    # Verify audit record was created without payload
    logs = auditor.get_recent_records(limit=10)
    assert len(logs) == 1
    assert logs[0].outcome == PolicyDecisionOutcome.DENY
    assert logs[0].reason_code == PolicyReasonCode.EXTERNAL_AI_DISABLED


def test_boundary_redacts_without_leaking_original_values(tmp_path: Path) -> None:
    """Verify that redaction does not leak original sensitive values."""
    db_path = tmp_path / "msk.db"
    db_manager = DatabaseManager(db_path)
    auditor = PolicyAuditor(db=db_manager, project_root=tmp_path)

    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["mock-ai"]),
        security=SecurityPolicy(secrets=PolicyAction.SANITIZE),
        category_rules={"symbol": PolicyAction.SANITIZE},
        allow_external_redaction=True,
    )

    boundary = PolicyEnforcementBoundary(
        policy=policy,
        auditor=auditor,
        project_id="test_proj",
    )

    context = _build_sample_context_result(secret_in_snippet=True)
    transmitted, decision = boundary.enforce_transmission(
        context,
        destination="external",
        provider="mock-ai",
    )

    assert transmitted is not None
    assert decision.outcome == PolicyDecisionOutcome.REDACT
    assert decision.reason_code == PolicyReasonCode.REDACTION_APPLIED

    # Critical check: transmitted context MUST NOT contain the original sensitive marker!
    raw_output = json.dumps(transmitted.model_dump())
    assert SYNTHETIC_SECRET_MARKER not in raw_output
    assert "[REDACTED" in raw_output


def test_boundary_allows_approved_external_sharing(tmp_path: Path) -> None:
    """Verify that benign context to approved provider evaluates to ALLOW."""
    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["mock-ai"]),
    )
    boundary = PolicyEnforcementBoundary(
        policy=policy,
        project_id="test_proj",
    )

    context = _build_sample_context_result(secret_in_snippet=False)
    transmitted, decision = boundary.enforce_transmission(
        context,
        destination="external",
        provider="mock-ai",
    )

    assert transmitted is not None
    assert decision.outcome == PolicyDecisionOutcome.ALLOW
    assert decision.reason_code == PolicyReasonCode.EXPLICITLY_APPROVED


def test_audit_logs_contain_zero_sensitive_data(tmp_path: Path) -> None:
    """Verify that audit logs never store source code, secret values, or question text."""
    db_path = tmp_path / "msk.db"
    db_manager = DatabaseManager(db_path)
    auditor = PolicyAuditor(db=db_manager, project_root=tmp_path)

    policy = PolicyConfig(
        external_llm=ExternalLLMPolicy(enabled=True, allowed_providers=["mock-ai"]),
        security=SecurityPolicy(secrets=PolicyAction.BLOCK),
    )
    boundary = PolicyEnforcementBoundary(
        policy=policy,
        auditor=auditor,
        project_id="test_proj",
    )

    context = _build_sample_context_result(secret_in_snippet=True)
    boundary.enforce_transmission(context, destination="external", provider="mock-ai")

    # Inspect sqlite records
    records = auditor.get_recent_records(limit=10)
    assert len(records) >= 1
    rec = records[0]

    # Verify audit payload content
    rec_json = json.dumps(rec.model_dump())
    assert SYNTHETIC_SECRET_MARKER not in rec_json
    assert EXAMPLE_REDACTION_VALUE not in rec_json
    assert "def process_payment" not in rec_json

    # Inspect jsonl audit file
    audit_file = tmp_path / ".msk" / "policy_audit.jsonl"
    assert audit_file.is_file()
    file_content = audit_file.read_text(encoding="utf-8")
    assert SYNTHETIC_SECRET_MARKER not in file_content
    assert EXAMPLE_REDACTION_VALUE not in file_content
    assert "def process_payment" not in file_content


def test_context_engine_sharing_policy_integration(tmp_path: Path) -> None:
    """Verify ContextEngine.evaluate_sharing_policy integrates seamlessly."""
    db_path = tmp_path / "msk.db"
    db_manager = DatabaseManager(db_path)
    repo = KnowledgeRepository(db_manager)

    with repo.db.transaction() as conn:
        conn.execute(
            "INSERT INTO projects (id, name, root_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            ("p1", "engine_test", str(tmp_path), "2026-10-26T00:00:00Z", "2026-10-26T00:00:00Z"),
        )
        conn.execute(
            """
            INSERT INTO files (id, project_id, path, file_type, size_bytes, content_hash, structural_hash, modified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("f1", "p1", "src/main.py", "source", 100, "hash1", "hash2", "2026-10-26T00:00:00Z"),
        )

    engine = ContextEngine(repo, "p1", tmp_path)
    res = _build_sample_context_result()

    # 1. Local sharing check
    transmitted_local, dec_local = engine.evaluate_sharing_policy(res, destination="local")
    assert transmitted_local is not None
    assert dec_local.outcome == PolicyDecisionOutcome.ALLOW

    # 2. External sharing check with default policy (disabled)
    transmitted_ext, dec_ext = engine.evaluate_sharing_policy(res, destination="external")
    assert transmitted_ext is None
    assert dec_ext.outcome == PolicyDecisionOutcome.DENY
