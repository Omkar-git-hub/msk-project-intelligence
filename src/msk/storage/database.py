"""SQLite database connection and schema management for MSK."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
import sqlite3

SCHEMA_V2 = """
PRAGMA foreign_keys = ON;


CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path TEXT NOT NULL UNIQUE,
    language TEXT,
    file_type TEXT NOT NULL DEFAULT 'source',
    size_bytes INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    structural_hash TEXT NOT NULL,
    modified_at TEXT NOT NULL,
    is_test INTEGER NOT NULL DEFAULT 0,
    is_sensitive INTEGER NOT NULL DEFAULT 0,
    security_flags TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_project_id ON files(project_id);
CREATE INDEX IF NOT EXISTS idx_files_is_test ON files(is_test);
CREATE INDEX IF NOT EXISTS idx_files_is_sensitive ON files(is_sensitive);

CREATE TABLE IF NOT EXISTS symbols (
    id TEXT PRIMARY KEY,
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    parent_symbol TEXT,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'public',
    is_test INTEGER NOT NULL DEFAULT 0,
    is_api_endpoint INTEGER NOT NULL DEFAULT 0,
    api_route TEXT,
    api_method TEXT,
    calls_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_symbols_file_id ON symbols(file_id);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_is_test ON symbols(is_test);
CREATE INDEX IF NOT EXISTS idx_symbols_is_api ON symbols(is_api_endpoint);

CREATE TABLE IF NOT EXISTS dependencies (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    version_spec TEXT,
    type TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dependencies_target ON dependencies(target);

CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(source_id);
CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships(target_id);
CREATE INDEX IF NOT EXISTS idx_relationships_type ON relationships(relationship_type);

CREATE TABLE IF NOT EXISTS tests (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    test_type TEXT NOT NULL,
    framework TEXT,
    target_symbol TEXT
);
CREATE INDEX IF NOT EXISTS idx_tests_project ON tests(project_id);
CREATE INDEX IF NOT EXISTS idx_tests_file ON tests(file_id);

CREATE TABLE IF NOT EXISTS api_endpoints (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    route TEXT NOT NULL,
    http_method TEXT NOT NULL,
    handler_symbol TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_endpoints_project ON api_endpoints(project_id);
CREATE INDEX IF NOT EXISTS idx_api_endpoints_route ON api_endpoints(route);

CREATE TABLE IF NOT EXISTS infrastructure (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_infrastructure_project ON infrastructure(project_id);
CREATE INDEX IF NOT EXISTS idx_infrastructure_kind ON infrastructure(kind);

CREATE TABLE IF NOT EXISTS security_findings (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    rule_id TEXT NOT NULL,
    severity TEXT NOT NULL,
    line_number INTEGER,
    description TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_security_findings_project ON security_findings(project_id);
CREATE INDEX IF NOT EXISTS idx_security_findings_rule ON security_findings(rule_id);

CREATE TABLE IF NOT EXISTS git_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    branch TEXT,
    commit_hash TEXT,
    is_dirty INTEGER NOT NULL,
    captured_at TEXT NOT NULL
);
"""


# Alias for backwards compatibility
SCHEMA_V1 = SCHEMA_V2


class DatabaseManager:

    """Manages SQLite connection and schema migrations."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize_schema()

    def get_connection(self) -> sqlite3.Connection:
        """Create a new SQLite connection with foreign keys and row factory."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager providing an atomic transaction."""
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize_schema(self) -> None:
        """Apply schema and run non-destructive migrations."""
        with self.transaction() as conn:
            conn.executescript(SCHEMA_V2)
            self._migrate_existing_tables(conn)

    def _migrate_existing_tables(self, conn: sqlite3.Connection) -> None:
        """Safely add missing columns to pre-existing tables if upgrading."""
        # Check files table columns
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(files);")
        file_cols = {row["name"] for row in cur.fetchall()}

        if "file_type" not in file_cols:
            conn.execute("ALTER TABLE files ADD COLUMN file_type TEXT NOT NULL DEFAULT 'source';")
        if "is_test" not in file_cols:
            conn.execute("ALTER TABLE files ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0;")
        if "is_sensitive" not in file_cols:
            conn.execute("ALTER TABLE files ADD COLUMN is_sensitive INTEGER NOT NULL DEFAULT 0;")
        if "security_flags" not in file_cols:
            conn.execute("ALTER TABLE files ADD COLUMN security_flags TEXT;")

        # Check symbols table columns
        cur.execute("PRAGMA table_info(symbols);")
        symbol_cols = {row["name"] for row in cur.fetchall()}

        if "is_test" not in symbol_cols:
            conn.execute("ALTER TABLE symbols ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0;")
        if "is_api_endpoint" not in symbol_cols:
            conn.execute("ALTER TABLE symbols ADD COLUMN is_api_endpoint INTEGER NOT NULL DEFAULT 0;")
        if "api_route" not in symbol_cols:
            conn.execute("ALTER TABLE symbols ADD COLUMN api_route TEXT;")
        if "api_method" not in symbol_cols:
            conn.execute("ALTER TABLE symbols ADD COLUMN api_method TEXT;")
        if "calls_json" not in symbol_cols:
            conn.execute("ALTER TABLE symbols ADD COLUMN calls_json TEXT;")
