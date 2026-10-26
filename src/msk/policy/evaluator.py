"""Deterministic policy evaluation engine for MSK context sharing."""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from msk.analyzer.security import SENSITIVE_FILE_PATTERNS, is_sensitive_path
from msk.config.models import PolicyAction, PolicyConfig
from msk.policy.models import (
    ContextCategory,
    PolicyDecision,
    PolicyDecisionOutcome,
    PolicyReasonCode,
)

logger = logging.getLogger("msk.policy.evaluator")


class PolicyEvaluator:
    """Evaluates privacy policy rules against context entities and transmission requests.

    Applies deterministic precedence: DENY > REDACT > ALLOW.
    Fails closed: any evaluation error or invalid configuration defaults to DENY for external sharing.
    """

    def __init__(self, policy: PolicyConfig | None = None) -> None:
        self.policy = policy or PolicyConfig()

    def evaluate_request(
        self,
        destination: str = "local",
        provider: str | None = None,
        categories: list[str] | None = None,
        file_paths: list[str] | None = None,
        redactions_count: int = 0,
        has_secrets: bool = False,
        context_items: list[dict[str, Any]] | None = None,
    ) -> PolicyDecision:
        """Evaluate policy rules and produce a deterministic decision."""
        dest = destination.lower().strip()
        now_iso = datetime.now(UTC).isoformat()

        # Wrap entire evaluation to guarantee fail-closed behavior
        try:
            return self._evaluate_internal(
                dest=dest,
                provider=provider,
                categories=categories or [],
                file_paths=file_paths or [],
                redactions_count=redactions_count,
                has_secrets=has_secrets,
                context_items=context_items or [],
                now_iso=now_iso,
            )
        except Exception as exc:
            logger.warning("Policy evaluation encountered an error, failing closed: %s", exc)
            return PolicyDecision(
                outcome=PolicyDecisionOutcome.DENY,
                reason_code=PolicyReasonCode.POLICY_EVALUATION_ERROR,
                explanation=f"Policy evaluation failure: {exc}. Failing closed to deny external sharing.",
                destination=dest,
                provider=provider,
                timestamp=now_iso,
            )

    def _evaluate_internal(
        self,
        dest: str,
        provider: str | None,
        categories: list[str],
        file_paths: list[str],
        redactions_count: int,
        has_secrets: bool,
        context_items: list[dict[str, Any]],
        now_iso: str,
    ) -> PolicyDecision:
        """Internal evaluation logic with strict precedence."""
        # 1. Local destination evaluation
        if dest == "local":
            if redactions_count > 0:
                return PolicyDecision(
                    outcome=PolicyDecisionOutcome.REDACT,
                    reason_code=PolicyReasonCode.LOCAL_OPERATION_ALLOWED,
                    explanation="Local context preparation allowed with secret scrubbing applied.",
                    destination=dest,
                    provider=provider or "local",
                    redactions_count=redactions_count,
                    redacted_categories=[ContextCategory.SECRETS.value],
                    timestamp=now_iso,
                )
            return PolicyDecision(
                outcome=PolicyDecisionOutcome.ALLOW,
                reason_code=PolicyReasonCode.LOCAL_OPERATION_ALLOWED,
                explanation="Local context preparation allowed strictly offline.",
                destination=dest,
                provider=provider or "local",
                allowed_categories=categories or [ContextCategory.SOURCE_CODE.value],
                timestamp=now_iso,
            )

        # 2. External destination checks
        # Check 2.1: Is external LLM enabled?
        if not self.policy.external_llm.enabled:
            return PolicyDecision(
                outcome=PolicyDecisionOutcome.DENY,
                reason_code=PolicyReasonCode.EXTERNAL_AI_DISABLED,
                explanation="External AI context transmission is disabled by policy.",
                destination=dest,
                provider=provider,
                timestamp=now_iso,
            )

        # Check 2.2: Is project sensitivity restricted?
        if self.policy.project_sensitivity == "restricted":
            return PolicyDecision(
                outcome=PolicyDecisionOutcome.DENY,
                reason_code=PolicyReasonCode.SENSITIVE_PROJECT_BLOCKED,
                explanation="External AI context sharing is blocked for restricted projects.",
                destination=dest,
                provider=provider,
                timestamp=now_iso,
            )

        # Check 2.3: Is provider approved?
        if provider:
            allowed = [p.lower() for p in self.policy.external_llm.allowed_providers]
            if provider.lower() not in allowed:
                return PolicyDecision(
                    outcome=PolicyDecisionOutcome.DENY,
                    reason_code=PolicyReasonCode.PROVIDER_NOT_ALLOWED,
                    explanation=(
                        f"External AI provider '{provider}' is not in approved providers list: "
                        f"{self.policy.external_llm.allowed_providers}."
                    ),
                    destination=dest,
                    provider=provider,
                    timestamp=now_iso,
                )

        # 3. Item and Category-level evaluation
        denied_items: list[str] = []
        redacted_categories: list[str] = []
        allowed_categories: list[str] = []
        needs_redaction = False

        # 3.1 Check file paths against blocked_files and sensitive patterns
        all_files = set(file_paths)
        for item in context_items:
            fp = item.get("file_path")
            if fp:
                all_files.add(fp)

        for fp in sorted(all_files):
            # Check explicit blocked files
            if self._matches_patterns(fp, self.policy.blocked_files):
                denied_items.append(f"blocked_file:{fp}")
                continue

            # Check sensitive file patterns
            is_sens, _ = is_sensitive_path(fp)
            if not is_sens:
                for pat in SENSITIVE_FILE_PATTERNS:
                    if re.search(pat, Path(fp).name, re.IGNORECASE):
                        is_sens = True
                        break

            if not is_sens and self.policy.security.custom_sensitive_patterns:
                if self._matches_patterns(fp, self.policy.security.custom_sensitive_patterns):
                    is_sens = True

            if is_sens:
                if self.policy.security.sensitive_files == PolicyAction.BLOCK:
                    denied_items.append(f"sensitive_file:{fp}")
                elif self.policy.security.sensitive_files == PolicyAction.SANITIZE:
                    if self.policy.allow_external_redaction:
                        needs_redaction = True
                        if ContextCategory.SENSITIVE_FILES.value not in redacted_categories:
                            redacted_categories.append(ContextCategory.SENSITIVE_FILES.value)
                    else:
                        denied_items.append(f"sensitive_file:{fp}")

            # Check allowed_files if configured
            if self.policy.allowed_files:
                if not self._matches_patterns(fp, self.policy.allowed_files):
                    denied_items.append(f"unapproved_file:{fp}")

        # 3.2 Check secrets
        if has_secrets or redactions_count > 0:
            if self.policy.security.secrets == PolicyAction.BLOCK:
                denied_items.append("detected_secrets")
            elif self.policy.security.secrets == PolicyAction.SANITIZE:
                needs_redaction = True
                if ContextCategory.SECRETS.value not in redacted_categories:
                    redacted_categories.append(ContextCategory.SECRETS.value)

        # 3.3 Check category rules
        all_categories = set(categories)
        for item in context_items:
            itype = item.get("item_type")
            if itype:
                all_categories.add(itype)

        for cat in sorted(all_categories):
            cat_action = self.policy.category_rules.get(cat)
            if cat_action is None:
                # Check normalized category name
                cat_action = self.policy.category_rules.get(cat.lower())

            if cat_action in (PolicyAction.BLOCK, "deny", "block"):
                denied_items.append(f"category:{cat}")
            elif cat_action in (PolicyAction.SANITIZE, "redact", "sanitize"):
                needs_redaction = True
                if cat not in redacted_categories:
                    redacted_categories.append(cat)
            else:
                if cat not in allowed_categories:
                    allowed_categories.append(cat)

        # 4. Resolve Precedence: DENY > REDACT > ALLOW
        if denied_items:
            # Determine specific reason code
            if any(item.startswith("sensitive_file:") or item.startswith("blocked_file:") for item in denied_items):
                code = PolicyReasonCode.PROHIBITED_FILE_BLOCKED
            elif any(item == "detected_secrets" for item in denied_items):
                code = PolicyReasonCode.SECRETS_DETECTED_BLOCKED
            else:
                code = PolicyReasonCode.PROHIBITED_CATEGORY_BLOCKED

            return PolicyDecision(
                outcome=PolicyDecisionOutcome.DENY,
                reason_code=code,
                explanation=f"Context transmission blocked due to prohibited items/rules: {', '.join(denied_items[:5])}.",
                destination=dest,
                provider=provider,
                denied_items=denied_items,
                timestamp=now_iso,
            )

        if needs_redaction:
            return PolicyDecision(
                outcome=PolicyDecisionOutcome.REDACT,
                reason_code=PolicyReasonCode.REDACTION_APPLIED,
                explanation="Context transmission permitted with redactions applied according to privacy policy.",
                destination=dest,
                provider=provider,
                redactions_count=redactions_count,
                redacted_categories=redacted_categories,
                allowed_categories=allowed_categories,
                timestamp=now_iso,
            )

        return PolicyDecision(
            outcome=PolicyDecisionOutcome.ALLOW,
            reason_code=PolicyReasonCode.EXPLICITLY_APPROVED,
            explanation="Context transmission explicitly permitted by active privacy policy.",
            destination=dest,
            provider=provider,
            allowed_categories=allowed_categories or [ContextCategory.SOURCE_CODE.value],
            timestamp=now_iso,
        )

    @staticmethod
    def _matches_patterns(path_str: str, patterns: list[str]) -> bool:
        """Check if path string or basename matches any regex or glob pattern."""
        if not patterns:
            return False
        norm = path_str.replace("\\", "/").lower()
        base = Path(path_str).name.lower()

        for pat in patterns:
            try:
                # Treat pattern as regex
                if re.search(pat, norm, re.IGNORECASE) or re.search(pat, base, re.IGNORECASE):
                    return True
            except re.error:
                # Fallback to substring match
                if pat.lower() in norm or pat.lower() in base:
                    return True
        return False
