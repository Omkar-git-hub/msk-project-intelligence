"""Knowledge graph node representations."""

from typing import Any
from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    """A node in the Project Knowledge Graph."""

    id: str
    type: str  # file, symbol, dependency, component
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """A directed edge in the Project Knowledge Graph."""

    id: str
    source_id: str
    target_id: str
    relationship_type: str
    properties: dict[str, Any] = Field(default_factory=dict)
