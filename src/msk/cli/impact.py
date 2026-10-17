"""Implementation of 'msk impact' CLI command."""

import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from msk.common.errors import MSKError, NotAProjectError
from msk.config.loader import get_msk_dir, load_project_config
from msk.impact.engine import ImpactEngine
from msk.impact.models import ImpactClassification, ImpactReport
from msk.knowledge.repository import KnowledgeRepository
from msk.project.detector import find_project_root
from msk.storage.database import DatabaseManager

console = Console()


def run_impact(
    file: Path | None = None,
    symbol: str | None = None,
    dependency: str | None = None,
    depth: int = 3,
    direct: bool = False,
    json_output: bool = False,
    root: Path | None = None,
) -> None:
    """Run impact analysis for a file, symbol, or dependency and present results."""
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

    # Validate that target parameter is provided
    if not file and not symbol and not dependency:
        raise MSKError(
            "Please specify a target to analyze: --file, --symbol, or --dependency.",
            hint="e.g. msk impact --file src/example.py or msk impact --symbol process_payment",
        )

    engine = ImpactEngine(repo, config.id, project_root)

    # Route query to corresponding engine analyzer
    report: ImpactReport
    if symbol:
        file_arg = str(file) if file else None
        report = engine.analyze_symbol(
            symbol_name=symbol,
            file_path=file_arg,
            max_depth=depth,
            direct_only=direct,
        )
    elif file:
        report = engine.analyze_file(
            file_path=str(file),
            max_depth=depth,
            direct_only=direct,
        )
    elif dependency:
        report = engine.analyze_dependency(
            dependency_name=dependency,
            max_depth=depth,
            direct_only=direct,
        )

    # Output Presentation
    if json_output:
        # Machine-readable output (raw JSON)
        print(json.dumps(report.model_dump(), indent=2))
        return

    _print_human_report(report)


def _print_human_report(report: ImpactReport) -> None:
    """Format and display human-readable impact report using Rich."""
    console.print()
    console.print("[bold]MSK Impact Analysis[/bold]")
    console.print("----------------------------------------")
    console.print(f"Target:         [bold cyan]{report.target_name}[/bold cyan] ({report.target_type})")
    if report.target_file and report.target_type != "file":
        console.print(f"File:           [dim]{report.target_file}[/dim]")
    scope_desc = "[green]Direct only (depth 1)[/green]" if report.max_depth == 1 else f"[yellow]Transitive (max depth {report.max_depth})[/yellow]"
    console.print(f"Scope:          {scope_desc}")
    console.print(
        f"Affected Items: [bold]{report.total_affected}[/bold] "
        f"([green]{report.direct_count} direct[/green], [yellow]{report.transitive_count} transitive[/yellow])"
    )
    console.print()

    if report.total_affected == 0:
        console.print(
            Panel(
                "[bold green][OK] No downstream affected items found.[/bold green]\n"
                "No other files, symbols, tests, or APIs in the project depend on this target.",
                border_style="green",
            )
        )
        console.print()
        return

    # 1. Main Impact Table
    table = Table(title="Downstream Impact Details", title_style="bold magenta", border_style="dim")
    table.add_column("Level", style="bold", no_wrap=True)
    table.add_column("Depth", justify="center", no_wrap=True)
    table.add_column("Type", style="cyan", no_wrap=True)
    table.add_column("Item", style="bold white")
    table.add_column("File Path", style="dim")
    table.add_column("Category", style="blue", no_wrap=True)
    table.add_column("Explanation / Path", style="white")

    for item in report.items:
        level_style = "[green]DIRECT[/green]" if item.classification == ImpactClassification.DIRECT else "[yellow]TRANSITIVE[/yellow]"
        path_str = " -> ".join(item.path) if len(item.path) > 1 else item.reason
        table.add_row(
            level_style,
            str(item.depth),
            item.type,
            item.name,
            item.file_path,
            str(item.category),
            path_str,
        )

    console.print(table)
    console.print()

    # 2. Affected Tests Section (if any tests are impacted)
    affected_tests = [i for i in report.items if i.type == "test"]
    if affected_tests:
        test_table = Table(title="Affected Tests (Recommended to Run)", title_style="bold green", border_style="green")
        test_table.add_column("Level", style="bold", no_wrap=True)
        test_table.add_column("Depth", justify="center", no_wrap=True)
        test_table.add_column("Test Name", style="bold green")
        test_table.add_column("Test File", style="dim")
        test_table.add_column("Reason")

        for t in affected_tests:
            lvl = "[green]DIRECT[/green]" if t.classification == ImpactClassification.DIRECT else "[yellow]TRANSITIVE[/yellow]"
            test_table.add_row(lvl, str(t.depth), t.name, t.file_path, t.reason)

        console.print(test_table)
        console.print()

    # 3. Affected API Endpoints Section (if any APIs are impacted)
    affected_apis = [i for i in report.items if i.type == "api_endpoint"]
    if affected_apis:
        api_table = Table(title="Affected API Endpoints", title_style="bold yellow", border_style="yellow")
        api_table.add_column("Level", style="bold", no_wrap=True)
        api_table.add_column("Depth", justify="center", no_wrap=True)
        api_table.add_column("Route & Method", style="bold yellow")
        api_table.add_column("File Path", style="dim")
        api_table.add_column("Reason")

        for a in affected_apis:
            lvl = "[green]DIRECT[/green]" if a.classification == ImpactClassification.DIRECT else "[yellow]TRANSITIVE[/yellow]"
            api_table.add_row(lvl, str(a.depth), a.name, a.file_path, a.reason)

        console.print(api_table)
        console.print()
