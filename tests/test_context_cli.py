"""CLI end-to-end integration tests for 'msk context' command."""

import json
import re
import shutil
from pathlib import Path

from typer.testing import CliRunner

from msk.cli.init import run_init
from msk.cli.main import app

runner = CliRunner()

ANSI_REGEX = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text for reliable assertions."""
    return ANSI_REGEX.sub("", text)


def test_cli_context_help() -> None:
    """Test msk context --help displays all arguments and description."""
    result = runner.invoke(
        app,
        ["context", "--help"],
        color=False,
        env={"NO_COLOR": "1", "TERM": "dumb"},
    )
    assert result.exit_code == 0
    clean_stdout = strip_ansi(result.stdout)
    assert "Select and rank relevant project context" in clean_stdout
    assert "--question" in clean_stdout
    assert "--file" in clean_stdout
    assert "--symbol" in clean_stdout
    assert "--error" in clean_stdout
    assert "--error-file" in clean_stdout
    assert "--budget" in clean_stdout
    assert "--json" in clean_stdout


def test_cli_context_question_human_output(tmp_path: Path) -> None:
    """Test human-readable output of msk context --question."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(
        fixture_dir,
        target,
        ignore=shutil.ignore_patterns(".msk"),
    )
    run_init(root=target, force=True)

    result = runner.invoke(
        app,
        [
            "context",
            "--question",
            "How does process_payment work?",
            "--root",
            str(target),
        ],
    )
    assert result.exit_code == 0
    clean_out = strip_ansi(result.stdout)
    assert "MSK Smart Context Engine" in clean_out
    assert "Context Budget:" in clean_out
    assert "Selected Project Context Entities" in clean_out
    assert "Privacy & Data Minimization" in clean_out


def test_cli_context_file_json_output(tmp_path: Path) -> None:
    """Test machine-readable JSON output of msk context --file --json."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(
        fixture_dir,
        target,
        ignore=shutil.ignore_patterns(".msk"),
    )
    run_init(root=target, force=True)

    result = runner.invoke(
        app,
        [
            "context",
            "--file",
            "src/payment/service.py",
            "--json",
            "--root",
            str(target),
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["project_name"] == "payment_service"
    assert data["request_summary"]["file"] == "src/payment/service.py"
    assert "budget_metrics" in data
    assert "privacy_summary" in data
    assert "selected_items" in data
    assert len(data["selected_items"]) > 0
    assert "llm_context" in data
    assert "# Project Context" in data["llm_context"]


def test_cli_context_symbol_and_file(tmp_path: Path) -> None:
    """Test symbol context selection with specific file."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(
        fixture_dir,
        target,
        ignore=shutil.ignore_patterns(".msk"),
    )
    run_init(root=target, force=True)

    result = runner.invoke(
        app,
        [
            "context",
            "--symbol",
            "process_payment",
            "--file",
            "src/payment/service.py",
            "--json",
            "--root",
            str(target),
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert any(
        "process_payment" in item["name"]
        for item in data["selected_items"]
    )


def test_cli_context_error_file(tmp_path: Path) -> None:
    """Test error stack trace provided via --error-file."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(
        fixture_dir,
        target,
        ignore=shutil.ignore_patterns(".msk"),
    )
    run_init(root=target, force=True)

    err_file = target / "trace.log"
    err_file.write_text(
        'File "src/payment/service.py", line 25, in process_payment\n'
        "ValueError: invalid transaction",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "context",
            "--error-file",
            "trace.log",
            "--json",
            "--root",
            str(target),
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["request_summary"]["has_error"] is True
    assert any(
        "service.py" in item["file_path"]
        for item in data["selected_items"]
    )


def test_cli_context_budget_enforcement(tmp_path: Path) -> None:
    """Test setting a tight token budget."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(
        fixture_dir,
        target,
        ignore=shutil.ignore_patterns(".msk"),
    )
    run_init(root=target, force=True)

    result = runner.invoke(
        app,
        [
            "context",
            "--file",
            "src/payment/service.py",
            "--budget",
            "150",
            "--json",
            "--root",
            str(target),
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["budget_metrics"]["token_budget"] == 150
    assert data["budget_metrics"]["estimated_tokens_used"] <= 150
    assert len(data["omitted_items"]) > 0


def test_cli_context_secret_redaction(tmp_path: Path) -> None:
    """Test that secrets in CLI inputs are scrubbed before output."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(
        fixture_dir,
        target,
        ignore=shutil.ignore_patterns(".msk"),
    )
    run_init(root=target, force=True)

    # Build a synthetic secret-like marker without embedding a credential.
    dummy_key = "-".join(("FAKE", "AWS", "TEST", "KEY"))
    key_name = "_".join(("api", "key"))

    question = (
        f"Why did request with {key_name}={dummy_key} "
        "fail in service?"
    )

    result = runner.invoke(
        app,
        [
            "context",
            "--question",
            question,
            "--json",
            "--root",
            str(target),
        ],
    )

    assert result.exit_code == 0
    assert dummy_key not in result.stdout

    data = json.loads(result.stdout)
    assert "[REDACTED_SECRET]" in data["request_summary"]["question"]
    assert data["privacy_summary"]["redactions_count"] > 0


def test_cli_context_uninitialized_project(tmp_path: Path) -> None:
    """Test running context command on uninitialized directory fails."""
    uninit = tmp_path / "empty_dir"
    uninit.mkdir()

    result = runner.invoke(
        app,
        ["context", "--question", "hello", "--root", str(uninit)],
    )
    assert result.exit_code == 1
    assert "No MSK project initialized" in result.stdout


def test_cli_context_no_arguments_error(tmp_path: Path) -> None:
    """Test running context command without query parameters fails."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(
        fixture_dir,
        target,
        ignore=shutil.ignore_patterns(".msk"),
    )
    run_init(root=target, force=True)

    result = runner.invoke(app, ["context", "--root", str(target)])
    assert result.exit_code == 1
    assert "Empty context request" in result.stdout


def test_cli_context_invalid_budget_error(tmp_path: Path) -> None:
    """Test running context command with zero budget fails."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(
        fixture_dir,
        target,
        ignore=shutil.ignore_patterns(".msk"),
    )
    run_init(root=target, force=True)

    result = runner.invoke(
        app,
        [
            "context",
            "--file",
            "src/payment/service.py",
            "--budget",
            "0",
            "--root",
            str(target),
        ],
    )
    assert result.exit_code == 1
    assert "Invalid context budget" in result.stdout
