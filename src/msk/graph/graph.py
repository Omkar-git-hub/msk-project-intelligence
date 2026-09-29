"""Knowledge graph traversal and query engine."""

from collections import defaultdict

from msk.graph.nodes import GraphEdge, GraphNode
from msk.graph.relationships import RelationshipType


class KnowledgeGraph:
    """In-memory graph constructed from SQLite repository for fast traversal."""

    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[str, GraphEdge] = {}
        self._out_edges: dict[str, list[str]] = defaultdict(list)
        self._in_edges: dict[str, list[str]] = defaultdict(list)

    def add_node(self, node: GraphNode) -> None:
        """Register a node in the graph."""
        self.nodes[node.id] = node

    def add_edge(self, edge: GraphEdge) -> None:
        """Register a directed edge in the graph."""
        self.edges[edge.id] = edge
        self._out_edges[edge.source_id].append(edge.id)
        self._in_edges[edge.target_id].append(edge.id)

    def get_outgoing(self, node_id: str, rel_type: RelationshipType | str | None = None) -> list[GraphEdge]:
        """Get outgoing edges from node_id, optionally filtered by relationship type."""
        edge_ids = self._out_edges.get(node_id, [])
        result = [self.edges[eid] for eid in edge_ids if eid in self.edges]
        if rel_type:
            target_type = str(rel_type)
            result = [e for e in result if e.relationship_type == target_type]
        return result

    def get_incoming(self, node_id: str, rel_type: RelationshipType | str | None = None) -> list[GraphEdge]:
        """Get incoming edges to node_id, optionally filtered by relationship type."""
        edge_ids = self._in_edges.get(node_id, [])
        result = [self.edges[eid] for eid in edge_ids if eid in self.edges]
        if rel_type:
            target_type = str(rel_type)
            result = [e for e in result if e.relationship_type == target_type]
        return result

    def to_dict(self) -> dict[str, list[dict]]:
        """Export serialized representation for JSON inspection."""
        return {
            "nodes": [n.model_dump() for n in self.nodes.values()],
            "edges": [e.model_dump() for e in self.edges.values()],
        }
