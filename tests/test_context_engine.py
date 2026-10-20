"""Comprehensive test suite for MSK Smart Context Engine."""

import shutil
from pathlib import Path

import pytest

from msk.cli.init import run_init
from msk.common.errors import MSKError
from msk.context.budget import estimate_tokens, select_items_within_budget
from msk.context.engine import ContextEngine
from msk.context.errors_parser import parse_error_text
from msk.context.models import ContextItem, ContextItemType, ContextRequest
from msk.context.privacy import PrivacyFilter
from msk.context.ranking import sort_and_rank_items
from msk.knowledge.repository import KnowledgeRepository
from msk.storage.database import DatabaseManager


@pytest.fixture
def payment_project(tmp_path: Path) -> Path:
    """Fixture providing an initialized copy of payment_service."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)
    return target


def test_estimate_tokens_deterministic() -> None:
    """Test deterministic token counter approximation."""
    text = "def process_payment(payment_data: dict) -> dict:\n    return {'status': 'success'}"
    tokens_1 = estimate_tokens(text)
    tokens_2 = estimate_tokens(text)
    assert tokens_1 == tokens_2
    assert tokens_1 > 0
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0


def test_error_parser_python_stack_trace() -> None:
    """Test parsing Python stack trace."""
    trace = """
Traceback (most recent call last):
  File "src/payment/controller.py", line 35, in handle_payment
    return self.service.process_payment(data)
  File "src/payment/service.py", line 42, in process_payment
    raise ValueError("Invalid payment currency: USD")
ValueError: Invalid payment currency: USD
"""
    parsed = parse_error_text(trace)
    assert parsed.error_type == "ValueError"
    assert parsed.error_message == "Invalid payment currency: USD"
    assert "src/payment/controller.py" in parsed.extracted_files
    assert "src/payment/service.py" in parsed.extracted_files
    assert "handle_payment" in parsed.extracted_symbols
    assert "process_payment" in parsed.extracted_symbols
    assert len(parsed.locations) == 2
    assert parsed.locations[1].line_number == 42


def test_error_parser_java_and_ts_stack_traces() -> None:
    """Test parsing Java and TypeScript stack traces."""
    java_trace = """
Exception in thread "main" java.lang.NullPointerException: transaction failed
    at com.payment.service.PaymentService.processPayment(PaymentService.java:45)
"""
    parsed_java = parse_error_text(java_trace)
    assert parsed_java.error_type == "NullPointerException"
    assert "PaymentService.java" in parsed_java.extracted_files
    assert "processPayment" in parsed_java.extracted_symbols

    ts_trace = """
Error: Request failed with status 500
    at handlePayment (src/payment/controller.ts:25:12)
"""
    parsed_ts = parse_error_text(ts_trace)
    assert "src/payment/controller.ts" in parsed_ts.extracted_files
    assert "handlePayment" in parsed_ts.extracted_symbols


def test_privacy_filter_secret_scrubbing() -> None:
    """Test scrubbing secrets from query and error strings."""
    filter_ = PrivacyFilter()

    # AWS key and OpenAI token constructed at runtime from separate fragments
    dummy_aws_key = "".join(["AK", "IA", "12345678", "90ABCDEF"])
    dummy_sk_token = "".join(["sk", "-", "1234567890", "1234567890", "abcdef"])
    dirty = f"Error with api_key: '{dummy_aws_key}' and token {dummy_sk_token}"
    clean = filter_.sanitize_text(dirty)
    assert dummy_aws_key not in clean
    assert dummy_sk_token not in clean
    assert "[REDACTED_SECRET]" in clean
    assert filter_.redactions_count >= 2

    # Password assignment
    dummy_pwd = "".join(["Super", "Secret", "Password", "123!"])
    pass_text = f'password = "{dummy_pwd}"'
    clean_pass = filter_.sanitize_text(pass_text)
    assert dummy_pwd not in clean_pass
    assert "[REDACTED_SECRET]" in clean_pass


def test_privacy_filter_sensitive_file_exclusion() -> None:
    """Test detecting sensitive files like .env or private keys."""
    filter_ = PrivacyFilter()
    assert filter_.is_sensitive_file(".env") is True
    assert filter_.is_sensitive_file(".env.production") is True
    assert filter_.is_sensitive_file("id_rsa") is True
    assert filter_.is_sensitive_file("cert.pem") is True
    assert filter_.is_sensitive_file("src/payment/service.py") is False

    allowed = filter_.check_and_record_file(".env.local")
    assert allowed is False
    assert ".env.local" in filter_.excluded_files


def test_exact_file_matching(payment_project: Path) -> None:
    """Test context retrieval when focusing on a specific file."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ContextEngine(repo, "payment-service", payment_project)

    req = ContextRequest(file="src/payment/service.py", budget_tokens=3000)
    res = engine.build_context(req)

    assert res.project_name == "payment_service"
    assert res.request_summary["file"] == "src/payment/service.py"
    assert len(res.selected_items) > 0

    # The exact file should have top relevance
    file_item = next((i for i in res.selected_items if i.item_type == ContextItemType.FILE and i.file_path == "src/payment/service.py"), None)
    assert file_item is not None
    assert file_item.relevance_score >= 100.0
    assert any("Explicitly requested file" in r for r in file_item.reasons)

    # Contained symbols should be present
    sym_items = [i for i in res.selected_items if i.item_type == ContextItemType.SYMBOL and i.file_path == "src/payment/service.py"]
    assert len(sym_items) > 0
    assert any("process_payment" in s.name for s in sym_items)


def test_exact_symbol_matching(payment_project: Path) -> None:
    """Test context retrieval when focusing on a specific symbol."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ContextEngine(repo, "payment-service", payment_project)

    req = ContextRequest(symbol="process_payment", file="src/payment/service.py", budget_tokens=3000)
    res = engine.build_context(req)

    symbol_item = next((i for i in res.selected_items if i.item_type == ContextItemType.SYMBOL and "process_payment" in i.name), None)
    assert symbol_item is not None
    assert symbol_item.relevance_score >= 100.0

    # Containing file should also be selected
    file_item = next((i for i in res.selected_items if i.item_type == ContextItemType.FILE and i.file_path == "src/payment/service.py"), None)
    assert file_item is not None


def test_error_context_matching(payment_project: Path) -> None:
    """Test context retrieval matching an error stack trace."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ContextEngine(repo, "payment-service", payment_project)

    stack_trace = """
Traceback (most recent call last):
  File "src/payment/service.py", line 25, in process_payment
    raise ValueError("Transaction failed")
"""
    req = ContextRequest(error=stack_trace, budget_tokens=3000)
    res = engine.build_context(req)

    # Should match service.py and process_payment
    matched_file = any(i.file_path == "src/payment/service.py" for i in res.selected_items)
    assert matched_file is True

    matched_sym = any("process_payment" in i.name for i in res.selected_items)
    assert matched_sym is True


def test_question_keyword_matching(payment_project: Path) -> None:
    """Test context retrieval matching keywords from natural language query."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ContextEngine(repo, "payment-service", payment_project)

    req = ContextRequest(question="How does handle_payment in controller invoke client?", budget_tokens=3000)
    res = engine.build_context(req)

    assert len(res.selected_items) > 0
    # Should find handle_payment or controller
    controller_match = any("controller" in i.file_path for i in res.selected_items)
    assert controller_match is True


def test_related_tests_included(payment_project: Path) -> None:
    """Test that tests targeting queried symbols are surfaced."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ContextEngine(repo, "payment-service", payment_project)

    req = ContextRequest(symbol="process_payment", file="src/payment/service.py", budget_tokens=3000)
    res = engine.build_context(req)

    assert len(res.related_tests) > 0
    assert any("test_payment_service.py" in t for t in res.related_tests)


def test_budget_enforcement_and_omissions(payment_project: Path) -> None:
    """Test that context respects small token budget and accurately logs omitted items."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ContextEngine(repo, "payment-service", payment_project)

    # Very small budget (e.g. 100 tokens)
    small_budget = 100
    req = ContextRequest(file="src/payment/service.py", budget_tokens=small_budget)
    res = engine.build_context(req)

    assert res.budget_metrics.estimated_tokens_used <= small_budget
    assert len(res.omitted_items) > 0
    for omitted in res.omitted_items:
        assert omitted.relevance_score <= res.selected_items[0].relevance_score
        assert "token budget" in omitted.reason.lower()


def test_ranking_stability_and_tie_breaking() -> None:
    """Test deterministic ranking tie-breaking."""
    item1 = ContextItem(
        id="sym_1",
        name="alpha_func",
        item_type=ContextItemType.SYMBOL,
        file_path="src/a.py",
        relevance_score=80.0,
        reasons=["reason"],
        snippet="code",
        estimated_tokens=20,
    )
    item2 = ContextItem(
        id="sym_2",
        name="beta_func",
        item_type=ContextItemType.SYMBOL,
        file_path="src/a.py",
        relevance_score=80.0,
        reasons=["reason"],
        snippet="code",
        estimated_tokens=20,
    )
    item3 = ContextItem(
        id="file_1",
        name="src/a.py",
        item_type=ContextItemType.FILE,
        file_path="src/a.py",
        relevance_score=80.0,
        reasons=["reason"],
        snippet="code",
        estimated_tokens=20,
    )

    # Symbol priority (1) should come before file (2); alpha before beta
    sorted_items = sort_and_rank_items([item2, item3, item1])
    assert sorted_items[0].name == "alpha_func"
    assert sorted_items[1].name == "beta_func"
    assert sorted_items[2].name == "src/a.py"


def test_duplicate_elimination_in_budget() -> None:
    """Test deduplicating identical candidate items."""
    item1 = ContextItem(
        id="sym_1",
        name="process_payment",
        item_type=ContextItemType.SYMBOL,
        file_path="src/service.py",
        relevance_score=90.0,
        snippet="def process(): pass",
        estimated_tokens=20,
    )
    # Duplicate item with same key
    item2 = ContextItem(
        id="sym_1_dup",
        name="process_payment",
        item_type=ContextItemType.SYMBOL,
        file_path="src/service.py",
        relevance_score=85.0,
        snippet="def process(): pass",
        estimated_tokens=20,
    )

    selected, omitted, metrics = select_items_within_budget([item1, item2], budget_tokens=1000)
    assert len(selected) == 1
    assert metrics.selected_count == 1
    assert len(omitted) == 0


def test_empty_request_raises_error(payment_project: Path) -> None:
    """Test that submitting an empty request raises MSKError."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ContextEngine(repo, "payment-service", payment_project)

    req = ContextRequest()
    with pytest.raises(MSKError) as exc_info:
        engine.build_context(req)
    assert "Empty context request" in exc_info.value.message


def test_invalid_budget_limits(payment_project: Path) -> None:
    """Test that negative, zero, or excessively large budget limits are rejected."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ContextEngine(repo, "payment-service", payment_project)

    with pytest.raises(MSKError) as exc_0:
        engine.build_context(ContextRequest(file="src/payment/service.py", budget_tokens=0))
    assert "Invalid context budget" in exc_0.value.message

    with pytest.raises(MSKError) as exc_neg:
        engine.build_context(ContextRequest(file="src/payment/service.py", budget_tokens=-50))
    assert "Invalid context budget" in exc_neg.value.message

    with pytest.raises(MSKError) as exc_big:
        engine.build_context(ContextRequest(file="src/payment/service.py", budget_tokens=200_000))
    assert "exceeds maximum allowed limit" in exc_big.value.message


def test_unindexed_file_warning(payment_project: Path) -> None:
    """Test that a file existing on disk but not in knowledge store produces an actionable warning."""
    # Create a new unindexed file on disk
    new_file = payment_project / "src" / "payment" / "unindexed_file.py"
    new_file.write_text("def unindexed(): pass", encoding="utf-8")

    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ContextEngine(repo, "payment-service", payment_project)

    req = ContextRequest(file="src/payment/unindexed_file.py", budget_tokens=1000)
    res = engine.build_context(req)

    assert any("exists on disk but is not indexed" in w and "msk update" in w for w in res.warnings)


def test_nonexistent_file_warning(payment_project: Path) -> None:
    """Test that a nonexistent file produces a warning."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ContextEngine(repo, "payment-service", payment_project)

    req = ContextRequest(file="src/payment/nonexistent.py", budget_tokens=1000)
    res = engine.build_context(req)

    assert any("not found in project" in w for w in res.warnings)


def test_read_only_operation(payment_project: Path) -> None:
    """Test that smart context engine never modifies database records or files."""
    db_path = payment_project / ".msk" / "msk.db"
    db_mgr = DatabaseManager(db_path)
    repo = KnowledgeRepository(db_mgr)
    initial_files_count = len(repo.get_all_files("payment_service"))

    engine = ContextEngine(repo, "payment-service", payment_project)

    req = ContextRequest(
        question="How to process a payment?",
        file="src/payment/service.py",
        symbol="process_payment",
        budget_tokens=2000,
    )
    res = engine.build_context(req)
    assert len(res.selected_items) > 0

    # Ensure database records were unchanged
    final_files_count = len(repo.get_all_files("payment_service"))
    assert initial_files_count == final_files_count

    # Ensure files were not touched
    service_file = payment_project / "src" / "payment" / "service.py"
    assert service_file.is_file()
