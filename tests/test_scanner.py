"""Tests for project scanner and boundary safety."""

import os
from pathlib import Path

from msk.project.scanner import ProjectScanner, is_binary_file


def test_scanner_discovers_source_files(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "helper.js").write_text("console.log('hi');", encoding="utf-8")

    scanner = ProjectScanner(tmp_path)
    files = list(scanner.scan())
    paths = {f.relative_path for f in files}

    assert "main.py" in paths
    assert "sub/helper.js" in paths


def test_scanner_skips_binary_files(tmp_path: Path) -> None:
    # Binary file with null bytes
    binary_file = tmp_path / "image.bin"
    binary_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    assert is_binary_file(binary_file) is True

    scanner = ProjectScanner(tmp_path)
    files = list(scanner.scan())
    paths = {f.relative_path for f in files}

    assert "image.bin" not in paths


def test_scanner_prevents_escaping_project_root(tmp_path: Path) -> None:
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    secret_file = outside_dir / "secret.txt"
    secret_file.write_text("super_secret", encoding="utf-8")

    proj_dir = tmp_path / "project"
    proj_dir.mkdir()
    (proj_dir / "app.py").write_text("# code", encoding="utf-8")

    # Attempt symlink pointing outside
    try:
        symlink_path = proj_dir / "leak_link"
        os.symlink(outside_dir, symlink_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        # On Windows without developer mode/admin rights, symlink creation may require privilege
        return

    scanner = ProjectScanner(proj_dir)
    scanned_files = list(scanner.scan())
    paths = {f.relative_path for f in scanned_files}

    # Verify outside secret was NOT scanned
    for p in paths:
        assert "secret.txt" not in p
