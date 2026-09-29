"""Export knowledge model to JSON files for inspection and interoperability."""

import json
from pathlib import Path

from msk.knowledge.models import (
    ApiEndpointEntity,
    FileEntity,
    InfrastructureEntity,
    RelationshipEntity,
    SecurityFindingEntity,
    SymbolEntity,
    TestEntity,
)


def export_project_knowledge(
    export_dir: str | Path,
    files: list[FileEntity],
    symbols: list[SymbolEntity],
    relationships: list[RelationshipEntity],
    tests: list[TestEntity] | None = None,
    apis: list[ApiEndpointEntity] | None = None,
    infrastructure: list[InfrastructureEntity] | None = None,
    security_findings: list[SecurityFindingEntity] | None = None,
) -> None:
    """Generate deterministic JSON exports in export_dir."""
    target_dir = Path(export_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. structure.json
    structure_data = [
        {
            "id": f.id,
            "path": f.path,
            "language": f.language,
            "file_type": f.file_type,
            "size_bytes": f.size_bytes,
            "content_hash": f.content_hash,
            "structural_hash": f.structural_hash,
            "modified_at": f.modified_at,
            "is_test": f.is_test,
            "is_sensitive": f.is_sensitive,
            "security_flags": f.security_flags,
        }
        for f in sorted(files, key=lambda x: x.path)
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
            "is_test": s.is_test,
            "is_api_endpoint": s.is_api_endpoint,
            "api_route": s.api_route,
            "api_method": s.api_method,
            "calls": s.calls,
        }
        for s in sorted(symbols, key=lambda x: (x.file_id, x.line_start, x.name))
    ]
    with (target_dir / "symbols.json").open("w", encoding="utf-8") as f_out:
        f_out.write(json.dumps(symbols_data, indent=2))

    # 3. graph.json
    graph_data = {
        "nodes": [{"id": f.id, "type": "file", "label": f.path} for f in sorted(files, key=lambda x: x.path)]
        + [
            {"id": s.id, "type": "symbol", "label": s.name, "kind": s.type}
            for s in sorted(symbols, key=lambda x: (x.file_id, x.line_start))
        ],
        "relationships": [
            {
                "id": r.id,
                "source_id": r.source_id,
                "target_id": r.target_id,
                "type": r.relationship_type,
                "metadata": json.loads(r.metadata_json) if r.metadata_json else {},
            }
            for r in sorted(relationships, key=lambda x: (x.relationship_type, x.source_id, x.target_id))
        ],
    }
    with (target_dir / "graph.json").open("w", encoding="utf-8") as f_out:
        f_out.write(json.dumps(graph_data, indent=2))

    # 4. tests.json
    tests_data = [
        {
            "id": t.id,
            "file_id": t.file_id,
            "name": t.name,
            "test_type": t.test_type,
            "framework": t.framework,
            "target_symbol": t.target_symbol,
        }
        for t in sorted(tests or [], key=lambda x: (x.file_id, x.name))
    ]
    with (target_dir / "tests.json").open("w", encoding="utf-8") as f_out:
        f_out.write(json.dumps(tests_data, indent=2))

    # 5. apis.json
    apis_data = [
        {
            "id": a.id,
            "file_id": a.file_id,
            "route": a.route,
            "http_method": a.http_method,
            "handler_symbol": a.handler_symbol,
        }
        for a in sorted(apis or [], key=lambda x: (x.route, x.http_method))
    ]
    with (target_dir / "apis.json").open("w", encoding="utf-8") as f_out:
        f_out.write(json.dumps(apis_data, indent=2))

    # 6. infrastructure.json
    infra_data = [
        {
            "id": i.id,
            "file_id": i.file_id,
            "kind": i.kind,
            "path": i.path,
            "details": i.details,
        }
        for i in sorted(infrastructure or [], key=lambda x: x.path)
    ]
    with (target_dir / "infrastructure.json").open("w", encoding="utf-8") as f_out:
        f_out.write(json.dumps(infra_data, indent=2))

    # 7. security.json (CRITICAL: zero secret strings, only sanitized findings)
    sec_data = [
        {
            "id": s.id,
            "file_id": s.file_id,
            "rule_id": s.rule_id,
            "severity": s.severity,
            "line_number": s.line_number,
            "description": s.description,
        }
        for s in sorted(security_findings or [], key=lambda x: (x.severity, x.file_id, x.line_number or 0))
    ]
    with (target_dir / "security.json").open("w", encoding="utf-8") as f_out:
        f_out.write(json.dumps(sec_data, indent=2))
