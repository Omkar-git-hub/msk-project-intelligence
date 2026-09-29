"""MSK source analysis package."""

from msk.analyzer.analyzer import FileAnalysisResult, analyze_file
from msk.analyzer.dependencies import ProjectDependency, parse_manifest_dependencies
from msk.analyzer.imports import ExtractedImport, extract_imports
from msk.analyzer.languages import detect_language, is_parsable_language
from msk.analyzer.parser import get_parser, parse_bytes
from msk.analyzer.symbols import ExtractedSymbol, extract_symbols

__all__ = [
    "ExtractedImport",
    "ExtractedSymbol",
    "FileAnalysisResult",
    "ProjectDependency",
    "analyze_file",
    "detect_language",
    "extract_imports",
    "extract_symbols",
    "get_parser",
    "is_parsable_language",
    "parse_bytes",
    "parse_manifest_dependencies",
]
