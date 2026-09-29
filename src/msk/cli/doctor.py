"""Implementation of 'msk doctor' diagnostic command."""

import platform
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from msk.analyzer.parser import get_parser
from msk.config.loader import get_msk_dir
from msk.project.detector import find_project_root

console = Console()


def run_doctor(root: Path | None = None) -> bool:
    """Run diagnostic checks on the environment and current project."""
    project_root = (root or find_project_root()).resolve()
    msk_dir = get_msk_dir(project_root)
    db_path = msk_dir / "msk.db"

    table = Table(title="MSK System Doctor", title_style="bold cyan", border_style="dim")
    table.add_column("Component", style="bold")
    table.add_column("Status", no_wrap=True)
    table.add_column("Details")

    all_ok = True

    # 1. Python runtime
    py_version = sys.version.split()[0]
    py_major, py_minor = sys.version_info[:2]
    if (py_major, py_minor) >= (3, 11):
        table.add_row("Python", "[bold green]PASS[/bold green]", f"{py_version} ({sys.executable})")
    else:
        all_ok = False
        table.add_row("Python", "[bold red]FAIL[/bold red]", f"{py_version} (Requires Python >= 3.11)")

    # 2. Operating System
    table.add_row("OS Platform", "[bold green]PASS[/bold green]", f"{platform.system()} {platform.release()} ({platform.machine()})")

    # 3. Tree-sitter Parsers
    target_languages = ["python", "java", "javascript", "typescript"]
    available_parsers = []
    missing_parsers = []
    for lang in target_languages:
        if get_parser(lang) is not None:
            available_parsers.append(lang)
        else:
            missing_parsers.append(lang)

    if not missing_parsers:
        table.add_row("Tree-sitter AST", "[bold green]PASS[/bold green]", f"Languages ready: {', '.join(available_parsers)}")
    else:
        table.add_row(
            "Tree-sitter AST",
            "[bold yellow]WARN[/bold yellow]",
            f"Available: {', '.join(available_parsers)} | Missing: {', '.join(missing_parsers)}",
        )

    # 4. Git CLI
    git_bin = shutil.which("git")
    if git_bin:
        try:
            ver = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False).stdout.strip()
            table.add_row("Git", "[bold green]PASS[/bold green]", ver)
        except Exception:
            table.add_row("Git", "[bold yellow]WARN[/bold yellow]", f"Git found at {git_bin} but could not get version")
    else:
        table.add_row("Git", "[bold yellow]WARN[/bold yellow]", "Git not found on PATH (Git features will be disabled)")

    # 5. Project Directory & Permissions
    is_writable = False
    try:
        test_file = project_root / ".msk_test_write.tmp"
        test_file.touch()
        test_file.unlink()
        is_writable = True
    except OSError:
        is_writable = False

    if is_writable:
        table.add_row("Filesystem", "[bold green]PASS[/bold green]", f"Write access verified at {project_root}")
    else:
        all_ok = False
        table.add_row("Filesystem", "[bold red]FAIL[/bold red]", f"Cannot write to {project_root}")

    # 6. MSK Project State
    if msk_dir.is_dir() and db_path.is_file():
        # Check SQLite integrity
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            result = cursor.execute("PRAGMA integrity_check;").fetchone()
            conn.close()
            if result and result[0] == "ok":
                table.add_row("MSK Database", "[bold green]PASS[/bold green]", f"SQLite integrity OK ({db_path})")
            else:
                table.add_row("MSK Database", "[bold red]FAIL[/bold red]", f"Corrupted: {result}")
                all_ok = False
        except Exception as exc:
            table.add_row("MSK Database", "[bold red]FAIL[/bold red]", f"Error opening database: {exc}")
            all_ok = False
    else:
        table.add_row("MSK Database", "[bold yellow]INFO[/bold yellow]", "Not initialized (run 'msk init')")

    console.print()
    console.print(table)
    console.print()

    if all_ok:
        console.print("[bold green]System check passed. Environment is ready for MSK.[/bold green]")
    else:
        console.print("[bold yellow]Some checks failed or require attention.[/bold yellow]")

    return all_ok
