"""Configuration loader and saver for MSK."""

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from msk.common.errors import MSKError
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
        f.write(json.dumps(config.model_dump(), indent=2))


def validate_policy_dict(data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate a policy dictionary against PolicyConfig schema."""
    errors: list[str] = []
    try:
        PolicyConfig.model_validate(data)
        return (True, [])
    except ValidationError as exc:
        for err in exc.errors():
            loc = " -> ".join(str(part) for part in err["loc"])
            msg = err["msg"]
            errors.append(f"Field '{loc}': {msg}")
        return (False, errors)
    except Exception as exc:
        errors.append(f"Unexpected policy validation error: {exc}")
        return (False, errors)


def validate_policy_file(path: str | Path) -> tuple[bool, list[str]]:
    """Validate a policy JSON file against PolicyConfig schema."""
    p = Path(path)
    if not p.is_file():
        return (False, [f"Policy file not found: {p}"])
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        return (False, [f"Invalid JSON in policy file: {exc}"])
    except Exception as exc:
        return (False, [f"Cannot read policy file: {exc}"])

    if not isinstance(data, dict):
        return (False, ["Policy root must be a JSON object"])

    return validate_policy_dict(data)


def load_policy_config(project_root: str | Path, fallback_to_default: bool = False) -> PolicyConfig:
    """Load PolicyConfig from .msk/policy.json or return safe default policy.

    If the file exists and is malformed:
    - If fallback_to_default is True, returns safe default policy.
    - If fallback_to_default is False, raises MSKError with specific details.
    """
    path = get_policy_config_path(project_root)
    if not path.is_file():
        return PolicyConfig()

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return PolicyConfig.model_validate(data)
    except Exception as exc:
        if fallback_to_default:
            return PolicyConfig()
        raise MSKError(
            f"Failed to load policy configuration from '{path}': {exc}",
            hint="Check .msk/policy.json syntax or run 'msk policy validate'.",
        ) from exc


def save_policy_config(project_root: str | Path, policy: PolicyConfig) -> None:
    """Save PolicyConfig to .msk/policy.json."""
    msk_dir = get_msk_dir(project_root)
    msk_dir.mkdir(parents=True, exist_ok=True)
    path = get_policy_config_path(project_root)
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(policy.model_dump(), indent=2))
