"""Builds and links knowledge graph entities from analysis results."""

import hashlib
from pathlib import Path

from msk.analyzer.analyzer import FileAnalysisResult
from msk.graph.relationships import RelationshipType
from msk.knowledge.models import (
    ApiEndpointEntity,
    DependencyEntity,
    FileEntity,
    InfrastructureEntity,
    RelationshipEntity,
    SecurityFindingEntity,
    SymbolEntity,
    TestEntity,
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
        """Construct files, symbols, dependencies, tests, APIs, and relationships, saving to SQLite."""
        file_entities: dict[str, FileEntity] = {}
        symbol_entities: dict[str, SymbolEntity] = {}
        dependency_entities: dict[str, DependencyEntity] = {}
        relationships: dict[str, RelationshipEntity] = {}
        tests: dict[str, TestEntity] = {}
        api_endpoints: dict[str, ApiEndpointEntity] = {}
        infrastructure: dict[str, InfrastructureEntity] = {}
        security_findings: dict[str, SecurityFindingEntity] = {}

        # Lookup caches for cross-referencing
        file_id_by_path: dict[str, str] = {}
        symbol_by_name: dict[str, list[SymbolEntity]] = {}

        # 1. Process files, symbols, infrastructure, and security findings
        for res in analysis_results:
            file_id = _make_id("file", self.project_id, res.relative_path)
            file_id_by_path[res.relative_path] = file_id

            file_entity = FileEntity(
                id=file_id,
                project_id=self.project_id,
                path=res.relative_path,
                language=res.language,
                file_type=res.file_type,
                size_bytes=res.size_bytes,
                content_hash=res.content_hash,
                structural_hash=res.structural_hash,
                modified_at=res.modified_at,
                is_test=res.is_test,
                is_sensitive=res.is_sensitive,
                security_flags=res.security_flags,
            )
            file_entities[file_id] = file_entity

            # Infrastructure
            if res.infrastructure:
                infra_id = _make_id("infra", self.project_id, file_id, res.infrastructure.kind)
                infrastructure[infra_id] = InfrastructureEntity(
                    id=infra_id,
                    project_id=self.project_id,
                    file_id=file_id,
                    kind=res.infrastructure.kind,
                    path=res.infrastructure.path,
                    details=res.infrastructure.details,
                )

            # Security findings (sanitized, zero secret values stored)
            for sf in res.security_findings:
                sec_id = _make_id("sec", self.project_id, file_id, sf.rule_id, str(sf.line_number or 0))
                security_findings[sec_id] = SecurityFindingEntity(
                    id=sec_id,
                    project_id=self.project_id,
                    file_id=file_id,
                    rule_id=sf.rule_id,
                    severity=sf.severity,
                    line_number=sf.line_number,
                    description=sf.description,
                )

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
                    is_test=sym.is_test,
                    is_api_endpoint=sym.is_api_endpoint,
                    api_route=sym.api_route,
                    api_method=sym.api_method,
                    calls=sym.calls,
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

                # Test entity
                if sym.is_test:
                    test_id = _make_id("test", self.project_id, file_id, sym.name)
                    tests[test_id] = TestEntity(
                        id=test_id,
                        project_id=self.project_id,
                        file_id=file_id,
                        name=sym.name,
                        test_type="suite" if sym.type == "class" else "case",
                        framework="pytest" if res.language == "python" else ("junit" if res.language == "java" else "jest"),
                    )

                # API Endpoint entity
                if sym.is_api_endpoint and sym.api_route:
                    api_id = _make_id("api", self.project_id, file_id, sym.api_route, sym.api_method or "GET")
                    api_endpoints[api_id] = ApiEndpointEntity(
                        id=api_id,
                        project_id=self.project_id,
                        file_id=file_id,
                        route=sym.api_route,
                        http_method=sym.api_method or "GET",
                        handler_symbol=sym.name,
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

        # 2. Correlate imports, dependencies, calls, and test targets across files
        for res in analysis_results:
            source_file_id = file_id_by_path[res.relative_path]

            # 2a. Import relationships
            for imp in res.imports:
                for imported_name in imp.names:
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

            # 2b. Call relationships (SYMBOL_CALLS_SYMBOL) and test targets (TEST_TARGETS_SYMBOL)
            for sym in res.symbols:
                sym_id = _make_id("sym", source_file_id, sym.name, str(sym.line_start))
                for called_name in sym.calls:
                    if called_name in symbol_by_name:
                        for target_sym in symbol_by_name[called_name]:
                            if target_sym.id == sym_id:
                                continue

                            # Relationship: SYMBOL_CALLS_SYMBOL
                            rel_call_id = _make_id(
                                "rel",
                                sym_id,
                                target_sym.id,
                                RelationshipType.SYMBOL_CALLS_SYMBOL,
                            )
                            relationships[rel_call_id] = RelationshipEntity(
                                id=rel_call_id,
                                project_id=self.project_id,
                                source_id=sym_id,
                                target_id=target_sym.id,
                                relationship_type=RelationshipType.SYMBOL_CALLS_SYMBOL,
                            )

                            # If caller is a test symbol or in a test file -> TEST_TARGETS_SYMBOL
                            if sym.is_test or res.is_test:
                                if not target_sym.is_test:
                                    rel_test_id = _make_id(
                                        "rel",
                                        sym_id,
                                        target_sym.id,
                                        RelationshipType.TEST_TARGETS_SYMBOL,
                                    )
                                    relationships[rel_test_id] = RelationshipEntity(
                                        id=rel_test_id,
                                        project_id=self.project_id,
                                        source_id=sym_id,
                                        target_id=target_sym.id,
                                        relationship_type=RelationshipType.TEST_TARGETS_SYMBOL,
                                    )
                                    # Update test entity target_symbol if matching
                                    test_lookup_id = _make_id("test", self.project_id, source_file_id, sym.name)
                                    if test_lookup_id in tests and not tests[test_lookup_id].target_symbol:
                                        tests[test_lookup_id].target_symbol = target_sym.name

        # 3. Save into SQLite repository
        file_list = list(file_entities.values())
        sym_list = list(symbol_entities.values())
        dep_list = list(dependency_entities.values())
        rel_list = list(relationships.values())
        test_list = list(tests.values())
        api_list = list(api_endpoints.values())
        infra_list = list(infrastructure.values())
        sec_list = list(security_findings.values())

        self.repo.save_files(file_list)
        self.repo.save_symbols(sym_list)
        self.repo.save_dependencies(dep_list)
        self.repo.save_relationships(rel_list)
        self.repo.save_tests(test_list)
        self.repo.save_api_endpoints(api_list)
        self.repo.save_infrastructure(infra_list)
        self.repo.save_security_findings(sec_list)

        # 4. Export JSON artifacts for inspection
        if export_exports:
            export_dir = self.project_root / ".msk" / "exports"
            export_project_knowledge(
                export_dir,
                file_list,
                sym_list,
                rel_list,
                test_list,
                api_list,
                infra_list,
                sec_list,
            )
