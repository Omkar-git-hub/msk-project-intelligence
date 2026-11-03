"""Tests for MSK Runtime Intelligence Engine."""

from datetime import UTC, datetime
from pathlib import Path

from msk.knowledge.models import FileEntity, ProjectEntity, SymbolEntity
from msk.knowledge.repository import KnowledgeRepository
from msk.runtime.engine import RuntimeIntelligenceEngine
from msk.storage.database import DatabaseManager


def _setup_engine_project(tmp_path: Path) -> RuntimeIntelligenceEngine:
    db_path = tmp_path / "msk.db"
    db = DatabaseManager(db_path)
    repo = KnowledgeRepository(db)
    proj_id = "proj_engine_test"

    now = datetime.now(UTC).isoformat()
    repo.save_project(
        ProjectEntity(
            id=proj_id,
            name="EngineTestProject",
            root_path=str(tmp_path),
            created_at=now,
            updated_at=now,
        )
    )

    # Add a sample project file and symbol
    f = FileEntity(
        id="f_worker",
        project_id=proj_id,
        path="src/worker.py",
        language="python",
        size_bytes=300,
        content_hash="chash1",
        structural_hash="shash1",
        modified_at=now,
    )
    repo.save_files([f])

    s = SymbolEntity(
        id="s_work",
        file_id="f_worker",
        name="do_work",
        type="function",
        line_start=10,
        line_end=25,
    )
    repo.save_symbols([s])

    return RuntimeIntelligenceEngine(
        db=db,
        repo=repo,
        project_id=proj_id,
        project_root=tmp_path,
        project_name="EngineTestProject",
    )


def test_ingest_logs_and_get_events(tmp_path: Path):
    engine = _setup_engine_project(tmp_path)
    raw = (
        "2026-11-03 14:00:00 [INFO] System initialized\n"
        "2026-11-03 14:01:00 [WARNING] Disk cache low\n"
    )
    events = engine.ingest_logs(raw, source="test_source", save=True)
    assert len(events) == 2

    stored = engine.get_events(limit=10)
    assert len(stored) == 2
    assert stored[0].source == "test_source"


def test_ingest_log_file(tmp_path: Path):
    engine = _setup_engine_project(tmp_path)
    log_file = tmp_path / "app.log"
    log_file.write_text("2026-11-03 15:00:00 [ERROR] Connection timeout to database\n")

    events = engine.ingest_log_file(log_file, save=True)
    assert len(events) == 1
    assert "Connection timeout" in events[0].message

    errors = engine.get_errors()
    assert len(errors) == 1
    assert errors[0].occurrence_count == 1


def test_analyze_runtime_error_flow(tmp_path: Path):
    engine = _setup_engine_project(tmp_path)
    tb = """
Traceback (most recent call last):
  File "src/worker.py", line 18, in do_work
    raise RuntimeError("Worker process crashed")
RuntimeError: Worker process crashed
"""
    ev, report = engine.analyze_runtime_error(tb, save=True)
    assert ev.exception_type == "RuntimeError"
    assert ev.file_path == "src/worker.py"
    assert ev.line_number == 18
    assert report.observed_facts.symbol_name == "do_work"
    assert len(report.investigation_checklist) > 0

    # Verify correlation through engine
    cor = engine.correlate_event(ev.id)
    assert cor.observed_facts.event_id == ev.id
    assert cor.inferred_impact.affected_file == "src/worker.py"
    assert cor.inferred_impact.affected_symbol == "do_work"


def test_engine_status_and_prune(tmp_path: Path):
    engine = _setup_engine_project(tmp_path)
    raw = "2026-11-03 12:00:00 [ERROR] Crash event\n"
    engine.ingest_logs(raw, save=True)

    status = engine.get_status()
    assert status.project_name == "EngineTestProject"
    assert status.total_events >= 1
    assert status.total_errors >= 1

    pruned = engine.prune(max_events=0, retention_days=0)
    assert pruned >= 1
