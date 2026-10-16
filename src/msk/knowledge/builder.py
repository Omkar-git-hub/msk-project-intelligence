"""Builds and links knowledge graph entities from analysis results."""

from pathlib import Path

from msk.analyzer.analyzer import FileAnalysisResult
from msk.knowledge.models import (
    ApiEndpointEntity,
    DependencyEntity,
    FileEntity,
    InfrastructureEntity,
    SecurityFindingEntity,
    SymbolEntity,
    TestEntity,
    make_entity_id,
)
from msk.knowledge.repository import KnowledgeRepository
from msk.knowledge.serializer import export_from_repository

# Alias for backwards compatibility
_make_id = make_entity_id


def extract_entities_from_analysis(
    project_id: str,
    analysis_results: list[FileAnalysisResult],
) -> tuple[
    list[FileEntity],
    list[SymbolEntity],
    list[DependencyEntity],
    list[TestEntity],
    list[ApiEndpointEntity],
    list[InfrastructureEntity],
    list[SecurityFindingEntity],
]:
    """Convert raw file analysis results into discrete database entities."""
    file_entities: list[FileEntity] = []
    symbol_entities: list[SymbolEntity] = []
    dependency_entities: list[DependencyEntity] = []
    test_entities: list[TestEntity] = []
    api_entities: list[ApiEndpointEntity] = []
    infra_entities: list[InfrastructureEntity] = []
    security_findings: list[SecurityFindingEntity] = []

    for res in analysis_results:
        file_id = make_entity_id("file", project_id, res.relative_path)
        imported_names = [name for imp in res.imports for name in imp.names]

        file_entities.append(
            FileEntity(
                id=file_id,
                project_id=project_id,
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
                imports=imported_names,
            )
        )

        # Infrastructure
        if res.infrastructure:
            infra_id = make_entity_id("infra", project_id, file_id, res.infrastructure.kind)
            infra_entities.append(
                InfrastructureEntity(
                    id=infra_id,
                    project_id=project_id,
                    file_id=file_id,
                    kind=res.infrastructure.kind,
                    path=res.infrastructure.path,
                    details=res.infrastructure.details,
                )
            )

        # Security findings (sanitized, zero secret values stored)
        for sf in res.security_findings:
            sec_id = make_entity_id("sec", project_id, file_id, sf.rule_id, str(sf.line_number or 0))
            security_findings.append(
                SecurityFindingEntity(
                    id=sec_id,
                    project_id=project_id,
                    file_id=file_id,
                    rule_id=sf.rule_id,
                    severity=sf.severity,
                    line_number=sf.line_number,
                    description=sf.description,
                )
            )

        # Symbols
        for sym in res.symbols:
            sym_id = make_entity_id("sym", file_id, sym.name, str(sym.line_start))
            symbol_entities.append(
                SymbolEntity(
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
            )

            # Test entity
            if sym.is_test:
                test_id = make_entity_id("test", project_id, file_id, sym.name)
                test_entities.append(
                    TestEntity(
                        id=test_id,
                        project_id=project_id,
                        file_id=file_id,
                        name=sym.name,
                        test_type="suite" if sym.type == "class" else "case",
                        framework="pytest" if res.language == "python" else ("junit" if res.language == "java" else "jest"),
                    )
                )

            # API Endpoint entity
            if sym.is_api_endpoint and sym.api_route:
                api_id = make_entity_id("api", project_id, file_id, sym.api_route, sym.api_method or "GET")
                api_entities.append(
                    ApiEndpointEntity(
                        id=api_id,
                        project_id=project_id,
                        file_id=file_id,
                        route=sym.api_route,
                        http_method=sym.api_method or "GET",
                        handler_symbol=sym.name,
                    )
                )

        # Manifest dependencies
        for dep in res.manifest_dependencies:
            dep_id = make_entity_id("dep", project_id, dep.source_file, dep.name, dep.version_spec or "")
            dependency_entities.append(
                DependencyEntity(
                    id=dep_id,
                    project_id=project_id,
                    source=dep.source_file,
                    target=dep.name,
                    version_spec=dep.version_spec,
                    type="package",
                )
            )

    return (
        file_entities,
        symbol_entities,
        dependency_entities,
        test_entities,
        api_entities,
        infra_entities,
        security_findings,
    )


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
        """Construct knowledge graph entities from analysis results and save to SQLite."""
        self.repo.clear_project_data(self.project_id)
        self.repo.apply_incremental_update(self.project_id, [], analysis_results)

        if export_exports:
            export_dir = self.project_root / ".msk" / "exports"
            export_from_repository(self.repo, self.project_id, export_dir)
