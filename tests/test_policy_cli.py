"""CLI integration tests for 'msk policy' and policy-aware 'msk context' commands."""

import json
import re
import shutil
from pathlib import Path

from typer.testing import CliRunner

from msk.cli.init import run_init
from msk.cli.main import app
from msk.config.loader import load_policy_config, save_policy_config
from msk.config.models import ExternalLLMPolicy

runner = CliRunner()
ANSI_REGEX = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences for deterministic assertions."""
    return ANSI_REGEX.sub("", text)


def test_cli_policy_help() -> None:
    """Test that msk policy --help displays all subcommands."""
    result = runner.invoke(app, ["policy", "--help"])
    assert result.exit_code == 0
    clean = strip_ansi(result.stdout)
    assert "show" in clean
    assert "validate" in clean
    assert "evaluate" in clean
    assert "audit" in clean


def test_cli_policy_show_human_and_json(tmp_path: Path) -> None:
    """Test msk policy show output in human and JSON format."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)

    # Human output
    res_human = runner.invoke(app, ["policy", "show", "--root", str(target)])
    assert res_human.exit_code == 0
    clean = strip_ansi(res_human.stdout)
    assert "MSK Privacy & Policy Configuration" in clean
    assert "External AI Sharing" in clean
    assert "Secrets" in clean

    # JSON output
    res_json = runner.invoke(app, ["policy", "show", "--json", "--root", str(target)])
    assert res_json.exit_code == 0
    data = json.loads(res_json.stdout)
    assert data["version"] == "1.0.0"
    assert "external_llm" in data
    assert "security" in data


def test_cli_policy_validate(tmp_path: Path) -> None:
    """Test msk policy validate on valid and invalid configurations."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)

    # Valid default
    res = runner.invoke(app, ["policy", "validate", "--root", str(target)])
    assert res.exit_code == 0
    assert "is valid" in res.stdout

    # Create invalid policy file
    pol_file = target / ".msk" / "policy.json"
    pol_file.write_text('{"project_sensitivity": "invalid_value"}', encoding="utf-8")

    res_invalid = runner.invoke(app, ["policy", "validate", "--root", str(target)])
    assert res_invalid.exit_code == 1
    assert "has errors" in res_invalid.stdout


def test_cli_policy_evaluate() -> None:
    """Test evaluating hypothetical context transmission requests via CLI."""
    # Local evaluation
    res_local = runner.invoke(app, ["policy", "evaluate", "--target", "local", "--json"])
    assert res_local.exit_code == 0
    data_local = json.loads(res_local.stdout)
    assert data_local["outcome"] == "ALLOW"
    assert data_local["reason_code"] == "LOCAL_OPERATION_ALLOWED"

    # External evaluation with default policy
    res_ext = runner.invoke(app, ["policy", "evaluate", "--target", "external", "--json"])
    assert res_ext.exit_code == 0
    data_ext = json.loads(res_ext.stdout)
    assert data_ext["outcome"] == "DENY"
    assert data_ext["reason_code"] == "EXTERNAL_AI_DISABLED"


def test_cli_policy_audit(tmp_path: Path) -> None:
    """Test policy audit command displays recorded events."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)

    # Run a context command to generate an audit event
    runner.invoke(app, ["context", "--file", "src/payment/service.py", "--root", str(target)])

    # Check audit log via CLI
    res = runner.invoke(app, ["policy", "audit", "--root", str(target), "--json"])
    assert res.exit_code == 0
    logs = json.loads(res.stdout)
    assert isinstance(logs, list)
    assert len(logs) >= 1
    assert logs[0]["destination"] == "local"
    assert logs[0]["outcome"] == "ALLOW"


def test_cli_context_external_blocked_by_default(tmp_path: Path) -> None:
    """Test msk context --target external is blocked by default policy."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)

    # Test human output
    res = runner.invoke(
        app,
        [
            "context",
            "--file",
            "src/payment/service.py",
            "--target",
            "external",
            "--root",
            str(target),
        ],
    )
    assert res.exit_code == 0
    clean = strip_ansi(res.stdout)
    assert "External Context Transmission Blocked by Policy" in clean
    assert "EXTERNAL_AI_DISABLED" in clean

    # Test JSON output
    res_json = runner.invoke(
        app,
        [
            "context",
            "--file",
            "src/payment/service.py",
            "--target",
            "external",
            "--json",
            "--root",
            str(target),
        ],
    )
    assert res_json.exit_code == 0
    data = json.loads(res_json.stdout)
    assert data["status"] == "DENIED"
    assert data["policy_decision"]["outcome"] == "DENY"
    assert data["policy_decision"]["reason_code"] == "EXTERNAL_AI_DISABLED"


def test_cli_context_external_approved_policy(tmp_path: Path) -> None:
    """Test msk context --target external succeeds when policy permits it."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)

    # Enable external LLM for mock-ai
    pol = load_policy_config(target)
    pol.external_llm = ExternalLLMPolicy(enabled=True, allowed_providers=["mock-ai"])
    save_policy_config(target, pol)

    res = runner.invoke(
        app,
        [
            "context",
            "--file",
            "src/payment/service.py",
            "--target",
            "external",
            "--provider",
            "mock-ai",
            "--json",
            "--root",
            str(target),
        ],
    )
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data.get("status") != "DENIED"
    assert "selected_items" in data
    assert len(data["selected_items"]) > 0
