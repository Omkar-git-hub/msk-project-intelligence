"""Ignore rule engine supporting .gitignore, .mskignore, and built-in rules."""

from pathlib import Path

import pathspec

DEFAULT_IGNORE_PATTERNS = [
    # VCS & MSK
    ".git/",
    ".msk/",
    ".hg/",
    ".svn/",
    # Dependencies & Package managers
    "node_modules/",
    ".venv/",
    "venv/",
    "ENV/",
    "env/",
    "vendor/",
    # Build artifacts
    "target/",
    "build/",
    "dist/",
    "out/",
    "bin/",
    "obj/",
    "*.egg-info/",
    # Cache and temporary files
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".coverage",
    "htmlcov/",
    ".cache/",
    "tmp/",
    "temp/",
    "*.tmp",
    # IDEs
    ".idea/",
    ".vscode/",
    "*.swp",
    "*.swo",
    # Known binary / media extensions
    "*.exe",
    "*.dll",
    "*.so",
    "*.dylib",
    "*.class",
    "*.jar",
    "*.war",
    "*.ear",
    "*.zip",
    "*.tar",
    "*.tar.gz",
    "*.tgz",
    "*.7z",
    "*.iso",
    "*.bin",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.svg",
    "*.webp",
    "*.pdf",
    "*.mp3",
    "*.mp4",
    "*.wav",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
]


class IgnoreEngine:
    """Evaluates whether paths should be ignored based on gitignore specs."""

    def __init__(self, root: str | Path, extra_patterns: list[str] | None = None) -> None:
        self.root = Path(root).resolve()
        patterns = list(DEFAULT_IGNORE_PATTERNS)

        # Load .gitignore if present
        gitignore_path = self.root / ".gitignore"
        if gitignore_path.is_file():
            patterns.extend(self._read_pattern_file(gitignore_path))

        # Load .mskignore if present
        mskignore_path = self.root / ".mskignore"
        if mskignore_path.is_file():
            patterns.extend(self._read_pattern_file(mskignore_path))

        if extra_patterns:
            patterns.extend(extra_patterns)

        self.spec = pathspec.PathSpec.from_lines("gitignore", patterns)

    @staticmethod
    def _read_pattern_file(file_path: Path) -> list[str]:
        try:
            with file_path.open("r", encoding="utf-8", errors="ignore") as f:
                return [line.strip() for line in f if line.strip() and not line.startswith("#")]
        except OSError:
            return []

    def is_ignored(self, path: str | Path, is_dir: bool = False) -> bool:
        """Check if path relative to project root matches ignore patterns."""
        abs_path = Path(path).resolve()
        try:
            rel_path = abs_path.relative_to(self.root).as_posix()
        except ValueError:
            return True

        if is_dir and not rel_path.endswith("/"):
            rel_path += "/"

        return bool(self.spec.match_file(rel_path))
