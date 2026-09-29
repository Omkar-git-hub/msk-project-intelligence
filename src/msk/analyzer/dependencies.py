"""Manifest and dependency parser for Python, Node, and Java."""

import json
import re
import tomllib
from pathlib import Path

from pydantic import BaseModel


class ProjectDependency(BaseModel):
    """External dependency declared in a package manifest."""

    name: str
    version_spec: str | None = None
    source_file: str
    ecosystem: str
    scope: str = "runtime"  # runtime, dev, test


def parse_manifest_dependencies(path: str | Path, root: str | Path) -> list[ProjectDependency]:
    """Parse dependencies from a recognized manifest file."""
    file_path = Path(path).resolve()
    rel_path = file_path.relative_to(Path(root).resolve()).as_posix()
    name = file_path.name

    if name == "requirements.txt" or name.endswith("-requirements.txt"):
        return _parse_requirements_txt(file_path, rel_path)

    if name == "pyproject.toml":
        return _parse_pyproject_toml(file_path, rel_path)

    if name == "package.json":
        return _parse_package_json(file_path, rel_path)

    if name == "pom.xml":
        return _parse_pom_xml(file_path, rel_path)

    return []


def _parse_requirements_txt(file_path: Path, rel_path: str) -> list[ProjectDependency]:
    deps: list[ProjectDependency] = []
    try:
        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                # Split name and version specs (e.g. requests>=2.31.0)
                match = re.match(r"^([a-zA-Z0-9_\-\.]+)(.*)$", line)
                if match:
                    pkg_name = match.group(1)
                    spec = match.group(2).strip() or None
                    deps.append(
                        ProjectDependency(
                            name=pkg_name,
                            version_spec=spec,
                            source_file=rel_path,
                            ecosystem="python",
                            scope="runtime",
                        )
                    )
    except OSError:
        pass
    return deps


def _parse_pyproject_toml(file_path: Path, rel_path: str) -> list[ProjectDependency]:
    deps: list[ProjectDependency] = []
    try:
        with file_path.open("rb") as f:
            data = tomllib.load(f)

        # Standard PEP 621 dependencies
        standard_deps = data.get("project", {}).get("dependencies", [])
        for dep in standard_deps:
            match = re.match(r"^([a-zA-Z0-9_\-\.]+)(.*)$", dep)
            if match:
                deps.append(
                    ProjectDependency(
                        name=match.group(1),
                        version_spec=match.group(2).strip() or None,
                        source_file=rel_path,
                        ecosystem="python",
                        scope="runtime",
                    )
                )

        # Optional / dev dependencies
        opt_deps = data.get("project", {}).get("optional-dependencies", {})
        for group, items in opt_deps.items():
            for dep in items:
                match = re.match(r"^([a-zA-Z0-9_\-\.]+)(.*)$", dep)
                if match:
                    deps.append(
                        ProjectDependency(
                            name=match.group(1),
                            version_spec=match.group(2).strip() or None,
                            source_file=rel_path,
                            ecosystem="python",
                            scope="dev" if group in ("dev", "test") else group,
                        )
                    )

        # Poetry dependencies
        poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
        for name, spec in poetry_deps.items():
            if name.lower() == "python":
                continue
            deps.append(
                ProjectDependency(
                    name=name,
                    version_spec=str(spec) if not isinstance(spec, dict) else spec.get("version"),
                    source_file=rel_path,
                    ecosystem="python",
                    scope="runtime",
                )
            )
    except Exception:
        pass
    return deps


def _parse_package_json(file_path: Path, rel_path: str) -> list[ProjectDependency]:
    deps: list[ProjectDependency] = []
    try:
        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        prod = data.get("dependencies", {})
        for name, spec in prod.items():
            deps.append(
                ProjectDependency(
                    name=name,
                    version_spec=str(spec),
                    source_file=rel_path,
                    ecosystem="node",
                    scope="runtime",
                )
            )

        dev = data.get("devDependencies", {})
        for name, spec in dev.items():
            deps.append(
                ProjectDependency(
                    name=name,
                    version_spec=str(spec),
                    source_file=rel_path,
                    ecosystem="node",
                    scope="dev",
                )
            )
    except Exception:
        pass
    return deps


def _parse_pom_xml(file_path: Path, rel_path: str) -> list[ProjectDependency]:
    deps: list[ProjectDependency] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        # Extract <dependency> blocks
        pattern = re.compile(r"<dependency>(.*?)</dependency>", re.DOTALL)
        for block in pattern.findall(content):
            group_match = re.search(r"<groupId>([^<]+)</groupId>", block)
            artifact_match = re.search(r"<artifactId>([^<]+)</artifactId>", block)
            version_match = re.search(r"<version>([^<]+)</version>", block)
            scope_match = re.search(r"<scope>([^<]+)</scope>", block)

            if artifact_match:
                group = group_match.group(1).strip() if group_match else ""
                artifact = artifact_match.group(1).strip()
                full_name = f"{group}:{artifact}" if group else artifact
                ver = version_match.group(1).strip() if version_match else None
                scope = scope_match.group(1).strip() if scope_match else "runtime"
                deps.append(
                    ProjectDependency(
                        name=full_name,
                        version_spec=ver,
                        source_file=rel_path,
                        ecosystem="java",
                        scope=scope,
                    )
                )
    except Exception:
        pass
    return deps
