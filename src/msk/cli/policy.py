"""CLI commands for MSK privacy policy inspection, validation, and evaluation."""

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from msk.common.errors import NotAProjectError
from msk.config.loader import (
    get_msk_dir,
    get_policy_config_path,
    load_policy_config,
    validate_policy_file,
)
from msk.policy.audit import PolicyAuditor
from msk.policy.evaluator import PolicyEvaluator
from msk.policy.models import PolicyDecisionOutcome
from msk.project.detector import find_project_root
from msk.storage.database import DatabaseManager

policy_app = typer.Typer(
    name="policy",
    help="Inspect, validate, and evaluate MSK privacy and context-sharing policies.",
    no_args_is_help=True,
)

console = Console()


@policy_app.command("show")
def policy_show(
    root: Path | None = typer.Option(None, "--root", "-r", help="Explicit project root directory"),
    json_output: bool = typer.Option(False, "--json", help="Output policy configuration in JSON format"),
) -> None:
    """Display the active privacy and security policy configuration."""
    project_root = (root or find_project_root()).resolve()
    msk_dir = get_msk_dir(project_root)

    if not msk_dir.is_dir():
        raise NotAProjectError(
            f"No MSK project initialized at {project_root}",
            hint="Run 'msk init' first to initialize project.",
        )

    policy = load_policy_config(project_root, fallback_to_default=True)

    if json_output:
        print(json.dumps(policy.model_dump(), indent=2))
        return

    console.print()
    console.print("[bold]MSK Privacy & Policy Configuration[/bold]")
    console.print("----------------------------------------")
    console.print(f"Policy File:        [dim]{get_policy_config_path(project_root)}[/dim]")
    console.print(f"Version:            [cyan]{policy.version}[/cyan]")
    console.print(f"Project Sensitivity:[bold magenta]{policy.project_sensitivity}[/bold magenta]")
    console.print()

    # External AI Table
    ext_table = Table(title="External AI Transmission Policy", title_style="bold blue", border_style="dim")
    ext_table.add_column("Setting", style="cyan")
    ext_table.add_column("Value", style="bold white")

    ext_status = "[bold green]ENABLED[/bold green]" if policy.external_llm.enabled else "[bold red]DISABLED[/bold red]"
    ext_table.add_row("External AI Sharing", ext_status)
    ext_table.add_row("Allowed Providers", ", ".join(policy.external_llm.allowed_providers))
    ext_table.add_row("Strict Redaction", str(policy.external_llm.enforce_strict_redaction))
    ext_table.add_row("External Redaction Allowed", str(policy.allow_external_redaction))
    console.print(ext_table)
    console.print()

    # Security Rules Table
    sec_table = Table(title="Security & Classification Actions", title_style="bold yellow", border_style="dim")
    sec_table.add_column("Category", style="yellow")
    sec_table.add_column("Configured Action", style="bold white")

    sec_table.add_row("Secrets", policy.security.secrets.value)
    sec_table.add_row("PII", policy.security.pii.value)
    sec_table.add_row("Sensitive Credential Files", policy.security.sensitive_files.value)
    for cat, action in sorted(policy.category_rules.items()):
        sec_table.add_row(f"Category: {cat}", str(action))

    console.print(sec_table)
    console.print()

    if policy.blocked_files:
        console.print(f"[bold red]Blocked Files Patterns:[/bold red] {', '.join(policy.blocked_files)}")
    if policy.allowed_files:
        console.print(f"[bold green]Allowed Files Patterns:[/bold green] {', '.join(policy.allowed_files)}")
    console.print()


@policy_app.command("validate")
def policy_validate(
    file: Path | None = typer.Option(None, "--file", "-f", help="Explicit path to policy.json file"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Project root directory"),
    json_output: bool = typer.Option(False, "--json", help="Output validation result in JSON format"),
) -> None:
    """Validate policy configuration syntax, types, and schema compliance."""
    if file:
        policy_path = file.resolve()
    else:
        project_root = (root or find_project_root()).resolve()
        policy_path = get_policy_config_path(project_root)

    is_valid, errors = validate_policy_file(policy_path)

    if json_output:
        print(
            json.dumps(
                {
                    "valid": is_valid,
                    "file": str(policy_path),
                    "errors": errors,
                },
                indent=2,
            )
        )
        if not is_valid:
            raise typer.Exit(code=1)
        return

    console.print()
    if is_valid:
        console.print(f"[bold green][VALID][/bold green] Policy configuration at '{policy_path}' is valid.")
    else:
        console.print(f"[bold red][INVALID][/bold red] Policy configuration at '{policy_path}' has errors:")
        for err in errors:
            console.print(f"  - [red]{err}[/red]")
        console.print()
        raise typer.Exit(code=1)
    console.print()


@policy_app.command("evaluate")
def policy_evaluate(
    target: str = typer.Option("external", "--target", "-t", help="Target destination: 'local' or 'external'"),
    provider: str | None = typer.Option(None, "--provider", "-p", help="Target AI provider name"),
    category: str | None = typer.Option(None, "--category", "-c", help="Context category to evaluate"),
    file_path: str | None = typer.Option(None, "--file", "-f", help="File path to evaluate"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Project root directory"),
    json_output: bool = typer.Option(False, "--json", help="Output evaluation result in JSON format"),
) -> None:
    """Evaluate a context-sharing policy decision for given parameters."""
    project_root = (root or find_project_root()).resolve()
    policy = load_policy_config(project_root, fallback_to_default=True)
    evaluator = PolicyEvaluator(policy)

    cats = [category] if category else []
    files = [file_path] if file_path else []

    decision = evaluator.evaluate_request(
        destination=target,
        provider=provider,
        categories=cats,
        file_paths=files,
    )

    if json_output:
        print(json.dumps(decision.model_dump(), indent=2))
        return

    console.print()
    console.print("[bold]MSK Policy Decision Evaluation[/bold]")
    console.print("----------------------------------------")

    color = "green" if decision.outcome == PolicyDecisionOutcome.ALLOW else ("yellow" if decision.outcome == PolicyDecisionOutcome.REDACT else "red")
    console.print(f"Outcome:        [{color} bold]{decision.outcome.value}[/{color} bold]")
    console.print(f"Reason Code:    [bold cyan]{decision.reason_code}[/bold cyan]")
    console.print(f"Destination:    {decision.destination}")
    if decision.provider:
        console.print(f"Provider:       {decision.provider}")
    console.print(f"Explanation:    {decision.explanation}")
    if decision.denied_items:
        console.print(f"Denied Items:   [red]{', '.join(decision.denied_items)}[/red]")
    console.print()


@policy_app.command("audit")
def policy_audit(
    limit: int = typer.Option(20, "--limit", "-l", help="Maximum number of audit events to display"),
    root: Path | None = typer.Option(None, "--root", "-r", help="Project root directory"),
    json_output: bool = typer.Option(False, "--json", help="Output audit events in JSON format"),
) -> None:
    """Display recent policy enforcement decision audit events."""
    project_root = (root or find_project_root()).resolve()
    msk_dir = get_msk_dir(project_root)
    db_path = msk_dir / "msk.db"

    db_manager = DatabaseManager(db_path) if db_path.is_file() else None
    auditor = PolicyAuditor(db=db_manager, project_root=project_root)
    records = auditor.get_recent_records(limit=limit)

    if json_output:
        print(json.dumps([r.model_dump() for r in records], indent=2))
        return

    console.print()
    console.print(f"[bold]MSK Policy Enforcement Audit Log[/bold] (Last {len(records)} events)")
    console.print("----------------------------------------")

    if not records:
        console.print("[dim]No policy decision audit events recorded yet.[/dim]\n")
        return

    table = Table(title="Policy Decision Audit Trail", title_style="bold cyan", border_style="dim")
    table.add_column("Timestamp", style="dim", no_wrap=True)
    table.add_column("Destination", style="cyan", no_wrap=True)
    table.add_column("Outcome", no_wrap=True)
    table.add_column("Reason Code", style="bold white")
    table.add_column("Categories", style="yellow")
    table.add_column("Items / Redactions", justify="right", style="blue")

    for r in records:
        outcome_style = "bold green" if r.outcome == PolicyDecisionOutcome.ALLOW else ("bold yellow" if r.outcome == PolicyDecisionOutcome.REDACT else "bold red")
        outcome_formatted = f"[{outcome_style}]{r.outcome.value}[/{outcome_style}]"
        cats_str = ", ".join(r.categories[:3]) if r.categories else "-"
        counts_str = f"{r.item_count} items / {r.redactions_count} red"
        table.add_row(
            r.timestamp[:19].replace("T", " "),
            r.destination,
            outcome_formatted,
            r.reason_code,
            cats_str,
            counts_str,
        )

    console.print(table)
    console.print()
