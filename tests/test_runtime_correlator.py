"""Tests for MSK Runtime Correlator."""

from datetime import UTC, datetime
from pathlib import Path

from msk.graph.relationships import RelationshipType
from msk.knowledge.models import (
    ApiEndpointEntity,
    FileEntity,
    ProjectEntity,
    RelationshipEntity,
    SymbolEntity,
)
from msk.knowledge.models import (
    TestEntity as CodeTestEntity,
)
from msk.knowledge.repository import KnowledgeRepository
from msk.runtime.correlator import RuntimeCorrelator
from msk.runtime.models import RuntimeEvent, RuntimeLogLevel, StackFrame, StackTrace
from msk.storage.database import DatabaseManager


def _setup_mock_project(tmp_path: Path):
    db_path = tmp_path / "msk.db"
    db = DatabaseManager(db_path)
    repo = KnowledgeRepository(db)
    proj_id = "proj_test_msk"

    now = datetime.now(UTC).isoformat()
    repo.save_project(
        ProjectEntity(
            id=proj_id,
            name="TestProject",
            root_path=str(tmp_path),
            created_at=now,
            updated_at=now,
        )
    )

    # 1. Add files
    file_order = FileEntity(
        id="f_order",
        project_id=proj_id,
        path="src/order.py",
        language="python",
        size_bytes=500,
        content_hash="h1",
        structural_hash="s1",
        modified_at=now,
    )
    file_api = FileEntity(
        id="f_api",
        project_id=proj_id,
        path="src/api.py",
        language="python",
        size_bytes=400,
        content_hash="h2",
        structural_hash="s2",
        modified_at=now,
    )
    file_test = FileEntity(
        id="f_test",
        project_id=proj_id,
        path="tests/test_order.py",
        language="python",
        is_test=True,
        size_bytes=300,
        content_hash="h3",
        structural_hash="s3",
        modified_at=now,
    )
    repo.save_files([file_order, file_api, file_test])

    # 2. Add symbols
    sym_process = SymbolEntity(
        id="sym_process",
        file_id="f_order",
        name="process_order",
        type="function",
        line_start=10,
        line_end=35,
    )
    sym_handle = SymbolEntity(
        id="sym_handle",
        file_id="f_api",
        name="create_order_handler",
        type="function",
        line_start=5,
        line_end=20,
    )
    repo.save_symbols([sym_process, sym_handle])

    # 3. Add relationships
    # api.py imports order.py
    rel_dep = RelationshipEntity(
        id="rel_dep_1",
        project_id=proj_id,
        source_id="f_api",
        target_id="f_order",
        relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
    )
    # create_order_handler calls process_order
    rel_call = RelationshipEntity(
        id="rel_call_1",
        project_id=proj_id,
        source_id="sym_handle",
        target_id="sym_process",
        relationship_type=RelationshipType.SYMBOL_CALLS_SYMBOL,
    )
    repo.save_relationships([rel_dep, rel_call])

    # 4. Add test
    test_1 = CodeTestEntity(
        id="t_1",
        project_id=proj_id,
        file_id="f_test",
        name="test_process_order_success",
        test_type="unit",
        target_symbol="process_order",
    )
    repo.save_tests([test_1])

    # 5. Add API endpoint
    api_1 = ApiEndpointEntity(
        id="api_1",
        project_id=proj_id,
        file_id="f_api",
        route="/api/v1/orders",
        http_method="POST",
        handler_symbol="create_order_handler",
    )
    repo.save_api_endpoints([api_1])

    return repo, proj_id, tmp_path


def test_correlate_event_resolution(tmp_path: Path):
    repo, proj_id, root = _setup_mock_project(tmp_path)
    correlator = RuntimeCorrelator(repo, proj_id, root)

    now = datetime.now(UTC).isoformat()
    st = StackTrace(
        exception_type="ValueError",
        message="Invalid order total",
        frames=[
            StackFrame(
                file_path="src/order.py",
                line_number=22,
                function_name="process_order",
                is_project_file=True,
            )
        ],
    )
    ev = RuntimeEvent(
        id="rte_err_1",
        project_id=proj_id,
        timestamp=now,
        level=RuntimeLogLevel.ERROR.value,
        message="ValueError: Invalid order total",
        file_path="src/order.py",
        line_number=22,
        symbol_name="process_order",
        exception_type="ValueError",
        stack_trace=st,
        fingerprint="fp_val_err",
        created_at=now,
    )

    report = correlator.correlate_event(ev)

    assert report.fingerprint == "fp_val_err"
    assert report.observed_facts.file_path == "src/order.py"
    assert report.observed_facts.line_number == 22
    assert report.observed_facts.symbol_name == "process_order"

    # Inferred impact checks
    inferred = report.inferred_impact
    assert inferred.affected_file == "src/order.py"
    assert inferred.affected_symbol == "process_order"
    assert any("create_order_handler" in c for c in inferred.callers)
    assert "src/api.py" in inferred.dependent_files
    assert any("test_process_order_success" in t for t in inferred.related_tests)
    assert any("POST /api/v1/orders" in a for a in inferred.related_apis)
    assert inferred.downstream_impact_count > 0
    assert len(inferred.recommended_actions) > 0


def test_correlate_unindexed_file(tmp_path: Path):
    repo, proj_id, root = _setup_mock_project(tmp_path)
    correlator = RuntimeCorrelator(repo, proj_id, root)

    now = datetime.now(UTC).isoformat()
    ev = RuntimeEvent(
        id="rte_unk",
        project_id=proj_id,
        timestamp=now,
        level=RuntimeLogLevel.ERROR.value,
        message="Unknown crash",
        file_path="unknown/path.py",
        line_number=99,
        fingerprint="fp_unk",
        created_at=now,
    )
    report = correlator.correlate_event(ev)
    assert report.inferred_impact.affected_file is None
    assert any("Run 'msk update'" in act for act in report.inferred_impact.recommended_actions)
