"""Repository for persisting and querying the Project Knowledge Model."""

import json
from msk.knowledge.models import (
    ApiEndpointEntity,
    DependencyEntity,
    FileEntity,
    InfrastructureEntity,
    ProjectEntity,
    ProjectKnowledgeSummary,
    RelationshipEntity,
    SecurityFindingEntity,
    SymbolEntity,
    TestEntity,
)
from msk.storage.database import DatabaseManager


class KnowledgeRepository:
    """Data access layer for MSK SQLite knowledge store."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db = db_manager

    def save_project(self, project: ProjectEntity) -> None:
        """Upsert project record."""
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO projects (id, name, root_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    root_path=excluded.root_path,
                    updated_at=excluded.updated_at
                """,
                (project.id, project.name, project.root_path, project.created_at, project.updated_at),
            )

    def get_project(self, project_id: str) -> ProjectEntity | None:
        """Fetch project by ID."""
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not row:
                return None
            return ProjectEntity(
                id=row["id"],
                name=row["name"],
                root_path=row["root_path"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def clear_project_data(self, project_id: str) -> None:
        """Clear all indexed data for a project while keeping the project record."""
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM security_findings WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM infrastructure WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM api_endpoints WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM tests WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM relationships WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM dependencies WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM files WHERE project_id = ?", (project_id,))

    def save_files(self, files: list[FileEntity]) -> None:
        """Batch insert files."""
        if not files:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO files (
                    id, project_id, path, language, file_type, size_bytes,
                    content_hash, structural_hash, modified_at, is_test, is_sensitive, security_flags
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    language=excluded.language,
                    file_type=excluded.file_type,
                    size_bytes=excluded.size_bytes,
                    content_hash=excluded.content_hash,
                    structural_hash=excluded.structural_hash,
                    modified_at=excluded.modified_at,
                    is_test=excluded.is_test,
                    is_sensitive=excluded.is_sensitive,
                    security_flags=excluded.security_flags
                """,
                [
                    (
                        f.id,
                        f.project_id,
                        f.path,
                        f.language,
                        f.file_type,
                        f.size_bytes,
                        f.content_hash,
                        f.structural_hash,
                        f.modified_at,
                        1 if f.is_test else 0,
                        1 if f.is_sensitive else 0,
                        json.dumps(f.security_flags) if f.security_flags else None,
                    )
                    for f in files
                ],
            )

    def get_all_files(self, project_id: str) -> list[FileEntity]:
        """Fetch all files belonging to a project."""
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT * FROM files WHERE project_id = ? ORDER BY path", (project_id,)).fetchall()
            return [
                FileEntity(
                    id=r["id"],
                    project_id=r["project_id"],
                    path=r["path"],
                    language=r["language"],
                    file_type=r["file_type"] if "file_type" in r.keys() else "source",
                    size_bytes=r["size_bytes"],
                    content_hash=r["content_hash"],
                    structural_hash=r["structural_hash"],
                    modified_at=r["modified_at"],
                    is_test=bool(r["is_test"]) if "is_test" in r.keys() else False,
                    is_sensitive=bool(r["is_sensitive"]) if "is_sensitive" in r.keys() else False,
                    security_flags=json.loads(r["security_flags"]) if ("security_flags" in r.keys() and r["security_flags"]) else [],
                )
                for r in rows
            ]

    def save_symbols(self, symbols: list[SymbolEntity]) -> None:
        """Batch insert extracted symbols."""
        if not symbols:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO symbols (
                    id, file_id, name, type, parent_symbol, line_start, line_end,
                    visibility, is_test, is_api_endpoint, api_route, api_method, calls_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    type=excluded.type,
                    parent_symbol=excluded.parent_symbol,
                    line_start=excluded.line_start,
                    line_end=excluded.line_end,
                    visibility=excluded.visibility,
                    is_test=excluded.is_test,
                    is_api_endpoint=excluded.is_api_endpoint,
                    api_route=excluded.api_route,
                    api_method=excluded.api_method,
                    calls_json=excluded.calls_json
                """,
                [
                    (
                        s.id,
                        s.file_id,
                        s.name,
                        s.type,
                        s.parent_symbol,
                        s.line_start,
                        s.line_end,
                        s.visibility,
                        1 if s.is_test else 0,
                        1 if s.is_api_endpoint else 0,
                        s.api_route,
                        s.api_method,
                        json.dumps(s.calls) if s.calls else None,
                    )
                    for s in symbols
                ],
            )

    def get_all_symbols(self, project_id: str) -> list[SymbolEntity]:
        """Fetch all symbols in a project."""
        with self.db.transaction() as conn:
            rows = conn.execute(
                """
                SELECT s.* FROM symbols s
                JOIN files f ON s.file_id = f.id
                WHERE f.project_id = ?
                ORDER BY s.name
                """,
                (project_id,),
            ).fetchall()
            return [
                SymbolEntity(
                    id=r["id"],
                    file_id=r["file_id"],
                    name=r["name"],
                    type=r["type"],
                    parent_symbol=r["parent_symbol"],
                    line_start=r["line_start"],
                    line_end=r["line_end"],
                    visibility=r["visibility"],
                    is_test=bool(r["is_test"]) if "is_test" in r.keys() else False,
                    is_api_endpoint=bool(r["is_api_endpoint"]) if "is_api_endpoint" in r.keys() else False,
                    api_route=r["api_route"] if "api_route" in r.keys() else None,
                    api_method=r["api_method"] if "api_method" in r.keys() else None,
                    calls=json.loads(r["calls_json"]) if ("calls_json" in r.keys() and r["calls_json"]) else [],
                )
                for r in rows
            ]

    def save_dependencies(self, deps: list[DependencyEntity]) -> None:
        """Batch insert dependencies with conflict resolution."""
        if not deps:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO dependencies (id, project_id, source, target, version_spec, type)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                [(d.id, d.project_id, d.source, d.target, d.version_spec, d.type) for d in deps],
            )

    def get_all_dependencies(self, project_id: str) -> list[DependencyEntity]:
        """Fetch all dependencies for a project."""
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT * FROM dependencies WHERE project_id = ?", (project_id,)).fetchall()
            return [
                DependencyEntity(
                    id=r["id"],
                    project_id=r["project_id"],
                    source=r["source"],
                    target=r["target"],
                    version_spec=r["version_spec"],
                    type=r["type"],
                )
                for r in rows
            ]

    def save_relationships(self, rels: list[RelationshipEntity]) -> None:
        """Batch insert knowledge relationships."""
        if not rels:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO relationships (id, project_id, source_id, target_id, relationship_type, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                [(r.id, r.project_id, r.source_id, r.target_id, r.relationship_type, r.metadata_json) for r in rels],
            )

    def get_all_relationships(self, project_id: str) -> list[RelationshipEntity]:
        """Fetch all relationships for a project."""
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT * FROM relationships WHERE project_id = ?", (project_id,)).fetchall()
            return [
                RelationshipEntity(
                    id=r["id"],
                    project_id=r["project_id"],
                    source_id=r["source_id"],
                    target_id=r["target_id"],
                    relationship_type=r["relationship_type"],
                    metadata_json=r["metadata_json"],
                )
                for r in rows
            ]

    def save_tests(self, tests: list[TestEntity]) -> None:
        """Batch insert test suite/case entities."""
        if not tests:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO tests (id, project_id, file_id, name, test_type, framework, target_symbol)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    test_type=excluded.test_type,
                    framework=excluded.framework,
                    target_symbol=excluded.target_symbol
                """,
                [(t.id, t.project_id, t.file_id, t.name, t.test_type, t.framework, t.target_symbol) for t in tests],
            )

    def get_all_tests(self, project_id: str) -> list[TestEntity]:
        """Fetch all tests for a project."""
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT * FROM tests WHERE project_id = ? ORDER BY name", (project_id,)).fetchall()
            return [
                TestEntity(
                    id=r["id"],
                    project_id=r["project_id"],
                    file_id=r["file_id"],
                    name=r["name"],
                    test_type=r["test_type"],
                    framework=r["framework"],
                    target_symbol=r["target_symbol"],
                )
                for r in rows
            ]

    def save_api_endpoints(self, endpoints: list[ApiEndpointEntity]) -> None:
        """Batch insert API endpoints."""
        if not endpoints:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO api_endpoints (id, project_id, file_id, route, http_method, handler_symbol)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    route=excluded.route,
                    http_method=excluded.http_method,
                    handler_symbol=excluded.handler_symbol
                """,
                [(e.id, e.project_id, e.file_id, e.route, e.http_method, e.handler_symbol) for e in endpoints],
            )

    def get_all_api_endpoints(self, project_id: str) -> list[ApiEndpointEntity]:
        """Fetch all API endpoints for a project."""
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT * FROM api_endpoints WHERE project_id = ? ORDER BY route", (project_id,)).fetchall()
            return [
                ApiEndpointEntity(
                    id=r["id"],
                    project_id=r["project_id"],
                    file_id=r["file_id"],
                    route=r["route"],
                    http_method=r["http_method"],
                    handler_symbol=r["handler_symbol"],
                )
                for r in rows
            ]

    def save_infrastructure(self, infra_list: list[InfrastructureEntity]) -> None:
        """Batch insert infrastructure metadata."""
        if not infra_list:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO infrastructure (id, project_id, file_id, kind, path, details)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind=excluded.kind,
                    path=excluded.path,
                    details=excluded.details
                """,
                [(i.id, i.project_id, i.file_id, i.kind, i.path, i.details) for i in infra_list],
            )

    def get_all_infrastructure(self, project_id: str) -> list[InfrastructureEntity]:
        """Fetch all infrastructure metadata for a project."""
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT * FROM infrastructure WHERE project_id = ? ORDER BY kind", (project_id,)).fetchall()
            return [
                InfrastructureEntity(
                    id=r["id"],
                    project_id=r["project_id"],
                    file_id=r["file_id"],
                    kind=r["kind"],
                    path=r["path"],
                    details=r["details"],
                )
                for r in rows
            ]

    def save_security_findings(self, findings: list[SecurityFindingEntity]) -> None:
        """Batch insert security findings (sanitized, no raw secret tokens)."""
        if not findings:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO security_findings (id, project_id, file_id, rule_id, severity, line_number, description)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    severity=excluded.severity,
                    line_number=excluded.line_number,
                    description=excluded.description
                """,
                [(s.id, s.project_id, s.file_id, s.rule_id, s.severity, s.line_number, s.description) for s in findings],
            )

    def get_all_security_findings(self, project_id: str) -> list[SecurityFindingEntity]:
        """Fetch all security findings for a project."""
        with self.db.transaction() as conn:
            rows = conn.execute("SELECT * FROM security_findings WHERE project_id = ? ORDER BY severity", (project_id,)).fetchall()
            return [
                SecurityFindingEntity(
                    id=r["id"],
                    project_id=r["project_id"],
                    file_id=r["file_id"],
                    rule_id=r["rule_id"],
                    severity=r["severity"],
                    line_number=r["line_number"],
                    description=r["description"],
                )
                for r in rows
            ]

    def get_summary(self, project_id: str) -> ProjectKnowledgeSummary:
        """Generate high-level summary metrics for CLI display."""
        with self.db.transaction() as conn:
            proj = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            project_name = proj["name"] if proj else "Unknown"
            updated_at = proj["updated_at"] if proj else ""

            # Files breakdown
            file_rows = conn.execute(
                """
                SELECT language, count(*) as cnt
                FROM files WHERE project_id = ?
                GROUP BY language
                """,
                (project_id,),
            ).fetchall()
            total_files = sum(r["cnt"] for r in file_rows)
            languages_breakdown = {r["language"] or "unknown": r["cnt"] for r in file_rows}

            # Symbols breakdown
            sym_rows = conn.execute(
                """
                SELECT s.type, count(*) as cnt
                FROM symbols s
                JOIN files f ON s.file_id = f.id
                WHERE f.project_id = ?
                GROUP BY s.type
                """,
                (project_id,),
            ).fetchall()
            total_symbols = sum(r["cnt"] for r in sym_rows)
            symbols_breakdown = {r["type"]: r["cnt"] for r in sym_rows}

            # Dependencies count
            dep_count = conn.execute(
                "SELECT count(*) as cnt FROM dependencies WHERE project_id = ?", (project_id,)
            ).fetchone()["cnt"]

            # Relationships count
            rel_count = conn.execute(
                "SELECT count(*) as cnt FROM relationships WHERE project_id = ?", (project_id,)
            ).fetchone()["cnt"]

            # Tests, APIs, Infra count
            test_count = conn.execute(
                "SELECT count(*) as cnt FROM tests WHERE project_id = ?", (project_id,)
            ).fetchone()["cnt"]

            api_count = conn.execute(
                "SELECT count(*) as cnt FROM api_endpoints WHERE project_id = ?", (project_id,)
            ).fetchone()["cnt"]

            infra_count = conn.execute(
                "SELECT count(*) as cnt FROM infrastructure WHERE project_id = ?", (project_id,)
            ).fetchone()["cnt"]

            sec_count = conn.execute(
                "SELECT count(*) as cnt FROM security_findings WHERE project_id = ?", (project_id,)
            ).fetchone()["cnt"]

            sensitive_file_count = conn.execute(
                "SELECT count(*) as cnt FROM files WHERE project_id = ? AND is_sensitive = 1", (project_id,)
            ).fetchone()["cnt"]

            return ProjectKnowledgeSummary(
                project_name=project_name,
                total_files=total_files,
                languages_count=len(languages_breakdown),
                languages_breakdown=languages_breakdown,
                total_symbols=total_symbols,
                symbols_breakdown=symbols_breakdown,
                total_dependencies=dep_count,
                total_relationships=rel_count,
                total_tests=test_count,
                total_api_endpoints=api_count,
                total_infrastructure=infra_count,
                last_updated=updated_at,
                secrets_detected=sec_count,
                sensitive_files=sensitive_file_count,
                status="READY",
            )
