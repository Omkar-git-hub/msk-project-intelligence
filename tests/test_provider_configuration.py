"""Unit tests for ProviderConfigurationManager."""

from pathlib import Path

import pytest

from msk.common.errors import MSKError
from msk.gateway.configuration import ProviderConfigurationManager
from msk.gateway.models import (
    AuthType,
    ModelConfig,
    ProviderConfig,
    ProviderType,
)


def test_load_default_config_when_missing(tmp_path: Path):
    """Verify loading configuration when file does not exist returns default mock config."""
    mgr = ProviderConfigurationManager(tmp_path)
    cfg = mgr.load_config()

    assert cfg.default_provider == "mock"
    assert len(cfg.providers) == 1
    assert cfg.providers[0].provider_id == "mock"
    assert cfg.providers[0].enabled


def test_add_and_list_providers(tmp_path: Path):
    """Verify adding new providers and listing them."""
    mgr = ProviderConfigurationManager(tmp_path)

    new_p = ProviderConfig(
        provider_id="local-ollama",
        display_name="Local Ollama",
        provider_type=ProviderType.OLLAMA,
        endpoint="http://localhost:11434",
        default_model="llama3",
        models=[ModelConfig(id="llama3")],
        auth_type=AuthType.NONE,
    )
    mgr.add_provider(new_p)

    providers = mgr.list_providers()
    assert len(providers) == 2
    assert any(p.provider_id == "local-ollama" for p in providers)

    # Adding duplicate ID must fail
    with pytest.raises(MSKError) as exc:
        mgr.add_provider(new_p)
    assert "already exists" in str(exc.value)


def test_update_provider(tmp_path: Path):
    """Verify updating provider configuration fields."""
    mgr = ProviderConfigurationManager(tmp_path)

    p = ProviderConfig(
        provider_id="custom-ai",
        display_name="Custom AI",
        provider_type=ProviderType.CUSTOM_OPENAI,
        endpoint="http://localhost:8000/v1",
        default_model="m1",
        timeout_seconds=30.0,
    )
    mgr.add_provider(p)

    updated = mgr.update_provider("custom-ai", {"endpoint": "http://localhost:9000/v1", "timeout_seconds": 60.0})
    assert updated.endpoint == "http://localhost:9000/v1"
    assert updated.timeout_seconds == 60.0

    retrieved = mgr.get_provider("custom-ai")
    assert retrieved is not None
    assert retrieved.endpoint == "http://localhost:9000/v1"

    # Updating unknown provider raises error
    with pytest.raises(MSKError):
        mgr.update_provider("nonexistent", {"timeout_seconds": 10.0})


def test_enable_and_disable_provider(tmp_path: Path):
    """Verify enabling and disabling providers."""
    mgr = ProviderConfigurationManager(tmp_path)
    mgr.disable_provider("mock")

    mock_p = mgr.get_provider("mock")
    assert mock_p is not None
    assert not mock_p.enabled

    mgr.enable_provider("mock")
    mock_p = mgr.get_provider("mock")
    assert mock_p is not None
    assert mock_p.enabled


def test_set_default_provider(tmp_path: Path):
    """Verify switching default provider updates flags across all providers."""
    mgr = ProviderConfigurationManager(tmp_path)

    new_p = ProviderConfig(
        provider_id="second-p",
        display_name="Second",
        provider_type=ProviderType.MOCK,
        endpoint="http://localhost:0",
        default_model="m2",
    )
    mgr.add_provider(new_p)

    mgr.set_default_provider("second-p")
    default_p = mgr.get_default_provider()
    assert default_p.provider_id == "second-p"
    assert default_p.is_default

    # Verify previous default was unset
    mock_p = mgr.get_provider("mock")
    assert mock_p is not None
    assert not mock_p.is_default


def test_remove_provider(tmp_path: Path):
    """Verify removing a provider."""
    mgr = ProviderConfigurationManager(tmp_path)

    new_p = ProviderConfig(
        provider_id="to-remove",
        display_name="Removable",
        provider_type=ProviderType.MOCK,
        endpoint="http://localhost:0",
        default_model="m1",
    )
    mgr.add_provider(new_p)
    assert mgr.get_provider("to-remove") is not None

    mgr.remove_provider("to-remove")
    assert mgr.get_provider("to-remove") is None

    # Removing non-existent provider raises error
    with pytest.raises(MSKError):
        mgr.remove_provider("to-remove")
