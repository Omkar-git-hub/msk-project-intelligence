"""Main entry point for MSK CLI."""

import sys
from pathlib import Path

import typer
from rich.console import Console

from msk.cli.context import run_context
from msk.cli.doctor import run_doctor
from msk.cli.impact import run_impact
from msk.cli.init import run_init
from msk.cli.policy import policy_app
from msk.cli.status import run_status
from msk.cli.update import run_update
from msk.common.errors import MSKError
from msk.common.logging import setup_logger

app = typer.Typer(
    name="msk",
    help="MSK - Local-first Project Intelligence, Privacy, and Security layer.",
    no_args_is_help=True,
    add_completion=False,
)

# Register policy command group
app.add_typer(policy_app, name="policy")

console = Console()


@app.callback()
def main_callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose log output"),
    debug: bool = typer.Option(False, "--debug", help="Show full debug tracebacks on error"),
) -> None:
    """MSK global configuration options."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["debug"] = debug
    setup_logger(verbose=verbose)


@app.command("init")
def init_command(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
    force: bool = typer.Option(False, "--force", "-f", help="Reinitialize and overwrite existing .msk directory"),
) -> None:
    """Analyze the project locally and initialize the Project Knowledge Model."""
    debug = ctx.obj.get("debug", False)
    verbose = ctx.obj.get("verbose", False)
    try:
        run_init(root=root, force=force, verbose=verbose)
    except MSKError as exc:
        if debug:
            raise
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        sys.exit(1)
    except Exception as exc:
        if debug:
            raise
        console.print(f"[bold red]Unexpected Error:[/bold red] {exc}")
        console.print("[dim]Use --debug to see full traceback.[/dim]")
        sys.exit(1)


@app.command("status")
def status_command(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Display project intelligence, symbols, dependencies, and health metrics."""
    debug = ctx.obj.get("debug", False)
    try:
        run_status(root=root)
    except MSKError as exc:
        if debug:
            raise
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        sys.exit(1)
    except Exception as exc:
        if debug:
            raise
        console.print(f"[bold red]Unexpected Error:[/bold red] {exc}")
        console.print("[dim]Use --debug to see full traceback.[/dim]")
        sys.exit(1)


@app.command("doctor")
def doctor_command(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Diagnose environment, parser readiness, Git status, and database health."""
    debug = ctx.obj.get("debug", False)
    try:
        ok = run_doctor(root=root)
        if not ok:
            sys.exit(1)
    except Exception as exc:
        if debug:
            raise
        console.print(f"[bold red]Doctor Error:[/bold red] {exc}")
        sys.exit(1)


@app.command("update")
def update_command(
    ctx: typer.Context,
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Incrementally update the knowledge model based on changed files."""
    debug = ctx.obj.get("debug", False)
    verbose = ctx.obj.get("verbose", False)
    try:
        run_update(root=root, verbose=verbose)
    except MSKError as exc:
        if debug:
            raise
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        sys.exit(1)
    except Exception as exc:
        if debug:
            raise
        console.print(f"[bold red]Unexpected Error:[/bold red] {exc}")
        console.print("[dim]Use --debug to see full traceback.[/dim]")
        sys.exit(1)


@app.command("impact")
def impact_command(
    ctx: typer.Context,
    file: Path | None = typer.Option(None, "--file", "-f", help="Target project file to analyze"),
    symbol: str | None = typer.Option(None, "--symbol", "-s", help="Target function, method, or class symbol"),
    dependency: str | None = typer.Option(None, "--dependency", help="Target external dependency package"),
    depth: int = typer.Option(3, "--depth", "-d", help="Maximum downstream traversal depth"),
    direct: bool = typer.Option(False, "--direct", help="Limit analysis to direct impact only (equivalent to depth 1)"),
    json_output: bool = typer.Option(False, "--json", help="Output results in machine-readable JSON format"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Analyze downstream impact of modifying a file, symbol, or dependency."""
    debug = ctx.obj.get("debug", False)
    try:
        run_impact(
            file=file,
            symbol=symbol,
            dependency=dependency,
            depth=depth,
            direct=direct,
            json_output=json_output,
            root=root,
        )
    except MSKError as exc:
        if debug:
            raise
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        sys.exit(1)
    except Exception as exc:
        if debug:
            raise
        console.print(f"[bold red]Unexpected Error:[/bold red] {exc}")
        console.print("[dim]Use --debug to see full traceback.[/dim]")
        sys.exit(1)


@app.command("context")
def context_command(
    ctx: typer.Context,
    question: str | None = typer.Option(None, "--question", "-q", help="Developer query, task, or debugging question"),
    file: Path | None = typer.Option(None, "--file", "-f", help="Target project file to focus context on"),
    symbol: str | None = typer.Option(None, "--symbol", "-s", help="Target function, method, or class symbol"),
    error: str | None = typer.Option(None, "--error", "-e", help="Error text or stack trace to analyze"),
    error_file: Path | None = typer.Option(None, "--error-file", help="Path to file containing error text or stack trace"),
    budget: int = typer.Option(2000, "--budget", "-b", help="Context token budget limit"),
    target: str = typer.Option("local", "--target", "-t", help="Context transmission target: 'local' or 'external'"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Target AI provider name"),
    json_output: bool = typer.Option(False, "--json", help="Output results in machine-readable JSON format"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Select and rank relevant project context for a question, file, symbol, or error."""
    debug = ctx.obj.get("debug", False)
    try:
        run_context(
            question=question,
            file=file,
            symbol=symbol,
            error=error,
            error_file=error_file,
            budget=budget,
            json_output=json_output,
            root=root,
            target=target,
            provider=provider,
        )
    except MSKError as exc:
        if debug:
            raise
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        sys.exit(1)
    except Exception as exc:
        if debug:
            raise
        console.print(f"[bold red]Unexpected Error:[/bold red] {exc}")
        console.print("[dim]Use --debug to see full traceback.[/dim]")
        sys.exit(1)


if __name__ == "__main__":
    app()
