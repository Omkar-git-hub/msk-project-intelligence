"""Implementation of 'msk context' CLI command."""

import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from msk.common.errors import MSKError, NotAProjectError
from msk.config.loader import get_msk_dir, load_project_config
from msk.context.engine import ContextEngine
from msk.context.models import ContextRequest, ContextResult
from msk.knowledge.repository import KnowledgeRepository
from msk.project.detector import find_project_root
from msk.storage.database import DatabaseManager

console = Console()


def run_context(
    question: str | None = None,
    file: Path | None = None,
    symbol: str | None = None,
    error: str | None = None,
    error_file: Path | None = None,
    budget: int = 2000,
    json_output: bool = False,
    root: Path | None = None,
) -> None:
    """Prepare ranked, privacy-safe, budget-aware project context for developer inquiries."""
    project_root = (root or find_project_root()).resolve()
    msk_dir = get_msk_dir(project_root)
    db_path = msk_dir / "msk.db"

    if not msk_dir.is_dir() or not db_path.is_file():
        raise NotAProjectError(
            f"No MSK project initialized at {project_root}",
            hint="Run 'msk init' first to build project intelligence.",
        )

    config = load_project_config(project_root)
    if not config:
        raise NotAProjectError(
            f"MSK configuration missing at {msk_dir / 'project.json'}",
            hint="Run 'msk init --force' to recreate.",
        )

    # Read error from file if specified
    error_content = error
    if error_file:
        resolved_err_file = error_file if error_file.is_absolute() else (project_root / error_file)
        if not resolved_err_file.is_file():
            raise MSKError(
                f"Error file not found: '{error_file}'.",
                hint="Verify the path to the error log or stack trace file.",
            )
        try:
            file_text = resolved_err_file.read_text(encoding="utf-8", errors="replace")
            error_content = f"{error}\n{file_text}" if error else file_text
        except Exception as exc:
            raise MSKError(f"Failed to read error file '{error_file}': {exc}") from exc

    # Build request
    req = ContextRequest(
        question=question,
        file=str(file) if file else None,
        symbol=symbol,
        error=error_content,
        budget_tokens=budget,
    )

    db_manager = DatabaseManager(db_path)
    repo = KnowledgeRepository(db_manager)
    engine = ContextEngine(repo, config.id, project_root)

    result = engine.build_context(req)

    if json_output:
        print(json.dumps(result.model_dump(), indent=2))
        return

    _print_human_context(result)


def _print_human_context(res: ContextResult) -> None:
    """Display rich human-readable context summary."""
    console.print()
    console.print("[bold]MSK Smart Context Engine[/bold]")
    console.print("----------------------------------------")
    console.print(f"Project:        [bold cyan]{res.project_name}[/bold cyan]")

    req = res.request_summary
    if req.get("question"):
        console.print(f"Query:          [white]{req['question']}[/white]")
    if req.get("file"):
        console.print(f"Focus File:     [dim]{req['file']}[/dim]")
    if req.get("symbol"):
        console.print(f"Focus Symbol:   [yellow]{req['symbol']}[/yellow]")
    if req.get("has_error"):
        console.print("Error Context:  [red]Diagnostic error/stack trace attached[/red]")

    bm = res.budget_metrics
    console.print(
        f"Context Budget: [bold]{bm.estimated_tokens_used}[/bold] / {bm.token_budget} tokens "
        f"([cyan]{bm.utilization_pct}%[/cyan] used | [green]{bm.selected_count} selected[/green], "
        f"[yellow]{bm.omitted_count} omitted[/yellow])"
    )
    console.print()

    # Warnings if any
    if res.warnings:
        warn_text = "\n".join(f"- {w}" for w in res.warnings)
        console.print(
            Panel(
                f"[bold yellow]Knowledge Model Warnings:[/bold yellow]\n{warn_text}",
                border_style="yellow",
            )
        )
        console.print()

    # Selected Items Table
    if not res.selected_items:
        console.print(
            Panel(
                "[yellow]No relevant project context found matching the query.[/yellow]\n"
                "Try broadening your question, checking file spelling, or running 'msk update'.",
                border_style="yellow",
            )
        )
        console.print()
    else:
        table = Table(title="Selected Project Context Entities", title_style="bold magenta", border_style="dim")
        table.add_column("Type", style="cyan", no_wrap=True)
        table.add_column("Score", justify="right", style="bold green", no_wrap=True)
        table.add_column("Entity Name", style="bold white")
        table.add_column("File Path", style="dim")
        table.add_column("Est. Tokens", justify="right", style="blue", no_wrap=True)
        table.add_column("Relevance Reasons", style="white")

        for item in res.selected_items:
            reasons_str = "; ".join(item.reasons)
            table.add_row(
                item.item_type.upper(),
                f"{item.relevance_score:.1f}",
                item.name,
                item.file_path,
                str(item.estimated_tokens),
                reasons_str,
            )

        console.print(table)
        console.print()

    # Architecture & Impact Relationships
    if res.relationships:
        rel_table = Table(title="Relevant Architecture & Impact Paths", title_style="bold blue", border_style="blue")
        rel_table.add_column("Relationship", style="bold cyan", no_wrap=True)
        rel_table.add_column("Depth", justify="center", no_wrap=True)
        rel_table.add_column("Source", style="dim")
        rel_table.add_column("Target / Impact", style="bold white")
        rel_table.add_column("Explanation / Path", style="white")

        for rel in res.relationships[:8]:
            path_str = " -> ".join(rel.path) if len(rel.path) > 1 else rel.reason
            rel_table.add_row(
                rel.relationship_type,
                str(rel.depth),
                rel.source,
                rel.target,
                path_str,
            )

        console.print(rel_table)
        console.print()

    # Related Tests
    if res.related_tests:
        test_table = Table(title="Recommended Verification Tests", title_style="bold green", border_style="green")
        test_table.add_column("Test Identifier", style="bold green")

        for t in res.related_tests[:10]:
            test_table.add_row(t)

        console.print(test_table)
        console.print()

    # Omitted Items Summary
    if res.omitted_items:
        omitted_summary = f"[yellow]{len(res.omitted_items)} candidate entities[/yellow] were omitted to stay within the {bm.token_budget} token budget limit."
        sample_names = ", ".join(i.name for i in res.omitted_items[:5])
        if len(res.omitted_items) > 5:
            sample_names += f" (+{len(res.omitted_items) - 5} more)"
        omitted_summary += f"\n[dim]Top omitted candidates: {sample_names}[/dim]"
        console.print(Panel(omitted_summary, title="Context Budget Omissions", border_style="yellow"))
        console.print()

    # Privacy Summary
    priv = res.privacy_summary
    priv_lines = [
        "[green][OK][/green] Local analysis: [bold]100% offline[/bold] (no data sent to external AI services)",
        f"Secret Redactions: [bold]{priv.redactions_count}[/bold] credential patterns sanitized",
    ]
    if priv.excluded_sensitive_files:
        priv_lines.append(f"Excluded Sensitive Files: [bold]{len(priv.excluded_sensitive_files)}[/bold] ({', '.join(priv.excluded_sensitive_files)})")
    priv_lines.append("[dim]" + priv.notes[-1] + "[/dim]")

    console.print(Panel("\n".join(priv_lines), title="Privacy & Data Minimization", border_style="green"))
    console.print()
