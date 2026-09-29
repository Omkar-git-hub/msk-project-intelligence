"""Domain models for the Project Knowledge Model."""

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
    size_bytes: int
    content_hash: str
    structural_hash: str
    modified_at: str


class SymbolEntity(BaseModel):
    """Represents an extracted code symbol."""

    id: str
    file_id: str
    name: str
    type: str  # class, method, function, interface, etc.
    parent_symbol: str | None = None
    line_start: int
    line_end: int
    visibility: str = "public"


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
    last_updated: str
    git_branch: str | None = None
    git_dirty: bool = False
    secrets_detected: int = 0
    sensitive_files: int = 0
    status: str = "READY"
