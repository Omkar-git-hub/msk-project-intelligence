"""Data models for MSK Smart Context Engine."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from msk.policy.models import PolicyDecision


class ContextItemType(StrEnum):
    """Types of project intelligence entities that can be included in context."""

    FILE = "file"
    SYMBOL = "symbol"
    TEST = "test"
    DEPENDENCY = "dependency"
    API_ENDPOINT = "api_endpoint"
    INFRASTRUCTURE = "infrastructure"
    SECURITY = "security"


class ContextRequest(BaseModel):
    """Structured request for smart context selection."""

    question: str | None = None
    file: str | None = None
    symbol: str | None = None
    error: str | None = None
    budget_tokens: int = 2000


class ContextItem(BaseModel):
    """A selected, ranked piece of project context."""

    id: str
    name: str
    item_type: str
    file_path: str
    relevance_score: float
    reasons: list[str] = Field(default_factory=list)
    snippet: str
    estimated_tokens: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelationshipEvidence(BaseModel):
    """Structural evidence explaining how selected items relate to each other."""

    source: str
    target: str
    relationship_type: str
    depth: int = 1
    path: list[str] = Field(default_factory=list)
    reason: str


class OmittedItem(BaseModel):
    """A candidate item excluded due to context budget limits."""

    name: str
    item_type: str
    file_path: str
    relevance_score: float
    estimated_tokens: int
    reason: str


class BudgetMetrics(BaseModel):
    """Metrics tracking context size and budget utilization."""

    token_budget: int
    estimated_tokens_used: int
    utilization_pct: float
    total_candidates: int
    selected_count: int
    omitted_count: int


class PrivacySummary(BaseModel):
    """Privacy protections applied during context selection."""

    local_only: bool = True
    redactions_count: int = 0
    excluded_sensitive_files: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ContextResult(BaseModel):
    """Structured, deterministic result of smart context analysis."""

    project_name: str
    analyzed_at: str
    request_summary: dict[str, Any]
    budget_metrics: BudgetMetrics
    privacy_summary: PrivacySummary
    selected_items: list[ContextItem] = Field(default_factory=list)
    relationships: list[RelationshipEvidence] = Field(default_factory=list)
    related_tests: list[str] = Field(default_factory=list)
    omitted_items: list[OmittedItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    llm_context: str = ""
    policy_decision: PolicyDecision | None = None
