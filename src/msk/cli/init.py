"""Implementation of 'msk init' command."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table

from msk.analyzer.analyzer import analyze_file
from msk.changes.git import get_git_state
from msk.common.errors import ProjectAlreadyInitializedError
from msk.config.loader import (
    get_msk_dir,
    get_project_config_path,
    load_policy_config,
    load_project_config,
    save_policy_config,
    save_project_config,
)
from msk.config.models import ProjectConfig
from msk.knowledge.builder import KnowledgeBuilder
from msk.knowledge.models import ProjectEntity
from msk.knowledge.repository import KnowledgeRepository
from msk.project.detector import find_project_root
from msk.project.metadata import determine_project_name
from msk.project.scanner import ProjectScanner
from msk.storage.database import DatabaseManager

console = Console()


def run_init(
    root: Path | None = None,
    force: bool = False,
    verbose: bool = False,
) -> None:
    """Execute local project initialization and analysis."""
    project_root = (root or find_project_root()).resolve()
    msk_dir = get_msk_dir(project_root)

    if msk_dir.is_dir() and not force:
        cfg = get_project_config_path(project_root)
        if cfg.is_file():
            raise ProjectAlreadyInitializedError(
                f"MSK project already exists at {project_root}",
                hint="Run 'msk update' to refresh, or 'msk init --force' to recreate.",
            )

    # 1. Create .msk directory structure
    msk_dir.mkdir(parents=True, exist_ok=True)
    (msk_dir / "cache").mkdir(parents=True, exist_ok=True)
    (msk_dir / "exports").mkdir(parents=True, exist_ok=True)

    # Ensure .msk/.gitignore ignores cache and temporary files
    msk_gitignore = msk_dir / ".gitignore"
    if not msk_gitignore.is_file():
        msk_gitignore.write_text("cache/\n*.tmp\n", encoding="utf-8")

    db_path = msk_dir / "msk.db"
    if force and db_path.is_file():
        db_path.unlink()

    # 2. Initialize project configuration and policy
    existing_cfg = load_project_config(project_root)
    proj_id = existing_cfg.id if (existing_cfg and not force) else str(uuid.uuid4())
    proj_name = determine_project_name(project_root)
    now_iso = datetime.now(UTC).isoformat()

    project_config = ProjectConfig(
        id=proj_id,
        name=proj_name,
        root=str(project_root),
        created_at=existing_cfg.created_at if (existing_cfg and not force) else now_iso,
        updated_at=now_iso,
    )
    save_project_config(project_root, project_config)
    save_policy_config(project_root, load_policy_config(project_root))

    # 3. Database initialization
    db_manager = DatabaseManager(db_path)
    repo = KnowledgeRepository(db_manager)

    project_entity = ProjectEntity(
        id=proj_id,
        name=proj_name,
        root_path=str(project_root),
        created_at=project_config.created_at,
        updated_at=now_iso,
    )
    repo.save_project(project_entity)
    repo.clear_project_data(proj_id)

    # 4. Scanning & Analysis
    scanner = ProjectScanner(project_root)
    scanned_files = list(scanner.scan())

    analysis_results = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"[cyan]Analyzing {len(scanned_files)} files...", total=len(scanned_files))
        for sf in scanned_files:
            res = analyze_file(sf, project_root)
            analysis_results.append(res)
            progress.advance(task)

    # 5. Build Knowledge Model
    builder = KnowledgeBuilder(proj_id, project_root, repo)
    builder.build_and_save(analysis_results, export_exports=True)

    # 6. Capture Git state
    git_state = get_git_state(project_root)

    # 7. Display Result Summary
    summary = repo.get_summary(proj_id)

    console.print()
    console.print(f"[bold green]Initialized MSK Project Intelligence[/bold green] in [bold]{project_root}[/bold]")
    console.print()

    table = Table(title=f"Project: {proj_name}", title_style="bold magenta", border_style="dim")
    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="green")

    table.add_row("Files Indexed", str(summary.total_files))
    table.add_row("Languages", f"{summary.languages_count} ({', '.join(summary.languages_breakdown.keys())})")
    table.add_row("Symbols Discovered", str(summary.total_symbols))
    table.add_row("External Dependencies", str(summary.total_dependencies))
    table.add_row("Knowledge Relationships", str(summary.total_relationships))
    if summary.total_tests:
        table.add_row("Tests Discovered", str(summary.total_tests))
    if summary.total_api_endpoints:
        table.add_row("API Endpoints", str(summary.total_api_endpoints))
    if summary.total_infrastructure:
        table.add_row("Infrastructure Configs", str(summary.total_infrastructure))
    if git_state.is_repo:
        table.add_row("Git Branch", git_state.branch or "detached")
        table.add_row("Git Clean", "Yes" if not git_state.is_dirty else f"No ({len(git_state.modified_files)} modified)")
    table.add_row("Knowledge Database", str(db_path.name))
    table.add_row("JSON Exports", ".msk/exports/")

    console.print(table)

    console.print()
    console.print("[dim]Run [bold]msk status[/bold] to inspect or [bold]msk doctor[/bold] to check system health.[/dim]")
