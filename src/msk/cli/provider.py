"""CLI commands for managing AI providers and models."""

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from msk.common.errors import MSKError, NotAProjectError
from msk.config.loader import get_msk_dir
from msk.gateway.configuration import ProviderConfigurationManager
from msk.gateway.connection_test import ConnectionTestResult, ConnectionTestRunner, StageStatus
from msk.gateway.credentials import CredentialManager
from msk.gateway.models import (
    AuthType,
    ModelConfig,
    ProviderConfig,
    ProviderType,
)
from msk.project.detector import find_project_root

console = Console()
provider_app = typer.Typer(
    name="provider",
    help="Manage AI providers, models, endpoints, and credentials.",
    no_args_is_help=True,
)


def _get_manager(root: Path | None = None) -> ProviderConfigurationManager:
    project_root = (root or find_project_root()).resolve()
    msk_dir = get_msk_dir(project_root)
    if not msk_dir.is_dir():
        raise NotAProjectError(
            f"No MSK project initialized at {project_root}",
            hint="Run 'msk init' first to initialize project intelligence.",
        )
    return ProviderConfigurationManager(project_root)


@provider_app.command("list")
def list_providers(
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """List all configured AI providers and models."""
    try:
        mgr = _get_manager(root)
        providers = mgr.list_providers()

        if not providers:
            console.print("[yellow]No AI providers configured.[/yellow] Run [bold]msk provider add[/bold] to add one.")
            return

        table = Table(title="MSK Configured AI Providers", show_header=True, header_style="bold cyan")
        table.add_column("Default", justify="center", width=8)
        table.add_column("ID", style="bold")
        table.add_column("Type")
        table.add_column("Display Name")
        table.add_column("Endpoint")
        table.add_column("Default Model")
        table.add_column("Credential")
        table.add_column("Status", justify="center")

        for p in providers:
            is_def = "★ [green]YES[/green]" if p.is_default else "—"
            status = "[green]ENABLED[/green]" if p.enabled else "[dim red]DISABLED[/dim red]"
            cred_info = CredentialManager.get_credential_status(p.credential_ref)
            cred_display = (
                f"[green]{cred_info['display']}[/green]" if cred_info["present"] else f"[yellow]{cred_info['display']}[/yellow]"
            )

            table.add_row(
                is_def,
                p.provider_id,
                p.provider_type.value,
                p.display_name,
                p.endpoint,
                p.default_model,
                cred_display,
                status,
            )

        console.print(table)
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc


@provider_app.command("add")
def add_provider(
    provider_id: str | None = typer.Option(None, "--id", help="Unique provider ID slug"),
    display_name: str | None = typer.Option(None, "--name", help="Display name for provider"),
    provider_type: str | None = typer.Option(None, "--type", help="Provider type (ollama, openai, anthropic, gemini, etc.)"),
    endpoint: str | None = typer.Option(None, "--endpoint", help="API endpoint URL"),
    model: str | None = typer.Option(None, "--model", help="Default model ID"),
    credential_ref: str | None = typer.Option(None, "--credential", help="Credential reference (e.g. env:OPENAI_API_KEY)"),
    is_default: bool = typer.Option(False, "--default", help="Set this provider as the project default"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Interactively configure and register a new AI provider."""
    try:
        mgr = _get_manager(root)

        # If flags are provided non-interactively
        if not provider_id:
            console.print(Panel("[bold cyan]MSK AI Provider Setup[/bold cyan]\nConfigure a local or cloud AI provider."))
            provider_id = typer.prompt("Provider ID (e.g. local-ollama, work-openai)").strip()

        if not display_name:
            display_name = typer.prompt("Display Name", default=provider_id.replace("-", " ").title()).strip()

        if not provider_type:
            console.print("Available Types: [cyan]ollama, local_openai, openai, anthropic, gemini, mock[/cyan]")
            provider_type = typer.prompt("Provider Type", default="ollama").strip().lower()

        # Normalize type
        try:
            ptype = ProviderType(provider_type)
        except ValueError as err:
            raise MSKError(
                f"Invalid provider type: '{provider_type}'",
                hint="Allowed types: " + ", ".join(t.value for t in ProviderType),
            ) from err

        # Default endpoint suggestions
        default_endpoints = {
            ProviderType.OLLAMA: "http://localhost:11434",
            ProviderType.LOCAL_OPENAI: "http://localhost:8000/v1",
            ProviderType.OPENAI: "https://api.openai.com/v1",
            ProviderType.ANTHROPIC: "https://api.anthropic.com/v1",
            ProviderType.GEMINI: "https://generativelanguage.googleapis.com",
            ProviderType.MOCK: "http://localhost:0",
        }

        if not endpoint:
            default_ep = default_endpoints.get(ptype, "http://localhost:8000")
            endpoint = typer.prompt("API Endpoint URL", default=default_ep).strip()

        # Default model suggestions
        default_models = {
            ProviderType.OLLAMA: "llama3",
            ProviderType.LOCAL_OPENAI: "default",
            ProviderType.OPENAI: "gpt-4o",
            ProviderType.ANTHROPIC: "claude-3-5-sonnet-20241022",
            ProviderType.GEMINI: "gemini-1.5-pro",
            ProviderType.MOCK: "mock-model",
        }

        if not model:
            default_m = default_models.get(ptype, "default-model")
            model = typer.prompt("Default Model ID", default=default_m).strip()

        # Credential reference suggestions
        default_creds = {
            ProviderType.OPENAI: "env:OPENAI_API_KEY",
            ProviderType.ANTHROPIC: "env:ANTHROPIC_API_KEY",
            ProviderType.GEMINI: "env:GEMINI_API_KEY",
        }

        if credential_ref is None and ptype in default_creds:
            credential_ref = typer.prompt("Credential Reference", default=default_creds[ptype]).strip()

        auth_type = AuthType.NONE if ptype in (ProviderType.MOCK, ProviderType.OLLAMA) else AuthType.BEARER

        new_provider = ProviderConfig(
            provider_id=provider_id,
            display_name=display_name,
            provider_type=ptype,
            endpoint=endpoint,
            default_model=model,
            models=[ModelConfig(id=model, display_name=model)],
            auth_type=auth_type,
            credential_ref=credential_ref if credential_ref else None,
            enabled=True,
            is_default=is_default,
        )

        mgr.add_provider(new_provider)
        console.print(f"[bold green]✓ Provider '{provider_id}' successfully added![/bold green]")
        if is_default:
            console.print("[green]✓ Set as default project provider.[/green]")
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc


@provider_app.command("update")
def update_provider(
    provider_id: str = typer.Argument(..., help="ID of provider to update"),
    endpoint: str | None = typer.Option(None, "--endpoint", help="Update API endpoint URL"),
    model: str | None = typer.Option(None, "--model", help="Update default model"),
    display_name: str | None = typer.Option(None, "--name", help="Update display name"),
    credential_ref: str | None = typer.Option(None, "--credential", help="Update credential reference"),
    timeout: float | None = typer.Option(None, "--timeout", help="Update request timeout in seconds"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Update settings for an existing provider."""
    try:
        mgr = _get_manager(root)
        updates: dict[str, str | float] = {}

        if endpoint:
            updates["endpoint"] = endpoint
        if model:
            updates["default_model"] = model
        if display_name:
            updates["display_name"] = display_name
        if credential_ref:
            updates["credential_ref"] = credential_ref
        if timeout is not None:
            updates["timeout_seconds"] = timeout

        if not updates:
            console.print("[yellow]No update options specified.[/yellow] Use --help to view available flags.")
            return

        updated = mgr.update_provider(provider_id, updates)
        console.print(f"[bold green]✓ Provider '{updated.provider_id}' successfully updated![/bold green]")
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc


@provider_app.command("remove")
def remove_provider(
    provider_id: str = typer.Argument(..., help="ID of provider to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm removal without interactive prompt"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Remove a configured AI provider."""
    try:
        mgr = _get_manager(root)
        provider = mgr.get_provider(provider_id)
        if not provider:
            raise MSKError(f"Provider '{provider_id}' not found.", hint="Run 'msk provider list' to view configured IDs.")

        if not yes:
            confirm = typer.confirm(f"Are you sure you want to remove provider '{provider_id}'?")
            if not confirm:
                console.print("[dim]Aborted.[/dim]")
                return

        mgr.remove_provider(provider_id)
        console.print(f"[bold green]✓ Provider '{provider_id}' removed.[/bold green]")
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc


@provider_app.command("enable")
def enable_provider(
    provider_id: str = typer.Argument(..., help="ID of provider to enable"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Enable a configured provider."""
    try:
        mgr = _get_manager(root)
        mgr.enable_provider(provider_id)
        console.print(f"[bold green]✓ Provider '{provider_id}' enabled.[/bold green]")
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc


@provider_app.command("disable")
def disable_provider(
    provider_id: str = typer.Argument(..., help="ID of provider to disable"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Disable a configured provider."""
    try:
        mgr = _get_manager(root)
        mgr.disable_provider(provider_id)
        console.print(f"[bold yellow]✓ Provider '{provider_id}' disabled.[/bold yellow]")
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc


@provider_app.command("set-default")
def set_default_provider(
    provider_id: str = typer.Argument(..., help="ID of provider to set as default"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Set the default AI provider for project commands."""
    try:
        mgr = _get_manager(root)
        target = mgr.set_default_provider(provider_id)
        console.print(f"[bold green]✓ Default provider set to '{target.provider_id}' ({target.display_name}).[/bold green]")
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc





def _render_test_result(result: ConnectionTestResult) -> None:
    """Format and display single connection test result matching MSK requirements."""
    console.print()
    console.print(
        Panel(
            f"[bold cyan]MSK Provider Connection Test[/bold cyan]\n"
            f"Provider: [bold]{result.provider_name}[/bold] ({result.provider_id})\n"
            f"Model:    [bold]{result.model}[/bold]",
            expand=False,
        )
    )

    stage_order = ["configuration", "endpoint", "authentication", "model_availability", "test_inference"]
    for s_key in stage_order:
        stage = result.stages.get(s_key)
        if stage:
            if stage.status == StageStatus.PASSED:
                status_badge = "[bold green]PASS[/bold green]"
            elif stage.status == StageStatus.FAIL:
                status_badge = "[bold red]FAIL[/bold red]"
            else:
                status_badge = "[dim yellow]SKIPPED[/dim yellow]"

            detail = f" [dim]({stage.message})[/dim]" if stage.status == StageStatus.FAIL and stage.message else ""
            console.print(f"  {stage.stage_name:<22}: {status_badge}{detail}")

    console.print(f"  {'Latency':<22}: [cyan]{result.latency_ms:.0f} ms[/cyan]")
    console.print()

    if result.overall == "SUCCESS":
        console.print("[bold green]Overall: SUCCESS[/bold green]")
    else:
        console.print("[bold red]Overall: FAILED[/bold red]")
        if result.error_summary:
            console.print(f"[yellow]Diagnostic Detail:[/yellow] {result.error_summary}")
    console.print()


@provider_app.command("test")
def test_provider_command(
    provider_id: str | None = typer.Argument(None, help="Provider ID to test. Tests all enabled providers if omitted."),
    model: str | None = typer.Option(None, "--model", "-m", help="Specific model ID to test"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
) -> None:
    """Test API endpoint connectivity, authentication, model availability, and inference latency."""
    try:
        mgr = _get_manager(root)
        runner = ConnectionTestRunner()

        if provider_id:
            provider = mgr.get_provider(provider_id)
            if not provider:
                raise MSKError(
                    f"Provider '{provider_id}' not found.",
                    hint="Run 'msk provider list' to view configured provider IDs.",
                )
            result = runner.run_test(provider, model_override=model)
            _render_test_result(result)
            if result.overall != "SUCCESS":
                raise typer.Exit(code=1)
        else:
            providers = [p for p in mgr.list_providers() if p.enabled]
            if not providers:
                console.print("[yellow]No enabled providers found to test.[/yellow]")
                return

            has_failure = False
            for p in providers:
                res = runner.run_test(p, model_override=model)
                _render_test_result(res)
                if res.overall != "SUCCESS":
                    has_failure = True

            if has_failure:
                raise typer.Exit(code=1)
    except MSKError as exc:
        console.print(f"[bold red]MSK Error:[/bold red] {exc.message}")
        if exc.hint:
            console.print(f"[yellow]Run:[/yellow] {exc.hint}")
        raise typer.Exit(code=1) from exc
