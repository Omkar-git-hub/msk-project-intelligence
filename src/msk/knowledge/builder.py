"""Builds and links knowledge graph entities from analysis results."""

import hashlib
from pathlib import Path
from typing import Any
import uuid

from msk.analyzer.analyzer import FileAnalysisResult
from msk.graph.relationships import RelationshipType
from msk.knowledge.models import (
    DependencyEntity,
    FileEntity,
    ProjectEntity,
    RelationshipEntity,
    SymbolEntity,
)
from msk.knowledge.repository import KnowledgeRepository
from msk.knowledge.serializer import export_project_knowledge


def _make_id(prefix: str, *parts: str) -> str:
    """Create a deterministic identifier from composite strings."""
    key = ":".join(parts)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


class KnowledgeBuilder:
    """Orchestrates building knowledge graph entities from analysis results."""

    def __init__(self, project_id: str, project_root: str | Path, repo: KnowledgeRepository) -> None:
        self.project_id = project_id
        self.project_root = Path(project_root).resolve()
        self.repo = repo

    def build_and_save(
        self,
        analysis_results: list[FileAnalysisResult],
        export_exports: bool = True,
    ) -> None:
        """Construct files, symbols, dependencies, and relationships, saving to SQLite."""
        file_entities: dict[str, FileEntity] = {}
        symbol_entities: dict[str, SymbolEntity] = {}
        dependency_entities: dict[str, DependencyEntity] = {}
        relationships: dict[str, RelationshipEntity] = {}

        # Lookup caches for cross-referencing
        file_id_by_path: dict[str, str] = {}
        symbol_by_name: dict[str, list[SymbolEntity]] = {}

        # 1. Process files and symbols
        for res in analysis_results:
            file_id = _make_id("file", self.project_id, res.relative_path)
            file_id_by_path[res.relative_path] = file_id

            file_entity = FileEntity(
                id=file_id,
                project_id=self.project_id,
                path=res.relative_path,
                language=res.language,
                size_bytes=res.size_bytes,
                content_hash=res.content_hash,
                structural_hash=res.structural_hash,
                modified_at=res.modified_at,
            )
            file_entities[file_id] = file_entity

            # Symbols
            for sym in res.symbols:
                sym_id = _make_id("sym", file_id, sym.name, str(sym.line_start))
                sym_entity = SymbolEntity(
                    id=sym_id,
                    file_id=file_id,
                    name=sym.name,
                    type=sym.type,
                    parent_symbol=sym.parent_symbol,
                    line_start=sym.line_start,
                    line_end=sym.line_end,
                    visibility=sym.visibility,
                )
                symbol_entities[sym_id] = sym_entity
                symbol_by_name.setdefault(sym.name, []).append(sym_entity)

                # Relationship: FILE_CONTAINS_SYMBOL
                rel_id = _make_id("rel", file_id, sym_id, RelationshipType.FILE_CONTAINS_SYMBOL)
                relationships[rel_id] = RelationshipEntity(
                    id=rel_id,
                    project_id=self.project_id,
                    source_id=file_id,
                    target_id=sym_id,
                    relationship_type=RelationshipType.FILE_CONTAINS_SYMBOL,
                )

            # Manifest dependencies
            for dep in res.manifest_dependencies:
                dep_id = _make_id("dep", self.project_id, dep.source_file, dep.name, dep.version_spec or "")
                dependency_entities[dep_id] = DependencyEntity(
                    id=dep_id,
                    project_id=self.project_id,
                    source=dep.source_file,
                    target=dep.name,
                    version_spec=dep.version_spec,
                    type="package",
                )

        # 2. Correlate imports and dependencies across files
        for res in analysis_results:
            source_file_id = file_id_by_path[res.relative_path]
            is_test_file = (
                "test" in res.relative_path.lower() or Path(res.relative_path).name.startswith("test_")
            )

            for imp in res.imports:
                for imported_name in imp.names:
                    # Check if imported name matches a project symbol
                    if imported_name in symbol_by_name:
                        for target_sym in symbol_by_name[imported_name]:
                            if target_sym.file_id == source_file_id:
                                continue

                            # Relationship: SYMBOL_IMPORTS_SYMBOL
                            rel_sym_id = _make_id(
                                "rel",
                                source_file_id,
                                target_sym.id,
                                RelationshipType.SYMBOL_IMPORTS_SYMBOL,
                            )
                            relationships[rel_sym_id] = RelationshipEntity(
                                id=rel_sym_id,
                                project_id=self.project_id,
                                source_id=source_file_id,
                                target_id=target_sym.id,
                                relationship_type=RelationshipType.SYMBOL_IMPORTS_SYMBOL,
                            )

                            # Relationship: FILE_DEPENDS_ON_FILE
                            rel_file_id = _make_id(
                                "rel",
                                source_file_id,
                                target_sym.file_id,
                                RelationshipType.FILE_DEPENDS_ON_FILE,
                            )
                            relationships[rel_file_id] = RelationshipEntity(
                                id=rel_file_id,
                                project_id=self.project_id,
                                source_id=source_file_id,
                                target_id=target_sym.file_id,
                                relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
                            )

                            # If test file imports target symbol -> TEST_TARGETS_SYMBOL
                            if is_test_file:
                                rel_test_id = _make_id(
                                    "rel",
                                    source_file_id,
                                    target_sym.id,
                                    RelationshipType.TEST_TARGETS_SYMBOL,
                                )
                                relationships[rel_test_id] = RelationshipEntity(
                                    id=rel_test_id,
                                    project_id=self.project_id,
                                    source_id=source_file_id,
                                    target_id=target_sym.id,
                                    relationship_type=RelationshipType.TEST_TARGETS_SYMBOL,
                                )

        # 3. Save into SQLite repository
        file_list = list(file_entities.values())
        sym_list = list(symbol_entities.values())
        dep_list = list(dependency_entities.values())
        rel_list = list(relationships.values())

        self.repo.save_files(file_list)
        self.repo.save_symbols(sym_list)
        self.repo.save_dependencies(dep_list)
        self.repo.save_relationships(rel_list)

        # 4. Export JSON artifacts for inspection
        if export_exports:
            export_dir = self.project_root / ".msk" / "exports"
            export_project_knowledge(export_dir, file_list, sym_list, rel_list)
