"""CLI integration tests for 'msk explain' command."""

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
    """Remove ANSI escape sequences from text for reliable assertions."""
    return ANSI_REGEX.sub("", text)


def _setup_initialized_project(tmp_path: Path) -> Path:
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)
    return target


def test_explain_help():
    """Verify 'msk explain --help' displays parameters."""
    result = runner.invoke(
        app,
        ["explain", "--help"],
        color=False,
        env={"NO_COLOR": "1", "TERM": "dumb"},
    )
    assert result.exit_code == 0
    clean_stdout = strip_ansi(result.stdout)
    assert "Explain code architecture" in clean_stdout
    assert "--file" in clean_stdout
    assert "--symbol" in clean_stdout
    assert "--error" in clean_stdout


def test_explain_missing_target_error(tmp_path: Path):
    """Verify 'msk explain' fails when no target is provided."""
    target = _setup_initialized_project(tmp_path)
    result = runner.invoke(app, ["explain", "--root", str(target)])
    assert result.exit_code == 1
    assert "Please specify a target to explain" in result.output


def test_explain_file(tmp_path: Path):
    """Verify 'msk explain --file <path>' outputs explanation with relevant entities."""
    target = _setup_initialized_project(tmp_path)

    result = runner.invoke(
        app,
        ["explain", "--file", "src/payment/service.py", "--root", str(target)],
    )
    assert result.exit_code == 0
    assert "MSK Code & Incident Explanation" in result.output
    assert "File: src/payment/service.py" in result.output
    assert "The project stays private." in result.output
    assert "Relevant Code Entities Analyzed" in result.output
    assert "Notice: AI-generated explanations are diagnostic assistance" in result.output


def test_explain_symbol(tmp_path: Path):
    """Verify 'msk explain --symbol <name>'."""
    target = _setup_initialized_project(tmp_path)

    result = runner.invoke(
        app,
        ["explain", "--symbol", "process_payment", "--root", str(target)],
    )
    assert result.exit_code == 0
    assert "Symbol: process_payment" in result.output
    assert "Relevant Code Entities Analyzed" in result.output


def test_explain_error_trace(tmp_path: Path):
    """Verify 'msk explain --error <trace>'."""
    target = _setup_initialized_project(tmp_path)

    error_text = "ValueError: transaction failed in process_payment at src/payment/service.py:45"
    result = runner.invoke(
        app,
        ["explain", "--error", error_text, "--root", str(target)],
    )
    assert result.exit_code == 0
    assert "Runtime Error / Incident" in result.output
    assert "The project stays private." in result.output


def test_explain_error_file(tmp_path: Path):
    """Verify 'msk explain --error-file <path>'."""
    target = _setup_initialized_project(tmp_path)
    err_file = target / "trace.log"
    err_file.write_text("NullPointerException in PaymentService.process at service.py:10", encoding="utf-8")

    result = runner.invoke(
        app,
        ["explain", "--error-file", str(err_file), "--root", str(target)],
    )
    assert result.exit_code == 0
    assert "Runtime Error / Incident" in result.output


def test_explain_json_output(tmp_path: Path):
    """Verify 'msk explain --json' produces structured output."""
    target = _setup_initialized_project(tmp_path)

    result = runner.invoke(
        app,
        ["explain", "--file", "src/payment/service.py", "--json", "--root", str(target)],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "SUCCESS"
    assert data["target"]["file"] == "src/payment/service.py"
    assert "explanation" in data
    assert "analyzed_items" in data
    assert "disclaimer" in data


def test_explain_external_blocked_by_policy(tmp_path: Path):
    """Verify 'msk explain --target external' fails closed when policy disables external AI."""
    target = _setup_initialized_project(tmp_path)

    policy = load_policy_config(target)
    policy.external_llm = ExternalLLMPolicy(enabled=False)
    save_policy_config(target, policy)

    result = runner.invoke(
        app,
        ["explain", "--file", "src/payment/service.py", "--target", "external", "--root", str(target)],
    )
    assert result.exit_code == 1
    assert "AI Transmission Blocked by Policy" in result.output
