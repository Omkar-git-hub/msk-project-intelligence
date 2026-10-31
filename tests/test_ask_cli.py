"""CLI integration tests for 'msk ask' command."""

import json
import re
import shutil
from pathlib import Path

from typer.testing import CliRunner

from msk.cli.init import run_init
from msk.cli.main import app
from msk.config.loader import load_policy_config, save_policy_config
from msk.config.models import ExternalLLMPolicy
from msk.gateway.configuration import ProviderConfigurationManager
from msk.gateway.models import ProviderConfig, ProviderType

runner = CliRunner()

ANSI_REGEX = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text for reliable assertions."""
    return ANSI_REGEX.sub("", text)


def _setup_initialized_project(tmp_path: Path) -> Path:
    """Copy fixture project and run init."""
    fixture_dir = Path("tests/fixtures/payment_service").resolve()
    target = tmp_path / "payment_service"
    shutil.copytree(fixture_dir, target, ignore=shutil.ignore_patterns(".msk"))
    run_init(root=target, force=True)
    return target


def test_ask_help():
    """Verify 'msk ask --help' displays parameters."""
    result = runner.invoke(
        app,
        ["ask", "--help"],
        color=False,
        env={"NO_COLOR": "1", "TERM": "dumb"},
    )
    assert result.exit_code == 0
    clean_stdout = strip_ansi(result.stdout)
    assert "Ask software engineering questions" in clean_stdout
    assert "--budget" in clean_stdout
    assert "--provider" in clean_stdout


def test_ask_uninitialized_project(tmp_path: Path):
    """Verify 'msk ask' fails on uninitialized directory."""
    result = runner.invoke(app, ["ask", "How does auth work?", "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "No MSK project initialized" in result.output


def test_ask_mock_provider_human_output(tmp_path: Path):
    """Verify 'msk ask' executes query with default mock provider."""
    target = _setup_initialized_project(tmp_path)

    result = runner.invoke(
        app,
        ["ask", "How does payment processing work?", "--root", str(target)],
    )
    assert result.exit_code == 0
    assert "MSK Project Assistant" in result.output
    assert "The project stays private." in result.output
    assert "Grounded Context Citations" in result.output


def test_ask_json_output(tmp_path: Path):
    """Verify 'msk ask --json' emits machine-readable payload."""
    target = _setup_initialized_project(tmp_path)

    result = runner.invoke(
        app,
        ["ask", "How does payment work?", "--json", "--root", str(target)],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "SUCCESS"
    assert data["provider"] == "mock"
    assert "answer" in data
    assert "The project stays private." in data["answer"]
    assert "cited_items" in data
    assert len(data["cited_items"]) > 0


def test_ask_external_blocked_by_policy(tmp_path: Path):
    """Verify 'msk ask --target external' is blocked when external AI is disabled."""
    target = _setup_initialized_project(tmp_path)

    policy = load_policy_config(target)
    policy.external_llm = ExternalLLMPolicy(enabled=False)
    save_policy_config(target, policy)

    result = runner.invoke(
        app,
        ["ask", "What is payment flow?", "--target", "external", "--root", str(target)],
    )
    assert result.exit_code == 1
    assert "AI Transmission Blocked by Policy" in result.output
    assert "The project stays private." in result.output


def test_ask_external_blocked_json_output(tmp_path: Path):
    """Verify 'msk ask --target external --json' emits DENIED status payload."""
    target = _setup_initialized_project(tmp_path)

    result = runner.invoke(
        app,
        ["ask", "Explain DB?", "--target", "external", "--json", "--root", str(target)],
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "DENIED"
    assert data["policy_decision"]["outcome"].lower() == "deny"


def test_ask_nonexistent_and_disabled_provider(tmp_path: Path):
    """Verify error handling for nonexistent or disabled providers."""
    target = _setup_initialized_project(tmp_path)

    # Nonexistent provider
    res_none = runner.invoke(app, ["ask", "hello", "--provider", "nonexistent-p", "--root", str(target)])
    assert res_none.exit_code == 1
    assert "Provider 'nonexistent-p' not found" in res_none.output

    # Disabled provider
    mgr = ProviderConfigurationManager(target)
    mgr.add_provider(
        ProviderConfig(
            provider_id="disabled-prov",
            display_name="Disabled",
            provider_type=ProviderType.MOCK,
            endpoint="http://localhost:0",
            default_model="m1",
            enabled=False,
        )
    )
    res_dis = runner.invoke(app, ["ask", "hello", "--provider", "disabled-prov", "--root", str(target)])
    assert res_dis.exit_code == 1
    assert "Provider 'disabled-prov' is disabled" in res_dis.output
