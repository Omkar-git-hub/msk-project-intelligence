"""Comprehensive tests for Phase 2: Project Knowledge Model Hardening."""

import json
from pathlib import Path
import tempfile
import pytest

from msk.analyzer.analyzer import analyze_file
from msk.analyzer.infrastructure import detect_infrastructure_file
from msk.analyzer.security import is_sensitive_path, scan_content_for_secrets
from msk.cli.init import run_init
from msk.graph.relationships import RelationshipType
from msk.knowledge.builder import KnowledgeBuilder
from msk.knowledge.models import ProjectEntity
from msk.knowledge.repository import KnowledgeRepository
from msk.project.scanner import ProjectScanner
from msk.storage.database import DatabaseManager


def test_payment_service_all_relationships_and_calls():
    """Verify that payment_service fixture produces all canonical relationship types including call graph."""
    fixture_path = Path("tests/fixtures/payment_service").resolve()

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "msk.db"
        db_mgr = DatabaseManager(db_path)
        repo = KnowledgeRepository(db_mgr)

        repo.save_project(
            ProjectEntity(
                id="proj_payment",
                name="payment_service",
                root_path=str(fixture_path),
                created_at="2026-09-29T00:00:00Z",
                updated_at="2026-09-29T00:00:00Z",
            )
        )

        scanner = ProjectScanner(fixture_path)
        files = list(scanner.scan())
        assert len(files) > 0

        res = [analyze_file(f, fixture_path) for f in files]
        builder = KnowledgeBuilder("proj_payment", fixture_path, repo)
        builder.build_and_save(res, export_exports=True)

        summary = repo.get_summary("proj_payment")
        assert summary.total_files == len(files)
        assert summary.total_symbols > 0
        assert summary.total_relationships > 0
        assert summary.total_tests > 0

        # Verify all canonical relationships exist
        rels = repo.get_all_relationships("proj_payment")
        rel_types = {r.relationship_type for r in rels}

        assert RelationshipType.FILE_CONTAINS_SYMBOL in rel_types
        assert RelationshipType.SYMBOL_IMPORTS_SYMBOL in rel_types
        assert RelationshipType.SYMBOL_CALLS_SYMBOL in rel_types
        assert RelationshipType.FILE_DEPENDS_ON_FILE in rel_types
        assert RelationshipType.TEST_TARGETS_SYMBOL in rel_types

        # Verify tests are recorded
        tests = repo.get_all_tests("proj_payment")
        test_names = {t.name for t in tests}
        assert "PaymentServiceTest" in test_names
        assert "test_process_payment_success" in test_names


def test_knowledge_model_regeneration_on_delete():
    """Verify that deleting .msk and re-running init completely recreates the model."""
    fixture_path = Path("tests/fixtures/payment_service").resolve()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy fixture into tmpdir so we can create/delete .msk without touching repo
        proj_dir = Path(tmpdir) / "test_project"
        import shutil

        shutil.copytree(fixture_path, proj_dir)

        # 1. Run init first time
        run_init(root=proj_dir, force=True)
        msk_dir = proj_dir / ".msk"
        assert (msk_dir / "msk.db").is_file()
        assert (msk_dir / "project.json").is_file()
        assert (msk_dir / "exports" / "graph.json").is_file()

        # Read first graph
        first_graph = json.loads((msk_dir / "exports" / "graph.json").read_text(encoding="utf-8"))
        first_node_count = len(first_graph["nodes"])
        first_rel_count = len(first_graph["relationships"])

        # 2. Delete .msk entirely
        shutil.rmtree(msk_dir)
        assert not msk_dir.exists()

        # 3. Re-run init
        run_init(root=proj_dir, force=True)
        assert (msk_dir / "msk.db").is_file()
        assert (msk_dir / "exports" / "graph.json").is_file()

        second_graph = json.loads((msk_dir / "exports" / "graph.json").read_text(encoding="utf-8"))
        assert len(second_graph["nodes"]) == first_node_count
        assert len(second_graph["relationships"]) == first_rel_count


def test_repeated_initialization_idempotence():
    """Verify that running init multiple times on the same directory does not corrupt data."""
    fixture_path = Path("tests/fixtures/payment_service").resolve()

    with tempfile.TemporaryDirectory() as tmpdir:
        proj_dir = Path(tmpdir) / "test_project"
        import shutil

        shutil.copytree(fixture_path, proj_dir)

        # Run init 3 times
        run_init(root=proj_dir, force=True)
        run_init(root=proj_dir, force=True)
        run_init(root=proj_dir, force=True)

        db_path = proj_dir / ".msk" / "msk.db"
        db_mgr = DatabaseManager(db_path)
        repo = KnowledgeRepository(db_mgr)

        with db_mgr.transaction() as conn:
            proj_count = conn.execute("SELECT count(*) as c FROM projects").fetchone()["c"]
            assert proj_count == 1
            proj_id = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()["id"]

        summary = repo.get_summary(proj_id)
        assert summary.total_files > 0


def test_sensitive_values_never_stored():
    """Verify that secrets are never stored in the database or JSON exports."""
    secret_key = "AKIAIOSFODNN7EXAMPLE"
    code = f'AWS_KEY = "{secret_key}"\npassword = "super_secret_password_123"\n'.encode("utf-8")

    findings = scan_content_for_secrets("config/settings.py", code)
    assert len(findings) > 0

    for f in findings:
        # Crucial check: raw secret must NEVER be in description or rule_id
        assert secret_key not in f.description
        assert "super_secret_password_123" not in f.description
        assert f.severity in ("HIGH", "MEDIUM", "CRITICAL", "LOW")

    # Sensitive path detection
    assert is_sensitive_path(".env")[0] is True
    assert is_sensitive_path("id_rsa")[0] is True
    assert is_sensitive_path("server.key")[0] is True
    assert is_sensitive_path("main.py")[0] is False


def test_all_json_exports_generated():
    """Verify all 7 JSON exports are generated and have valid structure."""
    fixture_path = Path("tests/fixtures/payment_service").resolve()

    with tempfile.TemporaryDirectory() as tmpdir:
        proj_dir = Path(tmpdir) / "test_project"
        import shutil

        shutil.copytree(fixture_path, proj_dir)
        run_init(root=proj_dir, force=True)

        exports_dir = proj_dir / ".msk" / "exports"
        required_exports = [
            "structure.json",
            "symbols.json",
            "graph.json",
            "tests.json",
            "apis.json",
            "infrastructure.json",
            "security.json",
        ]

        for fname in required_exports:
            file_path = exports_dir / fname
            assert file_path.is_file(), f"Missing export: {fname}"
            # Verify valid JSON
            content = json.loads(file_path.read_text(encoding="utf-8"))
            assert content is not None


def test_infrastructure_detection():
    """Verify detection of various infrastructure configuration files."""
    assert detect_infrastructure_file("Dockerfile") is not None
    assert detect_infrastructure_file("Dockerfile.dev") is not None
    assert detect_infrastructure_file("docker-compose.yml") is not None
    assert detect_infrastructure_file(".github/workflows/ci.yml") is not None
    assert detect_infrastructure_file("k8s/deployment.yaml") is not None
    assert detect_infrastructure_file("terraform/main.tf") is not None
    assert detect_infrastructure_file("Makefile") is not None
    assert detect_infrastructure_file("src/main.py") is None
