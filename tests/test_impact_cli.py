"""CLI end-to-end integration tests for 'msk impact' command."""

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from msk.cli.init import run_init
from msk.cli.main import app

runner = CliRunner()


def test_cli_impact_help() -> None:
    """Test msk impact --help displays all arguments and description."""
    result = runner.invoke(app, ["impact", "--help"])
    assert result.exit_code == 0
    assert "Analyze downstream impact" in result.stdout
    assert "--file" in result.stdout
    assert "--symbol" in result.stdout
    assert "--dependency" in result.stdout
    assert "--depth" in result.stdout
    assert "--direct" in result.stdout
    assert "--json" in result.stdout


def test_cli_impact_file_human_output(tmp_path: Path) -> None:
    """Test human-readable output of msk impact --file."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)

    result = runner.invoke(app, ["impact", "--file", "src/payment/client.py", "--root", str(target)])
    assert result.exit_code == 0
    assert "MSK Impact Analysis" in result.stdout
    assert "src/payment/client.py" in result.stdout
    assert "Downstream Impact Details" in result.stdout
    assert "DIRECT" in result.stdout


def test_cli_impact_file_json_output(tmp_path: Path) -> None:
    """Test machine-readable JSON output of msk impact --file --json."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)

    result = runner.invoke(app, ["impact", "--file", "src/payment/client.py", "--json", "--root", str(target)])
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert data["target_type"] == "file"
    assert data["target_name"] == "src/payment/client.py"
    assert data["total_affected"] > 0
    assert "items" in data
    assert isinstance(data["items"], list)
    assert "direct_count" in data
    assert "transitive_count" in data


def test_cli_impact_symbol_with_file(tmp_path: Path) -> None:
    """Test symbol impact analysis with specific file."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)

    result = runner.invoke(
        app,
        ["impact", "--symbol", "process_payment", "--file", "src/payment/service.py", "--root", str(target)],
    )
    assert result.exit_code == 0
    assert "process_payment" in result.stdout
    assert "Affected Items" in result.stdout


def test_cli_impact_direct_flag(tmp_path: Path) -> None:
    """Test --direct flag restricts traversal to depth 1."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)

    result = runner.invoke(
        app,
        ["impact", "--file", "src/payment/client.py", "--direct", "--json", "--root", str(target)],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["max_depth"] == 1
    assert data["transitive_count"] == 0
    for item in data["items"]:
        assert item["depth"] == 1
        assert item["classification"] == "direct"


def test_cli_impact_dependency(tmp_path: Path) -> None:
    """Test analyzing impact of an external dependency via CLI."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)

    result = runner.invoke(
        app,
        ["impact", "--dependency", "requests", "--root", str(target)],
    )
    assert result.exit_code == 0
    assert "requests" in result.stdout
    assert "requirements.txt" in result.stdout


def test_cli_impact_uninitialized_project(tmp_path: Path) -> None:
    """Test impact command fails cleanly when project is not initialized."""
    uninit_dir = tmp_path / "uninit"
    uninit_dir.mkdir()

    result = runner.invoke(app, ["impact", "--file", "test.py", "--root", str(uninit_dir)])
    assert result.exit_code == 1
    assert "No MSK project initialized" in result.stdout


def test_cli_impact_no_arguments_error(tmp_path: Path) -> None:
    """Test error when no target is provided."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)

    result = runner.invoke(app, ["impact", "--root", str(target)])
    assert result.exit_code == 1
    assert "Please specify a target" in result.stdout


def test_cli_impact_ambiguous_symbol_error(tmp_path: Path) -> None:
    """Test error when ambiguous symbol is provided without --file."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)

    result = runner.invoke(app, ["impact", "--symbol", "PaymentService", "--root", str(target)])
    assert result.exit_code == 1
    assert "Ambiguous symbol 'PaymentService'" in result.stdout
    assert "--file" in result.stdout
