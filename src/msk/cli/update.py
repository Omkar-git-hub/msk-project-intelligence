"""Implementation of incremental 'msk update' command."""

import logging
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from msk.analyzer.analyzer import analyze_file
from msk.changes.engine import detect_changes
from msk.common.errors import NotAProjectError
from msk.config.loader import get_msk_dir, load_project_config, save_project_config
from msk.knowledge.repository import KnowledgeRepository
from msk.knowledge.serializer import export_from_repository
from msk.project.detector import find_project_root
from msk.project.scanner import ProjectScanner
from msk.storage.database import DatabaseManager

console = Console()
logger = logging.getLogger("msk.cli.update")


def run_update(root: Path | None = None, verbose: bool = False) -> None:
    """Incrementally update project knowledge based on detected file changes."""
    project_root = (root or find_project_root()).resolve()
    msk_dir = get_msk_dir(project_root)
    db_path = msk_dir / "msk.db"

    if not msk_dir.is_dir() or not db_path.is_file():
        raise NotAProjectError(
            f"No MSK project initialized at {project_root}",
            hint="Run 'msk init' first.",
        )

    config = load_project_config(project_root)
    if not config:
        raise NotAProjectError(
            f"MSK configuration missing at {msk_dir / 'project.json'}",
            hint="Run 'msk init --force' to recreate.",
        )

    db_manager = DatabaseManager(db_path)
    repo = KnowledgeRepository(db_manager)

    # 1. Fetch currently recorded files
    recorded_files = repo.get_all_files(config.id)

    # 2. Scan current workspace applying all ignore rules and safety constraints
    scanner = ProjectScanner(project_root)
    scanned_files = list(scanner.scan())

    # 3. Detect changes using SHA-256 content hashes (not modification times alone)
    change_set = detect_changes(scanned_files, recorded_files)

    if change_set.total_changes == 0:
        console.print("[bold green][OK] Everything up to date.[/bold green] No changes detected.")
        if verbose:
            _print_summary_table(
                added=0,
                modified=0,
                deleted=0,
                renamed=0,
                unchanged=len(change_set.unchanged),
                analyzed=0,
                errors=0,
            )
        return

    console.print(
        f"[bold cyan]Detected {change_set.total_changes} change(s):[/bold cyan] "
        f"[green]{len(change_set.added)} added[/green], "
        f"[yellow]{len(change_set.modified)} modified[/yellow], "
        f"[red]{len(change_set.deleted)} deleted[/red]"
        + (f", [blue]{len(change_set.renamed)} renamed[/blue]" if change_set.renamed else "")
    )

    # 4. Analyze ONLY added, modified, and renamed files
    files_to_analyze = change_set.added + change_set.modified + [new_sf for (_, new_sf) in change_set.renamed]
    deleted_paths = [rf.path for rf in change_set.deleted] + [old_rf.path for (old_rf, _) in change_set.renamed]

    analyzed_results = []
    errors_encountered = 0

    for sf in files_to_analyze:
        try:
            res = analyze_file(sf, project_root)
            analyzed_results.append(res)
        except Exception as exc:
            errors_encountered += 1
            logger.warning(f"Failed to analyze {sf.relative_path}: {exc}")

    # 5. Atomically update SQLite database and reconcile knowledge graph
    repo.apply_incremental_update(
        project_id=config.id,
        deleted_paths=deleted_paths,
        new_or_modified_results=analyzed_results,
    )

    # 6. Generate safe atomic JSON exports consistent with SQLite state
    export_from_repository(repo, config.id, msk_dir / "exports")

    # 7. Update timestamps
    now_iso = datetime.now(UTC).isoformat()
    config.updated_at = now_iso
    save_project_config(project_root, config)

    proj_entity = repo.get_project(config.id)
    if proj_entity:
        proj_entity.updated_at = now_iso
        repo.save_project(proj_entity)

    # 8. Report concise metrics summary
    _print_summary_table(
        added=len(change_set.added),
        modified=len(change_set.modified),
        deleted=len(change_set.deleted),
        renamed=len(change_set.renamed),
        unchanged=len(change_set.unchanged),
        analyzed=len(analyzed_results),
        errors=errors_encountered,
    )

    console.print("[bold green][OK] Knowledge model successfully updated.[/bold green]")


def _print_summary_table(
    added: int,
    modified: int,
    deleted: int,
    renamed: int,
    unchanged: int,
    analyzed: int,
    errors: int,
) -> None:
    """Print structured summary metrics table for msk update."""
    table = Table(title="MSK Update Summary", title_style="bold magenta", border_style="dim")
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Count", style="green")

    table.add_row("Files Added", str(added))
    table.add_row("Files Modified", str(modified))
    table.add_row("Files Deleted", str(deleted))
    if renamed > 0:
        table.add_row("Files Renamed", str(renamed))
    table.add_row("Files Unchanged", str(unchanged))
    table.add_row("Files Analyzed", str(analyzed))
    if errors > 0:
        table.add_row("Errors Encountered", f"[bold red]{errors}[/bold red]")
    else:
        table.add_row("Errors Encountered", str(errors))

    console.print(table)
