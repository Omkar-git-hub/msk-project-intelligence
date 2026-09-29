"""Tests for imports and manifest dependency extraction."""

from pathlib import Path

from msk.analyzer.dependencies import parse_manifest_dependencies
from msk.analyzer.imports import extract_imports
from msk.analyzer.parser import parse_bytes


def test_python_import_extraction() -> None:
    code = b"""
import os
import sys
from payment.service import PaymentService, PaymentClient as PC
from .local_util import helper
"""
    tree = parse_bytes(code, "python")
    assert tree is not None
    imports = extract_imports(tree, "python", code)
    modules = {imp.module: imp for imp in imports}

    assert "os" in modules
    assert "sys" in modules
    assert "payment.service" in modules
    assert "PaymentService" in modules["payment.service"].names


def test_requirements_txt_parsing(tmp_path: Path) -> None:
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("requests>=2.31.0\npytest\n# comment\n\nflask==3.0.0\n", encoding="utf-8")

    deps = parse_manifest_dependencies(req_file, tmp_path)
    dep_map = {d.name: d for d in deps}

    assert "requests" in dep_map
    assert dep_map["requests"].version_spec == ">=2.31.0"
    assert "pytest" in dep_map
    assert "flask" in dep_map


def test_package_json_parsing(tmp_path: Path) -> None:
    pkg_file = tmp_path / "package.json"
    pkg_file.write_text(
        '{"dependencies": {"express": "^4.18.2"}, "devDependencies": {"typescript": "^5.0.0"}}',
        encoding="utf-8",
    )

    deps = parse_manifest_dependencies(pkg_file, tmp_path)
    dep_map = {d.name: d for d in deps}

    assert "express" in dep_map
    assert dep_map["express"].scope == "runtime"
    assert "typescript" in dep_map
    assert dep_map["typescript"].scope == "dev"
