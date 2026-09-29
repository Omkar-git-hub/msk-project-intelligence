"""Safe native Git integration."""

import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple


class GitState(NamedTuple):
    """Git repository snapshot."""

    is_repo: bool
    branch: str | None
    commit_hash: str | None
    is_dirty: bool
    modified_files: list[str]


def is_git_available() -> bool:
    """Check if git executable exists on PATH."""
    return shutil.which("git") is not None


def get_git_state(project_root: str | Path) -> GitState:
    """Extract Git status safely via native subprocess calls."""
    root_path = Path(project_root).resolve()

    if not is_git_available():
        return GitState(is_repo=False, branch=None, commit_hash=None, is_dirty=False, modified_files=[])

    # Check if inside git work tree
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0 or res.stdout.strip() != "true":
            return GitState(is_repo=False, branch=None, commit_hash=None, is_dirty=False, modified_files=[])
    except Exception:
        return GitState(is_repo=False, branch=None, commit_hash=None, is_dirty=False, modified_files=[])

    # Get branch name
    branch: str | None = None
    try:
        res_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if res_branch.returncode == 0:
            branch = res_branch.stdout.strip() or None
    except Exception:
        pass

    # Get commit hash
    commit_hash: str | None = None
    try:
        res_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if res_commit.returncode == 0:
            commit_hash = res_commit.stdout.strip() or None
    except Exception:
        pass

    # Check modified files
    modified: list[str] = []
    is_dirty = False
    try:
        res_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(root_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if res_status.returncode == 0:
            lines = res_status.stdout.splitlines()
            for line in lines:
                line_str = line.strip()
                if line_str:
                    is_dirty = True
                    parts = line_str.split(maxsplit=1)
                    if len(parts) == 2:
                        modified.append(parts[1])
    except Exception:
        pass

    return GitState(
        is_repo=True,
        branch=branch,
        commit_hash=commit_hash,
        is_dirty=is_dirty,
        modified_files=modified,
    )
