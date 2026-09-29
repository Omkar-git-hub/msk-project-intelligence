"""Domain models for the Project Knowledge Model."""

from typing import Any
from pydantic import BaseModel, Field


class ProjectEntity(BaseModel):
    """Represents a project root."""

    id: str
    name: str
    root_path: str
    created_at: str
    updated_at: str


class FileEntity(BaseModel):
    """Represents an analyzed project file."""

    id: str
    project_id: str
    path: str
    language: str | None
    file_type: str = "source"  # source, test, config, documentation, infrastructure, manifest
    size_bytes: int
    content_hash: str
    structural_hash: str
    modified_at: str
    is_test: bool = False
    is_sensitive: bool = False
    security_flags: list[str] = Field(default_factory=list)


class SymbolEntity(BaseModel):
    """Represents an extracted code symbol."""

    id: str
    file_id: str
    name: str
    type: str  # class, interface, enum, function, method, constructor
    parent_symbol: str | None = None
    line_start: int
    line_end: int
    visibility: str = "public"  # public, private, protected
    is_test: bool = False
    is_api_endpoint: bool = False
    api_route: str | None = None
    api_method: str | None = None
    calls: list[str] = Field(default_factory=list)


class DependencyEntity(BaseModel):
    """Represents an external or intra-project dependency."""

    id: str
    project_id: str
    source: str
    target: str
    version_spec: str | None = None
    type: str  # package, module


class RelationshipEntity(BaseModel):
    """Represents a directional edge in the project knowledge graph."""

    id: str
    project_id: str
    source_id: str
    target_id: str
    relationship_type: str
    metadata_json: str | None = None


class TestEntity(BaseModel):
    """Represents a discovered test suite or test case."""

    id: str
    project_id: str
    file_id: str
    name: str
    test_type: str  # unit, integration, e2e, suite, case
    framework: str | None = None  # pytest, junit, jest, mocha, unittest
    target_symbol: str | None = None


class ApiEndpointEntity(BaseModel):
    """Represents an API route or endpoint discovered in code."""

    id: str
    project_id: str
    file_id: str
    route: str
    http_method: str  # GET, POST, PUT, DELETE, PATCH, etc.
    handler_symbol: str | None = None


class InfrastructureEntity(BaseModel):
    """Represents infrastructure and deployment configuration."""

    id: str
    project_id: str
    file_id: str
    kind: str  # docker, docker-compose, github-actions, kubernetes, terraform, makefile
    path: str
    details: str | None = None


class SecurityFindingEntity(BaseModel):
    """Represents a detected security or privacy risk.

    CRITICAL PRIVACY GUARANTEE: This model NEVER stores raw secret values,
    passwords, tokens, or private keys. It only records rule IDs, line numbers,
    and sanitized descriptions.
    """

    id: str
    project_id: str
    file_id: str
    rule_id: str  # e.g., SENSITIVE_FILE, POTENTIAL_SECRET, PRIVATE_KEY_PATTERN
    severity: str  # LOW, MEDIUM, HIGH, CRITICAL
    line_number: int | None = None
    description: str


class ProjectKnowledgeSummary(BaseModel):
    """Summary statistics for msk status output."""

    project_name: str
    total_files: int
    languages_count: int
    languages_breakdown: dict[str, int] = Field(default_factory=dict)
    total_symbols: int
    symbols_breakdown: dict[str, int] = Field(default_factory=dict)
    total_dependencies: int
    total_relationships: int
    total_tests: int = 0
    total_api_endpoints: int = 0
    total_infrastructure: int = 0
    last_updated: str
    git_branch: str | None = None
    git_dirty: bool = False
    secrets_detected: int = 0
    sensitive_files: int = 0
    status: str = "READY"
