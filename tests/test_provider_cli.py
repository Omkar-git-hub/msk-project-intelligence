"""Unit and integration tests for 'msk provider' CLI commands."""

from pathlib import Path

from typer.testing import CliRunner

from msk.cli.main import app
from msk.config.loader import get_msk_dir
from msk.gateway.configuration import ProviderConfigurationManager
from msk.gateway.models import ProviderConfig, ProviderType

runner = CliRunner()


def _init_dummy_project(tmp_path: Path) -> Path:
    """Create a minimal .msk project structure in tmp_path."""
    msk_dir = get_msk_dir(tmp_path)
    msk_dir.mkdir(parents=True, exist_ok=True)
    (msk_dir / "project.json").write_text(
        '{"id": "p-test", "name": "dummy", "root": "' + str(tmp_path).replace("\\", "/") + '"}',
        encoding="utf-8",
    )
    (msk_dir / "msk.db").touch()
    return tmp_path


def test_provider_list_uninitialized_project(tmp_path: Path):
    """Verify provider list on an uninitialized project returns error."""
    result = runner.invoke(app, ["provider", "list", "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "No MSK project initialized" in result.output


def test_provider_list_initialized(tmp_path: Path):
    """Verify provider list displays configured providers table."""
    _init_dummy_project(tmp_path)

    result = runner.invoke(app, ["provider", "list", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "MSK Configured AI Providers" in result.output
    assert "mock" in result.output
    assert "ENABLED" in result.output


def test_provider_add_command(tmp_path: Path):
    """Verify non-interactive provider add command."""
    _init_dummy_project(tmp_path)

    result = runner.invoke(
        app,
        [
            "provider",
            "add",
            "--id",
            "local-ollama",
            "--name",
            "My Ollama",
            "--type",
            "ollama",
            "--endpoint",
            "http://localhost:11434",
            "--model",
            "llama3",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "Provider 'local-ollama' successfully added" in result.output

    # Verify it is listed
    mgr = ProviderConfigurationManager(tmp_path)
    p = mgr.get_provider("local-ollama")
    assert p is not None
    assert p.provider_type == ProviderType.OLLAMA


def test_provider_update_command(tmp_path: Path):
    """Verify provider update command modifies fields."""
    _init_dummy_project(tmp_path)

    mgr = ProviderConfigurationManager(tmp_path)
    mgr.add_provider(
        ProviderConfig(
            provider_id="updatable",
            display_name="Before",
            provider_type=ProviderType.MOCK,
            endpoint="http://localhost:0",
            default_model="m1",
        )
    )

    result = runner.invoke(
        app,
        [
            "provider",
            "update",
            "updatable",
            "--name",
            "After",
            "--timeout",
            "45",
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0
    assert "Provider 'updatable' successfully updated" in result.output

    updated = mgr.get_provider("updatable")
    assert updated.display_name == "After"
    assert updated.timeout_seconds == 45.0


def test_provider_enable_disable_command(tmp_path: Path):
    """Verify provider enable and disable commands."""
    _init_dummy_project(tmp_path)

    # Disable mock
    res_dis = runner.invoke(app, ["provider", "disable", "mock", "--root", str(tmp_path)])
    assert res_dis.exit_code == 0
    assert "disabled" in res_dis.output

    mgr = ProviderConfigurationManager(tmp_path)
    assert not mgr.get_provider("mock").enabled

    # Enable mock
    res_en = runner.invoke(app, ["provider", "enable", "mock", "--root", str(tmp_path)])
    assert res_en.exit_code == 0
    assert "enabled" in res_en.output
    assert mgr.get_provider("mock").enabled


def test_provider_set_default_command(tmp_path: Path):
    """Verify provider set-default command."""
    _init_dummy_project(tmp_path)

    mgr = ProviderConfigurationManager(tmp_path)
    mgr.add_provider(
        ProviderConfig(
            provider_id="second",
            display_name="Second",
            provider_type=ProviderType.MOCK,
            endpoint="http://localhost:0",
            default_model="m2",
        )
    )

    result = runner.invoke(app, ["provider", "set-default", "second", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "Default provider set to 'second'" in result.output

    assert mgr.get_default_provider().provider_id == "second"


def test_provider_remove_command(tmp_path: Path):
    """Verify provider remove command."""
    _init_dummy_project(tmp_path)

    mgr = ProviderConfigurationManager(tmp_path)
    mgr.add_provider(
        ProviderConfig(
            provider_id="delete-me",
            display_name="Delete Me",
            provider_type=ProviderType.MOCK,
            endpoint="http://localhost:0",
            default_model="m1",
        )
    )

    result = runner.invoke(app, ["provider", "remove", "delete-me", "--yes", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "Provider 'delete-me' removed" in result.output
    assert mgr.get_provider("delete-me") is None
