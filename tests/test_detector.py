"""Tests for project detector and root discovery."""

from pathlib import Path

from msk.project.detector import detect_ecosystems, find_project_root
from msk.project.metadata import determine_project_name


def test_find_project_root_from_cwd() -> None:
    root = find_project_root()
    assert (root / "pyproject.toml").is_file()


def test_find_project_root_from_subdir(tmp_path: Path) -> None:
    # Create fake project root
    (tmp_path / "pyproject.toml").touch()
    sub_dir = tmp_path / "src" / "deep" / "nested"
    sub_dir.mkdir(parents=True)

    discovered = find_project_root(sub_dir)
    assert discovered == tmp_path


def test_detect_ecosystems(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").touch()
    (tmp_path / "package.json").touch()

    ecosystems = detect_ecosystems(tmp_path)
    assert "python" in ecosystems
    assert "node" in ecosystems
    assert "java_maven" not in ecosystems


def test_determine_project_name(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "cool-service"\n', encoding="utf-8")
    name = determine_project_name(tmp_path)
    assert name == "cool-service"
