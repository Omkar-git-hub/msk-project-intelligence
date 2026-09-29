"""Project metadata extraction."""

import json
from pathlib import Path
import re
import tomllib
from typing import Any

from msk.project.detector import detect_ecosystems


def determine_project_name(root: str | Path) -> str:
    """Infer project name from package manifests or directory name."""
    root_path = Path(root).resolve()

    # 1. Check pyproject.toml
    pyproject_path = root_path / "pyproject.toml"
    if pyproject_path.is_file():
        try:
            with pyproject_path.open("rb") as f:
                data = tomllib.load(f)
                name = data.get("project", {}).get("name")
                if name:
                    return str(name)
        except Exception:
            pass

    # 2. Check package.json
    pkg_json = root_path / "package.json"
    if pkg_json.is_file():
        try:
            with pkg_json.open("r", encoding="utf-8") as f:
                data = json.load(f)
                name = data.get("name")
                if name:
                    return str(name)
        except Exception:
            pass

    # 3. Check pom.xml
    pom_path = root_path / "pom.xml"
    if pom_path.is_file():
        try:
            content = pom_path.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r"<artifactId>([^<]+)</artifactId>", content)
            if match:
                return match.group(1).strip()
        except Exception:
            pass

    # Fallback to directory name
    return root_path.name or "msk-project"


def extract_project_metadata(root: str | Path) -> dict[str, Any]:
    """Gather high-level metadata about the project root."""
    root_path = Path(root).resolve()
    return {
        "name": determine_project_name(root_path),
        "root": str(root_path),
        "ecosystems": detect_ecosystems(root_path),
        "has_git": (root_path / ".git").exists(),
        "has_msk": (root_path / ".msk").is_dir(),
    }
