"""Security and privacy boundary verification tests."""

import logging
from pathlib import Path

from msk.cli.init import run_init
from msk.common.logging import SecretScrubbingFilter
from msk.project.scanner import ProjectScanner


def test_log_secret_scrubbing() -> None:
    scrubber = SecretScrubbingFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Connecting with api_key: 'super_secret_token_12345'",
        args=(),
        exc_info=None,
    )
    scrubber.filter(record)
    assert "super_secret_token_12345" not in record.msg
    assert "[REDACTED_SECRET]" in record.msg


def test_dot_git_and_dot_msk_never_scanned(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (tmp_path / ".msk").mkdir()
    (tmp_path / ".msk" / "msk.db").write_text("sqlite", encoding="utf-8")
    (tmp_path / "valid.py").write_text("x = 1", encoding="utf-8")

    scanner = ProjectScanner(tmp_path)
    files = list(scanner.scan())
    scanned_paths = [f.relative_path for f in files]

    assert "valid.py" in scanned_paths
    for p in scanned_paths:
        assert not p.startswith(".git")
        assert not p.startswith(".msk")


def test_no_source_code_copies_in_msk(tmp_path: Path) -> None:
    # Set up project with code
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "business_logic.py").write_text(
        "class ProprietaryAlgo:\n    def calculate(self):\n        return 42 * 99\n",
        encoding="utf-8",
    )

    run_init(root=tmp_path)

    # Check files in .msk
    msk_dir = tmp_path / ".msk"
    assert msk_dir.is_dir()

    # Read all text inside .msk exports and configs
    for json_file in msk_dir.rglob("*.json"):
        content = json_file.read_text(encoding="utf-8")
        # Ensure raw source code body is not dumped in JSON
        assert "42 * 99" not in content
