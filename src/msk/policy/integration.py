"""Policy Integration Service providing centralized policy checking and subsystem helpers."""

import fnmatch
import logging
import re
from pathlib import Path
from typing import Any

from msk.config.loader import get_msk_dir, load_policy_config
from msk.config.models import PolicyAction, PolicyConfig
from msk.policy.audit import PolicyAuditor
from msk.policy.models import ContextCategory, PolicyDecision, PolicyDecisionOutcome, PolicyReasonCode
from msk.storage.database import DatabaseManager

logger = logging.getLogger("msk.policy.integration")


class PolicyIntegrationService:
    """Centralized policy integration service for MSK subsystems."""

    @staticmethod
    def is_file_blocked(file_path: str, policy: PolicyConfig) -> bool:
        """Determine if a file path is blocked under the active policy.

        Order of evaluation:
        1. If file matches any pattern in allowed_files, it is permitted (not blocked).
        2. If file matches any pattern in blocked_files, it is blocked.
        """
        norm_path = file_path.replace("\\", "/").lstrip("/")

        # Check allowed_files override first
        for pattern in policy.allowed_files:
            if PolicyIntegrationService._matches_pattern(norm_path, pattern):
                return False

        # Check blocked_files
        for pattern in policy.blocked_files:
            if PolicyIntegrationService._matches_pattern(norm_path, pattern):
                return True

        return False

    @staticmethod
    def _matches_pattern(path: str, pattern: str) -> bool:
        """Check if path matches a regex pattern or fnmatch glob pattern."""
        # Check fnmatch glob
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(Path(path).name, pattern):
            return True
        # Check regex
        try:
            if re.search(pattern, path):
                return True
        except re.error:
            pass
        return False

    @staticmethod
    def is_category_blocked(category: str, policy: PolicyConfig) -> bool:
        """Determine if an intelligence entity category is blocked under category rules."""
        norm_cat = category.lower().strip()

        category_aliases: dict[str, str] = {
            "test": ContextCategory.TESTS.value,
            "tests": ContextCategory.TESTS.value,
            "symbol": ContextCategory.SYMBOLS.value,
            "symbols": ContextCategory.SYMBOLS.value,
            "source": ContextCategory.SOURCE_CODE.value,
            "source_code": ContextCategory.SOURCE_CODE.value,
            "file": ContextCategory.SOURCE_CODE.value,
            "dependency": ContextCategory.DEPENDENCIES.value,
            "dependencies": ContextCategory.DEPENDENCIES.value,
            "security": ContextCategory.SECURITY_FINDINGS.value,
            "security_findings": ContextCategory.SECURITY_FINDINGS.value,
            "architecture": ContextCategory.ARCHITECTURE.value,
            "api_endpoint": ContextCategory.ARCHITECTURE.value,
            "infra": ContextCategory.INFRASTRUCTURE.value,
            "infrastructure": ContextCategory.INFRASTRUCTURE.value,
        }

        # Check raw string
        action = policy.category_rules.get(norm_cat)
        if action is not None:
            return action in (PolicyAction.BLOCK, "block", "deny")

        # Check canonical alias
        canonical = category_aliases.get(norm_cat)
        if canonical and canonical in policy.category_rules:
            action = policy.category_rules[canonical]
            return action in (PolicyAction.BLOCK, "block", "deny")

        # ContextCategory enum lookup
        for cat_enum in ContextCategory:
            if cat_enum.value == norm_cat or cat_enum.name.lower() == norm_cat:
                enum_action = policy.category_rules.get(cat_enum.value)
                if enum_action is not None:
                    return enum_action in (PolicyAction.BLOCK, "block", "deny")

        return False

    @staticmethod
    def filter_candidate_items(
        candidates: list[Any],
        policy: PolicyConfig,
    ) -> tuple[list[Any], list[Any]]:
        """Filter a list of candidates into (allowed_items, blocked_items).

        Works with ContextItem objects, ImpactItem objects, or dicts with file_path and item_type.
        """
        allowed: list[Any] = []
        blocked: list[Any] = []

        for item in candidates:
            # Extract file_path and category
            file_path = getattr(item, "file_path", None) or (item.get("file_path") if isinstance(item, dict) else None)
            category = getattr(item, "item_type", None) or getattr(item, "category", None) or (
                item.get("category") if isinstance(item, dict) else None
            )

            is_blocked = False
            if file_path and PolicyIntegrationService.is_file_blocked(str(file_path), policy):
                is_blocked = True
            elif category and PolicyIntegrationService.is_category_blocked(str(category), policy):
                is_blocked = True

            if is_blocked:
                blocked.append(item)
            else:
                allowed.append(item)

        return (allowed, blocked)

    @staticmethod
    def get_policy_summary(repo_path: Path) -> dict[str, Any]:
        """Aggregate active policy configuration and audit metrics for reporting and dashboard."""
        project_root = Path(repo_path).resolve()
        msk_dir = get_msk_dir(project_root)
        policy_file = msk_dir / "policy.json"
        is_configured = policy_file.is_file()

        policy = load_policy_config(project_root, fallback_to_default=True)

        # Count audit events
        audit_events_count = 0
        db_path = msk_dir / "msk.db"
        if db_path.is_file():
            try:
                db = DatabaseManager(db_path)
                with db.transaction() as conn:
                    row = conn.execute("SELECT COUNT(*) as cnt FROM policy_audit_logs").fetchone()
                    if row:
                        audit_events_count = row["cnt"]
            except Exception as exc:
                logger.debug("Could not query policy_audit_logs count: %s", exc)
        elif (msk_dir / "policy_audit.jsonl").is_file():
            try:
                lines = (msk_dir / "policy_audit.jsonl").read_text(encoding="utf-8").splitlines()
                audit_events_count = sum(1 for line in lines if line.strip())
            except Exception as exc:
                logger.debug("Could not read policy_audit.jsonl count: %s", exc)

        return {
            "configured": is_configured,
            "version": policy.version,
            "sensitivity": policy.project_sensitivity,
            "external_llm_enabled": policy.external_llm.enabled,
            "allowed_providers": policy.external_llm.allowed_providers,
            "strict_redaction": policy.external_llm.enforce_strict_redaction,
            "blocked_files_count": len(policy.blocked_files),
            "category_rules_count": len(policy.category_rules),
            "audit_events_count": audit_events_count,
            "policy": policy,
        }

    @staticmethod
    def enforce_external_payload(
        payload: dict[str, Any],
        destination: str,
        provider: str | None,
        project_root: Path | None = None,
        policy: PolicyConfig | None = None,
        project_id: str | None = None,
    ) -> tuple[bool, dict[str, Any], PolicyDecision]:
        """Evaluate and enforce policy against an arbitrary payload meant for external destination."""
        active_policy = policy or (load_policy_config(project_root, fallback_to_default=True) if project_root else PolicyConfig())

        # If destination is local, allow directly
        if destination == "local":
            decision = PolicyDecision(
                outcome=PolicyDecisionOutcome.ALLOW,
                reason_code=PolicyReasonCode.LOCAL_OPERATION_ALLOWED,
                explanation="Local operation allowed without external transmission.",
                destination="local",
                provider=provider,
            )
            return (True, payload, decision)

        # Check external LLM enabled
        if not active_policy.external_llm.enabled:
            decision = PolicyDecision(
                outcome=PolicyDecisionOutcome.DENY,
                reason_code=PolicyReasonCode.EXTERNAL_AI_DISABLED,
                explanation="External AI communication is disabled by project privacy policy.",
                destination=destination,
                provider=provider,
            )
            PolicyIntegrationService._record_audit(project_root, project_id, decision, payload)
            return (False, payload, decision)

        # Check provider allowance
        if provider and provider not in active_policy.external_llm.allowed_providers:
            decision = PolicyDecision(
                outcome=PolicyDecisionOutcome.DENY,
                reason_code=PolicyReasonCode.PROVIDER_NOT_ALLOWED,
                explanation=f"Provider '{provider}' is not in allowed providers list: {active_policy.external_llm.allowed_providers}",
                destination=destination,
                provider=provider,
            )
            PolicyIntegrationService._record_audit(project_root, project_id, decision, payload)
            return (False, payload, decision)

        # Check project sensitivity
        if active_policy.project_sensitivity == "restricted":
            decision = PolicyDecision(
                outcome=PolicyDecisionOutcome.DENY,
                reason_code=PolicyReasonCode.SENSITIVE_PROJECT_BLOCKED,
                explanation="Projects with 'restricted' sensitivity cannot transmit context to external AI.",
                destination=destination,
                provider=provider,
            )
            PolicyIntegrationService._record_audit(project_root, project_id, decision, payload)
            return (False, payload, decision)

        # Allow external transmission
        decision = PolicyDecision(
            outcome=PolicyDecisionOutcome.ALLOW,
            reason_code=PolicyReasonCode.EXPLICITLY_APPROVED,
            explanation=f"External transmission to '{provider or destination}' approved by policy.",
            destination=destination,
            provider=provider,
        )
        PolicyIntegrationService._record_audit(project_root, project_id, decision, payload)
        return (True, payload, decision)

    @staticmethod
    def _record_audit(
        project_root: Path | None,
        project_id: str | None,
        decision: PolicyDecision,
        payload: dict[str, Any],
    ) -> None:
        """Safely record an audit log entry for external policy enforcement without leaking sensitive data."""
        if not project_root:
            return
        try:
            msk_dir = get_msk_dir(project_root)
            db_path = msk_dir / "msk.db"
            db = DatabaseManager(db_path) if db_path.is_file() else None
            auditor = PolicyAuditor(db=db, project_root=project_root)
            items_count = len(payload.get("items", [])) if isinstance(payload, dict) else 0
            auditor.record_decision(
                project_id=project_id or "unknown",
                decision=decision,
                categories=list(payload.get("categories", [])) if isinstance(payload, dict) else [],
                item_count=items_count,
            )
        except Exception as exc:
            logger.debug("Failed to record audit log during enforce_external_payload: %s", exc)
