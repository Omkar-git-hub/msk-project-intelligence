"""Configuration loader and saver for MSK."""

import json
from pathlib import Path

from msk.config.models import PolicyConfig, ProjectConfig


def get_msk_dir(project_root: str | Path) -> Path:
    """Return the .msk directory path for a project root."""
    return Path(project_root) / ".msk"


def get_project_config_path(project_root: str | Path) -> Path:
    """Path to .msk/project.json."""
    return get_msk_dir(project_root) / "project.json"


def get_policy_config_path(project_root: str | Path) -> Path:
    """Path to .msk/policy.json."""
    return get_msk_dir(project_root) / "policy.json"


def load_project_config(project_root: str | Path) -> ProjectConfig | None:
    """Load ProjectConfig from .msk/project.json if it exists."""
    path = get_project_config_path(project_root)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return ProjectConfig.model_validate(data)


def save_project_config(project_root: str | Path, config: ProjectConfig) -> None:
    """Save ProjectConfig to .msk/project.json."""
    msk_dir = get_msk_dir(project_root)
    msk_dir.mkdir(parents=True, exist_ok=True)
    path = get_project_config_path(project_root)
    with path.open("w", encoding="utf-8") as f:
        json.dump(config.model_dump(), f, indent=2)


def load_policy_config(project_root: str | Path) -> PolicyConfig:
    """Load PolicyConfig from .msk/policy.json or return default policy."""
    path = get_policy_config_path(project_root)
    if not path.is_file():
        return PolicyConfig()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return PolicyConfig.model_validate(data)


def save_policy_config(project_root: str | Path, policy: PolicyConfig) -> None:
    """Save PolicyConfig to .msk/policy.json."""
    msk_dir = get_msk_dir(project_root)
    msk_dir.mkdir(parents=True, exist_ok=True)
    path = get_policy_config_path(project_root)
    with path.open("w", encoding="utf-8") as f:
        json.dump(policy.model_dump(), f, indent=2)
