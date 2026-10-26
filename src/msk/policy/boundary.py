"""Policy enforcement boundary integrating policy evaluation with MSK Smart Context."""

import copy
import logging

from msk.config.models import PolicyConfig
from msk.context.models import ContextItem, ContextResult
from msk.context.privacy import PrivacyFilter
from msk.policy.audit import PolicyAuditor
from msk.policy.evaluator import PolicyEvaluator
from msk.policy.models import (
    ContextCategory,
    PolicyDecision,
    PolicyDecisionOutcome,
)

logger = logging.getLogger("msk.policy.boundary")


class PolicyEnforcementBoundary:
    """Enforces policy constraints on ContextResult prior to external transmission.

    Guarantees:
    - Context evaluated as DENY is blocked and never transmitted.
    - Context evaluated as REDACT is sanitized and never leaks original values.
    - Local operations remain fully local and intact.
    - Minimal privacy-safe audit record is logged for every evaluation.
    """

    def __init__(
        self,
        policy: PolicyConfig | None = None,
        auditor: PolicyAuditor | None = None,
        project_id: str | None = None,
    ) -> None:
        self.policy = policy or PolicyConfig()
        self.evaluator = PolicyEvaluator(self.policy)
        self.auditor = auditor
        self.project_id = project_id or "unknown_project"

    def enforce_transmission(
        self,
        context_result: ContextResult,
        destination: str = "local",
        provider: str | None = None,
    ) -> tuple[ContextResult | None, PolicyDecision]:
        """Evaluate and enforce policy on context_result.

        Returns:
            (ContextResult, PolicyDecision) if ALLOW or REDACT.
            (None, PolicyDecision) if DENY.
        """
        # Collect metadata for policy evaluation
        categories = self._extract_categories(context_result)
        file_paths = [item.file_path for item in context_result.selected_items]
        redactions_count = context_result.privacy_summary.redactions_count
        has_secrets = redactions_count > 0 or bool(context_result.privacy_summary.excluded_sensitive_files)

        context_items_meta = [
            {
                "file_path": item.file_path,
                "item_type": item.item_type,
                "name": item.name,
            }
            for item in context_result.selected_items
        ]

        # Evaluate policy
        decision = self.evaluator.evaluate_request(
            destination=destination,
            provider=provider,
            categories=categories,
            file_paths=file_paths,
            redactions_count=redactions_count,
            has_secrets=has_secrets,
            context_items=context_items_meta,
        )

        # Audit decision
        if self.auditor:
            try:
                self.auditor.record_decision(
                    project_id=self.project_id,
                    decision=decision,
                    categories=categories,
                    item_count=len(context_result.selected_items),
                )
            except Exception as exc:
                logger.debug("Failed to record policy audit event: %s", exc)

        # Handle DENY
        if decision.outcome == PolicyDecisionOutcome.DENY:
            return (None, decision)

        # Handle REDACT
        if decision.outcome == PolicyDecisionOutcome.REDACT:
            redacted_result = self._apply_redactions(context_result, decision)
            return (redacted_result, decision)

        # Handle ALLOW
        return (context_result, decision)

    def _extract_categories(self, result: ContextResult) -> list[str]:
        """Extract category names present in the context result."""
        cats: set[str] = set()
        for item in result.selected_items:
            cats.add(item.item_type)

        if result.relationships:
            cats.add(ContextCategory.ARCHITECTURE.value)
        if result.related_tests:
            cats.add(ContextCategory.TESTS.value)
        if result.request_summary.get("has_error"):
            cats.add(ContextCategory.DIAGNOSTICS.value)
        if result.request_summary.get("question"):
            cats.add(ContextCategory.USER_QUERY.value)

        return sorted(cats)

    def _apply_redactions(self, result: ContextResult, decision: PolicyDecision) -> ContextResult:
        """Apply safe redaction transformations without leaking original sensitive content."""
        redacted = copy.deepcopy(result)
        privacy = PrivacyFilter()

        # Sanitize query and error in request summary
        if redacted.request_summary.get("question"):
            redacted.request_summary["question"] = privacy.sanitize_text(redacted.request_summary["question"])
        if redacted.request_summary.get("error_preview"):
            redacted.request_summary["error_preview"] = privacy.sanitize_text(redacted.request_summary["error_preview"])

        # Check which categories require redaction
        redact_cats = set(decision.redacted_categories)
        is_redacting_code = ContextCategory.SOURCE_CODE.value in redact_cats or "source" in redact_cats

        sanitized_items: list[ContextItem] = []
        for item in redacted.selected_items:
            # If category requires redaction, sanitize or mask snippet
            if is_redacting_code or item.item_type in redact_cats:
                # Mask snippet to symbol summary
                new_snippet = f"[REDACTED_BY_POLICY: {item.name} ({item.item_type})]"
            else:
                new_snippet = privacy.sanitize_text(item.snippet)

            sanitized_item = ContextItem(
                id=item.id,
                name=item.name,
                item_type=item.item_type,
                file_path=item.file_path,
                relevance_score=item.relevance_score,
                reasons=item.reasons,
                snippet=new_snippet,
                estimated_tokens=len(new_snippet) // 4 + 1,
                metadata=item.metadata,
            )
            sanitized_items.append(sanitized_item)

        redacted.selected_items = sanitized_items

        # Regenerate llm_context markdown with sanitized items
        redacted.llm_context = self._format_redacted_llm_context(redacted, decision)

        # Update privacy summary
        redacted.privacy_summary.redactions_count += privacy.redactions_count
        if "Context redaction applied under privacy policy enforcement." not in redacted.privacy_summary.notes:
            redacted.privacy_summary.notes.append("Context redaction applied under privacy policy enforcement.")

        return redacted

    def _format_redacted_llm_context(self, res: ContextResult, decision: PolicyDecision) -> str:
        """Format sanitized LLM context markdown."""
        sections: list[str] = []
        sections.append(f"# Project Context: {res.project_name}")
        sections.append(
            f"MSK Policy Enforcement: Context transmission sanitized under rule: {decision.reason_code}."
        )

        req = res.request_summary
        if req.get("question"):
            sections.append(f"## Developer Query\n{req['question']}")
        if req.get("file") or req.get("symbol"):
            focus = []
            if req.get("file"):
                focus.append(f"File: `{req['file']}`")
            if req.get("symbol"):
                focus.append(f"Symbol: `{req['symbol']}`")
            sections.append("## Focus Target\n" + ", ".join(focus))

        if res.selected_items:
            sections.append("## Relevant Code Entities (Sanitized)")
            for item in res.selected_items:
                reasons_str = "; ".join(item.reasons)
                sections.append(
                    f"### [{item.item_type.upper()}] {item.name} (Score: {item.relevance_score:.1f})\n"
                    f"*Location: `{item.file_path}` | Relevance: {reasons_str}*\n\n"
                    f"```text\n{item.snippet}\n```"
                )

        if res.relationships:
            sections.append("## Relevant Architecture & Relationship Paths")
            for rel in res.relationships[:10]:
                path_str = " -> ".join(rel.path) if len(rel.path) > 1 else rel.reason
                sections.append(f"- **{rel.relationship_type}**: {path_str}")

        if res.related_tests:
            sections.append("## Recommended Verification Tests")
            for t in res.related_tests[:10]:
                sections.append(f"- `{t}`")

        return "\n\n".join(sections)
