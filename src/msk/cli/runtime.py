"""CLI commands for MSK Local-First Runtime Intelligence."""

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from msk.common.errors import MSKError, NotAProjectError
from msk.config.loader import get_msk_dir, load_project_config
from msk.knowledge.repository import KnowledgeRepository
from msk.project.detector import find_project_root
from msk.runtime.engine import RuntimeIntelligenceEngine
from msk.storage.database import DatabaseManager

runtime_app = typer.Typer(
    name="runtime",
    help="MSK Local-First Runtime Intelligence, log analysis, and error correlation.",
    no_args_is_help=True,
)

console = Console()


def _get_engine(root: Path | None) -> tuple[RuntimeIntelligenceEngine, Path]:
    """Helper to initialize RuntimeIntelligenceEngine with project root verification."""
    project_root = (root or find_project_root()).resolve()
    msk_dir = get_msk_dir(project_root)
    db_path = msk_dir / "msk.db"

    if not msk_dir.is_dir() or not db_path.is_file():
        raise NotAProjectError(
            f"No MSK project initialized at {project_root}",
            hint="Run 'msk init' first to initialize project intelligence.",
        )

    project_cfg = load_project_config(project_root)
    project_name = project_cfg.name if project_cfg else "Unknown"
    project_id = project_cfg.id if project_cfg else "default"

    db = DatabaseManager(db_path)
    repo = KnowledgeRepository(db)
    engine = RuntimeIntelligenceEngine(
        db=db,
        repo=repo,
        project_id=project_id,
        project_root=project_root,
        project_name=project_name,
    )
    return engine, project_root


@runtime_app.command("status")
def runtime_status(
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format"),
) -> None:
    """Display runtime intelligence health, event statistics, and top errors."""
    try:
        engine, _ = _get_engine(root)
        summary = engine.get_status()

        if json_output:
            print(json.dumps(summary.model_dump(), indent=2))
            return

        console.print()
        header_text = (
            f"[bold cyan]MSK Runtime Intelligence Status[/bold cyan]\n"
            f"Project: [bold]{summary.project_name}[/bold] | "
            f"Total Events: [cyan]{summary.total_events}[/cyan] | "
            f"Errors: [red]{summary.total_errors}[/red] ([yellow]{summary.total_unresolved_errors} unresolved[/yellow])"
        )
        console.print(Panel(header_text, border_style="cyan"))

        if summary.last_event_timestamp:
            console.print(f"Last Observed Event: [dim]{summary.last_event_timestamp}[/dim]")
        console.print(
            f"Correlated Code Assets: [bold green]{summary.correlated_files_count}[/bold green] files, "
            f"[bold green]{summary.correlated_symbols_count}[/bold green] symbols"
        )
        console.print()

        if summary.top_errors:
            table = Table(title="Top Recurring Runtime Errors", border_style="dim", show_header=True)
            table.add_column("Error ID", style="cyan", width=18)
            table.add_column("Exception", style="bold red", width=22)
            table.add_column("Count", justify="right", style="yellow", width=8)
            table.add_column("Location", style="green")
            table.add_column("Last Seen", style="dim", width=20)
            table.add_column("Status", width=10)

            for err in summary.top_errors:
                loc = err.file_path or "unknown"
                if err.line_number:
                    loc += f":{err.line_number}"
                status = "[green]Resolved[/green]" if err.resolved else "[bold red]Active[/bold red]"
                table.add_row(
                    err.id,
                    err.exception_type,
                    str(err.occurrence_count),
                    loc,
                    err.last_seen[:19].replace("T", " "),
                    status,
                )
            console.print(table)
        else:
            console.print("[dim]No runtime errors recorded yet. Ingest logs using 'msk runtime logs'.[/dim]")
        console.print()
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc


@runtime_app.command("logs")
def runtime_logs(
    file: Path | None = typer.Option(None, "--file", "-f", help="Path to log file to ingest"),
    raw: str | None = typer.Option(None, "--raw", help="Raw log text to ingest"),
    source: str | None = typer.Option(None, "--source", "-s", help="Custom source identifier"),
    limit: int = typer.Option(20, "--limit", "-l", help="Number of parsed events to display"),
    no_save: bool = typer.Option(False, "--no-save", help="Do not persist parsed events to storage"),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Ingest application logs from file or standard input."""
    try:
        if not file and not raw:
            raise MSKError(
                "Please provide a log source: --file <path> or --raw '<text>'",
                hint="e.g. msk runtime logs --file app.log",
            )

        engine, _ = _get_engine(root)
        save = not no_save

        if file:
            events = engine.ingest_log_file(file, source=source, save=save)
        else:
            events = engine.ingest_logs(raw or "", source=source, save=save)

        if json_output:
            print(json.dumps([e.model_dump() for e in events[:limit]], indent=2))
            return

        console.print()
        console.print(
            Panel(
                f"Parsed [bold cyan]{len(events)}[/bold cyan] runtime events "
                f"({'persisted to MSK database' if save else 'dry-run, not persisted'})",
                title="Log Ingestion Summary",
                border_style="green",
            )
        )

        if events:
            table = Table(title=f"Recent Parsed Events (Showing {min(len(events), limit)})", border_style="dim", show_header=True)
            table.add_column("Timestamp", style="dim", width=19)
            table.add_column("Level", width=8)
            table.add_column("Type", style="cyan", width=12)
            table.add_column("Location", style="dim")
            table.add_column("Message Preview")

            for ev in events[:limit]:
                lvl_style = "red" if ev.level in ("ERROR", "CRITICAL") else ("yellow" if ev.level == "WARNING" else "green")
                lvl_str = f"[{lvl_style}]{ev.level}[/{lvl_style}]"
                loc = ev.file_path or ""
                if ev.line_number:
                    loc += f":{ev.line_number}"
                preview = ev.message.splitlines()[0][:60]
                table.add_row(
                    ev.timestamp[:19].replace("T", " "),
                    lvl_str,
                    ev.event_type,
                    loc,
                    preview,
                )
            console.print(table)
        console.print()
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc


@runtime_app.command("analyze")
def runtime_analyze(
    file: Path | None = typer.Option(None, "--file", "-f", help="Path to error or stack trace file"),
    raw: str | None = typer.Option(None, "--raw", help="Raw error or stack trace string"),
    depth: int = typer.Option(3, "--depth", "-d", help="Maximum impact traversal depth"),
    no_save: bool = typer.Option(False, "--no-save", help="Do not persist parsed error"),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Analyze a runtime error or stack trace, correlating with code and assessing impact."""
    try:
        if not file and not raw:
            raise MSKError(
                "Please provide error content to analyze: --file <path> or --raw '<traceback>'",
                hint="e.g. msk runtime analyze --file crash.log",
            )

        engine, project_root = _get_engine(root)
        error_text = raw
        if file:
            p = Path(file)
            res = p if p.is_absolute() else (project_root / p)
            if not res.is_file():
                raise MSKError(f"Error file not found: '{file}'")
            error_text = res.read_text(encoding="utf-8", errors="replace")

        _, report = engine.analyze_runtime_error(error_text or "", save=not no_save)

        if json_output:
            print(json.dumps(report.model_dump(), indent=2))
            return

        console.print()
        obs = report.observed_facts
        loc_str = obs.file_path or "Unknown file"
        if obs.line_number:
            loc_str += f":{obs.line_number}"
        if obs.symbol_name:
            loc_str += f" ({obs.symbol_name})"

        facts_text = (
            f"[bold red]Exception:[/bold red] {obs.exception_type}\n"
            f"[bold]Location:[/bold] [cyan]{loc_str}[/cyan]\n"
            f"[bold]Message:[/bold] {obs.error_message}\n"
            f"[bold]Occurrences:[/bold] {obs.occurrence_count}"
        )
        console.print(Panel(facts_text, title="[bold]Observed Runtime Facts (Empirical)[/bold]", border_style="red"))
        console.print()

        # Stack trace frames if present
        if obs.frames:
            t_frames = Table(title="Observed Call Hierarchy", border_style="dim", show_header=True)
            t_frames.add_column("#", width=3)
            t_frames.add_column("File", style="cyan")
            t_frames.add_column("Line", width=6)
            t_frames.add_column("Function", style="green")
            t_frames.add_column("Project File", width=12)

            for idx, fr in enumerate(obs.frames, 1):
                is_p = "[green]Yes[/green]" if fr.is_project_file else "[dim]External[/dim]"
                t_frames.add_row(str(idx), fr.file_path, str(fr.line_number), fr.function_name, is_p)
            console.print(t_frames)
            console.print()

        # Inferred Downstream Impacts
        if report.direct_impacts or report.transitive_impacts:
            t_impact = Table(title=f"Inferred Downstream Impact (Blast Radius: {report.total_blast_radius})", border_style="dim", show_header=True)
            t_impact.add_column("Category", style="cyan", width=16)
            t_impact.add_column("Classification", width=12)
            t_impact.add_column("Entity Name", style="bold")
            t_impact.add_column("Location", style="dim")
            t_impact.add_column("Reason")

            for item in report.direct_impacts + report.transitive_impacts:
                cls_styled = "[yellow]Direct[/yellow]" if item.classification == "direct" else "[dim]Transitive[/dim]"
                t_impact.add_row(
                    item.category,
                    cls_styled,
                    item.name,
                    item.file_path or "",
                    item.reason,
                )
            console.print(t_impact)
            console.print()

        # Impacted APIs
        if report.impacted_apis:
            api_lines = "\n".join(f"• [bold red]{api}[/bold red]" for api in report.impacted_apis)
            console.print(Panel(api_lines, title="[bold yellow]Potentially Degraded API Endpoints[/bold yellow]", border_style="yellow"))
            console.print()

        # Recommended Tests
        if report.recommended_tests:
            test_lines = "\n".join(f"• [cyan]{t}[/cyan]" for t in report.recommended_tests)
            console.print(Panel(test_lines, title="[bold green]Recommended Tests to Run[/bold green]", border_style="green"))
            console.print()

        # Actionable Investigation Checklist
        if report.investigation_checklist:
            check_lines = "\n".join(f"[bold]{idx}.[/bold] {item}" for idx, item in enumerate(report.investigation_checklist, 1))
            console.print(Panel(check_lines, title="[bold]Investigation & Resolution Checklist[/bold]", border_style="cyan"))
            console.print()
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc


@runtime_app.command("errors")
def runtime_errors(
    unresolved_only: bool = typer.Option(False, "--unresolved", "-u", help="Show unresolved errors only"),
    severity: str | None = typer.Option(None, "--severity", "-s", help="Filter by severity (ERROR, CRITICAL)"),
    resolve: str | None = typer.Option(None, "--resolve", help="Mark an error ID or fingerprint as resolved"),
    limit: int = typer.Option(50, "--limit", "-l", help="Maximum errors to display"),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """List, inspect, and manage recurring runtime errors."""
    try:
        engine, _ = _get_engine(root)

        if resolve:
            ok = engine.resolve_error(resolve)
            if ok:
                console.print(f"[green]Successfully marked error '{resolve}' as resolved.[/green]")
            else:
                console.print(f"[yellow]No error found matching '{resolve}'.[/yellow]")
            return

        errors = engine.get_errors(unresolved_only=unresolved_only, severity=severity, limit=limit)

        if json_output:
            print(json.dumps([e.model_dump() for e in errors], indent=2))
            return

        console.print()
        if not errors:
            console.print("[dim]No matching runtime errors found.[/dim]")
            console.print()
            return

        table = Table(title=f"Recorded Runtime Errors ({len(errors)})", border_style="dim", show_header=True)
        table.add_column("Error ID", style="cyan", width=18)
        table.add_column("Exception", style="bold red", width=22)
        table.add_column("Count", justify="right", style="yellow", width=8)
        table.add_column("Location", style="green")
        table.add_column("Last Seen", style="dim", width=20)
        table.add_column("Status", width=10)

        for err in errors:
            loc = err.file_path or "unknown"
            if err.line_number:
                loc += f":{err.line_number}"
            status = "[green]Resolved[/green]" if err.resolved else "[bold red]Active[/bold red]"
            table.add_row(
                err.id,
                err.exception_type,
                str(err.occurrence_count),
                loc,
                err.last_seen[:19].replace("T", " "),
                status,
            )
        console.print(table)
        console.print()
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc


@runtime_app.command("history")
def runtime_history(
    limit: int = typer.Option(50, "--limit", "-l", help="Number of events to display"),
    level: str | None = typer.Option(None, "--level", help="Filter by severity level"),
    event_type: str | None = typer.Option(None, "--type", help="Filter by event type"),
    since: str | None = typer.Option(None, "--since", help="Filter events since timestamp"),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Show chronological timeline of observed runtime events."""
    try:
        engine, _ = _get_engine(root)
        events = engine.get_events(limit=limit, level=level, event_type=event_type, since=since)

        if json_output:
            print(json.dumps([e.model_dump() for e in events], indent=2))
            return

        console.print()
        if not events:
            console.print("[dim]No runtime events recorded in history.[/dim]")
            console.print()
            return

        table = Table(title=f"Runtime Event Timeline ({len(events)})", border_style="dim", show_header=True)
        table.add_column("Timestamp", style="dim", width=19)
        table.add_column("Level", width=8)
        table.add_column("Type", style="cyan", width=12)
        table.add_column("Source", style="dim", width=14)
        table.add_column("Location", style="green", width=24)
        table.add_column("Message Preview")

        for ev in events:
            lvl_style = "red" if ev.level in ("ERROR", "CRITICAL") else ("yellow" if ev.level == "WARNING" else "green")
            lvl_str = f"[{lvl_style}]{ev.level}[/{lvl_style}]"
            loc = ev.file_path or ""
            if ev.line_number:
                loc += f":{ev.line_number}"
            preview = ev.message.splitlines()[0][:50]
            table.add_row(
                ev.timestamp[:19].replace("T", " "),
                lvl_str,
                ev.event_type,
                ev.source or "",
                loc,
                preview,
            )
        console.print(table)
        console.print()
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc


@runtime_app.command("correlate")
def runtime_correlate(
    identifier: str | None = typer.Argument(None, help="Error ID, fingerprint, or event ID"),
    latest: bool = typer.Option(False, "--latest", help="Correlate latest observed error"),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Connect a runtime observation directly to static project knowledge."""
    try:
        engine, _ = _get_engine(root)

        target_id = identifier
        if not target_id:
            if latest:
                errors = engine.get_errors(limit=1)
                if not errors:
                    raise MSKError("No runtime errors available to correlate.")
                target_id = errors[0].id
            else:
                raise MSKError(
                    "Please specify an error ID or fingerprint to correlate, or use --latest",
                    hint="e.g. msk runtime correlate rterr_... or msk runtime correlate --latest",
                )

        try:
            report = engine.correlate_error(target_id)
        except MSKError:
            report = engine.correlate_event(target_id)

        if json_output:
            print(json.dumps(report.model_dump(), indent=2))
            return

        console.print()
        obs = report.observed_facts
        inf = report.inferred_impact

        panel_text = (
            f"[bold]Correlation ID:[/bold] [cyan]{report.correlation_id}[/cyan]\n"
            f"[bold]Fingerprint:[/bold] [dim]{report.fingerprint}[/dim]\n"
            f"[bold red]Exception:[/bold red] {obs.exception_type}: {obs.error_message}\n\n"
            f"[bold green]Correlated Code Locus:[/bold green]\n"
            f"• File:   [cyan]{inf.affected_file or 'Unresolved'}[/cyan]\n"
            f"• Symbol: [bold]{inf.affected_symbol or 'Unresolved'}[/bold]"
        )
        console.print(Panel(panel_text, title="Runtime to Static Code Correlation", border_style="cyan"))
        console.print()

        if inf.callers:
            console.print(f"[bold]Upstream Callers ({len(inf.callers)}):[/bold] {', '.join(inf.callers)}")
        if inf.dependent_files:
            console.print(f"[bold]Dependent Files ({len(inf.dependent_files)}):[/bold] {', '.join(inf.dependent_files)}")
        if inf.related_apis:
            console.print(f"[bold yellow]Connected API Endpoints ({len(inf.related_apis)}):[/bold yellow] {', '.join(inf.related_apis)}")
        if inf.related_tests:
            console.print(f"[bold green]Connected Tests ({len(inf.related_tests)}):[/bold green] {', '.join(inf.related_tests)}")
        console.print()

        if inf.recommended_actions:
            actions_text = "\n".join(f"• {act}" for act in inf.recommended_actions)
            console.print(Panel(actions_text, title="Recommended Investigation Actions", border_style="green"))
            console.print()
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc


@runtime_app.command("prune")
def runtime_prune(
    max_events: int = typer.Option(1000, "--max-events", "-m", help="Maximum events to retain"),
    days: int = typer.Option(30, "--days", "-d", help="Retention period in days"),
    clear: bool = typer.Option(False, "--clear", help="Clear all runtime data for project"),
    json_output: bool = typer.Option(False, "--json", help="Output results in JSON format"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Prune or purge runtime observations to manage storage retention."""
    try:
        engine, _ = _get_engine(root)

        if clear:
            engine.storage.clear_runtime_data(engine.project_id)
            if json_output:
                print(json.dumps({"status": "SUCCESS", "message": "All runtime data cleared."}, indent=2))
                return
            console.print("[green]Cleared all runtime events and errors for project.[/green]")
            return

        pruned = engine.prune(max_events=max_events, retention_days=days)
        if json_output:
            print(json.dumps({"status": "SUCCESS", "pruned_events": pruned}, indent=2))
            return

        console.print(f"[green]Retention pruning complete. Pruned {pruned} older events.[/green]")
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc
