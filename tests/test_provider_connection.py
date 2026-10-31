"""Unit tests for the 6-stage provider connection test engine and CLI."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from msk.cli.main import app
from msk.config.loader import get_msk_dir
from msk.gateway.configuration import ProviderConfigurationManager
from msk.gateway.connection_test import ConnectionTestRunner, StageStatus
from msk.gateway.models import AuthType, ProviderConfig, ProviderType

runner = CliRunner()


def _init_dummy_project(tmp_path: Path) -> Path:
    msk_dir = get_msk_dir(tmp_path)
    msk_dir.mkdir(parents=True, exist_ok=True)
    (msk_dir / "project.json").write_text(
        '{"id": "p-test", "name": "dummy", "root": "' + str(tmp_path).replace("\\", "/") + '"}',
        encoding="utf-8",
    )
    (msk_dir / "msk.db").touch()
    return tmp_path


def test_mock_provider_connection_test():
    """Verify mock provider passes all diagnostic stages successfully."""
    test_runner = ConnectionTestRunner()
    cfg = ProviderConfig(
        provider_id="mock",
        display_name="Mock Provider",
        provider_type=ProviderType.MOCK,
        endpoint="http://localhost:0",
        default_model="mock-model",
        auth_type=AuthType.NONE,
        enabled=True,
    )

    result = test_runner.run_test(cfg)
    assert result.overall == "SUCCESS"
    assert result.stages["configuration"].status == StageStatus.PASSED
    assert result.stages["endpoint"].status == StageStatus.PASSED
    assert result.stages["authentication"].status == StageStatus.PASSED
    assert result.stages["model_availability"].status == StageStatus.PASSED
    assert result.stages["test_inference"].status == StageStatus.PASSED
    assert result.latency_ms >= 0


def test_missing_credentials_fails_auth_stage(monkeypatch):
    """Verify provider with unresolvable credentials fails at authentication stage."""
    test_runner = ConnectionTestRunner()
    cfg = ProviderConfig(
        provider_id="cloud-openai",
        display_name="OpenAI Cloud",
        provider_type=ProviderType.LOCAL_OPENAI,
        endpoint="http://localhost:8000/v1",
        default_model="gpt-4o",
        auth_type=AuthType.BEARER,
        credential_ref="env:UNSET_OPENAI_API_KEY",
    )
    monkeypatch.delenv("UNSET_OPENAI_API_KEY", raising=False)

    # Patch endpoint check so it passes Stage 2
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    with patch("msk.gateway.connection_test._safe_urlopen", return_value=mock_resp):
        result = test_runner.run_test(cfg)
        assert result.overall == "FAILED"
        assert result.stages["configuration"].status == StageStatus.PASSED
        assert result.stages["endpoint"].status == StageStatus.PASSED
        assert result.stages["authentication"].status == StageStatus.FAIL
        assert "could not be resolved" in result.stages["authentication"].message


def test_invalid_endpoint_fails_config_stage():
    """Verify bad endpoint scheme fails configuration stage."""
    test_runner = ConnectionTestRunner()
    cfg = ProviderConfig(
        provider_id="bad-ep",
        display_name="Bad Endpoint",
        provider_type=ProviderType.MOCK,
        endpoint="http://",
        default_model="m1",
    )
    # Force bad endpoint without pydantic error
    cfg.endpoint = "ftp://localhost"

    result = test_runner.run_test(cfg)
    assert result.overall == "FAILED"
    assert result.stages["configuration"].status == StageStatus.FAIL


def test_cli_provider_test_mock(tmp_path: Path):
    """Verify 'msk provider test' CLI output on default mock provider."""
    _init_dummy_project(tmp_path)

    result = runner.invoke(app, ["provider", "test", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "MSK Provider Connection Test" in result.output
    assert "Configuration" in result.output
    assert "Endpoint connectivity" in result.output
    assert "Authentication" in result.output
    assert "Test inference" in result.output
    assert "Overall: SUCCESS" in result.output


def test_cli_provider_test_specific_id_and_model(tmp_path: Path):
    """Verify 'msk provider test <id> --model <model>'."""
    _init_dummy_project(tmp_path)

    result = runner.invoke(app, ["provider", "test", "mock", "--model", "custom-model", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "custom-model" in result.output
    assert "Overall: SUCCESS" in result.output


def test_cli_provider_test_failure_exit_code(tmp_path: Path, monkeypatch):
    """Verify 'msk provider test' returns exit code 1 when a test fails."""
    _init_dummy_project(tmp_path)
    mgr = ProviderConfigurationManager(tmp_path)
    mgr.add_provider(
        ProviderConfig(
            provider_id="failing-provider",
            display_name="Failing Provider",
            provider_type=ProviderType.LOCAL_OPENAI,
            endpoint="http://localhost:9999/v1",
            default_model="m1",
            auth_type=AuthType.BEARER,
            credential_ref="env:MISSING_KEY",
        )
    )
    monkeypatch.delenv("MISSING_KEY", raising=False)

    result = runner.invoke(app, ["provider", "test", "failing-provider", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "Overall: FAILED" in result.output
