"""Repository for persisting and querying the Project Knowledge Model."""

import json
from msk.knowledge.models import (
    DependencyEntity,
    FileEntity,
    ProjectEntity,
    ProjectKnowledgeSummary,
    RelationshipEntity,
    SymbolEntity,
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
                INSERT INTO files (id, project_id, path, language, size_bytes, content_hash, structural_hash, modified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    language=excluded.language,
                    size_bytes=excluded.size_bytes,
                    content_hash=excluded.content_hash,
                    structural_hash=excluded.structural_hash,
                    modified_at=excluded.modified_at
                """,
                [
                    (f.id, f.project_id, f.path, f.language, f.size_bytes, f.content_hash, f.structural_hash, f.modified_at)
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
                    size_bytes=r["size_bytes"],
                    content_hash=r["content_hash"],
                    structural_hash=r["structural_hash"],
                    modified_at=r["modified_at"],
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
                INSERT INTO symbols (id, file_id, name, type, parent_symbol, line_start, line_end, visibility)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    type=excluded.type,
                    parent_symbol=excluded.parent_symbol,
                    line_start=excluded.line_start,
                    line_end=excluded.line_end,
                    visibility=excluded.visibility
                """,
                [
                    (s.id, s.file_id, s.name, s.type, s.parent_symbol, s.line_start, s.line_end, s.visibility)
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

            return ProjectKnowledgeSummary(
                project_name=project_name,
                total_files=total_files,
                languages_count=len(languages_breakdown),
                languages_breakdown=languages_breakdown,
                total_symbols=total_symbols,
                symbols_breakdown=symbols_breakdown,
                total_dependencies=dep_count,
                total_relationships=rel_count,
                last_updated=updated_at,
                secrets_detected=0,
                sensitive_files=0,
                status="READY",
            )
