"""File and source code analyzer orchestrator."""

from pathlib import Path
from pydantic import BaseModel, Field

from msk.analyzer.dependencies import ProjectDependency, parse_manifest_dependencies
from msk.analyzer.imports import ExtractedImport, extract_imports
from msk.analyzer.infrastructure import InfrastructureComponent, detect_infrastructure_file
from msk.analyzer.languages import detect_language, is_parsable_language
from msk.analyzer.parser import parse_bytes
from msk.analyzer.security import SecurityScanFinding, is_sensitive_path, scan_content_for_secrets
from msk.analyzer.symbols import ExtractedSymbol, extract_symbols
from msk.common.hashing import compute_structural_hash, sha256_bytes
from msk.project.scanner import ScannedFile


class FileAnalysisResult(BaseModel):
    """Structured analysis results for a single project file."""

    relative_path: str
    language: str | None
    file_type: str = "source"  # source, test, config, documentation, infrastructure, manifest
    size_bytes: int
    content_hash: str
    structural_hash: str
    modified_at: str
    is_test: bool = False
    is_sensitive: bool = False
    security_flags: list[str] = Field(default_factory=list)
    security_findings: list[SecurityScanFinding] = Field(default_factory=list)
    infrastructure: InfrastructureComponent | None = None
    symbols: list[ExtractedSymbol] = Field(default_factory=list)
    imports: list[ExtractedImport] = Field(default_factory=list)
    manifest_dependencies: list[ProjectDependency] = Field(default_factory=list)


def _classify_file_type(relative_path: str, is_test: bool, infra: InfrastructureComponent | None, is_manifest: bool) -> str:
    """Classify the primary purpose of the file."""
    if is_test:
        return "test"
    if infra:
        return "infrastructure"
    if is_manifest:
        return "manifest"
    p = Path(relative_path)
    suffix = p.suffix.lower()
    if suffix in (".md", ".rst", ".txt", ".adoc"):
        return "documentation"
    if suffix in (".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml"):
        return "config"
    return "source"


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

    # 4. Detect test characteristics
    p = Path(scanned.relative_path)
    name_lower = p.name.lower()
    parts_lower = [part.lower() for part in p.parts]

    is_test_name = (
        name_lower.startswith("test_")
        or name_lower.endswith(("_test.py", "test.java", "tests.java", "testcase.java", ".test.ts", ".spec.ts", ".test.js", ".spec.js"))
    )
    is_test_dir = False
    if "tests" in parts_lower or "test" in parts_lower:
        t_idx = parts_lower.index("tests") if "tests" in parts_lower else parts_lower.index("test")
        # If 'src' appears after 'tests' (like tests/fixtures/app/src/...), it's fixture source code
        if "src" not in parts_lower[t_idx + 1:]:
            is_test_dir = True

    is_test = is_test_name or is_test_dir


    # 5. Security & Privacy scanning
    is_sens, sens_flags = is_sensitive_path(scanned.relative_path)
    sec_findings = scan_content_for_secrets(scanned.relative_path, content_bytes)
    if sec_findings and not is_sens:
        is_sens = True
        sens_flags.append("SECRET_PATTERN_MATCH")

    # 6. Infrastructure detection
    infra = detect_infrastructure_file(scanned.relative_path)

    symbols: list[ExtractedSymbol] = []
    imports: list[ExtractedImport] = []

    # 7. AST Extraction if supported
    if language and is_parsable_language(language):
        tree = parse_bytes(content_bytes, language)
        if tree:
            symbols = extract_symbols(tree, language, content_bytes)
            imports = extract_imports(tree, language, content_bytes)
            # Explicitly delete tree so Nodes are freed via refcount (not cyclic GC).
            del tree

    # Mark symbol test status if file itself is a test file
    if is_test:
        for s in symbols:
            s.is_test = True

    # 8. Manifest parsing
    manifest_deps = parse_manifest_dependencies(scanned.absolute_path, root)
    is_manifest = bool(manifest_deps)

    # 9. File type classification
    file_type = _classify_file_type(scanned.relative_path, is_test, infra, is_manifest)

    # 10. Structural hash computation
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
        file_type=file_type,
        size_bytes=scanned.size_bytes,
        content_hash=content_hash,
        structural_hash=structural_hash,
        modified_at=scanned.modified_at,
        is_test=is_test,
        is_sensitive=is_sens,
        security_flags=sens_flags,
        security_findings=sec_findings,
        infrastructure=infra,
        symbols=symbols,
        imports=imports,
        manifest_dependencies=manifest_deps,
    )
