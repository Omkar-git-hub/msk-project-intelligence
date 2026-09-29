"""File and source code analyzer orchestrator."""

from pathlib import Path
from pydantic import BaseModel, Field

from msk.analyzer.dependencies import ProjectDependency, parse_manifest_dependencies
from msk.analyzer.imports import ExtractedImport, extract_imports
from msk.analyzer.languages import detect_language, is_parsable_language
from msk.analyzer.parser import parse_bytes
from msk.analyzer.symbols import ExtractedSymbol, extract_symbols
from msk.common.hashing import compute_structural_hash, sha256_bytes
from msk.project.scanner import ScannedFile


class FileAnalysisResult(BaseModel):
    """Structured analysis results for a single project file."""

    relative_path: str
    language: str | None
    size_bytes: int
    content_hash: str
    structural_hash: str
    modified_at: str
    symbols: list[ExtractedSymbol] = Field(default_factory=list)
    imports: list[ExtractedImport] = Field(default_factory=list)
    manifest_dependencies: list[ProjectDependency] = Field(default_factory=list)


def analyze_file(scanned: ScannedFile, root: Path) -> FileAnalysisResult:
    """Analyze a single project file, extracting AST symbols, imports, and dependencies."""
    # 1. Read bytes safely
    try:
        content_bytes = scanned.absolute_path.read_bytes()
    except OSError:
        content_bytes = b""

    # 2. Cryptographic content hash
    content_hash = sha256_bytes(content_bytes)

    # 3. Detect language
    language = detect_language(scanned.absolute_path)

    symbols: list[ExtractedSymbol] = []
    imports: list[ExtractedImport] = []

    # 4. AST Extraction if supported
    if language and is_parsable_language(language):
        tree = parse_bytes(content_bytes, language)
        if tree:
            symbols = extract_symbols(tree, language, content_bytes)
            imports = extract_imports(tree, language, content_bytes)
            # Explicitly delete tree so Nodes are freed via refcount (not cyclic GC).
            # On Python 3.14 / tree-sitter 0.26 / Windows, GC sweeping tree-sitter
            # objects causes access violations when Nodes outlive their owning Tree.
            del tree

    # 5. Manifest parsing
    manifest_deps = parse_manifest_dependencies(scanned.absolute_path, root)

    # 6. Structural hash computation
    structural_items: list[dict[str, str]] = []
    for s in symbols:
        structural_items.append({"type": "symbol", "name": s.name, "kind": s.type, "parent": s.parent_symbol or ""})
    for imp in imports:
        structural_items.append({"type": "import", "module": imp.module, "names": ",".join(sorted(imp.names))})
    for dep in manifest_deps:
        structural_items.append({"type": "dep", "name": dep.name, "ver": dep.version_spec or ""})

    if structural_items:
        structural_hash = compute_structural_hash(structural_items)
    else:
        structural_hash = content_hash

    return FileAnalysisResult(
        relative_path=scanned.relative_path,
        language=language,
        size_bytes=scanned.size_bytes,
        content_hash=content_hash,
        structural_hash=structural_hash,
        modified_at=scanned.modified_at,
        symbols=symbols,
        imports=imports,
        manifest_dependencies=manifest_deps,
    )
