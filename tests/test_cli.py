"""CLI end-to-end integration tests."""

from pathlib import Path
import shutil
from typer.testing import CliRunner
from msk.cli.main import app

runner = CliRunner()


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "MSK" in result.stdout
    assert "init" in result.stdout
    assert "status" in result.stdout
    assert "doctor" in result.stdout
    assert "update" in result.stdout


def test_cli_doctor(tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "MSK System Doctor" in result.stdout
    assert "Python" in result.stdout
    assert "Tree-sitter AST" in result.stdout


def test_cli_init_status_update_cycle(tmp_path: Path) -> None:
    # Copy fixture payment_service to tmp_path, ignoring any .msk
    fixture_dir = Path(__file__).parent / "fixtures" / "payment_service"
    target_proj = tmp_path / "payment-service"
    shutil.copytree(fixture_dir, target_proj, ignore=shutil.ignore_patterns(".msk"))

    # 1. msk status before init -> fails cleanly
    status_pre = runner.invoke(app, ["status", "--root", str(target_proj)])
    assert status_pre.exit_code == 1
    assert "No MSK project initialized" in status_pre.stdout

    # 2. msk init
    init_res = runner.invoke(app, ["init", "--root", str(target_proj)])
    assert init_res.exit_code == 0
    assert "Initialized MSK Project Intelligence" in init_res.stdout

    # Verify .msk artifacts exist
    msk_dir = target_proj / ".msk"
    assert (msk_dir / "msk.db").is_file()
    assert (msk_dir / "project.json").is_file()
    assert (msk_dir / "policy.json").is_file()
    assert (msk_dir / "exports" / "structure.json").is_file()
    assert (msk_dir / "exports" / "symbols.json").is_file()
    assert (msk_dir / "exports" / "graph.json").is_file()

    # 3. msk status after init
    status_post = runner.invoke(app, ["status", "--root", str(target_proj)])
    assert status_post.exit_code == 0
    assert "MSK Project" in status_post.stdout
    assert "Files:" in status_post.stdout
    assert "Symbols:" in status_post.stdout
    assert "Dependencies:" in status_post.stdout
    assert "Relationships:" in status_post.stdout

    # 4. msk init again without force -> fails with ProjectAlreadyInitializedError
    init_dup = runner.invoke(app, ["init", "--root", str(target_proj)])
    assert init_dup.exit_code == 1
    assert "already exists" in init_dup.stdout

    # 5. msk init with --force -> succeeds
    init_force = runner.invoke(app, ["init", "--force", "--root", str(target_proj)])
    assert init_force.exit_code == 0

    # 6. msk update when clean -> reports up to date
    update_clean = runner.invoke(app, ["update", "--root", str(target_proj)])
    assert update_clean.exit_code == 0
    assert "Everything up to date" in update_clean.stdout

    # 7. Add a new file and run msk update
    new_file = target_proj / "src" / "payment" / "extra.py"
    new_file.write_text("class ExtraService:\n    def run(self): pass\n", encoding="utf-8")

    update_added = runner.invoke(app, ["update", "--root", str(target_proj)])
    assert update_added.exit_code == 0
    assert "Knowledge model successfully updated" in update_added.stdout

    # 8. Test Regenerability: delete .msk and re-init
    shutil.rmtree(msk_dir)
    assert not msk_dir.exists()

    reinit_res = runner.invoke(app, ["init", "--root", str(target_proj)])
    assert reinit_res.exit_code == 0
    assert (msk_dir / "msk.db").is_file()
