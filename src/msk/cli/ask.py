"""Implementation of 'msk ask' CLI command."""

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from msk.common.errors import MSKError, NotAProjectError
from msk.config.loader import get_msk_dir, load_policy_config, load_project_config
from msk.context.engine import ContextEngine
from msk.context.models import ContextRequest
from msk.gateway.configuration import ProviderConfigurationManager
from msk.gateway.credentials import CredentialManager
from msk.gateway.models import ProviderType
from msk.gateway.registry import get_default_registry
from msk.knowledge.repository import KnowledgeRepository
from msk.policy.audit import PolicyAuditor
from msk.policy.boundary import PolicyEnforcementBoundary
from msk.policy.gateway import PolicyGatedGateway
from msk.policy.models import PolicyDecisionOutcome
from msk.project.detector import find_project_root
from msk.storage.database import DatabaseManager

console = Console()


def run_ask(
    question: str,
    file: Path | None = None,
    symbol: str | None = None,
    error: str | None = None,
    budget: int = 2000,
    provider: str | None = None,
    model: str | None = None,
    target: str | None = None,
    json_output: bool = False,
    root: Path | None = None,
) -> None:
    """Execute AI question-answering grounded in policy-governed project context."""
    if not question or not question.strip():
        raise MSKError(
            "Please provide a question to ask.",
            hint="e.g. msk ask 'How does payment processing work?'",
        )

    project_root = (root or find_project_root()).resolve()
    msk_dir = get_msk_dir(project_root)
    db_path = msk_dir / "msk.db"

    if not msk_dir.is_dir() or not db_path.is_file():
        raise NotAProjectError(
            f"No MSK project initialized at {project_root}",
            hint="Run 'msk init' first to index project intelligence.",
        )

    project_cfg = load_project_config(project_root)
    if not project_cfg:
        raise NotAProjectError(
            f"MSK configuration missing at {msk_dir / 'project.json'}",
            hint="Run 'msk init --force' to recreate.",
        )

    # 1. Resolve Provider and Destination
    provider_mgr = ProviderConfigurationManager(project_root)
    if provider:
        provider_cfg = provider_mgr.get_provider(provider)
        if not provider_cfg:
            raise MSKError(
                f"Provider '{provider}' not found.",
                hint="Run 'msk provider list' to see available providers.",
            )
    else:
        provider_cfg = provider_mgr.get_default_provider()

    if not provider_cfg.enabled:
        raise MSKError(
            f"Provider '{provider_cfg.provider_id}' is disabled.",
            hint=f"Run 'msk provider enable {provider_cfg.provider_id}' to enable it.",
        )

    # Determine destination (local vs external)
    if target:
        destination = target.strip().lower()
    else:
        if provider_cfg.provider_type in (ProviderType.MOCK, ProviderType.OLLAMA, ProviderType.LOCAL_OPENAI):
            destination = "local"
        else:
            destination = "external"

    # Override model if requested
    active_model = model or provider_cfg.default_model

    # 2. Build Smart Context locally
    db_manager = DatabaseManager(db_path)
    repo = KnowledgeRepository(db_manager)
    policy = load_policy_config(project_root, fallback_to_default=True)
    engine = ContextEngine(repo, project_cfg.id, project_root, policy=policy)

    req = ContextRequest(
        question=question,
        file=str(file) if file else None,
        symbol=symbol,
        error=error,
        budget_tokens=budget,
    )
    context_result = engine.build_context(req)

    # 3. Resolve API credentials & instantiate adapter
    api_token = CredentialManager.resolve_token(provider_cfg.credential_ref)
    adapter = get_default_registry().create(provider_cfg, api_key=api_token)

    # 4. Transmit strictly through PolicyGatedGateway
    auditor = PolicyAuditor(db=db_manager, project_root=project_root)
    boundary = PolicyEnforcementBoundary(
        policy=policy,
        auditor=auditor,
        project_id=project_cfg.id,
    )
    gateway = PolicyGatedGateway(adapter=adapter, boundary=boundary)

    response, decision = gateway.transmit(
        context_result=context_result,
        destination=destination,
        provider=provider_cfg.provider_id,
    )

    # 5. Handle Policy DENY (Fail-closed)
    if decision.outcome == PolicyDecisionOutcome.DENY or response is None:
        if json_output:
            print(
                json.dumps(
                    {
                        "status": "DENIED",
                        "policy_decision": decision.model_dump(),
                        "request_summary": context_result.request_summary,
                        "budget_metrics": context_result.budget_metrics.model_dump(),
                    },
                    indent=2,
                )
            )
            return

        console.print()
        console.print(
            Panel(
                f"[bold red]AI Transmission Blocked by Policy[/bold red]\n\n"
                f"Destination: [cyan]{destination}[/cyan] | Provider: [cyan]{provider_cfg.provider_id}[/cyan]\n"
                f"Reason: [bold yellow]{decision.reason_code}[/bold yellow]\n"
                f"{decision.explanation}\n\n"
                f"[dim]The project stays private. No code context was transmitted.[/dim]",
                title="Privacy Boundary Enforcement",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    # 6. Format Response Output
    answer_text = response.get("content") or response.get("simulated_response", "")

    if json_output:
        out = {
            "status": "SUCCESS",
            "provider": provider_cfg.provider_id,
            "model": active_model,
            "destination": destination,
            "question": question,
            "answer": answer_text,
            "latency_ms": response.get("latency_ms", 0.0),
            "tokens_used": response.get("tokens_used"),
            "policy_decision": decision.model_dump(),
            "context_metrics": context_result.budget_metrics.model_dump(),
            "cited_items": [
                {
                    "name": it.name,
                    "item_type": it.item_type,
                    "file_path": it.file_path,
                    "relevance_score": it.relevance_score,
                }
                for it in context_result.selected_items
            ],
            "recommended_tests": context_result.related_tests,
        }
        print(json.dumps(out, indent=2))
        return

    # Rich Human Output
    console.print()
    header_info = (
        f"[bold cyan]MSK Project Assistant[/bold cyan] | Provider: [bold]{provider_cfg.display_name}[/bold] "
        f"([dim]{active_model}[/dim]) | Destination: [bold]{destination}[/bold]"
    )
    console.print(Panel(header_info, border_style="cyan"))

    console.print()
    console.print(Markdown(answer_text))
    console.print()

    # Sources & Citations Table
    if context_result.selected_items:
        table = Table(title="Grounded Context Citations", border_style="dim", show_header=True)
        table.add_column("Type", style="cyan", width=10)
        table.add_column("Name", style="bold white")
        table.add_column("Location", style="dim")
        table.add_column("Score", justify="right")

        for item in context_result.selected_items:
            table.add_row(
                item.item_type.upper(),
                item.name,
                item.file_path or "—",
                f"{item.relevance_score:.1f}",
            )

        console.print(table)

    if context_result.related_tests:
        console.print("[dim]Related verification tests:[/dim] " + ", ".join(f"`{t}`" for t in context_result.related_tests[:5]))
    console.print()
