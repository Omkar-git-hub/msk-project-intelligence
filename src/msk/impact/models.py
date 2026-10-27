"""Data models for MSK Impact Analysis Engine."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from msk.policy.models import PolicyDecision


class ImpactCategory(StrEnum):
    """Semantic category explaining how/why an item is impacted."""

    CALLER = "caller"
    IMPORTER = "importer"
    DEPENDENT_FILE = "dependent_file"
    CONTAINED_SYMBOL = "contained_symbol"
    PARENT_SYMBOL = "parent_symbol"
    CHILD_SYMBOL = "child_symbol"
    TEST = "test"
    API_ENDPOINT = "api_endpoint"
    INFRASTRUCTURE = "infrastructure"
    DEPENDENCY = "dependency"


class ImpactClassification(StrEnum):
    """Direct vs transitive impact designation."""

    DIRECT = "direct"
    TRANSITIVE = "transitive"


class ImpactItem(BaseModel):
    """Represents a discrete downstream entity impacted by the queried change."""

    name: str
    type: str  # symbol, file, test, api_endpoint, infrastructure, dependency
    file_path: str
    category: str
    classification: str  # direct, transitive
    depth: int
    relationship_type: str
    path: list[str] = Field(default_factory=list)
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class ImpactReport(BaseModel):
    """Structured, deterministic report summarizing full downstream impact."""

    target_type: str  # file, symbol, dependency
    target_name: str
    target_file: str | None = None
    project_name: str
    analyzed_at: str
    max_depth: int
    total_affected: int
    direct_count: int
    transitive_count: int
    summary_by_type: dict[str, int] = Field(default_factory=dict)
    summary_by_category: dict[str, int] = Field(default_factory=dict)
    items: list[ImpactItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    policy_decision: PolicyDecision | None = None
