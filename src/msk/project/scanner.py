"""Streaming project filesystem scanner with boundary safety."""

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from msk.common.paths import is_within_root, to_relative_posix
from msk.project.ignore import IgnoreEngine


class ScannedFile(NamedTuple):
    """Metadata representing a scanned source or configuration file."""

    relative_path: str
    absolute_path: Path
    size_bytes: int
    modified_at: str


def is_binary_file(path: Path) -> bool:
    """Detect whether a file is binary by sniffing for null bytes in the first 1KB."""
    try:
        with path.open("rb") as f:
            chunk = f.read(1024)
            return b"\x00" in chunk
    except OSError:
        return True


class ProjectScanner:
    """Traverses and yields files within project root safely."""

    def __init__(
        self,
        root: str | Path,
        ignore_engine: IgnoreEngine | None = None,
        max_file_size_bytes: int = 10 * 1024 * 1024,  # 10MB limit per file by default
    ) -> None:
        self.root = Path(root).resolve()
        self.ignore_engine = ignore_engine or IgnoreEngine(self.root)
        self.max_file_size_bytes = max_file_size_bytes

    def scan(self) -> Iterator[ScannedFile]:
        """Stream files in project root, applying ignore rules and boundary checks."""
        visited_dirs: set[Path] = set()

        for current_dir, dirnames, filenames in self.root.walk():
            # Resolve directory to handle symlinks safely if symlinked
            if current_dir.is_symlink():
                try:
                    resolved_dir = current_dir.resolve()
                    if not is_within_root(resolved_dir, self.root):
                        dirnames.clear()
                        continue
                    if resolved_dir in visited_dirs:
                        dirnames.clear()
                        continue
                    visited_dirs.add(resolved_dir)
                except OSError:
                    dirnames.clear()
                    continue
            else:
                if current_dir in visited_dirs:
                    dirnames.clear()
                    continue
                visited_dirs.add(current_dir)

            # Filter out ignored or unsafe directories in-place
            filtered_dirs = []
            for d in dirnames:
                d_path = current_dir / d
                if self.ignore_engine.is_ignored(d_path, is_dir=True):
                    continue
                if d_path.is_symlink():
                    try:
                        if not is_within_root(d_path.resolve(), self.root):
                            continue
                    except OSError:
                        continue
                filtered_dirs.append(d)
            dirnames[:] = filtered_dirs

            for filename in filenames:
                file_path = current_dir / filename

                # Filter ignored files
                if self.ignore_engine.is_ignored(file_path, is_dir=False):
                    continue

                # Symlink safety check: only resolve if it is a symlink
                if file_path.is_symlink():
                    try:
                        resolved_file = file_path.resolve()
                        if not is_within_root(resolved_file, self.root):
                            continue
                    except OSError:
                        continue

                # Stat file
                try:
                    stat = file_path.stat()
                except OSError:
                    continue

                # Skip files exceeding size threshold
                if stat.st_size > self.max_file_size_bytes:
                    continue

                # Skip binary files
                if is_binary_file(file_path):
                    continue

                rel_posix = to_relative_posix(file_path, self.root)
                mtime_iso = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

                yield ScannedFile(
                    relative_path=rel_posix,
                    absolute_path=file_path,
                    size_bytes=stat.st_size,
                    modified_at=mtime_iso,
                )
