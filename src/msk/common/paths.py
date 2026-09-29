"""Path utilities and boundary safety for MSK."""

from pathlib import Path


def normalize_path(path: str | Path) -> Path:
    """Normalize path to a resolved Path object."""
    return Path(path).resolve()


def is_within_root(path: str | Path, root: str | Path) -> bool:
    """Check if `path` is contained within `root` without escaping."""
    resolved_path = Path(path).resolve()
    resolved_root = Path(root).resolve()
    try:
        resolved_path.relative_to(resolved_root)
        return True
    except ValueError:
        return False


def to_relative_posix(path: str | Path, root: str | Path) -> str:
    """Convert path to a root-relative posix string (e.g. 'src/app.py')."""
    resolved_path = Path(path).resolve()
    resolved_root = Path(root).resolve()
    rel = resolved_path.relative_to(resolved_root)
    return rel.as_posix()
