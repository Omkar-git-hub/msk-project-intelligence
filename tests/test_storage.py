"""Tests for SQLite database and KnowledgeRepository."""

from pathlib import Path

from msk.knowledge.models import (
    DependencyEntity,
    FileEntity,
    ProjectEntity,
    RelationshipEntity,
    SymbolEntity,
)
from msk.knowledge.repository import KnowledgeRepository
from msk.storage.database import DatabaseManager


def test_sqlite_repository_roundtrip(tmp_path: Path) -> None:
    db_file = tmp_path / "msk.db"
    db_manager = DatabaseManager(db_file)
    repo = KnowledgeRepository(db_manager)

    # 1. Project
    proj = ProjectEntity(
        id="proj_1",
        name="test-project",
        root_path=str(tmp_path),
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )
    repo.save_project(proj)
    fetched_proj = repo.get_project("proj_1")
    assert fetched_proj is not None
    assert fetched_proj.name == "test-project"

    # 2. Files
    file_entity = FileEntity(
        id="file_1",
        project_id="proj_1",
        path="src/main.py",
        language="python",
        size_bytes=128,
        content_hash="abc123hash",
        structural_hash="xyz789hash",
        modified_at="2026-01-01T00:00:00Z",
    )
    repo.save_files([file_entity])
    files = repo.get_all_files("proj_1")
    assert len(files) == 1
    assert files[0].path == "src/main.py"

    # 3. Symbols
    sym = SymbolEntity(
        id="sym_1",
        file_id="file_1",
        name="PaymentService",
        type="class",
        line_start=1,
        line_end=20,
    )
    repo.save_symbols([sym])
    symbols = repo.get_all_symbols("proj_1")
    assert len(symbols) == 1
    assert symbols[0].name == "PaymentService"

    # 4. Dependencies
    dep = DependencyEntity(
        id="dep_1",
        project_id="proj_1",
        source="requirements.txt",
        target="requests",
        version_spec=">=2.0.0",
        type="package",
    )
    repo.save_dependencies([dep])
    deps = repo.get_all_dependencies("proj_1")
    assert len(deps) == 1
    assert deps[0].target == "requests"

    # 5. Relationships
    rel = RelationshipEntity(
        id="rel_1",
        project_id="proj_1",
        source_id="file_1",
        target_id="sym_1",
        relationship_type="FILE_CONTAINS_SYMBOL",
    )
    repo.save_relationships([rel])
    rels = repo.get_all_relationships("proj_1")
    assert len(rels) == 1
    assert rels[0].relationship_type == "FILE_CONTAINS_SYMBOL"

    # 6. Summary metrics
    summary = repo.get_summary("proj_1")
    assert summary.total_files == 1
    assert summary.total_symbols == 1
    assert summary.total_dependencies == 1
    assert summary.total_relationships == 1
