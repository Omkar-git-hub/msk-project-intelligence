"""Tests for MSK Runtime Impact Engine."""

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
from msk.runtime.impact import RuntimeImpactEngine
from msk.runtime.models import (
    RuntimeErrorGroup,
    RuntimeEvent,
    RuntimeLogLevel,
    StackFrame,
    StackTrace,
)
from msk.storage.database import DatabaseManager


def _setup_impact_project(tmp_path: Path):
    db_path = tmp_path / "msk.db"
    db = DatabaseManager(db_path)
    repo = KnowledgeRepository(db)
    proj_id = "proj_impact_test"

    now = datetime.now(UTC).isoformat()
    repo.save_project(
        ProjectEntity(
            id=proj_id,
            name="ImpactTestProject",
            root_path=str(tmp_path),
            created_at=now,
            updated_at=now,
        )
    )

    # Files
    f_calc = FileEntity(
        id="f_calc",
        project_id=proj_id,
        path="src/calculator.py",
        language="python",
        size_bytes=200,
        content_hash="c1",
        structural_hash="s1",
        modified_at=now,
    )
    f_app = FileEntity(
        id="f_app",
        project_id=proj_id,
        path="src/app.py",
        language="python",
        size_bytes=300,
        content_hash="c2",
        structural_hash="s2",
        modified_at=now,
    )
    f_test = FileEntity(
        id="f_test",
        project_id=proj_id,
        path="tests/test_calculator.py",
        language="python",
        is_test=True,
        size_bytes=150,
        content_hash="c3",
        structural_hash="s3",
        modified_at=now,
    )
    repo.save_files([f_calc, f_app, f_test])

    # Symbols
    sym_div = SymbolEntity(
        id="sym_div",
        file_id="f_calc",
        name="divide",
        type="function",
        line_start=10,
        line_end=20,
    )
    sym_run = SymbolEntity(
        id="sym_run",
        file_id="f_app",
        name="run_calculation",
        type="function",
        line_start=5,
        line_end=25,
    )
    repo.save_symbols([sym_div, sym_run])

    # Relationships
    rel_import = RelationshipEntity(
        id="r_imp",
        project_id=proj_id,
        source_id="f_app",
        target_id="f_calc",
        relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
    )
    rel_call = RelationshipEntity(
        id="r_call",
        project_id=proj_id,
        source_id="sym_run",
        target_id="sym_div",
        relationship_type=RelationshipType.SYMBOL_CALLS_SYMBOL,
    )
    repo.save_relationships([rel_import, rel_call])

    # Test
    test_calc = CodeTestEntity(
        id="t_calc",
        project_id=proj_id,
        file_id="f_test",
        name="test_divide_by_zero",
        test_type="unit",
        target_symbol="divide",
    )
    repo.save_tests([test_calc])

    # API
    api_calc = ApiEndpointEntity(
        id="api_calc",
        project_id=proj_id,
        file_id="f_app",
        route="/api/calc/divide",
        http_method="POST",
        handler_symbol="run_calculation",
    )
    repo.save_api_endpoints([api_calc])

    return repo, proj_id, tmp_path


def test_analyze_event_impact(tmp_path: Path):
    repo, proj_id, root = _setup_impact_project(tmp_path)
    engine = RuntimeImpactEngine(repo, proj_id, root)

    now = datetime.now(UTC).isoformat()
    st = StackTrace(
        exception_type="ZeroDivisionError",
        message="division by zero",
        frames=[
            StackFrame(
                file_path="src/calculator.py",
                line_number=15,
                function_name="divide",
                is_project_file=True,
            )
        ],
    )
    event = RuntimeEvent(
        id="rte_div_zero",
        project_id=proj_id,
        timestamp=now,
        level=RuntimeLogLevel.ERROR.value,
        message="ZeroDivisionError: division by zero",
        file_path="src/calculator.py",
        line_number=15,
        symbol_name="divide",
        exception_type="ZeroDivisionError",
        stack_trace=st,
        fingerprint="fp_zero_div",
        created_at=now,
    )

    report = engine.analyze_event_impact(event)

    # Check observed facts
    assert report.observed_facts.exception_type == "ZeroDivisionError"
    assert report.observed_facts.file_path == "src/calculator.py"
    assert report.observed_facts.line_number == 15
    assert report.observed_facts.symbol_name == "divide"

    # Check inferences
    assert any("POST /api/calc/divide" in api for api in report.impacted_apis)
    assert any("test_divide_by_zero" in t for t in report.recommended_tests)
    assert len(report.investigation_checklist) > 0
    assert any("divide" in check for check in report.investigation_checklist)


def test_analyze_error_group_impact(tmp_path: Path):
    repo, proj_id, root = _setup_impact_project(tmp_path)
    engine = RuntimeImpactEngine(repo, proj_id, root)

    now = datetime.now(UTC).isoformat()
    group = RuntimeErrorGroup(
        id="rterr_1",
        project_id=proj_id,
        fingerprint="fp_zero_div",
        exception_type="ZeroDivisionError",
        message_template="ZeroDivisionError: division by zero",
        first_seen=now,
        last_seen=now,
        occurrence_count=5,
        file_path="src/calculator.py",
        line_number=15,
        symbol_name="divide",
    )

    report = engine.analyze_error_group_impact(group)
    assert report.observed_facts.occurrence_count == 5
    assert any("5 times" in c for c in report.investigation_checklist)


def test_analyze_unindexed_event_impact(tmp_path: Path):
    repo, proj_id, root = _setup_impact_project(tmp_path)
    engine = RuntimeImpactEngine(repo, proj_id, root)

    now = datetime.now(UTC).isoformat()
    event = RuntimeEvent(
        id="rte_unknown",
        project_id=proj_id,
        timestamp=now,
        level=RuntimeLogLevel.ERROR.value,
        message="ImportError: No module named 'lib'",
        file_path="external/lib.py",
        fingerprint="fp_imp_err",
        created_at=now,
    )

    report = engine.analyze_event_impact(event)
    assert report.total_blast_radius == 0
    assert len(report.investigation_checklist) > 0
