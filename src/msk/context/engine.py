"""Main orchestration engine for MSK Smart Context preparation."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from msk.common.errors import MSKError
from msk.config.loader import load_policy_config
from msk.config.models import PolicyConfig
from msk.context.budget import select_items_within_budget
from msk.context.models import (
    ContextItem,
    ContextRequest,
    ContextResult,
    PrivacySummary,
)
from msk.context.privacy import PrivacyFilter
from msk.context.retriever import ContextRetriever
from msk.knowledge.models import ProjectEntity
from msk.knowledge.repository import KnowledgeRepository
from msk.policy.audit import PolicyAuditor
from msk.policy.boundary import PolicyEnforcementBoundary
from msk.policy.models import PolicyDecision


class ContextEngine:
    """Orchestrates local-first smart context selection, ranking, budgeting, and privacy protection."""

    def __init__(
        self,
        repo: KnowledgeRepository,
        project_id: str,
        project_root: Path,
        policy: PolicyConfig | None = None,
    ) -> None:
        self.repo = repo
        self.project_id = project_id
        self.project_root = Path(project_root).resolve()
        self.policy = policy or load_policy_config(self.project_root, fallback_to_default=True)

        # Load project entity
        self.project = self.repo.get_project(self.project_id)
        if not self.project:
            with self.repo.db.transaction() as conn:
                row = conn.execute("SELECT * FROM projects WHERE name = ? LIMIT 1", (self.project_id,)).fetchone()
                if not row:
                    row = conn.execute("SELECT * FROM projects LIMIT 1").fetchone()
                if row:
                    self.project = ProjectEntity(
                        id=row["id"],
                        name=row["name"],
                        root_path=row["root_path"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                    self.project_id = self.project.id

        self.project_name = self.project.name if self.project else "Unknown"

    def _validate_request(self, request: ContextRequest) -> None:
        """Validate input parameters and reject empty or invalid requests."""
        has_question = bool(request.question and request.question.strip())
        has_file = bool(request.file and request.file.strip())
        has_symbol = bool(request.symbol and request.symbol.strip())
        has_error = bool(request.error and request.error.strip())

        if not (has_question or has_file or has_symbol or has_error):
            raise MSKError(
                "Empty context request. Please provide at least one query parameter: --question, --file, --symbol, or --error.",
                hint="e.g. msk context --question 'How does payment processing work?' or msk context --file src/payment/service.py",
            )

        if request.budget_tokens <= 0:
            raise MSKError(
                f"Invalid context budget: {request.budget_tokens}. Context budget must be greater than 0.",
                hint="Specify a valid token budget, e.g. --budget 2000",
            )

        if request.budget_tokens > 100_000:
            raise MSKError(
                f"Context budget {request.budget_tokens} exceeds maximum allowed limit of 100,000 tokens.",
                hint="Specify a smaller token budget, e.g. --budget 4000",
            )

    def build_context(self, request: ContextRequest) -> ContextResult:
        """Execute local smart context preparation for the given request."""
        self._validate_request(request)

        # Verify project has indexed files
        all_files = self.repo.get_all_files(self.project_id)
        if not all_files:
            raise MSKError(
                "MSK project knowledge model is empty.",
                hint="Run 'msk init --force' to index project files.",
            )

        # 1. Initialize Privacy Filter and sanitize inputs
        privacy = PrivacyFilter()
        sanitized_question = privacy.sanitize_text(request.question) if request.question else None
        sanitized_error = privacy.sanitize_text(request.error) if request.error else None

        sanitized_request = ContextRequest(
            question=sanitized_question,
            file=request.file.replace("\\", "/").strip() if request.file else None,
            symbol=request.symbol.strip() if request.symbol else None,
            error=sanitized_error,
            budget_tokens=request.budget_tokens,
        )

        request_summary: dict[str, Any] = {
            "question": sanitized_request.question,
            "file": sanitized_request.file,
            "symbol": sanitized_request.symbol,
            "has_error": bool(sanitized_request.error),
            "budget_tokens": sanitized_request.budget_tokens,
        }
        if sanitized_request.error:
            # Include truncated preview in summary
            first_line = sanitized_request.error.strip().splitlines()[0]
            request_summary["error_preview"] = first_line[:120]

        # 2. Candidate Retrieval & Deterministic Ranking
        retriever = ContextRetriever(
            repo=self.repo,
            project_id=self.project_id,
            project_root=self.project_root,
            privacy_filter=privacy,
        )

        candidates = retriever.retrieve_candidates(sanitized_request)

        # 3. Budget Selection & Deduplication
        selected_items, omitted_items, budget_metrics = select_items_within_budget(
            candidates=candidates,
            budget_tokens=sanitized_request.budget_tokens,
        )

        # 4. Synthesize LLM Context Representation
        llm_context = self._format_llm_context(
            request=sanitized_request,
            selected_items=selected_items,
            relationships=retriever.relationships,
            related_tests=sorted(retriever.related_tests),
        )

        # 5. Build Privacy Summary
        privacy_summary = PrivacySummary(
            local_only=True,
            redactions_count=privacy.redactions_count,
            excluded_sensitive_files=privacy.excluded_files,
            notes=privacy.get_privacy_notes(),
        )

        now_iso = datetime.now(UTC).isoformat()

        res = ContextResult(
            project_name=self.project_name,
            analyzed_at=now_iso,
            request_summary=request_summary,
            budget_metrics=budget_metrics,
            privacy_summary=privacy_summary,
            selected_items=selected_items,
            relationships=retriever.relationships,
            related_tests=sorted(retriever.related_tests),
            omitted_items=omitted_items,
            warnings=retriever.warnings,
            llm_context=llm_context,
        )

        # Evaluate local policy
        auditor = PolicyAuditor(db=self.repo.db, project_root=self.project_root)
        boundary = PolicyEnforcementBoundary(
            policy=self.policy,
            auditor=auditor,
            project_id=self.project_id,
        )
        _, decision = boundary.enforce_transmission(res, destination="local")
        res.policy_decision = decision

        return res

    def evaluate_sharing_policy(
        self,
        result: ContextResult,
        destination: str = "local",
        provider: str | None = None,
    ) -> tuple[ContextResult | None, PolicyDecision]:
        """Evaluate and enforce policy constraints for transmitting context to a destination."""
        auditor = PolicyAuditor(db=self.repo.db, project_root=self.project_root)
        boundary = PolicyEnforcementBoundary(
            policy=self.policy,
            auditor=auditor,
            project_id=self.project_id,
        )
        return boundary.enforce_transmission(
            context_result=result,
            destination=destination,
            provider=provider,
        )

    def _format_llm_context(
        self,
        request: ContextRequest,
        selected_items: list[ContextItem],
        relationships: list[Any],
        related_tests: list[str],
    ) -> str:
        """Format a clean, structured context markdown block suitable for a future LLM gateway."""
        sections: list[str] = []
        sections.append(f"# Project Context: {self.project_name}")
        sections.append("This context was selected locally by MSK Project Intelligence based on relevance and privacy constraints.")

        if request.question:
            sections.append(f"## Developer Query\n{request.question}")

        if request.file or request.symbol:
            target_desc = []
            if request.file:
                target_desc.append(f"File: `{request.file}`")
            if request.symbol:
                target_desc.append(f"Symbol: `{request.symbol}`")
            sections.append("## Focus Target\n" + ", ".join(target_desc))

        if request.error:
            sections.append(f"## Diagnostic Error Context (Sanitized)\n```text\n{request.error}\n```")

        if selected_items:
            sections.append("## Relevant Code Entities")
            for item in selected_items:
                reasons_str = "; ".join(item.reasons)
                sections.append(
                    f"### [{item.item_type.upper()}] {item.name} (Score: {item.relevance_score:.1f})\n"
                    f"*Location: `{item.file_path}` | Relevance: {reasons_str}*\n\n"
                    f"```text\n{item.snippet}\n```"
                )

        if relationships:
            sections.append("## Relevant Architecture & Relationship Paths")
            for rel in relationships[:10]:
                path_str = " -> ".join(rel.path) if len(rel.path) > 1 else rel.reason
                sections.append(f"- **{rel.relationship_type}**: {path_str}")

        if related_tests:
            sections.append("## Recommended Verification Tests")
            for t in related_tests[:10]:
                sections.append(f"- `{t}`")

        return "\n\n".join(sections)
