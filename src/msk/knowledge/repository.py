"""Repository for persisting and querying the Project Knowledge Model."""

import json
import sqlite3
from typing import TYPE_CHECKING

from msk.graph.relationships import RelationshipType
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
    make_entity_id,
)
from msk.storage.database import DatabaseManager

if TYPE_CHECKING:
    from msk.analyzer.analyzer import FileAnalysisResult


class KnowledgeRepository:
    """Data access layer for MSK SQLite knowledge store."""

    def __init__(self, db_manager: DatabaseManager) -> None:
        self.db = db_manager

    def save_project(self, project: ProjectEntity, conn: sqlite3.Connection | None = None) -> None:
        """Upsert project record."""
        def _op(c: sqlite3.Connection) -> None:
            c.execute(
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

        if conn is not None:
            _op(conn)
        else:
            with self.db.transaction() as c:
                _op(c)

    def get_project(self, project_id: str, conn: sqlite3.Connection | None = None) -> ProjectEntity | None:
        """Fetch project by ID."""
        def _op(c: sqlite3.Connection) -> ProjectEntity | None:
            row = c.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not row:
                return None
            return ProjectEntity(
                id=row["id"],
                name=row["name"],
                root_path=row["root_path"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

        if conn is not None:
            return _op(conn)
        with self.db.transaction() as c:
            return _op(c)

    def clear_project_data(self, project_id: str, conn: sqlite3.Connection | None = None) -> None:
        """Clear all indexed data for a project while keeping the project record."""
        def _op(c: sqlite3.Connection) -> None:
            c.execute("DELETE FROM security_findings WHERE project_id = ?", (project_id,))
            c.execute("DELETE FROM infrastructure WHERE project_id = ?", (project_id,))
            c.execute("DELETE FROM api_endpoints WHERE project_id = ?", (project_id,))
            c.execute("DELETE FROM tests WHERE project_id = ?", (project_id,))
            c.execute("DELETE FROM relationships WHERE project_id = ?", (project_id,))
            c.execute("DELETE FROM dependencies WHERE project_id = ?", (project_id,))
            c.execute("DELETE FROM files WHERE project_id = ?", (project_id,))

        if conn is not None:
            _op(conn)
        else:
            with self.db.transaction() as c:
                _op(c)

    def delete_files(
        self,
        project_id: str,
        file_paths: list[str],
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """Safely delete files and all associated symbols, edges, and dependencies."""
        if not file_paths:
            return

        def _op(c: sqlite3.Connection) -> None:
            for path in file_paths:
                file_row = c.execute(
                    "SELECT id FROM files WHERE project_id = ? AND path = ?",
                    (project_id, path),
                ).fetchone()
                if not file_row:
                    continue
                file_id = file_row["id"]

                # 1. Fetch all symbols for this file to clean up graph edges
                sym_rows = c.execute("SELECT id FROM symbols WHERE file_id = ?", (file_id,)).fetchall()
                sym_ids = [r["id"] for r in sym_rows]

                # 2. Delete relationships involving file_id
                c.execute(
                    "DELETE FROM relationships WHERE project_id = ? AND (source_id = ? OR target_id = ?)",
                    (project_id, file_id, file_id),
                )

                # 3. Delete relationships involving any symbol of this file
                for s_id in sym_ids:
                    c.execute(
                        "DELETE FROM relationships WHERE project_id = ? AND (source_id = ? OR target_id = ?)",
                        (project_id, s_id, s_id),
                    )

                # 4. Delete manifest dependencies originating from this file
                c.execute(
                    "DELETE FROM dependencies WHERE project_id = ? AND source = ?",
                    (project_id, path),
                )

                # 5. Delete file row (triggers ON DELETE CASCADE for symbols, tests, apis, infra, security)
                c.execute("DELETE FROM files WHERE id = ?", (file_id,))

        if conn is not None:
            _op(conn)
        else:
            with self.db.transaction() as c:
                _op(c)

    def save_files(self, files: list[FileEntity], conn: sqlite3.Connection | None = None) -> None:
        """Batch insert or update files."""
        if not files:
            return

        def _op(c: sqlite3.Connection) -> None:
            c.executemany(
                """
                INSERT INTO files (
                    id, project_id, path, language, file_type, size_bytes,
                    content_hash, structural_hash, modified_at, is_test, is_sensitive, security_flags, imports_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    language=excluded.language,
                    file_type=excluded.file_type,
                    size_bytes=excluded.size_bytes,
                    content_hash=excluded.content_hash,
                    structural_hash=excluded.structural_hash,
                    modified_at=excluded.modified_at,
                    is_test=excluded.is_test,
                    is_sensitive=excluded.is_sensitive,
                    security_flags=excluded.security_flags,
                    imports_json=excluded.imports_json
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
                        json.dumps(f.imports) if f.imports else None,
                    )
                    for f in files
                ],
            )

        if conn is not None:
            _op(conn)
        else:
            with self.db.transaction() as c:
                _op(c)

    def get_all_files(self, project_id: str, conn: sqlite3.Connection | None = None) -> list[FileEntity]:
        """Fetch all files belonging to a project."""
        def _op(c: sqlite3.Connection) -> list[FileEntity]:
            rows = c.execute("SELECT * FROM files WHERE project_id = ? ORDER BY path", (project_id,)).fetchall()
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
                    imports=json.loads(r["imports_json"]) if ("imports_json" in r.keys() and r["imports_json"]) else [],
                )
                for r in rows
            ]

        if conn is not None:
            return _op(conn)
        with self.db.transaction() as c:
            return _op(c)

    def save_symbols(self, symbols: list[SymbolEntity], conn: sqlite3.Connection | None = None) -> None:
        """Batch insert extracted symbols."""
        if not symbols:
            return

        def _op(c: sqlite3.Connection) -> None:
            c.executemany(
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

        if conn is not None:
            _op(conn)
        else:
            with self.db.transaction() as c:
                _op(c)

    def get_all_symbols(self, project_id: str, conn: sqlite3.Connection | None = None) -> list[SymbolEntity]:
        """Fetch all symbols in a project."""
        def _op(c: sqlite3.Connection) -> list[SymbolEntity]:
            rows = c.execute(
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

        if conn is not None:
            return _op(conn)
        with self.db.transaction() as c:
            return _op(c)

    def save_dependencies(self, deps: list[DependencyEntity], conn: sqlite3.Connection | None = None) -> None:
        """Batch insert dependencies with conflict resolution."""
        if not deps:
            return

        def _op(c: sqlite3.Connection) -> None:
            c.executemany(
                """
                INSERT INTO dependencies (id, project_id, source, target, version_spec, type)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                [(d.id, d.project_id, d.source, d.target, d.version_spec, d.type) for d in deps],
            )

        if conn is not None:
            _op(conn)
        else:
            with self.db.transaction() as c:
                _op(c)

    def get_all_dependencies(self, project_id: str, conn: sqlite3.Connection | None = None) -> list[DependencyEntity]:
        """Fetch all dependencies for a project."""
        def _op(c: sqlite3.Connection) -> list[DependencyEntity]:
            rows = c.execute("SELECT * FROM dependencies WHERE project_id = ?", (project_id,)).fetchall()
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

        if conn is not None:
            return _op(conn)
        with self.db.transaction() as c:
            return _op(c)

    def save_relationships(self, rels: list[RelationshipEntity], conn: sqlite3.Connection | None = None) -> None:
        """Batch insert knowledge relationships."""
        if not rels:
            return

        def _op(c: sqlite3.Connection) -> None:
            c.executemany(
                """
                INSERT INTO relationships (id, project_id, source_id, target_id, relationship_type, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
                """,
                [(r.id, r.project_id, r.source_id, r.target_id, r.relationship_type, r.metadata_json) for r in rels],
            )

        if conn is not None:
            _op(conn)
        else:
            with self.db.transaction() as c:
                _op(c)

    def get_all_relationships(self, project_id: str, conn: sqlite3.Connection | None = None) -> list[RelationshipEntity]:
        """Fetch all relationships for a project."""
        def _op(c: sqlite3.Connection) -> list[RelationshipEntity]:
            rows = c.execute("SELECT * FROM relationships WHERE project_id = ?", (project_id,)).fetchall()
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

        if conn is not None:
            return _op(conn)
        with self.db.transaction() as c:
            return _op(c)

    def save_tests(self, tests: list[TestEntity], conn: sqlite3.Connection | None = None) -> None:
        """Batch insert test suite/case entities."""
        if not tests:
            return

        def _op(c: sqlite3.Connection) -> None:
            c.executemany(
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

        if conn is not None:
            _op(conn)
        else:
            with self.db.transaction() as c:
                _op(c)

    def get_all_tests(self, project_id: str, conn: sqlite3.Connection | None = None) -> list[TestEntity]:
        """Fetch all tests for a project."""
        def _op(c: sqlite3.Connection) -> list[TestEntity]:
            rows = c.execute("SELECT * FROM tests WHERE project_id = ? ORDER BY name", (project_id,)).fetchall()
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

        if conn is not None:
            return _op(conn)
        with self.db.transaction() as c:
            return _op(c)

    def save_api_endpoints(self, endpoints: list[ApiEndpointEntity], conn: sqlite3.Connection | None = None) -> None:
        """Batch insert API endpoints."""
        if not endpoints:
            return

        def _op(c: sqlite3.Connection) -> None:
            c.executemany(
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

        if conn is not None:
            _op(conn)
        else:
            with self.db.transaction() as c:
                _op(c)

    def get_all_api_endpoints(self, project_id: str, conn: sqlite3.Connection | None = None) -> list[ApiEndpointEntity]:
        """Fetch all API endpoints for a project."""
        def _op(c: sqlite3.Connection) -> list[ApiEndpointEntity]:
            rows = c.execute("SELECT * FROM api_endpoints WHERE project_id = ? ORDER BY route", (project_id,)).fetchall()
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

        if conn is not None:
            return _op(conn)
        with self.db.transaction() as c:
            return _op(c)

    def save_infrastructure(self, infra_list: list[InfrastructureEntity], conn: sqlite3.Connection | None = None) -> None:
        """Batch insert infrastructure metadata."""
        if not infra_list:
            return

        def _op(c: sqlite3.Connection) -> None:
            c.executemany(
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

        if conn is not None:
            _op(conn)
        else:
            with self.db.transaction() as c:
                _op(c)

    def get_all_infrastructure(self, project_id: str, conn: sqlite3.Connection | None = None) -> list[InfrastructureEntity]:
        """Fetch all infrastructure metadata for a project."""
        def _op(c: sqlite3.Connection) -> list[InfrastructureEntity]:
            rows = c.execute("SELECT * FROM infrastructure WHERE project_id = ? ORDER BY kind", (project_id,)).fetchall()
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

        if conn is not None:
            return _op(conn)
        with self.db.transaction() as c:
            return _op(c)

    def save_security_findings(self, findings: list[SecurityFindingEntity], conn: sqlite3.Connection | None = None) -> None:
        """Batch insert security findings (sanitized, no raw secret tokens)."""
        if not findings:
            return

        def _op(c: sqlite3.Connection) -> None:
            c.executemany(
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

        if conn is not None:
            _op(conn)
        else:
            with self.db.transaction() as c:
                _op(c)

    def get_all_security_findings(self, project_id: str, conn: sqlite3.Connection | None = None) -> list[SecurityFindingEntity]:
        """Fetch all security findings for a project."""
        def _op(c: sqlite3.Connection) -> list[SecurityFindingEntity]:
            rows = c.execute("SELECT * FROM security_findings WHERE project_id = ? ORDER BY severity", (project_id,)).fetchall()
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

        if conn is not None:
            return _op(conn)
        with self.db.transaction() as c:
            return _op(c)

    def reconcile_relationships(self, project_id: str, conn: sqlite3.Connection | None = None) -> None:
        """Reconstruct and synchronize all canonical knowledge relationships for the project."""
        def _op(c: sqlite3.Connection) -> None:
            file_rows = c.execute(
                "SELECT id, path, is_test, imports_json FROM files WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            file_by_id: dict[str, dict] = {}
            for r in file_rows:
                file_by_id[r["id"]] = {
                    "path": r["path"],
                    "is_test": bool(r["is_test"]),
                    "imports": json.loads(r["imports_json"]) if r["imports_json"] else [],
                }

            sym_rows = c.execute(
                """
                SELECT s.id, s.file_id, s.name, s.type, s.is_test, s.calls_json
                FROM symbols s
                JOIN files f ON s.file_id = f.id
                WHERE f.project_id = ?
                """,
                (project_id,),
            ).fetchall()

            symbols = []
            symbol_by_name: dict[str, list[dict]] = {}
            for r in sym_rows:
                sym_dict = {
                    "id": r["id"],
                    "file_id": r["file_id"],
                    "name": r["name"],
                    "type": r["type"],
                    "is_test": bool(r["is_test"]),
                    "calls": json.loads(r["calls_json"]) if r["calls_json"] else [],
                }
                symbols.append(sym_dict)
                symbol_by_name.setdefault(r["name"], []).append(sym_dict)

            relationships: dict[str, RelationshipEntity] = {}

            # 1. FILE_CONTAINS_SYMBOL
            for s in symbols:
                rel_id = make_entity_id("rel", s["file_id"], s["id"], RelationshipType.FILE_CONTAINS_SYMBOL)
                relationships[rel_id] = RelationshipEntity(
                    id=rel_id,
                    project_id=project_id,
                    source_id=s["file_id"],
                    target_id=s["id"],
                    relationship_type=RelationshipType.FILE_CONTAINS_SYMBOL,
                )

            # 2. SYMBOL_IMPORTS_SYMBOL & FILE_DEPENDS_ON_FILE
            for file_id, f_data in file_by_id.items():
                for imp_name in f_data["imports"]:
                    if imp_name in symbol_by_name:
                        for target_sym in symbol_by_name[imp_name]:
                            if target_sym["file_id"] == file_id:
                                continue
                            rel_sym_id = make_entity_id(
                                "rel", file_id, target_sym["id"], RelationshipType.SYMBOL_IMPORTS_SYMBOL
                            )
                            relationships[rel_sym_id] = RelationshipEntity(
                                id=rel_sym_id,
                                project_id=project_id,
                                source_id=file_id,
                                target_id=target_sym["id"],
                                relationship_type=RelationshipType.SYMBOL_IMPORTS_SYMBOL,
                            )
                            rel_file_id = make_entity_id(
                                "rel", file_id, target_sym["file_id"], RelationshipType.FILE_DEPENDS_ON_FILE
                            )
                            relationships[rel_file_id] = RelationshipEntity(
                                id=rel_file_id,
                                project_id=project_id,
                                source_id=file_id,
                                target_id=target_sym["file_id"],
                                relationship_type=RelationshipType.FILE_DEPENDS_ON_FILE,
                            )

            # 3. SYMBOL_CALLS_SYMBOL & TEST_TARGETS_SYMBOL
            for s in symbols:
                f_data = file_by_id.get(s["file_id"], {})
                is_test_caller = s["is_test"] or f_data.get("is_test", False)
                for called_name in s["calls"]:
                    if called_name in symbol_by_name:
                        for target_sym in symbol_by_name[called_name]:
                            if target_sym["id"] == s["id"]:
                                continue
                            rel_call_id = make_entity_id(
                                "rel", s["id"], target_sym["id"], RelationshipType.SYMBOL_CALLS_SYMBOL
                            )
                            relationships[rel_call_id] = RelationshipEntity(
                                id=rel_call_id,
                                project_id=project_id,
                                source_id=s["id"],
                                target_id=target_sym["id"],
                                relationship_type=RelationshipType.SYMBOL_CALLS_SYMBOL,
                            )
                            if is_test_caller and not target_sym["is_test"]:
                                rel_test_id = make_entity_id(
                                    "rel", s["id"], target_sym["id"], RelationshipType.TEST_TARGETS_SYMBOL
                                )
                                relationships[rel_test_id] = RelationshipEntity(
                                    id=rel_test_id,
                                    project_id=project_id,
                                    source_id=s["id"],
                                    target_id=target_sym["id"],
                                    relationship_type=RelationshipType.TEST_TARGETS_SYMBOL,
                                )
                                test_id = make_entity_id("test", project_id, s["file_id"], s["name"])
                                c.execute(
                                    "UPDATE tests SET target_symbol = ? WHERE id = ? AND target_symbol IS NULL",
                                    (target_sym["name"], test_id),
                                )

            # Atomically replace relationships for project
            c.execute("DELETE FROM relationships WHERE project_id = ?", (project_id,))
            if relationships:
                c.executemany(
                    """
                    INSERT INTO relationships (id, project_id, source_id, target_id, relationship_type, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    [
                        (r.id, r.project_id, r.source_id, r.target_id, r.relationship_type, r.metadata_json)
                        for r in relationships.values()
                    ],
                )

        if conn is not None:
            _op(conn)
        else:
            with self.db.transaction() as c:
                _op(c)

    def apply_incremental_update(
        self,
        project_id: str,
        deleted_paths: list[str],
        new_or_modified_results: list["FileAnalysisResult"],
    ) -> None:
        """Atomically remove deleted/stale records, insert updated entities, and reconcile relationships."""
        from msk.knowledge.builder import extract_entities_from_analysis

        with self.db.transaction() as conn:
            # 1. Delete removed or modified files (and their symbols, edges, and dependencies)
            paths_to_delete = list(deleted_paths) + [r.relative_path for r in new_or_modified_results]
            self.delete_files(project_id, paths_to_delete, conn=conn)

            # 2. Extract and insert new/modified file entities
            if new_or_modified_results:
                f_list, s_list, d_list, t_list, a_list, i_list, sec_list = extract_entities_from_analysis(
                    project_id, new_or_modified_results
                )
                self.save_files(f_list, conn=conn)
                self.save_symbols(s_list, conn=conn)
                self.save_dependencies(d_list, conn=conn)
                self.save_tests(t_list, conn=conn)
                self.save_api_endpoints(a_list, conn=conn)
                self.save_infrastructure(i_list, conn=conn)
                self.save_security_findings(sec_list, conn=conn)

            # 3. Reconcile and rebuild relationships across all project entities
            self.reconcile_relationships(project_id, conn=conn)

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
