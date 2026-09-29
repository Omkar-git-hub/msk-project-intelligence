"""Implementation of 'msk status' command."""

from datetime import datetime, timezone
from pathlib import Path
from rich.console import Console

from msk.changes.git import get_git_state
from msk.common.errors import NotAProjectError
from msk.config.loader import get_msk_dir, load_project_config
from msk.knowledge.repository import KnowledgeRepository
from msk.project.detector import find_project_root
from msk.storage.database import DatabaseManager

console = Console()


def _format_time_ago(iso_timestamp: str) -> str:
    """Format an ISO timestamp into a human-friendly relative string."""
    try:
        dt = datetime.fromisoformat(iso_timestamp)
        now = datetime.now(timezone.utc)
        diff_secs = int((now - dt).total_seconds())

        if diff_secs < 60:
            return f"{max(diff_secs, 1)} seconds ago"
        diff_mins = diff_secs // 60
        if diff_mins < 60:
            return f"{diff_mins} minute{'s' if diff_mins != 1 else ''} ago"
        diff_hours = diff_mins // 60
        if diff_hours < 24:
            return f"{diff_hours} hour{'s' if diff_hours != 1 else ''} ago"
        diff_days = diff_hours // 24
        return f"{diff_days} day{'s' if diff_days != 1 else ''} ago"
    except Exception:
        return iso_timestamp


def run_status(root: Path | None = None) -> None:
    """Display project status and knowledge model metrics."""
    project_root = (root or find_project_root()).resolve()
    msk_dir = get_msk_dir(project_root)
    db_path = msk_dir / "msk.db"

    if not msk_dir.is_dir() or not db_path.is_file():
        raise NotAProjectError(
            f"No MSK project initialized at {project_root}",
            hint="Run 'msk init' to initialize project intelligence.",
        )

    config = load_project_config(project_root)
    if not config:
        raise NotAProjectError(
            f"MSK configuration missing at {msk_dir / 'project.json'}",
            hint="Run 'msk init --force' to recreate configuration.",
        )

    db_manager = DatabaseManager(db_path)
    repo = KnowledgeRepository(db_manager)
    summary = repo.get_summary(config.id)
    git_state = get_git_state(project_root)

    # Format output according to MSK spec
    console.print()
    console.print("[bold]MSK Project[/bold]")
    console.print("----------------------------")
    console.print()
    console.print(f"Project: [bold cyan]{summary.project_name}[/bold cyan]")
    console.print()
    console.print(f"Files:          [bold green]{summary.total_files:,}[/bold green]")
    console.print(f"Languages:      [bold green]{summary.languages_count}[/bold green]")
    console.print(f"Symbols:        [bold green]{summary.total_symbols:,}[/bold green]")
    console.print(f"Dependencies:   [bold green]{summary.total_dependencies:,}[/bold green]")
    console.print(f"Relationships:  [bold green]{summary.total_relationships:,}[/bold green]")
    console.print()

    console.print("Git:")
    if git_state.is_repo:
        console.print(f"  Branch: [cyan]{git_state.branch or 'detached'}[/cyan]")
        console.print(f"  Modified files: [yellow]{len(git_state.modified_files)}[/yellow]")
    else:
        console.print("  [dim]Not a git repository[/dim]")
    console.print()

    console.print("Knowledge:")
    console.print(f"  Last updated: [dim]{_format_time_ago(summary.last_updated)}[/dim]")
    console.print()

    console.print("Security:")
    console.print(f"  Secrets detected: [green]{summary.secrets_detected}[/green]")
    console.print(f"  Sensitive files:  [green]{summary.sensitive_files}[/green]")
    console.print()

    status_color = "bold green" if summary.status == "READY" else "bold yellow"
    console.print(f"Status: [{status_color}]{summary.status}[/{status_color}]")
    console.print()
