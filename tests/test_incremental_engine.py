"""Comprehensive test suite for Phase 3: Incremental Change Engine and Consistency."""

import json
import shutil
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from msk.analyzer.analyzer import analyze_file
from msk.changes.engine import detect_changes
from msk.cli.init import run_init
from msk.cli.main import app
from msk.cli.update import run_update
from msk.graph.relationships import RelationshipType
from msk.knowledge.models import FileEntity
from msk.knowledge.repository import KnowledgeRepository
from msk.knowledge.serializer import _atomic_write_json
from msk.project.scanner import ProjectScanner, ScannedFile
from msk.storage.database import DatabaseManager

runner = CliRunner()


def test_detect_changes_classification(tmp_path: Path) -> None:
    """Verify change detection accurately partitions added, modified, deleted, renamed, unchanged."""
    f1 = tmp_path / "unchanged.py"
    f1.write_text("print('hello')", encoding="utf-8")

    f2 = tmp_path / "modified.py"
    f2.write_text("original content", encoding="utf-8")

    # Scanned files
    sf_unchanged = ScannedFile(
        absolute_path=f1,
        relative_path="unchanged.py",
        size_bytes=len(f1.read_bytes()),
        modified_at="2026-10-16T00:00:00Z",
    )
    sf_modified = ScannedFile(
        absolute_path=f2,
        relative_path="modified.py",
        size_bytes=len(f2.read_bytes()),
        modified_at="2026-10-16T00:00:00Z",
    )

    # Recorded files
    from msk.common.hashing import sha256_bytes

    rf_unchanged = FileEntity(
        id="f1",
        project_id="p1",
        path="unchanged.py",
        language="python",
        size_bytes=len(f1.read_bytes()),
        content_hash=sha256_bytes(f1.read_bytes()),
        structural_hash="sh1",
        modified_at="2026-10-16T00:00:00Z",
    )
    rf_modified = FileEntity(
        id="f2",
        project_id="p1",
        path="modified.py",
        language="python",
        size_bytes=len(f2.read_bytes()),
        content_hash="old_hash_differs",
        structural_hash="sh2",
        modified_at="2026-10-16T00:00:00Z",
    )
    rf_deleted = FileEntity(
        id="f3",
        project_id="p1",
        path="deleted.py",
        language="python",
        size_bytes=50,
        content_hash="deleted_hash",
        structural_hash="sh3",
        modified_at="2026-10-16T00:00:00Z",
    )

    # 1. Run detect_changes
    cs = detect_changes([sf_unchanged, sf_modified], [rf_unchanged, rf_modified, rf_deleted])
    assert len(cs.unchanged) == 1
    assert cs.unchanged[0][0].path == "unchanged.py"
    assert len(cs.modified) == 1
    assert cs.modified[0].relative_path == "modified.py"
    assert len(cs.deleted) == 1
    assert cs.deleted[0].path == "deleted.py"
    assert len(cs.added) == 0
    assert len(cs.renamed) == 0

    # 2. Test Rename Detection
    # Add a new scanned file with the exact content_hash as rf_deleted
    f_renamed = tmp_path / "renamed_target.py"
    f_renamed.write_text("unique_rename_bytes", encoding="utf-8")
    from msk.common.hashing import sha256_bytes
    rename_hash = sha256_bytes(f_renamed.read_bytes())
    rf_deleted.content_hash = rename_hash

    sf_renamed = ScannedFile(
        absolute_path=f_renamed,
        relative_path="renamed_target.py",
        size_bytes=len(f_renamed.read_bytes()),
        modified_at="2026-10-16T00:00:00Z",
    )

    cs_rename = detect_changes([sf_unchanged, sf_renamed], [rf_unchanged, rf_deleted])
    assert len(cs_rename.renamed) == 1
    old_file, new_sf = cs_rename.renamed[0]
    assert old_file.path == "deleted.py"
    assert new_sf.relative_path == "renamed_target.py"
    assert len(cs_rename.added) == 0
    assert len(cs_rename.deleted) == 0


def test_incremental_update_add_file(tmp_path: Path) -> None:
    """Test adding a new source file updates knowledge model incrementally while preserving existing records."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target_proj = tmp_path / "test_proj"
    shutil.copytree(fixture_dir, target_proj)

    # 1. Initialize project
    run_init(root=target_proj, force=True)
    db_path = target_proj / ".msk" / "msk.db"
    db_mgr = DatabaseManager(db_path)
    repo = KnowledgeRepository(db_mgr)

    with db_mgr.transaction() as conn:
        proj_id = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()["id"]

    initial_files = repo.get_all_files(proj_id)

    # 2. Add new file
    extra_file = target_proj / "src" / "payment" / "extra_helper.py"
    extra_file.write_text("class ExtraHelper:\n    def compute_tax(self):\n        return 0.1\n", encoding="utf-8")

    # 3. Run incremental update
    run_update(root=target_proj)

    # 4. Verify post-update state
    updated_files = repo.get_all_files(proj_id)
    assert len(updated_files) == len(initial_files) + 1
    assert "src/payment/extra_helper.py" in {f.path for f in updated_files}

    # Verify unchanged files were preserved (same IDs and content hashes)
    updated_files_by_path = {f.path: f for f in updated_files}
    for orig_f in initial_files:
        assert orig_f.path in updated_files_by_path
        assert orig_f.id == updated_files_by_path[orig_f.path].id
        assert orig_f.content_hash == updated_files_by_path[orig_f.path].content_hash

    # Verify new symbol was discovered
    symbols = repo.get_all_symbols(proj_id)
    symbol_names = {s.name for s in symbols}
    assert "ExtraHelper" in symbol_names
    assert "compute_tax" in symbol_names

    # Verify exports are consistent
    exports_dir = target_proj / ".msk" / "exports"
    structure = json.loads((exports_dir / "structure.json").read_text(encoding="utf-8"))
    assert len(structure) == len(updated_files)


def test_incremental_update_modify_file(tmp_path: Path) -> None:
    """Test modifying a file updates its symbols and relationships while preserving unchanged files."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target_proj = tmp_path / "test_proj"
    shutil.copytree(fixture_dir, target_proj)

    run_init(root=target_proj, force=True)
    db_path = target_proj / ".msk" / "msk.db"
    db_mgr = DatabaseManager(db_path)
    repo = KnowledgeRepository(db_mgr)

    with db_mgr.transaction() as conn:
        proj_id = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()["id"]

    # Read pre-modification symbols
    pre_symbols = {s.name: s for s in repo.get_all_symbols(proj_id)}
    assert "PaymentService" in pre_symbols

    # Modify src/payment/service.py by adding a new method
    service_file = target_proj / "src" / "payment" / "service.py"
    current_content = service_file.read_text(encoding="utf-8")
    modified_content = current_content + "\n    def refund_payment(self, payment_id):\n        return True\n"
    service_file.write_text(modified_content, encoding="utf-8")

    run_update(root=target_proj)

    # Post-modification check
    post_symbols = {s.name: s for s in repo.get_all_symbols(proj_id)}
    assert "refund_payment" in post_symbols
    assert "PaymentService" in post_symbols


def test_incremental_update_delete_file_and_stale_cleanup(tmp_path: Path) -> None:
    """Test deleting a file cleans up its symbols, dependencies, and relationships without leaving stale records."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target_proj = tmp_path / "test_proj"
    shutil.copytree(fixture_dir, target_proj)

    run_init(root=target_proj, force=True)
    db_path = target_proj / ".msk" / "msk.db"
    db_mgr = DatabaseManager(db_path)
    repo = KnowledgeRepository(db_mgr)

    with db_mgr.transaction() as conn:
        proj_id = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()["id"]

    # Delete client.py
    client_file = target_proj / "src" / "payment" / "client.py"
    assert client_file.is_file()
    client_file.unlink()

    run_update(root=target_proj)

    # Verify client.py is gone
    files = repo.get_all_files(proj_id)
    file_paths = {f.path for f in files}
    assert "src/payment/client.py" not in file_paths

    # Verify PaymentClient symbol is gone
    symbols = repo.get_all_symbols(proj_id)
    symbol_names = {s.name for s in symbols}
    assert "PaymentClient" not in symbol_names

    # Verify no relationships have dangling references to deleted client
    rels = repo.get_all_relationships(proj_id)
    existing_file_ids = {f.id for f in files}
    existing_sym_ids = {s.id for s in symbols}
    valid_ids = existing_file_ids | existing_sym_ids

    for r in rels:
        assert r.source_id in valid_ids, f"Dangling relationship source_id: {r.source_id}"
        assert r.target_id in valid_ids, f"Dangling relationship target_id: {r.target_id}"


def test_incremental_update_rename_file(tmp_path: Path) -> None:
    """Test renaming a file detects the rename, removes old records, and registers new path."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target_proj = tmp_path / "test_proj"
    shutil.copytree(fixture_dir, target_proj)

    run_init(root=target_proj, force=True)
    db_path = target_proj / ".msk" / "msk.db"
    db_mgr = DatabaseManager(db_path)
    repo = KnowledgeRepository(db_mgr)

    with db_mgr.transaction() as conn:
        proj_id = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()["id"]

    old_file = target_proj / "src" / "payment" / "client.py"
    new_file = target_proj / "src" / "payment" / "custom_client.py"
    old_file.rename(new_file)

    run_update(root=target_proj)

    files = repo.get_all_files(proj_id)
    file_paths = {f.path for f in files}
    assert "src/payment/client.py" not in file_paths
    assert "src/payment/custom_client.py" in file_paths

    symbols = repo.get_all_symbols(proj_id)
    symbol_names = {s.name for s in symbols}
    assert "PaymentClient" in symbol_names


def test_incremental_update_idempotent_no_changes(tmp_path: Path) -> None:
    """Test running msk update repeatedly without changes is idempotent and performs no re-analysis."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target_proj = tmp_path / "test_proj"
    shutil.copytree(fixture_dir, target_proj)

    run_init(root=target_proj, force=True)

    # 1st update
    res1 = runner.invoke(app, ["update", "--root", str(target_proj)])
    assert res1.exit_code == 0
    assert "Everything up to date" in res1.stdout

    # 2nd update
    res2 = runner.invoke(app, ["update", "--root", str(target_proj)])
    assert res2.exit_code == 0
    assert "Everything up to date" in res2.stdout


def test_incremental_update_removes_stale_symbols_and_edges(tmp_path: Path) -> None:
    """Test editing a file to remove a function cleanly removes the symbol and all graph edges pointing to it."""
    proj = tmp_path / "graph_proj"
    proj.mkdir()
    (proj / "app.py").write_text(
        "def helper_func():\n    return 42\n\ndef main():\n    return helper_func()\n",
        encoding="utf-8",
    )

    run_init(root=proj, force=True)
    db_mgr = DatabaseManager(proj / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)

    with db_mgr.transaction() as conn:
        proj_id = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()["id"]

    # Verify helper_func exists and call edge exists
    rels_pre = repo.get_all_relationships(proj_id)
    call_rels_pre = [r for r in rels_pre if r.relationship_type == RelationshipType.SYMBOL_CALLS_SYMBOL]
    assert len(call_rels_pre) == 1

    # Edit app.py to remove helper_func
    (proj / "app.py").write_text("def main():\n    return 100\n", encoding="utf-8")

    run_update(root=proj)

    # Verify helper_func symbol is removed
    symbols_post = {s.name for s in repo.get_all_symbols(proj_id)}
    assert "helper_func" not in symbols_post
    assert "main" in symbols_post

    # Verify call edge is removed
    rels_post = repo.get_all_relationships(proj_id)
    call_rels_post = [r for r in rels_post if r.relationship_type == RelationshipType.SYMBOL_CALLS_SYMBOL]
    assert len(call_rels_post) == 0


def test_database_transaction_rollback_on_failure(tmp_path: Path) -> None:
    """Test database transaction rollback when an error occurs during incremental update."""
    proj = tmp_path / "rollback_proj"
    proj.mkdir()
    (proj / "main.py").write_text("def run(): pass\n", encoding="utf-8")

    run_init(root=proj, force=True)
    db_mgr = DatabaseManager(proj / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)

    with db_mgr.transaction() as conn:
        proj_id = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()["id"]

    initial_files = repo.get_all_files(proj_id)
    assert len(initial_files) == 1

    # Attempt an update with a mocked failure during entity insertion
    with patch.object(repo, "save_symbols", side_effect=RuntimeError("Simulated DB Crash")):
        new_file = proj / "extra.py"
        new_file.write_text("class ShouldRollback: pass\n", encoding="utf-8")

        scanner = ProjectScanner(proj)
        scanned = list(scanner.scan())
        res = [analyze_file(sf, proj) for sf in scanned if sf.relative_path == "extra.py"]

        try:
            repo.apply_incremental_update(proj_id, [], res)
        except RuntimeError:
            pass

    # Verify database was rolled back: extra.py is NOT in the database
    files_after = repo.get_all_files(proj_id)
    assert len(files_after) == 1
    assert files_after[0].path == "main.py"


def test_ignored_files_remain_excluded_during_update(tmp_path: Path) -> None:
    """Test files matching .gitignore or .mskignore are ignored during incremental update."""
    proj = tmp_path / "ignore_proj"
    proj.mkdir()
    (proj / "main.py").write_text("def main(): pass\n", encoding="utf-8")
    (proj / ".gitignore").write_text("*.log\nbuild/\n", encoding="utf-8")
    (proj / ".mskignore").write_text("secrets/\n", encoding="utf-8")

    run_init(root=proj, force=True)
    db_mgr = DatabaseManager(proj / ".msk" / "msk.db")
    repo = KnowledgeRepository(db_mgr)

    with db_mgr.transaction() as conn:
        proj_id = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()["id"]

    # Create ignored files
    (proj / "debug.log").write_text("log data", encoding="utf-8")
    build_dir = proj / "build"
    build_dir.mkdir()
    (build_dir / "output.js").write_text("compiled", encoding="utf-8")
    sec_dir = proj / "secrets"
    sec_dir.mkdir()
    (sec_dir / "key.txt").write_text("top secret", encoding="utf-8")

    run_update(root=proj)

    files = repo.get_all_files(proj_id)
    paths = {f.path for f in files}
    assert "debug.log" not in paths
    assert "build/output.js" not in paths
    assert "secrets/key.txt" not in paths


def test_atomic_json_exports_interruption_safe(tmp_path: Path) -> None:
    """Test that atomic JSON writing creates valid files and leaves no temporary files."""
    target_json = tmp_path / "export_test.json"
    data = {"project": "MSK", "version": "0.1.0a1", "items": [1, 2, 3]}

    _atomic_write_json(target_json, data)
    assert target_json.is_file()

    # Verify no .tmp file exists
    assert not target_json.with_suffix(".tmp").exists()

    # Verify content
    loaded = json.loads(target_json.read_text(encoding="utf-8"))
    assert loaded["project"] == "MSK"
    assert loaded["items"] == [1, 2, 3]
