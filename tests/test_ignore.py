"""Tests for ignore engine."""

from pathlib import Path

from msk.project.ignore import IgnoreEngine


def test_default_ignores(tmp_path: Path) -> None:
    engine = IgnoreEngine(tmp_path)

    assert engine.is_ignored(tmp_path / ".git", is_dir=True) is True
    assert engine.is_ignored(tmp_path / ".git" / "config", is_dir=False) is True
    assert engine.is_ignored(tmp_path / ".msk" / "msk.db", is_dir=False) is True
    assert engine.is_ignored(tmp_path / "node_modules" / "express", is_dir=True) is True
    assert engine.is_ignored(tmp_path / "app.py", is_dir=False) is False


def test_custom_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("*.log\nsecrets/\n", encoding="utf-8")
    engine = IgnoreEngine(tmp_path)

    assert engine.is_ignored(tmp_path / "app.log") is True
    assert engine.is_ignored(tmp_path / "secrets" / "key.txt") is True
    assert engine.is_ignored(tmp_path / "main.py") is False


def test_mskignore(tmp_path: Path) -> None:
    (tmp_path / ".mskignore").write_text("internal_docs/\n", encoding="utf-8")
    engine = IgnoreEngine(tmp_path)

    assert engine.is_ignored(tmp_path / "internal_docs" / "arch.md") is True
