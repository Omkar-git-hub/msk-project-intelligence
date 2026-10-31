"""Atomic JSON configuration manager for AI providers and models."""

import json
import logging
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from msk.common.errors import MSKError
from msk.config.loader import get_msk_dir
from msk.gateway.models import (
    AuthType,
    ModelConfig,
    ProviderConfig,
    ProvidersFileConfig,
    ProviderType,
)

logger = logging.getLogger("msk.gateway.configuration")


def get_providers_config_path(project_root: str | Path) -> Path:
    """Return primary path to .msk/config/providers.json with backward-compatible fallback."""
    msk_dir = get_msk_dir(project_root)
    primary_path = msk_dir / "config" / "providers.json"
    legacy_path = msk_dir / "providers.json"

    if not primary_path.is_file() and legacy_path.is_file():
        return legacy_path

    return primary_path


def get_default_providers_config() -> ProvidersFileConfig:
    """Return standard initial configuration with offline mock provider."""
    mock_model = ModelConfig(
        id="mock-model",
        display_name="Mock Local Simulation Model",
        context_window=8192,
    )
    mock_provider = ProviderConfig(
        provider_id="mock",
        display_name="Mock Offline Provider",
        provider_type=ProviderType.MOCK,
        endpoint="http://localhost:0",
        default_model="mock-model",
        models=[mock_model],
        auth_type=AuthType.NONE,
        enabled=True,
        is_default=True,
    )
    return ProvidersFileConfig(
        version="1.0.0",
        default_provider="mock",
        providers=[mock_provider],
    )


class ProviderConfigurationManager:
    """Manages loading, updating, and saving AI provider configurations."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.config_path = get_providers_config_path(self.project_root)

    def load_config(self) -> ProvidersFileConfig:
        """Load and validate providers configuration, returning defaults if not found."""
        if not self.config_path.is_file():
            return get_default_providers_config()

        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return ProvidersFileConfig.model_validate(data)
        except json.JSONDecodeError as exc:
            raise MSKError(
                f"Malformed JSON in providers configuration file at '{self.config_path}': {exc}",
                hint="Verify JSON syntax or delete the file to regenerate default provider settings.",
            ) from exc
        except ValidationError as exc:
            err_msgs = [f"{' -> '.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
            raise MSKError(
                f"Invalid provider configuration in '{self.config_path}': {'; '.join(err_msgs)}",
                hint="Check provider settings schema against required fields.",
            ) from exc
        except Exception as exc:
            raise MSKError(f"Failed to load provider configuration from '{self.config_path}': {exc}") from exc

    def save_config(self, config: ProvidersFileConfig) -> None:
        """Atomically persist providers configuration to disk."""
        target_dir = self.config_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)

        tmp_path = target_dir / f"{self.config_path.name}.tmp"
        try:
            payload = config.model_dump()
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, self.config_path)
        except Exception as exc:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise MSKError(f"Failed to atomically save provider configuration: {exc}") from exc

    def list_providers(self) -> list[ProviderConfig]:
        """Return all configured providers."""
        cfg = self.load_config()
        return cfg.providers

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        """Find a provider by ID."""
        cleaned_id = provider_id.strip().lower()
        cfg = self.load_config()
        for p in cfg.providers:
            if p.provider_id.lower() == cleaned_id:
                return p
        return None

    def get_default_provider(self) -> ProviderConfig:
        """Retrieve the active default provider."""
        cfg = self.load_config()
        # Find provider marked is_default or matching default_provider
        for p in cfg.providers:
            if p.is_default and p.enabled:
                return p
        for p in cfg.providers:
            if p.provider_id == cfg.default_provider and p.enabled:
                return p
        for p in cfg.providers:
            if p.enabled:
                return p
        if cfg.providers:
            return cfg.providers[0]

        # Return mock fallback if none exists
        default_cfg = get_default_providers_config()
        return default_cfg.providers[0]

    def add_provider(self, provider: ProviderConfig) -> None:
        """Add a new provider configuration."""
        cfg = self.load_config()
        if any(p.provider_id.lower() == provider.provider_id.lower() for p in cfg.providers):
            raise MSKError(
                f"Provider with ID '{provider.provider_id}' already exists.",
                hint=f"Use 'msk provider update {provider.provider_id}' to modify existing settings.",
            )

        if provider.is_default or not cfg.providers:
            for p in cfg.providers:
                p.is_default = False
            provider.is_default = True
            cfg.default_provider = provider.provider_id

        cfg.providers.append(provider)
        self.save_config(cfg)

    def update_provider(self, provider_id: str, updates: dict[str, Any]) -> ProviderConfig:
        """Update fields on an existing provider."""
        cfg = self.load_config()
        target: ProviderConfig | None = None
        for p in cfg.providers:
            if p.provider_id.lower() == provider_id.strip().lower():
                target = p
                break

        if target is None:
            raise MSKError(
                f"Provider '{provider_id}' not found.",
                hint="Run 'msk provider list' to view configured provider IDs.",
            )

        # Apply updates
        current_data = target.model_dump()
        current_data.update(updates)
        updated_provider = ProviderConfig.model_validate(current_data)

        # Replace in list
        new_list: list[ProviderConfig] = []
        for p in cfg.providers:
            if p.provider_id.lower() == provider_id.strip().lower():
                new_list.append(updated_provider)
            else:
                new_list.append(p)
        cfg.providers = new_list

        if updated_provider.is_default:
            for p in cfg.providers:
                if p.provider_id != updated_provider.provider_id:
                    p.is_default = False
            cfg.default_provider = updated_provider.provider_id

        self.save_config(cfg)
        return updated_provider

    def remove_provider(self, provider_id: str) -> bool:
        """Remove a provider by ID."""
        cfg = self.load_config()
        cleaned_id = provider_id.strip().lower()

        filtered = [p for p in cfg.providers if p.provider_id.lower() != cleaned_id]
        if len(filtered) == len(cfg.providers):
            raise MSKError(
                f"Cannot remove provider '{provider_id}': not found.",
                hint="Run 'msk provider list' to see active providers.",
            )

        cfg.providers = filtered

        # If we removed the default provider, elect a new one
        if cfg.default_provider.lower() == cleaned_id and cfg.providers:
            cfg.providers[0].is_default = True
            cfg.default_provider = cfg.providers[0].provider_id

        self.save_config(cfg)
        return True

    def enable_provider(self, provider_id: str) -> ProviderConfig:
        """Enable an existing provider."""
        return self.update_provider(provider_id, {"enabled": True})

    def disable_provider(self, provider_id: str) -> ProviderConfig:
        """Disable an existing provider."""
        return self.update_provider(provider_id, {"enabled": False})

    def set_default_provider(self, provider_id: str) -> ProviderConfig:
        """Set a provider as the default project AI provider."""
        cfg = self.load_config()
        target: ProviderConfig | None = None
        for p in cfg.providers:
            if p.provider_id.lower() == provider_id.strip().lower():
                target = p
                p.is_default = True
            else:
                p.is_default = False

        if target is None:
            raise MSKError(
                f"Cannot set default provider: '{provider_id}' not found.",
                hint="Run 'msk provider list' to view configured provider IDs.",
            )

        if not target.enabled:
            target.enabled = True

        cfg.default_provider = target.provider_id
        self.save_config(cfg)
        return target
