"""Comprehensive unit and integration test suite for MSK Impact Analysis Engine."""

import shutil
from pathlib import Path

import pytest

from msk.cli.init import run_init
from msk.cli.update import run_update
from msk.common.errors import MSKError
from msk.graph.relationships import RelationshipType
from msk.impact.engine import ImpactEngine
from msk.impact.models import ImpactCategory, ImpactClassification
from msk.knowledge.models import ProjectEntity
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


def test_direct_impact_file_dependency(payment_project: Path) -> None:
    """Test identifying direct dependent files when a file changes."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ImpactEngine(repo, "payment-service", payment_project)

    report = engine.analyze_file("src/payment/client.py", max_depth=1, direct_only=True)
    assert report.target_name == "src/payment/client.py"
    assert report.target_type == "file"
    assert report.max_depth == 1

    # Verify src/payment/service.py is directly impacted
    service_items = [i for i in report.items if i.file_path == "src/payment/service.py"]
    assert len(service_items) > 0
    assert any(i.classification == ImpactClassification.DIRECT for i in service_items)
    assert any(i.relationship_type == RelationshipType.FILE_DEPENDS_ON_FILE for i in service_items)

    # Verify contained symbol PaymentClient is present
    sym_items = [i for i in report.items if i.type == "symbol" and "PaymentClient" in i.name]
    assert len(sym_items) > 0
    assert sym_items[0].category == ImpactCategory.CONTAINED_SYMBOL
    assert sym_items[0].depth == 1


def test_direct_impact_symbol_call(payment_project: Path) -> None:
    """Test identifying direct callers when a symbol changes."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ImpactEngine(repo, "payment-service", payment_project)

    report = engine.analyze_symbol(
        "call_payment_api",
        file_path="src/payment/client.py",
        direct_only=True,
    )
    assert "call_payment_api" in report.target_name
    assert report.direct_count > 0

    # PaymentService.process_payment calls call_payment_api
    callers = [i for i in report.items if i.category == ImpactCategory.CALLER]
    caller_names = {c.name for c in callers}
    assert any("process_payment" in name for name in caller_names)

    # Containing file is reported
    file_items = [i for i in report.items if i.type == "file" and i.name == "src/payment/client.py"]
    assert len(file_items) == 1


def test_transitive_impact_across_multiple_levels(payment_project: Path) -> None:
    """Test multi-hop impact traversal: client.call_payment_api -> service.process_payment -> controller.handle_payment."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ImpactEngine(repo, "payment-service", payment_project)

    report = engine.analyze_symbol(
        "call_payment_api",
        file_path="src/payment/client.py",
        max_depth=3,
        direct_only=False,
    )

    assert report.total_affected > 0
    assert report.transitive_count > 0

    # Depth 1: process_payment
    d1_items = [i for i in report.items if i.depth == 1]
    assert any("process_payment" in i.name for i in d1_items)

    # Depth 2: handle_payment (calls process_payment)
    d2_items = [i for i in report.items if i.depth == 2]
    d2_names = {i.name for i in d2_items}
    assert any("handle_payment" in name for name in d2_names)

    # Check path construction
    handle_payment_item = next(i for i in d2_items if "handle_payment" in i.name)
    assert len(handle_payment_item.path) >= 2


def test_traversal_depth_bounds(payment_project: Path) -> None:
    """Test depth limit prevents traversal beyond specified threshold."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ImpactEngine(repo, "payment-service", payment_project)

    # Depth 1: direct only
    rep_depth1 = engine.analyze_file("src/payment/client.py", max_depth=1)
    max_d1 = max(i.depth for i in rep_depth1.items)
    assert max_d1 == 1
    assert rep_depth1.transitive_count == 0

    # Depth 3: includes transitive
    rep_depth3 = engine.analyze_file("src/payment/client.py", max_depth=3)
    max_d3 = max(i.depth for i in rep_depth3.items)
    assert max_d3 >= 2
    assert rep_depth3.transitive_count > 0
    assert rep_depth3.total_affected >= rep_depth1.total_affected


def test_cyclic_relationships_no_infinite_loop(tmp_path: Path) -> None:
    """Test that mutual / recursive calls terminate cleanly without infinite loops."""
    proj = tmp_path / "cyclic_proj"
    proj.mkdir()
    # Create circular call cycle: func_a calls func_b, func_b calls func_a
    (proj / "cycles.py").write_text(
        """def func_a():
    return func_b()

def func_b():
    return func_a()
""",
        encoding="utf-8",
    )
    run_init(root=proj, force=True)

    db_mgr = DatabaseManager(proj / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ImpactEngine(repo, "cyclic_proj", proj)

    # Run with large max_depth
    report = engine.analyze_symbol("func_a", max_depth=10)
    assert report.total_affected > 0

    # Ensure func_a is not duplicated and no infinite recursion occurred
    func_b_items = [i for i in report.items if i.name == "func_b"]
    assert len(func_b_items) == 1
    assert func_b_items[0].depth == 1


def test_duplicate_symbol_names_different_files(payment_project: Path) -> None:
    """Test disambiguation of duplicate symbol names in different files."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ImpactEngine(repo, "payment-service", payment_project)

    # PaymentService exists in both Python (src/payment/service.py) and Java (src/java/PaymentService.java)
    # 1. Without --file: raises MSKError with ambiguous symbol message
    with pytest.raises(MSKError) as exc_info:
        engine.analyze_symbol("PaymentService")
    assert "Ambiguous symbol 'PaymentService'" in exc_info.value.message
    assert "--file" in exc_info.value.message

    # 2. With --file specifying Python: resolves cleanly to Python service
    report_py = engine.analyze_symbol("PaymentService", file_path="src/payment/service.py")
    assert report_py.target_name == "PaymentService"
    assert report_py.target_file == "src/payment/service.py"
    # Callers should only be Python callers
    for item in report_py.items:
        if item.category == ImpactCategory.CALLER:
            assert item.file_path.endswith(".py")

    # 3. With --file specifying Java: resolves cleanly to Java service
    report_java = engine.analyze_symbol("PaymentService", file_path="src/java/PaymentService.java")
    assert report_java.target_name == "PaymentService"
    assert report_java.target_file == "src/java/PaymentService.java"


def test_deleted_files_and_symbols_not_in_impact_after_update(payment_project: Path) -> None:
    """Test that deleted files and their symbols are completely removed from impact results after msk update."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)

    # Delete client.py
    client_file = payment_project / "src" / "payment" / "client.py"
    client_file.unlink()

    run_update(root=payment_project)

    engine_post = ImpactEngine(repo, "payment-service", payment_project)

    # Querying client.py should raise error (not found)
    with pytest.raises(MSKError):
        engine_post.analyze_file("src/payment/client.py")

    # Querying service.py should no longer report client.py as dependent or contained
    report = engine_post.analyze_file("src/payment/service.py")
    affected_paths = {i.file_path for i in report.items}
    assert "src/payment/client.py" not in affected_paths


def test_renamed_files_reflect_new_path(payment_project: Path) -> None:
    """Test that renamed files are analyzed under their new path."""
    old_file = payment_project / "src" / "payment" / "client.py"
    new_file = payment_project / "src" / "payment" / "renamed_client.py"
    old_file.rename(new_file)

    run_update(root=payment_project)

    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ImpactEngine(repo, "payment-service", payment_project)

    # Old path should error
    with pytest.raises(MSKError):
        engine.analyze_file("src/payment/client.py")

    # New path should succeed
    report = engine.analyze_file("src/payment/renamed_client.py")
    assert report.target_name == "src/payment/renamed_client.py"
    assert any("service.py" in i.file_path for i in report.items)


def test_missing_files_and_symbols(payment_project: Path) -> None:
    """Test clear error messages for missing files and missing symbols."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ImpactEngine(repo, "payment-service", payment_project)

    # Missing file completely
    with pytest.raises(MSKError) as exc_file:
        engine.analyze_file("src/nonexistent/missing.py")
    assert "File not found" in exc_file.value.message

    # File exists on disk but not in DB
    unindexed = payment_project / "src" / "payment" / "unindexed_file.py"
    unindexed.write_text("def hello(): pass\n", encoding="utf-8")
    with pytest.raises(MSKError) as exc_unindexed:
        engine.analyze_file("src/payment/unindexed_file.py")
    assert "exists on disk but is not indexed" in exc_unindexed.value.message
    assert "msk update" in (exc_unindexed.value.hint or "")

    # Missing symbol in indexed file
    with pytest.raises(MSKError) as exc_sym:
        engine.analyze_symbol("non_existent_function", file_path="src/payment/service.py")
    assert "not found" in exc_sym.value.message


def test_empty_knowledge_graph(tmp_path: Path) -> None:
    """Test engine raises clear actionable message when knowledge graph is empty."""
    empty_proj = tmp_path / "empty_proj"
    empty_proj.mkdir()
    db_mgr = DatabaseManager(empty_proj / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    repo.save_project(
        ProjectEntity(
            id="empty_proj",
            name="empty_proj",
            root_path=str(empty_proj),
            created_at="2026-10-17T00:00:00Z",
            updated_at="2026-10-17T00:00:00Z",
        )
    )

    engine = ImpactEngine(repo, "empty_proj", empty_proj)
    with pytest.raises(MSKError) as exc:
        engine.analyze_file("any.py")
    assert "empty" in exc.value.message.lower()


def test_test_relationships_and_affected_tests(payment_project: Path) -> None:
    """Test that tests targeting affected symbols are surfaced prominently."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ImpactEngine(repo, "payment-service", payment_project)

    # Process payment is targeted by tests/test_payment_service.py
    report = engine.analyze_symbol("process_payment", file_path="src/payment/service.py")

    test_items = [i for i in report.items if i.type == "test"]
    assert len(test_items) > 0
    test_names = {t.name for t in test_items}
    assert "test_process_payment_success" in test_names


def test_stable_ordering_and_deduplication(payment_project: Path) -> None:
    """Test that repeated impact queries produce identical, deduplicated results."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ImpactEngine(repo, "payment-service", payment_project)

    rep1 = engine.analyze_file("src/payment/service.py", max_depth=3)
    rep2 = engine.analyze_file("src/payment/service.py", max_depth=3)

    assert rep1.total_affected == rep2.total_affected
    assert rep1.direct_count == rep2.direct_count
    assert rep1.transitive_count == rep2.transitive_count

    # Verify identical ordering of every item
    for i1, i2 in zip(rep1.items, rep2.items, strict=True):
        assert i1.name == i2.name
        assert i1.depth == i2.depth
        assert i1.type == i2.type
        assert i1.category == i2.category
        assert i1.classification == i2.classification


def test_dependency_impact(payment_project: Path) -> None:
    """Test analyzing external package dependency impact."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ImpactEngine(repo, "payment-service", payment_project)

    # In requirements.txt: requests
    report = engine.analyze_dependency("requests")
    assert report.target_name == "requests"
    assert report.total_affected > 0

    # Manifest file is reported
    manifest_items = [i for i in report.items if i.category == ImpactCategory.DEPENDENCY]
    assert len(manifest_items) > 0
    assert "requirements.txt" in manifest_items[0].file_path


def test_database_integrity_and_no_source_leaks(payment_project: Path) -> None:
    """Test that impact reports never leak raw source code or credentials."""
    db_mgr = DatabaseManager(payment_project / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)
    engine = ImpactEngine(repo, "payment-service", payment_project)

    report = engine.analyze_file("src/payment/service.py", max_depth=3)
    dumped_str = report.model_dump_json()

    # Source code content checks
    assert "def process_payment" not in dumped_str
    assert "import stripe" not in dumped_str
    assert "AKIA" not in dumped_str
