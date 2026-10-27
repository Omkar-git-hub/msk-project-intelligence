"""Pluggable AI Gateway interface and policy transmission gate.

Architecture Guarantee:
    POLICY ENGINE -> ALLOW/BLOCK/REDACT -> AI GATEWAY

All outbound transmissions to AI backends must pass through PolicyGatedGateway.
If a transmission is evaluated as DENY by PolicyEnforcementBoundary, it is
strictly blocked before reaching the network adapter.
"""

from typing import TYPE_CHECKING, Any, Protocol

from msk.config.models import PolicyConfig
from msk.policy.audit import PolicyAuditor
from msk.policy.models import PolicyDecision, PolicyDecisionOutcome

if TYPE_CHECKING:
    from msk.context.models import ContextResult
    from msk.policy.boundary import PolicyEnforcementBoundary


class AIGatewayAdapter(Protocol):
    """Protocol interface for pluggable AI/LLM backends."""

    def send_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Transmit context payload to the AI provider and return the response."""
        ...


class MockAIGateway:
    """Offline, local-first simulation gateway for deterministic testing.

    Makes zero network requests, requires zero API keys, and securely records
    the received payload for verification.
    """

    def __init__(self, canned_response: dict[str, Any] | None = None) -> None:
        self.canned_response = canned_response
        self.call_count: int = 0
        self.last_payload: dict[str, Any] | None = None

    def send_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Simulate local AI response without external communication."""
        self.call_count += 1
        self.last_payload = payload

        if self.canned_response is not None:
            return dict(self.canned_response)

        return {
            "status": "success",
            "provider": payload.get("provider", "mock"),
            "destination": payload.get("destination", "local"),
            "items_received": len(payload.get("items", [])),
            "simulated_response": "Offline local simulation response. The project stays private.",
        }


class PolicyGatedGateway:
    """Policy-gated AI Gateway wrapper ensuring no unsanctioned data leaves the environment.

    Guarantees:
    - Every transmission is evaluated against active project privacy policy.
    - If policy outcome is DENY, transmission is strictly aborted and the adapter is not called.
    - If policy outcome is REDACT, only sanitized context items are forwarded.
    - Every transmission attempt produces a privacy-safe audit record.
    """

    def __init__(
        self,
        adapter: AIGatewayAdapter,
        boundary: "PolicyEnforcementBoundary | None" = None,
        policy: PolicyConfig | None = None,
        auditor: PolicyAuditor | None = None,
        project_id: str | None = None,
    ) -> None:
        self.adapter = adapter
        if boundary is None:
            from msk.policy.boundary import PolicyEnforcementBoundary

            self.boundary = PolicyEnforcementBoundary(
                policy=policy or PolicyConfig(),
                auditor=auditor,
                project_id=project_id,
            )
        else:
            self.boundary = boundary

    def transmit(
        self,
        context_result: "ContextResult",
        destination: str = "local",
        provider: str | None = None,
    ) -> tuple[dict[str, Any] | None, PolicyDecision]:
        """Evaluate policy on ContextResult and transmit to the AI adapter if permitted.

        Returns:
            (response_dict, PolicyDecision) if ALLOW or REDACT.
            (None, PolicyDecision) if DENY.
        """
        sanitized_result, decision = self.boundary.enforce_transmission(
            context_result=context_result,
            destination=destination,
            provider=provider,
        )

        # Abort if DENY
        if decision.outcome == PolicyDecisionOutcome.DENY or sanitized_result is None:
            return (None, decision)

        # Package sanitized payload
        payload: dict[str, Any] = {
            "project_name": sanitized_result.project_name,
            "destination": destination,
            "provider": provider,
            "items": [
                {
                    "name": item.name,
                    "item_type": item.item_type,
                    "file_path": item.file_path,
                    "snippet": item.snippet,
                }
                for item in sanitized_result.selected_items
            ],
            "llm_context": sanitized_result.llm_context,
            "privacy_summary": sanitized_result.privacy_summary.model_dump(),
        }

        # Call underlying adapter
        response = self.adapter.send_context(payload)
        return (response, decision)
