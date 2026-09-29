"""Export knowledge model to JSON files for inspection and interoperability."""

import json
from pathlib import Path

from msk.knowledge.models import FileEntity, RelationshipEntity, SymbolEntity


def export_project_knowledge(
    export_dir: str | Path,
    files: list[FileEntity],
    symbols: list[SymbolEntity],
    relationships: list[RelationshipEntity],
) -> None:
    """Generate structure.json, symbols.json, and graph.json in export_dir."""
    target_dir = Path(export_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. structure.json
    structure_data = [
        {
            "id": f.id,
            "path": f.path,
            "language": f.language,
            "size_bytes": f.size_bytes,
            "content_hash": f.content_hash,
            "structural_hash": f.structural_hash,
            "modified_at": f.modified_at,
        }
        for f in files
    ]
    with (target_dir / "structure.json").open("w", encoding="utf-8") as f_out:
        f_out.write(json.dumps(structure_data, indent=2))

    # 2. symbols.json
    symbols_data = [
        {
            "id": s.id,
            "file_id": s.file_id,
            "name": s.name,
            "type": s.type,
            "parent_symbol": s.parent_symbol,
            "line_start": s.line_start,
            "line_end": s.line_end,
            "visibility": s.visibility,
        }
        for s in symbols
    ]
    with (target_dir / "symbols.json").open("w", encoding="utf-8") as f_out:
        f_out.write(json.dumps(symbols_data, indent=2))

    # 3. graph.json
    graph_data = {
        "nodes": [{"id": f.id, "type": "file", "label": f.path} for f in files]
        + [{"id": s.id, "type": "symbol", "label": s.name, "kind": s.type} for s in symbols],
        "relationships": [
            {
                "id": r.id,
                "source_id": r.source_id,
                "target_id": r.target_id,
                "type": r.relationship_type,
                "metadata": json.loads(r.metadata_json) if r.metadata_json else {},
            }
            for r in relationships
        ],
    }
    with (target_dir / "graph.json").open("w", encoding="utf-8") as f_out:
        f_out.write(json.dumps(graph_data, indent=2))

